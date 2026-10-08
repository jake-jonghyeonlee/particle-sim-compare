import numpy as np
import pytest

from simcompare.measurement import measure_full_mask, measure_union, measure_window, union_masks
from simcompare.numeric_schema import (
    CaseIdentity, Frame, MaskRecord, NumericContractError, PhysicalWindow, SourceCA,
)


def record(mask,valid=None,pitch=1,origin=(0,0),source_ca=None):
    shape=np.shape(mask)
    frame=Frame('f',pitch,origin,shape,np.ones(shape,bool) if valid is None else valid)
    return MaskRecord(5,frame,mask,source_ca=source_ca)


def test_original_full_area_pitch_half():
    source=record([[1,0],[0,1]],pitch=0.5)
    result=measure_full_mask(source)
    assert result.ca_um2==0.5 and result.valid_area_um2==1
    assert result.ca_per_valid_area==0.5 and result.status=='available'


def test_overlapping_half_open_windows_union_inside_one_cell():
    source=record([[1]])
    # Areas .75 and .75 overlap on .5: union 1, never sum 1.5.
    result=measure_union(source,[PhysicalWindow(0,0,0.75,1),PhysicalWindow(0.25,0,1,1)])
    assert result.ca_um2==1
    assert result.requested_area_um2==1 and result.valid_area_um2==1
    # Both axes overlap: .36 + .36 - .04 = .68.
    result=measure_union(source,[PhysicalWindow(0,0,0.6,0.6),PhysicalWindow(0.4,0.4,1,1)])
    assert result.ca_um2==pytest.approx(0.68)
    assert result.ca_per_valid_area==1


def test_window_uses_physical_origin_and_unit_conversion():
    source=record([[1,0]],pitch=0.5,origin=(10,20))
    result=measure_window(source,PhysicalWindow(10000,20000,10250,20500,'nm'))
    assert result.ca_um2==0.125
    assert result.valid_area_um2==0.125
    assert result.status=='available'


def test_unobserved_outside_and_observed_zero_both_have_zero_geometric_sum():
    source=record([[0]])
    outside=measure_window(source,PhysicalWindow(2,2,3,3))
    zero=measure_window(source,PhysicalWindow(0,0,1,1))
    assert outside.observed_ca_um2==zero.observed_ca_um2==0
    assert outside.ca_um2 is None and outside.status=='unavailable'
    assert outside.ca_per_valid_area is None
    assert zero.ca_um2==0 and zero.status=='available' and zero.ca_per_valid_area==0


def test_partial_outside_window_and_valid_hole_do_not_become_complete_zero():
    source=record([[1,1]],valid=[[1,0]])
    full=measure_full_mask(source)
    assert full.ca_um2 is None and full.partial_value==1 and full.valid_area_um2==1
    assert full.requested_area_um2==2 and full.status=='partial'
    hole=measure_window(source,PhysicalWindow(1,0,2,1))
    assert hole.status=='unavailable' and hole.ca_um2 is None
    partial=measure_window(source,PhysicalWindow(-1,0,1,1))
    assert partial.ca_um2 is None and partial.partial_value==1 and partial.status=='partial'


def test_missing_and_not_applicable_are_not_zero():
    frame=Frame('f',1,(0,0),(1,1),[[1]])
    missing=MaskRecord(5,frame,None,'missing',('missing_input',))
    na=MaskRecord(1,frame,None,'not_applicable',('below_height_vertical_short',))
    assert measure_full_mask(missing).status=='unavailable'
    assert measure_full_mask(na).status=='not_applicable'
    assert measure_full_mask(missing).ca_um2 is None and measure_full_mask(na).ca_um2 is None


