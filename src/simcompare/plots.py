"""analyze 결과를 요약하는 SVG 그림을 만든다.

추가 의존성 없이 SVG 문자열을 직접 쓴다. 글자는 파일을 여는 쪽(브라우저 등)의 글꼴로 그려지므로
한글 제목·설명을 넣을 수 있다. 그림은 결과 표(ranking.csv, loo_stability.csv)를 옮긴 것이며
새 판단을 더하지 않는다. 비교 그룹(comparison_group_id)마다 작은 그림을 세로로 쌓는다.
"""
from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

FONT = "'Apple SD Gothic Neo','Malgun Gothic','Noto Sans CJK KR','Noto Sans KR',sans-serif"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8983", "#e8e7e2"
CONTEXT = "#b9b7b0"
EMPHASIS = ("#eb6834", "#2a78d6")  # 주황, 파랑
FAILURE_LABEL = {"lateral_open": "끊김(lateral open)", "lateral_short": "옆 전극 붙음(lateral short)",
                 "vertical_short": "위아래 전극 붙음(vertical short)"}
WIDTH = 760


def _t(x, y, text, *, size=11, color=INK2, anchor="start", weight="normal", baseline="middle") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}" '
            f'font-weight="{weight}" dominant-baseline="{baseline}">{escape(str(text))}</text>')


def _svg(height: float, body: list[str], title: str, subtitle: str) -> str:
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height:.0f}" '
        f'viewBox="0 0 {WIDTH} {height:.0f}" font-family="{FONT}">',
        f'<rect width="100%" height="100%" fill="#ffffff"/>',
        _t(20, 24, title, size=16, color=INK, weight="bold"),
        _t(20, 46, subtitle, size=11, color=MUTED),
    ]
    return "\n".join(head + body + ["</svg>"])


def _group_title(group: pd.DataFrame) -> str:
    row = group.iloc[0]
    failure = FAILURE_LABEL.get(row["failure_type"], row["failure_type"])
    roles = str(row["role_key"]).split("|", 1)[-1].replace("|", " · ")
    return f"{failure} · 전극 역할 {roles} · layer {str(row['layer_key']).replace('|', ' · ')}  (사례 {len(group)}개)"


def _label_width(labels, cap: int = 26) -> float:
    longest = max((min(len(str(x)), cap) for x in labels), default=4)
    return 12.0 + longest * 6.4


