"""기존 analyze와 지역 보고의 경계·fingerprint·원본 배열 사용을 검사한다.

모델 정확도/수율 검증은 하지 않는다. 기존 산출물 보존은 아래 integration 테스트,
보고 전용 입력은 합성 builder와 regional 진입점을 수정한다.
"""
import copy
import json

import pytest

from scripts.regional_demo import build_demo, write_demo_inputs
from simcompare.pipeline import _training_fingerprint, run_analyze, run_train
from simcompare.regional import analyze_regions, load_regional_inputs


@pytest.fixture
def trained_cfg(demo_cfg):
    demo_cfg.train.epochs = 1
    demo_cfg.train.device = "cpu"
    run_train(demo_cfg)
    return demo_cfg


# ---------------------------------------------------------------------------
# enabled/disabled가 AE/search/legacy 산출물을 바꾸지 않는다
# ---------------------------------------------------------------------------
def test_analyze_with_unavailable_regional_sources_keeps_legacy_outputs(trained_cfg):
    from pathlib import Path
    cfg = trained_cfg
    result = run_analyze(cfg)
    out = Path(cfg.output_dir)
    for name in ("neighbors.csv", "ranking.csv", "review_cards.json", "edit_candidates.json"):
        assert (out/name).is_file()
    assert str(out/"regional_report.html") in result["outputs"]
    summary = json.loads((out/"regional_summary.json").read_text())
    assert summary["source_validations"]
    assert all(v["status"] == "unavailable" for v in summary["source_validations"])
    assert any(row["query"]["global_ca_value"] is not None
               for comparison in summary["comparisons"] for row in comparison["rows"])
    record = json.loads((out/"run_record.json").read_text())
    assert record["regional"]["source_unavailable"] > 0
    assert "training_fingerprint" in record and "loo_stability" in record


def test_regional_disabled_preserves_previous_analysis(trained_cfg):
    from pathlib import Path
    cfg = trained_cfg
    cfg.analysis.regional.enabled = False
    result = run_analyze(cfg)
    out = Path(cfg.output_dir)
    assert not (out/"regional_report.html").exists()
    assert not (out/"figures/regional").exists()
    assert all(not Path(path).name.startswith("regional_") for path in result["outputs"])
    assert json.loads((out/"run_record.json").read_text())["regional"] == {"enabled":False}
    assert (out/"edit_candidates.json").is_file()


def test_regional_analysis_does_not_change_search_or_legacy_edits(trained_cfg):
    from pathlib import Path
    cfg = trained_cfg
    cfg.analysis.regional.enabled = False
    run_analyze(cfg)
    out = Path(cfg.output_dir)
    names = ("neighbors.csv", "ranking.csv", "neighborhood_comparison.csv", "neighborhood_geometry.csv",
             "edit_candidates.json", "edit_candidates.csv", "variant_comparison.csv", "review_cards.json")
    before = {name:(out/name).read_bytes() for name in names}
    cfg.analysis.regional.enabled = True
    run_analyze(cfg)
    assert before == {name:(out/name).read_bytes() for name in names}


# ---------------------------------------------------------------------------
# 학습 입력 resize/fingerprint와 보고 입력을 독립적으로 유지한다
# ---------------------------------------------------------------------------
def test_regional_results_do_not_depend_on_model_image_size(tmp_path, demo_cfg, monkeypatch):
    inputs = build_demo()
    regional_cfg = write_demo_inputs(inputs, tmp_path)
    bundles, pairs = load_regional_inputs(inputs.bundles, regional_cfg)
    demo_cfg.analysis.regional = regional_cfg
    demo_cfg.data.image_size = [8, 8]
    from simcompare.dataset import encode_bundles
    demo_cfg.data.hotspot_sizes = [1., 2., 3.]
    small = encode_bundles(copy.deepcopy(bundles), {}, demo_cfg)
    import simcompare.regional_comparison as comparison_module
    original = comparison_module._measure_union_validated
    measured_shapes = []

    def measure_original(source, *args, **kwargs):
        measured_shapes.append(source.density.shape)
        assert source.density.shape == inputs.bundles[0].hotspots[0].array.shape
        assert source.density.shape not in ((8, 8), (128, 128))
        return original(source, *args, **kwargs)

    monkeypatch.setattr(comparison_module, "_measure_union_validated", measure_original)
    first = analyze_regions(bundles, demo_cfg.analysis.regional, pairs)
    demo_cfg.data.image_size = [128, 128]
    large = encode_bundles(copy.deepcopy(bundles), {}, demo_cfg)
    second = analyze_regions(bundles, demo_cfg.analysis.regional, pairs)
    assert measured_shapes
    assert first == second
    assert small.hotspot.shape[-2:] == (8, 8) and large.hotspot.shape[-2:] == (128, 128)
    assert small.hotspot.sum() != large.hotspot.sum()
    comparison = next(c for c in first.comparisons if c.comparison_id == "total-down-local-up")
    assert any(r.delta_roi_ca_um2 == 10. for r in comparison.rows if r.diameter_um == 1.)


def test_external_regional_inputs_do_not_change_training_fingerprint(tmp_path, demo_cfg):
    inputs = build_demo()
    cfg = write_demo_inputs(inputs, tmp_path/"external")
    demo_cfg.analysis.regional = cfg
    training = _training_fingerprint(demo_cfg)
    bundles, pairs = load_regional_inputs(inputs.bundles, cfg)
    first = analyze_regions(bundles, cfg, pairs)
    from pathlib import Path
    path = Path(cfg.correspondence_file)
    changed = copy.deepcopy(inputs.correspondence)
    changed["pairs"][0]["evidence"] = "수정된 합성 명시 대응 근거"
    path.write_text(json.dumps(changed), encoding="utf-8")
    bundles, pairs = load_regional_inputs(inputs.bundles, cfg)
    second = analyze_regions(bundles, cfg, pairs)
    assert first.fingerprint != second.fingerprint
    assert _training_fingerprint(demo_cfg) == training
