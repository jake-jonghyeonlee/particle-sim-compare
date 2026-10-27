"""전극 수 지도 — 손으로 검산할 수 있는 작은 예."""
import numpy as np
import pytest

from simcompare.electrode_map import DefectHotspot, electrode_count_maps, find_regions, summary_map


def mask(*cols):
    m = np.zeros((1, 12), dtype=bool)
    m[0, list(cols)] = True
    return m


def spot(electrodes, cols, layer="L1", d=5.0):
    return DefectHotspot(layer, tuple(electrodes), d, mask(*cols))


SPOTS = [
    spot(["gate", "data"], range(0, 6)),       # short
    spot(["gate", "com"], range(4, 8)),        # short
    spot(["gate"], range(10, 12)),             # open
    spot(["data", "pixel"], range(4, 6), layer="L2"),
    spot(["gate", "pixel"], range(0, 12), d=7.0),  # 다른 지름 — 5 µm 지도에는 안 들어감
]


def test_counts_distinct_electrodes_not_defect_cases():
    counts = electrode_count_maps(SPOTS)["L1"]
    assert counts[0, :4].tolist() == [2, 2, 2, 2]      # gate, data
    assert counts[0, 4:6].tolist() == [3, 3]           # gate, data, com (gate는 한 번)
    assert counts[0, 6:8].tolist() == [2, 2]           # gate, com
    assert counts[0, 10:].tolist() == [1, 1]           # open은 전극 하나


def test_layers_are_separate_and_summary_takes_the_maximum():
    maps = electrode_count_maps(SPOTS)
    assert set(maps) == {"L1", "L2"}
    assert maps["L2"][0, 4:6].tolist() == [2, 2]
    assert summary_map(maps)[0, 4:6].tolist() == [3, 3]


def test_other_diameters_are_ignored():
    assert electrode_count_maps(SPOTS, diameter_um=7.0)["L1"].max() == 2


def test_regions_are_ranked_and_list_the_electrodes_involved():
    regions = find_regions(SPOTS, min_count=2)
    top = regions[0]
    assert (top.layer, top.max_count, top.area_px, top.bbox) == ("L1", 3, 8, (0, 0, 1, 8))
    assert top.electrodes == ("com", "data", "gate")
    assert [(r.layer, r.max_count) for r in regions] == [("L1", 3), ("L2", 2)]
    assert find_regions(SPOTS, min_count=3)[0].area_px == 2


def test_no_hotspot_at_the_diameter_and_mismatched_grid_are_errors():
    with pytest.raises(ValueError, match="hotspot이 없다"):
        summary_map(electrode_count_maps(SPOTS, diameter_um=3.0))
    bad = DefectHotspot("L1", ("a", "b"), 5.0, np.zeros((2, 12), dtype=bool))
    with pytest.raises(ValueError, match="격자"):
        electrode_count_maps(SPOTS + [bad])

# canonical numeric_v2 경로: 기존 open fixture와 별도로 scope pending을 검사한다.
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

from simcompare.adapters.numeric import load_dataset
from simcompare.electrode_map import compute_electrode_maps
from simcompare.numeric_schema import (
    CaseIdentity, Endpoint, Frame, MaskRecord, NumericCase, NumericContractError, SourceCA,
)


def numeric_case(case_id, electrodes=('A', 'B'), cells=((0, 0),), *, layer='L1',
                 shape=(1, 2), pitch=1, origin=(0, 0), valid=None, diameter=5,
                 failure_type='lateral_short', step='step', product='product',
                 status='observed', frame_id='frame'):
    frame = Frame(frame_id, pitch, origin, shape, np.ones(shape, bool) if valid is None else valid)
    identity = CaseIdentity(product, 'design', 'source', step, 'exposure', case_id, frame_id, 'synthetic_v2')
    arr = np.zeros(shape, bool)
    for cell in cells:
        arr[cell] = True
    record = MaskRecord(diameter, frame, arr if status == 'observed' else None,
                        status, () if status == 'observed' else ('source_input_missing',),
                        identity=identity, analysis_layer=layer)
    endpoints = tuple(Endpoint(name, 'role-'+str(n), layer) for n, name in enumerate(electrodes))
    return NumericCase(identity, endpoints, failure_type, None, None, layer, layer,
                       {diameter: record}, {'case_id': case_id, 'ordered_endpoints': [vars(e) for e in endpoints]},
                       {'unit_id': 'unit', 'layout_asset': 'original-layout.npy'})


def test_canonical_duplicate_pairs_distinct_electrodes_and_layer_maximum():
    cases = [numeric_case('AB'), numeric_case('AC', ('A', 'C')),
             numeric_case('BA', ('B', 'A')), numeric_case('BA2', ('B', 'A')),
             numeric_case('CD', ('C', 'D'), layer='L2')]
    result = compute_electrode_maps(cases, min_count=3)
    assert result.layers[0].counts.tolist() == [[3, 0]]
    assert result.layers[1].counts.tolist() == [[2, 0]]
    assert result.summaries[0].counts.tolist() == [[3, 0]]  # max, 합 5도 전체 union 4도 아님
    assert all(layer.status == 'available' for layer in result.layers)
    assert result.layers[0].coverage['source_coverage_status'] == 'available'
    assert result.provenance['G7b'] == {'status':'answered','value':'short_only','authority':'user','decided_on':'2026-10-08'}
    assert result.provenance['defect_scope'] == 'short_only'
    assert result.provenance['domain'] == 'full_mask_valid'
    region = result.layers[0].regions[0]
    assert region.electrodes == ('A', 'B', 'C')
    assert len(region.cases) == 4
    assert region.review_roi_status == 'context_required'
    assert region.review_roi_reasons == ()


