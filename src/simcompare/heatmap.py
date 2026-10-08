"""Pillow 전극 수 지도: 원본 셀·2x2 domain 유지, 확률/자동 영역 선택 없음."""
from __future__ import annotations

import colorsys
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .numeric_schema import NumericContractError

UNAVAILABLE_COLOR = (160, 160, 160)
# 고정 discrete palette: 제품/레이어마다 같은 count는 같은 색이다.
COLORS = ((255, 255, 255), (49, 130, 189), (107, 174, 214),
          (255, 187, 120), (255, 127, 14), (214, 39, 40), (148, 103, 189))


def count_color(count):
    if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 0:
        raise NumericContractError('invalid_electrode_count')
    if count < len(COLORS):
        return COLORS[count]
    # 많은 전극에도 discrete count별 안정 색상. density/확률 scale을 쓰지 않는다.
    return tuple(round(c*255) for c in colorsys.hsv_to_rgb((count*.61803398875)%1, .75, .85))


def write_heatmap(out, name, counts, frame, *, status, reasons=(), provenance=None):
    """원본 grid를 PNG 좌상단에 1셀=1픽셀로 그림; 오른쪽은 count legend.

    invalid/미관측은 회색이며 0(흰색)과 다르다. frame 없는 summary는 sidecar만 쓴다.
    physical extent는 원본 domain의 half-open (x0,y0,x1,y1), 단위 um다.
    """
    out = Path(out);out.mkdir(parents=True, exist_ok=True)
    metadata = {'status':status,'reasons':list(reasons),'unit':'distinct_electrodes',
                'physical_unit':'um','extent_um':None if frame is None else list(frame.bbox_um),
                'frame':None if frame is None else {
                    'frame_id':frame.frame_id,'origin_xy_um':list(frame.origin_xy_um),
                    'pixel_pitch_um':frame.pixel_pitch_um,'shape_yx':list(frame.shape_yx),
                    'axis_order':frame.axis_order,'valid_hash':frame.valid_hash},
                'rendering':{'cell_pixels':1,'origin':'top_left','grid_bbox_px':None,
                             'unavailable_color':list(UNAVAILABLE_COLOR)},
                'provenance':provenance or {}, 'legend':[], 'png':None}
    if frame is not None:
        if counts is not None:
            counts = np.asarray(counts)
            if (counts.shape != frame.shape_yx or not np.issubdtype(counts.dtype,np.integer)
                    or np.any(counts < 0)):
                raise NumericContractError('invalid_electrode_count_map')
        height,width = frame.shape_yx
        values = [] if counts is None else sorted(map(int,np.unique(counts[frame.valid])))
        metadata['legend'] = [{'count':n,'color':list(count_color(n))} for n in values]
        metadata['rendering']['grid_bbox_px'] = [0,0,width,height]
        image = Image.new('RGB',(width+240,max(height,45+18*(len(values)+1))),UNAVAILABLE_COLOR)
        if counts is not None:
            pixels = image.load()
            for r,c in np.ndindex(frame.shape_yx):
                pixels[c,r] = count_color(int(counts[r,c])) if frame.valid[r,c] else UNAVAILABLE_COLOR
        draw = ImageDraw.Draw(image)
        draw.rectangle((width,0,image.width,image.height),fill='white')
        draw.text((width+10,5),'Distinct electrodes: '+status,fill='black')
        for index,n in enumerate(values):
            y=25+18*index
            draw.rectangle((width+10,y,width+23,y+12),fill=count_color(n))
            draw.text((width+30,y),str(n),fill='black')
        y=25+18*len(values)
        draw.rectangle((width+10,y,width+23,y+12),fill=UNAVAILABLE_COLOR)
        draw.text((width+30,y),'unavailable / outside valid',fill='black')
        filename=name+'.png';image.save(out/filename)
        metadata['png']=filename
    sidecar=name+'.json'
    (out/sidecar).write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return {'png':metadata['png'],'sidecar':sidecar,'status':status}


def write_heatmaps(out, maps):
    results=[]
    for kind,entries in (('layer',maps.layers),('summary',maps.summaries)):
        for entry in entries:
            key=asdict(entry.key) if kind=='layer' else list(entry.key)
            token=hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:20]
            provenance={**maps.provenance,'map_kind':kind,'key':key}
            if kind=='summary':provenance.update(entry.provenance)
            results.append({'kind':kind,'key':key,**write_heatmap(out,'electrode_map_'+kind+'_'+token,
                entry.counts,entry.frame,status=entry.status,reasons=entry.reasons,provenance=provenance)})
    return results
