"""candidates-dv (T3a): ΔV 등급 가중 취약점 ROI. real_hotspot_v1 두 제품 + dv-grade 출력으로 끝까지 돌린다.

장면은 작은 crop(10×240 셀, 0.1 µm → 1×24 µm)에 섬(island)을 놓아 만든다. 전극 이름으로 등급이 정해진다:
P* = 임피던스 high, 5 V / N* = low, 0 V / Q* = low, 0–1 V.  쌍 (P, N) = High, (N, N)·(P, P) = None, (Q, N) = Low(경계 10 V).
"""
import csv
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import yaml

from simcompare import dv_candidates
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main
from test_electrode_png import save

CROP = (10, 240)
FULL = (CROP[0] + 1000, CROP[1] + 1000)
TYPES = {'P': ('high', ('5', '5')), 'N': ('low', ('0', '0')), 'Q': ('low', ('0', '1'))}
STACK = [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'},
         {'actual_layer': 'L2', 'stack_order': 2, 'density_process_layer': 'P2'}]


def island(col, width=5, rows=3):
    return [(r, c) for r in range(rows) for c in range(col, col + width)]


def pattern(seed):
    arr = np.zeros(FULL, dtype=bool)
    arr[500 + seed % 5:503 + seed % 5, 502:508] = True
    arr[10:12, 10:12] = True
    return arr


def case_electrodes(name):
    match = re.fullmatch(r'(\w+?)-([^-]+)-([^-]+)-([^-]+)-([^-]+)', name)
    return match.group(2), match.group(4)


def write_case(root, name, cells):
    defect = name.split('-')[0]
    folder = root / 'images' / name
    (folder / 'result').mkdir(parents=True)
    save(folder / '1.png', pattern(1))
    save(folder / '2.png', pattern(2))
    for index, selected in ((1, cells), (2, ())):
        crop = np.zeros(CROP, dtype=np.uint8)
        for r, c in selected:
            crop[r, c] = 255
        Image.fromarray(crop).save(folder / f'hotspot_{defect}0{index}.png')
    with (folder / 'result' / f'{defect}.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerow(['1', f'{len(cells) * 0.01:.2f}'])
        writer.writerow(['2', '0'])


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dv_tree(base, name, case_names, root_name=None):
    """dv-grade 는 case 폴더 이름만 읽는다: 이름만 있는 가벼운 트리(루트 이름은 import 한 src_<side> 와 같게)."""
    root = base / f'dvroot_{name}' / (root_name or f'src_{name}')
    for case in case_names:
        (root / case).mkdir(parents=True)
        (root / case / '1.png').write_bytes(b'')
    return root


def make_dv(base, name, product, case_names, out_name=None, threshold='10', types=None, root_name=None):
    electrodes = sorted({e for case in case_names for e in case_electrodes(case)})
    sheet = base / f'sheet_{name}.csv'
    with open(sheet, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(['electrode_id', 'impedance', 'layers', 'T1_min', 'T1_max'])
        for electrode in electrodes:
            impedance, (low, high) = (types or {}).get(electrode) or TYPES[electrode[0]]
            writer.writerow([electrode, impedance, 'L1', low, high])
    out = base / (out_name or f'dv_{name}')
    code = main(['dv-grade', '--sheet', str(sheet), '--root', str(dv_tree(base, name, case_names, root_name)), '--product-id', product,
                 '--low-low-threshold-v', threshold, '--out', str(out)])
    assert code == 0
    return out


def build_product(base, name, product, cases):
    """cases: [(case_name, cells)]. (data_dir, assignment). import-hotspots + import-electrode-assets 를 합친 data_dir."""
    source = base / f'src_{name}'
    for case, cells in cases:
        write_case(source, case, cells)
    imported = base / f'imported_{name}'
    expected = base / f'expected_{name}.txt'
    expected.write_text(''.join(f'{case}\n' for case, _ in cases))
    import_hotspots(source, imported, expected_cases=expected)
    electrodes = sorted({e for case, _ in cases for e in case_electrodes(case)})
    pairs = sorted({tuple(sorted(case_electrodes(case))) for case, _ in cases})
    (imported / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n' + ''.join(
        f'{a}{b},{a},{b},line,pair-v1\n' for a, b in pairs))
    assignment = {'product_id': product, 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv', 'layer_stack': STACK,
                  'electrode_roles': {e: ('gate' if i % 2 else 'data') for i, e in enumerate(electrodes)}}
    assignment_path = base / f'assignment_{name}.json'
    assignment_path.write_text(json.dumps(assignment))
    cnt = base / f'cnt_{name}'
    save(cnt / product / 'cno' / '1_CNT.png', pattern(3))
    adapter = base / f'adapter_{name}'
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(source), '--numeric-data-dir',
                 str(imported), '--condition-assignment', str(assignment_path), '--cnt-from-dl1-root', str(cnt), '--source-version',
                 'layout_v1', '--max-asset-memory-mb', '256', '--out', str(adapter)])
    assert code == 0
    data = base / f'data_{name}'
    shutil.copytree(imported, data)
    (data / 'assets').mkdir(exist_ok=True)
    for file in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(adapter / file, data / file)
    for path in (adapter / 'assets').iterdir():
        shutil.copyfile(path, data / 'assets' / path.name)
    return data, assignment


