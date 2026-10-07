"""계산된 지역 결과를 standalone HTML·JSON·CSV와 local SVG로 저장한다.

CA 재계산, 창 선정, 등록/대응 추론은 하지 않는다. 표 필드는 _measurement_rows와
_comparison_rows, HTML 구성은 _comparison_html, 출력 계약은 write_report를 수정한다.
그림은 regional_plots에 맡기며 외부 asset/JS/네트워크를 사용하지 않는다.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
from html import escape
import json
import re
from pathlib import Path
from typing import Sequence

from .regional_schema import RegionalAnalysis, json_safe
from .regional_comparison import ComparisonRow, RegionalComparison, ordering_value, rank_rows
from .regional_plots import RenderCache, crop_svg, curves_svg, overview_svg
from .schema import AnalysisBundle

# ---------------------------------------------------------------------------
# 문서 표시 정책: 숫자는 계산 결과 그대로, None은 0 대신 결측 기호/사유로 표시
# ---------------------------------------------------------------------------
RELATIONSHIP_LABELS = {
    "confirmed_same_structure": "동일 구조 확인", "confirmed_added": "구조 추가 확인",
    "confirmed_removed": "구조 소멸 확인", "unknown": "구조 대응 미확인",
}
CSS = """
:root {color-scheme:light;--ink:#16263a;--muted:#54677d;--line:#d9e1e9;--accent:#176dc1}
*{box-sizing:border-box}body{margin:0;background:#edf1f5;color:var(--ink);font:16px/1.6 system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:32px 24px}h1{font-size:32px;line-height:1.25}h2{font-size:25px}
h3{font-size:21px}h4{font-size:17px}a{color:var(--accent)}
p,dd,summary{overflow-wrap:anywhere}td{overflow-wrap:normal;word-break:normal}
header,.comparison{background:white;padding:28px;margin-bottom:24px;border:1px solid var(--line);border-radius:12px}
.comparison{border-top:4px solid var(--accent)}.muted{color:var(--muted)}.status{padding:10px 14px;background:#f1f5fa}
.region{border-top:1px solid var(--line);padding-top:20px;margin-top:24px}figure{margin:12px 0 22px}
figure svg{width:100%;height:auto;display:block}figcaption{font-size:14px;color:var(--muted)}
.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px;margin:12px 0 20px}
th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top}
th{background:#eef3f8;white-space:nowrap}td.num{white-space:nowrap;font-variant-numeric:tabular-nums}
.increase{color:#a7341d;font-weight:650}.decrease{color:#216b45;font-weight:650}
.columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}
dl{font-size:14px}dt{color:var(--muted)}dd{margin:0 0 8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 monospace}
nav a{display:inline-block;margin:4px 14px 4px 0}details{padding:12px;background:#f6f8fb;margin:12px 0}
.pill{display:inline-block;padding:3px 8px;background:#e8eef6;border-radius:5px;font-size:13px;margin:3px}
@media(max-width:700px){main{padding:12px}.comparison,header{padding:16px}.columns{grid-template-columns:1fr}
h1{font-size:26px}}
"""


def _json(value: object) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.8g}"


# HTML share는 읽기 쉬운 소수 네 자리만 표시한다. 원 정밀도는 JSON/CSV에 보존한다.
SHARE_DISPLAY_DECIMALS = 4


def _share(value: float | None) -> str:
    return "—" if value is None else f"{value:.{SHARE_DISPLAY_DECIMALS}f}"


def _bounds(window) -> str:
    if window is None:
        return "—"
    return f"x [{window.x0:g}, {window.x1:g}), y [{window.y0:g}, {window.y1:g}) µm"


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _regions(comparison: RegionalComparison):
    return [region for group in comparison.region_sets for region in group.regions]


def _measurement_rows(analysis: RegionalAnalysis) -> list[dict]:
    result = []
    for comparison in analysis.comparisons:
        for row in comparison.rows:
            for side in ("query", "reference"):
                observation = getattr(row, side)
                if observation.sample_id is None:
                    continue
                roi = observation.roi
                result.append(dict(
                    comparison_id=row.comparison_id, region_id=row.region_id, diameter_um=row.diameter_um,
                    row_kind="region" if row.region_id else "global",
                    side=side, sample_id=observation.sample_id,
                    source_kind=observation.source_kind, source_mode=observation.source_mode,
                    global_ca_value=observation.global_ca_value, global_ca_unit=observation.global_ca_unit,
                    unit_total_ca_um2=observation.unit_total_ca_um2,
                    roi_ca_um2=roi.roi_ca_um2 if roi else None,
                    contribution_share=roi.contribution_share if roi else None,
                    roi_ca_per_valid_area=roi.roi_ca_per_valid_area if roi else None,
                    valid_roi_area_um2=roi.valid_roi_area_um2 if roi else None,
                    status=roi.status if roi else "unavailable", reason=observation.roi_reason,
                    global_reason=observation.global_reason, location_status=observation.location_status,
                    share_reason=roi.share_reason if roi else None,
                    normalized_reason=roi.normalized_reason if roi else None,
                    requested_windows_um=_json([asdict(w) for w in roi.requested_windows_um]) if roi else None,
                    clipped_bounds_um=(
                        _json(asdict(roi.clipped_bounds_um)) if roi and roi.clipped_bounds_um else None
                    ),
                    validation_json=_json(asdict(roi.validation)) if roi else None,
                    provenance_json=_json(observation.provenance), selected_union_ca_um2=None,
                    outside_regions_ca_um2=None,
                ))
        for source in comparison.source_summaries:
            result.append(dict(
                comparison_id=comparison.comparison_id, row_kind="selected_union", sample_id=source.sample_id,
                diameter_um=source.diameter_um, unit_total_ca_um2=source.unit_total_ca_um2,
                selected_union_ca_um2=source.selected_union_ca_um2,
                outside_regions_ca_um2=source.outside_regions_ca_um2,
                status="unavailable" if source.reason else "available", reason=source.reason,
                validation_json=(
                    _json(asdict(source.union_measurement.validation)) if source.union_measurement else None
                ),
            ))
    return result


def _comparison_rows(analysis: RegionalAnalysis) -> list[dict]:
    result = []
    for comparison in analysis.comparisons:
        for row in comparison.rows:
            result.append(dict(
                comparison_id=row.comparison_id,
                region_id=row.region_id, diameter_um=row.diameter_um,
                query_sample_id=row.query.sample_id, reference_sample_id=row.reference.sample_id,
                relationship=comparison.relationship, delta_direction=row.delta_direction,
                query_unit_total_ca_um2=row.query.unit_total_ca_um2,
                reference_unit_total_ca_um2=row.reference.unit_total_ca_um2,
                query_roi_ca_um2=row.query.roi.roi_ca_um2 if row.query.roi else None,
                reference_roi_ca_um2=row.reference.roi.roi_ca_um2 if row.reference.roi else None,
                delta_total_ca_um2=row.delta_total_ca_um2, delta_roi_ca_um2=row.delta_roi_ca_um2,
                relative_delta_total=row.relative_delta_total, relative_delta_roi=row.relative_delta_roi,
                total_status="available" if row.total_reason is None else "unavailable",
                total_reason=row.total_reason,
                local_status="available" if row.local_reason is None else "unavailable",
                local_reason=row.local_reason,
                reasons=_json(row.reasons),
                query_context=(
                    _json(comparison.correspondence.query_context) if comparison.correspondence else None
                ),
                reference_context=(
                    _json(comparison.correspondence.reference_context) if comparison.correspondence else None
                ),
            ))
    return result


def _write_csv(path: Path, rows: Sequence[dict], required: Sequence[str]) -> None:
    fields = list(required) + sorted({key for row in rows for key in row} - set(required))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(json_safe(row) for row in rows)


# ---------------------------------------------------------------------------
# 사람이 읽는 위치 중심 HTML: 렌더러는 수치를 다시 계산하지 않는다
# ---------------------------------------------------------------------------


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _delta(value: float | None) -> str:
    cls = (
        "increase" if value is not None and value > 0
        else "decrease" if value is not None and value < 0 else "num"
    )
    return f'<span class="{cls}">{_number(value)}</span>'


def _curve_table(rows: Sequence[ComparisonRow]) -> str:
    output = []
    for row in sorted(rows, key=lambda r: r.diameter_um):
        q, r = row.query, row.reference
        qroi, rroi = q.roi, r.roi
        reason = row.local_reason if row.region_id else row.total_reason
        output.append([
            _number(row.diameter_um), _number(r.unit_total_ca_um2), _number(q.unit_total_ca_um2),
                _delta(row.delta_total_ca_um2),
            _number(rroi.roi_ca_um2 if rroi else None), _number(qroi.roi_ca_um2 if qroi else None),
                _delta(row.delta_roi_ca_um2),
            _share(rroi.contribution_share if rroi else None),
                _share(qroi.contribution_share if qroi else None),
            escape(reason or "available"), escape(" · ".join(row.reasons) or "—")])
    return _table(["지름 µm", "ref 전체 µm²", "query 전체 µm²", "Δ 전체 µm²",
                   "ref 지역 µm²", "query 지역 µm²",
                   "Δ 지역 µm²", "ref 기여율", "query 기여율",
                   "비교 상태/사유", "선정/변화 근거"], output)


def _coverage_table(rows: Sequence[ComparisonRow]) -> str:
    output = []
    for row in rows:
        for side in ("query", "reference"):
            observation = getattr(row, side)
            roi = observation.roi
            output.append([_number(row.diameter_um), side,
                           escape(observation.sample_id or "해당 쪽 자료/구조 없음"),
                           escape(observation.source_kind or "—"), escape(observation.source_mode or "—"),
                           _number(roi.valid_roi_area_um2 if roi else None),
                           _number(roi.roi_ca_per_valid_area if roi else None),
                           escape(_bounds(roi.clipped_bounds_um) if roi else "—"),
                           escape(observation.roi_reason or "available"),
                               escape(observation.location_status)])
    return _table(["지름 µm", "쪽", "sample", "원천 kind", "mode", "valid 면적 µm²", "CA/valid 면적 (1)",
                   "실제 clip bounds", "지역 사유", "위치 관측"], output)


def _ordering_table(comparison: RegionalComparison) -> str:
    """지름별 CA level/증가 순서를 모두 보여 준다. 상위 k로 지역을 생략하지 않는다."""
    regions = _regions(comparison)
    labels = {region.region_id: f"R{i}" for i, region in enumerate(regions, 1)}
    output = []
    for metric in ("roi_level", "roi_increase", "total_level", "total_increase"):
        candidates = [row for row in comparison.rows if (row.region_id is not None) == metric.startswith("roi")]
        for row in rank_rows(candidates, metric):
            value = ordering_value(row, metric)
            output.append([metric, _number(row.diameter_um), labels.get(row.region_id, "unit 전체"),
                           _number(value), escape(" · ".join(row.reasons) or "—")])
    return _table(["정렬 기준 · 지름별 내림차순", "지름 µm", "창",
                   "CA 또는 Δ µm²", "선정/변화 근거"], output)


def _analysis_ordering_html(analysis: RegionalAnalysis) -> str:
    """모든 pair/own의 크기별 CA 순서로 본문 위치에 연결한다. 지역 누락은 없다."""
    all_rows = [row for comparison in analysis.comparisons for row in comparison.rows]
    body = ['<section class="comparison"><h2>전체 목록 · 지름별 CA 정렬</h2>',
            '<p>level은 query own 값 우선, 없으면 관측 reference own 값입니다. '
            '증가량은 query − reference이며 결측은 끝에 남깁니다.</p>']
    for metric in ("total_level", "total_increase", "roi_level", "roi_increase"):
        candidates = [row for row in all_rows if (row.region_id is not None) == metric.startswith("roi")]
        output = []
        for row in rank_rows(candidates, metric):
            anchor = "c-" + _safe_id(row.comparison_id)
            if row.region_id:
                anchor = "r-" + _safe_id(row.comparison_id) + "-" + row.region_id
            link = f'<a href="#{anchor}">{escape(row.comparison_id)}</a>'
            output.append([_number(row.diameter_um), link, escape(row.region_id or "unit 전체"),
                           _number(row.query.unit_total_ca_um2), _number(row.reference.unit_total_ca_um2),
                           _number(row.query.roi.roi_ca_um2 if row.query.roi else None),
                           _number(row.reference.roi.roi_ca_um2 if row.reference.roi else None),
                           _delta(row.delta_total_ca_um2), _delta(row.delta_roi_ca_um2),
                           escape(" · ".join(row.reasons) or "—")])
        body += [f'<details><summary>{metric} · 지름별 내림차순</summary>',
                 _table(["지름 µm", "비교/위치 링크", "지역 ID", "query 전체 µm²", "ref 전체 µm²",
                         "query 지역 µm²", "ref 지역 µm²", "Δ 전체 µm²",
                         "Δ 지역 µm²", "근거"], output), "</details>"]
    return "\n".join(body) + "</section>"


def _side_html(comparison: RegionalComparison, side: str, samples: dict[str, dict]) -> str:
    sid = getattr(comparison, side + "_sample_id")
    pair = comparison.correspondence
    if sid is None:
        context = getattr(pair, side + "_context") if pair else None
        label = "명시 구조 부재" if comparison.relationship in ("confirmed_added",
            "confirmed_removed") else "자료/대응 미확인"
        description = escape(_json(context)) if context is not None else "명시 context 없음"
        return (f'<section><h4>{side} · {label}</h4><p>CA는 0으로 대체하지 않습니다.</p>'
                f'<pre>{description}</pre></section>')
    sample = samples[sid]
    fields = [("sample", sid), ("제품/설계", f'{sample["product_id"]} / {sample["design_version"]}'),
              ("전극/layer", f'{sample["electrode_a_role"]} · {sample["layer_a"]} / '
                             f'{sample["electrode_b_role"]} · {sample["layer_b"]}'),
              ("불량/공정", f'{sample["failure_type"]} / {sample["assigned_process_layer"]}'),
              ("자료", "합성/demo" if sample["synthetic"] else "입력 생산자 자료")]
    content = "".join(f"<dt>{escape(k)}</dt><dd>{escape(str(v))}</dd>" for k, v in fields)
    return f'<section><h4>{side}</h4><dl>{content}</dl></section>'


def _write_svg(path: Path, svg: str, cache: RenderCache) -> None:
    """정적 그림에는 그 그림이 참조한 PNG만 넣는다. HTML은 공유 defs를 한 번만 보유한다."""
    tokens = re.findall(r'href="#(plane-[a-f0-9]+)"', svg)
    boundary = svg.index(">") + 1
    standalone = svg[:boundary] + cache.definitions(tokens) + svg[boundary:]
    path.write_text(standalone, encoding="utf-8")


def _figure(svg: str, caption: str, path: Path, out: Path) -> str:
    relative = path.relative_to(out).as_posix()
    return f'<figure>{svg}<figcaption>{escape(caption)} · <a href="{relative}">SVG 파일</a></figcaption></figure>'


def _comparison_html(comparison: RegionalComparison, samples: dict[str, dict], bundles: Sequence[AnalysisBundle],
                     out: Path, figures: Path, written: list[Path], cache: RenderCache) -> str:
    cid = _safe_id(comparison.comparison_id)
    title = RELATIONSHIP_LABELS[comparison.relationship]
    body = [f'<article class="comparison" id="c-{cid}"><h2>{escape(comparison.comparison_id)} · {title}</h2>',
            ('<div class="columns">' + _side_html(comparison, "query", samples)
             + _side_html(comparison, "reference", samples) + "</div>"),
            f'<p class="status">전체 비교: {escape(comparison.identity_reason or "구조 확인됨")} · '
            f'국부 등록: {escape(comparison.registration_reason or "명시 등록됨")}</p>',
            '<section data-stage="overview"><h3>전체 위치 · 지역 label</h3>']
    overview = overview_svg(comparison, bundles, cache=cache)
    path = figures / f"{cid}-overview.svg"
    _write_svg(path, overview, cache)
    written.append(path)
    body += [_figure(overview, "파랑/보라 전극 · 주황 양수 support · 붉은 정량 창 · "
                    "좌상단 원점, x 오른쪽/y 아래쪽. "
                    "배경은 첫 원천 valid mask, 사선은 미확인/밖 · "
                    "지름별 coverage는 표에서 확인", path, out), "</section>"]
    regions = _regions(comparison)
    if not regions:
        for row in comparison.rows:
            if row.region_id is not None:
                continue
            for observation in (row.query, row.reference):
                if observation.sample_id is None:
                    continue
                if observation.location_status == "observed_empty":
                    message = ("관측된 빈 support · 선택 union은 0입니다. "
                               "전체 위험 0을 뜻하지 않습니다.")
                else:
                    message = "위치 자료 미확인 · 선택 union/창 밖 잔여는 null입니다."
                label = f"{observation.sample_id} · 지름 {_number(row.diameter_um)} µm: {message}"
                body.append(f'<p class="status">정량 창 없음: {escape(label)}</p>')
    for i, region in enumerate(regions, 1):
        rows = [row for row in comparison.rows if row.region_id == region.region_id]
        body += [f'<section class="region" id="r-{cid}-{region.region_id}" data-stage="region">'
                 f'<h3>R{i} · {escape(_bounds(region.bounds_um))}</h3>',
                 f'<p class="muted">frame {escape(region.frame_id)} · ID {region.region_id} · '
                 f'표시 crop {escape(_bounds(region.display_bounds_um))}</p>']
        plots = (("crops", crop_svg(comparison, region, bundles, cache=cache),
                  "동일 물리 창 · 붉은 경계만 CA 적분 · 여백은 표시 전용"),
                 ("curves", curves_svg(rows, include_global=False),
                  "지름별 지역 CA · query − reference · 전체 곡선은 비교마다 한 번 표시"))
        for kind, svg, caption in plots:
            path = figures / f"{cid}-{region.region_id}-{kind}.svg"
            _write_svg(path, svg, cache)
            written.append(path)
            body.append(_figure(svg, caption, path, out))
        body += [_curve_table(rows), "<details><summary>valid coverage와 별도 면적 정규화</summary>",
            _coverage_table(rows), "</details></section>"]
    global_rows = [row for row in comparison.rows if row.region_id is None]
    body += ['<section data-stage="global"><h3>지름별 unit/source 전체 CA</h3>']
    svg = curves_svg(global_rows, include_roi=False)
    path = figures / f"{cid}-global-curves.svg"
    _write_svg(path, svg, cache)
    written.append(path)
    body.append(_figure(svg, "원래 전체 CA 곡선 · 비교마다 한 번 표시", path, out))
    body.append(_curve_table(global_rows))
    raw = [
        [_number(row.diameter_um), side, escape(observation.global_ca_unit or "—"),
         _number(observation.global_ca_value), escape(observation.global_reason or "available")]
        for row in global_rows for side, observation in (("query", row.query), ("reference", row.reference))
    ]
    body += [_table(["지름 µm", "쪽", "원 CA 단위", "원 CA 값", "관측/단위 사유"], raw), "</section>"]
    summaries = [[escape(s.sample_id), _number(s.diameter_um), _number(s.unit_total_ca_um2),
                  _number(s.selected_union_ca_um2), _number(s.outside_regions_ca_um2),
                  escape(s.reason or "available")]
                 for s in comparison.source_summaries]
    details = {key: value for key, value in asdict(comparison).items()
               if key not in ("rows", "source_summaries")}
    body += ["<details><summary>지름별 CA level/증가량 정렬 · 모든 창 유지</summary>",
             _ordering_table(comparison), "</details>", "<h3>source별 선택 union과 창 밖 잔여</h3>",
             _table(["sample", "지름 µm", "unit total µm²", "선택 union µm²", "창 밖 µm²",
                     "사유"], summaries),
             "<p class=\"muted\">서로 다른 pair total이나 정규화 비율을 합산하지 않습니다.</p>",
             "<details><summary>명시 대응·좌표·생산자 provenance와 unavailable 원인</summary>",
             f'<pre>{escape(_json(details))}</pre></details></article>']
    return "\n".join(body)


# ---------------------------------------------------------------------------
# 출력 IO: 같은 결과 객체에서 machine/HTML/static 그림을 함께 작성한다
# ---------------------------------------------------------------------------


def write_report(analysis: RegionalAnalysis, bundles: Sequence[AnalysisBundle],
    output_dir: Path) -> tuple[Path, ...]:
    """계산 결과와 원본 2D 표시 자료를 로컬 HTML/JSON/CSV/SVG로 저장한다.

    좌표는 명시 좌상단/um, CA는 계약의 원 값이며 renderer가 측정/등록을 재수행하지
    않는다. None은 JSON null/CSV 공란+사유다. HTML에 모든 그림을 inline하여 단독 이동
    가능하며 입력/기존 파일 삭제/외부 IO/JS는 없다. 생성 파일 목록을 반환한다.
    """
    out = Path(output_dir)
    figures = out / "figures/regional"
    figures.mkdir(parents=True, exist_ok=True)
    names = ("regional_report.html", "regional_summary.json",
        "regional_measurements.csv", "regional_comparisons.csv")
    outputs = [out / name for name in names]
    samples = {sample["sample_id"]: sample for sample in analysis.samples}
    cache = RenderCache()
    parts = [_comparison_html(c, samples, bundles, out, figures,
        outputs, cache) for c in analysis.comparisons]
    definitions = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                   'width="0" height="0" aria-hidden="true" style="position:absolute">'
                   + cache.definitions() + '</svg>')
    navigation = "".join(f'<a href="#c-{_safe_id(c.comparison_id)}">{escape(c.comparison_id)}</a>'
                         for c in analysis.comparisons)
    notes = "".join(f"<p>{escape(note)}</p>" for note in analysis.notes)
    counts = {status: sum(v["status"] == status for v in analysis.source_validations)
              for status in ("available", "unavailable")}
    header = (f'<header><h1>지역 CA · 위치 중심 비교</h1>{notes}'
              f'<p class="status">정량 원천 available {counts["available"]} · '
              f'unavailable {counts["unavailable"]}</p>'
              '<nav><a href="regional_summary.json">JSON</a><a href="regional_measurements.csv">측정 CSV</a>'
              f'<a href="regional_comparisons.csv">비교 CSV</a></nav><nav>{navigation}</nav>'
              f'<p class="muted">보고 fingerprint {analysis.fingerprint}</p></header>')
    html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>지역 CA 위치 비교</title>'
            f'<style>{CSS}</style></head><body>{definitions}<main>{header}'
             + "\n".join(parts) + _analysis_ordering_html(analysis) +
            '<details><summary>전체 원천 보존 gate 및 누락 사유</summary><pre>'+
             escape(_json(analysis.source_validations)) + "</pre></details></main></body></html>")
    outputs[0].write_text(html, encoding="utf-8")
    payload = asdict(analysis) | {"outputs": [p.relative_to(out).as_posix() for p in outputs]}
    outputs[1].write_text(json.dumps(json_safe(payload), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8")
    _write_csv(outputs[2], _measurement_rows(analysis), ("comparison_id", "region_id", "sample_id",
        "diameter_um", "row_kind"))
    _write_csv(outputs[3], _comparison_rows(analysis), ("comparison_id", "region_id", "diameter_um",
        "query_sample_id", "reference_sample_id"))
    return tuple(outputs)
