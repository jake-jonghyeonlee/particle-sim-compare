"""공통 데이터 계약(schema).

입력 어댑터, 전처리, 모델, 분석 모듈이 모두 이 구조만 주고받는다.
원본 파일 형식은 어댑터가 흡수하므로 여기에는 파일 형식이 나타나지 않는다.

기본 원칙
- 누락값은 None/NaN과 관측 마스크로 표현하고 0으로 채우지 않는다.
- lateral_open의 두 번째 전극은 누락이 아니라 NOT_APPLICABLE(해당 없음)이다.
- 물리적 의미가 불명확한 입력은 DataContractError로 거부한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

FAILURE_TYPES = ("lateral_open", "lateral_short", "vertical_short")
OPEN_TYPES = ("lateral_open",)
NOT_APPLICABLE = "N/A"
VALUE_KINDS = ("count", "average_count_per_glass", "probability", "rate_per_area", "density")
MODES = ("demo", "real")
IMAGE_SEMANTICS = ("binary", "processed_intensity", "probability")
LENGTH_UNIT_TO_UM = {
    "nm": 1e-3,
    "um": 1.0,
    "µm": 1.0,
    "μm": 1.0,
    "mm": 1e3,
}
DIMENSIONLESS_UNITS = ("1", "dimensionless")


class DataContractError(ValueError):
    """입력이 공통 데이터 계약을 위반했을 때 발생하는 오류."""


def normalize_length_unit(unit: str) -> str:
    """지원하는 길이 단위를 정규화한다. 마이크로미터 표기는 모두 ``um``으로 통일한다."""
    if not isinstance(unit, str) or not unit.strip():
        raise DataContractError("길이 단위가 없거나 비어 있습니다")
    value = unit.strip()
    if value not in LENGTH_UNIT_TO_UM:
        raise DataContractError(f"지원하지 않는 길이 단위 {unit!r}: nm, um, mm만 허용합니다")
    return "um" if value in ("um", "µm", "μm") else value


def convert_length(value, from_unit: str, to_unit: str = "um"):
    """스칼라 또는 NumPy 배열 길이를 지원 단위 사이에서 변환한다."""
    source = normalize_length_unit(from_unit)
    target = normalize_length_unit(to_unit)
    factor = LENGTH_UNIT_TO_UM[source] / LENGTH_UNIT_TO_UM[target]
    converted = np.asarray(value, dtype=float) * factor
    return float(converted) if converted.ndim == 0 else converted


def convert_area(value, from_unit: str, to_length_unit: str = "um"):
    """nm^2, um^2, mm^2 면적을 목표 길이 단위의 제곱으로 변환한다."""
    if not isinstance(from_unit, str):
        raise DataContractError("면적 단위가 없거나 비어 있습니다")
    text = from_unit.strip().replace("²", "^2")
    if not text.endswith("^2"):
        raise DataContractError(f"지원하지 않는 면적 단위 {from_unit!r}: nm^2, um^2, mm^2만 허용합니다")
    source = text[:-2]
    factor = convert_length(1.0, source, to_length_unit) ** 2
    converted = np.asarray(value, dtype=float) * factor
    return float(converted) if converted.ndim == 0 else converted


def is_dimensionless_unit(unit: str) -> bool:
    """CA처럼 무차원인 값의 허용 단위 표기인지 확인한다."""
    return isinstance(unit, str) and unit.strip().lower() in DIMENSIONLESS_UNITS


@dataclass
class ImageInput:
    """전극별 layout 또는 이물 크기별 hotspot 이미지 한 장.

    array는 2차원 float32 배열이다. value_semantics는 값의 의미를 나타내며
    binary(layout), processed_intensity(가공 hotspot 강도), probability 중 하나다.
    probability는 명시적 범례(legend_id)가 있을 때만 허용한다.
    """
    role: str
    array: np.ndarray | None
    path: str | None = None
    pixel_size: float | None = None
    origin_xy: tuple[float, float] | None = None
    particle_size: float | None = None
    particle_size_unit: str | None = None
    value_semantics: str = "binary"
    legend_id: str | None = None


@dataclass
class CACurve:
    """이물 크기별 CA(접촉 확률이 반영된 critical area) 곡선.

    particle_sizes는 엄격히 증가해야 하고, 관측되지 않은 크기의 values는 NaN,
    observed는 False다. unit은 legacy 무차원 "1" 또는 원 CA 면적(nm^2, um^2, mm^2)이며,
    particle_size_unit은 원형 이물 지름의 단위다. calc_settings에 계산 설정을 보존한다.
    """
    particle_sizes: np.ndarray
    values: np.ndarray
    observed: np.ndarray
    unit: str
    particle_size_unit: str = "um"
    calc_settings: dict = field(default_factory=dict)


@dataclass
class FabDistribution:
    """Fab·제품군·공정 layer·이물 크기 구간별 원자료 또는 가공 요약자료.

    value_kind(count, average_count_per_glass, probability, rate_per_area, density)는 서로 다른 의미이므로
    같은 방식으로 처리하지 않는다. is_reference가 True이면 순위 비교용 지정 기준 분포다.
    """
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
    averaging_basis: str | None = None
    is_reference: bool = False
    fab_pair_key: str | None = None


@dataclass
class Geometry:
    """적층 두께와 선택적 형상 수치. 수치의 물리적 정의는 해석하지 않고 그대로 전달한다."""
    thickness: dict[str, float] = field(default_factory=dict)
    shape_metrics: dict[str, float] = field(default_factory=dict)
    length_unit: str | None = None


@dataclass
class Provenance:
    """계산 설정, 계산 버전, 원본 파일, 합성 데이터 여부 등 추적 정보."""
    adapter: str
    source_files: list[str] = field(default_factory=list)
    calc_settings: dict = field(default_factory=dict)
    calc_version: str | None = None
    is_synthetic: bool = False


@dataclass
class AnalysisBundle:
    """분석 묶음: 제품 × 설계 버전 × 전극 역할 조합 × 불량 종류의 크기별 결과.

    layouts는 open이면 전극 1개(slot a), short이면 2개(slot a, b) 순서다.
    unit_width·unit_height·physical_length_unit이 모두 있어야 실제 크기 비교가 가능하다.
    """
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
    fab_pair_key: str | None = None
    mapping_contract_version: str | None = None

    @property
    def is_open(self) -> bool:
        """open 계열 불량(단일 전극)인지 여부."""
        return self.failure_type in OPEN_TYPES

    def role_pair(self) -> tuple[str, ...]:
        """해당 없음을 제외하고 정렬한 전극 역할 튜플. 전극 순서와 무관하게 비교할 때 쓴다."""
        roles = [self.electrode_a_role, self.electrode_b_role]
        return tuple(sorted(r for r in roles if r != NOT_APPLICABLE))

    def layer_pair(self) -> tuple[str, ...]:
        """해당 없음을 제외하고 정렬한 layer 튜플."""
        layers = [self.layer_a, self.layer_b]
        return tuple(sorted(x for x in layers if x != NOT_APPLICABLE))

    def has_physical_scale(self) -> bool:
        """unit-pixel의 가로·세로 실제 크기와 길이 단위가 모두 있는지 여부."""
        return (
            self.unit_width is not None
            and self.unit_height is not None
            and bool(self.physical_length_unit)
        )

    def unit_area(self, target_length_unit: str = "um") -> float | None:
        """target_length_unit으로 환산한 unit-pixel 면적. 실제 크기 정보가 없으면 None."""
        if not self.has_physical_scale():
            return None
        width = convert_length(self.unit_width, self.physical_length_unit, target_length_unit)
        height = convert_length(self.unit_height, self.physical_length_unit, target_length_unit)
        return float(width) * float(height)

    def ca_area_unit(self, target_length_unit: str = "um") -> str:
        """면적 정규화 결과의 단위를 반환한다."""
        target = normalize_length_unit(target_length_unit)
        if self.ca is None or is_dimensionless_unit(self.ca.unit):
            return f"1/{target}^2"
        convert_area(1.0, self.ca.unit, target)
        return "1"

    def ca_per_area(self, target_length_unit: str = "um") -> tuple[np.ndarray | None, str]:
        """CA를 unit-pixel 면적으로 나눈 값과 사유를 반환한다.

        legacy 무차원 CA는 길이 제곱당 위험 지표가 된다.
        원 CA 면적은 목표 길이 제곱으로 환산한 뒤 나누어 무차원 비가 된다.
        """
        if self.ca is None:
            return None, "CA 곡선 없음"
        area = self.unit_area(target_length_unit)
        if area is None:
            return None, "unit-pixel 실제 크기 없음: 물리 길이·면적 비교 불가"
        values = self.ca.values if is_dimensionless_unit(self.ca.unit) else convert_area(
            self.ca.values, self.ca.unit, target_length_unit
        )
        return values / area, "ok"


def _require_text(value: object, name: str) -> None:
    """문자열 필드가 비어 있으면 DataContractError를 낸다."""
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"필수 필드 {name}이(가) 없거나 비어 있습니다")


def validate_curve(curve: CACurve, where: str = "ca") -> None:
    """CA 곡선의 배열 길이, 크기 증가, 관측값 범위, 미관측 NaN, 단위를 검사한다."""
    sizes = np.asarray(curve.particle_sizes, dtype=float)
    values = np.asarray(curve.values, dtype=float)
    observed = np.asarray(curve.observed, dtype=bool)
    if not (sizes.ndim == values.ndim == observed.ndim == 1) or not (
        len(sizes) == len(values) == len(observed)
    ):
        raise DataContractError(f"{where}: 크기·값·관측 여부 배열은 길이가 같은 1차원이어야 합니다")
    if len(sizes) == 0 or np.any(~np.isfinite(sizes)) or np.any(sizes <= 0):
        raise DataContractError(f"{where}: 이물 크기는 유한한 양수여야 합니다")
    if np.any(np.diff(sizes) <= 0):
        raise DataContractError(f"{where}: 이물 크기는 엄격히 증가해야 합니다")
    _require_text(curve.unit, f"{where}.unit")
    dimensionless = is_dimensionless_unit(curve.unit)
    if not dimensionless:
        convert_area(1.0, curve.unit)
    if np.any(~np.isfinite(values[observed])) or np.any(values[observed] < 0):
        raise DataContractError(f"{where}: 관측된 CA 값은 0 이상의 유한한 값이어야 합니다")
    if dimensionless and np.any(values[observed] > 1):
        raise DataContractError(f"{where}: 관측된 CA 값은 0 이상 1 이하의 유한한 무차원 값이어야 합니다")
    if np.any(~np.isnan(values[~observed])):
        raise DataContractError(f"{where}: 미관측 CA 값은 채우지 말고 NaN이어야 합니다")
    normalize_length_unit(curve.particle_size_unit)


def canonicalize_ca(curve: CACurve, length_unit: str = "um") -> CACurve:
    """Validate and convert area CA in place; repeated conversion is idempotent."""
    validate_curve(curve)
    if not is_dimensionless_unit(curve.unit):
        target = normalize_length_unit(length_unit)
        curve.values = convert_area(curve.values, curve.unit, target)
        curve.unit = f"{target}^2"
    return curve


def prepare_ca_bundles(bundles: list[AnalysisBundle], mode: str,
                       length_unit: str = "um") -> list[str]:
    """Enforce CA families before comparison and canonicalize every area curve."""
    families = {"dimensionless": [], "area": []}
    warnings = []
    for bundle in bundles:
        if bundle.ca is None:
            continue
        canonicalize_ca(bundle.ca, length_unit)
        family = "dimensionless" if is_dimensionless_unit(bundle.ca.unit) else "area"
        families[family].append(bundle.sample_id)
        version = bundle.provenance.calc_version if bundle.provenance else None
        if family == "dimensionless" and version and version.startswith("particle-sim-"):
            message = (f"{bundle.sample_id}: pre-fix particle-sim export has dimensionless CA "
                       "already normalized by ROI; re-export with the fixed particle-sim exporter")
            if mode == "real":
                raise DataContractError(message)
            warnings.append(message)
    if all(families.values()):
        message = "Mixed CA unit families cannot be compared until a conversion basis is confirmed: " + "; ".join(
            f"{name} count={len(ids)}, samples={ids[:3]}" for name, ids in families.items())
        if mode == "real":
            raise DataContractError(message)
        warnings.append(message)
    return warnings


def validate_bundle(bundle: AnalysisBundle, mode: str = "real") -> list[str]:
    """분석 묶음을 검사해 치명적 위반은 오류로, 나머지는 경고 목록으로 반환한다.

    mode가 real이면 vertical_short의 assigned_process_layer 누락도 오류다.
    demo에서는 같은 상황을 경고로만 남긴다(적용 layer를 추정하지 않음).
    """
    if mode not in MODES:
        raise DataContractError(f"알 수 없는 mode {mode!r}")
    for name in (
        "sample_id", "product_id", "product_family", "design_version",
        "design_lineage_id", "electrode_a_role", "layer_a", "fab_id",
    ):
        _require_text(getattr(bundle, name), name)
    sid = bundle.sample_id
    if bundle.failure_type not in FAILURE_TYPES:
        raise DataContractError(f"{sid}: 알 수 없는 failure_type {bundle.failure_type!r}")
    if bundle.is_open:
        if bundle.electrode_b_role != NOT_APPLICABLE or bundle.layer_b != NOT_APPLICABLE:
            raise DataContractError(f"{sid}: open 불량의 두 번째 전극은 NOT_APPLICABLE(해당 없음)이어야 합니다")
    else:
        for name in ("electrode_b_role", "layer_b"):
            value = getattr(bundle, name)
            _require_text(value, name)
            if value == NOT_APPLICABLE:
                raise DataContractError(f"{sid}: short 불량에는 {name}이(가) 필요합니다")
    validate_mapping_contract(bundle)
    warnings: list[str] = []
    if bundle.failure_type == "vertical_short" and not bundle.assigned_process_layer:
        message = f"{sid}: vertical_short는 assigned_process_layer를 명시해야 합니다(추정하지 않음)"
        if mode == "real":
            raise DataContractError(message)
        warnings.append(message)
    for name in ("unit_width", "unit_height"):
        value = getattr(bundle, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise DataContractError(f"{sid}: {name}은(는) 양수여야 합니다")
    if (bundle.unit_width is not None or bundle.unit_height is not None) and not bundle.physical_length_unit:
        raise DataContractError(f"{sid}: physical_length_unit 없이 unit 크기가 주어졌습니다")
    if not bundle.has_physical_scale():
        warnings.append(f"{sid}: unit-pixel 실제 크기 없음: 물리 길이 비교 불가")
    expected_layouts = 1 if bundle.is_open else 2
    if len(bundle.layouts) > expected_layouts:
        raise DataContractError(f"{sid}: {bundle.failure_type}에 layout이 {len(bundle.layouts)}개로 너무 많습니다")
    if len(bundle.layouts) < expected_layouts:
        warnings.append(f"{sid}: layout 이미지 누락")
    for image in bundle.layouts + bundle.hotspots:
        if image.value_semantics not in IMAGE_SEMANTICS:
            raise DataContractError(f"{sid}: 알 수 없는 이미지 의미 {image.value_semantics!r}")
        if image.value_semantics == "probability" and not image.legend_id:
            raise DataContractError(f"{sid}: 확률 이미지는 명시적 범례가 필요합니다")
        if image.pixel_size is not None and image.pixel_size <= 0:
            raise DataContractError(f"{sid}: pixel_size는 양수여야 합니다")
        if image.particle_size is not None:
            if image.particle_size <= 0 or not math.isfinite(image.particle_size):
                raise DataContractError(f"{sid}: hotspot 이물 지름은 유한한 양수여야 합니다")
            if image.particle_size_unit is None:
                raise DataContractError(f"{sid}: hotspot 이물 지름 단위가 필요합니다")
            normalize_length_unit(image.particle_size_unit)
    if not bundle.hotspots:
        warnings.append(f"{sid}: hotspot 이미지 누락")
    if bundle.ca is None:
        warnings.append(f"{sid}: CA 곡선 누락")
    else:
        validate_curve(bundle.ca, f"{sid}.ca")
    return warnings


def validate_fab(dist: FabDistribution) -> None:
    """Fab 분포의 필수 단위, 값 종류, 구간 정렬·비중첩, 관측값, 조사 면적 조건을 검사한다."""
    did = dist.fab_distribution_id
    _require_text(did, "fab_distribution_id")
    if dist.fab_pair_key is not None:
        if not isinstance(dist.fab_pair_key, str):
            raise DataContractError(f"{did}: fab_pair_key must be text")
        if not dist.fab_pair_key.strip():
            dist.fab_pair_key = None
        elif dist.fab_pair_key != dist.fab_pair_key.strip():
            raise DataContractError(f"{did}: fab_pair_key has edged whitespace")
    for name in ("fab_id", "process_layer", "value_unit", "size_unit"):
        _require_text(getattr(dist, name), f"{did}.{name}")
    if dist.value_kind not in VALUE_KINDS:
        raise DataContractError(f"{did}: 알 수 없는 value_kind {dist.value_kind!r}")
    lower = np.asarray(dist.bin_lower, dtype=float)
    upper = np.asarray(dist.bin_upper, dtype=float)
    values = np.asarray(dist.raw_values, dtype=float)
    observed = np.asarray(dist.observed, dtype=bool)
    if not (len(lower) == len(upper) == len(values) == len(observed)) or len(lower) == 0:
        raise DataContractError(f"{did}: 구간·값 배열은 비어 있지 않고 길이가 같아야 합니다")
    if np.any(~np.isfinite(lower)) or np.any(~np.isfinite(upper)) or np.any(lower >= upper):
        raise DataContractError(f"{did}: 각 구간은 유한한 lower < upper여야 합니다")
    if np.any(upper[:-1] > lower[1:]):
        raise DataContractError(f"{did}: 구간은 정렬되고 겹치지 않아야 합니다")
    if np.any(~np.isfinite(values[observed])) or np.any(values[observed] < 0):
        raise DataContractError(f"{did}: 관측값은 0 이상의 유한값이어야 합니다")
    if np.any(~np.isnan(values[~observed])):
        raise DataContractError(f"{did}: 미관측 값은 NaN이어야 합니다")
    if dist.value_kind in ("count", "average_count_per_glass") and dist.surveyed_area is None:
        raise DataContractError(f"{did}: count 자료에는 조사 면적(surveyed_area)이 필요합니다")
    if dist.surveyed_area is not None and (dist.surveyed_area <= 0 or not dist.area_unit):
        raise DataContractError(f"{did}: surveyed_area는 양수이고 area_unit이 있어야 합니다")
    if dist.surveyed_area is not None:
        convert_area(dist.surveyed_area, dist.area_unit)
    if dist.value_kind == "average_count_per_glass":
        if dist.averaging_basis != "per_glass":
            raise DataContractError(f"{did}: average_count_per_glass는 averaging_basis='per_glass'여야 합니다")
        if not dist.period:
            raise DataContractError(f"{did}: average_count_per_glass에는 측정 기간(period)이 필요합니다")


def _is_blank(value: object) -> bool:
    """None, NaN, 공백 문자열을 '값 없음'으로 판단한다."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def group_size_records(records: list[dict]) -> dict[str, CACurve]:
    """크기별 개별 CA 기록을 묶음별 CACurve로 변환한다.

    기록 형식은 {sample_id, particle_size, particle_size_unit, ca_value, ca_unit}이다. ca_value가 비어 있으면
    미관측(NaN)으로 두고 0으로 바꾸지 않는다. 같은 크기 중복이나 단위 혼합은 오류다.
    """
    grouped: dict[str, dict[float, float | None]] = {}
    units: dict[str, str] = {}
    size_units: dict[str, str] = {}
    for record in records:
        sid = str(record["sample_id"])
        size = float(record["particle_size"])
        unit = str(record["ca_unit"])
        size_unit = normalize_length_unit(str(record["particle_size_unit"]))
        if sid in units and units[sid] != unit:
            raise DataContractError(f"{sid}: CA 단위가 섞여 있습니다 ({units[sid]!r}, {unit!r})")
        units[sid] = unit
        if sid in size_units and size_units[sid] != size_unit:
            raise DataContractError(
                f"{sid}: CA 이물 지름 단위가 섞여 있습니다 ({size_units[sid]!r}, {size_unit!r})"
            )
        size_units[sid] = size_unit
        sizes = grouped.setdefault(sid, {})
        if size in sizes:
            raise DataContractError(f"{sid}: 이물 크기 {size}의 CA 기록이 중복되었습니다")
        raw = record.get("ca_value")
        sizes[size] = None if _is_blank(raw) else float(raw)
    curves: dict[str, CACurve] = {}
    for sid, by_size in grouped.items():
        ordered = sorted(by_size)
        observed = np.array([by_size[s] is not None for s in ordered], dtype=bool)
        values = np.array(
            [np.nan if by_size[s] is None else by_size[s] for s in ordered], dtype=float
        )
        curve = CACurve(
            np.array(ordered, dtype=float), values, observed, units[sid],
            particle_size_unit=size_units[sid],
        )
        validate_curve(curve, f"{sid}.ca")
        curves[sid] = curve
    return curves