def test_canonical_diagonal_cells_are_separate_and_ids_are_stable():
    cases = [numeric_case('AB', cells=((0, 0), (1, 1)), shape=(2, 2)),
             numeric_case('AC', ('A', 'C'), cells=((0, 0), (1, 1)), shape=(2, 2))]
    result = compute_electrode_maps(cases, min_count=3)
    regions = result.layers[0].regions
    assert len(regions) == 2
    assert [r.area_um2 for r in regions] == [1, 1]
    assert {r.bbox_yx for r in regions} == {(0, 0, 1, 1), (1, 1, 2, 2)}
    assert [r.region_id for r in regions] == sorted(r.region_id for r in regions)
    reversed_regions = compute_electrode_maps(reversed(cases), min_count=3).layers[0].regions
    assert [r.region_id for r in regions] == [r.region_id for r in reversed_regions]
    assert result.provenance['connectivity'] == 4


def test_physical_coordinates_valid_hole_and_original_vs_region_ca():
    # pitch=0.5: 관측 활성 셀 3개 원본 CA=0.75, non-rectangular ROI 셀 2개 CA=0.5.
    valid = [[1, 0, 1], [1, 1, 1]]
    ab = numeric_case('AB', cells=((0, 0), (0, 1), (0, 2), (1, 0)),
                      shape=(2, 3), pitch=.5, origin=(10, 20), valid=valid)
    ac = numeric_case('AC', ('A', 'C'), cells=((0, 0), (0, 1), (1, 0)),
                      shape=(2, 3), pitch=.5, origin=(10, 20), valid=valid)
    result = compute_electrode_maps([ab, ac], min_count=3)
    layer = result.layers[0]
    assert layer.counts.tolist() == [[3, 0, 2], [3, 0, 0]]
    region = layer.regions[0]
    assert region.bbox_yx == (0, 0, 2, 1)
    assert region.region_bbox_um == (10, 20, 10.5, 21)
    assert region.center_xy_um == (10.25, 20.5)
    assert region.area_px == 2 and region.area_um2 == .5
    assert region.coverage['valid_area_um2'] == 1.25
    assert region.coverage['full_area_um2'] == 1.5
    assert region.coverage['region_valid_area_um2'] == .5
    assert region.coverage['source_coverage_status'] == 'partial'
    links = {link.identity['case_id']: link for link in region.cases}
    assert links['AB'].original_case_ca.ca_um2 is None
    assert links['AB'].original_case_ca.partial_value == .75
    assert links['AB'].case_ca_in_region.ca_um2 == .5
    assert links['AC'].original_case_ca.ca_um2 is None
    assert links['AC'].original_case_ca.partial_value == .5
    assert links['AC'].case_ca_in_region.ca_um2 == .5
    assert links['AB'].original_case_ca.basis == 'full_mask_valid'
    assert links['AB'].raw['case_id'] == 'AB'
    assert links['AB'].endpoints[0]['actual_layer'] == 'L1'
    payload = result.to_dict()
    assert payload['layers'][0]['regions'][0]['frame']['origin_xy_um'] == [10, 20]
    assert payload['layers'][0]['regions'][0]['cases'][0]['source_metadata']['layout_asset'] == 'original-layout.npy'
    json.dumps(payload, allow_nan=False)


def test_region_ca_measures_component_not_its_bounding_rectangle():
    ab = numeric_case('AB', cells=((0, 0), (0, 1), (1, 0), (1, 1)), shape=(2, 2))
    ac = numeric_case('AC', ('A', 'C'), cells=((0, 0), (0, 1), (1, 0)), shape=(2, 2))
    region = compute_electrode_maps([ab, ac], min_count=3).layers[0].regions[0]
    assert region.region_bbox_um == (0, 0, 2, 2)
    assert region.area_um2 == 3
    assert region.cases[0].original_case_ca.ca_um2 == 4
    assert region.cases[0].case_ca_in_region.ca_um2 == 3  # bbox 전체의 4가 아님


@pytest.mark.parametrize('kwargs', [{'origin': (1, 0)}, {'pitch': .5},
                                    {'valid': [[1, 0]]}, {'frame_id': 'other'}])
def test_incompatible_layer_frames_preserve_layers_but_disable_summary(kwargs):
    result = compute_electrode_maps([numeric_case('AB'), numeric_case('CD', ('C', 'D'), layer='L2', **kwargs)], min_count=2)
    assert len(result.layers) == 2 and all(layer.counts[0, 0] == 2 for layer in result.layers)
    summary = result.summaries[0]
    assert summary.status == 'unavailable' and summary.counts is None
    assert 'incompatible_layer_frames' in summary.reasons


