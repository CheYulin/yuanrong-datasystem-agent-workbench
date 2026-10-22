import hashlib
import itertools
import json
from pathlib import Path

source = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor/coordinator-report-001')
output = Path('/home/t14s/workspace/scratch/2026-10-22-kvc-result-coverage/exist-source-audit.json')
data = json.loads((source / 'data.json').read_text())
manifest = dict((line.split('  ', 1)[1], line.split('  ', 1)[0]) for line in (source / 'SHA256SUMS').read_text().splitlines())
rows = [row for row in data['rows'] if row['operation'].startswith('exist')]
identities = {(r['product'], r['repeat'], r['batch'], r['operation']) for r in rows}
assert len(rows) == 72 and identities == set(itertools.product(('datasystem', 'mooncake', 'lmcache'), range(3), (1, 16, 256, 4096), ('exist_hit', 'exist_miss')))
records = []
for row in rows:
    raw = (source / row['evidence']).read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    assert sha256 == manifest[row['evidence']]
    assert json.loads(raw)['valid'] == (row['status'] == 'valid')
    records.append({'identity': row['identity'], 'status': row['status'], 'evidence': row['evidence'], 'sha256': sha256})
result = json.dumps({'passed': True, 'expected': 72, 'observed': len(rows), 'records': records}, indent=2) + '\n'
if output.exists():
    assert output.read_text() == result, 'Prior proof differs'
else:
    output.write_text(result)
print('72 EXIST identities, raw hashes and native valid flags verified')
