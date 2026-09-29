"""Fab 원자료·가공 요약자료 해석과 CAA 계산.

CAA는 CA에 Fab 가중치를 적용한 파생 지표이며 입력이나 정답으로 쓰지 않는다.
실제 가중식과 단위가 확정되지 않았으므로 현재는 demo 규칙 하나만 제공한다.

원칙
- 원본 분포(FabDistribution)와 적용 가중치(FabWeights)를 분리해 보존한다.
- 크기 구간 변환이나 보간 없이 같은 크기 격자에서만 곱해 합산한다.
- 적용 공정 layer는 입력/설정으로만 정하며 하부 layer 등으로 추정하지 않는다.
- 결과를 실제 불량률이나 수율로 표시하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simcompare.schema import (
    AnalysisBundle,
    CACurve,
    DataContractError,
    FabDistribution,
    canonicalize_ca,
    is_dimensionless_unit,
    convert_area,
    convert_length,
    validate_fab,
    validate_mapping_contract,
)

DEMO_RULE = "demo_probability_per_bin"
DEMO_LABEL = "DEMO CAA: 예시용 가중치 계산이며 실제 불량률·수율이 아님"


class CAAError(DataContractError):
    """Fab 해석 또는 CAA 계산 조건이 충족되지 않을 때 발생하는 오류."""


@dataclass
class FabWeights:
    """원본 분포에서 만든 크기별 적용 가중치. sizes는 구간 중앙값, 미관측 구간의 weights는 NaN이다."""
    sizes: np.ndarray      # 구간 중앙값
    weights: np.ndarray    # 미관측 구간은 NaN
    size_unit: str
    rule: str
    source_id: str         # 원본 fab_distribution_id
    weight_unit: str
    is_demo: bool


@dataclass
class CAAResult:
    """CAA 계산 결과. 계산할 수 없으면 value는 None이고 reason에 사유가 남는다."""
    value: float | None
    unit: str | None
    used_sizes: list[float]
    missing_sizes: list[float]
    rule: str
    source_id: str
    is_demo: bool
    label: str
    reason: str | None


def bin_midpoints(dist: FabDistribution) -> np.ndarray:
    """각 크기 구간의 중앙값 (lower + upper) / 2를 반환한다."""
    lower = np.asarray(dist.bin_lower, dtype=float)
    upper = np.asarray(dist.bin_upper, dtype=float)
    return ((lower + upper) / 2.0).astype(np.float64)


def fab_shape_and_total(dist: FabDistribution) -> tuple[np.ndarray, float, str]:
    """분포를 모양(shape)과 총량(total)으로 분리한다.

    count, probability, rate_per_area는 원본 값을, density는 값×구간 폭을 질량으로 본다.
    shape = 질량 / 총량이며 미관측 구간은 NaN이다. 관측 구간이 없으면 CAAError.
    """
    validate_fab(dist)
    lower = np.asarray(dist.bin_lower, dtype=float)
    upper = np.asarray(dist.bin_upper, dtype=float)
    values = np.asarray(dist.raw_values, dtype=float)
    observed = np.asarray(dist.observed, dtype=bool)

    if not np.any(observed):
        raise CAAError(
            f"{dist.fab_distribution_id}: 관측된 구간이 없어 분포 모양과 총량을 정의할 수 없습니다"
        )

    if dist.value_kind == "density":
        mass = values * (upper - lower)
        unit = f"{dist.value_unit}*{dist.size_unit}"
    elif dist.value_kind in ("count", "average_count_per_glass"):
        surveyed_area_um2 = convert_area(dist.surveyed_area, dist.area_unit, "um")
        mass = values / surveyed_area_um2
        suffix = "/glass" if dist.value_kind == "average_count_per_glass" else ""
        unit = f"{dist.value_unit}{suffix}/um^2"
    else:
        mass = values.copy()
        unit = dist.value_unit

    observed_mass = mass[observed]
    total = float(np.sum(observed_mass))
    if total == 0.0:
        shape = np.full_like(mass, np.nan, dtype=float)
    else:
        shape = np.where(observed, mass / total, np.nan).astype(float)
    return shape, total, unit


def interpret_fab(
    dist: FabDistribution,
    rule: str | None,
    *,
    mode: str,
    confirmed: bool = False,
) -> FabWeights:
    """설정된 규칙으로 원본 분포를 적용 가중치로 해석한다.

    지원 규칙은 demo_probability_per_bin 하나이며 mode demo와 value_kind probability에서만
    동작한다. 규칙이 없거나 확정되지 않은 규칙이면 CAAError를 낸다.
    """
    validate_fab(dist)
    if rule is None:
        raise CAAError("CAA 계산 규칙이 설정되지 않았습니다")

    if rule == DEMO_RULE:
        if mode != "demo":
            raise CAAError(
                f"demo 규칙 {DEMO_RULE!r}은(는) mode 'demo'에서만 사용할 수 있습니다(현재 {mode!r})"
            )
        if dist.value_kind != "probability":
            raise CAAError(
                f"demo 규칙 {DEMO_RULE!r}은(는) value_kind 'probability'만 허용합니다. "
                f"{dist.value_kind!r} 값의 변환 방식은 확정되지 않았습니다"
            )
        weights = np.asarray(dist.raw_values, dtype=float).copy()
        sizes = bin_midpoints(dist)
        return FabWeights(
            sizes=sizes,
            weights=weights,
            size_unit=dist.size_unit,
            rule=rule,
            source_id=dist.fab_distribution_id,
            weight_unit="probability/bin",
            is_demo=True,
        )

    raise CAAError(f"알 수 없거나 확정되지 않은 CAA 규칙 {rule!r}")


def select_distribution(
    bundle: AnalysisBundle,
    distributions: dict[str, FabDistribution],
    overrides: dict[str, str] | None = None,
) -> FabDistribution:
    """분석 묶음에 적용할 실제 Fab 분포를 고른다.

    적용 layer는 process_layer_overrides["제품|불량종류"]가 있으면 그 값을, 없으면
    assigned_process_layer를 쓴다. 둘 다 없으면 오류다. 기준 분포는 선택하지 않으며,
    후보가 없거나 여러 개면 오류를 낸다.
    """
    try:
        validate_mapping_contract(bundle)
    except DataContractError as exc:
        raise CAAError(str(exc)) from exc
    modern = bundle.mapping_contract_version == "1.2"
    key = f"{bundle.product_id}|{bundle.failure_type}"
    if modern and overrides and key in overrides and overrides[key] != bundle.assigned_process_layer:
        raise CAAError(f"{bundle.sample_id}: override {overrides[key]!r} conflicts with source assignment {bundle.assigned_process_layer!r}")
    layer = (
        overrides.get(key)
        if overrides and key in overrides
        else bundle.assigned_process_layer
    )

    if layer is None:
        raise CAAError(
            f"{bundle.sample_id}: 적용 공정 layer가 지정되지 않았습니다. "
            "하부 layer 등으로 추정하지 않습니다"
        )

    if bundle.fab_distribution_id is not None:
        dist = distributions.get(bundle.fab_distribution_id)
        if dist is None:
            raise CAAError(
                f"{bundle.sample_id}: fab_distribution_id "
                f"{bundle.fab_distribution_id!r}을(를) 찾을 수 없습니다"
            )
        if dist.is_reference:
            raise CAAError(
                f"{bundle.sample_id}: 기준 분포 "
                f"{bundle.fab_distribution_id!r}은(는) 실제 Fab 분포로 선택할 수 없습니다"
            )
        if dist.fab_id != bundle.fab_id:
            raise CAAError(
                f"{bundle.sample_id}: fab_distribution_id {bundle.fab_distribution_id!r}"
                f"의 fab_id {dist.fab_id!r}가 묶음 fab_id {bundle.fab_id!r}와 일치하지 않습니다"
            )
        if dist.process_layer != layer:
            raise CAAError(
                f"{bundle.sample_id}: fab_distribution_id {bundle.fab_distribution_id!r}"
                f"의 process_layer {dist.process_layer!r}가 {layer!r}와 일치하지 않습니다"
            )
        if dist.fab_pair_key is not None and (not modern or dist.fab_pair_key != bundle.fab_pair_key):
            raise CAAError(f"{bundle.sample_id}: explicit distribution pair mismatch")
        if modern and dist.product_family not in (None, bundle.product_family):
            raise CAAError(f"{bundle.sample_id}: explicit distribution family mismatch")
        return dist

    candidates = [
        d
        for d in distributions.values()
        if not d.is_reference
        and d.fab_id == bundle.fab_id
        and d.process_layer == layer
        and (d.product_family == bundle.product_family or d.product_family is None)
        and d.fab_pair_key is None
    ]
    final = []
    for family in (bundle.product_family, None):
        final = [d for d in candidates if d.product_family == family]
        if final:
            break
    if len(final) == 0:
        raise CAAError(
            f"{bundle.sample_id}: 해당하는 Fab 분포가 없습니다; pair-specific distributions require explicit fab_distribution_id. "
            f"(fab_id={bundle.fab_id!r}, layer={layer!r}, family={bundle.product_family!r}, pair={bundle.fab_pair_key!r})"
        )
    if len(final) > 1:
        ids = sorted(d.fab_distribution_id for d in final)
        raise CAAError(
            f"{bundle.sample_id}: Fab 분포 후보가 여러 개라 선택할 수 없습니다 {ids} "
            f"(fab={bundle.fab_id!r}, layer={layer!r}, family={bundle.product_family!r}, pair={bundle.fab_pair_key!r})"
        )
    return final[0]


def compute_caa(
    ca: CACurve,
    weights: FabWeights,
    *,
    require_full_coverage: bool = True,
    canonical_length_unit: str = "um",
) -> CAAResult:
    """CA 곡선과 Fab 가중치를 곱해 합산한 CAA를 계산한다.

    가중치 크기와 관측된 CA 크기가 정확히 일치(rtol=1e-6)하는 점만 사용한다.
    누락 크기가 있고 require_full_coverage이면 값을 만들지 않고 사유만 남긴다.
    """
    canonicalize_ca(ca, canonical_length_unit)
    try:
        w_sizes = convert_length(weights.sizes, weights.size_unit, ca.particle_size_unit)
    except DataContractError as exc:
        raise CAAError(f"CAA 이물 지름 단위 불일치: {exc}") from None
    w_sizes = np.asarray(w_sizes, dtype=float)
    w_values = np.asarray(weights.weights, dtype=float)
    finite = np.isfinite(w_values)
    if not np.any(finite):
        raise CAAError(
            f"{weights.source_id}: 유효한 가중치가 없어 CAA를 계산할 수 없습니다"
        )

    ca_sizes = np.asarray(ca.particle_sizes, dtype=float)
    ca_values = np.asarray(ca.values, dtype=float)
    ca_observed = np.asarray(ca.observed, dtype=bool)

    used_sizes: list[float] = []
    missing_sizes: list[float] = []
    matched_weight: list[float] = []
    matched_ca: list[float] = []

    for size, weight in zip(w_sizes[finite], w_values[finite]):
        matches = np.isclose(ca_sizes, size, rtol=1e-6, atol=0.0) & ca_observed
        if np.any(matches):
            idx = int(np.argmax(matches))
            used_sizes.append(float(size))
            matched_weight.append(float(weight))
            matched_ca.append(float(ca_values[idx]))
        else:
            missing_sizes.append(float(size))

    unit = "relative_index" if is_dimensionless_unit(ca.unit) else f"relative_index*{ca.unit}"
    label = DEMO_LABEL if weights.is_demo else "CAA (설정된 규칙)"

    if missing_sizes and require_full_coverage:
        return CAAResult(
            value=None,
            unit=unit,
            used_sizes=used_sizes,
            missing_sizes=missing_sizes,
            rule=weights.rule,
            source_id=weights.source_id,
            is_demo=weights.is_demo,
            label=label,
            reason=f"이물 크기 {missing_sizes}의 CA 누락",
        )

    value = float(sum(w * c for w, c in zip(matched_weight, matched_ca)))
    if missing_sizes:
        reason = f"일부 크기만 반영; 이물 크기 {missing_sizes}의 CA 누락"
    else:
        reason = None
    return CAAResult(
        value=value,
        unit=unit,
        used_sizes=used_sizes,
        missing_sizes=missing_sizes,
        rule=weights.rule,
        source_id=weights.source_id,
        is_demo=weights.is_demo,
        label=label,
        reason=reason,
    )
