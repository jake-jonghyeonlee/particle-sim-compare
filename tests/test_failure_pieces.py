"""G7a 불량 유형 조각: 독립 set oracle와 수작업 면적/가중/panel 검산."""
from dataclasses import replace
import json
import random

import numpy as np
import pytest

from simcompare.linepixel import FAILURE_PIECES, compare_rates, compute_areas
from simcompare.numeric_schema import Frame
from test_density import density
from test_linepixel import group, make_case
from test_panel_comparison import layer, weighted


def piece_values(g):
    return {name:metric.value for name,metric in g.failure_pieces.items()}


def oracle(cases):
    lateral=set().union(*(cells for kind,cells in cases if kind=='lateral_short'))
    vertical=set().union(*(cells for kind,cells in cases if kind=='vertical_short'))
    return {'lateral_only':lateral-vertical,'vertical_only':vertical-lateral,
            'simultaneous':lateral & vertical},lateral | vertical


def test_hand_written_mapped_short_union_pieces_and_line_pixel_unchanged():
    cases=[make_case('lat_AB','A','B',{0,1,2},width=7),
           make_case('lat_AC','A','C',{2,3},width=7),
           make_case('vert_AD','A','D',{3,4},'pixel',width=7,failure_type='vertical_short'),
           make_case('vert_EF','E','F',{6},'pixel',width=7,failure_type='vertical_short')]
    g=group(compute_areas(cases))
    assert piece_values(g)=={'lateral_only':3,'vertical_only':2,'simultaneous':1}
    assert g.union.value==6 and sum(piece_values(g).values())==6
    assert g.line.area.value==4 and g.pixel.area.value==2 and g.transfer.value==1
    assert g.line.shared[0].area.value==1  # shared AB/AC cell 2, simultaneous cell 3
    assert g.failure_piece_masks['simultaneous'].tolist()==[[False,False,False,True,False,False,False]]
    assert all(metric.basis=='full_mask_valid_observed_sources_failure_type_union' for metric in g.failure_pieces.values())
    assert all(metric.coverage==g.union.coverage for metric in g.failure_pieces.values())
    assert all(not mask.flags.writeable for mask in g.failure_piece_masks.values())


def test_same_pair_lateral_and_vertical_one_cell_is_simultaneous_not_shared():
    a=make_case('lat','A','B',{0},width=1)
    b=make_case('vert','B','A',{0},width=1,failure_type='vertical_short')
    g=group(compute_areas([a,b]))
    assert piece_values(g)=={'lateral_only':0,'vertical_only':0,'simultaneous':1}
    assert g.union.value==1 and not g.line.shared
    assert g.line.exclusive[0].area.value==1
    assert {case.failure_type for case in g.source_cases}=={'lateral_short','vertical_short'}
    assert group(compute_areas([b,a])).to_dict()==g.to_dict()


def test_below_height_na_is_excluded_while_missing_vertical_makes_pieces_partial():
    a=make_case('lat','A','B',{0,1},diameter=1)
    b=make_case('vert','A','C',{2},failure_type='vertical_short')
    g=group(compute_areas([a,b]),diameter=1)
    assert piece_values(g)=={'lateral_only':2,'vertical_only':None,'simultaneous':None}
    assert g.failure_pieces['lateral_only'].status=='available'
    assert all(g.failure_pieces[n].status=='not_applicable' for n in ('vertical_only','simultaneous'))
    assert g.coverage.not_applicable==1
    b.records[1]=replace(b.records[1],input_status='missing',mask=None,reasons=('source_input_missing',))
    g=group(compute_areas([a,b]),diameter=1)
    assert all(m.status=='partial' and m.value is None for m in g.failure_pieces.values())
    assert {name:m.partial_value for name,m in g.failure_pieces.items()}=={'lateral_only':2,'vertical_only':0,'simultaneous':0}
    assert g.coverage.not_applicable==0 and g.coverage.missing==1


