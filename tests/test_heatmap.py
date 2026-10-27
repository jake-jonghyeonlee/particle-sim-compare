"""Pillow 원본 셀 색과 물리 extent의 손계산 oracle."""
import json

import numpy as np
from PIL import Image
import pytest

from simcompare.heatmap import write_heatmap
from simcompare.numeric_schema import Frame, NumericContractError


def test_hand_checked_cells_legend_extent_and_valid_hole(tmp_path):
    frame=Frame('FRAME',.5,(100,200),(2,3),[[1,1,1],[1,0,1]])
    result=write_heatmap(tmp_path,'counts',np.array([[0,2,3],[1,7,4]]),frame,
                         status='partial',reasons=['valid_coverage_partial'])
    with Image.open(tmp_path/result['png']) as image:
        assert image.getpixel((0,0))==(255,255,255)  # observed zero
        assert image.getpixel((134,0))==(207,215,231)  # count 2
        assert image.getpixel((268,0))==(190,201,223)  # count 3
        assert image.getpixel((0,134))==(229,233,242)  # count 1
        assert image.getpixel((134,134))==(160,160,160)  # invalid, not 7 or 0
        assert image.getpixel((268,134))==(175,189,215)  # count 4
    sidecar=json.loads((tmp_path/result['sidecar']).read_text())
    assert sidecar['extent_um']==[100,200,101.5,201]
    assert sidecar['rendering']['grid_bbox_px']==[0,0,402,268]
    assert sidecar['rendering']['scale']==134
    assert [entry['count'] for entry in sidecar['legend']]==[0,1,2,3,4]
    assert sidecar['frame']['pixel_pitch_um']==.5
    assert sidecar['status']=='partial'


def test_unavailable_is_gray_not_observed_zero_and_incompatible_has_no_image(tmp_path):
    frame=Frame('F',1,(0,0),(1,1),[[1]])
    result=write_heatmap(tmp_path,'missing',None,frame,status='unavailable',reasons=['source_case_missing'])
    with Image.open(tmp_path/result['png']) as image:
        assert image.getpixel((0,0))==(160,160,160)
    assert json.loads((tmp_path/result['sidecar']).read_text())['legend']==[]
    result=write_heatmap(tmp_path,'incompatible',None,None,status='unavailable',reasons=['incompatible_layer_frames'])
    assert result['png'] is None
    assert json.loads((tmp_path/result['sidecar']).read_text())['extent_um'] is None


@pytest.mark.parametrize('counts',[np.array([[.5]]),np.array([[-1]]),np.array([[[1]]])])
def test_invalid_counts_not_thresholded_or_resized(tmp_path,counts):
    with pytest.raises(NumericContractError,match='invalid_electrode_count_map'):
        write_heatmap(tmp_path,'bad',counts,Frame('F',1,(0,0),(1,1),[[1]]),status='available')


def test_two_by_four_grid_visible_integer_scale_and_exact_cells(tmp_path):
    frame=Frame('F',.5,(100,200),(2,4),np.ones((2,4),bool))
    result=write_heatmap(tmp_path,'small',np.array([[0,1,2,3],[4,5,6,0]]),frame,status='available')
    meta=json.loads((tmp_path/result['sidecar']).read_text())
    assert meta['rendering']['scale']==100
    assert meta['rendering']['grid_bbox_px']==[0,0,400,200]
    assert meta['extent_um']==[100,200,102,201]
    with Image.open(tmp_path/result['png']) as image:
        assert image.getpixel((199,99))==(229,233,242)
        assert image.getpixel((200,0))==(207,215,231)
        assert image.getpixel((250,150))==(153,170,204)
        assert image.getpixel((399,199))==(255,255,255)


def test_large_2000_pixel_grid_stays_one_to_one(tmp_path):
    frame=Frame('F',.05,(0,0),(1,2000),np.ones((1,2000),bool))
    counts=np.zeros((1,2000),dtype=int);counts[0,1999]=3
    result=write_heatmap(tmp_path,'large',counts,frame,status='available')
    meta=json.loads((tmp_path/result['sidecar']).read_text())
    assert meta['rendering']['scale']==1
    assert meta['rendering']['grid_bbox_px']==[0,0,2000,1]
    assert meta['extent_um']==[0,0,100,.05]
    with Image.open(tmp_path/result['png']) as image:
        assert image.getpixel((1998,0))==(255,255,255)
        assert image.getpixel((1999,0))==(190,201,223)


def test_r2_sequential_palette_above_six_hand_checked():
    from simcompare.heatmap import count_color
    assert count_color(7)==(143,162,199)
    assert count_color(8)==(135,155,195)
    assert count_color(16)==(95,122,175)
    assert all(all(b<=a for a,b in zip(count_color(n),count_color(n+1))) for n in range(199))


def test_grade_options_change_palette_unit_title_and_labels_only(tmp_path):
    frame=Frame('FRAME',.5,(100,200),(1,3),[[1,1,1]])
    colors={0:(255,255,255),1:(255,236,160),2:(255,152,0),3:(198,40,40)}
    result=write_heatmap(tmp_path,'grade',np.array([[0,1,3]],dtype=np.uint8),frame,status='available',
                         palette=lambda value:colors[int(value)],unit='grade_max',title='Max grade',labels=lambda value:f'g{value}')
    sidecar=json.loads((tmp_path/result['sidecar']).read_text())
    assert sidecar['unit']=='grade_max' and sidecar['legend']==[{'count':0,'color':[255,255,255],'label':'g0'},
        {'count':1,'color':[255,236,160],'label':'g1'},{'count':3,'color':[198,40,40],'label':'g3'}]
    with Image.open(tmp_path/result['png']) as image:
        assert image.getpixel((0,0))==(255,255,255) and image.getpixel((134,0))==(255,236,160) and image.getpixel((268,0))==(198,40,40)
    plain=write_heatmap(tmp_path,'plain',np.array([[0,1,3]],dtype=np.uint8),frame,status='available')
    default=json.loads((tmp_path/plain['sidecar']).read_text())
    assert default['unit']=='distinct_electrodes' and all('label' not in entry for entry in default['legend'])


def test_unsigned_integer_count_maps_are_accepted(tmp_path):
    frame=Frame('FRAME',.5,(0,0),(1,2),[[1,1]])
    for dtype in (np.uint8,np.uint16):
        assert write_heatmap(tmp_path,'u'+np.dtype(dtype).name,np.array([[1,2]],dtype=dtype),frame,status='available')['png']
