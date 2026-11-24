# Efeitos do SGD5 -> programas (ordinal por classe de estágio -> registro de contêiner).
# Formato (GTA6-Asset-Info, structure/06-models-shaders.md, "Archive layout in detail"):
#   registro de contêiner: u8 len, nome, NUL, u8 kind, u32 n, u32 0x000DDCE7, u32 n, u32 usize, deflate
#   efeito: u8 len, nome, NUL, u32 tamanho, 6 listas (u32 n, u32[n]): vs+ms, ps, gs, (hs), (ds), cs
#   número de programa = ordinal do contêiner dentro da classe de estágio, em ordem de arquivo
# Observado aqui: shaders.rpf tem DOIS arquivos SGD5 (magia "SGD5" duas vezes); a numeração
# reinicia em cada um e os efeitos de cada um ficam entre o último registro dele e o próximo SGD5.
# O estágio vem da parte DXIL (16 bits altos da versão do programa), não do nome.
# O hash de runtime (blake3) não é calculado aqui: liga-se ao censo pelo offset do registro.
# Uso: python -I sgd5_effects.py shaders.rpf saida.json
import json, mmap, re, struct, sys, zlib

f = open(sys.argv[1], 'rb')
m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)


def u32(o):
    return struct.unpack_from('<I', m, o)[0]


recs = []
for x in re.finditer(re.escape(struct.pack('<I', 0x000DDCE7)), m):
    at = x.start()
    n = u32(at - 4)
    if u32(at + 4) != n or n < 12:
        continue
    nul = at - 6
    name = None
    if m[nul] == 0:
        s = nul - 1
        while s > 0 and 0x21 <= m[s] < 0x7f:
            s -= 1
        for l in range(max(s, 0), nul):
            if m[l] == nul - l:
                name = m[l + 1:nul].decode('ascii', 'replace')
                break
    recs.append({'offset': at, 'name': name, 'n': n})

KIND = {0: 'ps', 1: 'vs', 2: 'gs', 3: 'hs', 4: 'ds', 5: 'cs', 13: 'ms', 14: 'as'}


def unpack(r):
    at, n = r['offset'], r['n']
    d = zlib.decompressobj(-15)
    try:
        data = d.decompress(bytes(m[at + 12:at + n]))
    except zlib.error:
        return None
    return data if len(data) == u32(at + 8) else None


def stage_of(data):
    if not data or data[:4] != b'DXBC':
        return None
    for i in range(struct.unpack_from('<I', data, 0x1c)[0]):
        po = struct.unpack_from('<I', data, 0x20 + 4 * i)[0]
        if data[po:po + 4] == b'DXIL':
            return KIND.get(struct.unpack_from('<I', data, po + 8)[0] >> 16, 'k?')
    return None


mags = [x.start() for x in re.finditer(b'SGD5', m)]
counts = {}
for r in recs:
    r['sgd5'] = max(i for i, g in enumerate(mags) if g <= r['offset'])
    r['stage'] = stage_of(unpack(r))
    k = {'vs': 'vsms', 'ms': 'vsms'}.get(r['stage'], r['stage'])
    r['class'] = k
    key = (r['sgd5'], k)
    r['ordinal'] = counts.get(key, 0)
    counts[key] = counts.get(key, 0) + 1
print('registros', len(recs), 'por (sgd5, classe)', counts)

NAME = re.compile(rb'([\x02-\x7f])([A-Za-z0-9_@.-]+)\x00')
effects = []
for g in range(len(mags)):
    rs = [r for r in recs if r['sgd5'] == g]
    end = rs[-1]['offset'] + rs[-1]['n']
    stop = mags[g + 1] if g + 1 < len(mags) else len(m)
    limits = [counts.get((g, c), 0) for c in ('vsms', 'ps', 'gs')] + [10 ** 9, 10 ** 9, counts.get((g, 'cs'), 0)]
    pos = end
    for x in NAME.finditer(m, end, stop):
        L, nm = x.group(1)[0], x.group(2)
        if L != len(nm) + 1 or x.start() < pos:
            continue
        o = x.end() + 4
        lists, ok = [], True
        for i in range(6):
            if o + 4 > stop:
                ok = False
                break
            n = u32(o)
            o += 4
            if n > 5000:
                ok = False
                break
            vals = list(struct.unpack_from(f'<{n}I', m, o))
            o += 4 * n
            if any(v >= limits[i] for v in vals) or vals != sorted(set(vals)):
                ok = False
                break
            lists.append(vals)
        if not ok or not any(lists):
            continue
        effects.append({'sgd5': g, 'name': nm.decode(), 'offset': x.start(), 'lists': lists})
        pos = o
print('efeitos', len(effects), 'por sgd5', {g: sum(1 for e in effects if e['sgd5'] == g) for g in range(len(mags))})

by = {(r['sgd5'], r['class'], r['ordinal']): r for r in recs}
names = ['vsms', 'ps', 'gs', 'hs', 'ds', 'cs']
out = {'counts': {f'{g}:{c}': n for (g, c), n in counts.items()},
       'records': [{'offset': hex(r['offset']), 'name': r['name'], 'stage': r['stage'], 'sgd5': r['sgd5'],
                    'ordinal': r['ordinal']} for r in recs],
       'effects': []}
unresolved = 0
for e in effects:
    progs = []
    for i, vals in enumerate(e['lists']):
        for v in vals:
            r = by.get((e['sgd5'], names[i], v))
            unresolved += r is None
            progs.append({'class': names[i], 'ordinal': v, 'stage': r and r['stage'], 'name': r and r['name'],
                          'record_offset': r and hex(r['offset'])})
    out['effects'].append({'sgd5': e['sgd5'], 'name': e['name'], 'offset': hex(e['offset']), 'programs': progs})
used = {p['record_offset'] for e in out['effects'] for p in e['programs']}
print('referências sem registro', unresolved, '| registros referenciados por algum efeito', len(used), 'de', len(recs))
json.dump(out, open(sys.argv[2], 'w'), indent=1)
print('ok', sys.argv[2])