def test_representative_diameter_change_absence_and_observed_zero():
    case = numeric_case('AB')
    extra = numeric_case('AB', ('A', 'B'), cells=((0, 1),), diameter=7)
    assert compute_electrode_maps([extra]).layers[0].status == 'unavailable'
    case.records[7] = extra.records[7]
    assert compute_electrode_maps([case], diameter_um=7).layers[0].counts.tolist() == [[0, 2]]
    missing = compute_electrode_maps([case], diameter_um=3).layers[0]
    assert missing.status == 'unavailable'
    assert missing.coverage['missing'][0]['reasons'] == ['representative_diameter_missing']
    zero = compute_electrode_maps([numeric_case('zero', cells=())]).layers[0]
    assert zero.counts.tolist() == [[0, 0]]
    assert missing.counts is None
    assert zero.coverage['source_coverage_status'] == 'available'
    assert zero.status == 'available'  # short-only scope가 확정된 관측 0


def test_missing_case_count_is_a_lower_bound_and_type_is_preserved():
    lat = numeric_case('lat')
    vert = numeric_case('vert', ('A', 'C'), failure_type='vertical_short', status='missing')
    result = compute_electrode_maps([lat, vert], min_count=2)
    layer = result.layers[0]
    assert layer.counts.tolist() == [[2, 0]]
    assert layer.coverage['source_coverage_status'] == 'partial'
    assert layer.coverage['missing'][0]['identity']['case_id'] == 'vert'
    assert layer.coverage['not_applicable'] == []
    vert.records[5] = numeric_case('vert', ('A', 'C'), failure_type='vertical_short').records[5]
    region = compute_electrode_maps([lat, vert], min_count=3).layers[0].regions[0]
    assert {c.failure_type for c in region.cases} == {'lateral_short', 'vertical_short'}


def test_loader_integration_needs_neither_pairs_nor_density_and_checks_na_evidence():
    dataset = load_dataset(Path(__file__).parent/'fixtures/numeric_v2')
    dataset.pairs.clear()
    dataset.density.clear()
    for case in dataset.cases:
        case.classification = None
        case.pair_id = None
    result = compute_electrode_maps(dataset, min_count=2)
    assert result.layers[0].counts.tolist() == [[2, 2, 0, 0], [0, 0, 0, 0]]
    assert {c.failure_type for c in result.layers[0].regions[0].cases} == {'lateral_short', 'vertical_short'}
    below = compute_electrode_maps(dataset, diameter_um=2, min_count=2).layers[0]
    assert len(below.coverage['not_applicable']) == 2
    assert len(below.coverage['observed']) == 1
    dataset.cases[1].metadata['height_evidence'] = None
    with pytest.raises(NumericContractError, match='not_applicable_evidence_required'):
        compute_electrode_maps(dataset)


def test_manifest_missing_case_is_not_complete():
    dataset = load_dataset(Path(__file__).parent/'fixtures/numeric_v2')
    dataset.cases.pop()
    result = compute_electrode_maps(dataset, min_count=2)
    assert result.layers[0].coverage['source_coverage_status'] == 'partial'
    assert 'manifest_case_missing' in result.layers[0].reasons


def test_source_ca_conservation_failure_is_reported_not_rescaled():
    case = numeric_case('AB')
    r = case.records[5]
    case.records[5] = replace(r, source_ca=SourceCA(9, r.frame.frame_id, r.frame.valid_hash))
    layer = compute_electrode_maps([case], min_count=2).layers[0]
    assert layer.status == 'unavailable'
    assert layer.counts is None
    assert 'conservation_failed' in layer.reasons
    assert layer.coverage['measurement_errors'][0]['identity']['case_id'] == 'AB'


def test_canonical_open_is_rejected_by_user_short_only_decision_legacy_unchanged():
    with pytest.raises(NumericContractError, match='map_requires_short_only'):
        compute_electrode_maps([numeric_case('AB'), numeric_case('open', ('A',), failure_type='open')])
    assert electrode_count_maps([spot(['gate'], [10])])['L1'][0, 10] == 1


@pytest.mark.parametrize('diameter', [0, -1, float('nan'), float('inf'), True])
def test_invalid_representative_diameter(diameter):
    with pytest.raises(NumericContractError):
        compute_electrode_maps([numeric_case('AB')], diameter_um=diameter)
    with pytest.raises(NumericContractError):
        electrode_count_maps(SPOTS, diameter_um=diameter)


@pytest.mark.parametrize('arr', [np.zeros((1, 1, 1)), [[float('nan')]], [[-1]], [[.5]], []])
def test_legacy_masks_are_exact_finite_2d_binary(arr):
    with pytest.raises(NumericContractError):
        DefectHotspot('L1', ('A', 'B'), 5, arr)


@pytest.mark.parametrize('electrodes', [(), ('',), (' ',)])
def test_empty_electrodes_rejected(electrodes):
    with pytest.raises(NumericContractError):
        DefectHotspot('L1', electrodes, 5, [[1]])
    with pytest.raises(NumericContractError):
        compute_electrode_maps([numeric_case('bad', electrodes)])


