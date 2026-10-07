"""합성 생성기를 재사용해 출력 수치·결측·좌표·offline 계약을 검사한다.

브라우저 시각 PASS나 실제 정확도 검증은 하지 않는다. 보고 반례는 아래 테스트,
합성 입력 구조 변경은 scripts/regional_demo.py의 build_demo를 수정한다.
"""
import csv
from dataclasses import replace
import json
import re
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from scripts.regional_demo import build_demo, write_demo_inputs
from simcompare.regional import analyze_regions, load_regional_inputs, write_regional_analysis
from simcompare.regional_plots import curves_svg, overview_svg
from simcompare.regional_report import write_report


@pytest.fixture
def regional_case(tmp_path):
    inputs = build_demo()
    cfg = write_demo_inputs(inputs, tmp_path)
    bundles, pairs = load_regional_inputs(inputs.bundles, cfg)
    return tmp_path, inputs, cfg, bundles, analyze_regions(bundles, cfg, pairs)


# ---------------------------------------------------------------------------
# 동일 결과 객체에서 모든 출력/원천 추적/escape를 확인한다
# ---------------------------------------------------------------------------
def test_regional_report_exports_html_json_csv_and_local_figures(regional_case):
    out, inputs, cfg, bundles, analysis = regional_case
    result = write_regional_analysis(inputs.bundles, cfg, out)
    names = {p.name for p in result.outputs}
    assert {"regional_report.html", "regional_summary.json", "regional_measurements.csv",
            "regional_comparisons.csv"} <= names
    assert all(p.is_file() for p in result.outputs)
    raw = (out/"regional_summary.json").read_text()
    assert "NaN" not in raw and "Infinity" not in raw
    summary = json.loads(raw)
    assert summary["schema_version"] == "1"
    assert summary["fingerprint"] == analysis.fingerprint
    assert any(v["status"] == "available" for v in summary["source_validations"])
    assert any(v["reason"] == "missing_source" for v in summary["source_validations"])
    gate = next(v["validation"] for v in summary["source_validations"] if v["status"] == "available")
    assert gate["residual_um2"] == 0.
    assert gate["conservation_rtol"] == cfg.conservation_rtol
    assert gate["provenance"]["manifest_sha256"]
    measurements = list(csv.DictReader((out/"regional_measurements.csv").open()))
    local = next(row for row in measurements if row["row_kind"] == "region" and row["roi_ca_um2"])
    assert local["region_id"] and json.loads(local["requested_windows_um"])
    assert json.loads(local["provenance_json"])["source_files"]
    assert "outside_regions_ca_um2" in measurements[0]
    html = (out/"regional_report.html").read_text()
    for link in re.findall(r'href="([^"]+)"', html):
        if link.startswith(("#", "data:")):
            continue
        assert (out/link).is_file()
    assert summary["outputs"] == [str(p.relative_to(out)) for p in result.outputs]
    assert result.run_record["conservation_available"] > 0


