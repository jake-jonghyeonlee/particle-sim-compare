"""예시 어댑터 형식의 합성 demo 데이터 생성기.

생성되는 CA·hotspot·Fab 값은 단순한 수식으로 만든 값이며 물리 시뮬레이션 결과가 아니다.
코드 경로를 확인하기 위한 다음 조건을 포함한다.
- 서로 다른 unit-pixel 크기와 실제 크기가 없는 제품(P6)
- 제품 간 대응되는 전극 역할, open과 short
- 일부 hotspot·형상 수치·CA 크기 누락, 다른 CA 크기 격자(P3)
- 같은 설계 계열의 변경 버전(P1 v2)
- 형상은 비슷하지만 위험 수치가 다른 제품 쌍(P2, P5)
- 일부 겹치는 Fab과 제품군 분포
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image

DISCLAIMER = (
    "합성 DEMO 데이터입니다. CA·hotspot·Fab 값은 단순한 수식으로 만든 것으로 "
    "물리 시뮬레이션 결과가 아니며 연구 근거로 사용하면 안 됩니다."
)

# 제품, 버전, 제품군, Fab, 가로, 세로(None=실제 크기 없음), 전극 간격, 선폭, 위험 계수, 이미지 높이(px)
_PRODUCTS = [
    ("P1", "v1", "F1", "FAB_A", 60.0, 90.0, 6.0, 4.0, 1.0, 72),
    ("P1", "v2", "F1", "FAB_A", 60.0, 90.0, 8.0, 4.0, 1.0, 72),
    ("P2", "v1", "F1", "FAB_A", 45.0, 70.0, 5.0, 3.0, 1.0, 56),
    ("P3", "v1", "F2", "FAB_B", 80.0, 120.0, 10.0, 5.0, 1.0, 96),
    ("P4", "v1", "F2", "FAB_B", 50.0, 75.0, 7.0, 4.0, 1.0, 60),
    ("P5", "v1", "F3", "FAB_A", 70.0, 100.0, 5.2, 3.0, 3.0, 80),
    ("P6", "v1", "F3", "FAB_B", None, None, 9.0, 4.0, 1.0, 64),
]
_FAILURES = [
    # 코드, 불량 종류, 전극 a 역할, a layer, 전극 b 역할, b layer, 적용 공정 layer
    ("OPEN", "lateral_open", "data", "M2", "N/A", "N/A", "M2"),
    ("LSHORT", "lateral_short", "data", "M2", "pixel_electrode", "ITO", "ITO"),
    ("VSHORT", "vertical_short", "gate", "M1", "data", "M2", "M2"),
]
_BIN_LOWER = [0.5, 1.5, 3.0, 6.0, 12.0]
_BIN_UPPER = [1.5, 2.5, 5.0, 10.0, 20.0]
_FAB_BASE = {
    "FAB_A": np.array([0.35, 0.30, 0.20, 0.10, 0.05]),
    "FAB_B": np.array([0.10, 0.20, 0.30, 0.25, 0.15]),
}
HOTSPOT_SIZES = [2.0, 4.0, 8.0]
CA_GRID = [1.0, 2.0, 4.0, 8.0, 16.0]


def _blur(image: np.ndarray, passes: int = 3) -> np.ndarray:
    """간단한 5점 평균 블러를 passes번 반복한다."""
    out = image.astype(np.float64)
    for _ in range(passes):
        padded = np.pad(out, 1, mode="edge")
        out = (
            padded[:-2, 1:-1] + padded[2:, 1:-1] + padded[1:-1, :-2]
            + padded[1:-1, 2:] + padded[1:-1, 1:-1]
        ) / 5.0
    return out


def _layouts(code: str, h: int, w: int, gap_px: int, lw_px: int) -> list[np.ndarray]:
    """불량 종류별 전극 이미지(데이터선, 화소 전극, 게이트선)를 이진 배열로 만든다."""
    x0 = max(1, w // 4)
    data = np.zeros((h, w), dtype=np.float32)
    data[:, x0:x0 + lw_px] = 1.0
    if code == "OPEN":
        return [data]
    if code == "LSHORT":
        pixel = np.zeros((h, w), dtype=np.float32)
        start = min(w - 2, x0 + lw_px + gap_px)
        pixel[h // 8: h - h // 8, start: w - 2] = 1.0
        return [data, pixel]
    gate = np.zeros((h, w), dtype=np.float32)
    y0 = h // 2
    gate[y0:y0 + lw_px, :] = 1.0
    return [gate, data]


def _hotspot(layouts: list[np.ndarray], code: str, size: float, risk: float) -> np.ndarray:
    """전극이 가까운 영역을 강조한 가공 hotspot 강도 이미지를 만든다(위험 계수와 이물 크기에 비례)."""
    if code == "OPEN":
        base = layouts[0]
    elif code == "LSHORT":
        a, b = (_blur(x, passes=4) for x in layouts)
        base = a * b * 4.0
    else:
        base = layouts[0] * layouts[1]
    passes = int(1 + size)
    heat = _blur(base, passes=passes)
    if heat.max() > 0:
        heat = heat / heat.max()
    return np.clip(heat * min(1.0, 0.25 * risk * size / 2.0), 0.0, 1.0)


def generate_demo_data(out_dir: str | Path, *, seed: int = 0) -> dict:
    """out_dir에 합성 demo 데이터를 쓰고 요약 dict를 반환한다.

    bundles.csv, layouts.csv, hotspots.csv, ca_records.csv, fab.json, geometry.json,
    provenance.json과 NPY/PNG 이미지, summary.json, README_SYNTHETIC.txt를 만든다.
    같은 seed면 같은 파일이 생성된다.
    """
    out = Path(out_dir)
    (out / "arrays").mkdir(parents=True, exist_ok=True)
    (out / "images").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    bundle_rows, layout_rows, hotspot_rows, ca_rows = [], [], [], []
    geometry: dict[str, dict] = {}
    used_pairs: set[tuple[str, str]] = set()
    for product, version, family, fab, uw, uh, gap, lw, factor, h in _PRODUCTS:
        used_pairs.add((fab, family))
        w = int(round(h * uw / uh)) if uw is not None else 64
        pixel_size = (uh / h) if uh is not None else None
        scale = pixel_size if pixel_size is not None else 1.5
        gap_px = max(1, int(round(gap / scale)))
        lw_px = max(1, int(round(lw / scale)))
        area_scale = (uw * uh / 5000.0) if uw is not None else 1.0
        grid = [1.0, 3.0, 4.0, 8.0, 16.0] if product == "P3" else CA_GRID
        for code, ftype, ra, la, rb, lb, layer in _FAILURES:
            sid = f"{product}-{version}-{code}"
            is_v2 = version == "v2"
            bundle_rows.append({
                "sample_id": sid, "product_id": product, "product_family": family,
                "design_version": version, "design_lineage_id": f"L{product[1:]}",
                "electrode_a_role": ra, "electrode_b_role": rb, "layer_a": la, "layer_b": lb,
                "failure_type": ftype, "fab_id": fab, "fab_distribution_id": "",
                "assigned_process_layer": layer,
                "unit_width": "" if uw is None else uw, "unit_height": "" if uh is None else uh,
                "physical_length_unit": "" if uw is None else "um",
                "parent_design_id": f"{product}-v1" if is_v2 else "",
                "change_description": "data-pixel 전극 간격 확대" if is_v2 else "",
                "calc_version": "synthetic-0",
            })
            risk = factor * (6.0 / gap) * (1.0 + 0.3 * (code == "VSHORT"))
            images = _layouts(code, h, w, gap_px, lw_px)
            for slot, image in zip("ab", images):
                rel = f"arrays/{sid}-{slot}.npy"
                np.save(out / rel, image)
                layout_rows.append({"sample_id": sid, "slot": slot, "path": rel,
                                    "pixel_size": "" if pixel_size is None else pixel_size,
                                    "origin_x": 0.0, "origin_y": 0.0})
            for size in HOTSPOT_SIZES:
                if product == "P4" and size == 8.0:
                    continue
                heat = _hotspot(images, code, size, risk)
                rel = f"images/{sid}-hot{int(size)}.png"
                Image.fromarray((heat * 255).round().astype(np.uint8), mode="L").save(out / rel)
                hotspot_rows.append({"sample_id": sid, "particle_size": size, "path": rel,
                                     "particle_size_unit": "um",
                                     "pixel_size": "" if pixel_size is None else pixel_size,
                                     "origin_x": 0.0, "origin_y": 0.0})
            for size in grid:
                # CA는 0..1 범위의 무차원 확률 기반 누적 위험값이다. 분석 면적에 따라 증가한다.
                value = 0.005 * risk * area_scale * (size / 16.0) ** 1.5 * (
                    1.0 + 0.05 * rng.standard_normal()
                )
                # P2의 16 um CA는 미관측으로 남긴다(빈 값, 0 아님).
                blank = product == "P2" and size == 16.0
                ca_rows.append({"sample_id": sid, "particle_size": size,
                                "particle_size_unit": "um",
                                "ca_value": "" if blank else round(max(value, 0.0), 6),
                                "ca_unit": "1"})
            if product != "P4":
                geometry[sid] = {
                    "thickness": {"M1": 0.3, "M2": 0.35 + 0.01 * int(product[1:]), "ITO": 0.05},
                    "shape_metrics": {"gap": gap, "line_width": lw},
                    "length_unit": "um",
                }

    distributions = []
    for fab, family in sorted(used_pairs):
        for layer in ("M2", "ITO"):
            # 구간 중앙값(1, 2, 4, 8, 16 um)이 CA 격자와 정확히 일치하도록 구간을 정했다.
            probs = _FAB_BASE[fab] * (1.0 + 0.1 * rng.standard_normal(5))
            probs = np.clip(probs, 0.01, None)
            distributions.append({
                "fab_distribution_id": f"{fab}-{family}-{layer}", "fab_id": fab,
                "product_family": family, "process_layer": layer,
                "bin_lower": _BIN_LOWER, "bin_upper": _BIN_UPPER,
                "raw_values": [round(float(p), 6) for p in probs / probs.sum()],
                "value_kind": "probability", "value_unit": "1", "size_unit": "um",
                "surveyed_area": None, "area_unit": None, "period": "demo", "is_reference": False,
            })
    distributions.append({
        "fab_distribution_id": "REF_FLAT", "fab_id": "REFERENCE", "product_family": None,
        "process_layer": "ANY", "bin_lower": _BIN_LOWER, "bin_upper": _BIN_UPPER,
        "raw_values": [0.2] * 5, "value_kind": "probability", "value_unit": "1",
        "size_unit": "um", "surveyed_area": None, "area_unit": None, "period": "demo",
        "is_reference": True,
    })
    distributions.append({
        "fab_distribution_id": "FAB_A-COUNT-DEMO", "fab_id": "FAB_A", "product_family": "F1",
        "process_layer": "M9", "bin_lower": _BIN_LOWER, "bin_upper": _BIN_UPPER,
        "raw_values": [120, 80, 40, None, 5], "value_kind": "count", "value_unit": "particles",
        "size_unit": "um", "surveyed_area": 1.0e6, "area_unit": "um^2", "period": "demo",
        "is_reference": False,
    })

    def write_csv(name: str, rows: list[dict]) -> None:
        with open(out / name, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write_csv("bundles.csv", bundle_rows)
    write_csv("layouts.csv", layout_rows)
    write_csv("hotspots.csv", hotspot_rows)
    write_csv("ca_records.csv", ca_rows)
    layer_rows = []
    for product in sorted({row["product_id"] for row in bundle_rows}):
        for order, layer in enumerate(("M1", "M2", "ITO"), start=1):
            layer_rows.append({
                "product_id": product, "design_version": "*", "layer_name": layer,
                "stack_order": order, "fab_process_layer": layer,
            })
    write_csv("layer_stack.csv", layer_rows)
    (out / "fab.json").write_text(json.dumps({"distributions": distributions}, indent=2), encoding="utf-8")
    (out / "geometry.json").write_text(json.dumps(geometry, indent=2), encoding="utf-8")
    (out / "provenance.json").write_text(json.dumps({
        "calc_settings": {"note": "synthetic"}, "calc_version": "synthetic-0", "is_synthetic": True,
    }, indent=2), encoding="utf-8")
    (out / "README_SYNTHETIC.txt").write_text(DISCLAIMER + "\n", encoding="utf-8")
    summary = {
        "n_bundles": len(bundle_rows),
        "products": sorted({row["product_id"] for row in bundle_rows}),
        "notes": [
            DISCLAIMER,
            "P6은 unit-pixel 실제 크기 정보가 없습니다",
            "P4는 8 um hotspot과 형상 수치가 없습니다",
            "P3은 다른 CA 이물 크기 격자를 쓰고, P2는 16 um CA가 누락되어 있습니다",
            "P1 v2는 설계 계열 L1에 속한 P1 v1의 변경 설계입니다",
            "FAB_A-COUNT-DEMO는 demo CAA 규칙이 거부해야 하는 count 분포입니다",
            "Fab과 제품군이 일부 겹칩니다: F1은 FAB_A, F2는 FAB_B에만 있고 F3은 양쪽에 있습니다",
        ],
        "similar_shape_different_risk": ["P2", "P5"],
        "seed": seed,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
