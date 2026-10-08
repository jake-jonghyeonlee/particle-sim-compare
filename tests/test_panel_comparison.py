from dataclasses import asdict,replace
import json
from pathlib import Path

import pytest

from simcompare.adapters.numeric import load_dataset
from simcompare.density import adapt_density
from simcompare.linepixel import (PanelSpec,UnitAreaBasis,compare_rates,compute_areas,compute_rates,unit_count)
from simcompare.numeric_schema import NumericContractError
from test_linepixel import make_case,oracle_cases
from test_density import density


def weighted(cases,dist=None, *, basis=True, product='product',panel=(60,10),rounding='floor'):
    areas=compute_areas(cases);areas.pair_table_version='synthetic_pairs_v1'
    frame=next(iter(areas.groups.values())).frame
    size=(frame.shape_yx[1]*frame.pixel_pitch_um,frame.shape_yx[0]*frame.pixel_pitch_um)
    bases={} if not basis else {(product,frame.frame_id):UnitAreaBasis('one_unit',True,frame.frame_id,size,'explicit synthetic one-unit fixture')}
    specs={} if panel is None else {product:PanelSpec(panel,size,rounding=rounding)}
    dist=density({5:1}) if dist is None else dist
    return compute_rates(areas,{dist.process_layer:dist},unit_bases=bases,panels=specs)


def layer(result,name='M2'):
    values=[v for k,v in result.layers.items() if k.analysis_layer==name]
    assert len(values)==1
    return values[0]


def test_two_diameter_weighted_sum_13_not_average_5_2():
    cases=[make_case('d5','A','B',{0,1}),make_case('d7','A','B',{0,1,2,3,4,5},diameter=7)]
    r=layer(weighted(cases,density({5:0.5,7:2})))
    assert r.line.unit.value==13
    assert r.line.panel.value==1300
    assert r.pixel.unit.value==0
    assert r.line.unit.unit=='1' and r.line.unit.distribution_ids==('D',)
    assert r.line.unit.area_basis['basis']=='one_unit'
    assert r.line.panel.rounding['policy']=='floor' and r.line.panel.rounding['unit_count']==100


def test_line_pixel_and_shared_bucket_conservation_with_same_density():
    r=layer(weighted(oracle_cases(),density({5:0.5})))
    assert r.line.unit.value==2 and r.pixel.unit.value==1 and r.transfer_unit.value==1
    for share in [r.line,r.pixel]:
        assert sum(b.unit.value for b in share.exclusive)+sum(b.unit.value for b in share.shared)==share.unit.value
        assert sum(b.panel.value for b in share.exclusive)+sum(b.panel.value for b in share.shared)==share.panel.value
    assert r.line.shared[0].unit.value==0.5
    assert len(r.line.shared[0].pair_ids)==2


def test_panel_per_axis_rounding_half_up_not_bankers_or_floor_area_ratio():
    assert unit_count((2.5,3.5),(1,1))==6
    assert unit_count((2.5,3.5),(1,1),rounding='half_up_round')==12
    assert unit_count((2500,3500),(1000,1000),length_unit='nm')==6
    assert unit_count((1.999999999999,1),(1,1))==1
    assert unit_count((0.5,0.5),(1,1),rounding='half_up_round')==1


@pytest.mark.parametrize('panel,unit,length_unit',[((),(1,1),'um'),((1,),(1,1),'um'),((1,2,3),(1,1),'um'),
    ((1,1),(1,),'um'),((0,1),(1,1),'um'),((1,1),(-1,1),'um'),((float('nan'),1),(1,1),'um'),
    ((1,float('inf')),(1,1),'um'),((True,1),(1,1),'um'),((1,1),(1,1),'inch')])
def test_panel_dimension_length_finite_positive_and_unit_checks(panel,unit,length_unit):
    with pytest.raises(NumericContractError):PanelSpec(panel,unit,length_unit)


def test_panel_rounding_choice_recorded_and_invalid_choice_rejected():
    assert PanelSpec((2.5,3.5),(1,1),rounding='half_up_round').metadata['unit_count']==12
    with pytest.raises(NumericContractError):PanelSpec((1,1),(1,1),rounding='round')


def test_each_product_own_panel_count_delta_sign_minus_50():
    reference=weighted([make_case('a','A','B',{0,1},product='ref')],product='ref',panel=(60,10))
    target=weighted([make_case('a','A','B',{0,1,2},product='target')],product='target',panel=(30,10))
    result=next(iter(compare_rates(reference,target,reference_product_id='ref',target_product_id='target').values()))
    assert layer(reference).line.unit.value==2 and layer(reference).line.panel.value==200
    assert layer(target).line.unit.value==3 and layer(target).line.panel.value==150
    assert result.line.value==-50 and result.pixel.value==0
    assert result.line.rounding['reference']['unit_count']==100 and result.line.rounding['target']['unit_count']==50
    assert result.line.distribution_ids==('D',) and result.line.area_basis['reference']['basis']=='one_unit'
    assert sum(v.value for v in result.line_exclusive.values())+sum(v.value for v in result.line_shared.values())==-50


