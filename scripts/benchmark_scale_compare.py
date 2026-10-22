#!/usr/bin/env python3
"""Real-scale benchmark, one CLI process per command.

    python scripts/benchmark_scale_compare.py --work /tmp/bench --cases 300 --size 1262 --profile centered [--keep]
        [--max-bucket-output-entries N] [--max-operation-bytes N] [--python PYTHON] [--full-bucket-detail]
        [--stages 1-2|1-6] [--electrodes distinct|shared] [--target-shape H W] [--pairs 50 200]
        [--stage6-runs SPEC ...] [--window-sizes 10 20 30] [--jitter-um 0.5]

`--stages 1-2` (default): import-hotspots -> validate-data -> compare (the original Phase A benchmark).
`--stages 1-6` (Phase B, plan_v3_1 B4): additionally import-electrode-assets (3), candidates (4), confirm-pairs (5, from fake DL2
matches built out of the stage-4 exports) and compare-windows (6) with and without the stage-2 `comparison_masks.zip`, against a
stage-5-produced correspondence AND a hand-labelled one (both endpoint bindings, repeats). Every stage's counts are asserted
(ready ROIs, queries, confirmed pairs, exactly ready*sizes*32 rows, named-pair / jitter / explained branches ran) because exit code 3
alone is not success; a failed assertion lands in `warnings` and `assertions_failed`. Stage 6 also runs a guard-refusal boundary
(limit one byte below the reported estimate must refuse before any heavy call).

Data spec (fixed here): per model `--cases` cases at crop `--size` (µm * 10 pixels), 32 diameters (1..30, 40, 50), one layer
`L1`, distinct electrode pair `A<i>`/`B<i>` per case, 70 % line / 30 % pixel pairs. Reference and target use different seeds.
  scattered: 1-3 circular blobs at random places, shared by 3 consecutive cases (make_compare_fixture behaviour).
  components: three lattice sites per case (a G x G lattice, spacing ~size/(G+1)), circle radius as `centered`; at 5 um the
             sites are separated components with many electrodes each (stage 4 gets many regions, stage 5 many queries).
  centered : one band (rectangle) per case at the crop centre with +-50 px jitter; half-height = diameter-growing radius
             r = max(1, d * size / 400), half-width = r + 60 px, so most cases overlap like real crops.
Each command runs as its own process under `/usr/bin/time -l` (macOS) or `-v` (Linux): wall, isolated peak RSS, exit code,
error code (from error.json / stderr), output sizes, and the file-hash / mask-decode counts of the process
(`io_counters`, via SIMCOMPARE_IO_COUNTERS). Prints one JSON object; use --keep to retain the data.
"""
import argparse
import csv
import hashlib
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
PITCH = 0.1
ORIGIN = (50.0, 50.0)
FRAME_ID = 'real_hotspot_crop_v1'
LATTICE = 14
PRODUCTS = {'reference': 'PREF', 'target': 'PTGT'}


def _layout_png(path, electrode, shape):
    """Layout of one electrode (black = shape) on the crop + 1000 px border frame, like tests/stage46_generators.electrode_layout."""
    h, w = shape
    rng = np.random.RandomState(int(hashlib.sha256(electrode.encode()).hexdigest()[:8], 16))
    full = np.full((h + 1000, w + 1000), 255, np.uint8)
    r, c = 500 + rng.randint(0, max(1, h - 4)), 500 + rng.randint(0, max(1, w - 6))
    full[r:r + 3, c:c + 5] = 0
    full[10:12, 10:12] = 0
    Image.fromarray(full).save(path, compress_level=1)