class World:
    """두 제품(reference=PA, target=PB)의 입력과 dv-grade 출력."""

    def __init__(self, base, ref_cases, tgt_cases, *, ref_product='PA', tgt_product='PB', ref_dv_cases=None, tgt_dv_cases=None,
                 tgt_types=None):
        self.base = Path(base)
        sections = {}
        for name, cases, product in (('reference', ref_cases, ref_product), ('target', tgt_cases, tgt_product)):
            data, assignment = build_product(self.base, name, product, cases)
            sections[name] = {'data_dir': str(data), 'product_id': product, 'condition_assignment': assignment,
                              'asset_manifest': {'path': 'review_assets.json', 'sha256': sha(data / 'review_assets.json')},
                              'electrode_import_record': {'path': 'electrode_asset_import_record.json',
                                                          'sha256': sha(data / 'electrode_asset_import_record.json')}}
        cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused', 'map': {'diameter_um': 1}, **sections}
        self.config = self.base / 'real.yaml'
        self.config.write_text(yaml.safe_dump(cfg))
        self.cases = {'reference': ref_cases, 'target': tgt_cases}
        self.products = {'reference': ref_product, 'target': tgt_product}
        self.dv = {'reference': make_dv(self.base, 'reference', ref_product, ref_dv_cases or [c for c, _ in ref_cases]),
                   'target': make_dv(self.base, 'target', tgt_product, tgt_dv_cases or [c for c, _ in tgt_cases], types=tgt_types)}

    def dv_variant(self, name, side, case_names, **kwargs):
        """같은 제품의 다른 dv-grade 출력(case 목록·전극 종류를 바꾼 것)."""
        kwargs.setdefault('root_name', f'src_{side}')
        return make_dv(self.base, name, self.products[side], case_names, **kwargs)

    def run(self, out='out', *, k=4, grade='high', window=20, overlap=0.5, extra=(), dv=None):
        dv = dv or self.dv
        return main(['candidates-dv', '--config', str(self.config), '--dv-grades-reference', str(dv['reference']),
                     '--dv-grades-target', str(dv['target']), '--min-count', str(k), '--roi-grade', grade, '--window-um', str(window),
                     '--merge-overlap', str(overlap), '--out', str(self.base / out), *extra])


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def queries(payload, direction='reference_to_target'):
    return next(d for d in payload['directions'] if d['direction'] == direction)['queries']


def stage_map(payload, direction='reference_to_target'):
    funnel = next(f for f in payload['funnel']['directions'] if f['direction'] == direction)
    return {s['stage']: s for s in funnel['stages']}


# 장면 -------------------------------------------------------------------------------------------------------------
# 섬 I1(col 10), I2(col 70: 6 µm 오른쪽) 은 High 쌍 둘씩(전극 4개), I3(col 150) 은 None 쌍으로 전극 5개.
def standard_cases(layer='L1'):
    def name(defect, a, b):
        return f'{defect}-{a}-{layer}-{b}-{layer}'
    i1 = island(10)
    i2 = island(70)
    i3 = island(150)
    i4 = island(200)
    return [(name('lateral_short', 'P1', 'N1'), i1), (name('lateral_short', 'P2', 'N2'), i1),
            (name('lateral_short', 'P3', 'N3'), i2), (name('lateral_short', 'P4', 'N4'), i2),
            (name('lateral_short', 'N5', 'N6'), i3), (name('lateral_short', 'N5', 'N7'), i3), (name('lateral_short', 'N8', 'N9'), i3),
            (name('lateral_short', 'N10', 'Q1'), i4), (name('lateral_short', 'N11', 'Q2'), i4)]


# target 제품에서는 P1 이 low 0 V 라 (P1, N1) 이 None 이 된다: 같은 pair_key 가 두 제품에서 다른 등급이다.
TWIST = {'P1': ('low', ('0', '0'))}


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    return World(tmp_path_factory.mktemp('dvc'), standard_cases(), standard_cases(), tgt_types=TWIST)


@pytest.fixture(scope='module')
def basic(world):
    assert world.run('basic') == 0
    return world.base / 'basic'