def test_delta_bucket_union_and_transfer_net_change_without_spatial_claim():
    ref=weighted(oracle_cases(),panel=(60,10))
    target_cases=[make_case('a','A','B',{0,1}),make_case('b','A','C',{1,2}),
                  make_case('c','D','E',{0,3,4},'pixel'),make_case('d','E','F',{4,5},'pixel')]
    target=weighted(target_cases,panel=(60,10))
    delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
    assert delta.line.value==-100 and delta.pixel.value==100 and delta.transfer.value==-100
    assert sum(m.value for m in delta.line_exclusive.values())+sum(m.value for m in delta.line_shared.values())==delta.line.value
    assert sum(m.value for m in delta.pixel_exclusive.values())+sum(m.value for m in delta.pixel_shared.values())==delta.pixel.value
    assert 'simultaneous' not in json.dumps(delta.to_dict())


def test_missing_density_even_at_zero_area_does_not_become_observed_zero():
    cases=[make_case('a','A','B',{0,1})]
    complete=layer(weighted(cases,density({5:1})))
    partial=layer(weighted(cases,density({5:1},missing=(40,))))
    assert complete.line.unit.value==2
    assert partial.line.unit.value is None and partial.line.unit.partial_value==2
    assert partial.line.panel.value is None and partial.line.panel.partial_value==200
    assert partial.line.unit.status=='partial' and 'missing_density_input' in partial.line.unit.reasons


def test_pending_gates_preserve_areas_and_block_weighted_panel_and_delta():
    data=load_dataset(Path(__file__).parent/'fixtures/numeric_v2')
    areas=compute_areas(data)
    dist=adapt_density(data.density)
    pending=compute_rates(areas,{dist.process_layer:dist},unit_bases={})
    r=next(iter(pending.layers.values()))
    assert next(g for k,g in areas.groups.items() if k.diameter_um==5).union.value==2
    assert r.line.unit.value is None and r.line.panel.value is None
    assert 'density_contract_unconfirmed' in r.line.unit.reasons
    assert 'unit_area_basis_unconfirmed' in r.line.unit.reasons
    delta=next(iter(compare_rates(pending,pending,reference_product_id='EXAMPLE_PRODUCT',target_product_id='EXAMPLE_PRODUCT').values()))
    assert delta.line.value is None and delta.pixel.value is None and delta.transfer.value is None
    assert pending.areas.gates['G1']['answer'] is None and pending.areas.gates['G2']['answer'] is None


def test_two_by_two_not_divided_and_real_basis_not_authorised():
    cases=[make_case('a','A','B',{0,1,2,3})]
    areas=compute_areas(cases)
    bad=UnitAreaBasis('one_unit',True,'frame',(3,0.5),'explicit mismatched synthetic unit')
    r=compute_rates(areas,{'density-M2':density({5:1})},unit_bases={('product','frame'):bad})
    assert layer(r).line.unit.value is None and 'one_unit_domain_mismatch' in layer(r).line.unit.reasons
    r=weighted(cases,basis=False)
    assert layer(r).line.unit.value is None and r.areas.groups[next(k for k in r.areas.groups if k.diameter_um==5)].line.area.value==4
    real=replace(bad,is_synthetic=False,unit_size_um=(6,1))
    assert layer(compute_rates(areas,{'density-M2':density({5:1})},unit_bases={('product','frame'):real})).line.unit.value is None


def test_below_height_na_excludes_only_its_source_case():
    cases=[make_case('lat','A','B',{0},diameter=1),make_case('vert','A','B',{1},failure_type='vertical_short')]
    r=layer(weighted(cases,density({1:2,5:3})))
    assert r.line.unit.value==5
    only_vertical=layer(weighted([cases[1]],density({5:3},missing=(1,2))))
    assert only_vertical.line.unit.status=='available' and only_vertical.line.unit.value==3


def test_incomplete_and_unmapped_inputs_cannot_produce_complete_delta():
    complete=weighted([make_case('a','A','B',{0,1})])
    unknown=weighted([make_case('a','A','B',{0,1}),make_case('b','C','D',{2},kind=None)])
    delta=next(iter(compare_rates(complete,unknown,reference_product_id='product',target_product_id='product').values()))
    assert delta.line.value is None and 'unmapped_pair' in delta.line.reasons
    assert layer(unknown).line.unit.partial_value==2


