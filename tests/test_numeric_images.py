from pathlib import Path
import hashlib

import numpy as np
from PIL import Image
import pytest

from simcompare.numeric_images import read_binary, resolve_path
from simcompare.numeric_schema import Frame, MaskRecord, NumericContractError, NumericPathError
from simcompare.measurement import measure_full_mask

FIXTURE=Path(__file__).parent/'fixtures/numeric_v2'


@pytest.mark.parametrize('name',['mask.png','mask_bool.npy','mask_01.npy','mask_255.npy'])
def test_encodings_have_identical_hand_calculated_ca(name):
    mask=read_binary(FIXTURE,name)
    assert mask.tolist()==[[True,False],[False,True]]
    frame=Frame('f',0.5,(0,0),(2,2),[[1,1],[1,1]])
    measured=measure_full_mask(MaskRecord(5,frame,mask))
    assert measured.ca_um2==0.5
    assert measured.valid_area_um2==1
    assert measured.status=='available'


@pytest.mark.parametrize('value', [np.array([[np.nan]]),np.array([[-1]]),np.ones((1,1,1)),
    np.array([[0.5]]),np.array([[255.0]]),np.array([[1,255]],dtype='uint8'),
    np.array([[1+0j]]),np.empty((0,1))])
def test_invalid_nominal_arrays_are_not_thresholded(tmp_path,value):
    np.save(tmp_path/'bad.npy',value)
    with pytest.raises(NumericContractError):
        read_binary(tmp_path,'bad.npy')


def test_pickle_rejected(tmp_path):
    np.save(tmp_path/'bad.npy',np.array([[object()]],dtype=object))
    with pytest.raises(NumericContractError):
        read_binary(tmp_path,'bad.npy')


@pytest.mark.parametrize('path',['../mask.npy','/tmp/mask.npy','C:\\mask.npy','..\\mask.npy'])
def test_absolute_and_relative_escapes_rejected(tmp_path,path):
    with pytest.raises(NumericPathError):
        resolve_path(tmp_path,path)


def test_symlink_escape_rejected(tmp_path):
    root=tmp_path/'root';root.mkdir()
    np.save(tmp_path/'outside.npy',np.array([[1]]))
    (root/'link.npy').symlink_to(tmp_path/'outside.npy')
    with pytest.raises(NumericPathError):
        read_binary(root,'link.npy')


def test_probability_semantics_not_promoted_even_if_values_happen_to_be_binary():
    with pytest.raises(NumericContractError,match='unsupported_mask_semantics'):
        read_binary(FIXTURE,'mask.png',value_semantics='probability')


def test_black_hotspot_png_is_read_as_marked_and_gives_the_hand_calculated_area(tmp_path):
    # 2x3 image, hotspot drawn black (0) on white (255): 2 black cells.
    Image.fromarray(np.array([[0,255,255],[255,0,255]],dtype=np.uint8)).save(tmp_path/'black.png')
    marked=read_binary(tmp_path,'black.png',value_semantics='binary_zero_is_marked')
    assert marked.tolist()==[[True,False,False],[False,True,False]]
    assert not marked.flags.writeable
    # Without the declaration the same file is read the other way round: 4 cells, not 2.
    assert int(read_binary(tmp_path,'black.png').sum())==4


def test_color_and_fractional_png_not_converted(tmp_path):
    Image.fromarray(np.zeros((1,1,3),dtype='uint8')).save(tmp_path/'rgb.png')
    Image.fromarray(np.array([[128]],dtype='uint8')).save(tmp_path/'gray.png')
    for name in ['rgb.png','gray.png']:
        with pytest.raises(NumericContractError):
            read_binary(tmp_path,name)


def test_asset_hash_checked():
    with pytest.raises(NumericContractError,match='input_hash_mismatch'):
        read_binary(FIXTURE,'mask.png',sha256='0'*64)
    digest=hashlib.sha256((FIXTURE/'mask.png').read_bytes()).hexdigest()
    assert read_binary(FIXTURE,'mask.png',sha256=digest).sum()==2
def test_real_asset_requires_explicit_polarity(tmp_path):
    import numpy as np
    import pytest
    from simcompare.numeric_images import read_binary
    from simcompare.numeric_schema import NumericContractError
    np.save(tmp_path/'black.npy',np.array([[0,255]],dtype=np.uint8))
    with pytest.raises(NumericContractError,match='explicit_value_semantics_required'):
        read_binary(tmp_path,'black.npy',synthetic=False)
    assert read_binary(tmp_path,'black.npy',synthetic=False,value_semantics='binary_zero_is_marked').tolist()==[[True,False]]
    assert read_binary(tmp_path,'black.npy',synthetic=True).tolist()==[[False,True]]