def test_no_scipy_or_legacy_runtime_dependency():
    script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('scipy') or fullname in ('simcompare.schema', 'simcompare.preprocess', 'simcompare.adapters.example'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from simcompare.electrode_map import DefectHotspot, find_regions
import simcompare.linepixel, simcompare.density
assert len(find_regions([DefectHotspot('L1', ('A', 'B'), 5, [[1,0],[0,1]])], min_count=2)) == 2
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_region_sort_order_count_then_area_then_stable_id():
    cases = [numeric_case('AB', cells=((0, 0), (0, 2), (0, 3), (0, 5)), shape=(1, 7)),
             numeric_case('AC', ('A', 'C'), cells=((0, 0), (0, 5)), shape=(1, 7)),
             numeric_case('AD', ('A', 'D'), cells=((0, 5),), shape=(1, 7))]
    regions = compute_electrode_maps(cases, min_count=2).layers[0].regions
    assert [(r.max_count, r.area_um2) for r in regions] == [(4, 1), (3, 1), (2, 2)]


def test_product_summary_combines_two_layer_steps_with_hand_written_maximum():
    cases = [numeric_case('AB',step='STEP_L1'), numeric_case('AC',('A','C'),step='STEP_L1'),
             numeric_case('CD',('C','D'),layer='L2',step='STEP_L2')]
    cases[2].identity = replace(cases[2].identity,source_id='other_source',exposure_id='other_exposure')
    cases[2].records[5] = replace(cases[2].records[5],identity=cases[2].identity)
    result = compute_electrode_maps(cases)
    assert len(result.layers) == 2 and len(result.summaries) == 1
    assert result.summaries[0].counts.tolist() == [[3,0]]
    provenance = result.summaries[0].provenance
    assert provenance['combined_steps'] == ['STEP_L1','STEP_L2']
    assert provenance['combined_sources'] == ['other_source','source']
    assert provenance['combined_exposures'] == ['exposure','other_exposure']
    assert len(provenance['layer_inputs']) == 2


def test_product_design_and_unit_stay_separate_in_summary():
    a = numeric_case('AB')
    b = numeric_case('CD',('C','D'),product='other')
    c = numeric_case('EF',('E','F'));c.metadata['unit_id']='unit2'
    d = numeric_case('GH',('G','H'));d.identity=replace(d.identity,design_version='other')
    d.records[5]=replace(d.records[5],identity=d.identity)
    result = compute_electrode_maps([a,b,c,d],min_count=2)
    assert len(result.layers)==len(result.summaries)==4
    assert all(m.counts.tolist()==[[2,0]] for m in result.summaries)


@pytest.mark.parametrize('threshold', [0, -1, 1.5, True])
def test_invalid_connectivity_threshold(threshold):
    with pytest.raises(NumericContractError, match='invalid_count_threshold'):
        compute_electrode_maps([numeric_case('AB')], min_count=threshold)


def test_duplicate_case_and_same_layer_incompatible_frame_rejected():
    case = numeric_case('AB')
    with pytest.raises(NumericContractError, match='duplicate_source_case'):
        compute_electrode_maps([case, case])
    with pytest.raises(NumericContractError, match='incompatible_source_frame'):
        compute_electrode_maps([case, numeric_case('AC', ('A', 'C'), origin=(1, 0))])


def test_region_coverage_does_not_alias_the_layer_coverage():
    """RB-A12: a caller that mutates the LayerMap coverage cannot change the regions' (and vice versa); lists are tuples
    shared by the regions of a layer, and the serialised form is the layer's own."""
    import json
    cases = [numeric_case('AB', cells=((0, 0), (1, 1)), shape=(2, 2)),
             numeric_case('AC', ('A', 'C'), cells=((0, 0), (1, 1)), shape=(2, 2))]
    layer = compute_electrode_maps(cases, min_count=3).layers[0]
    first, second = layer.regions
    keys = ('observed', 'missing', 'not_applicable', 'measurement_errors')
    before = {key: json.dumps(first.coverage[key], sort_keys=True) for key in keys}
    assert all(isinstance(first.coverage[key], tuple) for key in keys)
    assert all(first.coverage[key] is second.coverage[key] for key in keys)        # shared, not copied per region
    for key in keys:
        assert json.dumps(first.coverage[key], sort_keys=True) == json.dumps(layer.coverage[key], sort_keys=True)
    layer.coverage['observed'].append({'injected': True})
    layer.coverage['observed'][0]['identity_mutated'] = True
    layer.coverage['source_coverage_summary']['x'] = 1
    assert {key: json.dumps(first.coverage[key], sort_keys=True) for key in keys} == before
    assert 'x' not in first.coverage['source_coverage_summary']
    with pytest.raises(AttributeError):
        first.coverage['observed'].append(1)


def test_valid_hole_is_excluded_from_region_and_bbox_coverage():
    # 연결된 3x3 고리: 가운데 invalid 1셀, 셀 면적=1.
    cells = tuple((r, c) for r in range(3) for c in range(3))
    valid = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]
    result = compute_electrode_maps([numeric_case('AB', cells=cells, shape=(3, 3), valid=valid)], min_count=2)
    region = result.layers[0].regions[0]
    assert region.area_um2 == 8 and region.region_bbox_um == (0, 0, 3, 3)
    assert not region.mask[1, 1]
    assert region.coverage['bbox_valid_area_um2'] == 8
    assert region.coverage['bbox_area_um2'] == 9
    assert region.cases[0].original_case_ca.ca_um2 is None
    assert region.cases[0].original_case_ca.partial_value == 8
    assert region.cases[0].case_ca_in_region.ca_um2 == 8


