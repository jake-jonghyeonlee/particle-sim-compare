"""공통 schema 묶음을 모델·기준선용 마스크 배열로 변환한다.

이미지는 종횡비를 유지해 고정 크기로 맞추고 여백은 유효 영역 마스크로 표시한다.
수치 입력은 값 배열과 관측 마스크를 함께 만들며, 누락값은 NaN으로 남긴다.
정규화 통계는 fit_normalizers에서 학습 행만으로 계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from simcompare.caa import CAAError, bin_midpoints, fab_shape_and_total, select_distribution
from simcompare.preprocess import Normalizer, align_curve, letterbox, safe_log10
from simcompare.schema import AnalysisBundle, DataContractError, FabDistribution, convert_length

META_COLUMNS = [
    "sample_id", "product_id", "product_family", "design_version", "design_lineage_id",
    "electrode_a_role", "electrode_b_role", "layer_a", "layer_b", "failure_type", "fab_id",
    "fab_distribution_id", "assigned_process_layer", "unit_width", "unit_height",
    "physical_length_unit", "has_physical_scale", "is_open", "parent_design_id",
    "unit_width_canonical", "unit_height_canonical", "canonical_length_unit",
]


@dataclass
class EncodedSet:
    """변환된 전체 데이터셋. 모든 배열의 첫 축은 meta 행 순서와 같다.

    - layout: (N, 2, H, W) 전극 a·b 이미지, layout_valid는 유효 영역, layout_present는 존재 여부
    - hotspot: (N, K, H, W) 설정한 이물 크기별 hotspot
    - ca_log / ca_area_log: 원 CA와 면적 대비 CA의 log10 (누락 NaN)
    - fab_shape / fab_total_log: Fab 분포 모양과 log10 총량(분리 보존)
    - geom: 실제 크기·두께·형상 수치, geom_names에 열 이름
    - notes: 변환 중 남긴 안내(면적 보정 불가, Fab 분포 사용 불가 등)
    """
    meta: pd.DataFrame
    layout: np.ndarray
    layout_valid: np.ndarray
    layout_present: np.ndarray
    hotspot: np.ndarray
    hotspot_valid: np.ndarray
    hotspot_present: np.ndarray
    ca_log: np.ndarray
    ca_mask: np.ndarray
    ca_area_log: np.ndarray
    ca_area_mask: np.ndarray
    fab_shape: np.ndarray
    fab_mask: np.ndarray
    fab_total_log: np.ndarray
    fab_total_mask: np.ndarray
    geom: np.ndarray
    geom_mask: np.ndarray
    geom_names: list[str]
    notes: list[str]

    def __len__(self) -> int:
        """분석 묶음 수."""
        return len(self.meta)


def _meta_row(bundle: AnalysisBundle, canonical_length_unit: str) -> dict:
    """분석 묶음 하나를 메타데이터 한 행(dict)으로 만든다."""
    row = {name: getattr(bundle, name, None) for name in META_COLUMNS}
    row["has_physical_scale"] = bundle.has_physical_scale()
    row["is_open"] = bundle.is_open
    row["canonical_length_unit"] = canonical_length_unit
    if bundle.has_physical_scale():
        row["unit_width_canonical"] = convert_length(
            bundle.unit_width, bundle.physical_length_unit, canonical_length_unit
        )
        row["unit_height_canonical"] = convert_length(
            bundle.unit_height, bundle.physical_length_unit, canonical_length_unit
        )
    else:
        row["unit_width_canonical"] = np.nan
        row["unit_height_canonical"] = np.nan
    return row


def _geom_names(bundles: list[AnalysisBundle]) -> list[str]:
    """모든 묶음에 등장하는 두께·형상 수치 이름을 모아 수치 입력 열 순서를 정한다."""
    thickness, shape = set(), set()
    for bundle in bundles:
        if bundle.geometry is not None:
            thickness.update(bundle.geometry.thickness)
            shape.update(bundle.geometry.shape_metrics)
    return ["unit_width", "unit_height"] + [f"thk:{k}" for k in sorted(thickness)] + [
        f"shape:{k}" for k in sorted(shape)
    ]


def encode_bundles(
    bundles: list[AnalysisBundle], distributions: dict[str, FabDistribution], cfg
) -> EncodedSet:
    """분석 묶음 목록을 EncodedSet으로 변환한다.

    - CA는 설정한 크기 격자에 정렬하며 관측 범위 밖으로 외삽하지 않는다.
    - 실제 크기나 단위가 맞지 않으면 면적 대비 CA 행 전체를 마스크 처리한다.
    - Fab 분포를 고를 수 없으면 해당 행을 마스크 처리하고 사유를 notes에 남긴다.
    """
    if not bundles:
        raise ValueError("변환할 분석 묶음이 없습니다")
    h, w = (int(v) for v in cfg.data.image_size)
    n = len(bundles)
    hot_sizes = [float(s) for s in cfg.data.hotspot_sizes]
    ca_grid = np.asarray(cfg.data.ca_grid, dtype=float)
    fab_grid = np.asarray(cfg.data.fab_grid, dtype=float)
    k, g, b = len(hot_sizes), len(ca_grid), len(fab_grid)
    names = _geom_names(bundles)
    overrides = cfg.caa.process_layer_overrides
    canonical_unit = cfg.data.canonical_length_unit

    enc = EncodedSet(
        meta=pd.DataFrame([_meta_row(x, canonical_unit) for x in bundles], columns=META_COLUMNS),
        layout=np.zeros((n, 2, h, w), np.float32),
        layout_valid=np.zeros((n, 2, h, w), np.float32),
        layout_present=np.zeros((n, 2), np.float32),
        hotspot=np.zeros((n, k, h, w), np.float32),
        hotspot_valid=np.zeros((n, k, h, w), np.float32),
        hotspot_present=np.zeros((n, k), np.float32),
        ca_log=np.full((n, g), np.nan),
        ca_mask=np.zeros((n, g), bool),
        ca_area_log=np.full((n, g), np.nan),
        ca_area_mask=np.zeros((n, g), bool),
        fab_shape=np.full((n, b), np.nan),
        fab_mask=np.zeros((n, b), bool),
        fab_total_log=np.full((n, 1), np.nan),
        fab_total_mask=np.zeros((n, 1), bool),
        geom=np.full((n, len(names)), np.nan),
        geom_mask=np.zeros((n, len(names)), bool),
        geom_names=names,
        notes=[],
    )
    area_reasons: dict[str, int] = {}
    for i, bundle in enumerate(bundles):
        for slot, image in enumerate(bundle.layouts[:2]):
            if image.array is None:
                continue
            out, valid, _ = letterbox(image.array, (h, w), binary=True)
            enc.layout[i, slot], enc.layout_valid[i, slot], enc.layout_present[i, slot] = out, valid, 1.0
        for j, size in enumerate(hot_sizes):
            match = [
                x for x in bundle.hotspots
                if x.array is not None and x.particle_size is not None
                and np.isclose(
                    convert_length(x.particle_size, x.particle_size_unit, canonical_unit),
                    size, rtol=1e-6, atol=0,
                )
            ]
            if match:
                out, valid, _ = letterbox(match[0].array, (h, w), binary=False)
                enc.hotspot[i, j], enc.hotspot_valid[i, j], enc.hotspot_present[i, j] = out, valid, 1.0
        if bundle.ca is not None:
            vals, mask = align_curve(
                convert_length(bundle.ca.particle_sizes, bundle.ca.particle_size_unit, canonical_unit),
                bundle.ca.values, bundle.ca.observed, ca_grid,
                cfg.data.ca_alignment,
            )
            enc.ca_log[i], enc.ca_mask[i] = safe_log10(vals, mask), mask
            per_area, reason = bundle.ca_per_area(canonical_unit)
            if per_area is None:
                area_reasons[reason] = area_reasons.get(reason, 0) + 1
            else:
                vals, mask = align_curve(
                    convert_length(bundle.ca.particle_sizes, bundle.ca.particle_size_unit, canonical_unit),
                    per_area, bundle.ca.observed, ca_grid,
                    cfg.data.ca_alignment,
                )
                enc.ca_area_log[i], enc.ca_area_mask[i] = safe_log10(vals, mask), mask
        try:
            dist = select_distribution(bundle, distributions, overrides)
            shape, total, _unit = fab_shape_and_total(dist)
        except (CAAError, DataContractError) as exc:
            enc.notes.append(f"{bundle.sample_id}: Fab 분포 사용 불가: {exc}")
        else:
            mids = convert_length(bin_midpoints(dist), dist.size_unit, canonical_unit)
            for j, size in enumerate(fab_grid):
                hit = np.flatnonzero(np.isclose(mids, size, rtol=1e-6, atol=0))
                if hit.size and np.isfinite(shape[hit[0]]):
                    enc.fab_shape[i, j], enc.fab_mask[i, j] = shape[hit[0]], True
            if total > 0:
                enc.fab_total_log[i, 0], enc.fab_total_mask[i, 0] = np.log10(total), True
        values: dict[str, float] = {}
        if bundle.has_physical_scale():
            values["unit_width"] = convert_length(
                bundle.unit_width, bundle.physical_length_unit, canonical_unit
            )
            values["unit_height"] = convert_length(
                bundle.unit_height, bundle.physical_length_unit, canonical_unit
            )
        if bundle.geometry is not None:
            values.update({f"thk:{k}": v for k, v in bundle.geometry.thickness.items()})
            values.update({f"shape:{k}": v for k, v in bundle.geometry.shape_metrics.items()})
        for j, name in enumerate(names):
            value = values.get(name)
            if value is not None and np.isfinite(value):
                enc.geom[i, j], enc.geom_mask[i, j] = float(value), True
    for reason, count in sorted(area_reasons.items()):
        enc.notes.append(f"면적 대비 CA 계산 불가 {count}건: {reason}")
    return enc


def fit_normalizers(enc: EncodedSet, train_pos: np.ndarray) -> dict[str, Normalizer]:
    """학습 행(train_pos)만으로 CA·Fab 총량·형상 수치 정규화 통계를 계산한다."""
    rows = np.asarray(train_pos, dtype=int)
    return {
        "ca": Normalizer.fit(enc.ca_log, enc.ca_mask, rows),
        "ca_area": Normalizer.fit(enc.ca_area_log, enc.ca_area_mask, rows),
        "fab_total": Normalizer.fit(enc.fab_total_log, enc.fab_total_mask, rows),
        "geom": Normalizer.fit(enc.geom, enc.geom_mask, rows),
    }


def to_model_arrays(enc: EncodedSet, norms: dict[str, Normalizer]) -> dict[str, np.ndarray]:
    """정규화를 적용해 모델 입력용 float32 배열 dict를 만든다.

    마스크 밖 값은 0 자리표시값으로 두며, 실제 누락 여부는 함께 넘기는 마스크가 기준이다.
    """
    def f32(x: np.ndarray) -> np.ndarray:
        return np.asarray(x, dtype=np.float32)

    return {
        "layout": enc.layout, "layout_valid": enc.layout_valid, "layout_present": enc.layout_present,
        "hotspot": enc.hotspot, "hotspot_valid": enc.hotspot_valid,
        "hotspot_present": enc.hotspot_present,
        "ca": f32(np.hstack([
            norms["ca"].transform(enc.ca_log, enc.ca_mask),
            norms["ca_area"].transform(enc.ca_area_log, enc.ca_area_mask),
        ])),
        "ca_mask": f32(np.hstack([enc.ca_mask, enc.ca_area_mask])),
        "fab": f32(np.where(enc.fab_mask, enc.fab_shape, 0.0)), "fab_mask": f32(enc.fab_mask),
        "fab_total": f32(norms["fab_total"].transform(enc.fab_total_log, enc.fab_total_mask)),
        "fab_total_mask": f32(enc.fab_total_mask),
        "geom": f32(norms["geom"].transform(enc.geom, enc.geom_mask)), "geom_mask": f32(enc.geom_mask),
    }


def subset_arrays(arrays: dict[str, np.ndarray], pos) -> dict[str, np.ndarray]:
    """배열 dict의 모든 항목을 같은 행 위치로 잘라 반환한다."""
    idx = np.asarray(pos, dtype=int)
    return {key: value[idx] for key, value in arrays.items()}
