"""지역 입력 IO와 순수 분석·리포트 출력을 조합한다.

CA 계산/영역 선정/HTML은 각 책임 모듈에 맡긴다. 외부 입력 연결은 load_regional_inputs,
분석 조합은 analyze_regions, 기존 pipeline 연결은 write_regional_analysis를 수정한다.
모델용 resize 배열이나 AE/search/전역 geometry를 지역 판단에 사용하지 않는다.
"""
from __future__ import annotations

from collections import Counter
import copy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from typing import Sequence

import numpy as np

from .adapters.regional import load_source_manifest, source_for_diameter, source_frame_reason
from .config import RegionalConfig
from .correspondence import CorrespondencePair, load_correspondence
from .regional_comparison import RegionalComparison, compare_pair
from .regional_measurement import validate_source
from .regions import REGION_ALGORITHM_VERSION, observed_diameters
from .schema import AnalysisBundle, DataContractError
from .regional_schema import RegionalAnalysis, RegionalOutput, json_safe
from .regional_report import write_report

# ---------------------------------------------------------------------------
# 보고 계약과 JSON 변환: unavailable은 null이며 수치 0을 만들지 않는다
# ---------------------------------------------------------------------------
REGIONAL_SCHEMA_VERSION = "1"
REGIONAL_NOTES = (
    "CA는 sample/source/지름별 unit total이며 전극쌍을 더한 제품 전체 CA가 아닙니다.",
    "국부 delta는 명시 대응과 좌표 등록이 모두 검증된 경우에만 제공됩니다.",
    "Fab 가중, 수율 환산, 자동 설계 편집과 Tree/SHAP은 이 분석에 포함되지 않습니다.",
)


def _array_hash(array: np.ndarray | None) -> str | None:
    if array is None:
        return None
    digest = hashlib.sha256()
    digest.update(str((array.dtype.str, array.shape)).encode())
    digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def _sample_metadata(bundle: AnalysisBundle) -> dict:
    return {
        key: copy.deepcopy(getattr(bundle, key)) for key in (
            "sample_id", "product_id", "product_family", "design_version", "electrode_a_role", "electrode_b_role",
            "layer_a", "layer_b", "failure_type", "assigned_process_layer", "unit_width", "unit_height",
            "physical_length_unit",
        )
    } | {
        "synthetic": bundle.provenance.is_synthetic,
        "provenance": asdict(bundle.provenance),
        "layout_available": bool(bundle.layouts),
    }


def _input_signature(bundle: AnalysisBundle) -> dict:
    images = []
    for image in bundle.layouts + bundle.hotspots:
        metadata = {
            key: getattr(image, key) for key in (
                "role", "pixel_size", "origin_xy", "particle_size", "particle_size_unit",
                "value_semantics", "legend_id",
            )
        }
        images.append(metadata | {"array_sha256": _array_hash(image.array)})
    sources = []
    for source in bundle.ca_spatial_sources:
        sources.append({"diameter_um": source.diameter_um, "kind": source.source_kind,
                        "mode": source.source_mode, "frame": asdict(source.frame),
                        "semantics": source.semantics, "ca_source": source.ca_source,
                        "unavailable_reason": source.unavailable_reason,
                        "contract": source.calculation_contract_id, "total_ca": source.total_ca,
                        "total_ca_unit": source.total_ca_unit, "provenance": source.provenance,
                        "density_sha256": _array_hash(source.density),
                        "valid_sha256": _array_hash(source.valid_mask)})
    curve = None if bundle.ca is None else asdict(bundle.ca)
    return {
        "sample": _sample_metadata(bundle),
        "images": sorted(images, key=lambda x: json.dumps(json_safe(x), sort_keys=True)),
        "sources": sorted(sources, key=lambda x: x["diameter_um"]),
        "ca": curve,
    }