def test_unavailable_layer_and_summary_have_null_counts_with_frame_and_reasons():
    missing=numeric_case('missing',status='missing')
    result=compute_electrode_maps([missing])
    assert result.layers[0].status==result.summaries[0].status=='unavailable'
    assert result.layers[0].counts is result.summaries[0].counts is None
    assert result.layers[0].frame.origin_xy_um==(0,0)
    assert 'source_case_missing' in result.layers[0].reasons
    document=result.to_dict()
    assert document['layers'][0]['counts'] is document['summaries'][0]['counts'] is None


def test_summary_preserves_known_layer_lower_bound_when_other_layer_missing():
    cases=[numeric_case('AB',step='L1'),numeric_case('AC',('A','C'),step='L1'),
           numeric_case('CD',('C','D'),layer='L2',step='L2',status='missing')]
    result=compute_electrode_maps(cases)
    assert len(result.summaries)==1 and result.summaries[0].counts.tolist()==[[3,0]]
    assert result.summaries[0].status=='partial'
    assert 'source_case_missing' in result.summaries[0].reasons
    assert len(result.summaries[0].coverage['layer_maps'])==2


def test_default_map_has_no_regions_or_ranking_and_is_scope_complete():
    result=compute_electrode_maps([numeric_case('AB'),numeric_case('AC',('A','C'))])
    assert result.layers[0].counts.tolist()==[[3,0]]
    assert result.layers[0].regions==()
    assert result.provenance['threshold'] is None
    assert result.provenance['connectivity'] is None
    assert result.provenance['regions_requested'] is False
    assert result.layers[0].status==result.summaries[0].status=='available'
    assert 'map_scope_unconfirmed' not in result.layers[0].reasons


# ---- 등급 지도(T3a): compute_graded_electrode_maps ---------------------------------------------------------------
GRADES = {}


def graded(cases, grades, *, k=2, select='high', **kwargs):
    """grades: case_id -> 'High'|'Low'|'None'|None. (ElectrodeMaps, GradedMaps)."""
    from simcompare.electrode_map import compute_graded_electrode_maps
    return compute_graded_electrode_maps(cases, lambda case: grades.get(case.identity.case_id), min_count=k, roi_grade=select, **kwargs)


def test_graded_counts_overlap_reverse_duplicate_and_grade_max():
    cases = [numeric_case('AB'), numeric_case('AC', ('A', 'C')), numeric_case('BA', ('B', 'A')), numeric_case('BA2', ('B', 'A')),
             numeric_case('XY', ('X', 'Y'), cells=((0, 1),))]
    maps, gm = graded(cases, {'AB': 'High', 'AC': 'High', 'BA': 'High', 'BA2': 'High', 'XY': 'None'})
    assert maps.layers[0].counts.tolist() == [[3, 2]]            # 기존 count_all 은 그대로
    layer = gm.layers[0]
    assert layer.count_high.tolist() == [[3, 0]]                 # A-B, A-C 겹침 3, 역순·중복 case 는 전극을 두 번 세지 않는다
    assert layer.count_visible.tolist() == [[3, 0]]
    assert layer.grade_max.tolist() == [[3, 1]]                  # None 만 덮인 셀은 1
    assert (layer.count_high.dtype, layer.count_visible.dtype, layer.grade_max.dtype) == (np.uint16, np.uint16, np.uint8)


def test_reverse_duplicate_cases_count_two():
    cases = [numeric_case('AB'), numeric_case('BA', ('B', 'A')), numeric_case('BA2', ('B', 'A'))]
    _, gm = graded(cases, {'AB': 'High', 'BA': 'High', 'BA2': 'High'})
    assert gm.layers[0].count_high.tolist() == [[2, 0]]


def test_count_high_le_count_visible_le_count_all_cellwise_and_values_in_range():
    rng = np.random.RandomState(7)
    cases, grades = [], {}
    names = 'ABCDEF'
    for index in range(24):
        a, b = rng.choice(6, 2, replace=False)
        cells = [(int(r), int(c)) for r in range(3) for c in range(4) if rng.rand() < .4]
        cases.append(numeric_case(f'c{index}', (names[a], names[b]), cells=cells, shape=(3, 4)))
        grades[f'c{index}'] = [None, 'None', 'Low', 'High'][index % 4]
    maps, gm = graded(cases, grades, k=1, select='low_or_high')
    counts, layer = maps.layers[0].counts, gm.layers[0]
    assert (layer.count_high <= layer.count_visible).all() and (layer.count_visible <= counts).all()
    assert set(np.unique(layer.grade_max).tolist()) <= {0, 1, 2, 3}
    assert layer.grade_max.dtype == np.uint8


def test_ungraded_case_adds_to_count_all_only_and_grade_max_zero():
    maps, gm = graded([numeric_case('AB')], {})
    assert maps.layers[0].counts.tolist() == [[2, 0]]
    assert gm.layers[0].count_high.tolist() == [[0, 0]] and gm.layers[0].grade_max.tolist() == [[0, 0]]
    assert maps.layers[0].regions == ()                          # sel 지도가 0 이라 영역 없음


