"""Independent set oracles for canonical full-mask area and pair membership."""
from dataclasses import replace
import itertools
import json
from pathlib import Path

import numpy as np
import pytest

from simcompare.adapters.numeric import load_dataset
from simcompare.linepixel import bucket_key, compute_areas, pair_of
from simcompare.numeric_schema import (
    DIAMETERS_UM, CaseIdentity, Endpoint, Frame, MaskRecord, NumericCase,
    NumericContractError, SourceCA,
)


def make_case(case_id, a, b, columns, kind='line', *, diameter=5, width=6,
              pitch=1, origin=(0,0), step='step', exposure='exposure', source='source',
              product='product', layer='M2', valid=None, failure_type='lateral_short',
              pair_id=None, frame_id='frame'):
    frame=Frame(frame_id,pitch,origin,(1,width),[[1]*width] if valid is None else valid)
    identity=CaseIdentity(product,'design',source,step,exposure,case_id,frame_id,'synthetic_v2')
    endpoints=(Endpoint(a,'gate','M1' if failure_type=='vertical_short' else layer),Endpoint(b,'data',layer))
    pair_id=pair_id or '-'.join(sorted((a,b)))
    raw={'case_id':case_id,'fab_pair_key':f'RAW:{a}-{b}',
         'assigned_process_layer':step,'ordered_endpoints':[vars(ep) for ep in endpoints]}
    records={}; metadata={'unit_id':'unit','height_um':3 if failure_type=='vertical_short' else None,
                         'height_evidence':'synthetic height' if failure_type=='vertical_short' else None,'records':[]}
    for d in DIAMETERS_UM:
        mask=np.zeros((1,width),bool)
        if d==diameter:mask[0,list(columns)]=True
        na=failure_type=='vertical_short' and d<3
        status='not_applicable' if na else 'observed'
        reasons=('below_height_vertical_short',) if na else ()
        records[d]=MaskRecord(d,frame,None if na else mask,status,reasons,identity=identity,analysis_layer=layer)
        metadata['records'].append({'diameter_um':d,'applicability_evidence':{
            'height_um':3,'comparison':'diameter_um < height_um','source':'synthetic'} if na else None})
    return NumericCase(identity,endpoints,failure_type,pair_id if kind else None,kind,layer,
                       'density-'+layer,records,raw,metadata)


def group(result,diameter=5,**fields):
    matches=[v for k,v in result.groups.items() if k.diameter_um==diameter and all(getattr(k,n)==v for n,v in fields.items())]
    assert len(matches)==1
    return matches[0]


def values(share):
    return {b.pair_ids:b.area.observed_area_um2 for b in (*share.exclusive,*share.shared)}


def set_oracle(pair_sets):
    """Pure Python sets, independent from the production mask/bitset operations."""
    line_sets={p:s for p,(kind,s) in pair_sets.items() if kind=='line'}
    pixel_sets={p:s for p,(kind,s) in pair_sets.items() if kind=='pixel'}
    line=set().union(*line_sets.values())
    pixel_raw=set().union(*pixel_sets.values())
    effective=pixel_raw-line
    transfer=pixel_raw & line
    union=line | pixel_raw
    buckets={}
    for kind,sets,keep in [('line',line_sets,line),('pixel',pixel_sets,effective)]:
        counts={}
        for cell in keep:
            members=tuple(sorted(p for p,s in sets.items() if cell in s))
            counts[members]=counts.get(members,0)+1
        buckets[kind]=counts
    return line,effective,transfer,union,buckets


def oracle_cases():
    return [make_case('lat_AB','A','B',{0,1,2}),make_case('lat_AC','A','C',{2,3}),
            make_case('lat_DE','D','E',{1,3,4},'pixel'),make_case('lat_EF','E','F',{4,5},'pixel')]