def test_conservation_reports_residual_without_changing_original_mask():
    source=record([[1,0]],pitch=0.5)
    ca=SourceCA(0.5,'f',source.frame.valid_hash)
    original=source.mask.copy()
    source=MaskRecord(5,source.frame,source.mask,source_ca=ca)
    result=measure_full_mask(source)
    assert result.status=='unavailable' and result.reasons==('conservation_failed',)
    assert result.residual_um2==-0.25 and result.source_ca_um2==0.5
    assert np.array_equal(source.mask,original)
    correct=MaskRecord(5,source.frame,source.mask,source_ca=SourceCA(0.25,'f',source.frame.valid_hash))
    assert measure_full_mask(correct).residual_um2==0
    assert measure_full_mask(correct).status=='available'


def test_source_ca_must_refer_to_same_valid_domain():
    source=record([[1]])
    for ca in [SourceCA(1,'different',source.frame.valid_hash),SourceCA(1,'f','wrong_valid_hash')]:
        result=measure_full_mask(MaskRecord(5,source.frame,source.mask,source_ca=ca))
        assert result.status=='unavailable' and result.reasons==('source_ca_domain_mismatch',)


@pytest.mark.parametrize('pitch,origin',[(0.5,(0,0)),(1,(1,0))])
def test_same_shape_different_pitch_or_origin_cannot_union(pitch,origin):
    with pytest.raises(NumericContractError,match='incompatible_source_frame'):
        union_masks([record([[1]]),record([[1]],pitch=pitch,origin=origin)])


def test_union_preserves_cases_and_checks_source_conditions():
    frame=Frame('f',1,(0,0),(1,2),[[1,1]])
    a=CaseIdentity('p','v','source','step','exposure','a','f','v2')
    b=CaseIdentity('p','v','source','step','exposure','b','f','v2')
    first=MaskRecord(5,frame,[[1,0]],identity=a)
    second=MaskRecord(5,frame,[[1,1]],identity=b)
    assert union_masks([first,second]).tolist()==[[True,True]]
    assert first.identity.case_id=='a' and second.identity.case_id=='b'
    other=CaseIdentity('p','v','source','step','another exposure','b','f','v2')
    with pytest.raises(NumericContractError,match='incompatible_source_conditions'):
        union_masks([first,MaskRecord(5,frame,[[1,1]],identity=other)])


def test_unobserved_or_different_diameter_cannot_union():
    first=record([[1]])
    for second in [MaskRecord(6,first.frame,[[1]]),MaskRecord(5,first.frame,None,'missing',('missing_input',))]:
        with pytest.raises(NumericContractError,match='incompatible_union_record'):
            union_masks([first,second])


@pytest.mark.parametrize('kwargs',[dict(conservation_rtol=-1),dict(conservation_atol_um2=np.nan)])
def test_invalid_conservation_tolerances(kwargs):
    with pytest.raises(NumericContractError):
        measure_full_mask(record([[1]]),**kwargs)


def test_union_does_not_collapse_different_analysis_layers():
    source=record([[1]])
    a=MaskRecord(5,source.frame,source.mask,analysis_layer='M1')
    b=MaskRecord(5,source.frame,source.mask,analysis_layer='M2')
    with pytest.raises(NumericContractError,match='incompatible_analysis_layer'):
        union_masks([a,b])


def test_tiny_partial_window_is_not_promoted_by_absolute_area_tolerance():
    source=record([[1]])
    result=measure_window(source,PhysicalWindow(-1e-7,0,1e-7,1e-7))
    assert result.status=='partial'
    assert result.ca_um2 is None
    assert result.partial_value==pytest.approx(1e-14,rel=1e-12,abs=0)
    assert result.requested_area_um2==pytest.approx(2e-14,rel=1e-12,abs=0)


def test_partial_ca_and_outside_valid_cell_count_preserve_known_sum():
    result=measure_full_mask(record([[1,1,1],[0,1,0]],valid=[[1,0,1],[1,0,1]]))
    assert result.status=='partial' and result.ca_um2 is None
    assert result.partial_value==result.observed_ca_um2==2
    assert result.ca_per_valid_area is None and result.partial_ca_per_valid_area==.5
    assert 'active_cells_outside_valid:2' in result.reasons