def test_regions_follow_the_selected_map_not_count_all_ranking_counterexample():
    # A: 전부 None 인 5개 전극(count_all 5) / B: High 인 3개 전극. 비가중은 A>B, 등급(high)은 B 만 영역이 된다.
    none_cases = [numeric_case(f'n{i}', (f'N{i}', f'M{i}'), cells=((0, 0),), shape=(1, 6)) for i in range(2)]
    none_cases.append(numeric_case('n2', ('N0', 'Q'), cells=((0, 0),), shape=(1, 6)))
    high_cases = [numeric_case('h0', ('H0', 'H1'), cells=((0, 4),), shape=(1, 6)), numeric_case('h1', ('H0', 'H2'), cells=((0, 4),), shape=(1, 6))]
    grades = {c.identity.case_id: 'None' for c in none_cases}
    grades.update({c.identity.case_id: 'High' for c in high_cases})
    maps, gm = graded(none_cases + high_cases, grades, k=3)
    regions = maps.layers[0].regions
    assert [r.bbox_yx for r in regions] == [(0, 4, 1, 5)]
    assert regions[0].max_count == 3
    plain = compute_electrode_maps(none_cases + high_cases, min_count=3).layers[0].regions
    assert sorted(r.bbox_yx for r in plain) == [(0, 0, 1, 1), (0, 4, 1, 5)]
    assert max(r.max_count for r in plain) == 5 and regions[0].max_count == 3


def test_high_island_at_end_of_long_none_region_and_two_islands_are_separate_regions():
    long_none = numeric_case('n', ('X', 'Y'), cells=[(0, c) for c in range(10)], shape=(1, 10))
    left = numeric_case('hl', ('A', 'B'), cells=((0, 0), (0, 1)), shape=(1, 10))
    right = numeric_case('hr', ('C', 'D'), cells=((0, 8), (0, 9)), shape=(1, 10))
    maps, _ = graded([long_none, left, right], {'n': 'None', 'hl': 'High', 'hr': 'High'}, k=2)
    assert sorted(r.bbox_yx for r in maps.layers[0].regions) == [(0, 0, 1, 2), (0, 8, 1, 10)]
    only_right, _ = graded([long_none, right], {'n': 'None', 'hr': 'High'}, k=2)
    assert [r.bbox_yx for r in only_right.layers[0].regions] == [(0, 8, 1, 10)]


def test_low_or_high_selects_visible_map_and_high_ignores_low():
    cases = [numeric_case('lo', ('A', 'B'), cells=((0, 0),), shape=(1, 3)), numeric_case('hi', ('C', 'D'), cells=((0, 2),), shape=(1, 3))]
    grades = {'lo': 'Low', 'hi': 'High'}
    high, _ = graded(cases, grades, k=2, select='high')
    visible, _ = graded(cases, grades, k=2, select='low_or_high')
    assert [r.bbox_yx for r in high.layers[0].regions] == [(0, 2, 1, 3)]
    assert sorted(r.bbox_yx for r in visible.layers[0].regions) == [(0, 0, 1, 1), (0, 2, 1, 3)]


def test_valid_hole_cells_are_excluded_from_graded_maps():
    case = numeric_case('hl', ('A', 'B'), cells=((0, 0), (0, 1), (0, 2)), shape=(1, 3), valid=[[1, 0, 1]])
    maps, gm = graded([case], {'hl': 'High'}, k=2)
    assert gm.layers[0].count_high.tolist() == [[2, 0, 2]] and gm.layers[0].grade_max.tolist() == [[3, 0, 3]]
    assert sorted(r.bbox_yx for r in maps.layers[0].regions) == [(0, 0, 1, 1), (0, 2, 1, 3)]


def test_same_position_other_layer_and_other_source_stay_separate_keys():
    cases = [numeric_case('l1', layer='L1'), numeric_case('l2', ('A', 'B'), layer='L2'), numeric_case('s2', ('A', 'B'), step='other')]
    maps, gm = graded(cases, {'l1': 'High', 'l2': 'High', 's2': 'High'})
    assert len(maps.layers) == len(gm.layers) == 3
    assert all(layer.count_high.tolist() == [[2, 0]] for layer in gm.layers)
    assert [g.key for g in gm.layers] == [m.key for m in maps.layers]


def test_graded_region_ids_are_stable_and_differ_from_plain_ids():
    cases = [numeric_case('AB'), numeric_case('AC', ('A', 'C'))]
    grades = {'AB': 'High', 'AC': 'High'}
    first, _ = graded(cases, grades, k=3)
    second, _ = graded(list(reversed(cases)), grades, k=3)
    plain = compute_electrode_maps(cases, min_count=3).layers[0].regions[0]
    ids = [r.region_id for r in first.layers[0].regions]
    assert ids == [r.region_id for r in second.layers[0].regions] and ids and ids[0] != plain.region_id
    low, _ = graded(cases, grades, k=3, select='low_or_high')
    assert [r.region_id for r in low.layers[0].regions] != ids


def test_graded_requires_valid_arguments():
    from simcompare.electrode_map import compute_graded_electrode_maps
    with pytest.raises(NumericContractError, match='invalid_count_threshold'):
        compute_graded_electrode_maps([numeric_case('AB')], lambda case: 'High', min_count=0, roi_grade='high')
    with pytest.raises(NumericContractError, match='invalid_roi_grade'):
        compute_graded_electrode_maps([numeric_case('AB')], lambda case: 'High', min_count=1, roi_grade='all')
    with pytest.raises(NumericContractError, match='invalid_grade'):
        compute_graded_electrode_maps([numeric_case('AB')], lambda case: 'Medium', min_count=1, roi_grade='high')