def _fit(text: str, cap: int = 26) -> str:
    """긴 사례 ID는 앞부분만 남기고 줄임표를 붙인다(원래 ID는 CSV에 있다)."""
    text = str(text)
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _spread(ys: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """라벨 세로 위치가 gap보다 가깝지 않도록 순서를 유지한 채 밀어낸다."""
    order = np.argsort(ys, kind="stable")
    out = list(ys)
    prev = -math.inf
    for i in order:
        out[i] = max(out[i], prev + gap)
        prev = out[i]
    overflow = prev - hi
    if overflow > 0:
        for i in order:
            out[i] = max(lo, out[i] - overflow)
    return out


# ------------------------------------------------------------------ 순위 변화
def _slope_panel(group: pd.DataFrame, x0: float, y0: float, width: float, height: float,
                 left: str, right: str, names: tuple[str, str], title: str) -> list[str]:
    parts = [_t(x0, y0, title, size=12, color=INK, weight="bold")]
    data = group[["sample_id", left, right]].copy()
    if data[left].isna().all() and data[right].isna().all():
        parts.append(_t(x0, y0 + 30, "두 순위 모두 값이 없어 비교할 수 없습니다", color=MUTED))
        return parts
    n = int(np.nanmax(data[[left, right]].to_numpy(dtype=float)))
    label_w = _label_width(data["sample_id"], cap=16)
    top, bottom = y0 + 34, y0 + height - 26
    step = (bottom - top) / max(n - 1, 1)
    xl, xr = x0 + 30 + label_w, x0 + width - label_w - 62

    def ypos(rank):
        return top + (rank - 1) * step

    for rank in range(1, n + 1):
        parts.append(f'<line x1="{xl:.1f}" x2="{xr:.1f}" y1="{ypos(rank):.1f}" y2="{ypos(rank):.1f}" '
                     f'stroke="{GRID}" stroke-width="0.6"/>')
    parts.append(_t(xl, bottom + 16, names[0], size=11, color=INK, anchor="middle"))
    parts.append(_t(xr, bottom + 16, names[1], size=11, color=INK, anchor="middle"))

    shift = (data[right] - data[left]).abs()
    movers = shift[shift > 0].sort_values(ascending=False, kind="stable").index[:2]
    colors = {idx: EMPHASIS[i] for i, idx in enumerate(movers)}
    lines, marks, left_labels, right_labels = [], [], [], []
    for idx, row in data.iterrows():
        a, b = row[left], row[right]
        color = colors.get(idx, CONTEXT)
        strong = idx in colors
        z = marks if strong else lines
        if pd.notna(a) and pd.notna(b):
            z.append(f'<line x1="{xl:.1f}" y1="{ypos(a):.1f}" x2="{xr:.1f}" y2="{ypos(b):.1f}" '
                     f'stroke="{color}" stroke-width="{2.2 if strong else 1.1}"/>')
        for rank, x in ((a, xl), (b, xr)):
            if pd.isna(rank):
                continue
            z.append(f'<circle cx="{x:.1f}" cy="{ypos(rank):.1f}" r="{4 if strong else 3}" fill="{color}" '
                     f'stroke="#ffffff" stroke-width="1"/>')
        missing = pd.isna(a) or pd.isna(b)
        name = _fit(row["sample_id"], 16) + ("*" if missing else "")
        if pd.notna(a):
            left_labels.append((ypos(a), name, strong))
        if pd.notna(b):
            text = f"{name}  {int(a)}위→{int(b)}위" if strong and pd.notna(a) else name
            right_labels.append((ypos(b), text, strong))
    parts += lines + marks
    for labels, x, anchor, dx in ((left_labels, xl, "end", -8), (right_labels, xr, "start", 8)):
        ys = _spread([y for y, _, _ in labels], 11.5, top - 6, bottom + 2)
        for (_, text, strong), y in zip(labels, ys):
            parts.append(_t(x + dx, y, text, size=10.5, color=INK if strong else INK2, anchor=anchor,
                            weight="bold" if strong else "normal"))
    for rank in range(1, n + 1):
        parts.append(_t(x0, ypos(rank), f"{rank}위", size=9.5, color=MUTED))
    if data[[left, right]].isna().any(axis=1).any():
        parts.append(_t(x0, bottom + 32, "* 한쪽 값이 없어 순위를 비교하지 못한 사례", size=9.5, color=MUTED))
    return parts


def rank_shift_svg(ranked: pd.DataFrame) -> str:
    """비교 그룹마다 원 CA→면적당 CA, 기준 분포 CAA→실제 Fab CAA 순위 변화를 그린다."""
    body, y = [], 70.0
    panel_w = (WIDTH - 60) / 2
    for _, group in ranked.groupby("comparison_group_id", sort=True):
        n = len(group)
        height = max(150.0, 60 + 22 * n)
        body.append(_t(20, y, _group_title(group), size=12.5, color=INK, weight="bold"))
        body.append(f'<line x1="20" x2="{WIDTH - 20}" y1="{y + 10:.1f}" y2="{y + 10:.1f}" stroke="{INK}" stroke-width="1"/>')
        body += _slope_panel(group, 20, y + 32, panel_w, height, "rank_ca_max", "rank_ca_area_max",
                             ("원 CA", "면적당 CA"), "① 크기 보정 전후")
        body += _slope_panel(group, 40 + panel_w, y + 32, panel_w, height, "rank_caa_reference", "rank_caa_actual",
                             ("기준 분포 CAA", "실제 Fab CAA"), "② Fab 분포 반영 전후")
        y += height + 90
    return _svg(y, body, "위험 순위 변화 (ranking.csv)",
                "1위가 가장 위험합니다. 순위가 가장 크게 바뀐 두 사례만 색으로 표시했습니다. 인과나 수율을 뜻하지 않습니다.")


# ------------------------------------------------------------------ CA 곡선
def _size_columns(ranked: pd.DataFrame) -> list[tuple[float, str]]:
    cols = []
    for col in ranked.columns:
        if col.startswith("ca@"):
            try:
                size = float(col[3:])
            except ValueError:
                continue
            if size > 0:
                cols.append((size, col))
    return sorted(cols)


def _plain(value: float) -> str:
    """0.00001처럼 지수 표기 없이 쓴다."""
    return f"{value:.12f}".rstrip("0").rstrip(".") if value < 1 else f"{value:g}"


def _log_ticks(lo: float, hi: float) -> list[float]:
    a, b = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    if a == b:
        b += 1
    return [10.0 ** k for k in range(a, b + 1)]


def ca_curves_svg(ranked: pd.DataFrame) -> str:
    """비교 그룹마다 이물 크기별 원 CA 곡선을 그린다(양쪽 로그 눈금, 빠진 값은 선을 끊는다)."""
    sizes = _size_columns(ranked)
    body, y = [], 70.0
    if not sizes:
        body.append(_t(20, y, "CA 크기 열이 없어 그릴 수 없습니다", color=MUTED))
        return _svg(y + 30, body, "이물 크기별 CA 곡선 (risk_table.csv)", "")
    height = 240.0
    for _, group in ranked.groupby("comparison_group_id", sort=True):
        values = group[[c for _, c in sizes]].to_numpy(dtype=float)
        values = np.where(values > 0, values, np.nan)
        body.append(_t(20, y, _group_title(group), size=12.5, color=INK, weight="bold"))
        body.append(f'<line x1="20" x2="{WIDTH - 20}" y1="{y + 10:.1f}" y2="{y + 10:.1f}" stroke="{INK}" stroke-width="1"/>')
        top, bottom = y + 34, y + 34 + height
        if np.isnan(values).all():
            body.append(_t(20, top + 20, "양수 CA 값이 없어 그릴 수 없습니다", color=MUTED))
            y = bottom + 70
            continue
        label_w = _label_width(group["sample_id"])
        xl, xr = 80.0, WIDTH - 30 - label_w
        ticks = _log_ticks(np.nanmin(values), np.nanmax(values))
        ly0, ly1 = math.log10(ticks[0]), math.log10(ticks[-1])
        sx0, sx1 = math.log2(sizes[0][0]), math.log2(sizes[-1][0])

        def px(size):
            return xl + (0.5 if sx1 == sx0 else (math.log2(size) - sx0) / (sx1 - sx0)) * (xr - xl)

        def py(v):
            return bottom - (math.log10(v) - ly0) / (ly1 - ly0) * (bottom - top)

        for tick in ticks:
            body.append(f'<line x1="{xl:.1f}" x2="{xr:.1f}" y1="{py(tick):.1f}" y2="{py(tick):.1f}" stroke="{GRID}" stroke-width="0.6"/>')
            body.append(_t(xl - 8, py(tick), _plain(tick), size=9.5, color=MUTED, anchor="end"))
        for size, _ in sizes:
            body.append(_t(px(size), bottom + 14, f"{size:g}", size=9.5, color=MUTED, anchor="middle"))
        body.append(_t((xl + xr) / 2, bottom + 32, "이물 크기 (로그 눈금)", size=10.5, color=INK2, anchor="middle"))
        body.append(f'<text x="24" y="{(top + bottom) / 2:.1f}" font-size="10.5" fill="{INK2}" text-anchor="middle" '
                    f'transform="rotate(-90 24 {(top + bottom) / 2:.1f})">CA (로그 눈금)</text>')

        ca_max = pd.to_numeric(group["ca_max"], errors="coerce").to_numpy(dtype=float)
        order = np.argsort(np.where(np.isnan(ca_max), -np.inf, -ca_max), kind="stable")
        top_row = order[0] if np.isfinite(ca_max[order[0]]) else None
        labelled = set(order[: len(order) if len(order) <= 8 else 3])
        back, front, labels = [], [], []
        for i in order[::-1]:
            strong = i == top_row
            color = EMPHASIS[0] if strong else CONTEXT
            bucket = front if strong else back
            pts = [(px(s), py(v)) for (s, _), v in zip(sizes, values[i]) if np.isfinite(v)]
            run = []
            for (s, _), v in zip(sizes, values[i]):
                if np.isfinite(v):
                    run.append(f"{px(s):.1f},{py(v):.1f}")
                    continue
                if len(run) > 1:
                    bucket.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="{color}" '
                                  f'stroke-width="{2.2 if strong else 1.1}"/>')
                run = []
            if len(run) > 1:
                bucket.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="{color}" '
                              f'stroke-width="{2.2 if strong else 1.1}"/>')
            for x, yy in pts:
                bucket.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="{3.5 if strong else 2.5}" fill="{color}" '
                              f'stroke="#ffffff" stroke-width="0.8"/>')
            if pts and i in labelled:
                labels.append((pts[-1][1], _fit(group["sample_id"].iloc[i]), strong))
        body += back + front
        ys = _spread([yy for yy, _, _ in labels], 11.5, top - 4, bottom + 4)
        for (_, text, strong), yy in zip(labels, ys):
            body.append(_t(xr + 10, yy, text, size=10.5, color=INK if strong else INK2,
                           weight="bold" if strong else "normal"))
        y = bottom + 70
    return _svg(y, body, "이물 크기별 CA 곡선 (risk_table.csv)",
                "주황은 그룹에서 원 CA 최댓값이 가장 큰 사례입니다. 빠진 값은 채우지 않고 선을 끊었습니다.")


