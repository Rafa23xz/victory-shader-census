//! Censo de shaders: um registro JSONL por programa nativo, com o resultado de
//! cada etapa do victory-shader medido em separado e panics isolados (um panic
//! vira o estado `panic` daquela etapa; o resto do corpus continua).
//!
//! Uso: shader-census <saida.jsonl> <arquivos...> [--validate] [--pipelines] [--jobs N]
//!
//! Etapas (na ordem do victory-shader): decode+IR (`ir::build`), recursos
//! (`resource::track`), I/O (`io::raise`), estruturação (`structure::structure`),
//! emissão DXIL (`emit::emit_with`), validação (`dxil.dll`), pipeline no host
//! (D3D12, com estágio parceiro "stub" — não é o par real do jogo).
//!
//! Identificadores: `hash` = 16 primeiros hex de blake3 do contêiner, o mesmo
//! que o VIctory imprime em runtime (`victory-gpu/src/pipeline.rs`).

use std::collections::{BTreeMap, HashSet};
use std::io::Write;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use serde_json::{Value as J, json};
use victory_shader::emit::host::{self, Host};
use victory_shader::emit::{self, validator::Validator};
use victory_shader::{Container, FourCC, HwStage, ShaderError, io, ir, native_programs, resource, structure};

struct Job {
    file: String,
    offset: usize,
    name: Option<String>,
    bytes: Option<Vec<u8>>,
}

fn reason(e: &ShaderError) -> (String, Option<usize>) {
    match e {
        ShaderError::Unsupported { offset, what } => (what.to_string(), Some(*offset)),
        ShaderError::Isa { offset, reason } => (reason.to_string(), Some(*offset)),
        e => (e.to_string(), None),
    }
}

fn panic_text(p: Box<dyn std::any::Any + Send>) -> String {
    p.downcast_ref::<&str>()
        .map(|s| s.to_string())
        .or_else(|| p.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "panic".into())
}

/// Roda uma etapa; devolve o valor (se ok) e o registro da etapa.
fn stage<T>(f: impl FnOnce() -> Result<T, ShaderError>) -> (Option<T>, J) {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(v)) => (Some(v), json!({"ok": true})),
        Ok(Err(e)) => {
            let (r, off) = reason(&e);
            (None, json!({"ok": false, "reason": r, "at": off.map(|o| format!("{o:#x}"))}))
        }
        Err(p) => (None, json!({"ok": false, "panic": panic_text(p)})),
    }
}

fn stage_name(s: HwStage) -> &'static str {
    match s {
        HwStage::Ls => "ls",
        HwStage::Hs => "hs",
        HwStage::Es => "es",
        HwStage::Gs => "gs",
        HwStage::Vs => "vs",
        HwStage::Ps => "ps",
        HwStage::Ms => "ms",
        HwStage::Cs => "cs",
    }
}

struct Checks {
    dump: Option<String>,
    validator: Option<Validator>,
    host: Option<(Host, emit::HostShader)>,
}

