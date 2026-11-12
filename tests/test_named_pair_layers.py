"""06 named_pair matching key = (electrode, layer) endpoints + candidate case_id diagnostics (vdist-filter/T1)."""
from dataclasses import replace

import pytest

from simcompare.local_delta import _as_dataset, _named_pair, _square, compute_local_delta
from simcompare.numeric_schema import Endpoint
from test_local_delta import make_case2d, pair, rect, row_at


def vcase(case_id, layers, cells, *, product='A', failure_type='vertical_short', a='E1', b='E2'):
    """A case whose endpoint layers are `layers` (pair of actual_layer); analysis layer stays M2."""
    case = make_case2d(case_id, a, b, cells, failure_type=failure_type, kind='pixel', product=product)
    return replace(case, endpoints=(Endpoint(a, 'gate', layers[0]), Endpoint(b, 'data', layers[1])))


def eps(layers, a='E1', b='E2'):
    return (Endpoint(a, 'gate', layers[0]), Endpoint(b, 'data', layers[1]))


def named_of(a, b, layers_a, layers_b=None, **kwargs):
    layers_b = layers_a if layers_b is None else layers_b
    bound = pair('p', a, b, (1.0, 1.0), (1.0, 1.0), **kwargs)
    bound = replace(bound, a=replace(bound.a, endpoints=eps(layers_a)), b=replace(bound.b, endpoints=eps(layers_b)))
    result = compute_local_delta([bound], a, b, window_sizes_um=[1.0])
    return row_at(result, 'p', 1.0)['named_pair']


def crossed(product):
    return [vcase('x1', ('L1', 'L2'), rect(range(4), [1]), product=product),
            vcase('x2', ('L2', 'L1'), rect(range(4), [1, 2]), product=product)]


def test_a_crossed_layers_of_same_electrodes_each_find_their_own_case():
    a, b = crossed('A'), crossed('B')
    first = named_of(a, b, ('L1', 'L2'))
    second = named_of(a, b, ('L2', 'L1'))
    assert first['cases']['a']['case_id'] == 'x1' and first['cases']['b']['case_id'] == 'x1'
    assert second['cases']['a']['case_id'] == 'x2' and second['cases']['b']['case_id'] == 'x2'
    assert first['a'].value == pytest.approx(0.5) and second['a'].value == pytest.approx(1.0)
    assert first['reasons'] == [] and second['reasons'] == []
    assert 'candidates' not in first


def test_b_same_layers_split_by_failure_type():
    a = [vcase('v', ('L1', 'L2'), rect(range(4), [1]), failure_type='vertical_short'),
         vcase('l', ('L1', 'L2'), rect(range(4), [1, 2]), failure_type='lateral_short')]
    b = [vcase('v', ('L1', 'L2'), rect(range(4), [1]), failure_type='vertical_short', product='B'),
         vcase('l', ('L1', 'L2'), rect(range(4), [1, 2]), failure_type='lateral_short', product='B')]
    bound = pair('p', a, b, (1.0, 1.0), (1.0, 1.0), failure_type='lateral_short')
    bound = replace(bound, a=replace(bound.a, endpoints=eps(('L1', 'L2'))), b=replace(bound.b, endpoints=eps(('L1', 'L2'))))
    named = row_at(compute_local_delta([bound], a, b, window_sizes_um=[1.0]), 'p', 1.0)['named_pair']
    assert named['cases']['a']['case_id'] == 'l' and named['reasons'] == []
    bound = replace(bound, a=replace(bound.a, failure_type='vertical_short'), b=replace(bound.b, failure_type='vertical_short'))
    named = row_at(compute_local_delta([bound], a, b, window_sizes_um=[1.0]), 'p', 1.0)['named_pair']
    assert named['cases']['a']['case_id'] == 'v'


def test_c_true_duplicate_is_ambiguous_with_candidate_case_ids():
    a = [vcase('d1', ('L1', 'L2'), rect(range(4), [1])), vcase('d2', ('L1', 'L2'), rect(range(4), [2]))]
    b = [vcase('d1', ('L1', 'L2'), rect(range(4), [1]), product='B')]
    named = named_of(a, b, ('L1', 'L2'))
    assert named['reasons'] == ['named_pair_case_ambiguous_a']
    assert named['candidates'] == {'a': ['d1', 'd2']}
    assert named['cases']['b']['case_id'] == 'd1'


def test_d_layer_changed_pair_matches():
    a = [vcase('old', ('L1', 'L2'), rect(range(4), [1]))]
    b = [vcase('new', ('L2', 'L3'), rect(range(4), [1, 2]), product='B')]
    named = named_of(a, b, ('L1', 'L2'), ('L2', 'L3'))
    assert named['reasons'] == []
    assert named['cases']['a']['case_id'] == 'old' and named['cases']['b']['case_id'] == 'new'


def test_e_one_sided_and_cno_named_results_unchanged():
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(2)))]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(2)), product='B')]
    bound = pair('p', a, b, (1.0, 1.0), (1.0, 1.0), eps=(Endpoint('A', 'gate', 'M2'), Endpoint('C', 'data', 'M2')))
    named = _named_pair(_as_dataset(a), _as_dataset(b), bound, 5, _square((1.0, 1.0), 1.0), _square((1.0, 1.0), 1.0))
    assert named['reasons'] == [] and named['delta'].value == 0
    one = _named_pair(_as_dataset(a), _as_dataset([]), bound, 5, _square((1.0, 1.0), 1.0), _square((1.0, 1.0), 1.0))
    assert one['reasons'] == ['named_pair_case_not_found_b'] and one['cases']['a']['case_id'] == 'lat'
    assert one['candidates'] == {'b': []}


def test_f_side_without_endpoints_has_no_named_result():
    a = [make_case2d('lat', 'A', 'C', rect(range(4), range(2)))]
    b = [make_case2d('lat', 'A', 'C', rect(range(4), range(2)), product='B')]
    bound = pair('p', a, b, (1.0, 1.0), (1.0, 1.0))
    assert bound.a.endpoints is None
    result = compute_local_delta([bound], a, b, window_sizes_um=[1.0])
    assert row_at(result, 'p', 1.0)['named_pair'] is None


def test_g_case_only_difference_in_layer_is_not_found_with_diagnostic_candidates():
    a = [vcase('x1', ('L1', 'L2'), rect(range(4), [1]))]
    b = [vcase('x1', ('L1', 'L2'), rect(range(4), [1]), product='B')]
    named = named_of(a, b, ('l1', 'L2'), ('L1', 'L2'))
    assert named['reasons'] == ['named_pair_case_not_found_a']
    assert named['candidates'] == {'a': ['x1']}
    assert named['cases']['b']['case_id'] == 'x1'