def test_priority_union_and_shares_hand_written_counterexample():
    g=group(compute_areas(oracle_cases(),chunk_cells=2))
    assert g.line.area.value==4 and g.pixel.area.value==2
    assert g.transfer.value==2 and g.union.value==6
    assert values(g.line)=={('A-B',):2,('A-C',):1,('A-B','A-C'):1}
    assert values(g.pixel)=={('E-F',):1,('D-E','E-F'):1}
    assert g.union.value==g.line.area.value+g.pixel.area.value
    for share in (g.line,g.pixel):
        assert sum(b.area.value for b in share.exclusive)+sum(b.area.value for b in share.shared)==share.area.value
    assert [s.measurement.ca_um2 for s in g.source_cases]==[3,2,3,2]


@pytest.mark.parametrize('chunk',[1,2,3,65536])
def test_membership_matches_independent_set_oracle_and_permutation(chunk):
    expected=set_oracle({'A-B':('line',{0,1,2}),'A-C':('line',{2,3}),
                         'D-E':('pixel',{1,3,4}),'E-F':('pixel',{4,5})})
    cases=oracle_cases()
    baseline=group(compute_areas(cases,chunk_cells=chunk)).to_dict()
    for order in itertools.permutations(cases):
        g=group(compute_areas(order,chunk_cells=chunk))
        assert [g.line.area.value,g.pixel.area.value,g.transfer.value,g.union.value]==[len(s) for s in expected[:4]]
        assert values(g.line)==expected[4]['line'] and values(g.pixel)==expected[4]['pixel']
        assert g.to_dict()==baseline


def test_same_pair_union_keeps_both_source_failure_types_and_ca():
    a=make_case('lat','A','B',{0,1,2})
    b=make_case('vert','A','B',{2,3,4},failure_type='vertical_short')
    g=group(compute_areas([a,b]))
    assert g.line.area.value==5
    assert values(g.line)=={('A-B',):5} and not g.line.shared
    assert {s.failure_type for s in g.source_cases}=={'lateral_short','vertical_short'}
    assert [s.measurement.ca_um2 for s in g.source_cases]==[3,3]
    assert len(g.line.exclusive[0].source_case_keys)==2
    assert g.source_cases[1].endpoints[0].actual_layer=='M1'
    assert g.pair_membership=={'A-B':('A','B')}


def test_loader_coverage_same_pair_lateral_vertical_raw_identity_roundtrip():
    path=Path(__file__).parent/'fixtures/numeric_v2'
    data=load_dataset(path)
    result=compute_areas(data)
    g=group(result)
    assert g.line.area.value==2 and g.pixel.area.value==0 and g.union.value==2
    assert g.coverage.expected==3 and g.coverage.observed==3
    assert len(g.source_cases)==3 and len(g.line.exclusive)==1
    assert {s.failure_type for s in g.source_cases}=={'lateral_short','vertical_short'}
    assert {s.raw['fab_pair_key'] for s in g.source_cases}=={'EXAMPLE_UNIT:M2-M2','EXAMPLE_UNIT:M1-M2','EXAMPLE_UNIT:M2-M1'}
    assert result.input_hash==data.provenance['input_hash']
    assert result.pair_table_hash==data.provenance['pair_table_hash']
    assert result.pair_table_version=='EXAMPLE_pairs_v2_1'
    missing=group(result,diameter=40)
    assert missing.coverage.missing==1 and missing.union.status=='partial'
    below=group(result,diameter=1)
    assert below.coverage.not_applicable==2 and below.union.status=='available'
    assert below.line.area.value==0
    assert result.gates['G7a']['status']=='pending'


@pytest.mark.parametrize('pair_count',[63,65,129])
def test_63_pairs_shared_membership_single_cell_without_pair_limit(pair_count):
    cases=[make_case(f'case-{i:03}',f'A{i:03}',f'B{i:03}',{0},width=1) for i in range(pair_count)]
    g=group(compute_areas(cases,chunk_cells=1))
    assert g.union.value==1 and g.line.area.value==1
    assert not g.line.exclusive and len(g.line.shared)==1
    bucket=g.line.shared[0]
    assert bucket.area.value==1 and len(bucket.pair_ids)==pair_count and len(bucket.source_case_keys)==pair_count
    assert group(compute_areas(reversed(cases),chunk_cells=1)).to_dict()==g.to_dict()


