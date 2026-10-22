"""Byte comparison of two compare-windows output folders; the only normalised fields are the two config provenance keys
(the benchmark used cfg_old.yaml / cfg_new.yaml) and the folder path inside execution.json (ignored)."""
import json, sys
from pathlib import Path
a, b = Path(sys.argv[1]), Path(sys.argv[2])
fa = {p.name: p for p in a.iterdir() if p.is_file()}
fb = {p.name: p for p in b.iterdir() if p.is_file()}
print('only in A', sorted(set(fa) - set(fb))[:5], 'only in B', sorted(set(fb) - set(fa))[:5])
differs = []
for name in sorted(set(fa) & set(fb)):
    if name == 'execution.json':
        continue
    x, y = fa[name].read_bytes(), fb[name].read_bytes()
    if x == y:
        continue
    if name == 'local_delta.json':
        jx, jy = json.loads(x), json.loads(y)
        for j in (jx, jy):
            j['provenance'].pop('config_path'), j['provenance'].pop('config_sha256')
        if jx == jy:
            differs.append((name, 'only config_path/config_sha256'))
            continue
    differs.append((name, 'DIFFERENT'))
print('files compared', len(set(fa) & set(fb)) - 1, 'differences', differs)
