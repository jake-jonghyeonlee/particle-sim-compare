import numpy as np
import pytest

from simcompare.numeric_schema import (
    Frame, NumericContractError, NumericFrameError, Pair, PhysicalWindow,
    convert_area, convert_length, diameter_key, require_same_frame,
)


def test_units_and_diameter_identity():
    assert convert_length(500, 'nm') == 0.5
    assert convert_length(0.005, 'mm') == 5
    assert convert_area(500000, 'nm^2') == 0.5
    assert convert_area(0.000002, 'mm^2') == 2
    assert diameter_key(5000, 'nm') == diameter_key(5, 'um')
    assert diameter_key(700, 'nm') == diameter_key(0.7, 'um')
    assert diameter_key(0.7000000000001) != diameter_key(0.7)


@pytest.mark.parametrize('value,unit', [(0,'um'),(-1,'um'),(np.nan,'um'),(np.inf,'um'),(True,'um'),(1,'inch')])
def test_bad_diameter(value, unit):
    with pytest.raises(NumericContractError):
        diameter_key(value, unit)


@pytest.mark.parametrize('value,unit', [(1,'1'),(1,'count/glass'),(-1,'um^2'),(np.nan,'um^2'),(True,'um^2')])
def test_bad_area(value, unit):
    with pytest.raises(NumericContractError):
        convert_area(value, unit)


@pytest.mark.parametrize('override', [dict(pixel_pitch_um=0), dict(pixel_pitch_um=np.nan),
    dict(origin_xy_um=(0,np.inf)), dict(shape_yx=(2.0,2)), dict(shape_yx=(0,2)),
    dict(valid=np.ones((1,2))), dict(axis_order='xy'), dict(origin_convention='cell_center')])
def test_invalid_frame(override):
    kwargs=dict(frame_id='f',pixel_pitch_um=1,origin_xy_um=(0,0),shape_yx=(2,2),valid=np.ones((2,2),bool))
    kwargs.update(override)
    with pytest.raises(NumericContractError):
        Frame(**kwargs)


@pytest.mark.parametrize('pitch,origin,valid', [(0.5,(0,0),[[1]]),(1,(1,0),[[1]]),(1,(0,0),[[0]])])
def test_frame_compatibility_includes_origin_pitch_and_valid(pitch, origin, valid):
    a=Frame('f',1,(0,0),(1,1),[[1]])
    b=Frame('f',pitch,origin,(1,1),valid)
    with pytest.raises(NumericFrameError, match='incompatible_source_frame'):
        require_same_frame(a,b)


def test_pair_is_unordered_but_does_not_rewrite_raw_ids():
    pair=Pair('p',frozenset(['A:α','B-β']),'line','v2')
    assert pair.electrode_ids==frozenset(['B-β','A:α'])
    assert pair.classification=='line'


@pytest.mark.parametrize('window',[PhysicalWindow(1,0,1,1),PhysicalWindow(0,1,1,0),PhysicalWindow(0,0,np.inf,1)])
def test_invalid_windows(window):
    with pytest.raises(NumericContractError):
        window.bounds_um()
