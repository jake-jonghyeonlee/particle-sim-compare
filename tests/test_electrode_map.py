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
    result = compute_electrode_maps(cases)
    assert result.layers[0].counts.tolist() == [[3, 0]]
    assert result.layers[1].counts.tolist() == [[2, 0]]
    assert result.summaries[0].counts.tolist() == [[3, 0]]  # max, 합 5도 전체 union 4도 아님
    assert all(layer.status == 'partial' for layer in result.layers)
    assert result.layers[0].coverage['source_coverage_status'] == 'available'
    assert result.provenance['G7b']['value'] is None
    assert result.provenance['defect_scope'] == 'short_only'
    assert result.provenance['domain'] == 'full_mask_valid'
    region = result.layers[0].regions[0]
    assert region.electrodes == ('A', 'B', 'C')
    assert len(region.cases) == 4
    assert region.review_roi_status == 'blocked'
    assert region.review_roi_reasons == ('map_scope_unconfirmed',)


def test_canonical_diagonal_cells_are_separate_and_ids_are_stable():
    cases = [numeric_case('AB', cells=((0, 0), (1, 1)), shape=(2, 2)),
             numeric_case('AC', ('A', 'C'), cells=((0, 0), (1, 1)), shape=(2, 2))]
    result = compute_electrode_maps(cases)
    regions = result.layers[0].regions
    assert len(regions) == 2
    assert [r.area_um2 for r in regions] == [1, 1]
    assert {r.bbox_yx for r in regions} == {(0, 0, 1, 1), (1, 1, 2, 2)}
    assert [r.region_id for r in regions] == sorted(r.region_id for r in regions)
    reversed_regions = compute_electrode_maps(reversed(cases)).layers[0].regions
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
    region = compute_electrode_maps([ab, ac]).layers[0].regions[0]
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
    assert zero.status == 'partial'  # 관측 0도 G7b는 pending


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
    region = compute_electrode_maps([lat, vert]).layers[0].regions[0]
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


def test_canonical_open_is_excluded_provisionally_without_changing_legacy_open():
    result = compute_electrode_maps([numeric_case('AB'), numeric_case('open', ('A',), failure_type='open')], min_count=2)
    assert result.layers[0].counts.tolist() == [[2, 0]]
    assert result.provenance['excluded_cases'][0]['failure_type'] == 'open'
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
