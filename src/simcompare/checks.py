"""validate-data 보고서: 입력 누락, 실제 크기, hotspot–layout 중복, 교락, CAA 적용 가능 여부.

학습 전에 데이터의 한계를 먼저 드러내기 위한 점검이며 자료를 수정하지 않는다.
"""
from __future__ import annotations

import numpy as np

from simcompare.caa import CAAError, interpret_fab, select_distribution
from simcompare.split import confounding_report, group_ids

OVERLAP_THRESHOLD = 0.8


def _overlap(enc, i: int) -> float | None:
    """한 샘플에서 hotspot 평균 이미지와 layout 합성 이미지의 상관계수를 구한다.

    가공 hotspot이 layout 형상을 그대로 담고 있는지 확인하는 용도다. 계산할 수 없으면 None.
    """
    hot = enc.hotspot_present[i] > 0
    lay = enc.layout_present[i] > 0
    if not hot.any() or not lay.any():
        return None
    hotspot = enc.hotspot[i, hot].mean(axis=0)
    layout = enc.layout[i, lay].max(axis=0)
    valid = (enc.hotspot_valid[i, hot].min(axis=0) > 0.5) & (enc.layout_valid[i, lay].min(axis=0) > 0.5)
    a, b = hotspot[valid], layout[valid]
    if a.size < 2 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def run_checks(loaded, enc, cfg) -> dict:
    """데이터 점검 결과를 JSON으로 저장할 수 있는 dict로 반환한다.

    open 불량의 두 번째 전극은 누락이 아닌 '해당 없음' 건수로 따로 센다.
    Fab과 제품군 교락 상태, 독립 그룹 수, 크기 격자 관측률도 함께 담는다.
    """
    meta = enc.meta
    bundles = loaded.bundles
    shorts = ~meta["is_open"].to_numpy(bool)
    missing = {
        "layout_a": int((enc.layout_present[:, 0] == 0).sum()),
        "layout_b_short": int(((enc.layout_present[:, 1] == 0) & shorts).sum()),
        # open의 두 번째 전극은 누락이 아니라 해당 없음으로 따로 센다.
        "open_b_not_applicable": int((~shorts).sum()),
        "fab": int((~enc.fab_mask.any(axis=1) & ~enc.fab_total_mask[:, 0]).sum()),
        "geometry": int((~enc.geom_mask.any(axis=1)).sum()),
    }
    for j, size in enumerate(cfg.data.hotspot_sizes):
        missing[f"hotspot@{size:g}"] = int((enc.hotspot_present[:, j] == 0).sum())
    for j, size in enumerate(cfg.data.ca_grid):
        missing[f"ca@{size:g}"] = int((~enc.ca_mask[:, j]).sum())

    overlap = []
    for i, sid in enumerate(meta["sample_id"]):
        corr = _overlap(enc, i)
        if corr is not None:
            overlap.append({"sample_id": sid, "corr": round(corr, 4), "flagged": corr > OVERLAP_THRESHOLD})

    caa_issues = []
    for bundle in bundles:
        try:
            dist = select_distribution(bundle, loaded.distributions, cfg.caa.process_layer_overrides)
            interpret_fab(dist, cfg.caa.rule, mode=cfg.mode, confirmed=cfg.caa.confirmed)
        except CAAError as exc:
            caa_issues.append({"sample_id": bundle.sample_id, "reason": str(exc)})

    groups = group_ids(meta)
    ca_values = enc.ca_log[enc.ca_mask]
    ca_raw = np.power(10.0, ca_values) if ca_values.size else np.array([], dtype=float)
    comparison_groups = {
        (
            row["failure_type"],
            tuple(sorted(x for x in (row["electrode_a_role"], row["electrode_b_role"]) if x != "N/A")),
            tuple(sorted(x for x in (row["layer_a"], row["layer_b"]) if x != "N/A")),
        )
        for _, row in meta.iterrows()
    }
    return {
        "mode": cfg.mode,
        "synthetic": any(b.provenance.is_synthetic for b in bundles),
        "counts": {
            "n_bundles": len(meta),
            "n_products": int(meta["product_id"].nunique()),
            "n_lineages": int(meta["design_lineage_id"].nunique()),
            "n_families": int(meta["product_family"].nunique()),
            "n_fabs": int(meta["fab_id"].nunique()),
            "by_failure_type": {k: int(v) for k, v in meta["failure_type"].value_counts().sort_index().items()},
        },
        "missing_inputs": missing,
        "physical_scale": {
            "missing_sample_ids": sorted(meta.loc[~meta["has_physical_scale"].astype(bool), "sample_id"]),
            "message": "이 샘플들은 실제 크기 정보가 없어 물리 길이 비교가 불가능하며 픽셀 상대 비교만 가능합니다",
        },
        "hotspot_layout_overlap": {
            "items": overlap,
            "n_flagged": sum(item["flagged"] for item in overlap),
            "threshold": OVERLAP_THRESHOLD,
            "message": (
                "가공 hotspot 이미지는 layout 내용과 겹칠 수 있어 이미지 특징이 "
                "형상을 이중으로 반영할 수 있습니다"
            ),
        },
        "confounding": confounding_report(meta, "fab_id", "product_family"),
        "independent_groups": int(len(set(groups))),
        "comparison_groups": int(len(comparison_groups)),
        "split_feasible": len(set(groups)) >= 2,
        "caa_applicability": caa_issues,
        "grid_coverage": {
            f"ca@{size:g}": round(float(enc.ca_mask[:, j].mean()), 4)
            for j, size in enumerate(cfg.data.ca_grid)
        },
        "ca_value_range": {
            "valid_contract": [0.0, 1.0],
            "observed_min": None if ca_raw.size == 0 else float(ca_raw.min()),
            "observed_max": None if ca_raw.size == 0 else float(ca_raw.max()),
            "n_above_typical_0_03": int((ca_raw > 0.03).sum()),
            "message": "0.03 초과는 오류가 아니지만 현재 알려진 일반 범위보다 높아 원자료 확인이 필요합니다",
        },
        "warnings": list(loaded.warnings) + list(enc.notes),
    }