def test_two_diameter_weighted_sum_13_not_average_and_all_three_weighted_pieces():
    cases=[make_case('d5','A','B',{0,1}),make_case('d7','A','B',{0,1,2,3,4,5},diameter=7)]
    result=layer(weighted(cases,density({5:.5,7:2})))
    assert result.failure_pieces['lateral_only'].unit.value==13
    assert result.failure_pieces['lateral_only'].panel.value==1300
    assert result.failure_pieces['vertical_only'].unit.value==0
    assert result.failure_pieces['simultaneous'].unit.value==0
    cases=[make_case('lat5','A','B',{0,1}),
           make_case('vert5','A','C',{1,2},'pixel',failure_type='vertical_short'),
           make_case('lat7','A','B',{0,1,2},diameter=7),
           make_case('vert7','A','C',{2,3,4},'pixel',diameter=7,failure_type='vertical_short')]
    result=layer(weighted(cases,density({5:.5,7:2})))
    assert {n:p.unit.value for n,p in result.failure_pieces.items()}=={
        'lateral_only':4.5,'vertical_only':4.5,'simultaneous':2.5}
    for piece in result.failure_pieces.values():
        assert piece.unit.unit=='1' and piece.unit.distribution_ids==('D',)
        assert piece.unit.area_basis['basis']=='one_unit'
        assert piece.panel.rounding['unit_count']==100


def test_each_product_panel_count_piece_delta_minus_50_and_gate_pending():
    ref=weighted([make_case('a','A','B',{0,1},product='ref')],product='ref',panel=(60,10))
    target=weighted([make_case('a','A','B',{0,1,2},product='target')],product='target',panel=(30,10))
    delta=next(iter(compare_rates(ref,target,reference_product_id='ref',target_product_id='target').values()))
    assert {n:m.value for n,m in delta.failure_pieces.items()}=={'lateral_only':-50,'vertical_only':0,'simultaneous':0}
    assert delta.line.value==-50 and delta.pixel.value==0 and delta.transfer.value==0
    assert delta.failure_pieces['lateral_only'].rounding['reference']['unit_count']==100
    assert delta.failure_pieces['lateral_only'].rounding['target']['unit_count']==50
    blocked=weighted([make_case('a','A','B',{0,1,2},product='target')],
                     density({5:1},synthetic=False,unit='count/glass'),basis=False,product='target')
    assert group(blocked.areas).failure_pieces['lateral_only'].value==3
    for piece in layer(blocked).failure_pieces.values():
        assert piece.unit.value is piece.panel.value is None
        assert {'density_contract_unconfirmed','unit_area_basis_unconfirmed'}<=set(piece.panel.reasons)
    delta=next(iter(compare_rates(ref,blocked,reference_product_id='ref',target_product_id='target').values()))
    assert all(m.value is None and m.status=='unavailable' for m in delta.failure_pieces.values())


def test_missing_density_and_unmapped_pairs_propagate_to_each_piece():
    cases=[make_case('a','A','B',{0,1})]
    result=weighted(cases,density({5:.5},missing=(7,)))
    assert all(p.unit.value is None and p.unit.status=='partial' for p in layer(result).failure_pieces.values())
    assert layer(result).failure_pieces['lateral_only'].unit.partial_value==1
    cases.append(make_case('unmapped','C','D',{2},kind=None,failure_type='vertical_short'))
    g=group(compute_areas(cases))
    assert g.failure_pieces['lateral_only'].partial_value==2
    assert g.failure_pieces['vertical_only'].partial_value==0  # only mapped shorts in U
    assert all(m.value is None and 'unmapped_pair' in m.reasons for m in g.failure_pieces.values())


def test_randomized_two_dimensional_set_oracle_and_union_conservation():
    rng=random.Random(730)
    for trial in range(20):
        cases,sets=[],[]
        for index in range(6):
            kind='lateral_short' if index%2==0 else 'vertical_short'
            cells={i for i in range(8) if rng.randrange(3)==0}
            case=make_case(str(index),f'A{index}',f'B{index}',cells,
                           'line' if index%3 else 'pixel',width=8,pitch=.5,failure_type=kind)
            old_frame=case.records[5].frame
            frame=Frame(old_frame.frame_id,.5,(0,0),(2,4),np.ones((2,4),bool))
            case.records={d:replace(r,frame=frame,mask=None if r.mask is None else r.mask.reshape(2,4)) for d,r in case.records.items()}
            cases.append(case);sets.append((kind,cells))
        expected,union=oracle(sets)
        g=group(compute_areas(cases,chunk_cells=3))
        assert {n:m.value for n,m in g.failure_pieces.items()}=={n:len(cells)*.25 for n,cells in expected.items()}
        assert sum(m.value for m in g.failure_pieces.values())==g.union.value==len(union)*.25
        assert not any((g.failure_piece_masks[a] & g.failure_piece_masks[b]).any()
                       for a,b in (('lateral_only','vertical_only'),('lateral_only','simultaneous'),('vertical_only','simultaneous')))
        assert json.dumps(g.to_dict(),sort_keys=True)==json.dumps(group(compute_areas(reversed(cases))).to_dict(),sort_keys=True)


