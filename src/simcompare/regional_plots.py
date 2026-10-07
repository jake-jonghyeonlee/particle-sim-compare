"""원본 layout/hotspot을 좌표 패널로, 이미 계산된 CA를 곡선으로 그린다.

CA 적분·등록 추론·위험 선정·파일 IO는 하지 않는다. 색/축은 아래 표시 상수,
좌표 패널은 overview_svg/crop_svg, 결측에서 끊기는 곡선은 curves_svg를 수정한다.
PNG는 원본 셀을 그대로 담고 SVG에서 물리 축척으로 표시하며 정량 resize는 없다.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field, replace
from html import escape
from io import BytesIO
from typing import Sequence

import numpy as np
from PIL import Image

from .regional_comparison import ComparisonRow, RegionalComparison
from .regional_measurement import frame_geometry_um, geometry_um
from .regional_schema import PhysicalWindow, SpatialFrame
from .regions import Region
from .schema import AnalysisBundle, convert_length

# ---------------------------------------------------------------------------
# 표시 정책: 물리 계산과 무관한 화면 크기/색/여백이며 단위는 SVG 화면 px
# ---------------------------------------------------------------------------
WIDTH = 1000
PANEL_WIDTH, PANEL_HEIGHT = 440, 280
PANEL_LEFT, PANEL_TOP, PANEL_GAP = 35, 65, 55
FIGURE_HEIGHT = 410
FONT = "'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif"
INK, MUTED, QUERY, REFERENCE = "#16263a", "#54677d", "#176dc1", "#8155b1"
LAYOUT_COLORS = ((91, 137, 164), (162, 139, 171))
HOTSPOT_COLOR = (233, 105, 43)
REGION_COLOR = "#c54820"
DISPLAY_ALPHA = 200  # 원본 positivity support를 볼 수 있게 하는 표시 불투명도
MIN_SUPPORT_ALPHA = 45  # 아주 약한 양수 성분도 화면에서 생략하지 않기 위한 값
LABEL_LIMIT = 32
CHART_TOP, CHART_HEIGHT, CHART_LEFT, CHART_RIGHT = 55, 180, 85, 45
CHART_BLOCK_HEIGHT = 290

# 제목·축·범례가 그림과 겹치지 않도록 정한 화면 px 위치/간격이다. 물리 계산에 쓰지
# 않는다.
PANEL_TITLE_Y, PANEL_SUBTITLE_Y, EMPTY_OFFSET_Y = 25, 48, 80
PANEL_BOUNDS_Y, PANEL_LEGEND_Y = 365, 387
REGION_LABEL_INSET, REGION_LABEL_MIN_Y, REGION_LABEL_GAP = 3, 15, 5
CHART_TITLE_Y, CHART_REFERENCE_Y = 25, 45
Y_AXIS_LABEL_X, ZERO_LABEL_X, Y_AXIS_LABEL_INSET = 10, 45, 8
X_AXIS_LABEL_GAP, X_TICK_LABEL_GAP, LEGEND_RIGHT_INSET = 35, 18, 220
EMPTY_INSET_X = 30
POINT_RADIUS, CURVE_STROKE, REGION_STROKE = 4, 2.5, 2
FONT_BODY, FONT_TITLE, FONT_SMALL, FONT_LABEL, FONT_TICK = 14, 17, 12, 13, 11
HATCH_SIZE = 8  # 미확인 영역을 셀 값과 혼동하지 않도록 만든 화면 사선 패턴의 주기


@dataclass(frozen=True)
class _Plane:
    array: np.ndarray
    x: float
    y: float
    pitch: float
    color: tuple[int, int, int]
    intensity: bool
    kind: str


def _text(x: float, y: float, text: object, *, size: int = FONT_BODY, color: str = INK) -> str:
    return f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" fill="{color}">{escape(str(text))}</text>'


def _short(text: str) -> str:
    return text if len(text) <= LABEL_LIMIT else text[:LABEL_LIMIT - 1] + "…"


def _svg(body: Sequence[str], height: int, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" role="img" font-family="{FONT}">'
            f'<title>{escape(title)}</title><rect width="100%" height="100%" fill="#fff"/>' +
            "\n".join(body) + "</svg>")


def _plane_image(array: np.ndarray, color: tuple[int, int, int], intensity: bool) -> Image.Image:
    positive = np.isfinite(array) & (array > 0)
    rgba = np.zeros((*array.shape, 4), dtype=np.uint8)
    rgba[:, :, :3] = color
    if intensity:
        # 표시 alpha만 범위를 제한한다. 원본 density 및 측정 값은 절대 clip하지 않는다.
        alpha = MIN_SUPPORT_ALPHA + np.nan_to_num(np.clip(array, 0, 1)) * float(DISPLAY_ALPHA - MIN_SUPPORT_ALPHA)
        rgba[:, :, 3] = np.where(positive, alpha, 0).astype(np.uint8)
    else:
        rgba[:, :, 3] = np.where(positive, DISPLAY_ALPHA, 0).astype(np.uint8)
    return Image.fromarray(rgba)


def _png(image: Image.Image) -> str:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _build_planes(bundle: AnalysisBundle, translation: tuple[float, float], physical: bool) -> list[_Plane]:
    planes = []
    images = [(image, LAYOUT_COLORS[i % len(LAYOUT_COLORS)], False, "layout") for i, image in enumerate(bundle.layouts)]
    images += [(image, HOTSPOT_COLOR, True, "hotspot") for image in bundle.hotspots]
    for image, color, intensity, kind in images:
        if image.array is None or image.array.ndim != 2:
            continue
        if physical:
            if image.pixel_size is None or image.origin_xy is None or bundle.physical_length_unit is None:
                continue
            try:
                pitch, x, y = geometry_um(SpatialFrame(image.pixel_size, image.origin_xy,
                                                     bundle.physical_length_unit))
            except ValueError:
                continue
            x, y = x + translation[0], y + translation[1]
        else:
            pitch, x, y = 1., 0., 0.  # 표시 전용 pixel 좌표이며 정량 원점으로 전달하지 않는다.
        planes.append(_Plane(image.array, x, y, pitch, color, intensity, kind))
    if not bundle.hotspots:
        for source in bundle.ca_spatial_sources:
            if source.density is None:
                continue
            try:
                pitch, x, y = frame_geometry_um(source)
            except ValueError:
                continue
            if physical:
                x, y = x + translation[0], y + translation[1]
            else:
                pitch, x, y = 1., 0., 0.
            planes.append(_Plane(source.density, x, y, pitch, HOTSPOT_COLOR, True, "ca_map"))
    return planes


@dataclass
class RenderCache:
    """한 보고 실행의 원본 plane·표시 crop PNG를 재사용한다. 입력 변경/IO/CA 계산은 없다."""
    planes: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    tiles: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)

    def image(self, plane: _Plane, bounds: PhysicalWindow) -> tuple[str, float, float, float, float] | None:
        """실제 표시 bounds와 만나는 원본 셀만 인코딩한다. 물리 축척은 um, resize는 없다."""
        h, w = plane.array.shape
        left = max(0, int(np.floor((bounds.x0 - plane.x) / plane.pitch)))
        top = max(0, int(np.floor((bounds.y0 - plane.y) / plane.pitch)))
        right = min(w, int(np.ceil((bounds.x1 - plane.x) / plane.pitch)))
        bottom = min(h, int(np.ceil((bounds.y1 - plane.y) / plane.pitch)))
        if right <= left or bottom <= top:
            return None
        key = (id(plane.array), plane.color, plane.intensity, left, top, right, bottom)
        if key not in self.tiles:
            tile = plane.array[top:bottom, left:right]
            if not np.any(np.isfinite(tile) & (tile > 0)):
                self.tiles[key] = None
            else:
                # 같은 PNG는 sample/지름/창에서 다시 본문에 복사하지 않고 defs를 공유한다.
                plane_key = (id(plane.array), plane.color, plane.intensity)
                if plane_key not in self.images:
                    self.images[plane_key] = _plane_image(plane.array, plane.color, plane.intensity)
                uri = _png(self.images[plane_key].crop((left, top, right, bottom)))
                token = "plane-" + hashlib.sha256(uri.encode()).hexdigest()
                self.assets.setdefault(token, uri)
                self.tiles[key] = token
        token = self.tiles[key]
        if token is None:
            return None
        return token, plane.x + left * plane.pitch, plane.y + top * plane.pitch, \
            (right - left) * plane.pitch, (bottom - top) * plane.pitch

    def definitions(self, tokens: Sequence[str] | None = None) -> str:
        """공유 PNG 정의를 inline SVG로 반환한다. standalone 정적 SVG도 같은 정의를 사용한다."""
        selected = sorted(self.assets if tokens is None else set(tokens))
        images = [f'<image id="{token}" width="1" height="1" preserveAspectRatio="none" '
                  f'href="{self.assets[token]}" xlink:href="{self.assets[token]}"/>' for token in selected]
        return "<defs>" + "".join(images) + "</defs>"


def _planes(bundle: AnalysisBundle, translation: tuple[float, float], physical: bool,
            cache: RenderCache) -> list[_Plane]:
    key = (bundle.sample_id, physical)
    if key not in cache.planes:
        cache.planes[key] = _build_planes(bundle, (0., 0.), physical)
    return [replace(plane, x=plane.x + translation[0], y=plane.y + translation[1])
            for plane in cache.planes[key]]


def _bounds(planes: Sequence[_Plane], regions: Sequence[Region]) -> PhysicalWindow | None:
    windows = [
        PhysicalWindow(p.x, p.y, p.x + p.array.shape[1] * p.pitch, p.y + p.array.shape[0] * p.pitch)
        for p in planes
    ]
    windows += [r.display_bounds_um for r in regions]
    if not windows:
        return None
    return PhysicalWindow(min(w.x0 for w in windows), min(w.y0 for w in windows),
                          max(w.x1 for w in windows), max(w.y1 for w in windows))


def _translation(comparison: RegionalComparison, sid: str, common: bool) -> tuple[float, float]:
    registration = comparison.correspondence.registration if comparison.correspondence else None
    if not common or registration is None:
        return 0., 0.
    if sid == comparison.query_sample_id:
        return registration.query_translation_xy_um
    return registration.reference_translation_xy_um


def _coverage_plane(bundle: AnalysisBundle, translation: tuple[float, float]) -> _Plane | None:
    for source in bundle.ca_spatial_sources:
        if source.density is None or source.valid_mask is None or source.valid_mask.shape != source.density.shape:
            continue
        try:
            pitch, x, y = frame_geometry_um(source)
        except ValueError:
            continue
        return _Plane(source.valid_mask, x + translation[0], y + translation[1], pitch, (255, 255, 255), False, "valid")
    return None


def _panel(bundle: AnalysisBundle, planes: Sequence[_Plane], bounds: PhysicalWindow | None,
           regions: Sequence[Region], index: int, *, physical: bool, common: bool,
           translation: tuple[float, float], panel_id: str, labels: dict[str, int], cache: RenderCache) -> list[str]:
    left = PANEL_LEFT + index * (PANEL_WIDTH + PANEL_GAP)
    body = [_text(left, PANEL_TITLE_Y, _short(bundle.sample_id), size=FONT_TITLE),
            _text(left, PANEL_SUBTITLE_Y, "명시 공통 좌표" if common else "별도 local 좌표 · 미등록",
                  color=MUTED)]
    if bounds is None:
        return body + [_text(left, PANEL_TOP + EMPTY_OFFSET_Y, "layout/hotspot 위치 자료 없음", color=MUTED)]
    scale = min(PANEL_WIDTH / (bounds.x1 - bounds.x0), PANEL_HEIGHT / (bounds.y1 - bounds.y0))
    draw_width, draw_height = (bounds.x1 - bounds.x0) * scale, (bounds.y1 - bounds.y0) * scale
    x0 = left + (PANEL_WIDTH - draw_width) / 2
    y0 = PANEL_TOP + (PANEL_HEIGHT - draw_height) / 2
    clip_id, hatch_id = panel_id + "-clip", panel_id + "-hatch"
    body += [f'<defs><clipPath id="{clip_id}"><rect x="{x0}" y="{y0}" width="{draw_width}" height="{draw_height}"/>'
             f'</clipPath><pattern id="{hatch_id}" width="{HATCH_SIZE}" height="{HATCH_SIZE}" '
             'patternUnits="userSpaceOnUse">'
             f'<rect width="{HATCH_SIZE}" height="{HATCH_SIZE}" fill="#edf0f4"/>'
             f'<path d="M0 {HATCH_SIZE} L{HATCH_SIZE} 0" stroke="#c6ced8"/></pattern></defs>',
             f'<rect x="{x0}" y="{y0}" width="{draw_width}" height="{draw_height}" fill="url(#{hatch_id})"/>',
             f'<g clip-path="url(#{clip_id})">']
    if bundle.sample_id not in cache.coverage:
        cache.coverage[bundle.sample_id] = _coverage_plane(bundle, (0., 0.))
    coverage = cache.coverage[bundle.sample_id] if physical else None
    if coverage:
        coverage = replace(coverage, x=coverage.x + translation[0], y=coverage.y + translation[1])
    drawing = ([coverage] if coverage else []) + list(planes)
    for plane in drawing:
        tile = cache.image(plane, bounds)
        if tile is None:
            continue
        token, px, py, pw, ph = tile
        x, y = x0 + (px - bounds.x0) * scale, y0 + (py - bounds.y0) * scale
        width, height = pw * scale, ph * scale
        body.append(f'<use href="#{token}" xlink:href="#{token}" '
                    f'transform="translate({x} {y}) scale({width} {height})" '
                    'style="image-rendering:pixelated"/>')
    for region in regions:
        w = region.bounds_um
        x, y = x0 + (w.x0 - bounds.x0) * scale, y0 + (w.y0 - bounds.y0) * scale
        width, height = (w.x1 - w.x0) * scale, (w.y1 - w.y0) * scale
        body.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="none" '
                    f'stroke="{REGION_COLOR}" stroke-width="{REGION_STROKE}" data-region-id="{region.region_id}"/>')
        body.append(_text(x + REGION_LABEL_INSET, max(y0 + REGION_LABEL_MIN_Y, y - REGION_LABEL_GAP),
                          f"R{labels[region.region_id]}", size=FONT_LABEL, color=REGION_COLOR))
    body += ["</g>", f'<rect x="{x0}" y="{y0}" width="{draw_width}" height="{draw_height}" '
             'fill="none" stroke="#8191a3"/>']
    unit = "µm" if physical else "px · 물리 원점/축척 미확인"
    coordinates = f"x [{bounds.x0:g}, {bounds.x1:g}), y [{bounds.y0:g}, {bounds.y1:g}) {unit}"
    legend = "layout 없음 · hotspot만 표시" if not bundle.layouts else "전극 a/b + 양수 hotspot support"
    body += [_text(left, PANEL_BOUNDS_Y, coordinates, size=FONT_SMALL, color=MUTED),
             _text(left, PANEL_LEGEND_Y, legend, size=FONT_SMALL, color=MUTED)]
    return body


# ---------------------------------------------------------------------------
# 위치 overview와 동일 물리 축척 crop: 등록 없는 상대 패널은 겹치지 않는다
# ---------------------------------------------------------------------------


def overview_svg(comparison: RegionalComparison, bundles: Sequence[AnalysisBundle],
                 *, cache: RenderCache | None = None) -> str:
    """원본 2D 자료의 전체 좌표와 지역 label을 SVG로 반환한다. IO/CA 계산은 없다.

    등록 pair는 translation된 um 공통 bounds/축척, 미등록은 별도 local 패널이다.
    원점/단위 결측 영상은 표시 전용 px 패널로 명시하고 정량 등록을 추론하지 않는다.
    """
    standalone = cache is None
    cache = cache or RenderCache()
    by_id = {b.sample_id: b for b in bundles}
    present = [by_id[sid] for sid in (comparison.query_sample_id, comparison.reference_sample_id) if sid in by_id]
    common = len(comparison.region_sets) == 1 and comparison.region_sets[0].common_frame
    infos = []
    for bundle in present:
        translation = _translation(comparison, bundle.sample_id, common)
        regions = [r for group in comparison.region_sets for r in group.regions
                   if bundle.sample_id in r.member_sample_ids]
        planes = _planes(bundle, translation, True, cache)
        physical = bool(planes or regions)
        if not physical:
            planes = _planes(bundle, (0., 0.), False, cache)
        infos.append((bundle, planes, regions, physical, translation))
    common_bounds = _bounds([p for _, planes, _, _, _ in infos for p in planes],
                            [r for _, _, regions, _, _ in infos for r in regions]) if common else None
    body = []
    token = comparison.rows[0].comparison_id if comparison.rows else comparison.comparison_id
    prefix = hashlib.sha256(token.encode()).hexdigest()
    labels = {
        r.region_id: i for i, r in enumerate([r for group in comparison.region_sets for r in group.regions], 1)
    }
    for i, (bundle, planes, regions, physical, translation) in enumerate(infos):
        bounds = common_bounds if common else _bounds(planes, regions)
        body += _panel(bundle, planes, bounds, regions, i, physical=physical, common=common,
                       translation=translation, panel_id=f"overview-{prefix}-{i}", labels=labels, cache=cache)
    if standalone:
        body.insert(0, cache.definitions())
    return _svg(body, FIGURE_HEIGHT, "전체 layout/hotspot와 지역 위치")


def crop_svg(comparison: RegionalComparison, region: Region, bundles: Sequence[AnalysisBundle],
             *, cache: RenderCache | None = None) -> str:
    """고정 창의 원본 crop을 SVG로 반환한다. um 좌상단 기준, 추가 반경 팽창은 없다.

    공통 등록에서는 양쪽 같은 bounds/scale를 사용한다. 미등록은 region 소유 sample만
    별도 local 패널에 표시하고 delta를 그리지 않는다. margin은 표시 bounds에만 적용된다.
    """
    standalone = cache is None
    cache = cache or RenderCache()
    by_id = {b.sample_id: b for b in bundles}
    common = any(group.common_frame and region in group.regions for group in comparison.region_sets)
    body = []
    labels = {
        r.region_id: i for i, r in enumerate([r for group in comparison.region_sets for r in group.regions], 1)
    }
    members = [sid for sid in (comparison.query_sample_id, comparison.reference_sample_id)
               if sid in region.member_sample_ids]
    for i, sid in enumerate(members):
        bundle = by_id[sid]
        translation = _translation(comparison, sid, common)
        body += _panel(bundle, _planes(bundle, translation, True, cache), region.display_bounds_um, [region], i,
                       physical=True, common=common, translation=translation,
                       panel_id=f"crop-{region.region_id}-{i}", labels=labels, cache=cache)
    if standalone:
        body.insert(0, cache.definitions())
    return _svg(body, FIGURE_HEIGHT, "동일 물리 창 · 정량 경계와 표시 crop")


# ---------------------------------------------------------------------------
# 계산 결과 곡선: 미관측 지름을 가로질러 선을 연결하거나 보간하지 않는다
# ---------------------------------------------------------------------------


def _chart(rows: Sequence[ComparisonRow], roi: bool, top: int) -> list[str]:
    rows = sorted(rows, key=lambda row: row.diameter_um)

    def value(row: ComparisonRow, side: str) -> float | None:
        observation = getattr(row, side)
        if roi:
            return observation.roi.roi_ca_um2 if observation.roi else None
        return observation.unit_total_ca_um2

    values = [value(row, side) for row in rows for side in ("query", "reference")]
    ymax = max((v for v in values if v is not None), default=0.)
    ymax = ymax if ymax > 0 else 1.  # 값이 전부 0/결측인 표시축만 1로 확보한다.
    xmin = min((row.diameter_um for row in rows), default=0.)
    xmax = max((row.diameter_um for row in rows), default=1.)
    xspan = xmax - xmin if xmax > xmin else 1.
    width = WIDTH - CHART_LEFT - CHART_RIGHT
    base = top + CHART_TOP + CHART_HEIGHT

    def point(diameter: float, ca: float) -> tuple[float, float]:
        return CHART_LEFT + (diameter - xmin) / xspan * width, base - ca / ymax * CHART_HEIGHT

    title = "지역 CA 기여 · µm²" if roi else "unit/source 전체 CA · µm²"
    body = [_text(CHART_LEFT, top + CHART_TITLE_Y, title, size=FONT_TITLE),
            f'<path d="M{CHART_LEFT} {top + CHART_TOP} V{base} H{WIDTH - CHART_RIGHT}" fill="none" stroke="#8191a3"/>',
            _text(Y_AXIS_LABEL_X, top + CHART_TOP + Y_AXIS_LABEL_INSET, f"{ymax:g}", size=FONT_SMALL),
            _text(ZERO_LABEL_X, base, "0", size=FONT_SMALL),
            _text(CHART_LEFT, base + X_AXIS_LABEL_GAP, "이물 지름 (µm)", size=FONT_LABEL, color=MUTED)]
    for side, color, label in (("query", QUERY, "query"), ("reference", REFERENCE, "reference")):
        segments, segment = [], []
        for row in rows:
            ca = value(row, side)
            if ca is None:
                if segment:
                    segments.append(segment)
                    segment = []
            else:
                segment.append(point(row.diameter_um, ca))
        if segment:
            segments.append(segment)
        for i, points in enumerate(segments):
            encoded = " ".join(f"{x:.4f},{y:.4f}" for x, y in points)
            body.append(f'<polyline points="{encoded}" fill="none" stroke="{color}" stroke-width="{CURVE_STROKE}" '
                        f'data-series="{side}" data-segment="{i}" data-metric="{"roi" if roi else "total"}"/>')
            body.extend(f'<circle cx="{x}" cy="{y}" r="{POINT_RADIUS}" fill="{color}"/>' for x, y in points)
        legend_y = top + CHART_TITLE_Y if side == "query" else top + CHART_REFERENCE_Y
        body.append(_text(WIDTH - LEGEND_RIGHT_INSET, legend_y, label, size=FONT_LABEL, color=color))
    for row in rows:
        x, _ = point(row.diameter_um, 0)
        body.append(_text(x, base + X_TICK_LABEL_GAP, f"{row.diameter_um:g}", size=FONT_TICK, color=MUTED))
    if not any(v is not None for v in values):
        body.append(_text(CHART_LEFT + EMPTY_INSET_X, top + CHART_TOP + EMPTY_OFFSET_Y,
                          "면적 CA 미제공 · 원 CA 단위/사유는 아래 표에서 확인", color=MUTED))
    return body


def curves_svg(rows: Sequence[ComparisonRow], *, include_global: bool = True,
               include_roi: bool = True) -> str:
    """동일 region의 모든 지름 global/ROI CA 곡선을 그린다. 값은 계산 결과 그대로다.

    배열/원점은 재계산하지 않고 um² 수치만 표시한다. legacy와 결측은 곡선값을 만들지
    않으며 missing 지름에서 segment를 끊는다. include_*은 중복 global 그림을 생략하는
    표시 선택이다. IO/보간/입력 변경은 없다.
    """
    body, blocks = [], 0
    for selected, roi in ((include_global, False), (include_roi, True)):
        if selected:
            body.extend(_chart(rows, roi, CHART_BLOCK_HEIGHT * blocks))
            blocks += 1
    if not blocks:
        raise ValueError("at least one curve metric must be selected")
    return _svg(body, CHART_BLOCK_HEIGHT * blocks, "지름별 CA · 결측에서 끊긴 곡선")