fn record(job: &Job, seen: &Mutex<HashSet<blake3::Hash>>, checks: &mut Checks) -> Vec<J> {
    let base = json!({"file": job.file, "offset": format!("{:#x}", job.offset), "name": job.name});
    let mut out = Vec::new();
    let Some(bytes) = &job.bytes else {
        let mut r = base;
        r["container"] = json!({"ok": false, "reason": "corrupt record"});
        return vec![r];
    };
    let hash = blake3::hash(bytes).to_hex()[..16].to_owned();
    let Ok(container) = Container::parse(bytes) else {
        let mut r = base;
        r["hash"] = json!(hash);
        r["container"] = json!({"ok": false, "reason": "not DXBC"});
        return vec![r];
    };
    let Some(dxil) = container.part(FourCC::DXIL).map(|p| p.data) else {
        let mut r = base;
        r["hash"] = json!(hash);
        r["container"] = json!({"ok": false, "reason": "no DXIL part"});
        return vec![r];
    };
    if !seen.lock().unwrap().insert(blake3::hash(dxil)) {
        return out; // mesmo programa já medido (duplicata por conteúdo do DXIL)
    }
    let (programs, native) = stage(|| native_programs(dxil));
    let Some(programs) = programs else {
        let mut r = base;
        r["hash"] = json!(hash);
        r["native"] = native;
        return vec![r];
    };
    for (index, p) in programs.iter().enumerate() {
        let mut r = base.clone();
        r["hash"] = json!(hash);
        r["program"] = json!(index);
        r["stage"] = json!(stage_name(p.stage));
        r["wave64"] = json!(p.wave64);
        r["vgprs"] = json!(p.vgprs);
        r["code_bytes"] = json!(p.code.len());
        r["root_params"] = json!(p.layout.len());
        let mut kinds: BTreeMap<String, usize> = BTreeMap::new();
        for u in &p.used {
            *kinds.entry(format!("{:?}", u.kind)).or_default() += 1;
        }
        r["used_resources"] = json!(kinds);
        let code = p.patched_code();
        let (program, s) = stage(|| ir::build(&code, p.wave64, p.vgprs));
        r["ir"] = s;
        let Some(program) = program else {
            out.push(r);
            continue;
        };
        let mut ops: BTreeMap<&str, usize> = BTreeMap::new();
        let mut n = 0;
        for b in &program.blocks {
            for i in &b.insts {
                *ops.entry(i.name()).or_default() += 1;
                n += 1;
            }
        }
        r["instructions"] = json!(n);
        r["blocks"] = json!(program.blocks.len());
        r["opcodes"] = json!(ops);
        let (res, s) = stage(|| resource::track(&program, &p.layout, p.stage));
        r["resources"] = s;
        let wants_io = matches!(p.stage, HwStage::Vs | HwStage::Ps | HwStage::Cs | HwStage::Ms);
        let (raised, s) = if wants_io { stage(|| io::raise(&program, p)) } else { (None, json!({"ok": false, "reason": "stage not handled by io::raise"})) };
        r["io"] = s;
        let (st, s) = stage(|| structure::structure(&program));
        r["structure"] = s;
        let (Some(res), Some(raised), Some(st)) = (res, raised, st) else {
            r["emit"] = json!({"ok": false, "reason": "earlier stage failed"});
            out.push(r);
            continue;
        };
        let options = emit::TranslateOptions {
            wave64: checks.host.as_ref().map_or(emit::Wave64Mode::Native, |(h, _)| h.recommended_wave64_mode()),
        };
        r["wave64_mode"] = json!(format!("{:?}", options.wave64));
        let (shader, s) = stage(|| emit::emit_with(&container, p, &program, &st, &raised, &res, options));
        r["emit"] = s;
        let Some(mut shader) = shader else {
            out.push(r);
            continue;
        };
        r["dxil_bytes"] = json!(shader.dxil.len());
        if let Some(dir) = &checks.dump {
            let _ = std::fs::write(format!("{dir}/{hash}_{index}.dxil"), &shader.dxil);
        }
        if let Some(v) = checks.validator.as_mut() {
            r["validate"] = match catch_unwind(AssertUnwindSafe(|| v.validate(&mut shader.dxil))) {
                Ok(Ok(())) => json!({"ok": true}),
                Ok(Err(text)) => {
                    if let Some(dir) = &checks.dump {
                        let _ = std::fs::write(format!("{dir}/{hash}_{index}.validator.txt"), &text);
                    }
                    let first = text.lines().find(|l| l.contains("error")).unwrap_or(text.lines().next().unwrap_or("")).to_owned();
                    json!({"ok": false, "reason": first})
                }
                Err(p) => json!({"ok": false, "panic": panic_text(p)}),
            };
        }
        if let Some((h, stub_ps)) = &checks.host {
            let made = catch_unwind(AssertUnwindSafe(|| match shader.stage {
                HwStage::Vs => h.pipeline(&shader, stub_ps).map(drop),
                HwStage::Cs => h.compute_pipeline(&shader).map(drop),
                HwStage::Ms => h.mesh_pipeline(&shader, stub_ps).map(drop),
                _ => host::stub_vs(&shader).and_then(|vs| h.pipeline(&vs, &shader).map(drop)),
            }));
            r["pipeline"] = match made {
                Ok(Ok(())) => json!({"ok": true}),
                Ok(Err(e)) => json!({"ok": false, "reason": e}),
                Err(p) => json!({"ok": false, "panic": panic_text(p)}),
            };
        }
        out.push(r);
    }
    out
}

