# Matriz por opcode: uso no corpus, decode conferido com LLVM, nome no PercyStar, resultado da tradução.
# Uso: python -I opcode_matrix.py censo.jsonl agree.resumo.json percy_divergentes.txt saida.csv
import collections, csv, json, re, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8')]
agree = json.load(open(sys.argv[2], encoding='utf-8'))
percy_bad = set(l.split()[0] for l in open(sys.argv[3], encoding='utf-8') if l.strip())
ST = ('ir', 'resources', 'io', 'structure', 'emit', 'validate', 'pipeline')
use, ok, fail_named, val_bad = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
for r in rows:
    ops = r.get('opcodes') or {}
    good = 'stage' in r and all(r.get(s, {'ok': True}).get('ok') for s in ST)
    em = r.get('emit', {})
    why = em.get('reason', '') or em.get('panic', '')
    for o in ops:
        use[o] += 1
        ok[o] += good
        if why and re.search(r'(^|\W)' + re.escape(o) + r'(\W|$)', why):
            fail_named[o] += 1
        if r.get('validate') and not r['validate'].get('ok'):
            val_bad[o] += 1
def klass(o):
    if fail_named[o]: return 'decodificada, tradução recusada (motivo cita o opcode)'
    if ok[o] == use[o]: return 'traduzida e validada em todos os programas que a usam'
    if ok[o] == 0: return 'nunca num programa que chega ao fim'
    return 'traduzida; programas falham por outros motivos'
with open(sys.argv[4], 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['opcode', 'programas_que_usam', 'programas_ok', 'pct_ok', 'recusas_que_citam', 'em_programas_rejeitados_pelo_validador',
                'decode_vs_llvm_gfx1013', 'nome_no_percystar', 'classe'])
    for o, n in use.most_common():
        dec = 'igual (amostra 300)' if agree['opcodes'].get(o) else 'fora da amostra'
        w.writerow([o, n, ok[o], f'{100 * ok[o] / n:.1f}', fail_named[o], val_bad[o], dec,
                    'ERRADO' if o in percy_bad else '', klass(o)])
print('opcodes', len(use), '| citados em recusas', sum(1 for o in use if fail_named[o]),
      '| sempre ok', sum(1 for o in use if ok[o] == use[o]))
for o in sorted(use, key=lambda o: -fail_named[o])[:15]:
    if fail_named[o]: print(f'  {o:28} usa {use[o]:6} recusas {fail_named[o]:5}')
