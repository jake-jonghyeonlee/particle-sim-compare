"""fraction_of_frame: 단위 없는 frame 면적 비율과 제품 간 delta (PLAN_v3 T3)."""
from dataclasses import replace

import numpy as np
import pytest

from simcompare.linepixel import FRACTION_NAMES, FAILURE_PIECES, compare_fractions, compute_areas, compute_rates
from simcompare.numeric_schema import NumericContractError
from test_linepixel import group
from test_local_delta import make_case2d, rect


def areas_of(cases, **kwargs):
    return compute_areas(cases, **kwargs)


def test_hand_fraction_quarter_of_one_square_micron_frame():
    cases = [make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 10), pitch=0.1)]
    g = group(areas_of(cases))
    assert g.frame_area_um2 == pytest.approx(1.0)
    f = g.fractions['union']
    assert f.value == pytest.approx(0.25) and f.status == 'available' and f.unit == '1'
    assert f.numerator_um2 == pytest.approx(0.25) and f.frame_area_um2 == pytest.approx(1.0)
    assert f.valid_area_um2 == pytest.approx(1.0) and f.basis == 'area_over_frame_area'
    assert g.fractions['line'].value == pytest.approx(0.25) and g.fractions['pixel_effective'].value == 0
    assert g.fractions['transfer'].value == 0 and g.fractions['lateral_only'].value == pytest.approx(0.25)
    assert set(g.fractions) == set(FRACTION_NAMES)
    assert sum(g.fractions[p].value for p in FAILURE_PIECES) == pytest.approx(g.fractions['union'].value)
    encoded = g.to_dict()
    assert encoded['frame_area_um2'] == pytest.approx(1.0)
    assert encoded['fraction_of_frame']['union']['value'] == pytest.approx(0.25)


def test_same_mask_in_twice_the_frame_keeps_area_but_halves_the_fraction():
    small = group(areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 10), pitch=0.1)]))
    large = group(areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 20), pitch=0.1, product='B')]))
    assert small.union.value == large.union.value == pytest.approx(0.25)  # absolute CA cannot tell the designs apart
    assert small.fractions['union'].value == pytest.approx(0.25)
    assert large.fractions['union'].value == pytest.approx(0.125)
    assert large.fractions['union'].frame_area_um2 == pytest.approx(2.0)


def test_valid_hole_and_missing_source_are_partial_and_na_is_not_applicable():
    valid = np.ones((10, 10), bool)
    valid[0, 0] = False
    g = group(areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 10), pitch=0.1, valid=valid)]))
    f = g.fractions['union']
    assert f.status == 'partial' and f.value is None
    assert f.partial_value == pytest.approx(0.24) and f.valid_area_um2 == pytest.approx(0.99)
    assert f.frame_area_um2 == pytest.approx(1.0) and 'partial_observation' in f.reasons
    cases = [make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 10), pitch=0.1),
             make_case2d('other', 'D', 'E', rect(range(2), range(2)), shape=(10, 10), pitch=0.1)]
    cases[1].records[5] = replace(cases[1].records[5], input_status='missing', mask=None, reasons=('source_input_missing',))
    g = group(areas_of(cases))
    assert g.fractions['union'].status == 'partial' and g.fractions['union'].partial_value == pytest.approx(0.25)
    vertical = [make_case2d('vert', 'A', 'B', rect(range(5), range(5)), shape=(10, 10), pitch=0.1, failure_type='vertical_short')]
    g = group(areas_of(vertical), diameter=1)
    assert all(g.fractions[name].status == 'not_applicable' and g.fractions[name].value is None
               and g.fractions[name].partial_value is None for name in FRACTION_NAMES)


