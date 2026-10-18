"""국부 ΔCA (compare-windows): 손계산 창 면적, 평행이동 불변, clipping/valid, core, 조각 합, 정렬·민감도."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from simcompare.correspondence import BoundPair, BoundSide
from simcompare.linepixel import FAILURE_PIECES
from simcompare.local_delta import (
    METRICS, compute_local_delta, spearman, write_local_delta,
)
from simcompare.numeric_schema import DIAMETERS_UM, Endpoint, Frame, NumericContractError
from test_linepixel import make_case


def make_case2d(case_id, a, b, cells, *, shape=(4, 4), pitch=0.5, product='A', failure_type='lateral_short',
                kind='line', diameter=5, valid=None, layer='M2', step='step'):
    case = make_case(case_id, a, b, set(), kind, product=product, failure_type=failure_type, layer=layer, step=step)
    frame = Frame('frame', pitch, (0, 0), shape, np.ones(shape, bool) if valid is None else np.asarray(valid, bool))
    records = {}
    for d, record in case.records.items():
        mask = None
        if record.mask is not None:
            mask = np.zeros(shape, bool)
            if d == diameter:
                for row, col in cells:
                    mask[row, col] = True
        records[d] = replace(record, frame=frame, mask=mask)
    case.records = records
    return case


def rect(rows, cols):
    return {(r, c) for r in rows for c in cols}


def side(cases, center, *, product='A', layer='M2', eps=None, failure_type=None, filters=None):
    frame = next(iter(cases)).records[5].frame
    return BoundSide(product, 'design', frame, tuple(center), layer, eps, failure_type, None, filters or {})


def pair(pair_id, a_cases, b_cases, center_a, center_b, **kwargs):
    source = {'kind': 'manual', 'labelled_by': 'test', 'labelled_at': '2026-10-18'}
    return BoundPair(pair_id, side(a_cases, center_a, product='A', **kwargs), side(b_cases, center_b, product='B', **kwargs),
                     None, source, 'ready', ())


def row_at(result, pair_id, size, diameter=5):
    return next(r for r in result.rows if r['pair_id'] == pair_id and r['window_size_um'] == size and r['diameter_um'] == diameter)


def test_hand_window_areas_pitch_half_and_cell_misalignment():
    full = [make_case2d('full', 'A', 'B', rect(range(4), range(4)))]
    quadrant = [make_case2d('q', 'A', 'B', rect(range(2), range(2)), product='B')]
    result = compute_local_delta([pair('aligned', full, quadrant, (1.0, 1.0), (1.0, 1.0)),
                                  pair('shifted', full, quadrant, (0.9, 1.1), (1.0, 1.0))],
                                 full, quadrant, window_sizes_um=[1.0])
    for pair_id in ('aligned', 'shifted'):
        window = row_at(result, pair_id, 1.0)['window']
        assert window['a']['union'].value == pytest.approx(1.0) and window['a']['union'].fraction_of_window == pytest.approx(1.0)
        assert window['a']['union'].window_area_um2 == 1.0 and window['a']['union'].status == 'available'
    assert row_at(result, 'shifted', 1.0)['window']['bounds_um']['a'] == [0.4, 0.6, 1.4, 1.6]
    aligned = row_at(result, 'aligned', 1.0)['window']
    assert aligned['b']['union'].value == pytest.approx(0.25) and aligned['b']['union'].fraction_of_window == pytest.approx(0.25)
    assert aligned['delta']['union'].value == pytest.approx(-0.75) and aligned['delta']['union'].status == 'available'
    assert aligned['delta']['union'].fraction_delta == pytest.approx(-0.75)
    assert aligned['bounds_um'] == {'a': [0.5, 0.5, 1.5, 1.5], 'b': [0.5, 0.5, 1.5, 1.5]}
    assert result.status == 'available' and result.reasons == ()


def test_identical_products_and_pure_translation_give_zero_delta():
    a = [make_case2d('q', 'A', 'B', rect(range(2), range(2)))]
    same = [make_case2d('q', 'A', 'B', rect(range(2), range(2)), product='B')]
    moved = [make_case2d('q', 'A', 'B', rect(range(2), range(1, 3)), product='B')]
    result = compute_local_delta([pair('same', a, same, (1.0, 1.0), (1.0, 1.0)),
                                  pair('moved', a, moved, (1.0, 1.0), (1.5, 1.0))],
                                 a, same + moved[:0] if False else same, window_sizes_um=[1.0])
    # separate run for the translated product, which is its own dataset
    translated = compute_local_delta([pair('moved', a, moved, (1.0, 1.0), (1.5, 1.0))], a, moved, window_sizes_um=[1.0])
    for res, pid in ((result, 'same'), (translated, 'moved')):
        for d in DIAMETERS_UM:
            delta = row_at(res, pid, 1.0, d)['window']['delta']
            assert all(delta[m].value == 0 and delta[m].status == 'available' for m in METRICS), (pid, d)
    assert row_at(translated, 'moved', 1.0)['window']['a']['union'].value == pytest.approx(0.25)
    assert row_at(translated, 'moved', 1.0)['window']['b']['union'].value == pytest.approx(0.25)


def test_clipped_window_and_valid_hole_inside_block_delta_but_hole_outside_does_not():
    a = [make_case2d('full', 'A', 'B', rect(range(4), range(4)))]
    b = [make_case2d('full', 'A', 'B', rect(range(4), range(4)), product='B')]
    clipped = compute_local_delta([pair('edge', a, b, (0.2, 1.0), (1.0, 1.0))], a, b, window_sizes_um=[1.0])
    window = row_at(clipped, 'edge', 1.0)['window']
    assert window['a']['union'].status == 'partial' and window['a']['union'].value is None
    assert window['a']['union'].partial_value == pytest.approx(0.7)
    assert window['delta']['union'].status == 'unavailable' and window['delta']['union'].value is None
    assert 'a_partial' in window['delta']['union'].reasons and 'a:partial_observation' in window['delta']['union'].reasons
    assert clipped.status == 'partial' and clipped.reasons == ('incomplete_local_delta',)
    valid = np.ones((4, 4), bool)
    valid[0, 0] = False  # hole at x∈[0,.5), y∈[0,.5)
    holed = [make_case2d('full', 'A', 'B', rect(range(4), range(4)), valid=valid)]
    outside = compute_local_delta([pair('far', holed, b, (1.5, 1.5), (1.0, 1.0))], holed, b, window_sizes_um=[1.0])
    far = row_at(outside, 'far', 1.0)['window']
    assert far['a']['union'].status == 'available' and far['a']['union'].value == 1.0
    assert far['delta']['union'].value == 0 and far['delta']['union'].status == 'available'
    inside = compute_local_delta([pair('near', holed, b, (0.5, 0.5), (1.0, 1.0))], holed, b, window_sizes_um=[1.0])
    near = row_at(inside, 'near', 1.0)['window']
    assert near['a']['union'].status == 'partial' and near['a']['union'].partial_value == pytest.approx(0.75)
    assert near['a']['union'].valid_area_um2 == pytest.approx(0.75)
    assert near['delta']['union'].status == 'unavailable'


def test_attributable_core_is_inner_square_and_not_applicable_when_window_le_diameter():
    shape = (16, 16)  # 8 um × 8 um at pitch .5
    a = [make_case2d('full', 'A', 'B', rect(range(16), range(16)), shape=shape)]
    b = [make_case2d('full', 'A', 'B', rect(range(16), range(16)), shape=shape, product='B')]
    result = compute_local_delta([pair('p', a, b, (4.0, 4.0), (4.0, 4.0))], a, b, window_sizes_um=[1.0, 6.0])
    small = row_at(result, 'p', 1.0)
    assert small['attributable_core']['side_um'] is None
    assert all(small['attributable_core']['delta'][m].status == 'not_applicable' for m in METRICS)
    assert small['attributable_core']['delta']['union'].reasons == ('window_smaller_than_diameter',)
    large = row_at(result, 'p', 6.0)
    assert large['window']['a']['union'].value == pytest.approx(36.0)
    assert large['attributable_core']['side_um'] == 1.0
    assert large['attributable_core']['a']['union'].value == pytest.approx(1.0)
    assert large['attributable_core']['bounds_um']['a'] == [3.5, 3.5, 4.5, 4.5]
    assert large['attributable_core']['delta']['union'].value == 0
    at_d1 = row_at(result, 'p', 1.0, 1)
    assert at_d1['attributable_core']['side_um'] is None  # 1.0 − 1 = 0 → not applicable
    at_d1_large = row_at(result, 'p', 6.0, 1)
    assert at_d1_large['attributable_core']['side_um'] == 5.0


def test_pieces_sum_to_union_inside_window_and_named_pair_is_bounded_by_union():
    eps = (Endpoint('A', 'gate', 'M1'), Endpoint('B', 'data', 'M2'))
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(2))),
         make_case2d('vert', 'A', 'B', rect(range(4), range(1, 3)), failure_type='vertical_short', kind='pixel')]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(2)), product='B'),
         make_case2d('vert', 'A', 'B', rect(range(4), range(1, 3)), product='B', failure_type='vertical_short', kind='pixel')]
    result = compute_local_delta([pair('p', a, b, (1.0, 1.0), (1.0, 1.0), eps=eps, failure_type='vertical_short')],
                                 a, b, window_sizes_um=[1.0])
    window = row_at(result, 'p', 1.0)['window']
    values = {m: window['a'][m].value for m in METRICS}
    assert values == {'union': pytest.approx(1.0), 'lateral_only': pytest.approx(0.0),
                      'vertical_only': pytest.approx(0.5), 'simultaneous': pytest.approx(0.5)}
    assert sum(values[p] for p in FAILURE_PIECES) == pytest.approx(values['union'])
    named = row_at(result, 'p', 1.0)['named_pair']
    assert named['cases']['a'] == {'case_id': 'vert', 'failure_type': 'vertical_short', 'pair_id': 'A-B', 'classification': 'pixel'}
    assert named['a'].value == pytest.approx(1.0) and named['a'].value <= window['a']['union'].value
    assert named['delta'].value == 0 and named['a'].basis == 'physical_window_single_case'
    # below the vertical height: vertical pieces N/A, lateral available, named vertical case N/A
    low = row_at(result, 'p', 1.0, 2)
    assert low['window']['a']['vertical_only'].status == 'not_applicable'
    assert low['window']['delta']['vertical_only'].status == 'not_applicable'
    assert low['window']['delta']['lateral_only'].status == 'available' and low['window']['delta']['union'].status == 'available'
    assert low['named_pair']['a'].status == 'not_applicable' and low['named_pair']['delta'].status == 'not_applicable'
    missing = compute_local_delta([pair('q', a, b, (1.0, 1.0), (1.0, 1.0),
                                        eps=(Endpoint('X', 'gate', 'M2'), Endpoint('Y', 'data', 'M2')))], a, b, window_sizes_um=[1.0])
    named = row_at(missing, 'q', 1.0)['named_pair']
    assert named['reasons'] == ['named_pair_case_not_found_a', 'named_pair_case_not_found_b']
    assert named['delta'].status == 'unavailable' and 'a:named_pair_case_not_found_a' in named['delta'].reasons


def test_missing_source_makes_window_partial_but_frame_hole_elsewhere_does_not():
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(4))), make_case2d('other', 'D', 'E', rect(range(4), range(4)))]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)), product='B'),
         make_case2d('other', 'D', 'E', rect(range(4), range(4)), product='B')]
    b[1].records[5] = replace(b[1].records[5], input_status='missing', mask=None, reasons=('source_input_missing',))
    result = compute_local_delta([pair('p', a, b, (1.0, 1.0), (1.0, 1.0))], a, b, window_sizes_um=[1.0])
    window = row_at(result, 'p', 1.0)['window']
    assert window['b']['union'].status == 'partial' and window['b']['union'].partial_value == pytest.approx(1.0)
    assert 'source_coverage_incomplete' in window['b']['union'].reasons
    assert window['delta']['union'].status == 'unavailable' and 'b_partial' in window['delta']['union'].reasons
    assert row_at(result, 'p', 1.0, 7)['window']['delta']['union'].value == 0  # other diameters unaffected


def test_group_missing_or_ambiguous_and_blocked_pairs():
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)))]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)), product='B'),
         make_case2d('lat2', 'A', 'C', rect(range(4), range(4)), product='B', step='STEP2')]
    result = compute_local_delta([pair('p', a, b, (1.0, 1.0), (1.0, 1.0))], a, b, window_sizes_um=[1.0])
    window = row_at(result, 'p', 1.0)['window']
    assert window['b']['union'].status == 'unavailable' and window['b']['union'].reasons == ('ambiguous_source_group_b',)
    filtered = compute_local_delta([pair('p', a, b, (1.0, 1.0), (1.0, 1.0), filters={'process_step_id': 'STEP2'})],
                                   a, b, window_sizes_um=[1.0])
    assert row_at(filtered, 'p', 1.0)['window']['b']['union'].status == 'available'
    assert row_at(filtered, 'p', 1.0)['window']['a']['union'].status == 'unavailable'  # A has no STEP2 group
    assert row_at(filtered, 'p', 1.0)['window']['a']['union'].reasons == ('no_source_group_a',)
    blocked = replace(pair('q', a, b, (1.0, 1.0), (1.0, 1.0)), status='blocked', reasons=('position_outside_source_frame_a',))
    result = compute_local_delta([blocked], a, b, window_sizes_um=[1.0])
    assert result.rows == [] and result.status == 'unavailable' and result.reasons == ('blocked_pairs_present',)


def test_jitter_ranking_and_sensitivity_with_three_pairs(tmp_path):
    shape = (8, 8)  # 4 um × 4 um
    a = [make_case2d('lat', 'A', 'C', set(), shape=shape)]
    # (row, col) cells: .25 um² near (1,1), .5 um² near (1,3), .75 um² near (3,1); all inside the 1 um and 2 um windows
    cells = rect([1], [1]) | rect([5], [1, 2]) | (rect([1], [5, 6]) | rect([2], [5]))
    b = [make_case2d('lat', 'A', 'C', cells, shape=shape, product='B')]
    pairs = [pair('P1', a, b, (1.0, 1.0), (1.0, 1.0)), pair('P2', a, b, (1.0, 3.0), (1.0, 3.0)),
             pair('P3', a, b, (3.0, 1.0), (3.0, 1.0))]
    result = compute_local_delta(pairs, a, b, window_sizes_um=[1.0, 2.0], jitter_um=0.25, sort_diameter_um=5)
    deltas = {pid: row_at(result, pid, 1.0)['window']['delta']['union'].value for pid in ('P1', 'P2', 'P3')}
    assert deltas == {'P1': pytest.approx(0.25), 'P2': pytest.approx(0.5), 'P3': pytest.approx(0.75)}
    assert [e['pair_id'] for e in result.ranking['by_window_size']['1.0']] == ['P3', 'P2', 'P1']
    assert [e['rank'] for e in result.ranking['by_window_size']['2.0']] == [1, 2, 3]
    jitter = row_at(result, 'P1', 1.0)['jitter']
    assert jitter['status'] == 'available' and jitter['sign_stable'] is True
    # +x / +y shifts leave only a .25 × .5 sliver of cell (1,1) inside the window; −x / −y shifts keep it whole
    assert jitter['delta_union_values'] == [pytest.approx(0.125), pytest.approx(0.25), pytest.approx(0.125), pytest.approx(0.25)]
    assert jitter['min'] == pytest.approx(0.125) and jitter['max'] == pytest.approx(0.25)
    agreement = result.sensitivity['rank_agreement']
    assert agreement == [{'sizes_um': [1.0, 2.0], 'spearman_abs_delta': pytest.approx(1.0), 'n_pairs': 3,
                          'status': 'available', 'reason': None}]
    per = {(e['pair_id'], e['diameter_um']): e for e in result.sensitivity['per_pair_diameter']}
    assert per[('P1', 5.0)]['sign_consistent'] is True and per[('P1', 5.0)]['delta_union_by_size'] == {'1.0': pytest.approx(0.25), '2.0': pytest.approx(0.25)}
    assert result.sensitivity['core_applicable_diameters_um']['2.0'] == [1.0]
    assert result.sensitivity['core_applicable_diameters_um']['1.0'] == []
    paths = write_local_delta(tmp_path, result)
    names = sorted(Path(p).name for p in paths)
    assert names == ['local_delta.json', 'local_delta_rows.csv', 'window_sensitivity.json',
                     'window_sensitivity_rank_agreement.csv', 'window_sensitivity_rows.csv']
    payload = json.loads((tmp_path / 'local_delta.json').read_text())
    assert payload['schema_version'] == 'local_delta_v1' and payload['status'] == 'available'
    assert len(payload['rows']) == 3 * 2 * len(DIAMETERS_UM)
    assert payload['rows'][0]['window']['delta']['union']['direction'] == 'b_minus_a'
    csv_text = (tmp_path / 'local_delta_rows.csv').read_text()
    assert csv_text.splitlines()[0].startswith('pair_id,window_size_um,diameter_um,kind,side_um,metric')


def test_spearman_hand_values():
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 2], [1, 2]) is None
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486832980505138)


@pytest.mark.parametrize('kwargs,code', [
    (dict(window_sizes_um=[]), 'window_sizes_required'),
    (dict(window_sizes_um=[1.0, 1.0]), 'duplicate_window_size'),
    (dict(window_sizes_um=[0.0]), 'invalid_number'),
    (dict(window_sizes_um=[1.0], sort_diameter_um=4.5), 'invalid_representative_diameter'),
    (dict(window_sizes_um=[1.0], jitter_um=-1), 'invalid_number'),
])
def test_invalid_arguments(kwargs, code):
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)))]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)), product='B')]
    with pytest.raises(NumericContractError) as info:
        compute_local_delta([pair('p', a, b, (1.0, 1.0), (1.0, 1.0))], a, b, **kwargs)
    assert info.value.code == code


def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'simcompare', *map(str, args)], capture_output=True, text=True)


def correspondence_file(path, *, a_product='reference'):
    def endpoint_rows():
        return [{'electrode_id': 'A', 'role': 'gate', 'actual_layer': 'M2'},
                {'electrode_id': 'B', 'role': 'data', 'actual_layer': 'M2'}]
    payload = {'schema_version': 'roi_correspondence_v1',
               'producer': {'kind': 'manual', 'version': '1', 'commit': None, 'created_at': '2026-10-18'},
               'pairs': [{'pair_id': 'P1',
                          'a': {'product_id': a_product, 'design_version': 'synthetic_design', 'frame_id': 'reference_frame',
                                'center_xy_um': [102.5, 200.5], 'analysis_layer': 'M2', 'endpoints': endpoint_rows(),
                                'failure_type': 'lateral_short'},
                          'b': {'product_id': 'target', 'design_version': 'synthetic_design', 'frame_id': 'target_frame',
                                'center_xy_um': [101.5, 200.5], 'analysis_layer': 'M2', 'endpoints': endpoint_rows(),
                                'failure_type': 'lateral_short'},
                          'source': {'kind': 'manual', 'labelled_by': 'designer', 'labelled_at': '2026-10-18'},
                          'status': 'ready', 'reasons': []}]}
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def test_cli_compare_windows_on_demo_data(tmp_path):
    data = tmp_path / 'demo'
    assert run_cli('demo-data', '--out', data).returncode == 0
    correspondence = correspondence_file(tmp_path / 'c.json')
    missing = run_cli('compare-windows', '--config', data / 'config.yaml', '--correspondence', correspondence)
    assert missing.returncode == 2 and 'window-sizes' in missing.stderr
    # the demo frames are one or two cells tall: a 2 um window leaves the frame → partial, exit 3
    clipped = run_cli('compare-windows', '--config', data / 'config.yaml', '--correspondence', correspondence,
                      '--window-sizes', 1, 2, '--out', tmp_path / 'clipped')
    assert clipped.returncode == 3 and json.loads(clipped.stdout)['reasons'] == ['incomplete_local_delta']
    run = run_cli('compare-windows', '--config', data / 'config.yaml', '--correspondence', correspondence,
                  '--window-sizes', 1, '--sort-diameter-um', 5, '--out', tmp_path / 'out')
    assert run.returncode == 0, run.stderr
    payload = json.loads((tmp_path / 'out' / 'local_delta.json').read_text())
    assert payload['status'] == 'available' and payload['provenance']['n_ready'] == 1
    assert payload['provenance']['correspondence_sha256']
    d5 = next(r for r in payload['rows'] if r['window_size_um'] == 1 and r['diameter_um'] == 5)
    assert d5['window']['a']['union']['value'] == 1.0 and d5['window']['b']['union']['value'] == 1.0
    assert d5['window']['delta']['union']['value'] == 0 and d5['named_pair']['cases']['a']['case_id'] == 'AB'
    assert d5['window']['a']['union']['fraction_of_window'] == 1.0
    assert (tmp_path / 'out' / 'execution.json').exists() and (tmp_path / 'out' / 'window_sensitivity.json').exists()
    assert json.loads((tmp_path / 'out' / 'execution.json').read_text())['command'] == 'compare-windows'
    swapped = correspondence_file(tmp_path / 'bad.json', a_product='target')
    bad = run_cli('compare-windows', '--config', data / 'config.yaml', '--correspondence', swapped,
                  '--window-sizes', 1, '--out', tmp_path / 'bad')
    assert bad.returncode == 2 and 'correspondence_product_mismatch' in bad.stderr
    assert json.loads((tmp_path / 'bad' / 'error.json').read_text())['reasons'] == ['correspondence_product_mismatch']


def test_explained_fraction_matches_product_delta_when_repeats_cover_everything():
    shape = (8, 8)  # 4 um × 4 um → frame area 16 um²
    a = [make_case2d('lat', 'A', 'C', set(), shape=shape)]
    cells = rect([1], [1]) | rect([5], [1, 2]) | (rect([1], [5, 6]) | rect([2], [5]))
    b = [make_case2d('lat', 'A', 'C', cells, shape=shape, product='B')]
    base = [pair('P1', a, b, (1.0, 1.0), (1.0, 1.0)), pair('P2', a, b, (1.0, 3.0), (1.0, 3.0)),
            pair('P3', a, b, (3.0, 1.0), (3.0, 1.0))]
    without = compute_local_delta(base, a, b, window_sizes_um=[1.0])
    assert without.explained['status'] == 'unavailable' and without.explained['reasons'] == ['repeats_per_unit_required']
    assert without.explained['pairs_without_repeats'] == ['P1', 'P2', 'P3']
    once = [replace(p, repeats_per_unit={'a': 1, 'b': 1}) for p in base]
    result = compute_local_delta(once, a, b, window_sizes_um=[1.0, 2.0])
    at5 = result.explained['by_window_size']['1.0']['5.0']
    assert at5['status'] == 'available'
    assert at5['explained_b'] == pytest.approx(1.5 / 16) and at5['explained_a'] == 0
    assert at5['actual_delta_fraction_of_frame'] == pytest.approx(1.5 / 16) and at5['residual'] == pytest.approx(0.0)
    at7 = result.explained['by_window_size']['1.0']['7.0']
    assert at7['explained_delta'] == 0 and at7['actual_delta_fraction_of_frame'] == 0
    twice = [replace(p, repeats_per_unit={'a': 1, 'b': 2}) for p in base]
    doubled = compute_local_delta(twice, a, b, window_sizes_um=[1.0]).explained['by_window_size']['1.0']['5.0']
    assert doubled['explained_delta'] == pytest.approx(3.0 / 16) and doubled['residual'] == pytest.approx(-1.5 / 16)
    # 2 um windows around (1,1) and (1,3) touch at y=2 (half-open → no overlap); 3 um windows overlap
    assert result.explained['by_window_size']['2.0']['5.0']['status'] == 'available'
    overlapping = compute_local_delta(once, a, b, window_sizes_um=[3.0]).explained['by_window_size']['3.0']['5.0']
    assert overlapping['status'] == 'unavailable' and 'overlapping_windows_a' in overlapping['reasons']
    assert json.loads(json.dumps(result.to_dict()))['explained_fraction']['direction'] == 'b_minus_a'


def test_compact_json_keeps_rows_in_csv_only(tmp_path):
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)))]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(4)), product='B')]
    result = compute_local_delta([pair('p', a, b, (1.0, 1.0), (1.0, 1.0))], a, b, window_sizes_um=[1.0], sort_diameter_um=5)
    full = json.loads((Path(write_local_delta(tmp_path / 'full', result)[0])).read_text())
    compact = json.loads((Path(write_local_delta(tmp_path / 'compact', result, compact=True)[0])).read_text())
    assert full['json_detail'] == 'full' and len(full['rows']) == full['n_rows'] == len(DIAMETERS_UM)
    assert compact['json_detail'] == 'compact' and compact['rows'] is None and compact['n_rows'] == len(DIAMETERS_UM)
    assert compact['ranking'] == full['ranking'] and compact['window_sensitivity'] == full['window_sensitivity']
    assert compact['rows_csv'] == 'local_delta_rows.csv'
    full_csv = (tmp_path / 'full' / 'local_delta_rows.csv').read_text()
    assert (tmp_path / 'compact' / 'local_delta_rows.csv').read_text() == full_csv
    assert len(full_csv.splitlines()) == 1 + len(DIAMETERS_UM) * 2 * len(METRICS)


def test_cropped_window_measurement_equals_full_frame_measurement():
    """crop_to_window must not change value, valid area, status or completeness (edges, holes, outside)."""
    import random
    from simcompare.local_delta import _measure_cropped
    from simcompare.measurement import measure_union
    from simcompare.numeric_schema import MaskRecord, PhysicalWindow
    rng = random.Random(1018)
    for trial in range(60):
        shape = (rng.randint(3, 9), rng.randint(3, 9))
        pitch = rng.choice([0.5, 1.0, 0.25])
        origin = (rng.choice([0.0, 10.0, -2.5]), rng.choice([0.0, 20.0]))
        valid = np.ones(shape, bool)
        if rng.random() < 0.5:
            valid[rng.randrange(shape[0]), rng.randrange(shape[1])] = False
        frame = Frame('f', pitch, origin, shape, valid)
        mask = np.array([[rng.random() < 0.4 for _ in range(shape[1])] for _ in range(shape[0])])
        width = shape[1] * pitch; height = shape[0] * pitch
        cx = origin[0] + rng.uniform(-0.3 * width, 1.3 * width)
        cy = origin[1] + rng.uniform(-0.3 * height, 1.3 * height)
        side = rng.choice([pitch * 0.7, pitch * 1.5, pitch * 3.3])
        window = PhysicalWindow(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
        full = measure_union(MaskRecord(5.0, frame, mask, analysis_layer='M2'), [window])
        cropped = _measure_cropped(frame, mask, window, diameter=5.0, analysis_layer='M2')
        assert cropped.status == full.status, (trial, cropped, full)
        assert cropped.ca_um2 == pytest.approx(full.ca_um2) if full.ca_um2 is not None else cropped.ca_um2 is None
        assert cropped.partial_value == pytest.approx(full.partial_value) if full.partial_value is not None else cropped.partial_value is None
        assert cropped.observed_ca_um2 == pytest.approx(full.observed_ca_um2)
        assert cropped.valid_area_um2 == pytest.approx(full.valid_area_um2)
        assert cropped.requested_area_um2 == pytest.approx(full.requested_area_um2)

