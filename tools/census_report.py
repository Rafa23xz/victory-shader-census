# Resumo do censo JSONL: primeira etapa que falha, por estágio; motivos; panics; opcodes.
# Uso: python -I census_report.py censo.jsonl [n_motivos]
import collections, json, re, sys
ST = ['ir', 'resources', 'io', 'structure', 'emit', 'validate', 'pipeline']
rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8')]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30
def first_fail(r):
    if 'stage' not in r: return 'container/native'
    for s in ST:
        v = r.get(s)
        if v is None: continue
        if not v.get('ok'):
            # io/resources/structure falham "em paralelo": a emissão nem roda; a primeira na ordem conta
            return s
    return 'ok'
by = collections.defaultdict(collections.Counter)
reasons = collections.Counter(); ex = {}
for r in rows:
    st = r.get('stage', '?'); ff = first_fail(r)
    by[st][ff] += 1
    if ff not in ('ok',):
        v = r.get(ff, {}) if ff in ST else r.get('native', r.get('container', {}))
        why = v.get('panic') and 'PANIC ' + v['panic'] or v.get('reason', '?')
        why = re.sub(r'0x[0-9a-f]+', 'X', str(why)); why = re.sub(r'\d+', 'N', why)[:120]
        reasons[(st, ff, why)] += 1; ex.setdefault((st, ff, why), r.get('hash'))
print('programas', len(rows))
print(f"{'estágio':7} {'total':>6} " + ' '.join(f'{s:>10}' for s in ['ok'] + ST))
for st in sorted(by):
    c = by[st]; t = sum(c.values())
    print(f"{st:7} {t:6} " + ' '.join(f'{c.get(s, 0):10}' for s in ['ok'] + ST))
print()
for (st, ff, why), n in reasons.most_common(N):
    print(f'{n:6} {st:3} {ff:9} {why}  [ex {ex[(st, ff, why)]}]')
panics = [(r.get('stage'), r['hash'], s, r[s]['panic']) for r in rows for s in ST if isinstance(r.get(s), dict) and r[s].get('panic')]
print('\npanics', len(panics))
for p in panics[:20]: print('  ', p)