def test_basic_run_grades_ranks_merge_and_export(world, basic):
    out = basic
    payload = load(out / 'candidate_queries_v1.json')
    assert payload['schema_version'] == 'candidate_queries_v1' and payload['status'] == 'available'
    assert payload['args']['roi_grade'] == 'high' and payload['args']['min_count'] == 4
    assert payload['policy']['representative'] == 'dv_roi_rank_v1'
    for side in ('reference', 'target'):
        record = payload['inputs'][side]['dv_grades']
        assert record['product_id'] == {'reference': 'PA', 'target': 'PB'}[side] and record['manifest_sha256'] and record['sheet_sha256']
        assert record['threshold_v'] == '10' and record['grade_rule_version'] == 'dv_grade_rule_v1'
    # 두 섬(High 4전극씩)은 W=20 R=.5 로 병합되어 질의 하나. 5전극 None 섬은 영역이 아니다.
    [query] = queries(payload)
    assert len(query['merged_from']) == 2 and query['region_id'] in query['merged_from']
    assert query['grade_max'] == 'High' and query['sel_max_count'] == query['high_max_count'] == 4 and query['max_count'] == 4
    assert query['rank_graded'] == 1 and query['score_rule_version'] == 'dv_roi_rank_v1'
    assert query['sel_cells_in_window_ratio'] == 1.0 and 'selected_cells_outside_window' not in query['flags']
    stages = stage_map(payload)
    assert [s for s in stages] == ['unweighted_regions', 'graded_regions', 'merged_queries', 'after_max_candidates', 'exported_ready']
    assert stages['unweighted_regions']['count'] == 4 and stages['graded_regions']['count'] == 2 and stages['merged_queries']['count'] == 1
    assert stages['merged_queries']['dropped'] == 1 and stages['merged_queries']['reasons'] == {'merged_overlap': 1}
    assert stages['graded_regions']['dropped'] == 2 and stages['graded_regions']['reasons'] == {'below_graded_threshold': 2}
    # v22-2: 병합된 영역의 export 에 두 섬의 case 가 모두 있다
    rows = load(out / 'reference_export' / 'review_rois.json')['rois']
    assert {row['case_id'] for row in rows} == {c for c, _ in standard_cases()[:4]}
    assert {row['source_region_id'] for row in rows} == {query['region_id']}
    assert {tuple(row['center_xy_um']) for row in rows} == {tuple(query['center_xy_um'])}
    # 비가중 목록: 5전극 None 섬이 count_all 1위, 등급 영역이 없어 below_graded_threshold
    vulnerability = load(out / 'vulnerability_v1.json')
    assert vulnerability['schema_version'] == 'vulnerability_v1'
    product = vulnerability['products']['reference']
    unweighted = sorted(product['unweighted_regions'], key=lambda r: r['rank_count_all'])
    assert [r['max_count'] for r in unweighted] == [5, 4, 4, 4] and unweighted[0]['flags'] == ['below_graded_threshold']
    assert unweighted[0]['grade_max'] == 'None' and unweighted[0]['sel_max_count'] == 0 and unweighted[0]['graded_region_ids'] == []
    assert sorted(len(r['graded_region_ids']) for r in unweighted) == [0, 0, 1, 1]
    assert sorted(r['grade_max'] for r in unweighted) == ['High', 'High', 'Low', 'None']
    regions = {r['region_id']: r for r in product['regions']}
    assert len(regions) == 2 and sum(r['is_representative'] for r in regions.values()) == 1
    assert all(r['exported'] == r['is_representative'] for r in regions.values())
    first = regions[query['region_id']]
    assert first['rank_graded'] == 1 and first['rank_unweighted'] == 1   # 질의가 하나라 같은 집합 안 순위도 1
    assert first['n_pairs_by_grade']['High'] >= 2 and {p['pair_key'] for p in first['pairs']} == {'N1-P1', 'N2-P2'}
    assert (out / 'execution.json').is_file() and json.loads((out / 'execution.json').read_text())['command'] == 'candidates-dv'
    assert {p['pair_key'] for p in product['pairs']} >= {'N1-P1', 'N5-N6'}
    assert next(p for p in product['pairs'] if p['pair_key'] == 'N5-N6')['grade'] == 'None'


def test_grade_maps_files_values_and_sidecar_units(basic):
    sidecars = sorted((basic / 'grade_maps').glob('reference_*.json'))
    assert len(sidecars) == 3
    by_kind = {path.stem.rsplit('_', 2)[-2] + '_' + path.stem.rsplit('_', 1)[-1]: load(path) for path in sidecars}
    assert set(by_kind) == {'count_high', 'count_visible', 'grade_max'}
    assert by_kind['grade_max']['unit'] == 'grade_max_0_uncovered_1_none_2_low_3_high'
    assert {entry['count'] for entry in by_kind['grade_max']['legend']} <= {0, 1, 2, 3}
    assert {entry['label'] for entry in by_kind['grade_max']['legend']} == {'0 not covered / ungraded only', '1 None', '2 Low', '3 High'}
    assert by_kind['count_high']['unit'] == 'distinct_electrodes_high_grade' and by_kind['count_visible']['unit'].endswith('low_or_high_grade')
    assert max(entry['count'] for entry in by_kind['count_high']['legend']) == 4
    assert all((basic / 'grade_maps' / path.with_suffix('.png').name).is_file() for path in sidecars)
    # candidate_queries_v1 / execution 가 만든 파일 목록과 실제 파일이 같다
    listed = set(json.loads((basic / 'execution.json').read_text())['outputs']) | {'execution.json'}
    actual = {path.relative_to(basic).as_posix() for path in basic.rglob('*') if path.is_file()}
    assert listed == actual


def test_target_direction_and_same_pair_key_with_different_grades(world, basic):
    payload = load(basic / 'candidate_queries_v1.json')
    [query] = queries(payload, 'target_to_reference')
    assert len(query['merged_from']) == 1 and query['sel_max_count'] == 4   # target 에서 I1 은 P1 이 low 라 4 미만
    products = load(basic / 'vulnerability_v1.json')['products']
    reference = {p['pair_key']: p for p in products['reference']['pairs']}
    target = {p['pair_key']: p for p in products['target']['pairs']}
    assert reference['N1-P1']['grade'] == 'High' and target['N1-P1']['grade'] == 'None'
    assert products['target']['dv_provenance']['product_id'] == 'PB' and products['reference']['dv_provenance']['product_id'] == 'PA'
    # 제품별 표: 각 제품의 region 은 자기 제품의 등급 표로만 센다
    assert len(products['target']['regions']) == 1 and products['target']['regions'][0]['n_pairs_by_grade']['High'] == 2
    cases = {row['case_name']: row for row in products['target']['cases']}
    assert cases['lateral_short-P1-L1-N1-L1']['grade'] == 'None'