fn jobs_of(path: &str, data: &[u8]) -> Vec<Job> {
    let mut v = Vec::new();
    if victory_game::sgd5::is_sgd5(data) {
        for rec in victory_game::sgd5::records(data) {
            v.push(Job { file: path.into(), offset: rec.offset, name: rec.name.map(str::to_owned), bytes: rec.unpack() });
        }
    }
    for off in memchr::memmem::find_iter(data, b"DXBC") {
        if Container::parse(&data[off..]).is_ok() {
            // contêiner guardado como está (despejos do runtime, executável);
            // tamanho total no cabeçalho (+0x18), como o título o entrega
            let size = (u32::from_le_bytes(data[off + 0x18..off + 0x1c].try_into().unwrap()) as usize).min(data.len() - off);
            v.push(Job { file: path.into(), offset: off, name: None, bytes: Some(data[off..off + size].to_vec()) });
        }
    }
    v
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let out_path = args.first().expect("uso: shader-census <saida.jsonl> <arquivos...> [--validate] [--pipelines] [--jobs N]").clone();
    let validate = args.iter().any(|a| a == "--validate");
    let pipelines = args.iter().any(|a| a == "--pipelines");
    let jobs_n = args.iter().position(|a| a == "--jobs").and_then(|i| args.get(i + 1)).and_then(|n| n.parse().ok())
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(4, |n| n.get()));
    let mut files: Vec<&String> = Vec::new();
    let mut skip = false;
    for a in &args[1..] {
        if skip { skip = false; continue; }
        if a == "--jobs" || a == "--dump" || a == "--only" { skip = true; continue; }
        if !a.starts_with("--") { files.push(a); }
    }
    // Panics viram dados; não poluir a saída com a mensagem padrão de cada um.
    std::panic::set_hook(Box::new(|_| {}));
    let dll = validate.then(Validator::default_path).flatten();
    let dump = args.iter().position(|a| a == "--dump").and_then(|i| args.get(i + 1)).cloned();
    let only: Option<HashSet<String>> = args.iter().position(|a| a == "--only").and_then(|i| args.get(i + 1))
        .map(|v| v.split(',').map(|h| h.trim().to_ascii_lowercase()).collect());
    let out = Mutex::new(std::io::BufWriter::new(std::fs::File::create(&out_path).expect("saída")));
    let seen = Mutex::new(HashSet::new());
    let count = AtomicUsize::new(0);
    for f in files {
        let data = std::fs::read(f).expect("leitura");
        let mut jobs = jobs_of(f, &data);
        if let Some(only) = &only {
            jobs.retain(|j| j.bytes.as_ref().is_some_and(|b| only.contains(&blake3::hash(b).to_hex()[..16])));
        }
        eprintln!("{f}: {} contêineres", jobs.len());
        let next = AtomicUsize::new(0);
        std::thread::scope(|s| {
            for _ in 0..jobs_n.min(jobs.len().max(1)) {
                s.spawn(|| {
                    let mut checks = Checks {
                        dump: dump.clone(),
                        validator: validate.then(|| Validator::load(dll.as_deref()).ok()).flatten(),
                        host: pipelines.then(|| Host::new(false).ok().map(|h| (h, host::stub_ps()))).flatten(),
                    };
                    while let Some(job) = jobs.get(next.fetch_add(1, Ordering::Relaxed)) {
                        let recs = record(job, &seen, &mut checks);
                        let mut o = out.lock().unwrap();
                        for r in recs {
                            writeln!(o, "{r}").unwrap();
                        }
                        let n = count.fetch_add(1, Ordering::Relaxed) + 1;
                        if n % 2000 == 0 {
                            eprintln!("  {n} contêineres");
                        }
                    }
                });
            }
        });
    }
    out.lock().unwrap().flush().unwrap();
    eprintln!("fim: {} contêineres", count.load(Ordering::Relaxed));
}