def test_three_way_shared_membership_is_one_unsplit_bucket():
    cases=[make_case('a','A','B',{0,1}),make_case('b','A','C',{1,2}),make_case('c','A','D',{1,3})]
    g=group(compute_areas(cases))
    oracle=set_oracle({'A-B':('line',{0,1}),'A-C':('line',{1,2}),'A-D':('line',{1,3})})
    assert g.line.area.value==4
    assert values(g.line)==oracle[4]['line']=={('A-B',):1,('A-C',):1,('A-D',):1,('A-B','A-C','A-D'):1}
    assert len(g.line.shared)==1 and g.line.shared[0].area.value==1


def test_union_conditions_and_diameters_remain_separate():
    base=make_case('a','A','B',{0,1},diameter=5,pitch=0.5)
    d7=make_case('b','A','B',{0,1,2,3},diameter=7,pitch=0.5)
    other_step=make_case('c','A','B',{0},step='step2',pitch=0.5)
    other_source=make_case('d','A','B',{0,1,2},source='source2',pitch=0.5)
    result=compute_areas([base,d7,other_step,other_source])
    assert len(result.groups)==96
    assert group(result,5,process_step_id='step',source_id='source').union.value==0.5
    assert group(result,7,process_step_id='step',source_id='source').union.value==1
    assert group(result,5,process_step_id='step2',source_id='source').union.value==0.25
    assert group(result,5,process_step_id='step',source_id='source2').union.value==0.75


@pytest.mark.parametrize('field,value',[('exposure','exposure2'),('product','product2'),('layer','M3')])
def test_union_product_layer_and_exposure_are_not_collapsed(field,value):
    result=compute_areas([make_case('a','A','B',{0}),make_case('b','A','B',{0,1},**{field:value})])
    assert len(result.groups)==64
    assert sorted(g.union.value for k,g in result.groups.items() if k.diameter_um==5)==[1,2]


def test_shared_serialisation_stable_keys_not_failure_fragment_metrics():
    g=group(compute_areas(oracle_cases()))
    document=json.loads(json.dumps(g.to_dict(),ensure_ascii=False))
    shared=document['line']['shared'][0]
    assert shared['kind']=='shared' and shared['pair_ids']==['A-B','A-C']
    assert shared['area']['value']==1
    assert shared['bucket_id']==bucket_key(g.key,'line',['A-C','A-B'])
    assert shared['bucket_id']!=bucket_key(g.key,'pixel',['A-B','A-C'])
    assert 'simultaneous' not in json.dumps({k:document[k] for k in ('line','pixel','transfer')})
    assert document['failure_pieces']['simultaneous']['value']==0
    assert document['failure_fragment_policy']=={'status':'implemented','value':['lateral_only','vertical_only','simultaneous'],
                                                 'decided_on':'2026-10-08','axis':'failure_type','reasons':[]}
    assert len({b.bucket_id for share in (g.line,g.pixel) for b in (*share.exclusive,*share.shared)})==5


def test_coverage_missing_vertical_stays_missing_without_height_inference():
    a=make_case('a','A','B',{0})
    b=make_case('b','A','B',{0,1},failure_type='vertical_short')
    # Missing d5 remains missing; a lack of height never becomes N/A.
    b.records[5]=replace(b.records[5],mask=None,input_status='missing',reasons=('missing_input',))
    b.metadata['height_evidence']=None
    # Other known N/A records still need their evidence; make them explicit missing too.
    for d in [1,2]:b.records[d]=replace(b.records[d],input_status='missing',reasons=('missing_input',))
    g=group(compute_areas([a,b]))
    assert g.union.status==g.line.area.status==g.pixel.area.status=='partial'
    assert g.union.value is None and g.union.partial_value==1 and g.coverage.missing==1 and g.coverage.not_applicable==0
    assert g.source_cases[1].input_status=='missing'
    assert g.source_cases[1].measurement.ca_um2 is None