# ------------------------------------------------------------------ 제품 제외 안정성
def loo_stability_svg(loo: pd.DataFrame) -> str:
    """제품을 하나씩 뺐을 때 위험 상위 목록과 비슷한 사례 목록이 유지되는 비율을 막대로 그린다."""
    metrics = (("top_items_jaccard", "위험 상위 목록 유지율", EMPHASIS[1]),
               ("neighbor_jaccard", "비슷한 사례 목록 유지율", EMPHASIS[0]))
    label_w = _label_width([f"{p} 제외" for p in loo.get("held_out_product", [])])
    xl, xr = 20 + label_w, WIDTH - 60
    y = 92.0
    body = []
    for i, (_, name, color) in enumerate(metrics):
        lx = 20 + i * 200
        body.append(f'<rect x="{lx}" y="62" width="12" height="12" rx="2" fill="{color}"/>')
        body.append(_t(lx + 18, 68, name, size=11, color=INK2))
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        x = xl + frac * (xr - xl)
        body.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y - 4:.1f}" y2="{y + 34 * len(loo):.1f}" stroke="{GRID}" stroke-width="0.6"/>')
        body.append(_t(x, y + 34 * len(loo) + 12, f"{frac:g}", size=9.5, color=MUTED, anchor="middle"))
    for _, row in loo.iterrows():
        body.append(_t(xl - 8, y + 14, f"{row['held_out_product']} 제외", size=11, color=INK, anchor="end"))
        for j, (col, _, color) in enumerate(metrics):
            by = y + 2 + j * 14
            value = row.get(col)
            if value is None or pd.isna(value):
                body.append(_t(xl + 4, by + 6, "값 없음", size=9.5, color=MUTED))
                continue
            w = max(0.0, min(1.0, float(value))) * (xr - xl)
            body.append(f'<rect x="{xl:.1f}" y="{by:.1f}" width="{w:.1f}" height="12" rx="2" fill="{color}"/>')
            body.append(_t(xl + w + 5, by + 6, f"{float(value):.2f}", size=9.5, color=INK2))
        y += 34
    return _svg(y + 30, body, "제품 하나를 빼도 결과가 유지되나요 (loo_stability.csv)",
                "1이면 전체 자료로 분석한 목록과 같고 0이면 전혀 겹치지 않습니다. 낮은 제품은 결론이 그 제품에 기대는지 확인하세요.")


def write_analysis_figures(out_dir: Path, ranked: pd.DataFrame, loo: pd.DataFrame) -> list[Path]:
    """out_dir/figures/ 아래에 rank_shift.svg, ca_curves.svg, loo_stability.svg를 쓴다."""
    fig_dir = Path(out_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name, svg in (("rank_shift.svg", rank_shift_svg(ranked)), ("ca_curves.svg", ca_curves_svg(ranked)),
                      ("loo_stability.svg", loo_stability_svg(loo))):
        path = fig_dir / name
        path.write_text(svg, encoding="utf-8")
        outputs.append(path)
    return outputs