def _sites(shape):
    h, w = shape
    return [((i + 1) * h // (LATTICE + 1), (j + 1) * w // (LATTICE + 1)) for i in range(LATTICE) for j in range(LATTICE)]


def _centres(profile, shape, rng, jitter):
    h, w = shape
    if profile == 'scattered':
        centres = [(rng.randint(0, h), rng.randint(0, w)) for _ in range(rng.randint(1, 4))]
        return [(cy + jitter.randint(-6, 7), cx + jitter.randint(-6, 7)) for cy, cx in centres]
    if profile == 'components':
        sites = _sites(shape)
        return [sites[k] for k in sorted(set(int(v) for v in jitter.randint(0, len(sites), 3)))]
    return [(h // 2 + jitter.randint(-50, 51), w // 2 + jitter.randint(-50, 51))]


def _case(args):
    """Write one case folder (hotspot PNGs, a csv whose areas are the pixel counts x pitch^2, electrode layout copies)."""
    root, name, index, shape, profile, seed, layout_a, layout_b = args
    h, w = shape
    rng = np.random.RandomState(seed * 100003 + index // (3 if profile == 'scattered' else 1))
    jitter = np.random.RandomState(seed * 7919 + index)
    case = Path(root) / name
    (case / 'result').mkdir(parents=True)
    shutil.copyfile(layout_a, case / '1.png')
    shutil.copyfile(layout_b, case / '2.png')
    centres = _centres(profile, shape, rng, jitter)
    yy, xx = np.ogrid[:h, :w]
    rows = []
    for diameter in DIAMETERS:
        radius = max(1.0, diameter * max(h, w) / 400.0)
        mask = np.zeros((h, w), np.uint8)
        for cy, cx in centres:
            if profile == 'centered':
                half_w, half_h = int(radius) + 60, int(radius)
                mask[max(0, cy - half_h):max(0, cy + half_h + 1), max(0, cx - half_w):max(0, cx + half_w + 1)] = 255
            else:
                lo_y, hi_y = max(0, int(cy - radius)), min(h, int(cy + radius) + 2)
                lo_x, hi_x = max(0, int(cx - radius)), min(w, int(cx + radius) + 2)
                sub = (yy[lo_y:hi_y] - cy) ** 2 + (xx[:, lo_x:hi_x] - cx) ** 2 <= radius ** 2
                mask[lo_y:hi_y, lo_x:hi_x][sub] = 255
        Image.fromarray(mask).save(case / f'hotspot_lateral_short{diameter:02d}.png', compress_level=1)
        rows.append((diameter, f'{int(np.count_nonzero(mask)) * PITCH * PITCH:.2f}'))
    with (case / 'result' / 'lateral_short.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('defect_size(um)', 'Area(um2)'))
        writer.writerows(rows)
    return name


def _layout_job(args):
    _layout_png(*args)


def model_cases(side, cases, electrodes):
    """[(electrode_a, electrode_b)] for one side. distinct: A<i>/B<i> (reference), C<i>/D<i> (target); shared: unordered pairs of a
    small pool, so every electrode sits in many cases."""
    if electrodes == 'shared':
        pool = 2
        while pool * (pool - 1) // 2 < cases:
            pool += 1
        names = [f'{"E" if side == "reference" else "F"}{k:03d}' for k in range(pool)]
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
        np.random.RandomState(3 if side == 'reference' else 4).shuffle(pairs)
        return pairs[:cases]
    first, second = ('A', 'B') if side == 'reference' else ('C', 'D')
    return [(f'{first}{i:04d}', f'{second}{i:04d}') for i in range(cases)]


def case_name(pair):
    return f'lateral_short-{pair[0]}-L1-{pair[1]}-L1'


def generate(work, cases, shapes, profile, electrodes, workers):
    """Write input/<side> for both models; returns (generation wall seconds, {side: [(ea, eb)]})."""
    start = time.perf_counter()
    work.mkdir(parents=True, exist_ok=True)
    layout_jobs, jobs, pairs = [], [], {}
    for side, seed in (('reference', 1), ('target', 2)):
        pairs[side] = model_cases(side, cases, electrodes)
        (work / f'expected_cases_{side}.txt').write_text('\n'.join(case_name(p) for p in pairs[side]) + '\n', encoding='utf-8')
        layouts = work / 'layouts' / side
        layouts.mkdir(parents=True)
        for electrode in sorted({e for p in pairs[side] for e in p}):
            layout_jobs.append((layouts / f'{electrode}.png', electrode, shapes[side]))
        for index, pair in enumerate(pairs[side]):
            jobs.append((str(work / 'input' / side), case_name(pair), index, shapes[side], profile, seed,
                         str(layouts / f'{pair[0]}.png'), str(layouts / f'{pair[1]}.png')))
    with Pool(workers) as pool:
        for _ in pool.imap_unordered(_layout_job, layout_jobs, chunksize=16):
            pass
        for _ in pool.imap_unordered(_case, jobs, chunksize=4):
            pass
    return round(time.perf_counter() - start, 1), pairs


def assignment_for(product, limits):
    return {'product_id': product, 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
            'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
            'electrode_role_policy': 'electrode_id',
            'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}], **limits}


def write_pair_tables(work, pairs):
    """One row per electrode pair, class line / pixel 70:30 (seeded)."""
    rng = np.random.RandomState(5)
    for side in ('reference', 'target'):
        with (work / 'imported' / side / 'pairs.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('pair_id', 'electrode_a_id', 'electrode_b_id', 'class', 'pair_table_version'))
            for index, (a, b) in enumerate(pairs[side]):
                writer.writerow((f'P{index:04d}', a, b, 'line' if rng.rand() < .7 else 'pixel', 'bench-pairs-v1'))


def write_config(work, limits, full_detail, name, output_dir, with_assets=False, map_diameter=None):
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': str(output_dir)}
    if map_diameter is not None:
        cfg['map'] = {'diameter_um': map_diameter}
    for side in ('reference', 'target'):
        spec = {'data_dir': str(work / 'imported' / side), 'product_id': PRODUCTS[side],
                'condition_assignment': assignment_for(PRODUCTS[side], limits)}
        if with_assets:
            for key, filename in (('asset_manifest', 'review_assets.json'),
                                  ('electrode_import_record', 'electrode_asset_import_record.json')):
                spec[key] = {'path': filename, 'sha256': hashlib.sha256((work / 'imported' / side / filename).read_bytes()).hexdigest()}
        cfg[side] = spec
    if full_detail:
        cfg['compare'] = {'full_bucket_detail': True}
    path = work / name
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return path


def folder_bytes(path):
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())


def run(label, command, python, out_dir, extra_env=None):
    """Run `python -m simcompare <command>` under /usr/bin/time; returns a result dict."""
    time_flag = '-l' if DARWIN else '-v'
    counters = Path(out_dir).parent / f'.io_counters_{re.sub(r"[^A-Za-z0-9_.-]", "_", label)}.json' if out_dir else None
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), **(extra_env or {}))
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
            'io_counters': io, 'output_bytes': folder_bytes(out_dir) if out_dir else None,
            'stdout': stdout_json(proc.stdout)}



DIRECTIONS = {'reference_to_target': ('reference', 'target'), 'target_to_reference': ('target', 'reference')}
SEEDED_TOPK = 3


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def stdout_json(proc_text):
    try:
        return json.loads(proc_text)
    except ValueError:
        return None


def fake_matches(candidates_dir, out_dir, shapes, seed, margin_um):
    """psdl2_baseline_ncc_v1 `all_ready` match files built from the stage-4 exports (DL2 itself is never run): per ready query three
    candidate centres next to ready index ROIs, clamped inside the index frame by margin_um so every pair is window-ready."""
    rng = np.random.RandomState(seed)
    paths, queries = {}, {}
    for direction, (query_name, index_name) in DIRECTIONS.items():
        query = load_json(Path(candidates_dir) / f'{query_name}_export' / 'review_rois.json')
        index = load_json(Path(candidates_dir) / f'{index_name}_index_export' / 'review_rois.json')
        index_rows = [r for r in index['rois'] if r['status'] == 'ready']
        h, w = shapes[index_name]
        x0, y0, x1, y1 = ORIGIN[0] + margin_um, ORIGIN[1] + margin_um, ORIGIN[0] + w * PITCH - margin_um, ORIGIN[1] + h * PITCH - margin_um
        results = []
        for row in sorted((r for r in query['rois'] if r['status'] == 'ready'), key=lambda r: r['roi_id']):
            picks = []
            for _ in range(SEEDED_TOPK):
                cx, cy = index_rows[rng.randint(len(index_rows))]['center_xy_um'] if index_rows else (x0, y0)
                picks.append((round(float(min(max(cx + rng.uniform(-2, 2), x0), x1)), 4),
                              round(float(min(max(cy + rng.uniform(-2, 2), y0), y1)), 4)))
            scores = sorted(rng.rand(SEEDED_TOPK).tolist(), reverse=True)
            results.append({
                'query': {'roi_id': row['roi_id'], 'product_id': row['product_id'], 'frame_id': row['frame_id'],
                          'center_xy_um': row['center_xy_um']},
                'status': 'available', 'reasons': [],
                'primary': [{'rank': rank, 'score': scores[rank - 1], 'center_xy_um': list(c),
                             'product_id': PRODUCTS[index_name], 'frame_id': FRAME_ID,
                             'template_top_left_px_yx': [0, 0]} for rank, c in enumerate(picks, 1)],
                'endpoints_used': [], 'template_sha256': 'f' * 64, 'shared_with': [],
                'flags': {'low_structure': False, 'window_clipped': False, 'query_valid_hole': False,
                          'candidate_valid_hole': False, 'score_gap': scores[0] - scores[1], 'near_equal_peaks': True},
                'query_invalid_pixel_count': 0})
        queries[direction] = len(results)
        payload = {'schema_version': 'psdl2_baseline_ncc_v1', 'method': 'zncc_fft_role_layer_union',
                   'implementation_revision': 'baseline_ncc_batch_v1.3', 'mode': 'all_ready', 'window_um': 1.0,
                   'topk': SEEDED_TOPK, 'candidate_product_id': PRODUCTS[index_name], 'n_queries': len(results),
                   'status': 'available', 'results': results,
                   'inputs': {'query_export_sha256': sha256_file(Path(candidates_dir) / f'{query_name}_export' / 'review_rois.json'),
                              'index_export_sha256': sha256_file(Path(candidates_dir) / f'{index_name}_index_export' / 'review_rois.json')},
                   'arguments': {'window_um': 1.0, 'topk': SEEDED_TOPK, 'self_check': False}}
        path = Path(out_dir) / f'{direction}.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        paths[direction] = path
    return paths, queries


def decisions_file(path, matches, accept):
    """decisions.csv accepting exactly `accept` candidates (rank 1 of every query first, then rank 2, 3), round-robin over directions."""
    per_direction = {}
    for direction, match in sorted(matches.items()):
        entries = sorted(load_json(match)['results'], key=lambda e: e['query']['roi_id'])
        per_direction[direction] = [f'{direction}:{e["query"]["roi_id"]}:r{c["rank"]}' for rank in range(1, SEEDED_TOPK + 1)
                                    for e in entries for c in e['primary'] if c['rank'] == rank]
    chosen, cursors = [], {d: 0 for d in per_direction}
    while len(chosen) < accept and any(cursors[d] < len(per_direction[d]) for d in per_direction):
        for direction in sorted(per_direction):
            if len(chosen) < accept and cursors[direction] < len(per_direction[direction]):
                chosen.append(per_direction[direction][cursors[direction]])
                cursors[direction] += 1
    with Path(path).open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['candidate_id', 'decision', 'note'])
        writer.writerows((c, 'accept', 'ok') for c in chosen)
    return len(chosen)


def labelled_correspondence(path, pairs, shapes, profile, count, seed, margin_um):
    """roi_correspondence_v1 hand-labelled: every pair binds BOTH endpoints (so the named-pair branch runs), repeats_per_frame on every
    pair, centres where the profile puts hotspots (centered: crop centre; components/scattered: lattice sites / anywhere)."""
    rng = np.random.RandomState(seed)

    def centre(side):
        h, w = shapes[side]
        if profile == 'centered':
            row, col = h / 2 + rng.uniform(-60, 60), w / 2 + rng.uniform(-60, 60)
        elif profile == 'components':
            sites = _sites(shapes[side])
            row, col = sites[rng.randint(len(sites))]
            row, col = row + rng.uniform(-20, 20), col + rng.uniform(-20, 20)
        else:
            row, col = rng.uniform(0, h), rng.uniform(0, w)
        x, y = ORIGIN[0] + col * PITCH, ORIGIN[1] + row * PITCH
        return (round(min(max(x, ORIGIN[0] + margin_um), ORIGIN[0] + w * PITCH - margin_um), 4),
                round(min(max(y, ORIGIN[1] + margin_um), ORIGIN[1] + h * PITCH - margin_um), 4))

    def side_payload(side):
        ea, eb = pairs[side][rng.randint(len(pairs[side]))]
        x, y = centre(side)
        return {'product_id': PRODUCTS[side], 'design_version': 'DESIGN', 'frame_id': FRAME_ID, 'center_xy_um': [x, y],
                'analysis_layer': 'L1', 'failure_type': 'lateral_short',
                'endpoints': [{'electrode_id': ea, 'role': ea, 'actual_layer': 'L1'},
                              {'electrode_id': eb, 'role': eb, 'actual_layer': 'L1'}]}
    out = []
    for n in range(count):
        out.append({'pair_id': f'P{n:04d}', 'a': side_payload('reference'), 'b': side_payload('target'),
                    'source': {'kind': 'manual', 'labelled_by': 'designer', 'labelled_at': '2026-10-22'},
                    'status': 'ready', 'reasons': [],
                    'repeats_per_frame': {'a': int(rng.randint(1, 4)), 'b': int(rng.randint(1, 4))}})
    Path(path).write_text(json.dumps({'schema_version': 'roi_correspondence_v1',
                                      'producer': {'kind': 'manual', 'version': '1', 'commit': None, 'created_at': '2026-10-22'},
                                      'pairs': out}, ensure_ascii=False), encoding='utf-8')
    return Path(path)


def peak_disk(work):
    return folder_bytes(work)


class Run:
    """Collects the command results and the failed assertions of one benchmark."""

    def __init__(self, python):
        self.python, self.commands, self.failed, self.notes, self.disk_peak = python, [], [], {}, 0

    def step(self, work, label, command, out_dir=None, env=None, ok=(0, 3)):
        result = run(label, command, self.python, out_dir, env)
        result['disk_after_bytes'] = folder_bytes(work)
        self.disk_peak = max(self.disk_peak, result['disk_after_bytes'])
        self.commands.append(result)
        if result['exit_code'] not in ok:
            self.failed.append(f'{label}: exit {result["exit_code"]} {result.get("error_code") or ""} {result.get("detail") or ""}')
        return result

    def check(self, condition, message):
        if not condition:
            self.failed.append(message)
        return bool(condition)


def stage6_checks(bench, label, out_dir, sizes, jitter, corr_path, reused, decodes_expected):
    """Exit code 3 alone is not success: the outputs of one compare-windows run are measured against its own inputs."""
    doc = load_json(Path(out_dir) / 'local_delta.json')
    corr = load_json(corr_path)
    ready = sum(1 for p in corr['pairs'] if p['status'] == 'ready')
    rows = doc['rows']
    facts = {'pairs_in_file': len(corr['pairs']), 'ready_pairs': ready, 'provenance_n_ready': doc['provenance']['n_ready'],
             'n_rows': doc['n_rows'], 'rows_expected': ready * len(sizes) * len(DIAMETERS),
             'named_pair_rows': sum(1 for r in rows if r.get('named_pair')),
             'jitter_rows': sum(1 for r in rows if r.get('jitter')),
             'explained_present': bool(doc.get('explained_fraction')), 'status': doc['status'],
             'both_endpoint_pairs': sum(1 for p in corr['pairs'] if p['a'].get('endpoints') and p['b'].get('endpoints'))}
    bench.check(facts['provenance_n_ready'] == ready, f'{label}: n_ready {facts["provenance_n_ready"]} != ready pairs in file {ready}')
    bench.check(facts['n_rows'] == len(rows) == facts['rows_expected'], f'{label}: rows {len(rows)} (n_rows {facts["n_rows"]}) != {facts["rows_expected"]}')
    bench.check(facts['jitter_rows'] == len(rows) if jitter else True, f'{label}: jitter ran on {facts["jitter_rows"]} of {len(rows)} rows')
    bench.check(facts['named_pair_rows'] > 0 or facts['both_endpoint_pairs'] == 0, f'{label}: named-pair branch did not run')
    bench.check(facts['explained_present'], f'{label}: explained_fraction branch did not run')
    leftovers = sorted(p.name for p in Path(out_dir).iterdir() if p.name.startswith('.'))
    bench.check(not leftovers, f'{label}: leftover control files {leftovers}')
    if decodes_expected is not None:
        facts['decodes_expected_for'] = decodes_expected
    return facts


def guard_boundary(bench, work, cfg_path, corr_path, sizes, jitter, name, limits_key='max_operation_bytes'):
    """Plan B3: the guard refuses BEFORE any compute. limit 1 -> estimate X1; limit X1-1 must refuse citing X1 in seconds (no
    mask decode beyond the load validation, no output); limit X1 either runs or refuses with a larger X2 of the next phase."""
    cfg = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8'))
    report = []

    def attempt(limit, tag):
        trial = json.loads(json.dumps(cfg))
        for side in ('reference', 'target'):
            trial[side]['condition_assignment'][limits_key] = limit
        path = work / f'config_guard_{tag}.yaml'
        path.write_text(yaml.safe_dump(trial), encoding='utf-8')
        out = work / 'out' / f'guard_{name}_{tag}'
        result = bench.step(work, f'guard {name} limit={limit}', ['compare-windows', '--config', str(path), '--correspondence', str(corr_path),
                                                       '--window-sizes', *[str(s) for s in sizes], '--jitter-um', str(jitter),
                                                       '--sort-diameter-um', '5', '--out', str(out)], out, ok=(0, 2, 3))
        match = re.search(r'estimated (\d+) bytes exceed (?:limit )?(\d+)', (result.get('detail') or '') + ' ')
        entry = {'limit': limit, 'exit_code': result['exit_code'], 'estimate': int(match.group(1)) if match else None,
                 'wall_seconds': result['wall_seconds'], 'peak_rss_mib': result['peak_rss_mib'],
                 'published': sorted(p.name for p in out.iterdir() if p.name.startswith(('local_delta', 'window_sensitivity'))) if out.exists() else []}
        report.append(entry)
        return entry
    first = attempt(1, 'one')
    if not bench.check(first['exit_code'] == 2 and first['estimate'], f'guard {name}: limit 1 did not refuse with an estimate: {first}'):
        return report
    below = attempt(first['estimate'] - 1, 'below')
    bench.check(below['exit_code'] == 2 and below['estimate'] == first['estimate'] and not below['published'],
                f'guard {name}: one byte below the estimate did not refuse the same estimate without outputs: {below}')
    limit = first['estimate']
    for tag in ('at', 'at2', 'at3'):  # each later phase (compute, rows, report) quotes its own, larger estimate
        entry = attempt(limit, tag)
        entry['accepted'] = entry['exit_code'] in (0, 3)
        if entry['accepted'] or not entry['estimate'] or entry['estimate'] <= limit:
            break
        limit = entry['estimate']
    return report


def parse_stage_range(text):
    first, _, last = text.partition('-')
    return int(first), int(last or first)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--work', required=True, help='work folder (created; must not hold other data)')
    parser.add_argument('--cases', type=int, default=300)
    parser.add_argument('--size', type=int, default=1262)
    parser.add_argument('--target-shape', type=int, nargs=2, metavar=('H', 'W'),
                        help='crop of the target model in pixels (default: --size x --size); unequal frames when it differs')
    parser.add_argument('--profile', choices=('scattered', 'centered', 'components'), default='centered')
    parser.add_argument('--electrodes', choices=('distinct', 'shared'), default='distinct')
    parser.add_argument('--stages', default='1-2', help='1-2 (default, Phase A) or 1-6')
    parser.add_argument('--pairs', type=int, nargs='+', default=[50, 200], help='stage 5 / labelled pair counts P (stage 6)')
    parser.add_argument('--stage6-runs', nargs='*', metavar='P:FIXTURE:REUSE',
                        help='FIXTURE stage5|labelled, REUSE artefact|recompute; default: every P labelled+artefact, stage5+artefact, '
                             'labelled+recompute')
    parser.add_argument('--window-sizes', type=float, nargs='+', default=[10.0, 20.0, 30.0])
    parser.add_argument('--jitter-um', type=float, default=0.5)
    parser.add_argument('--min-count', type=int, default=3)
    parser.add_argument('--window-um', type=float, default=6.0, help='stage 4 query window (um); must be below the region spacing '
                        'of the components lattice (~8 um at 1262 px) or every region merges into one query')
    parser.add_argument('--merge-overlap', type=float, default=0.5)
    parser.add_argument('--guard-boundary', type=int, metavar='P', help='guard-refusal bracket on a labelled fixture with P pairs (recompute)')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument('--max-bucket-output-entries', type=int)
    parser.add_argument('--max-operation-bytes', type=int)
    parser.add_argument('--full-bucket-detail', action='store_true')
    parser.add_argument('--keep', action='store_true', help='keep generated data (default: delete at the end)')
    parser.add_argument('--reuse', action='store_true', help='reuse existing input/ and imported/ in --work')
    args = parser.parse_args()
    work = Path(args.work)
    first_stage, last_stage = parse_stage_range(args.stages)
    shapes = {'reference': (args.size, args.size), 'target': tuple(args.target_shape or (args.size, args.size))}
    limits = {}
    if args.max_bucket_output_entries:
        limits['max_bucket_output_entries'] = args.max_bucket_output_entries
    if args.max_operation_bytes:
        limits['max_operation_bytes'] = args.max_operation_bytes
    result = {'cases': args.cases, 'size': args.size, 'shapes': shapes, 'profile': args.profile, 'electrodes': args.electrodes,
              'stages': args.stages, 'python': args.python, 'limits': limits, 'commands': []}
    bench = Run(args.python)
    bench.commands = result['commands']
    started = time.perf_counter()
    try:
        if not args.reuse:
            if work.exists() and any(work.iterdir()):
                sys.exit(f'{work} is not empty')
            result['generate_seconds'], pairs = generate(work, args.cases, shapes, args.profile, args.electrodes, args.workers)
            result['input_bytes'] = folder_bytes(work / 'input')
            for side in ('reference', 'target'):
                step = bench.step(work, f'import-hotspots {side}',
                                  ['import-hotspots', '--root', str(work / 'input' / side), '--out', str(work / 'imported' / side),
                                   '--expected-cases', str(work / f'expected_cases_{side}.txt')], work / 'imported' / side)
                if step['exit_code'] not in (0, 3):  # import-hotspots always reports 'partial' (3): physical metadata is unset
                    break
            result['imported_bytes'] = folder_bytes(work / 'imported')
            if bench.failed:
                return finish(args, work, result, bench, started)
            write_pair_tables(work, pairs)
        else:
            pairs = {side: model_cases(side, args.cases, args.electrodes) for side in ('reference', 'target')}
        compare_dir = work / 'out' / 'compare'
        cfg2 = write_config(work, limits, args.full_bucket_detail, 'config_stage2.yaml', compare_dir)
        if last_stage >= 2:
            step = bench.step(work, 'validate-data', ['validate-data', '--config', str(cfg2), '--out', str(work / 'out' / 'validate')],
                              work / 'out' / 'validate')
            refused = [w for w in ('invalid_configured_input', 'oversized_real') if w in (step['detail'] or '')]
            if refused:  # exit 3 hides an input that was refused: a timing of such a run is not a result
                step['refused'] = refused
                bench.failed.append(f'validate-data refused its input ({refused[0]}): not a valid measurement')
                return finish(args, work, result, bench, started)
            step = bench.step(work, 'compare', ['compare', '--config', str(cfg2)], compare_dir)
            if step['exit_code'] not in (0, 3):
                return finish(args, work, result, bench, started)
            step['artefact_bytes'] = folder_bytes(compare_dir / 'comparison_masks.zip')
            result['disk_peak_bytes'] = bench.disk_peak
        if last_stage >= 3:
            cnt = work / 'cnt'
            for side in ('reference', 'target'):
                (cnt / PRODUCTS[side] / 'cno').mkdir(parents=True)
                _layout_png(cnt / PRODUCTS[side] / 'cno' / '1_CNT.png', 'CNT', shapes[side])
                assignment = work / f'assignment_{side}.json'
                assignment.write_text(json.dumps(assignment_for(PRODUCTS[side], limits)), encoding='utf-8')
                out = work / 'assets' / side
                step = bench.step(work, f'import-electrode-assets {side}',
                                  ['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(work / 'input' / side),
                                   '--numeric-data-dir', str(work / 'imported' / side), '--condition-assignment', str(assignment),
                                   '--cnt-from-dl1-root', str(cnt), '--source-version', 'LAYOUT_V1', '--max-asset-memory-mb', '16384',
                                   '--out', str(out)], out)
                if step['exit_code'] not in (0, 3):
                    return finish(args, work, result, bench, started)
                for name in ('review_assets.json', 'electrode_asset_import_record.json'):
                    shutil.copyfile(out / name, work / 'imported' / side / name)
                shutil.copytree(out / 'assets', work / 'imported' / side / 'assets', dirs_exist_ok=True)
                step['assets_files'] = sum(1 for p in (out / 'assets').iterdir())
            cfg = write_config(work, limits, args.full_bucket_detail, 'config.yaml', compare_dir, with_assets=True, map_diameter=5)
        if last_stage >= 4:
            cand = work / 'out' / 'candidates'
            step = bench.step(work, 'candidates', ['candidates', '--config', str(cfg), '--min-count', str(args.min_count),
                                                  '--window-um', str(args.window_um), '--merge-overlap', str(args.merge_overlap),
                                                  '--out', str(cand)], cand)
            if step['exit_code'] not in (0, 3):
                return finish(args, work, result, bench, started)
            result['stage4'] = stage4_facts(bench, cand, step)
        sizes, jitter = args.window_sizes, args.jitter_um
        margin = max(sizes) / 2 + jitter + 0.05
        corr_files = {}
        if last_stage >= 5:
            matches, queries = fake_matches(cand, work, shapes, 11, margin)
            result['stage5'] = {'queries': queries, 'runs': {}}
            if not bench.check(sum(queries.values()) > 0, 'stage 4 produced no ready query ROI'):
                return finish(args, work, result, bench, started)
            for count in args.pairs:
                accepted = decisions_file(work / f'decisions_P{count}.csv', matches, count)
                out = work / 'out' / f'confirm_P{count}'
                step = bench.step(work, f'confirm-pairs P={count}',
                                  ['confirm-pairs', '--config', str(cfg), '--candidates', str(cand),
                                   *[a for d, p in matches.items() for a in ('--match', f'{d}={p}')],
                                   '--decisions', str(work / f'decisions_P{count}.csv'), '--labelled-by', 'bench',
                                   '--mutual-tolerance-um', '5', '--out', str(out)], out)
                corr = out / 'roi_correspondence.json'
                facts = {'accepted_decisions': accepted, 'exit_code': step['exit_code'], 'output_bytes': step['output_bytes']}
                if corr.exists():
                    pairs_in = load_json(corr)['pairs']
                    facts.update(pairs=len(pairs_in), ready=sum(1 for p in pairs_in if p['status'] == 'ready'),
                                 both_endpoints=sum(1 for p in pairs_in if p['a'].get('endpoints') and p['b'].get('endpoints')))
                    corr_files[(count, 'stage5')] = corr
                    # accepted decisions of the two directions that name the same position collapse into one pair
                    bench.check(0 < facts['ready'] == facts['pairs'] <= accepted and facts['pairs'] >= accepted // 2,
                                f'confirm-pairs P={count}: {facts["pairs"]} pairs ({facts["ready"]} ready) for {accepted} accepted decisions')
                else:
                    bench.failed.append(f'confirm-pairs P={count}: no roi_correspondence.json')
                result['stage5']['runs'][str(count)] = facts
            result['disk_peak_bytes'] = bench.disk_peak
        if last_stage >= 6:
            for count in sorted(set(args.pairs) | ({args.guard_boundary} if args.guard_boundary else set())):
                corr_files[(count, 'labelled')] = labelled_correspondence(work / f'labelled_P{count}.json', pairs, shapes, args.profile,
                                                                         count, 100 + count, margin)
            specs = args.stage6_runs
            if specs is None:
                specs = [f'{p}:{f}:{r}' for p in args.pairs for f, r in (('labelled', 'artefact'), ('stage5', 'artefact'), ('labelled', 'recompute'))]
            result['stage6'] = []
            artefact = compare_dir / 'comparison_masks.zip'
            hidden = compare_dir / 'comparison_masks.zip.hidden'
            for spec in specs:
                count_text, fixture, reuse = spec.split(':')
                count = int(count_text)
                label = f'compare-windows P={count} {fixture} {reuse}'
                corr_path = corr_files.get((count, fixture))
                if corr_path is None:
                    bench.failed.append(f'{label}: no correspondence fixture')
                    continue
                if reuse == 'recompute':
                    artefact.rename(hidden)
                out = work / 'out' / f'windows_P{count}_{fixture}_{reuse}'
                try:
                    step = bench.step(work, label, ['compare-windows', '--config', str(cfg), '--correspondence', str(corr_path),
                                                    '--window-sizes', *[str(s) for s in sizes], '--jitter-um', str(jitter),
                                                    '--sort-diameter-um', '5', '--out', str(out)], out)
                finally:
                    if hidden.exists():
                        hidden.rename(artefact)
                if step['exit_code'] in (0, 3):
                    step['checks'] = stage6_checks(bench, label, out, sizes, jitter, corr_path, reuse == 'artefact', None)
                if not args.keep:
                    shutil.rmtree(out, ignore_errors=True)
            if args.guard_boundary:
                artefact.rename(hidden)
                try:
                    result['guard_boundary'] = guard_boundary(bench, work, cfg, corr_files[(args.guard_boundary, 'labelled')], sizes, jitter,
                                                              f'P{args.guard_boundary}')
                finally:
                    if hidden.exists():
                        hidden.rename(artefact)
    finally:
        pass
    return finish(args, work, result, bench, started)


def stage4_facts(bench, cand, step):
    """What the candidates run produced: ready/blocked ROI rows and queries per export, funnel numbers from stdout."""
    facts = {'exit_code': step['exit_code'], 'output_bytes': step['output_bytes'], 'exports': {}}
    for export in sorted(cand.glob('*_export')):
        doc = load_json(export / 'review_rois.json')
        rows = doc['rois']
        facts['exports'][export.name] = {'rows': len(rows), 'ready': sum(1 for r in rows if r['status'] == 'ready'),
                                         'bytes': (export / 'review_rois.json').stat().st_size}
    execution = cand / 'execution.json'
    if execution.exists():
        payload = load_json(execution)
        facts['funnel'] = payload.get('funnel')
    bench.check(any(v['ready'] > 0 for v in facts['exports'].values()), 'candidates: no ready ROI in any export')
    return facts


def finish(args, work, result, bench, started):
    decodes = {}
    for step in result['commands']:
        counters = step.get('io_counters') or {}
        for kind in ('artefact', 'recompute'):
            if step['command'].startswith('compare-windows') and step['command'].endswith(kind) and 'mask_decodes' in counters:
                decodes.setdefault(kind, []).append(counters['mask_decodes'])
    if decodes.get('artefact') and decodes.get('recompute'):  # the artefact must actually save the second decode pass
        result['artefact_decodes'] = {k: v for k, v in decodes.items()}
        bench.check(max(decodes['artefact']) < min(decodes['recompute']), f'artefact reuse did not reduce mask decodes: {decodes}')
    result['total_command_seconds'] = round(sum(step['wall_seconds'] for step in result['commands']), 1)
    result['wall_seconds_total'] = round(time.perf_counter() - started, 1)
    result['disk_peak_bytes'] = max(bench.disk_peak, result.get('disk_peak_bytes', 0))
    if bench.failed:
        result['assertions_failed'] = bench.failed
        result['warnings'] = bench.failed
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