def test_coverage_unmapped_and_missing_only_are_not_available_zero():
    known=make_case('a','A','B',{0})
    unknown=make_case('b','C','D',{1,2},kind=None)
    g=group(compute_areas([known,unknown]))
    assert g.union.status=='partial' and g.union.value is None and g.union.partial_value==1 and g.coverage.unmapped==1
    assert g.source_cases[1].measurement.ca_um2==2
    unknown_only=group(compute_areas([unknown]))
    assert unknown_only.union.status=='unavailable' and unknown_only.union.value is None
    missing=make_case('c','A','B',set())
    missing.records[5]=replace(missing.records[5],mask=None,input_status='missing',reasons=('missing_input',))
    unavailable=group(compute_areas([missing]))
    assert unavailable.union.status=='unavailable' and unavailable.union.value is None
    assert unavailable.union.observed_area_um2==0
    zero=group(compute_areas([make_case('zero','A','B',set())]))
    assert zero.union.status=='available' and zero.union.value==0


def test_coverage_valid_holes_are_not_observed_zero():
    g=group(compute_areas([make_case('a','A','B',{0,1},valid=[[1,0,1,1,1,1]])]))
    assert g.line.area.value is None and g.line.area.partial_value==1 and g.line.area.status=='partial'
    assert g.coverage.observed_cells==5 and g.coverage.expected_cells==6


def test_coverage_conservation_failure_does_not_enter_union():
    bad=make_case('a','A','B',{0})
    r=bad.records[5]
    bad.records[5]=replace(r,source_ca=SourceCA(2,r.frame.frame_id,r.frame.valid_hash))
    g=group(compute_areas([bad]))
    assert g.union.status=='unavailable' and g.union.value is None
    assert g.coverage.rejected==1
    assert g.source_cases[0].measurement.residual_um2==-1
    assert 'conservation_failed' in g.union.reasons


@pytest.mark.parametrize('override',[dict(origin=(1,0)),dict(pitch=0.5),dict(valid=[[1,0,1,1,1,1]])])
def test_union_rejects_incompatible_origin_pitch_or_valid(override):
    with pytest.raises(NumericContractError,match='incompatible_source_frame'):
        compute_areas([make_case('a','A','B',{0}),make_case('b','A','C',{1},**override)])


def test_coverage_not_applicable_needs_source_evidence():
    vertical=make_case('v','A','B',{0},failure_type='vertical_short')
    assert group(compute_areas([vertical]),1).union.status=='not_applicable'
    vertical.metadata['height_evidence']=None
    with pytest.raises(NumericContractError,match='not_applicable_evidence_required'):
        compute_areas([vertical])


def test_coverage_duplicate_source_case_or_missing_diameter_rejected():
    case=make_case('a','A','B',{0})
    with pytest.raises(NumericContractError,match='duplicate_case_identity'):
        compute_areas([case,case])
    case.records.pop(50)
    with pytest.raises(NumericContractError,match='incomplete_case_coverage'):
        compute_areas([case])


@pytest.mark.parametrize('chunk',[0,-1,True,0.5])
def test_membership_invalid_chunk_sizes(chunk):
    with pytest.raises(NumericContractError,match='invalid_membership_chunk_size'):
        compute_areas(oracle_cases(),chunk_cells=chunk)


def test_pair_table_collision_and_raw_ids_not_rewritten():
    with pytest.raises(NumericContractError,match='pair_id_collision'):
        compute_areas([make_case('a','A','B',{0},pair_id='same'),make_case('b','C','D',{1},pair_id='same')])
    assert pair_of(' A:α ','B-β')==frozenset(['B-β',' A:α '])
    with pytest.raises(NumericContractError):pair_of('','B')


def test_area_path_still_does_not_accept_a_unit_divisor():
    with pytest.raises(TypeError):compute_areas(oracle_cases(),units_in_mask=4)


def test_duplicate_pair_masks_union_once_with_two_case_links():
    a=make_case('a','A','B',{0,1})
    b=make_case('b','B','A',{0,1})
    g=group(compute_areas([a,b]))
    assert g.union.value==2 and len(g.line.exclusive)==1 and not g.line.shared
    assert len(g.line.exclusive[0].source_case_keys)==2
    assert [s.measurement.ca_um2 for s in g.source_cases]==[2,2]