def test_k_above_every_selected_count_leaves_no_query_and_funnel_agrees(world):
    assert world.run('k5', k=5) == 0
    out = world.base / 'k5'
    payload = load(out / 'candidate_queries_v1.json')
    for direction in ('reference_to_target', 'target_to_reference'):
        assert queries(payload, direction) == []
        stages = stage_map(payload, direction)
        assert stages['unweighted_regions']['count'] == 1 and stages['graded_regions']['count'] == 0
        assert stages['graded_regions']['dropped'] == 1 and stages['merged_queries']['count'] == 0 == stages['exported_ready']['count']
    assert load(out / 'reference_export' / 'review_rois.json')['rois'] == []
    region = load(out / 'vulnerability_v1.json')['products']['reference']['unweighted_regions'][0]
    assert region['max_count'] == 5 and region['flags'] == ['below_graded_threshold'] and region['sel_max_count'] == 0


def test_low_or_high_selects_visible_map_and_ranks_by_grade(world):
    assert world.run('lh', grade='low_or_high') == 0
    out = world.base / 'lh'
    payload = load(out / 'candidate_queries_v1.json')
    assert payload['policy']['selected_map'] == 'count_visible'
    first, second = queries(payload)
    assert (first['grade_max'], first['sel_max_count'], first['rank_graded']) == ('High', 4, 1)
    assert (second['grade_max'], second['sel_max_count'], second['rank_graded']) == ('Low', 4, 2)   # 같은 전극 수는 등급이 높은 쪽이 먼저
    assert second['visible_max_count'] == 4 and second['high_max_count'] == 0 and len(second['merged_from']) == 1
    assert stage_map(payload)['graded_regions']['count'] == 3


def test_max_candidates_cut_is_recorded_and_exports_only_kept(world):
    assert world.run('cut', window=4, extra=('--max-candidates', '1')) == 0
    out = world.base / 'cut'
    payload = load(out / 'candidate_queries_v1.json')
    [query] = queries(payload)
    stages = stage_map(payload)
    assert stages['merged_queries']['count'] == 2 and stages['after_max_candidates']['count'] == 1
    assert stages['after_max_candidates']['dropped'] == 1 and stages['after_max_candidates']['reasons'] == {'cut_by_max_candidates': 1}
    assert payload['funnel']['cut_by_max_candidates'] == 1   # reference 질의 둘 중 하나(target 은 질의가 하나라 안 잘림)
    rows = load(out / 'reference_export' / 'review_rois.json')['rois']
    assert {row['source_region_id'] for row in rows} == {query['region_id']}
    regions = load(out / 'vulnerability_v1.json')['products']['reference']['regions']
    assert sorted(r['exported'] for r in regions) == [False, True] and all(r['is_representative'] for r in regions)


def test_ungraded_case_is_partial_exit_3_and_not_in_the_grade_map(world, capsys):
    names = [c for c, _ in world.cases['reference']]
    missing = 'lateral_short-P2-L1-N2-L1'
    variant = world.dv_variant('ungr', 'reference', [c for c in names if c != missing])
    assert world.run('ungr', dv={'reference': variant, 'target': world.dv['target']}) == 3
    out = world.base / 'ungr'
    payload = load(out / 'candidate_queries_v1.json')
    [warning] = [w for w in payload['warnings'] if w['code'] == 'dv_ungraded_cases']
    assert warning['product'] == 'reference' and warning['count'] == 1 and warning['cases'] == [missing] and payload['status'] == 'partial'
    product = load(out / 'vulnerability_v1.json')['products']['reference']
    assert product['ungraded_cases'] == [missing] and product['n_graded_cases'] == len(names) - 1
    assert stage_map(payload)['graded_regions']['count'] == 1   # I1 의 High 전극이 4 → 2 로 줄어 영역이 아니다
    unweighted = {r['region_id']: r for r in product['unweighted_regions']}
    assert sorted(r['max_count'] for r in unweighted.values()) == [4, 4, 4, 5]   # count_all 은 등급 없는 case 도 센다


def _patched_prepare(monkeypatch, edit):
    """candidates.prepare_side 가 만든 side 를 edit(name, side) 로 바꾼 뒤 돌려준다(테스트 전용 입력 변형)."""
    from simcompare import candidates
    original = candidates.prepare_side

    def prepare(cfg, name, *args, **kwargs):
        side = original(cfg, name, *args, **kwargs)
        edit(name, side)
        return side
    monkeypatch.setattr(candidates, 'prepare_side', prepare)


def test_representative_centre_on_an_invalid_cell_keeps_exporter_reason_and_flags_the_query(tmp_path, monkeypatch):
    """고리 모양 섬(3×5 에서 가운데 셀만 비움): 영역 bbox 중심이 비어 있는 셀 위다. 그 셀만 invalid 로 만든다.
    real_hotspot 의 frame 은 전부 valid 라 구멍은 테스트가 넣고, valid 해시·자산 일치 검사는 원래 값으로 맞춘다."""
    from simcompare import roi_export
    from simcompare.numeric_schema import Frame
    ring = [(r, c) for r in range(3) for c in range(10, 15) if (r, c) != (1, 12)]

    def name(a, b):
        return f'lateral_short-{a}-L1-{b}-L1'
    cases = [(name('P1', 'N1'), ring), (name('P2', 'N2'), ring)]
    ring_world = World(tmp_path, cases, cases)
    assert ring_world.run('plain', k=4) == 0
    [plain] = queries(load(tmp_path / 'plain' / 'candidate_queries_v1.json'))
    centre = plain['center_xy_um']
    assert 'query_centre_invalid' not in plain['flags'] and plain['status'] == 'ready'

    original_hash = Frame.valid_hash
    original_asset = roi_export._asset

    def punch_hole(side_name, side):
        for frame in {id(f): f for f in side.dataset.frames.values()}.values():
            col = int((centre[0] - frame.origin_xy_um[0]) / frame.pixel_pitch_um)
            row = int((centre[1] - frame.origin_xy_um[1]) / frame.pixel_pitch_um)
            valid = frame.valid.copy()
            valid[row, col] = False
            hashes[id(frame)] = original_hash.fget(frame)
            object.__setattr__(frame, 'valid', valid)
    hashes = {}
    _patched_prepare(monkeypatch, punch_hole)
    monkeypatch.setattr(Frame, 'valid_hash', property(lambda self: hashes.get(id(self)) or original_hash.fget(self)))
    monkeypatch.setattr(roi_export, '_asset', lambda root, ref, frame, *, valid=False, memo=None:
                        original_asset(root, ref, frame, valid=False, memo=memo))
    assert ring_world.run('hole', k=4) == 3
    payload = load(tmp_path / 'hole' / 'candidate_queries_v1.json')
    [hole] = queries(payload)
    assert hole['center_xy_um'] == centre
    assert 'query_centre_invalid' in hole['flags'] and 'position_outside_valid_domain' in hole['reasons']   # 질의 플래그와 exporter 사유 둘 다
    assert hole['status'] == 'blocked'


