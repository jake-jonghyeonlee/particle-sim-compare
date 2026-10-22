#!/usr/bin/env python3
"""Real-scale benchmark for import-hotspots -> validate-data -> compare, one CLI process per command.

    python scripts/benchmark_scale_compare.py --work /tmp/bench --cases 300 --size 1262 --profile centered [--keep]
        [--max-bucket-output-entries N] [--max-operation-bytes N] [--python PYTHON] [--full-bucket-detail]

Data spec (fixed here): per model `--cases` cases at crop `--size` (µm * 10 pixels), 32 diameters (1..30, 40, 50), one layer
`L1`, distinct electrode pair `A<i>`/`B<i>` per case, 70 % line / 30 % pixel pairs. Reference and target use different seeds.
  scattered: 1-3 circular blobs at random places, shared by 3 consecutive cases (make_compare_fixture behaviour).
  centered : one band (rectangle) per case at the crop centre with +-50 px jitter; half-height = diameter-growing radius
             r = max(1, d * size / 400), half-width = r + 60 px, so most cases overlap like real crops.
Each command runs as its own process under `/usr/bin/time -l` (macOS) or `-v` (Linux): wall, isolated peak RSS, exit code,
error code (from error.json / stderr), output sizes, and the file-hash / mask-decode counts of the process
(`io_counters`, via SIMCOMPARE_IO_COUNTERS). Prints one JSON object; use --keep to retain the data.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image
import yaml

ROOT = Path(__file__).resolve().parents[1]
DIAMETERS = (*range(1, 31), 40, 50)
DARWIN = sys.platform == 'darwin'


def _png_bytes_cache(path, size):
    Image.new('L', (size, size), 0).save(path, compress_level=1)


def _case(args):
    """Write one case folder (hotspot PNGs, csv, electrode PNG copies)."""
    root, name, index, size, profile, seed, template = args
    rng = np.random.RandomState(seed * 100003 + index // (3 if profile == 'scattered' else 1))
    jitter = np.random.RandomState(seed * 7919 + index)
    case = Path(root) / name
    (case / 'result').mkdir(parents=True)
    shutil.copyfile(template, case / '1.png')
    shutil.copyfile(template, case / '2.png')
    with (case / 'result' / 'lateral_short.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('defect_size(um)', 'Area(um2)'))
        for diameter in DIAMETERS:
            writer.writerow((diameter, '0.01'))
    if profile == 'scattered':
        centres = [(rng.randint(0, size), rng.randint(0, size)) for _ in range(rng.randint(1, 4))]
        centres = [(cy + jitter.randint(-6, 7), cx + jitter.randint(-6, 7)) for cy, cx in centres]
    else:
        centres = [(size // 2 + jitter.randint(-50, 51), size // 2 + jitter.randint(-50, 51))]
    yy, xx = np.ogrid[:size, :size]
    for diameter in DIAMETERS:
        radius = max(1.0, diameter * size / 400.0)
        mask = np.zeros((size, size), np.uint8)
        for cy, cx in centres:
            if profile == 'scattered':
                lo_y, hi_y = max(0, int(cy - radius)), min(size, int(cy + radius) + 2)
                lo_x, hi_x = max(0, int(cx - radius)), min(size, int(cx + radius) + 2)
                sub = (yy[lo_y:hi_y] - cy) ** 2 + (xx[:, lo_x:hi_x] - cx) ** 2 <= radius ** 2
                mask[lo_y:hi_y, lo_x:hi_x][sub] = 255
            else:
                half_w = int(radius) + 60
                half_h = int(radius)
                mask[max(0, cy - half_h):max(0, cy + half_h + 1), max(0, cx - half_w):max(0, cx + half_w + 1)] = 255
        Image.fromarray(mask).save(case / f'hotspot_lateral_short{diameter:02d}.png', compress_level=1)
    return name


def generate(work, cases, size, profile, side_seeds, workers):
    """Write input/<side> for both models; returns the generation wall seconds."""
    start = time.perf_counter()
    work.mkdir(parents=True, exist_ok=True)
    names = [f'lateral_short-A{i:04d}-L1-B{i:04d}-L1' for i in range(cases)]
    (work / 'expected_cases.txt').write_text('\n'.join(names) + '\n', encoding='utf-8')
    template = work / 'blank_electrode.png'
    _png_bytes_cache(template, size + 1000)
    jobs = []
    for side, seed in side_seeds.items():
        for index, name in enumerate(names):
            jobs.append((str(work / 'input' / side), name, index, size, profile, seed, str(template)))
    with Pool(workers) as pool:
        for _ in pool.imap_unordered(_case, jobs, chunksize=4):
            pass
    return round(time.perf_counter() - start, 1)


def write_config(work, cases, limits, full_detail):
    rng = np.random.RandomState(5)
    assignment = {'product_id': 'FX', 'design_version': 'FX', 'source_id': 'FX', 'process_step_id': 'STEP',
                  'exposure_id': 'EXP', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}], **limits}
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'reports',
           'reference': {'data_dir': str(work / 'imported' / 'reference'), 'condition_assignment': dict(assignment)},
           'target': {'data_dir': str(work / 'imported' / 'target'), 'condition_assignment': dict(assignment)}}
    if full_detail:
        cfg['compare'] = {'full_bucket_detail': True}
    classes = ['line' if rng.rand() < .7 else 'pixel' for _ in range(cases)]
    for side in ('reference', 'target'):
        with (work / 'imported' / side / 'pairs.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('pair_id', 'electrode_a_id', 'electrode_b_id', 'class', 'pair_table_version'))
            for index in range(cases):
                writer.writerow((f'P{index:04d}', f'A{index:04d}', f'B{index:04d}', classes[index], 'bench-pairs-v1'))
    path = work / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return path


def folder_bytes(path):
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def run(label, command, python, out_dir):
    """Run `python -m simcompare <command>` under /usr/bin/time; returns a result dict."""
    time_flag = '-l' if DARWIN else '-v'
    counters = Path(out_dir).parent / f'.io_counters_{label.replace(" ", "_")}.json' if out_dir else None
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    if counters is not None:
        counters.unlink(missing_ok=True)
        env['SIMCOMPARE_IO_COUNTERS'] = str(counters)  # file hashes / mask decodes of this process (A4)
    start = time.perf_counter()
    proc = subprocess.run(['/usr/bin/time', time_flag, python, '-m', 'simcompare', *command],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    wall = time.perf_counter() - start
    rss = None
    if DARWIN:
        match = re.search(r'(\d+)\s+maximum resident set size', proc.stderr)
        rss = int(match.group(1)) / 1048576 if match else None
    else:
        match = re.search(r'Maximum resident set size \(kbytes\): (\d+)', proc.stderr)
        rss = int(match.group(1)) / 1024 if match else None
    error_code = None
    detail = None
    error_json = Path(out_dir) / 'error.json' if out_dir else None
    if proc.returncode not in (0, 3) and error_json is not None and error_json.exists():
        error = json.loads(error_json.read_text(encoding='utf-8'))
        error_code = (error.get('reasons') or [None])[0]
        detail = error.get('detail')
    elif proc.returncode not in (0, 3):
        stderr_lines = [line for line in proc.stderr.splitlines() if line.startswith('오류') or 'Error' in line]
        detail = stderr_lines[-1] if stderr_lines else proc.stderr[-300:]
    execution = Path(out_dir) / 'execution.json' if out_dir else None
    if proc.returncode == 3 and execution is not None and execution.exists():
        detail = ','.join(json.loads(execution.read_text(encoding='utf-8')).get('reasons') or [])
    io = json.loads(counters.read_text(encoding='utf-8')) if counters is not None and counters.exists() else None
    return {'command': label, 'wall_seconds': round(wall, 1), 'peak_rss_mib': round(rss, 1) if rss else None,
            'exit_code': proc.returncode, 'error_code': error_code, 'detail': detail,
            'io_counters': io, 'output_bytes': folder_bytes(out_dir) if out_dir else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--work', required=True, help='work folder (created; must not hold other data)')
    parser.add_argument('--cases', type=int, default=300)
    parser.add_argument('--size', type=int, default=1262)
    parser.add_argument('--profile', choices=('scattered', 'centered'), default='centered')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument('--max-bucket-output-entries', type=int)
    parser.add_argument('--max-operation-bytes', type=int)
    parser.add_argument('--full-bucket-detail', action='store_true')
    parser.add_argument('--keep', action='store_true', help='keep generated data (default: delete at the end)')
    parser.add_argument('--reuse', action='store_true', help='reuse existing input/ and imported/ in --work')
    args = parser.parse_args()
    work = Path(args.work)
    result = {'cases': args.cases, 'size': args.size, 'profile': args.profile, 'python': args.python,
              'limits': {}, 'commands': []}
    limits = {}
    if args.max_bucket_output_entries:
        limits['max_bucket_output_entries'] = args.max_bucket_output_entries
    if args.max_operation_bytes:
        limits['max_operation_bytes'] = args.max_operation_bytes
    result['limits'] = limits
    if not args.reuse:
        if work.exists() and any(work.iterdir()):
            sys.exit(f'{work} is not empty')
        result['generate_seconds'] = generate(work, args.cases, args.size, args.profile,
                                              {'reference': 1, 'target': 2}, args.workers)
        result['input_bytes'] = folder_bytes(work / 'input')
        for side in ('reference', 'target'):
            step = run(f'import-hotspots {side}',
                       ['import-hotspots', '--root', str(work / 'input' / side), '--out', str(work / 'imported' / side),
                        '--expected-cases', str(work / 'expected_cases.txt')], args.python, work / 'imported' / side)
            result['commands'].append(step)
            if step['exit_code'] not in (0, 3):  # import-hotspots always reports 'partial' (3): physical metadata is unset
                break
        result['imported_bytes'] = folder_bytes(work / 'imported')
    ok = all(step['exit_code'] in (0, 3) for step in result['commands'])
    if ok:
        config = write_config(work, args.cases, limits, args.full_bucket_detail)
        for command in ('validate-data', 'compare'):
            out = work / 'out' / command
            step = run(command, [command, '--config', str(config), '--out', str(out)], args.python, out)
            result['commands'].append(step)
            if step['exit_code'] not in (0, 3):
                break
            # exit 3 hides an input that was refused (admission / configured input): a timing of such a run is not a result
            refused = [word for word in ('invalid_configured_input', 'oversized_real') if word in (step['detail'] or '')]
            if refused:
                step['refused'] = refused
                result.setdefault('warnings', []).append(f'{command} refused its input ({refused[0]}): not a valid measurement')
                break
    result['total_command_seconds'] = round(sum(step['wall_seconds'] for step in result['commands']), 1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
