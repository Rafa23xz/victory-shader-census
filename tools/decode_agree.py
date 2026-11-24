# Compara o decoder do VIctory (shader-info --disasm) com o LLVM AMDGPU (gfx1030),
# instrução por instrução, nos mesmos bytes e nos mesmos limites. Uso:
#   python -I decode_agree.py <victory.exe> <llvm-mc.exe> <saida.jsonl> <arquivos.dxbc...>
# Cada instrução que o VIctory decodifica é enviada ao LLVM com um s_nop depois (para
# o LLVM realinhar se discordar do tamanho); o llvm-objdump dá o offset real de cada
# instrução. Mede: (1) tamanho igual, (2) mnemônico igual (sem sufixo _e32/_e64).
import collections, json, os, re, subprocess, sys, tempfile

vic, mc, out = sys.argv[1:4]
files = sys.argv[4:]
OBJDUMP = os.path.join(os.path.dirname(mc), 'llvm-objdump.exe')
LINE = re.compile(r'^\s{4}([0-9a-f]{6}): ((?:[0-9A-F]{8} )+)\s*(\S+)\s*(.*)$')
OBJ = re.compile(r'^\s*(\S+)(.*?)//\s*([0-9A-F]{12}):((?: [0-9A-F]{8})+)')
SEP = bytes.fromhex('000080bf')  # s_nop 0
CPU = os.environ.get('LLVM_CPU', 'gfx1030')


def victory(path):
    r = subprocess.run([vic, 'shader-info', path, '--disasm'], capture_output=True, text=True, errors='replace')
    progs, cur = [], None
    for l in r.stdout.splitlines():
        m = LINE.match(l)
        if m:
            if cur is None:
                cur = {}
                progs.append(cur)
            cur[int(m.group(1), 16)] = (m.group(2).split(), m.group(3), m.group(4).strip())
        elif not l.startswith('    ') or 'RootParam' in l or 'UsedResource' in l:
            cur = None
    return [sorted(p.items()) for p in progs if p]


def norm(mn):
    return re.sub(r'_(e32|e64|sdwa|dpp|dpp8)$', '', mn)


def llvm(insts):
    stream, starts = b'', []
    for _, (words, _, _) in insts:
        starts.append(len(stream))
        stream += b''.join(int(w, 16).to_bytes(4, 'little') for w in words) + SEP
    d = tempfile.mkdtemp()
    s_, o_ = os.path.join(d, 'p.s'), os.path.join(d, 'p.o')
    with open(s_, 'w') as f:
        f.write('.text\n')
        for i in range(0, len(stream), 64):
            f.write('.byte ' + ','.join(str(b) for b in stream[i:i + 64]) + '\n')
    subprocess.run([mc, '-triple=amdgcn-amd-amdhsa', '-mcpu=' + CPU, '-filetype=obj', s_, '-o', o_], check=True)
    r = subprocess.run([OBJDUMP, '-d', '--mcpu=' + CPU, '--mattr=+wavefrontsize32', o_],
                       capture_output=True, text=True)
    at = {}
    for l in r.stdout.splitlines():
        m = OBJ.match(l)
        if m:
            at[int(m.group(3), 16)] = (m.group(1), m.group(2).strip(), 4 * len(m.group(4).split()))
    return [at.get(p) for p in starts]


tot, diff, lenbad, ex = collections.Counter(), collections.Counter(), collections.Counter(), {}
nins = 0
with open(out, 'w') as f:
    for path in files:
        for pi, prog in enumerate(victory(path)):
            prog = [x for x in prog if x[1][1] != 'v_nop' or x[0] > 0x40]  # preâmbulo com hash
            bad = 0
            for (off, (words, mn, ops)), got in zip(prog, llvm(prog)):
                nins += 1
                tot[mn] += 1
                if got is None or got[2] != 4 * len(words):
                    lenbad[mn] += 1
                    bad += 1
                    ex.setdefault(('LEN', mn), f'{os.path.basename(path)}#{pi}@{off:#x}: victory="{mn} {ops}" llvm={got[:2] if got else None}')
                    continue
                lmn = norm(got[0])
                if lmn != mn:
                    diff[(mn, lmn)] += 1
                    ex.setdefault((mn, lmn), f'{os.path.basename(path)}#{pi}@{off:#x}: victory="{mn} {ops}" llvm="{got[0]} {got[1]}"')
            f.write(json.dumps({'file': os.path.basename(path), 'prog': pi, 'insts': len(prog), 'len_mismatch': bad}) + '\n')
print('arquivos', len(files), 'instruções', nins, 'opcodes distintos', len(tot))
print('tamanho divergente do LLVM', sum(lenbad.values()))
print('mnemônico divergente', sum(diff.values()))
for (a, b), n in diff.most_common(40):
    print(f'{n:7} victory={a:28} llvm={b:28} ex: {ex[(a, b)][:220]}')
for a, n in lenbad.most_common(15):
    print(f'{n:7} TAMANHO victory={a:24} ex: {ex[("LEN", a)][:220]}')
json.dump({'instrucoes': nins, 'opcodes': tot, 'mnemonico_diferente': {f'{a}|{b}': n for (a, b), n in diff.items()},
           'tamanho_diferente': lenbad}, open(out + '.resumo.json', 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
