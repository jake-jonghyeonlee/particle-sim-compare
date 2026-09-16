"""validate-data report: coverage, scale, overlap, confounding and CAA applicability."""
from __future__ import annotations

import numpy as np

from simcompare.caa import CAAError, interpret_fab, select_distribution
from simcompare.split import confounding_report, group_ids

OVERLAP_THRESHOLD = 0.8


def _overlap(enc, i: int) -> float | None:
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
    meta = enc.meta
    bundles = loaded.bundles
    shorts = ~meta["is_open"].to_numpy(bool)
    missing = {
        "layout_a": int((enc.layout_present[:, 0] == 0).sum()),
        "layout_b_short": int(((enc.layout_present[:, 1] == 0) & shorts).sum()),
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
        "split_feasible": len(set(groups)) >= 2,
        "caa_applicability": caa_issues,
        "grid_coverage": {
            f"ca@{size:g}": round(float(enc.ca_mask[:, j].mean()), 4)
            for j, size in enumerate(cfg.data.ca_grid)
        },
        "warnings": list(loaded.warnings) + list(enc.notes),
    }