def test_graded_extra_bytes_are_counted_before_allocation():
    from simcompare.electrode_map import graded_extra_bytes
    assert graded_extra_bytes(cells=1000, layers=3) == 1000 * (5 * 3 + 4)
    assert graded_extra_bytes(cells=0, layers=3) == 0


def test_graded_reserved_bytes_reach_the_admission_guard_before_any_mask_is_loaded(monkeypatch):
    from simcompare.adapters import real_hotspot
    from simcompare.electrode_map import _key, compute_graded_electrode_maps, graded_extra_bytes
    dataset = load_dataset(Path(__file__).parent/'fixtures/numeric_v2')
    seen = []
    monkeypatch.setattr(real_hotspot, 'guard_stage_map', lambda stage, data, reserved=0: seen.append(reserved))
    compute_graded_electrode_maps(dataset, lambda case: 'High', min_count=2, roi_grade='high', reserved_bytes=100)
    cells = next(iter(dataset.frames.values())).valid.size
    assert seen == [100 + graded_extra_bytes(cells, len({_key(case) for case in dataset.cases}))] and seen[0] > 100
    seen.clear()
    compute_electrode_maps(dataset, min_count=2, reserved_bytes=100)
    assert seen == [100]       # 등급이 없는 경로는 전과 같은 값


def test_graded_missing_case_keeps_a_lower_bound_and_partial_coverage():
    lat = numeric_case('lat')
    missing = numeric_case('vert', ('A', 'C'), failure_type='vertical_short', status='missing')
    maps, gm = graded([lat, missing], {'lat': 'High', 'vert': 'High'}, k=2)
    assert gm.layers[0].count_high.tolist() == [[2, 0]] and maps.layers[0].coverage['source_coverage_status'] == 'partial'
    assert len(maps.layers[0].regions) == 1


def test_plain_compute_is_unchanged_by_the_graded_code_path():
    cases = [numeric_case('AB'), numeric_case('AC', ('A', 'C'))]
    graded_maps, _ = graded(cases, {'AB': 'High', 'AC': 'Low'}, k=3, select='low_or_high')
    plain = compute_electrode_maps(cases, min_count=3)
    assert graded_maps.layers[0].counts.tolist() == plain.layers[0].counts.tolist()
    assert [c.identity['case_id'] for c in graded_maps.layers[0].regions[0].cases] == [c.identity['case_id'] for c in plain.layers[0].regions[0].cases]


# ---- 바이트 불변(T3a): 변경 전 기준 commit 0145ff5 에서 고정한 sha256 ------------------------------------------
# 임시 경로와 producer_commit 은 정규화한다. npz 는 배열 바이트로 해시한다(zip 헤더 제외).
import hashlib as _hashlib
import os
import re as _re

BASELINE_SHAS = {
    'candidates_demo': {
        'candidate_queries_v1.json': 'e7189d0b3e726952baa9c38e2ddbd447bb91a121d372fb4a837701707a99d55e',
        'execution.json': '4e0bb545459934db1e9371c287177337b248939b68ff051ad61165a23986e482',
        'funnel.csv': '7838acbd417162e5176ba9c73dbb5e63219c20721357cebebdc5cebd61573ba1',
        'funnel.json': 'a22a13ae0fa68e80b290cd59abeffdb558feb28c980f8e9415c3b1140bf45324',
        'reference_export/review_rois.json': 'a565cafda66cbf1ee133fe80a61c345bcf198350bf57917ae8aa1eb88798b09a',
        'reference_index_export/review_rois.json': '758c52a9760d530b110d114a1bdeea949c6b779fa4454c028343fe578c52e5d8',
        'target_export/review_rois.json': '592d509f150d766674fd5859959be5a1ed8cf71313d8f5185fea2328569647c8',
        'target_index_export/review_rois.json': '1377962f773494666651b800aeea4b6a3716c80a54771b1daeda1ac3a5abf4bf'},
    'candidates_real': {
        'candidate_queries_v1.json': 'faae721dd78107d8cd963c8eabf6c1aa6e2d5b7461d1194873ac72129f88ab55',
        'execution.json': '71052d384dde5fe5017c55c4d15bfd341490603f31de1b9a3e28621c0bf3da62',
        'funnel.csv': '176a98c405f36d86b6ae4d7d9db01e52dd97622448683f4280ecb948d2f26528',
        'funnel.json': '69e4706b8f16d229f19817d807f550e28fd7bf994203ebf3e46caf136d1a4c78',
        'reference_export/review_rois.json': 'cfdc77608d71693c1ae3b82066a675ec1a95ab9e7c912ac0c791f1fb35707a34',
        'reference_index_export/review_rois.json': 'b6d61f6f9da6c80c7eaf0f7386f0886d6fe09f74a86f9ae8b9dc260264b9ecc6',
        'target_export/review_rois.json': '6f93edb1aea7683e172b1bafa5322ec625a0bcf5725628e3a69240e34ad1f781',
        'target_index_export/review_rois.json': 'a992325d6633fcac4f775acda5b4594ad0e9b3c519cb7d76693e073576c8dc92'},
    'electrode_map_demo': {
        'electrode_map.html': '8f91946f7c2113e36a943e602c66770e02a989b3954c33a13c349f2fc9d7a20d',
        'electrode_map.json': 'b128ad12c31c5a40d12b97441ba47e15366f69f36799a186a66f97ee9c5e9f65',
        'electrode_map_layer_dad86b4780ba65ff2f07.json': '93f10961d0251cfc241f02c921826655744013285650af68d77ecf7e48714469',
        'electrode_map_layer_dad86b4780ba65ff2f07.png': '7c3bbe1a80fc83690489e90892dce55581876ca745d7184905dc328e16d6c008',
        'electrode_map_layers_0.npz': '4d4003f8dcf0a2f01a7533bfdedf55a449089eca2d66743a320344c194735817',
        'electrode_map_summaries_0.npz': '4d4003f8dcf0a2f01a7533bfdedf55a449089eca2d66743a320344c194735817',
        'electrode_map_summary_16134bada04ff34214c3.json': 'ae9776e8288b3c7a061ea455ec4e86d900476eae597fc6e7317d385c156a8264',
        'electrode_map_summary_16134bada04ff34214c3.png': '7c3bbe1a80fc83690489e90892dce55581876ca745d7184905dc328e16d6c008',
        'execution.json': 'cc81e9aadeef10867ded97f800a8413cfb1a305d07f28c6fe7d31951500180ec'},
    'heatmap_default': {
        'counts.json': '7a6320edea8416c7b2361a4bfe65d7d89360ebe766011548fdade2ee1631550a',
        'counts.png': 'c685e71de8f91f1ffc37f151c07c1fc0bd06be9c9ed79885cc62b62995e78ade'},
}


