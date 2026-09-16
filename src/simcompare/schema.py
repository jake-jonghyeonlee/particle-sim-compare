"""Common data contract shared by adapters, preprocessing, models and analysis.

Missing values are represented with ``None``/``NaN`` plus explicit masks and are
never replaced by 0. The second electrode of an open failure is
``NOT_APPLICABLE`` (a defined state, not a missing value).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

FAILURE_TYPES = ("lateral_open", "lateral_short", "vertical_short")
OPEN_TYPES = ("lateral_open",)
NOT_APPLICABLE = "N/A"
VALUE_KINDS = ("count", "probability", "rate_per_area", "density")
MODES = ("demo", "real")
IMAGE_SEMANTICS = ("binary", "processed_intensity", "probability")


class DataContractError(ValueError):
    """Input violates the common data contract."""


@dataclass
class ImageInput:
    role: str
    array: np.ndarray | None
    path: str | None = None
    pixel_size: float | None = None
    origin_xy: tuple[float, float] | None = None
    particle_size: float | None = None
    value_semantics: str = "binary"
    legend_id: str | None = None


@dataclass
class CACurve:
    particle_sizes: np.ndarray
    values: np.ndarray
    observed: np.ndarray
    unit: str
    calc_settings: dict = field(default_factory=dict)


@dataclass
class FabDistribution:
    fab_distribution_id: str
    fab_id: str
    product_family: str | None
    process_layer: str
    bin_lower: np.ndarray
    bin_upper: np.ndarray
    raw_values: np.ndarray
    observed: np.ndarray
    value_kind: str
    value_unit: str
    size_unit: str
    surveyed_area: float | None = None
    area_unit: str | None = None
    period: str | None = None
    is_reference: bool = False


@dataclass
class Geometry:
    thickness: dict[str, float] = field(default_factory=dict)
    shape_metrics: dict[str, float] = field(default_factory=dict)
    length_unit: str | None = None


@dataclass
class Provenance:
    adapter: str
    source_files: list[str] = field(default_factory=list)
    calc_settings: dict = field(default_factory=dict)
    calc_version: str | None = None
    is_synthetic: bool = False


@dataclass
class AnalysisBundle:
    sample_id: str
    product_id: str
    product_family: str
    design_version: str
    design_lineage_id: str
    electrode_a_role: str
    electrode_b_role: str
    layer_a: str
    layer_b: str
    failure_type: str
    fab_id: str
    fab_distribution_id: str | None = None
    assigned_process_layer: str | None = None
    unit_width: float | None = None
    unit_height: float | None = None
    physical_length_unit: str | None = None
    layouts: list[ImageInput] = field(default_factory=list)
    hotspots: list[ImageInput] = field(default_factory=list)
    ca: CACurve | None = None
    geometry: Geometry | None = None
    parent_design_id: str | None = None
    change_description: str | None = None
    provenance: Provenance = field(default_factory=lambda: Provenance(adapter="unknown"))

    @property
    def is_open(self) -> bool:
        return self.failure_type in OPEN_TYPES

    def role_pair(self) -> tuple[str, ...]:
        roles = [self.electrode_a_role, self.electrode_b_role]
        return tuple(sorted(r for r in roles if r != NOT_APPLICABLE))

    def layer_pair(self) -> tuple[str, ...]:
        layers = [self.layer_a, self.layer_b]
        return tuple(sorted(x for x in layers if x != NOT_APPLICABLE))

    def has_physical_scale(self) -> bool:
        return (
            self.unit_width is not None
            and self.unit_height is not None
            and bool(self.physical_length_unit)
        )

    def unit_area(self) -> float | None:
        if not self.has_physical_scale():
            return None
        return float(self.unit_width) * float(self.unit_height)

    def ca_per_area(self) -> tuple[np.ndarray | None, str]:
        """Return (CA / unit-pixel area, reason). Values stay NaN where unobserved."""
        if self.ca is None:
            return None, "no CA curve"
        area = self.unit_area()
        if area is None:
            return None, "no physical unit-pixel size; physical comparison impossible"
        if self.ca.unit != f"{self.physical_length_unit}^2":
            return None, f"CA unit {self.ca.unit!r} does not match {self.physical_length_unit}^2"
        return self.ca.values / area, "ok"


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"required field {name} is missing or empty")


def validate_curve(curve: CACurve, where: str = "ca") -> None:
    sizes = np.asarray(curve.particle_sizes, dtype=float)
    values = np.asarray(curve.values, dtype=float)
    observed = np.asarray(curve.observed, dtype=bool)
    if not (sizes.ndim == values.ndim == observed.ndim == 1) or not (
        len(sizes) == len(values) == len(observed)
    ):
        raise DataContractError(f"{where}: sizes/values/observed must be 1-D of equal length")
    if len(sizes) == 0 or np.any(~np.isfinite(sizes)) or np.any(sizes <= 0):
        raise DataContractError(f"{where}: particle sizes must be finite and positive")
    if np.any(np.diff(sizes) <= 0):
        raise DataContractError(f"{where}: particle sizes must be strictly increasing")
    if np.any(~np.isfinite(values[observed])) or np.any(values[observed] < 0):
        raise DataContractError(f"{where}: observed CA values must be finite and >= 0")
    if np.any(~np.isnan(values[~observed])):
        raise DataContractError(f"{where}: unobserved CA values must be NaN, not filled")
    _require_text(curve.unit, f"{where}.unit")


def validate_bundle(bundle: AnalysisBundle, mode: str = "real") -> list[str]:
    """Raise DataContractError on violations; return non-fatal warnings."""
    if mode not in MODES:
        raise DataContractError(f"unknown mode {mode!r}")
    for name in (
        "sample_id", "product_id", "product_family", "design_version",
        "design_lineage_id", "electrode_a_role", "layer_a", "fab_id",
    ):
        _require_text(getattr(bundle, name), name)
    sid = bundle.sample_id
    if bundle.failure_type not in FAILURE_TYPES:
        raise DataContractError(f"{sid}: unknown failure_type {bundle.failure_type!r}")
    if bundle.is_open:
        if bundle.electrode_b_role != NOT_APPLICABLE or bundle.layer_b != NOT_APPLICABLE:
            raise DataContractError(f"{sid}: open failure must use NOT_APPLICABLE for electrode b")
    else:
        for name in ("electrode_b_role", "layer_b"):
            value = getattr(bundle, name)
            _require_text(value, name)
            if value == NOT_APPLICABLE:
                raise DataContractError(f"{sid}: short failure requires {name}")
    warnings: list[str] = []
    if bundle.failure_type == "vertical_short" and not bundle.assigned_process_layer:
        message = f"{sid}: vertical_short requires explicit assigned_process_layer (never inferred)"
        if mode == "real":
            raise DataContractError(message)
        warnings.append(message)
    for name in ("unit_width", "unit_height"):
        value = getattr(bundle, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise DataContractError(f"{sid}: {name} must be positive when given")
    if (bundle.unit_width is not None or bundle.unit_height is not None) and not bundle.physical_length_unit:
        raise DataContractError(f"{sid}: unit size given without physical_length_unit")
    if not bundle.has_physical_scale():
        warnings.append(f"{sid}: no physical unit-pixel size; physical length comparison impossible")
    expected_layouts = 1 if bundle.is_open else 2
    if len(bundle.layouts) > expected_layouts:
        raise DataContractError(f"{sid}: {len(bundle.layouts)} layouts for {bundle.failure_type}")
    if len(bundle.layouts) < expected_layouts:
        warnings.append(f"{sid}: layout images missing")
    for image in bundle.layouts + bundle.hotspots:
        if image.value_semantics not in IMAGE_SEMANTICS:
            raise DataContractError(f"{sid}: unknown image semantics {image.value_semantics!r}")
        if image.value_semantics == "probability" and not image.legend_id:
            raise DataContractError(f"{sid}: probability image requires an explicit legend")
        if image.pixel_size is not None and image.pixel_size <= 0:
            raise DataContractError(f"{sid}: pixel_size must be positive")
    if not bundle.hotspots:
        warnings.append(f"{sid}: hotspot images missing")
    if bundle.ca is None:
        warnings.append(f"{sid}: CA curve missing")
    else:
        validate_curve(bundle.ca, f"{sid}.ca")
    return warnings


def validate_fab(dist: FabDistribution) -> None:
    did = dist.fab_distribution_id
    _require_text(did, "fab_distribution_id")
    for name in ("fab_id", "process_layer", "value_unit", "size_unit"):
        _require_text(getattr(dist, name), f"{did}.{name}")
    if dist.value_kind not in VALUE_KINDS:
        raise DataContractError(f"{did}: unknown value_kind {dist.value_kind!r}")
    lower = np.asarray(dist.bin_lower, dtype=float)
    upper = np.asarray(dist.bin_upper, dtype=float)
    values = np.asarray(dist.raw_values, dtype=float)
    observed = np.asarray(dist.observed, dtype=bool)
    if not (len(lower) == len(upper) == len(values) == len(observed)) or len(lower) == 0:
        raise DataContractError(f"{did}: bin and value arrays must be non-empty and equal length")
    if np.any(~np.isfinite(lower)) or np.any(~np.isfinite(upper)) or np.any(lower >= upper):
        raise DataContractError(f"{did}: each bin needs finite lower < upper")
    if np.any(upper[:-1] > lower[1:]):
        raise DataContractError(f"{did}: bins must be sorted and non-overlapping")
    if np.any(~np.isfinite(values[observed])) or np.any(values[observed] < 0):
        raise DataContractError(f"{did}: observed values must be finite and >= 0")
    if np.any(~np.isnan(values[~observed])):
        raise DataContractError(f"{did}: unobserved values must be NaN")
    if dist.value_kind == "count" and dist.surveyed_area is None:
        raise DataContractError(f"{did}: count data requires surveyed_area")
    if dist.surveyed_area is not None and (dist.surveyed_area <= 0 or not dist.area_unit):
        raise DataContractError(f"{did}: surveyed_area must be positive with area_unit")


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def group_size_records(records: list[dict]) -> dict[str, CACurve]:
    """Convert per-size records {sample_id, particle_size, ca_value, ca_unit} to curves.

    A blank ca_value means "not observed" and stays NaN; it is never 0.
    """
    grouped: dict[str, dict[float, float | None]] = {}
    units: dict[str, str] = {}
    for record in records:
        sid = str(record["sample_id"])
        size = float(record["particle_size"])
        unit = str(record["ca_unit"])
        if sid in units and units[sid] != unit:
            raise DataContractError(f"{sid}: mixed CA units {units[sid]!r} and {unit!r}")
        units[sid] = unit
        sizes = grouped.setdefault(sid, {})
        if size in sizes:
            raise DataContractError(f"{sid}: duplicate CA record for particle size {size}")
        raw = record.get("ca_value")
        sizes[size] = None if _is_blank(raw) else float(raw)
    curves: dict[str, CACurve] = {}
    for sid, by_size in grouped.items():
        ordered = sorted(by_size)
        observed = np.array([by_size[s] is not None for s in ordered], dtype=bool)
        values = np.array(
            [np.nan if by_size[s] is None else by_size[s] for s in ordered], dtype=float
        )
        curve = CACurve(np.array(ordered, dtype=float), values, observed, units[sid])
        validate_curve(curve, f"{sid}.ca")
        curves[sid] = curve
    return curves