def _fingerprint(bundles: Sequence[AnalysisBundle], cfg: RegionalConfig,
                 pairs: Sequence[CorrespondencePair]) -> str:
    payload = {
        "schema_version": REGIONAL_SCHEMA_VERSION,
        "algorithm": REGION_ALGORITHM_VERSION,
        "config": asdict(cfg),
        "samples": [_input_signature(b) for b in sorted(bundles, key=lambda b: b.sample_id)],
        "correspondence": [asdict(p) for p in sorted(pairs, key=lambda p: p.comparison_id)],
    }
    encoded = json.dumps(json_safe(payload), sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 외부 입력 IO: 기존 bundle을 복사해서 연결, 학습 입력은 변경하지 않는다
# ---------------------------------------------------------------------------


def load_regional_inputs(bundles: Sequence[AnalysisBundle],
                         cfg: RegionalConfig) -> tuple[list[AnalysisBundle], list[CorrespondencePair]]:
    """선택 JSON/NPY 원천·대응표를 읽어 추가 source를 가진 새 bundle 목록을 만든다.

    원본 [y,x] 배열/좌상단 원점/단위는 어댑터 계약 그대로다. 파일 형식·누락 오류는
    DataContractError이며 지정하지 않은 대응은 추론하지 않는다. IO 읽기만 수행하고
    원본 bundle/학습 자료/AE 입력에는 추가 원천을 쓰거나 변경하지 않는다.
    """
    additions = load_source_manifest(cfg.source_manifest, bundles) if cfg.source_manifest else []
    augmented = [
        replace(b, ca_spatial_sources=(
            list(b.ca_spatial_sources) + [s for s in additions if s.sample_id == b.sample_id]
        )) for b in bundles
    ]
    pairs = load_correspondence(cfg.correspondence_file, augmented) if cfg.correspondence_file else []
    return augmented, pairs


# ---------------------------------------------------------------------------
# 순수 조합: 모든 명시 pair와 unmatched sample의 own 결과를 유지한다
# ---------------------------------------------------------------------------


def analyze_regions(bundles: Sequence[AnalysisBundle], cfg: RegionalConfig,
                    pairs: Sequence[CorrespondencePair] = ()) -> RegionalAnalysis:
    """원본 bundle과 검증된 명시 pair에서 지름별 순수 지역 보고 결과를 반환한다.

    배열은 [y,x], 원점은 좌상단이며 길이/면적은 um/um²로 계산한다. 원천 없는 sample도
    원 global CA와 unavailable 사유를 남긴다. 등록 없이 local delta를 추론하지 않는다.
    모델 resize·geometry/AE 점수·IO·input 변경·pair 간 total 합산은 없다.
    """
    by_id = {b.sample_id: b for b in bundles}
    if len(by_id) != len(bundles):
        raise DataContractError("duplicate regional sample_id")
    comparisons, used = [], set()
    if cfg.enabled:
        for pair in sorted(pairs, key=lambda p: p.comparison_id):
            for sid in (pair.query_sample_id, pair.reference_sample_id):
                if sid is not None and sid not in by_id:
                    raise DataContractError("regional pair references missing sample")
            comparisons.append(compare_pair(
                by_id.get(pair.query_sample_id), by_id.get(pair.reference_sample_id), pair, cfg,
            ))
            used.update(sid for sid in (pair.query_sample_id, pair.reference_sample_id) if sid is not None)
        for sid in sorted(set(by_id) - used):
            comparisons.append(compare_pair(by_id[sid], None, None, cfg))
    validations = []
    for bundle in sorted(bundles, key=lambda b: b.sample_id):
        for diameter in observed_diameters([bundle]):
            source, reason = source_for_diameter(bundle, diameter)
            gate = validate_source(source, conservation_rtol=cfg.conservation_rtol,
                                   conservation_atol_um2=cfg.conservation_atol_um2) if source else None
            frame_reason = source_frame_reason(bundle, source, cfg.coordinate_atol_um) if source else None
            if gate is not None and gate.status == "available" and frame_reason:
                # 보존 합이 맞아도 이미지 좌표가 다르면 지역 측정과 요약 모두 불가다.
                gate = replace(gate, status="unavailable", reason=frame_reason)
            validations.append({"sample_id": bundle.sample_id, "diameter_um": diameter,
                                "status": gate.status if gate else "unavailable",
                                "reason": gate.reason if gate else reason,
                                "validation": asdict(gate) if gate else None})
    notes = REGIONAL_NOTES
    if any(b.provenance.is_synthetic for b in bundles):
        notes += ("합성/demo 자료는 코드 동작 확인용이며 "
                  "실제 정확도나 수율 근거가 아닙니다.",)
    return RegionalAnalysis(
        REGIONAL_SCHEMA_VERSION, REGION_ALGORITHM_VERSION, asdict(cfg), _fingerprint(bundles, cfg, pairs),
        tuple(_sample_metadata(b) for b in sorted(bundles, key=lambda b: b.sample_id)),
        tuple(comparisons), tuple(validations), notes,
    )


def write_regional_analysis(bundles: Sequence[AnalysisBundle], cfg: RegionalConfig,
                            output_dir: str | Path) -> RegionalOutput:
    """지역 입력 읽기→순수 계산→HTML/JSON/CSV/원본 기반 SVG 출력을 수행한다.

    원본 배열·명시 좌표만 사용하고 기존 출력은 지우지 않는다. disabled는 IO 없이 빈
    결과를 반환한다. 반환 run_record는 학습 fingerprint를 변경하지 않는 보고 기록이다.
    """
    if not cfg.enabled:
        return RegionalOutput((), {"enabled": False})
    augmented, pairs = load_regional_inputs(bundles, cfg)
    analysis = analyze_regions(augmented, cfg, pairs)
    outputs = write_report(analysis, augmented, Path(output_dir))
    input_files = {}
    for name, path in (("source_manifest", cfg.source_manifest), ("correspondence_file", cfg.correspondence_file)):
        if path:
            input_files[name] = {"path": str(Path(path).resolve()),
                                 "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    reasons = Counter(v["reason"] for v in analysis.source_validations if v["reason"] is not None)
    record = {
        "enabled": True,
        "schema_version": analysis.schema_version,
        "algorithm_version": analysis.algorithm_version,
        "fingerprint": analysis.fingerprint,
        "config": analysis.config,
        "conservation_available": sum(v["status"] == "available" for v in analysis.source_validations),
        "conservation_failed": sum(v["reason"] == "conservation_failed" for v in analysis.source_validations),
        "source_unavailable": sum(v["status"] != "available" for v in analysis.source_validations),
        "unavailable_reasons": dict(sorted(reasons.items())),
        "source_validations": analysis.source_validations,
        "input_files": input_files,
        "outputs": [str(p) for p in outputs],
    }
    return RegionalOutput(outputs, record)