def _snapshot(folder, tmp_path):
    shas = {}
    for path in sorted(Path(folder).rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        if path.suffix == '.npz':
            with np.load(path) as archive:
                digest = _hashlib.sha256()
                for name in sorted(archive.files):
                    digest.update(name.encode() + archive[name].tobytes() + str(archive[name].dtype).encode())
            shas[relative] = digest.hexdigest()
            continue
        data = path.read_bytes()
        if path.suffix in ('.json', '.csv', '.html'):
            data = data.replace(str(tmp_path).encode(), b'<TMP>')
            data = _re.sub(rb'"producer_commit": "[0-9a-f]{40}"', b'"producer_commit": "<COMMIT>"', data)
            # 임시 경로가 들어 있는 파일의 해시(export 파일·설정 파일): 그 파일은 따로 해시한다
            data = _re.sub(rb'"(export_sha256|index_export_sha256|config_sha256)": "[0-9a-f]{64}"', rb'"\1": "<SHA>"', data)
        shas[relative] = _hashlib.sha256(data).hexdigest()
    return shas


def _byte_identity_runs(tmp_path):
    from simcompare.cli import main
    demo = tmp_path / 'demo'
    assert main(['demo-data', '--out', str(demo)]) == 0
    runs = {}
    assert main(['electrode-map', '--config', str(demo / 'config.yaml'), '--min-count', '4', '--out', str(tmp_path / 'em')]) == 0
    runs['electrode_map_demo'] = _snapshot(tmp_path / 'em', tmp_path)
    assert main(['candidates', '--config', str(demo / 'config.yaml'), '--min-count', '4', '--window-um', '4',
                 '--merge-overlap', '0.5', '--out', str(tmp_path / 'cand')]) == 0
    runs['candidates_demo'] = _snapshot(tmp_path / 'cand', tmp_path)
    from test_candidates import real_config
    (tmp_path / 'realdir').mkdir()
    config = real_config(tmp_path / 'realdir')
    code = main(['candidates', '--config', str(config), '--min-count', '1', '--window-um', '10', '--merge-overlap', '0.5',
                 '--out', str(tmp_path / 'cand_real')])
    assert code == 0
    runs['candidates_real'] = _snapshot(tmp_path / 'cand_real', tmp_path)
    from simcompare.heatmap import write_heatmap
    from simcompare.numeric_schema import Frame
    frame = Frame('FRAME', .5, (100, 200), (2, 3), [[1, 1, 1], [1, 0, 1]])
    write_heatmap(tmp_path / 'heat', 'counts', np.array([[0, 2, 3], [1, 7, 4]]), frame, status='partial', reasons=['valid_coverage_partial'])
    runs['heatmap_default'] = _snapshot(tmp_path / 'heat', tmp_path)
    return runs


BASELINE_ENV = {'python': '3.11', 'PIL': '12.3.0', 'numpy': '2.4.6'}   # 해시를 고정한 환경: PNG·npz 바이트는 PIL·numpy 버전에 따라 다르다


def test_baseline_outputs_are_byte_identical(tmp_path):
    import sys
    import PIL
    here = {'python': '%d.%d' % sys.version_info[:2], 'PIL': PIL.__version__, 'numpy': np.__version__}
    if here != BASELINE_ENV:
        pytest.skip(f'baseline sha256 values were recorded with {BASELINE_ENV}, this environment is {here}')
    runs = _byte_identity_runs(tmp_path)
    if os.environ.get('PRINT_BASELINE'):
        print('BASELINE', json.dumps(runs, indent=1, sort_keys=True))
    assert runs == BASELINE_SHAS