def test_side_without_cases_is_a_warning_with_exit_3_not_a_product_mismatch(world, monkeypatch, capsys):
    from dataclasses import replace

    def no_cases(name, side):
        if name == 'reference':
            side.dataset = replace(side.dataset, cases=[])
            side.status, side.reasons, side.product_ids = 'unavailable', ['same_name_endpoints_unresolved'], []
    _patched_prepare(monkeypatch, no_cases)
    capsys.readouterr()
    assert world.run('nocases') == 3
    assert 'dv_grade_product_mismatch' not in capsys.readouterr().err
    out = world.base / 'nocases'
    payload = load(out / 'candidate_queries_v1.json')
    codes = {(w['code'], w['product']) for w in payload['warnings']}
    assert ('dv_candidates_side_has_no_cases', 'reference') in codes and ('side_unavailable', 'reference') in codes
    assert payload['status'] == 'partial' and queries(payload) == []
    product = load(out / 'vulnerability_v1.json')['products']['reference']
    assert product['regions'] == [] and product['n_dataset_cases'] == 0 and not any(c['in_dataset'] for c in product['cases'])


def test_duplicate_case_id_in_one_dataset_exits_2_before_writing(world, monkeypatch, capsys):
    from dataclasses import replace

    def duplicate(name, side):
        if name == 'target':
            side.dataset = replace(side.dataset, cases=list(side.dataset.cases) + [side.dataset.cases[0]])
    _patched_prepare(monkeypatch, duplicate)
    _exit2(world, capsys, 'dv_candidates_duplicate_case_id', name='dup')


def _exit2(world, capsys, code, *, dv=None, extra=(), name='bad', **kwargs):
    capsys.readouterr()
    assert world.run(name, dv=dv, extra=extra, **kwargs) == 2
    err = capsys.readouterr().err
    assert code in err, err
    assert not (world.base / name).exists()
    return err


def test_case_missing_from_dataset_exits_2_and_writes_nothing(world, capsys):
    names = [c for c, _ in world.cases['reference']] + ['lateral_short-P9-L1-N9-L1']
    variant = world.dv_variant('extra', 'reference', names)
    _exit2(world, capsys, 'dv_grade_product_mismatch', dv={'reference': variant, 'target': world.dv['target']})


