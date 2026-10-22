#!/usr/bin/env python3
"""Measure configured `compare` on a fixture: peak RSS (isolated child process), wall time, comparison.json size and,
for a v1 file, the compact-JSON byte breakdown (raw / pair_lineage_case_keys / contributing_source_case_keys / rest).

    python scripts/make_compare_fixture.py --out /tmp/fx --cases 100 --size 200
    python scripts/measure_compare_payload.py /tmp/fx/config.yaml [--breakdown-only]
"""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = r'''import json,resource,sys,time
from simcompare.pipeline import load_numeric_config,run_numeric_compare
cfg=load_numeric_config(sys.argv[1]); start=time.perf_counter()
r=run_numeric_compare(cfg)
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
print(json.dumps({'exit_code':r['exit_code'],'wall_seconds':round(time.perf_counter()-start,2),'peak_rss_mib':round(rss,1)}))
'''


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8'))


def breakdown(payload):
    """Bytes of each v1 component inside the compact encoding of the whole payload."""
    total = size(payload)
    parts = {'raw': 0, 'pair_lineage_case_keys': 0, 'contributing_source_case_keys': 0}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'raw' and 'source_key' in node:
                    parts['raw'] += size(value)
                elif key in ('pair_lineage_case_keys', 'contributing_source_case_keys'):
                    parts[key] += size(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    for side in ('reference', 'target'):
        walk(payload.get(side))
    parts['rest'] = total - sum(parts.values())
    parts['total_compact'] = total
    return parts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config')
    parser.add_argument('--breakdown-only', action='store_true')
    args = parser.parse_args()
    config = Path(args.config)
    result = {}
    if not args.breakdown_only:
        env = {'PYTHONPATH': str(ROOT / 'src'), 'PATH': '/usr/bin:/bin'}
        run = subprocess.run([sys.executable, '-c', HELPER, str(config)], cwd=ROOT, env=env, capture_output=True, text=True)
        if run.returncode:
            raise SystemExit(run.stderr)
        result.update(json.loads(run.stdout))
    import yaml
    out = config.parent / yaml.safe_load(config.read_text())['output_dir'] / 'comparison.json'
    result['comparison_json_bytes'] = out.stat().st_size
    payload = json.loads(out.read_text(encoding='utf-8'))
    result['schema_version'] = payload['schema_version']
    if payload['schema_version'].endswith('_v1'):
        result['breakdown'] = breakdown(payload)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