def test_compare_fractions_delta_only_when_both_complete_and_layers_unknown_are_not_zero():
    a = areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 10), pitch=0.1)])
    b = areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 20), pitch=0.1, product='B')])
    deltas = compare_fractions(a, b)
    key = next(k for k in deltas if k[1] == 5)
    union = deltas[key]['union']
    assert union.value == pytest.approx(-0.125) and union.status == 'available'
    assert union.reference_value == pytest.approx(0.25) and union.target_value == pytest.approx(0.125)
    assert union.frame_area_um2 == {'reference': pytest.approx(1.0), 'target': pytest.approx(2.0)}
    assert union.direction == 'target_minus_reference' and union.unit == '1'
    zero = deltas[next(k for k in deltas if k[1] == 7)]['union']
    assert zero.value == 0 and zero.status == 'available'
    valid = np.ones((10, 20), bool)
    valid[3, 3] = False
    holed = areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 20), pitch=0.1, product='B', valid=valid)])
    blocked = compare_fractions(a, holed)[key]['union']
    assert blocked.value is None and blocked.status == 'unavailable'
    assert 'target_partial' in blocked.reasons and 'target:partial_observation' in blocked.reasons
    other_layer = areas_of([make_case2d('lat', 'A', 'C', rect(range(5), range(5)), shape=(10, 10), pitch=0.1, product='B', layer='M9')])
    mixed = compare_fractions(a, other_layer)
    assert len(mixed) == 2 * 32
    unknown = [m['union'] for (ck, d), m in mixed.items() if d == 5]
    assert {m.status for m in unknown} == {'unavailable'}
    assert {r for m in unknown for r in m.reasons} == {'reference_layer_unknown', 'target_layer_unknown'}
    vertical_a = areas_of([make_case2d('vert', 'A', 'B', rect(range(5), range(5)), shape=(10, 10), pitch=0.1, failure_type='vertical_short')])
    vertical_b = areas_of([make_case2d('vert', 'A', 'B', rect(range(5), range(5)), shape=(10, 10), pitch=0.1, failure_type='vertical_short', product='B')])
    na = compare_fractions(vertical_a, vertical_b)[next(k for k in compare_fractions(vertical_a, vertical_b) if k[1] == 2)]['union']
    assert na.status == 'not_applicable' and na.value is None
    with pytest.raises(NumericContractError, match='expected_area_result'):
        compare_fractions(a, None)


def test_report_has_fraction_table_and_html_section(tmp_path):
    from simcompare.report import comparison_report, write_numeric_report
    from test_density import density
    from test_panel_comparison import weighted
    from test_linepixel import make_case
    reference = weighted([make_case('a', 'A', 'B', {0, 1}, product='ref')], product='ref', panel=(60, 10))
    target = weighted([make_case('a', 'A', 'B', {0, 1, 2}, product='target')], product='target', panel=(30, 10))
    from simcompare.linepixel import compare_rates
    deltas = compare_rates(reference, target, reference_product_id='ref', target_product_id='target')
    payload, tables = comparison_report(reference, target, deltas)
    assert 'fractions' in tables and payload['fraction_of_frame_note']
    rows = [r for r in tables['fractions'] if r['diameter_um'] == 5 and r['metric'] == 'union']
    by_side = {r['side']: r for r in rows}
    assert by_side['reference']['value'] == pytest.approx(2 / 6) and by_side['target']['value'] == pytest.approx(3 / 6)
    assert by_side['delta']['value'] == pytest.approx(1 / 6) and by_side['delta']['status'] == 'available'
    assert by_side['reference']['frame_area_um2'] == 6 and by_side['delta']['unit'] == '1'
    assert payload['fraction_deltas'][0]['metrics']['union']['direction'] == 'target_minus_reference'
    write_numeric_report(tmp_path, 'comparison', payload, tables)
    assert (tmp_path / 'comparison_fractions.csv').exists()
    html = (tmp_path / 'comparison.html').read_text()
    assert '<h2>frame 면적 비율 (fraction_of_frame)</h2>' in html and 'G2의 한 unit 환산이 필요 없습니다' in html
    # existing tables unchanged
    values = {r['class']: r['value'] for r in tables['summary'] if r['side'] == 'reference' and r['domain'] == 'panel'}
    assert set(values) == {'line', 'pixel', 'transfer'}
