"""Seeded generators for the stage 4-6 equivalence oracle (tests/test_stage46_equivalence.py).

Everything here is DATA generation: real-style hotspot trees -> import-hotspots -> import-electrode-assets -> one
data_dir per model, plus configs, correspondences, pair tables and DL2-style match files. The generation always runs
through the FROZEN 7bcb07a package (reference_stage46) so that later changes to the production adapters cannot
change the fixtures the oracle is compared on.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image
import yaml

sys.path.insert(0, str(Path(__file__).parent / 'reference_stage46'))
import loader  # noqa: E402

DESIGN = 'DESIGN'
FRAME_ID = 'real_hotspot_crop_v1'
PITCH = 0.1
ORIGIN = (50.0, 50.0)
DIAMETERS = (*range(1, 31), 40, 50)
KINDS = ('blob', 'comb', 'dense', 'many', 'full', 'empty', 'centered')


def frozen():
    loader.load()
    import simcompare_7bcb07a as pkg  # noqa: F401
    return sys.modules['simcompare_7bcb07a']


@dataclass(frozen=True)
class CaseSpec:
    ea: str
    eb: str
    la: str = 'L1'
    lb: str = 'L1'
    defect: str = 'lateral_short'
    kind: str = 'blob'
    seed: int = 0
    csv: str = 'ok'                 # ok | missing | mismatch
    drop: tuple = ()                # diameters without a hotspot image
    layouts: bool = True            # 1.png/2.png present

    @property
    def name(self):
        return f'{self.defect}-{self.ea}-{self.la}-{self.eb}-{self.lb}'


@dataclass
class ModelSpec:
    product_id: str
    shape: tuple                    # hotspot crop (h, w) in pixels
    cases: list
    layers: tuple = (('L1', 1, 'P1'),)
    roles: object = 'default'       # 'default' | dict | None (no roles) | 'policy'
    expected: object = 'all'        # 'all' | None | list of case names
    pair_classes: dict = field(default_factory=dict)   # frozenset -> 'line'|'pixel'


@dataclass
class ModelData:
    spec: ModelSpec
    data_dir: Path
    assignment: dict
    root: Path


def _dilate(mask, radius):
    if radius <= 0 or not mask.any():
        return mask.copy()
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            ys, yd = (slice(max(dy, 0), h), slice(0, h - max(dy, 0))) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
            xs, xd = (slice(max(dx, 0), w), slice(0, w - max(dx, 0))) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
            out[ys, xs] |= mask[yd, xd]
    return out


def base_mask(shape, kind, seed):
    rng = np.random.RandomState(seed)
    h, w = shape
    m = np.zeros(shape, bool)
    if kind == 'empty':
        return m
    if kind == 'full':
        m[:] = True
        return m
    if kind == 'centered':          # the same central blob for every case: high link volume on one region
        m[h // 2 - 1:h // 2 + 2, w // 2 - 2:w // 2 + 3] = True
        return m
    if kind == 'blob':
        for _ in range(rng.randint(1, 4)):
            r, c = rng.randint(0, h - 2), rng.randint(0, w - 3)
            m[r:r + rng.randint(1, 4), c:c + rng.randint(1, 5)] = True
    elif kind == 'comb':
        r0 = rng.randint(1, max(2, h // 2))
        m[r0:r0 + 2, 1:w - 1] = True
        for c in range(1, w - 1, 3):
            m[r0:min(h, r0 + rng.randint(3, 8)), c] = True
    elif kind == 'dense':
        m = rng.rand(h, w) < 0.4
    elif kind == 'many':            # many separated 1-2 px components
        for r in range(1, h - 1, 4):
            for c in range(1, w - 1, 4):
                if rng.rand() < 0.7:
                    m[r:r + rng.randint(1, 3), c:c + rng.randint(1, 3)] = True
    else:
        raise ValueError(kind)
    return m


def diameter_mask(base, diameter):
    return _dilate(base, min(6, (int(diameter) - 1) // 4))


def _save_gray(path, black):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.where(black, 0, 255).astype(np.uint8)).save(path)


def _save_mask(path, mask):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def electrode_layout(electrode, layer, shape):
    """Deterministic layout of one electrode: black = shape. Centre crop carries the pattern, the border noise."""
    h, w = shape
    seed = int(hashlib.sha256(f'{electrode}/{layer}'.encode()).hexdigest()[:8], 16)
    rng = np.random.RandomState(seed)
    full = np.zeros((h + 1000, w + 1000), bool)
    r, c = 500 + rng.randint(0, max(1, h - 4)), 500 + rng.randint(0, max(1, w - 6))
    full[r:r + 3, c:c + 5] = True
    full[10:12, 10:12] = True
    return full


def _quiet(func, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = func(*args, **kwargs)
    return result, out.getvalue(), err.getvalue()


def _roles(spec):
    names = sorted({e for c in spec.cases for e in (c.ea, c.eb)})
    if spec.roles == 'default':
        return {name: ('gate' if i % 2 == 0 else 'data') for i, name in enumerate(names)}
    return spec.roles


def build_model(tmp: Path, name: str, spec: ModelSpec) -> ModelData:
    """raw hotspot tree -> import-hotspots -> import-electrode-assets -> merged data_dir (all by the frozen package)."""
    pkg = frozen()
    from simcompare_7bcb07a.adapters.real_hotspot import import_hotspots
    from simcompare_7bcb07a.cli import main as frozen_main
    base = Path(tmp) / name
    raw = base / 'src'
    for case in spec.cases:
        folder = raw / 'images' / case.name
        (folder / 'result').mkdir(parents=True, exist_ok=True)
        if case.layouts:
            _save_gray(folder / '1.png', electrode_layout(case.ea, case.la, spec.shape))
            _save_gray(folder / '2.png', electrode_layout(case.eb, case.lb, spec.shape))
        rows = []
        mask = base_mask(spec.shape, case.kind, case.seed)
        for d in DIAMETERS:
            m = diameter_mask(mask, d)
            if d not in case.drop:
                _save_mask(folder / f'hotspot_{case.defect}{d:02d}.png', m)
            area = int(np.count_nonzero(m)) * 0.01
            if case.csv == 'mismatch' and d % 7 == 0:
                area += 3.0
            rows.append((d, f'{area:.2f}'))
        if case.csv != 'missing':
            import csv
            with (folder / 'result' / f'{case.defect}.csv').open('w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(['defect_size(um)', 'Area(um2)'])
                writer.writerows(rows)
    expected = None
    if spec.expected == 'all':
        expected = [c.name for c in spec.cases]
    elif spec.expected:
        expected = list(spec.expected)
    expected_path = None
    if expected is not None:
        expected_path = base / 'expected.txt'
        expected_path.write_text('\n'.join(expected) + '\n')
    imported = base / 'imported'
    _quiet(import_hotspots, raw, imported, expected_cases=expected_path)
    # pair table: one row per unordered electrode pair (A-B and B-A share a row)
    seen, lines = set(), ['pair_id,electrode_a_id,electrode_b_id,class,pair_table_version']
    for case in spec.cases:
        key = frozenset((case.ea, case.eb))
        if key in seen:
            continue
        seen.add(key)
        cls = spec.pair_classes.get(key, 'line' if len(seen) % 2 else 'pixel')
        lines.append(f'{case.ea}{case.eb},{case.ea},{case.eb},{cls},pair-v1')
    (imported / 'pairs.csv').write_text('\n'.join(lines) + '\n')
    assignment = {'product_id': spec.product_id, 'design_version': DESIGN, 'source_id': 'SOURCE',
                  'process_step_id': 'STEP', 'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT',
                  'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': l, 'stack_order': o, 'density_process_layer': p}
                                  for l, o, p in spec.layers]}
    roles = _roles(spec)
    if roles == 'policy':
        assignment['electrode_role_policy'] = 'electrode_id'
    elif roles is not None:
        assignment['electrode_roles'] = dict(roles)
    assignment_path = base / 'assignment.json'
    assignment_path.write_text(json.dumps(assignment))
    cnt_root = base / 'cnt_dl1'
    _save_gray(cnt_root / spec.product_id / 'cno' / '1_CNT.png', electrode_layout('CNT', 'X', spec.shape))
    adapter = base / 'adapter'
    code, out_, err = _quiet(frozen_main, ['import-electrode-assets', '--source-kind', 'hotspot_case_electrode',
                                        '--root', str(raw), '--numeric-data-dir', str(imported),
                                        '--condition-assignment', str(assignment_path),
                                        '--cnt-from-dl1-root', str(cnt_root), '--source-version', 'layout_v1',
                                        '--max-asset-memory-mb', '256', '--out', str(adapter)])
    assert code in (0, 3), (err, out_)
    data = base / 'data'
    shutil.copytree(imported, data)
    (data / 'assets').mkdir(exist_ok=True)
    for fname in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(adapter / fname, data / fname)
    for path in (adapter / 'assets').iterdir():
        shutil.copyfile(path, data / 'assets' / path.name)
    shutil.rmtree(base / 'adapter')
    return ModelData(spec, data, assignment, raw)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def side_config(model: ModelData, *, record=True, manifest=True):
    spec = {'data_dir': str(model.data_dir), 'product_id': model.spec.product_id,
            'condition_assignment': model.assignment}
    if manifest:
        spec['asset_manifest'] = {'path': 'review_assets.json', 'sha256': sha(model.data_dir / 'review_assets.json')}
    if record:
        spec['electrode_import_record'] = {'path': 'electrode_asset_import_record.json',
                                           'sha256': sha(model.data_dir / 'electrode_asset_import_record.json')}
    return spec


def write_config(path: Path, reference: ModelData, target: ModelData, *, diameter_um=1, record=True, manifest=True,
                 extra=None):
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused', 'map': {'diameter_um': diameter_um},
           'reference': side_config(reference, record=record, manifest=manifest),
           'target': side_config(target, record=record, manifest=manifest)}
    cfg.update(extra or {})
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=True, allow_unicode=True), encoding='utf-8')
    return Path(path)


# --- scenario catalogue -----------------------------------------------------------------------------------------

def _pairs(electrodes, layer='L1', defect='lateral_short', limit=None, seed=0, kinds=('blob',), **extra):
    rng = np.random.RandomState(seed)
    combos = [(a, b) for i, a in enumerate(electrodes) for b in electrodes[i + 1:]]
    rng.shuffle(combos)
    cases = []
    for n, (a, b) in enumerate(combos[:limit]):
        if n % 5 == 4:                      # some reversed names so A-B and B-A share one pair-table row
            a, b = b, a
        cases.append(CaseSpec(a, b, layer, layer, defect, kinds[n % len(kinds)], seed * 1000 + n, **extra))
    return cases


def scenario_specs(name):
    """Return (reference_spec, target_spec, map_kwargs) for a named scenario."""
    if name == 'shared':            # shared electrodes, equal frames, single layer
        e = ['A', 'B', 'C', 'D', 'E']
        return (ModelSpec('PREF', (24, 30), _pairs(e, seed=1, kinds=('blob', 'centered', 'comb'), limit=8)),
                ModelSpec('PTGT', (24, 30), _pairs(e, seed=2, kinds=('blob', 'centered', 'dense'), limit=8)))
    if name == 'distinct':          # every case its own electrodes, asymmetric frames
        ref = [CaseSpec(f'R{2 * i}', f'R{2 * i + 1}', kind=k, seed=10 + i)
               for i, k in enumerate(('blob', 'centered', 'centered', 'comb', 'many', 'dense'))]
        tgt = [CaseSpec(f'T{2 * i}', f'T{2 * i + 1}', kind=k, seed=30 + i)
               for i, k in enumerate(('centered', 'blob', 'centered', 'many', 'comb', 'dense'))]
        return ModelSpec('PREF', (24, 30), ref), ModelSpec('PTGT', (30, 36), tgt)
    if name == 'multilayer':        # two lateral layers + vertical shorts, asymmetric frames
        layers = (('L1', 1, 'P1'), ('L2', 2, 'P2'))
        e = ['A', 'B', 'C', 'D']
        ref = _pairs(e, 'L1', seed=3, kinds=('blob', 'centered', 'comb'), limit=5) \
            + _pairs(['E', 'F', 'G'], 'L2', seed=4, kinds=('centered', 'many'), limit=3) \
            + [CaseSpec('A', 'E', 'L1', 'L2', 'vertical_short', 'blob', 77), CaseSpec('B', 'F', 'L1', 'L2', 'vertical_short', 'centered', 78)]
        tgt = _pairs(e, 'L1', seed=5, kinds=('centered', 'blob'), limit=5) \
            + _pairs(['E', 'F', 'G'], 'L2', seed=6, kinds=('blob', 'comb'), limit=3) \
            + [CaseSpec('A', 'E', 'L1', 'L2', 'vertical_short', 'blob', 79)]
        return ModelSpec('PREF', (28, 24), ref, layers), ModelSpec('PTGT', (28, 32), tgt, layers)
    if name == 'components':        # many separated components, comb + dense masks, more cases
        e = ['A', 'B', 'C', 'D', 'E', 'F']
        return (ModelSpec('PREF', (40, 48), _pairs(e, seed=7, kinds=('many', 'comb', 'dense', 'blob'), limit=12)),
                ModelSpec('PTGT', (40, 48), _pairs(e, seed=8, kinds=('many', 'comb', 'blob', 'dense'), limit=12)))
    if name == 'incomplete':        # missing/mismatching CSV, dropped diameters, missing layouts, unexpected inventory
        e = ['A', 'B', 'C', 'D']
        cases = _pairs(e, seed=9, kinds=('blob', 'centered'), limit=5)
        cases[0] = CaseSpec(cases[0].ea, cases[0].eb, kind='blob', seed=91, csv='missing')
        cases[1] = CaseSpec(cases[1].ea, cases[1].eb, kind='centered', seed=92, csv='mismatch')
        cases[2] = CaseSpec(cases[2].ea, cases[2].eb, kind='comb', seed=93, drop=(1, 2, 5, 7, 40))
        ref = ModelSpec('PREF', (24, 30), cases, expected=[c.name for c in cases] + ['lateral_short-Z-L1-Y-L1'])
        tgt = ModelSpec('PTGT', (24, 30), _pairs(e, seed=10, kinds=('centered', 'blob'), limit=5), expected=None)
        return ref, tgt
    if name == 'noroles':           # no roles -> every ROI blocked at the real role gate
        e = ['A', 'B', 'C']
        return (ModelSpec('PREF', (24, 30), _pairs(e, seed=11, kinds=('centered',)), roles=None),
                ModelSpec('PTGT', (24, 30), _pairs(e, seed=12, kinds=('centered',)), roles=None))
    if name == 'policy':
        e = ['A', 'B', 'C', 'D']
        return (ModelSpec('PREF', (24, 30), _pairs(e, seed=13, kinds=('centered', 'blob'), limit=4), roles='policy'),
                ModelSpec('PTGT', (24, 30), _pairs(e, seed=14, kinds=('blob', 'centered'), limit=4), roles='policy'))
    if name == 'unicode':           # non-ASCII electrode / layer names -> non-ASCII reasons and ids
        layers = (('금속1', 1, 'P1'),)
        e = ['전극가', '전극나', '전극다']
        return (ModelSpec('PREF', (24, 30), _pairs(e, '금속1', seed=15, kinds=('centered', 'blob')), layers),
                ModelSpec('PTGT', (24, 30), _pairs(e, '금속1', seed=16, kinds=('centered', 'comb')), layers))
    raise KeyError(name)


_CACHE = {}


def scenario(tmp_factory, name):
    """Build (and cache for the session) reference/target ModelData for a named scenario."""
    if name not in _CACHE:
        base = Path(tmp_factory.mktemp(f'stage46_{name}'))
        ref_spec, tgt_spec = scenario_specs(name)
        _CACHE[name] = (base, build_model(base, 'reference', ref_spec), build_model(base, 'target', tgt_spec))
    return _CACHE[name]


def clone_scenario(tmp_factory, name, label, dest=None):
    """Writable copy of a cached scenario (for mutation tests); data_dir paths are re-based. ``dest`` re-creates the
    copy at a fixed path so two packages can be run on byte-identical, identically located inputs."""
    base, ref, tgt = scenario(tmp_factory, name)
    if dest is not None:
        out = Path(dest)
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
    else:
        out = Path(tmp_factory.mktemp(f'stage46_{name}_{label}'))
    copies = []
    for model, key in ((ref, 'reference'), (tgt, 'target')):
        data = out / key / 'data'
        shutil.copytree(model.data_dir, data)
        copies.append(ModelData(model.spec, data, model.assignment, model.root))
    return out, copies[0], copies[1]


# --- stage 5 inputs (DL2-style match files + decisions) ---------------------------------------------------------

DIRECTIONS = {'reference_to_target': ('reference', 'target'), 'target_to_reference': ('target', 'reference')}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def fake_matches(cand_dir: Path, models: dict, out_dir: Path, seed=0, topk=3):
    """psdl2_baseline_ncc_v1 all_ready outputs built from the candidates exports (DL2 itself is never run)."""
    rng = np.random.RandomState(seed)
    paths = {}
    for direction, (query_name, index_name) in DIRECTIONS.items():
        query = _load_json(Path(cand_dir) / f'{query_name}_export' / 'review_rois.json')
        index = _load_json(Path(cand_dir) / f'{index_name}_index_export' / 'review_rois.json')
        index_rows = [r for r in index['rois'] if r['status'] == 'ready']
        spec = models[index_name].spec
        h, w = spec.shape
        results = []
        for row in sorted((r for r in query['rois'] if r['status'] == 'ready'), key=lambda r: r['roi_id']):
            picks = []
            for rank in range(topk):
                if index_rows and rng.rand() < 0.7:
                    cx, cy = index_rows[rng.randint(len(index_rows))]['center_xy_um']
                else:
                    cx, cy = ORIGIN[0] + rng.rand() * w * PITCH, ORIGIN[1] + rng.rand() * h * PITCH
                picks.append((round(float(cx), 4), round(float(cy), 4)))
            scores = sorted(rng.rand(topk).tolist(), reverse=True)
            results.append({
                'query': {'roi_id': row['roi_id'], 'product_id': row['product_id'], 'frame_id': row['frame_id'],
                          'center_xy_um': row['center_xy_um']},
                'status': 'available', 'reasons': [],
                'primary': [{'rank': rank, 'score': scores[rank - 1], 'center_xy_um': list(c),
                             'product_id': spec.product_id, 'frame_id': FRAME_ID,
                             'template_top_left_px_yx': [0, 0]} for rank, c in enumerate(picks, 1)],
                'endpoints_used': [], 'template_sha256': 'f' * 64, 'shared_with': [],
                'flags': {'low_structure': False, 'window_clipped': False, 'query_valid_hole': False,
                          'candidate_valid_hole': False, 'score_gap': scores[0] - scores[1], 'near_equal_peaks': True},
                'query_invalid_pixel_count': 0})
        payload = {'schema_version': 'psdl2_baseline_ncc_v1', 'method': 'zncc_fft_role_layer_union',
                   'implementation_revision': 'baseline_ncc_batch_v1.3', 'mode': 'all_ready', 'window_um': 1.0,
                   'topk': topk, 'candidate_product_id': spec.product_id, 'n_queries': len(results),
                   'status': 'available', 'results': results,
                   'inputs': {'query_export_sha256': sha(Path(cand_dir) / f'{query_name}_export' / 'review_rois.json'),
                              'index_export_sha256': sha(Path(cand_dir) / f'{index_name}_index_export' / 'review_rois.json')},
                   'arguments': {'window_um': 1.0, 'topk': topk, 'self_check': False}}
        path = Path(out_dir) / f'{direction}.json'
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        paths[direction] = path
    return paths


def decisions_file(path: Path, matches: dict, seed=0, accept=0.7, notes=('', 'ok', '확인함')):
    """decisions.csv over the match files: rank 1 mostly accepted, other ranks mostly rejected or left undecided."""
    import csv
    from simcompare_7bcb07a.confirm_pairs import candidate_id
    rng = np.random.RandomState(seed)
    rows = []
    for direction, match in sorted(matches.items()):
        for entry in _load_json(match)['results']:
            for cand in entry['primary']:
                roll = rng.rand()
                if cand['rank'] == 1:
                    verdict = 'accept' if roll < accept else ('reject' if roll < accept + 0.15 else None)
                else:
                    verdict = 'accept' if roll < 0.12 else ('reject' if roll < 0.5 else None)
                if verdict:
                    rows.append((candidate_id(direction, entry['query']['roi_id'], cand['rank']), verdict,
                                 notes[rng.randint(len(notes))]))
    with Path(path).open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['candidate_id', 'decision', 'note'])
        writer.writerows(rows)
    return Path(path)


# --- hand-built correspondences (both endpoint bindings, repeats, edge/outside/duplicate centres) ----------------

def _side_payload(model: ModelData, case: CaseSpec, centre, *, endpoints, failure_type=True):
    spec = model.spec
    roles = _roles(spec)
    layer = case.la if case.defect == 'lateral_short' else max(
        (case.la, case.lb), key=lambda l: next(o for name, o, _ in spec.layers if name == l))
    side = {'product_id': spec.product_id, 'design_version': DESIGN, 'frame_id': FRAME_ID,
            'center_xy_um': [float(centre[0]), float(centre[1])], 'analysis_layer': layer}
    if endpoints:
        if roles is None or roles == 'policy':
            roles = {e: e for e in (case.ea, case.eb)}
        side['endpoints'] = [{'electrode_id': case.ea, 'role': roles.get(case.ea, case.ea), 'actual_layer': case.la},
                             {'electrode_id': case.eb, 'role': roles.get(case.eb, case.eb), 'actual_layer': case.lb}]
    if failure_type:
        side['failure_type'] = case.defect
    return side


def correspondence(path: Path, reference: ModelData, target: ModelData, seed=0, n_pairs=6, *, endpoints='both',
                   repeats='all', blocked=1, outside=1, edge=1, duplicate=1, unmatched_endpoints=0, failure_type=True, margin=0.0):
    """roi_correspondence_v1 over random cases/centres.

    endpoints: both | a_only | none. repeats: all (random) | uniform (same for every pair) | some | none. edge: pairs whose centre is flush with a frame edge;
    margin: inside centres keep this distance from the frame edges; outside: pairs whose centre leaves the frame; duplicate: a repeated position; unmatched_endpoints: pairs naming an
    electrode pair that has no case (named case missing); blocked: pairs written with status=blocked.
    """
    rng = np.random.RandomState(seed)
    pairs, used = [], []

    def centre(model, mode):
        h, w = model.spec.shape
        x0, y0, x1, y1 = ORIGIN[0], ORIGIN[1], ORIGIN[0] + w * PITCH, ORIGIN[1] + h * PITCH
        if mode == 'edge':
            return (x0 if rng.rand() < 0.5 else x1, y0 + rng.rand() * (y1 - y0)) if rng.rand() < 0.5 \
                else (x0 + rng.rand() * (x1 - x0), y0 if rng.rand() < 0.5 else y1)
        if mode == 'outside':
            return x1 + 0.5, y1 + 0.5
        return (x0 + margin + rng.rand() * (x1 - x0 - 2 * margin), y0 + margin + rng.rand() * (y1 - y0 - 2 * margin))

    modes = ['inside'] * max(0, n_pairs - blocked - outside - edge - duplicate)
    modes += ['edge'] * edge + ['outside'] * outside + ['duplicate'] * duplicate + ['blocked'] * blocked
    for n, mode in enumerate(modes[:max(n_pairs, 1)] if n_pairs else []):
        ca = reference.spec.cases[rng.randint(len(reference.spec.cases))]
        cb = target.spec.cases[rng.randint(len(target.spec.cases))]
        if mode == 'duplicate' and used:
            pa, pb = used[rng.randint(len(used))]
        else:
            m = 'inside' if mode in ('blocked', 'duplicate') else mode
            pa, pb = centre(reference, m), centre(target, 'inside' if m == 'outside' and rng.rand() < 0.5 else m)
        used.append((pa, pb))
        bind_a = endpoints in ('both', 'a_only')
        bind_b = endpoints == 'both'
        if n < unmatched_endpoints:
            cb = CaseSpec('X1', 'X2', cb.la, cb.lb, cb.defect)
            bind_b = bind_a = True
        a = _side_payload(reference, ca, pa, endpoints=bind_a, failure_type=failure_type)
        b = _side_payload(target, cb, pb, endpoints=bind_b, failure_type=failure_type)
        pair = {'pair_id': f'P{n:03d}', 'a': a, 'b': b,
                'source': {'kind': 'manual', 'labelled_by': 'designer', 'labelled_at': '2026-10-22'},
                'status': 'blocked' if mode == 'blocked' else 'ready',
                'reasons': ['pixel_pitch_mismatch'] if mode == 'blocked' else []}
        if repeats == 'uniform':
            pair['repeats_per_frame'] = {'a': 2, 'b': 1}
        elif repeats == 'all' or (repeats == 'some' and n % 2 == 0):
            pair['repeats_per_frame'] = {'a': int(rng.randint(1, 4)), 'b': int(rng.randint(1, 4))}
        pairs.append(pair)
    payload = {'schema_version': 'roi_correspondence_v1',
               'producer': {'kind': 'manual', 'version': '1', 'commit': None, 'created_at': '2026-10-22'},
               'pairs': pairs}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return Path(path)