def test_absent_layer_observed_vs_unknown_is_explicit():
    ref=weighted([make_case('r','A','B',{0})])
    extra=make_case('t','A','B',{0,1},layer='M3')
    areas=compute_areas([extra]);areas.pair_table_version='synthetic_pairs_v1'
    target=compute_rates(areas,{'density-M3':density({5:1},process='density-M3')},
        unit_bases={('product','frame'):UnitAreaBasis('one_unit',True,'frame',(6,1),'synthetic')},
        panels={'product':PanelSpec((60,10),(6,1))})
    key=layer(target,'M3').key.comparison_key
    unknown=compare_rates(ref,target,reference_product_id='product',target_product_id='product')
    assert unknown[key].line.value is None and 'reference_layer_unknown' in unknown[key].line.reasons
    observed=compare_rates(ref,target,reference_product_id='product',target_product_id='product',
                           reference_absent={key:'explicit full inventory: M3 absent'})
    assert observed[key].line.value==200
    assert observed[key].line.coverage[0]['observed_absence']=='explicit full inventory: M3 absent'


def test_different_density_distribution_comparison_not_structure_only():
    ref=weighted([make_case('a','A','B',{0})],density({5:1},distribution='D_ref'))
    target=weighted([make_case('b','A','B',{0})],density({5:2},distribution='D_target'))
    delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
    assert delta.line.value==100 and delta.line.distribution_ids==('D_ref','D_target')
    assert delta.comparison_basis=='different_or_absent_density_distributions'


def test_changed_values_under_same_density_id_are_not_structure_only():
    ref=weighted([make_case('a','A','B',{0})],density({5:1}))
    target=weighted([make_case('b','A','B',{0})],density({5:2}))
    delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
    assert delta.line.value==100
    assert delta.comparison_basis=='different_or_absent_density_distributions'


def test_unknown_or_conflicting_pair_table_versions_block_comparison():
    ref=weighted([make_case('a','A','B',{0})])
    target=weighted([make_case('b','A','B',{0,1})])
    for marker in [None,'other_pairs_v2']:
        target.areas.pair_table_version=marker
        delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
        assert delta.line.value is None
        assert any('pair_table_version' in reason for reason in delta.line.reasons)


def test_reused_pair_id_with_different_electrodes_blocks_join():
    ref=weighted([make_case('a','A','B',{0},pair_id='PAIR')])
    target=weighted([make_case('b','C','D',{0},pair_id='PAIR')])
    delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
    assert delta.line.value is None and 'pair_identity_mismatch' in delta.line.reasons


def test_observed_absent_bucket_retains_both_sides_basis_rounding_and_distribution():
    ref=weighted([make_case('a','A','B',{0})])
    target=weighted([make_case('a','A','B',{0}),make_case('b','A','C',{0,1})])
    delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
    shared=next(iter(delta.line_shared.values()))
    assert shared.value==100
    assert shared.area_basis['reference']['basis']=='one_unit'
    assert shared.rounding['reference']['unit_count']==100
    assert shared.distribution_ids==('D',)


def test_all_weighted_values_and_delta_values_have_metadata():
    result=weighted(oracle_cases())
    def check(value):
        if isinstance(value,dict):
            if 'value' in value and 'status' in value:
                assert value['unit']=='1'
                for key in ('distribution_ids','area_basis','rounding','coverage','reasons'):assert key in value
            for item in value.values():check(item)
        elif isinstance(value,(list,tuple)):
            for item in value:check(item)
    check(asdict(layer(result)))
    delta=next(iter(compare_rates(result,result,reference_product_id='product',target_product_id='product').values()))
    check(delta.to_dict())
    assert json.loads(json.dumps(result.to_dict()))['gates']=={}


def test_missing_panel_dimensions_do_not_expose_unit_value_as_partial_panel():
    result=layer(weighted([make_case('a','A','B',{0})],panel=None))
    assert result.line.unit.value==1
    assert result.line.panel.value is None and result.line.panel.partial_value is None
    assert result.line.panel.status=='unavailable'


def test_weighted_output_preserves_density_values_and_per_diameter_factors():
    result=weighted([make_case('a','A','B',{0,1})],density({5:0.5,40:3,50:4}))
    document=result.to_dict()
    dist=document['density_distributions']['density-M2']
    assert dist['bins'][4]['value_per_um2']==0.5
    assert dist['bins'][30]['value_per_um2']==3 and dist['bins'][31]['value_per_um2']==4
    bin5=next(c for c in layer(result).line.unit.coverage if c['diameter_um']==5)
    assert bin5['density_per_um2']==0.5 and bin5['distribution_id']=='D'


def test_weighting_includes_1_30_40_50_um_bins_without_interpolation():
    cases=[make_case('d1','A','B',{0},diameter=1),
           make_case('d30','A','B',{0,1},diameter=30),
           make_case('d40','A','B',{0,1,2},diameter=40),
           make_case('d50','A','B',{0,1,2,3},diameter=50)]
    result=layer(weighted(cases,density({1:1,30:2,40:3,50:4})))
    assert result.line.unit.value==30  # 1*1 + 2*2 + 3*3 + 4*4
    assert result.line.panel.value==3000
    assert result.pixel.unit.value==0
