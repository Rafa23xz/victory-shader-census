# victory-shader-census

Ferramentas para medir, etapa por etapa, a tradução de shaders do Xbox Series X|S (RDNA2/GFX10)
para D3D12/DXIL feita pelo [VIctory](https://github.com/Marvisak/VIctory). **Não contém nenhum
dado do jogo**: você roda localmente sobre os seus próprios arquivos.

*English summary below.*

## O que tem aqui

| Ferramenta | O que faz |
|---|---|
| `census/` (Rust) | Um registro JSONL por programa nativo, com o resultado de **cada etapa** do `victory-shader` em separado: IR (`ir::build`), recursos, I/O, estruturação, emissão DXIL, **validador DXIL** e criação de pipeline D3D12. Panics viram dado (o `victory translate` atual cai inteiro se uma thread dá panic). O `hash` é o mesmo que o VIctory imprime em runtime (blake3 do contêiner, 16 hex). |
| `tools/decode_agree.py` | Compara o decoder do VIctory (`shader-info --disasm`) com o LLVM AMDGPU, instrução por instrução, mesmos bytes e mesmos limites. |
| `tools/sgd5_effects.py` | Efeitos do SGD5 → lista de programas (formato documentado no [GTA6-Asset-Info](https://github.com/TrinitronClient/GTA6-Asset-Info)). |
| `tools/census_report.py` | Primeira etapa que falha, por estágio, e motivos agrupados. |
| `tools/opcode_matrix.py` | Matriz por opcode: uso, programas ok, recusas que citam o opcode, decode × LLVM. |

## Como usar

```
# ao lado de um clone do VIctory em ../VIctory
cd census && cargo build --release
target/release/shader-census censo.jsonl <seu>/shaders.rpf --validate --pipelines
python -I ../tools/census_report.py censo.jsonl
```

Opções do censo: `--validate` (precisa do `dxil.dll` do Windows SDK), `--pipelines` (cria pipeline
no GPU local, com estágio parceiro stub), `--jobs N`, `--only hash,hash`, `--dump pasta` (DXIL emitida
e texto completo do validador). Para comparar duas versões do VIctory, aponte o `Cargo.toml` para
outra árvore.

Decode: `python -I tools/decode_agree.py victory.exe llvm-mc.exe saida.jsonl arquivos.dxbc...`
(variável `LLVM_CPU`, padrão `gfx1030`; use `gfx1013`).

## Resultados (contagens agregadas, RTX 4080, 2026-11-24)

- **Decode correto**: 921.861 instruções, 331 opcodes, 0 divergências entre VIctory e LLVM `gfx1013`.
- **O ISA do console é da família GFX10.1** (gfx1010/gfx1013), não gfx1030: o código usa
  `v_mac_f32`/`v_mad_f32`, inexistentes no gfx1030, e o VOP3 0x140 é `v_mad_legacy_f32` (não fundido).
- **Disassembler do PercyStar**: 26 de 149 opcodes nomeados divergem do Mesa/LLVM (17%), entre eles
  `v_cndmask_b32`, `s_lshl_b32`, `s_cselect_b32`, `s_and_saveexec_b64`. Não usar como referência.
- **Os bloqueios não são opcodes ausentes**: de 385 opcodes no corpus, só 2 aparecem como motivo de
  recusa. Travam construções: máscara de lane lida como dado, `select`/junção de máscara não
  rastreada, `v_readfirstlane`/cross-lane em fluxo divergente, descritor não rastreado, interpolação
  com baricêntricas calculadas, estágio geométrico ES+GS.
- **DXIL inválida que vira pipeline**: com alguns PRs ainda não aceitos aplicados, centenas de
  programas emitidos são rejeitados pelo validador, mas o D3D12 os aceita porque o VIctory assina o
  contêiner. Casos vistos: atômico de imagem de 64 bits num recurso do heap anotado como float4 e
  sem a flag `AtomicInt64OnHeapResource`; amostragem com LOD implícito em compute; tamanho de saída
  de mesh shader acima de 32.768.
- **Emulação wave64 → wave32**: aplicada a todo compute wave64, ela regride programas que o `main`
  já traduzia (recusas de `v_readfirstlane` em fluxo divergente e panics "forward reference outside
  a phi" em `emit/bitcode.rs`). Proposta: emular só onde a emissão nativa exigiria `WaveSize(64)`.

Estados seguem a convenção C-2 do [gta6-pc-port-hub](https://github.com/fishingpvalues/gta6-pc-port-hub):
`parsed / translated / validated / bound / executed / matched`. Aqui se mede até `validated` e
criação de pipeline; `executed`/`matched` ainda não.

## Licença

GPL-3.0-or-later (o censo usa crates do VIctory, GPL-3.0). Nenhum código do PercyStar foi copiado.

---

## English summary

Tools that measure, stage by stage, how VIctory translates Xbox Series (RDNA2/GFX10) shaders to
D3D12 DXIL. **No game data included**; run them locally on your own files.

- `census/`: one JSONL record per native program with the result of every `victory-shader` stage
  (IR, resources, I/O, structurisation, DXIL emission, **DXIL validator**, D3D12 pipeline), panics
  isolated. `hash` matches the one VIctory logs at run time.
- `tools/decode_agree.py`: VIctory decoder vs LLVM AMDGPU, same bytes, same boundaries.
- `tools/sgd5_effects.py`: SGD5 effects → program lists. `census_report.py`, `opcode_matrix.py`: reports.

Findings (aggregate counts): the decoder agrees with LLVM `gfx1013` on 921,861 instructions; the
console ISA is GFX10.1-family (uses `v_mac_f32`/`v_mad_f32`, absent on gfx1030); PercyStar's
disassembler mislabels 17% of the opcodes it names; only 2 of 385 opcodes are refusal reasons, the
blockers are constructs (lane masks as data, divergent wave ops, untracked descriptors, computed
barycentrics, merged ES+GS); some emitted DXIL fails the validator yet becomes a pipeline because the
container is self-signed; wave64→wave32 emulation applied to every wave64 compute program regresses
programs `main` already translated.