def test_membership_conflicting_pair_ids_or_classes_rejected():
    for b in [make_case('b','B','A',{0},pair_id='different'),
              make_case('b','B','A',{0},kind='pixel')]:
        with pytest.raises(NumericContractError,match='conflicting_pair_definition'):
            compute_areas([make_case('a','A','B',{0}),b])


def test_coverage_expected_case_absence_in_dataset_is_not_zero():
    dataset=load_dataset(Path(__file__).parent/'fixtures/numeric_v2')
    dataset.cases.pop(0)
    with pytest.raises(NumericContractError,match='expected_case_coverage_mismatch'):
        compute_areas(dataset)


def test_coverage_converted_raw_diameter_na_evidence_is_preserved():
    case=make_case('v','A','B',{0},failure_type='vertical_short')
    raw_record=case.metadata['records'][0]
    del raw_record['diameter_um']
    raw_record.update(diameter=1000,diameter_unit='nm')
    g=group(compute_areas([case]),1)
    assert g.union.status=='not_applicable'
    assert g.source_cases[0].applicability_evidence['source']=='synthetic'


def test_membership_two_dimensional_multi_row_hand_written_oracle():
    cases=oracle_cases()
    for case in cases:
        frame=Frame('frame',.5,(0,0),(2,3),[[1,1,1],[1,1,1]])
        case.records={d:replace(r,frame=frame,mask=r.mask.reshape(2,3)) for d,r in case.records.items()}
    g=group(compute_areas(cases,chunk_cells=2))
    assert (g.line.area.value,g.pixel.area.value,g.transfer.value,g.union.value)==(1,.5,.5,1.5)
    assert values(g.line)=={('A-B',):.5,('A-C',):.25,('A-B','A-C'):.25}
    assert values(g.pixel)=={('E-F',):.25,('D-E','E-F'):.25}
    assert g.line_mask.tolist()==[[True,True,True],[True,False,False]]
    assert g.pixel_effective_mask.tolist()==[[False,False,False],[False,True,True]]
    assert g.transfer_mask.tolist()==[[False,True,False],[True,False,False]]
    assert g.union.value==g.line.area.value+g.pixel.area.value
    for share in (g.line,g.pixel):
        assert sum(b.area.value for b in (*share.exclusive,*share.shared))==share.area.value


def test_bucket_contributors_are_separate_from_pair_lineage_at_diameter():
    a=make_case('active','A','B',{0,1})
    missing=make_case('missing','A','B',{0})
    missing.records[5]=replace(missing.records[5],mask=None,input_status='missing',reasons=('missing_input',))
    zero=make_case('zero','A','B',set())
    overlap=make_case('overlap','A','C',{1})
    g=group(compute_areas([a,missing,zero,overlap]))
    ids={s.source_key:s.identity.case_id for s in g.source_cases}
    exclusive=g.line.exclusive[0]
    shared=g.line.shared[0]
    assert {ids[k] for k in exclusive.pair_lineage_case_keys}=={'active','missing','zero'}
    assert {ids[k] for k in exclusive.contributing_source_case_keys}=={'active'}
    assert {ids[k] for k in shared.pair_lineage_case_keys}=={'active','missing','zero','overlap'}
    assert {ids[k] for k in shared.contributing_source_case_keys}=={'active','overlap'}
    assert exclusive.area.value is None and exclusive.area.partial_value==1
    assert shared.area.value is None and shared.area.partial_value==1


def test_partial_area_zero_and_unknown_are_distinct_null_complete_values():
    known=make_case('known','A','B',{0})
    unknown=make_case('unknown','C','D',{1},kind=None)
    g=group(compute_areas([known,unknown]))
    assert g.line.area.value is None and g.line.area.partial_value==1
    assert g.pixel.area.value is None and g.pixel.area.partial_value==0
    assert g.union.value is None and g.union.partial_value==1
    assert g.source_cases[1].measurement.ca_um2==1
