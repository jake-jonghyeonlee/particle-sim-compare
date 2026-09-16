"""Fab raw-distribution interpretation and demo CAA computation.

This module is deliberately separate from the model code. It turns a raw Fab
particle distribution into a shape/total decomposition and into CAA weights,
and it combines those weights with an already-computed CA curve.

CAA is a *derived* value (CA x Fab weights); it is never an independent input
or a ground truth. Missing values are kept as NaN plus boolean masks and are
never replaced by 0. Uncertain physical meaning (units, bins, CAA formula,
vertical-short layer) raises :class:`CAAError` in real mode; only the labelled
demo rule may run in demo mode.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simcompare.schema import (
    AnalysisBundle,
    CACurve,
    DataContractError,
    FabDistribution,
    validate_fab,
)

DEMO_RULE = "demo_probability_per_bin"
DEMO_LABEL = "DEMO CAA: 예시용 가중치 계산이며 실제 불량률·수율이 아님"


class CAAError(DataContractError):
    """CAA-specific contract violation."""


@dataclass
class FabWeights:
    sizes: np.ndarray      # bin midpoints
    weights: np.ndarray    # NaN where the bin is unobserved
    rule: str
    source_id: str         # fab_distribution_id
    weight_unit: str
    is_demo: bool


@dataclass
class CAAResult:
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
    """Return ``(lower + upper) / 2`` as float64 for every bin."""
    lower = np.asarray(dist.bin_lower, dtype=float)
    upper = np.asarray(dist.bin_upper, dtype=float)
    return ((lower + upper) / 2.0).astype(np.float64)


def fab_shape_and_total(dist: FabDistribution) -> tuple[np.ndarray, float, str]:
    """Decompose a raw distribution into (shape, total, unit).

    ``shape`` is the distribution *form* (mass normalised to sum to 1 over the
    observed bins, NaN where unobserved) and ``total`` is the *amount*. They are
    returned separately on purpose so that form and amount never collapse into
    one number.
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
    """Build :class:`FabWeights` for ``dist`` under the configured ``rule``.

    Only the labelled demo rule is implemented. Real CAA formulas are
    intentionally not implemented here; any non-demo rule is rejected even when
    ``confirmed`` is provided (``confirmed`` is accepted as a future hook and
    must be True for any non-demo rule).
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
    """Select the Fab distribution for ``bundle``; never infer the layer."""
    key = f"{bundle.product_id}|{bundle.failure_type}"
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
        return dist

    candidates = [
        d
        for d in distributions.values()
        if not d.is_reference
        and d.fab_id == bundle.fab_id
        and d.process_layer == layer
        and (d.product_family == bundle.product_family or d.product_family is None)
    ]
    exact = [d for d in candidates if d.product_family == bundle.product_family]
    final = exact if exact else candidates
    if len(final) == 0:
        raise CAAError(
            f"{bundle.sample_id}: 해당하는 Fab 분포가 없습니다 "
            f"(fab_id={bundle.fab_id!r}, layer={layer!r}, family={bundle.product_family!r})"
        )
    if len(final) > 1:
        ids = sorted(d.fab_distribution_id for d in final)
        raise CAAError(
            f"{bundle.sample_id}: Fab 분포 후보가 여러 개라 선택할 수 없습니다 {ids}"
        )
    return final[0]


def compute_caa(
    ca: CACurve,
    weights: FabWeights,
    *,
    require_full_coverage: bool = True,
) -> CAAResult:
    """Combine a CA curve with Fab weights into a derived CAA value.

    No interpolation and no conversion between different bins: each weight size
    must match an *observed* CA size exactly (within ``rtol=1e-6``).
    """
    w_sizes = np.asarray(weights.sizes, dtype=float)
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

    unit = f"{ca.unit}*{weights.weight_unit}"
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