def _edit_by_case(src, dst, edit):
    shutil.copytree(src, dst)
    path = Path(dst) / 'dv_grades_by_case.csv'
    with open(path, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.reader(handle))
    edit(rows)
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle, lineterminator='\n').writerows(rows)
    manifest_path = Path(dst) / 'dv_grade_manifest.json'
    manifest = load(manifest_path)
    manifest['outputs']['dv_grades_by_case.csv'] = sha(path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return Path(dst)


def test_layer_or_electrode_mismatch_exits_2(world, capsys):
    header = None

    def layer(rows):
        rows[1][rows[0].index('layer_a')] = 'L2'
    changed = _edit_by_case(world.dv['reference'], world.base / 'dv_layer', layer)
    _exit2(world, capsys, 'dv_grade_case_endpoint_mismatch', dv={'reference': changed, 'target': world.dv['target']}, name='bad_layer')


def test_same_dv_folder_or_symlink_and_swapped_folders_exit_2(world, capsys):
    same = {'reference': world.dv['reference'], 'target': world.dv['reference']}
    _exit2(world, capsys, 'dv_grades_dir_shared', dv=same, name='bad_same')
    link = world.base / 'dv_link'
    if not link.exists():
        os.symlink(world.dv['reference'], link)
    _exit2(world, capsys, 'dv_grades_dir_shared', dv={'reference': world.dv['reference'], 'target': link}, name='bad_link')
    swapped = {'reference': world.dv['target'], 'target': world.dv['reference']}
    _exit2(world, capsys, 'dv_grade_product_mismatch', dv=swapped, name='bad_swap')


def test_failure_after_output_creation_removes_only_own_files(world, monkeypatch, capsys):
    from simcompare import candidates

    def boom(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(candidates, '_export_side', boom)
    capsys.readouterr()
    assert world.run('failed_run') == 2 and 'disk full' in capsys.readouterr().err
    assert not (world.base / 'failed_run').exists()   # grade_maps/ 를 포함해 이 실행이 만든 것만 지웠고 폴더가 남지 않는다


def test_numeric_config_is_rejected_before_anything_is_read(world, capsys):
    demo = world.base / 'demo_numeric'
    assert main(['demo-data', '--out', str(demo)]) == 0
    capsys.readouterr()
    code = main(['candidates-dv', '--config', str(demo / 'config.yaml'), '--dv-grades-reference', str(world.dv['reference']),
                 '--dv-grades-target', str(world.dv['target']), '--min-count', '1', '--roi-grade', 'high', '--window-um', '4',
                 '--merge-overlap', '0.5', '--out', str(world.base / 'bad_numeric')])
    assert code == 2 and 'dv_candidates_requires_real_hotspot' in capsys.readouterr().err
    assert not (world.base / 'bad_numeric').exists()


def test_arguments_are_required_and_validated(world, capsys):
    base = ['candidates-dv', '--config', str(world.config), '--dv-grades-reference', str(world.dv['reference']),
            '--dv-grades-target', str(world.dv['target']), '--min-count', '4', '--roi-grade', 'high', '--window-um', '20',
            '--merge-overlap', '0.5', '--out', str(world.base / 'bad_args')]
    for flag in ('--dv-grades-reference', '--dv-grades-target', '--min-count', '--roi-grade', '--window-um', '--merge-overlap', '--out', '--config'):
        position = base.index(flag)
        with pytest.raises(SystemExit) as info:
            main(base[:position] + base[position + 2:])
        assert info.value.code == 2
    with pytest.raises(SystemExit) as info:
        main(base[:base.index('high')] + ['all'] + base[base.index('high') + 1:])
    assert info.value.code == 2
    assert main(base[:base.index('4')] + ['0'] + base[base.index('4') + 1:]) == 2
    assert main(base + ['--max-candidates', '0']) == 2
    assert not (world.base / 'bad_args').exists()
    existing = world.base / 'exists_already'
    existing.mkdir(exist_ok=True)
    assert main(base[:-1] + [str(existing)]) == 2 and list(existing.iterdir()) == []


def _normalised(folder, base):
    shas = {}
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file():
            data = path.read_bytes()
            if path.suffix in ('.json', '.csv'):
                data = data.replace(str(folder).encode(), b'<OUT>').replace(str(base).encode(), b'<TMP>')
                data = re.sub(rb'"producer_commit": "[0-9a-f]{40}"', b'"producer_commit": "<COMMIT>"', data)
                data = re.sub(rb'"(export_sha256|index_export_sha256|config_sha256)": "[0-9a-f]{64}"', rb'"\1": "<SHA>"', data)
            shas[path.relative_to(folder).as_posix()] = hashlib.sha256(data).hexdigest()
    return shas


def test_two_runs_write_the_same_bytes(world, basic):
    assert world.run('again') == 0
    assert _normalised(basic, world.base).keys() == _normalised(world.base / 'again', world.base).keys()
    first, second = _normalised(basic, world.base), _normalised(world.base / 'again', world.base)
    assert first == second


def test_confirm_pairs_reads_the_candidates_dv_output(world, basic, capsys):
    """candidates-dv 의 출력 폴더는 기존 confirm-pairs 가 그대로 읽는다(목록 → DL2 결과 모양 → 수락 → correspondence)."""
    capsys.readouterr()
    payload = load(basic / 'candidate_queries_v1.json')
    matches = {}
    for direction, query_name, index_name in (('reference_to_target', 'reference', 'target'), ('target_to_reference', 'target', 'reference')):
        rows = [r for r in load(basic / f'{query_name}_export' / 'review_rois.json')['rois'] if r['status'] == 'ready']
        index_rows = [r for r in load(basic / f'{index_name}_index_export' / 'review_rois.json')['rois'] if r['status'] == 'ready']
        frame_id = index_rows[0]['frame_id']
        results = []
        for row in sorted(rows, key=lambda r: r['roi_id']):
            results.append({
                'query': {'roi_id': row['roi_id'], 'product_id': row['product_id'], 'frame_id': row['frame_id'], 'center_xy_um': row['center_xy_um']},
                'status': 'available', 'reasons': [],
                'primary': [{'rank': rank, 'score': (0.9, 0.8, 0.5)[rank - 1], 'center_xy_um': [51.5 + rank, 50.5],
                             'product_id': index_rows[0]['product_id'], 'frame_id': frame_id, 'template_top_left_px_yx': [0, 0]}
                            for rank in (1, 2, 3)],
                'endpoints_used': [], 'template_sha256': 'f' * 64, 'shared_with': [],
                'flags': {'low_structure': False, 'window_clipped': False, 'query_valid_hole': False, 'candidate_valid_hole': False,
                          'score_gap': 0.1, 'near_equal_peaks': False},
                'query_invalid_pixel_count': 0})
        path = world.base / f'match_{direction}.json'
        path.write_text(json.dumps({
            'schema_version': 'psdl2_baseline_ncc_v1', 'method': 'zncc_fft_role_layer_union', 'implementation_revision': 'baseline_ncc_batch_v1.3',
            'mode': 'all_ready', 'window_um': 20.0, 'topk': 3, 'candidate_product_id': index_rows[0]['product_id'], 'n_queries': len(results),
            'status': 'available', 'results': results,
            'inputs': {'query_export_sha256': sha(basic / f'{query_name}_export' / 'review_rois.json'),
                       'index_export_sha256': sha(basic / f'{index_name}_index_export' / 'review_rois.json')},
            'arguments': {'window_um': 20.0, 'topk': 3, 'self_check': False}}, indent=2), encoding='utf-8')
        matches[direction] = path
    args = ['confirm-pairs', '--config', str(world.config), '--candidates', str(basic)]
    for direction, path in matches.items():
        args += ['--match', f'{direction}={path}']
    assert main(args + ['--list']) == 0
    listing = json.loads(capsys.readouterr().out)
    ids = [c['candidate_id'] for c in listing['candidates']]
    assert len(ids) == 18 and ids[0].startswith('reference_to_target:')   # reference 4 ROI(병합 영역의 case 4개) + target 2 ROI, 각 top-3
    decisions = world.base / 'decisions.csv'
    decisions.write_text('candidate_id,decision,note\n' + f'{ids[0]},accept,ok\n', encoding='utf-8')
    assert main(args + ['--decisions', str(decisions), '--labelled-by', 'designer1', '--mutual-tolerance-um', '0.5',
                        '--out', str(world.base / 'confirmed')]) == 0
    pairs = load(world.base / 'confirmed' / 'roi_correspondence.json')['pairs']
    assert len(pairs) == 1 and payload['status'] == 'available'


# 여러 섬·두 layer ---------------------------------------------------------------------------------------------------
def multi_cases():
    def case(layer, a, b):
        return f'lateral_short-{a}-{layer}-{b}-{layer}'
    cases = []
    # L1: 양 끝 섬(전극 6개)과 가운데 섬(전극 4개): 중심이 10 µm 간격이라 W=20, R=.5 에서 전이 병합된다
    for island_col, pairs in ((10, (('P1', 'N1'), ('P2', 'N2'), ('P3', 'N3'))), (110, (('P4', 'N4'), ('P5', 'N5'))),
                              (210, (('P6', 'N6'), ('P7', 'N7'), ('P8', 'N8')))):
        cases += [(case('L1', a, b), island(island_col)) for a, b in pairs]
    # L2: X(High 전극 4개 + None 전극 5개 → count_all 9) 는 L1 의 첫 섬과 같은 위치, Y(High 전극 8개) 는 19 µm 떨어져 있다
    cases += [(case('L2', a, b), island(10)) for a, b in (('P11', 'N11'), ('P12', 'N12'), ('N13', 'N14'), ('N13', 'N15'), ('N16', 'N17'))]
    cases += [(case('L2', a, b), island(200)) for a, b in (('P13', 'N18'), ('P14', 'N19'), ('P15', 'N20'), ('P16', 'N21'))]
    return cases


@pytest.fixture(scope='module')
def multi(tmp_path_factory):
    world = World(tmp_path_factory.mktemp('dvm'), multi_cases(), multi_cases())
    assert world.run('multi') == 0
    return world


def by_layer(payload, direction='reference_to_target'):
    result = {}
    for query in queries(payload, direction):
        result.setdefault(query['map_key']['layer'], []).append(query)
    return result


def test_transitive_merge_keeps_every_member_case_and_flags_cells_outside_the_window(multi):
    out = multi.base / 'multi'
    payload = load(out / 'candidate_queries_v1.json')
    [merged] = by_layer(payload)['L1']
    assert len(merged['merged_from']) == 3 and merged['sel_max_count'] == 6
    assert 0 < merged['sel_cells_in_window_ratio'] < 1 and merged['sel_cells_in_window'] < merged['sel_cells_total']
    assert merged['sel_cells_total'] == 3 * 15   # 섬 셋(3×5 셀)의 합집합: 분모는 창으로 자르지 않는다
    assert 'selected_cells_outside_window' in merged['flags'] and 'merged_member_cells_outside_window' in merged['flags']
    assert 'window_clipped' in merged['flags']   # 섬이 frame 왼쪽 끝 근처라 W=20 창이 frame 밖으로 나간다
    # 가운데 섬이 대표가 아니더라도 export 에는 세 섬의 모든 case(전극 8쌍)가 있다
    rows = [r for r in load(out / 'reference_export' / 'review_rois.json')['rois'] if r['source_region_id'] == merged['region_id']]
    assert {r['case_id'] for r in rows} == {c for c, _ in multi_cases() if '-L1-' in c} and len(rows) == 8


def test_same_position_in_two_layers_is_never_merged_and_ranks_differ_between_weighted_and_unweighted(multi):
    out = multi.base / 'multi'
    payload = load(out / 'candidate_queries_v1.json')
    layers = by_layer(payload)
    assert sorted(layers) == ['L1', 'L2'] and len(layers['L1']) == 1 and len(layers['L2']) == 2
    x, y = sorted(layers['L2'], key=lambda q: q['center_xy_um'][0])   # X 가 왼쪽(L1 첫 섬과 같은 위치)
    l1 = layers['L1'][0]
    assert y['center_xy_um'][0] > x['center_xy_um'][0] + 15
    assert all(len(q['merged_from']) == 1 for q in (x, y)) and set(x['merged_from']).isdisjoint(l1['merged_from'])
    # 등급 순위: Y(선정 전극 8) > L1 병합(6) > X(4). 비가중(count_all): X(9) > Y(8) > L1(6) — 전극이 많아도 등급이 낮으면 뒤로 간다
    assert (y['rank_graded'], l1['rank_graded'], x['rank_graded']) == (1, 2, 3)
    assert (x['rank_unweighted'], y['rank_unweighted'], l1['rank_unweighted']) == (1, 2, 3)
    assert (x['max_count'], y['max_count'], l1['max_count']) == (9, 8, 6) and (x['sel_max_count'], y['sel_max_count']) == (4, 8)
    assert [q['rank_graded'] for q in queries(payload)] == [1, 2, 3]   # 질의 순서 = 등급 순위
    product = load(out / 'vulnerability_v1.json')['products']['reference']
    unweighted = {r['region_id']: r for r in product['unweighted_regions']}
    assert len(unweighted) == 5 and sorted(r['rank_count_all'] for r in unweighted.values()) == [1, 2, 3, 4, 5]
    assert x['count_all' if 'count_all' in x else 'max_count'] == 9


def test_rank_counterexample_none_heavy_region_loses_to_high_region(multi):
    """비가중 1위(count_all 9) 영역은 High 전극이 4개뿐이라 등급 순위에서 High 8개인 영역 뒤로 간다."""
    out = multi.base / 'multi'
    product = load(out / 'vulnerability_v1.json')['products']['reference']
    regions = {r['region_id']: r for r in product['regions']}
    heavy = max((r for r in regions.values() if r['max_count'] == 9), key=lambda r: r['max_count'])
    best = min(regions.values(), key=lambda r: r['rank_graded'] or 99)
    assert best['sel_max_count'] == 8 and best['max_count'] == 8
    assert heavy['sel_max_count'] == 4 and heavy['rank_graded'] == 3 and heavy['rank_unweighted'] == 1
    assert heavy['n_pairs_by_grade'] == {'None': 3, 'Low': 0, 'High': 2, 'ungraded_cases': 0}


def test_identical_dv_outputs_are_a_note_not_an_error(tmp_path):
    cases = standard_cases()[:2]
    world = World(tmp_path, cases, cases, ref_product='PA', tgt_product='PA')
    copy = tmp_path / 'dv_target_copy'
    shutil.copytree(world.dv['reference'], copy)
    assert world.run('same_outputs', k=2, dv={'reference': world.dv['reference'], 'target': copy}) == 0
    payload = load(tmp_path / 'same_outputs' / 'candidate_queries_v1.json')
    codes = [n['code'] for n in payload['notes'] if n['code'].startswith('dv_')]
    assert 'dv_grades_identical_outputs' in codes and payload['status'] == 'available'
    assert payload['warnings'] == []


def test_renamed_dv_root_is_a_note_not_an_error(world):
    names = [c for c, _ in world.cases['reference']]
    renamed = world.dv_variant('renamed', 'reference', names, root_name='another_root_name')
    assert world.run('renamed_run', dv={'reference': renamed, 'target': world.dv['target']}) == 0
    notes = [n for n in load(world.base / 'renamed_run' / 'candidate_queries_v1.json')['notes'] if n['code'].startswith('dv_')]
    assert [(n['code'], n['product']) for n in notes] == [('dv_grade_root_name_differs', 'reference')]
    assert notes[0]['dv_grade_root'] == 'another_root_name' and notes[0]['import_source_root'] == 'src_reference'


# 단위 -------------------------------------------------------------------------------------------------------------
def test_window_ratio_90_outside_10_inside_and_invalid_centre():
    from simcompare.numeric_schema import Frame
    frame = Frame('F', 0.1, (0, 0), (1, 100), np.ones((1, 100), bool))
    mask = np.ones((1, 100), bool)
    total, inside = dv_candidates._window_cells(frame, mask, (9.5, 0.05), 1.0)   # 창 x∈[9.0, 10.0): 셀 90–99 만
    assert (total, inside) == (100, 10) and inside / total == pytest.approx(0.1)
    hole = np.ones((1, 100), bool)
    hole[0, 50] = False
    assert dv_candidates._centre_invalid(Frame('F', 0.1, (0, 0), (1, 100), hole), (5.05, 0.05))
    assert not dv_candidates._centre_invalid(frame, (5.05, 0.05))
    assert dv_candidates._centre_invalid(frame, (11.0, 0.05)) and dv_candidates._centre_invalid(frame, (5.0, 1.0))


def test_union_region_dedupes_cases_in_member_rank_order():
    from simcompare.electrode_map import compute_graded_electrode_maps
    from test_electrode_map import numeric_case
    cases = [numeric_case('a1', ('A', 'B'), cells=((0, 0), (0, 1)), shape=(1, 8)), numeric_case('a2', ('C', 'D'), cells=((0, 0), (0, 1)), shape=(1, 8)),
             numeric_case('b1', ('E', 'F'), cells=((0, 6), (0, 7)), shape=(1, 8)), numeric_case('b2', ('G', 'H'), cells=((0, 6), (0, 7)), shape=(1, 8)),
             numeric_case('both', ('I', 'J'), cells=((0, 1), (0, 6)), shape=(1, 8))]
    maps, _ = compute_graded_electrode_maps(cases, lambda case: 'High', min_count=4, roi_grade='high')
    left, right = sorted(maps.layers[0].regions, key=lambda r: r.bbox_yx)
    union = dv_candidates._union_regions([left, right])
    assert union.region_id == left.region_id and union.center_xy_um == left.center_xy_um and union.bbox_yx == left.bbox_yx
    ids = [link.identity['case_id'] for link in union.cases]
    assert ids == ['a1', 'a2', 'both', 'b1', 'b2'] and len(set(ids)) == len(ids)   # 'both' 는 두 member 에 있지만 한 번
    assert union.mask.sum() == left.mask.sum() + right.mask.sum() and union.electrodes == tuple('ABCDEFGHIJ')
    assert dv_candidates._union_regions([left]) is left


def test_grade_option_and_sel_map_validation():
    with pytest.raises(Exception) as info:
        dv_candidates._validate_dv_args('everything')
    assert 'invalid_candidates_dv_argument' in str(info.value)
    dv_candidates._validate_dv_args('high')
    dv_candidates._validate_dv_args('low_or_high')