def validate_mapping_contract(bundle: AnalysisBundle) -> None:
    """Validate the explicit transition before applying either assignment policy."""
    version = bundle.mapping_contract_version
    if version not in (None, "1.1", "1.2"):
        raise DataContractError(f"{bundle.sample_id}: unknown mapping_contract_version {version!r}")
    if version != "1.2":
        if bundle.fab_pair_key is not None:
            raise DataContractError(f"{bundle.sample_id}: fab_pair_key requires mapping_contract_version 1.2")
        return
    for name in ("assigned_process_layer", "fab_pair_key"):
        value = getattr(bundle, name)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise DataContractError(f"{bundle.sample_id}: 1.2 requires nonblank {name} without edged whitespace")
    suffix = f":{bundle.layer_a}-{bundle.layer_b}"
    if not bundle.fab_pair_key.endswith(suffix) or not bundle.fab_pair_key[:-len(suffix)]:
        raise DataContractError(f"{bundle.sample_id}: fab_pair_key must retain endpoint suffix {suffix!r}")


def make_fab_pair_key(unit: str, layer_a: str, layer_b: str, registry: dict) -> str:
    """Build an ordered raw key, rejecting ambiguous concatenations."""
    raw = (unit, layer_a, layer_b)
    if any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in raw):
        raise DataContractError("fab_pair_key components must be nonblank without edged whitespace")
    key = f"{unit}:{layer_a}-{layer_b}"
    if key in registry and registry[key] != raw:
        raise DataContractError(f"fab_pair_key collision {key!r}: {registry[key]!r} and {raw!r}")
    registry[key] = raw
    return key