def test_report_is_self_contained_and_escapes_input_labels(regional_case):
    out, _, cfg, bundles, analysis = regional_case
    from fixtures.regional.builders import make_bundle, make_pair
    attack = '<script>x</script><img onerror="x">'
    q = make_bundle(attack, {1.: [[1, 0]]})
    r = make_bundle("reference", {1.: [[1, 0]]})
    q.product_id = attack
    pair = make_pair(q, r)
    pair = replace(pair, comparison_id=attack, registration=replace(pair.registration, frame_id=attack))
    bundles = [q, r]
    modified = analyze_regions(bundles, cfg, [pair])
    write_report(modified, bundles, out)
    html = (out/"regional_report.html").read_text()
    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html
    assert "data:image/png;base64," in html and "<svg" in html
    assert not re.search(r'(?:src|href)=["\']https?://', html)
    assert "@import" not in html and "<link" not in html
    assert 'lang="ko"' in html
    from html.parser import HTMLParser
    class CheckMarkup(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert tag not in ("script", "img", "link")
            assert all(not key.lower().startswith("on") for key, value in attrs)
    CheckMarkup().feed(html)
    assert attack not in html
    for svg in (out / "figures/regional").glob("*.svg"):
        ET.fromstring(svg.read_text())


# ---------------------------------------------------------------------------
# 수치/미관측 반례: 총량과 국부 방향을 별도로 보존한다
# ---------------------------------------------------------------------------
def test_report_shows_total_improvement_and_local_worsening_together(regional_case):
    out, _, _, bundles, analysis = regional_case
    comparison = next(c for c in analysis.comparisons if c.comparison_id == "total-down-local-up")
    rows = [r for r in comparison.rows if r.diameter_um == 1. and r.region_id]
    row = next(r for r in rows if r.delta_roi_ca_um2 == 10.)
    assert (row.reference.unit_total_ca_um2, row.query.unit_total_ca_um2) == (100., 80.)
    assert (row.reference.roi.roi_ca_um2, row.query.roi.roi_ca_um2) == (20., 30.)
    assert row.delta_total_ca_um2 == -20.
    assert row.reference.roi.contribution_share == .2
    assert row.query.roi.contribution_share == .375
    write_report(analysis, bundles, out)
    csv_rows = list(csv.DictReader((out/"regional_comparisons.csv").open()))
    exported = next(r for r in csv_rows if r["region_id"] == row.region_id and r["diameter_um"] == "1.0")
    assert float(exported["delta_roi_ca_um2"]) == 10.
    assert float(exported["delta_total_ca_um2"]) == -20.
    html = (out/"regional_report.html").read_text()
    assert '<span class="increase">10</span>' in html
    assert '<span class="decrease">-20</span>' in html
    assert "0.375" in html and "roi_increase" in html
    assert "전체 목록 · 지름별 CA 정렬" in html
    ordering = html.split("total_level · 지름별 내림차순", 1)[1].split("</details>", 1)[0]
    assert ordering.index("total-down-local-up") < ordering.index("moved-new-disappeared")
    assert html.index('data-stage="overview"') < html.index('data-stage="region"')


def test_report_breaks_curves_at_missing_sizes(regional_case):
    _, _, cfg, bundles, _ = regional_case
    query = next(b for b in bundles if b.sample_id == "nominal-query")
    observed = query.ca.observed.copy()
    observed[1] = False
    changed = replace(query, ca=replace(query.ca, observed=observed),
                      ca_spatial_sources=[s for s in query.ca_spatial_sources if s.diameter_um != 2.])
    modified = [changed if b.sample_id == query.sample_id else b for b in bundles]
    inputs = build_demo()
    from simcompare.correspondence import parse_correspondence
    pairs = parse_correspondence(inputs.correspondence, modified)
    comparison = next(c for c in analyze_regions(modified, cfg, pairs).comparisons
                      if c.comparison_id == "total-down-local-up")
    rows = [r for r in comparison.rows if r.region_id is None]
    assert rows[1].query.unit_total_ca_um2 is None
    root = ET.fromstring(curves_svg(rows))
    segments = [e for e in root.iter() if e.tag.endswith("polyline")
                and e.attrib.get("data-series") == "query" and e.attrib.get("data-metric") == "total"]
    assert len(segments) == 2
    assert all(len(e.attrib["points"].split()) == 1 for e in segments)


def test_unregistered_report_uses_separate_coordinate_panels(regional_case):
    out, _, _, bundles, analysis = regional_case
    comparison = next(c for c in analysis.comparisons if c.comparison_id == "unregistered")
    assert all(r.delta_roi_ca_um2 is None for r in comparison.rows)
    svg = overview_svg(comparison, bundles)
    assert svg.count("별도 local 좌표") == 2
    assert "100, 130" in svg and "10, 40" in svg
    assert "명시 공통 좌표" not in svg
    write_report(analysis, bundles, out)
    html = (out/"regional_report.html").read_text()
    assert comparison.registration_reason in html
    assert "구조 추가 확인" in html and "구조 소멸 확인" in html
    assert "CA는 0으로 대체하지 않습니다" in html


def test_regional_report_adds_no_fab_yield_or_tree_scores(regional_case):
    out, _, _, bundles, analysis = regional_case
    write_report(analysis, bundles, out)
    payload = json.loads((out/"regional_summary.json").read_text())
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value))
        return set()
    assert not {"caa", "yield", "tree_score", "shap", "fab_weight", "ae_score"} & keys(payload)
    assert "합성/demo" in (out/"regional_report.html").read_text()


def test_regional_report_does_not_label_pair_global_geometry_as_local(regional_case):
    out, _, _, bundles, analysis = regional_case
    write_report(analysis, bundles, out)
    html = (out/"regional_report.html").read_text()
    assert "pair:min_gap" not in html and "neighborhood_geometry" not in html
    assert "valid coverage" in html and "정량" in html


def test_regional_analysis_creates_no_layout_edits(regional_case):
    out, inputs, cfg, _, _ = regional_case
    snapshots = [image.array.copy() for b in inputs.bundles for image in b.layouts+b.hotspots]
    result = write_regional_analysis(inputs.bundles, cfg, out)
    assert not any("edit" in p.name or "variant" in p.name for p in result.outputs)
    for actual, before in zip([im.array for b in inputs.bundles for im in b.layouts+b.hotspots], snapshots):
        np.testing.assert_array_equal(actual, before)
    assert all(not b.ca_spatial_sources for b in inputs.bundles)