def test_report_has_distinct_piece_table_and_keeps_line_pixel_bucket_oracle(tmp_path):
    from simcompare.report import comparison_report,write_numeric_report
    cases=[make_case('lat_AB','A','B',{0,1,2},width=7),
           make_case('lat_AC','A','C',{2,3},width=7),
           make_case('vert_AD','A','D',{3,4},'pixel',width=7,failure_type='vertical_short'),
           make_case('vert_EF','E','F',{6},'pixel',width=7,failure_type='vertical_short')]
    result=weighted(cases,density({5:1}),panel=(70,10))
    delta=compare_rates(result,result,reference_product_id='product',target_product_id='product')
    payload,tables=comparison_report(result,result,delta)
    assert 'failure_pieces' in tables
    area_rows=[r for r in tables['failure_pieces'] if r['side']=='reference' and r['domain']=='full_mask' and r['diameter_um']==5]
    assert {r['piece']:r['value'] for r in area_rows}=={'lateral_only':3,'vertical_only':2,'simultaneous':1}
    assert all(r['axis']=='failure_type' for r in area_rows)
    values={r['class']:r['value'] for r in tables['summary'] if r['side']=='reference' and r['domain']=='panel'}
    assert values=={'line':400,'pixel':200,'transfer':100}
    assert payload['reference']['areas']['groups'][4]['line']['shared'][0]['kind']=='shared'
    write_numeric_report(tmp_path,'comparison',payload,tables)
    assert (tmp_path/'comparison_failure_pieces.csv').exists()
    html=(tmp_path/'comparison.html').read_text()
    assert '<h2>불량 유형 조각</h2><table>' in html and 'line / pixel과 다른 축' in html


def test_partial_source_blocks_delta_and_known_sums_remain_separate():
    a=make_case('lat','A','B',{0,1})
    b=make_case('vert','A','C',{1,2},failure_type='vertical_short')
    ref=weighted([a,b],density({5:1}))
    b.records[5]=replace(b.records[5],input_status='missing',mask=None,reasons=('source_input_missing',))
    target=weighted([a,b],density({5:1}))
    assert layer(target).failure_pieces['lateral_only'].unit.value is None
    assert layer(target).failure_pieces['lateral_only'].unit.partial_value==2
    delta=next(iter(compare_rates(ref,target,reference_product_id='product',target_product_id='product').values()))
    assert all(m.value is None and m.status=='unavailable' for m in delta.failure_pieces.values())
    assert delta.failure_pieces['lateral_only'].partial_value is None
    assert delta.failure_pieces['lateral_only'].coverage[1]['partial_value']==200


def test_process_conditions_and_diameters_do_not_create_cross_group_simultaneous():
    lat=make_case('lat','A','B',{0},step='STEP1')
    vert=make_case('vert','A','C',{0},step='STEP2',failure_type='vertical_short')
    result=compute_areas([lat,vert])
    assert piece_values(group(result,process_step_id='STEP1'))=={'lateral_only':1,'vertical_only':0,'simultaneous':0}
    assert piece_values(group(result,process_step_id='STEP2'))=={'lateral_only':0,'vertical_only':1,'simultaneous':0}
    vert=make_case('vert','A','C',{0},diameter=7,failure_type='vertical_short')
    result=compute_areas([lat,replace(vert,identity=replace(vert.identity,process_step_id='STEP1'),
        records={d:replace(r,identity=replace(vert.identity,process_step_id='STEP1')) for d,r in vert.records.items()})])
    assert piece_values(group(result,diameter=5))=={'lateral_only':1,'vertical_only':0,'simultaneous':0}
    assert piece_values(group(result,diameter=7))=={'lateral_only':0,'vertical_only':1,'simultaneous':0}


def test_valid_hole_is_partial_and_all_na_remains_not_applicable():
    a=make_case('lat','A','B',{0,1},width=2,pitch=.5,valid=[[1,0]])
    g=group(compute_areas([a]))
    assert g.failure_pieces['lateral_only'].value is None
    assert g.failure_pieces['lateral_only'].partial_value==.25
    assert all(m.status=='partial' for m in g.failure_pieces.values())
    assert sum(m.partial_value for m in g.failure_pieces.values())==g.union.partial_value==.25
    b=make_case('vert','A','B',{0},failure_type='vertical_short')
    g=group(compute_areas([b]),diameter=1)
    assert all(m.value is None and m.status=='not_applicable' and m.partial_value is None for m in g.failure_pieces.values())
