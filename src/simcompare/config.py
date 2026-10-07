"""YAML 설정을 엄격하게 검증해 중첩 dataclass로 읽는다.

어느 단계에서든 알 수 없는 키는 점으로 구분한 경로와 함께 ConfigError를 낸다.
누락값을 조용히 채우지 않으며, 기본값이 있는 항목에 null을 명시하면 오류다.
mode가 real이면 demo 규칙을 거부하고, CAA 규칙 사용에는 confirmed: true가 필요하다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

from simcompare.schema import DataContractError, normalize_length_unit
from simcompare.regional_schema import DEFAULT_CONSERVATION_ATOL_UM2, DEFAULT_CONSERVATION_RTOL


class ConfigError(DataContractError):
    """설정 파일이 설정 계약을 위반했을 때 발생하는 오류."""


# ---------------------------------------------------------------------------
# 설정 dataclass
# ---------------------------------------------------------------------------

@dataclass
class DataConfig:
    """데이터 경로, CA·hotspot·Fab 크기 격자, 이미지 크기, 곡선 정렬 방식, hotspot 범례."""
    data_dir: str
    ca_grid: list[float]
    hotspot_sizes: list[float]
    fab_grid: list[float]
    canonical_length_unit: str = "um"
    image_size: list[int] = field(default_factory=lambda: [64, 64])
    ca_alignment: str = "exact"
    hotspot_legend: dict | None = None


@dataclass
class CAAConfig:
    """CAA 규칙과 확정 여부, 기준 분포 ID, 크기 전체 포함 요구 여부, 적용 layer 지정값."""
    rule: str | None = None
    confirmed: bool = False
    reference_distribution_id: str | None = None
    require_full_coverage: bool = True
    process_layer_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class ModelSection:
    """활성 입력, 합성곱 채널, 입력별·결합 특징 크기, 입력별 손실 가중치."""
    enabled_inputs: list[str] = field(
        default_factory=lambda: ["layout", "hotspot", "ca", "fab", "geometry"]
    )
    conv_channels: list[int] = field(default_factory=lambda: [8, 16, 32])
    modality_dim: int = 16
    latent_dim: int = 32
    loss_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class TrainConfig:
    """에폭, 배치 크기, 학습률, seed, 실행 장치, 제품 균형 샘플링 여부."""
    epochs: int = 30
    batch_size: int = 16
    lr: float = 1e-3
    seed: int = 0
    device: str = "cpu"
    balance_products: bool = True


@dataclass
class SplitConfig:
    """평가 비율 또는 평가로 보낼 제품·설계 계열 ID, 분리 seed."""
    test_fraction: float = 0.25
    test_groups: list[str] = field(default_factory=list)
    seed: int = 0


@dataclass
class RegionalConfig:
    """지역 보고/적분 설정. 표시 여백은 정량 창이나 입자 모델을 바꾸지 않는다."""
    enabled: bool = True
    correspondence_file: str | None = None
    source_manifest: str | None = None
    connectivity: int = 8
    merge_gap_um: float = 0.0
    display_context_margin_um: float = 0.0
    conservation_rtol: float = DEFAULT_CONSERVATION_RTOL
    conservation_atol_um2: float = DEFAULT_CONSERVATION_ATOL_UM2
    # 좌표/증감은 절대 오차만 사용한다. 큰 원점이나 0 baseline으로 오차를 확대하지 않는다.
    coordinate_atol_um: float = 1e-9
    delta_atol_um2: float = 1e-9
    include_area_normalized: bool = False


@dataclass
class AnalysisConfig:
    """이웃 수, 군집 수, layer 조건, 다른 제품만 보기, 필터, 크기 허용 비율, 제품 제외 비교 상위 개수,
    유사 제품 비교군을 만들 공간, 좁은 간격 구간을 셀 여유 폭(canonical 길이 단위)."""
    k_neighbors: int = 5
    n_clusters: int = 4
    layer_policy: str = "exact"
    other_products_only: bool = True
    filters: dict[str, list] = field(default_factory=dict)
    size_tolerance: float | None = None
    loo_top_k: int = 5
    neighborhood_space: str = "with_risk_ae"
    pair_near_margin: float = 1.0
    regional: RegionalConfig = field(default_factory=RegionalConfig)


@dataclass
class AppConfig:
    """전체 설정."""
    mode: str
    output_dir: str
    data: DataConfig
    caa: CAAConfig
    model: ModelSection
    train: TrainConfig
    split: SplitConfig
    analysis: AnalysisConfig


# ---------------------------------------------------------------------------
# 검증 도우미
# ---------------------------------------------------------------------------

KNOWN_INPUTS = ("layout", "hotspot", "ca", "fab", "geometry")
CA_ALIGNMENT_VALUES = ("exact", "loglinear_within_range")
MODE_VALUES = ("demo", "real")
DEVICE_VALUES = ("cpu", "auto", "cuda", "mps")
LAYER_POLICY_VALUES = ("exact", "ignore")
NEIGHBORHOOD_SPACE_VALUES = ("with_risk_ae", "ae_struct", "ae_fab", "shape_only", "with_risk_baseline")


def _is_int(value: Any) -> bool:
    """bool을 제외한 정수인지 확인한다."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_float(value: Any) -> bool:
    """bool을 제외한 유한한 숫자인지 확인한다(NaN/inf 거부)."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _number_error(value: Any) -> str:
    """숫자 검증 실패 메시지를 만든다."""
    if isinstance(value, float) and not math.isfinite(value):
        return f"유한한 숫자여야 합니다(현재 {value})"
    return f"숫자여야 합니다(현재 {type(value).__name__})"


def _check_unknown(raw: dict, allowed: set[str], path: str) -> None:
    """허용되지 않은 키가 있으면 경로와 함께 ConfigError를 낸다."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: 매핑이어야 합니다")
    for key in raw:
        if key not in allowed:
            dotted = f"{path}.{key}" if path else str(key)
            raise ConfigError(f"알 수 없는 설정 키 {dotted!r}")


def _require_str(raw: dict, key: str, path: str) -> str:
    """필수 문자열 키를 읽는다."""
    if key not in raw:
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"필수 설정 키 누락 {dotted!r}")
    value = raw[key]
    if not isinstance(value, str):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: 문자열이어야 합니다(현재 {type(value).__name__})")
    return value


def _opt_str(raw: dict, key: str, path: str, *, allow_none: bool = False) -> str | None:
    """선택 문자열 키를 읽는다. 키가 없으면 None, null은 allow_none일 때만 허용."""
    if key not in raw:
        return None
    value = raw[key]
    if value is None:
        if allow_none:
            return None
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: null일 수 없습니다")
    if not isinstance(value, str):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: 문자열이어야 합니다(현재 {type(value).__name__})")
    return value


def _opt_float(raw: dict, key: str, path: str, *, allow_none: bool = False) -> float | None:
    """선택 숫자 키를 읽는다. 키가 없으면 None, null은 allow_none일 때만 허용."""
    if key not in raw:
        return None
    value = raw[key]
    if value is None:
        if allow_none:
            return None
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: null일 수 없습니다")
    if not _is_float(value):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: {_number_error(value)}")
    return float(value)


def _opt_int(raw: dict, key: str, path: str, *, allow_none: bool = False) -> int | None:
    """선택 정수 키를 읽는다. 키가 없으면 None, null은 allow_none일 때만 허용."""
    if key not in raw:
        return None
    value = raw[key]
    if value is None:
        if allow_none:
            return None
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: null일 수 없습니다")
    if not _is_int(value):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: 정수여야 합니다(현재 {type(value).__name__})")
    return value


def _opt_bool(raw: dict, key: str, path: str, *, allow_none: bool = False) -> bool | None:
    """선택 bool 키를 읽는다. 키가 없으면 None, null은 allow_none일 때만 허용."""
    if key not in raw:
        return None
    value = raw[key]
    if value is None:
        if allow_none:
            return None
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: null일 수 없습니다")
    if not isinstance(value, bool):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: true/false여야 합니다(현재 {type(value).__name__})")
    return value


def _opt_dict(raw: dict, key: str, path: str, *, allow_none: bool = False) -> dict | None:
    """선택 매핑 키를 읽는다. 키가 없으면 None, null은 allow_none일 때만 허용."""
    if key not in raw:
        return None
    value = raw[key]
    if value is None:
        if allow_none:
            return None
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: null일 수 없습니다")
    if not isinstance(value, dict):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: 매핑이어야 합니다(현재 {type(value).__name__})")
    return value


def _opt_list(raw: dict, key: str, path: str, *, allow_none: bool = False) -> list | None:
    """선택 리스트 키를 읽는다. 키가 없으면 None, null은 allow_none일 때만 허용."""
    if key not in raw:
        return None
    value = raw[key]
    if value is None:
        if allow_none:
            return None
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: null일 수 없습니다")
    if not isinstance(value, list):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: 리스트여야 합니다(현재 {type(value).__name__})")
    return value


def _check_grid(values: list[float], path: str, allow_empty: bool = False) -> list[float]:
    """크기 격자가 비어 있지 않고 유한한 양수이며 엄격히 증가하는지 확인한다."""
    if not isinstance(values, list):
        raise ConfigError(f"{path}: 리스트여야 합니다")
    if len(values) == 0:
        if allow_empty:
            return []
        raise ConfigError(f"{path}: 비어 있을 수 없습니다")
    result: list[float] = []
    for i, v in enumerate(values):
        if not _is_float(v):
            raise ConfigError(f"{path}[{i}]: {_number_error(v)}")
        fv = float(v)
        if fv <= 0:
            raise ConfigError(f"{path}[{i}]: 0보다 커야 합니다")
        result.append(fv)
    for i in range(1, len(result)):
        if result[i] <= result[i - 1]:
            raise ConfigError(f"{path}: 엄격히 증가해야 합니다")
    return result


def _check_legend(legend: dict, path: str) -> None:
    """hotspot 범례 구조를 검사한다.

    형식: {legend_id: 문자열, entries: [{rgb: [r, g, b], value: 숫자}], tolerance: 0 이상 정수}
    """
    unknown = set(legend) - {"legend_id", "entries", "tolerance"}
    if unknown:
        raise ConfigError(f"알 수 없는 설정 키 '{path}.{sorted(map(str, unknown))[0]}'")
    if not isinstance(legend.get("legend_id"), str) or not legend["legend_id"].strip():
        raise ConfigError(f"{path}.legend_id: 비어 있지 않은 문자열이 필요합니다")
    entries = legend.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}.entries: 비어 있지 않은 리스트가 필요합니다")
    for i, entry in enumerate(entries):
        where = f"{path}.entries[{i}]"
        if not isinstance(entry, dict) or set(entry) != {"rgb", "value"}:
            raise ConfigError(f"{where}: 키는 정확히 'rgb'와 'value'여야 합니다")
        rgb = entry["rgb"]
        if (not isinstance(rgb, list) or len(rgb) != 3
                or not all(_is_int(c) and 0 <= c <= 255 for c in rgb)):
            raise ConfigError(f"{where}.rgb: 0..255 범위 정수 3개여야 합니다")
        if not _is_float(entry["value"]):
            raise ConfigError(f"{where}.value: 유한한 숫자여야 합니다")
    tolerance = legend.get("tolerance", 0)
    if not _is_int(tolerance) or tolerance < 0:
        raise ConfigError(f"{path}.tolerance: 0 이상의 정수여야 합니다")


def _check_int_list(values: list[Any], path: str, positive: bool = True) -> list[int]:
    """정수 리스트를 검사한다(positive이면 양수만 허용)."""
    if not isinstance(values, list):
        raise ConfigError(f"{path}: 리스트여야 합니다")
    result: list[int] = []
    for i, v in enumerate(values):
        if not _is_int(v):
            raise ConfigError(f"{path}[{i}]: 정수여야 합니다(현재 {type(v).__name__})")
        if positive and v <= 0:
            raise ConfigError(f"{path}[{i}]: 양수여야 합니다")
        result.append(v)
    return result


# ---------------------------------------------------------------------------
# 절별 파서
# ---------------------------------------------------------------------------

def _parse_data(raw: dict, path: str = "data") -> DataConfig:
    """data 절을 읽는다."""
    _check_unknown(raw, {
        "data_dir", "ca_grid", "hotspot_sizes", "fab_grid",
        "canonical_length_unit", "image_size", "ca_alignment", "hotspot_legend",
    }, path)

    data_dir = _require_str(raw, "data_dir", path)

    if "ca_grid" not in raw:
        raise ConfigError(f"필수 설정 키 누락 {path}.ca_grid")
    ca_grid = _check_grid(raw["ca_grid"], f"{path}.ca_grid")

    if "hotspot_sizes" not in raw:
        raise ConfigError(f"필수 설정 키 누락 {path}.hotspot_sizes")
    hotspot_sizes = _check_grid(raw["hotspot_sizes"], f"{path}.hotspot_sizes", allow_empty=True)

    if "fab_grid" not in raw:
        raise ConfigError(f"필수 설정 키 누락 {path}.fab_grid")
    fab_grid = _check_grid(raw["fab_grid"], f"{path}.fab_grid")

    canonical_length_unit = raw.get("canonical_length_unit", "um")
    if not isinstance(canonical_length_unit, str):
        raise ConfigError(f"{path}.canonical_length_unit: 문자열이어야 합니다")
    try:
        canonical_length_unit = normalize_length_unit(canonical_length_unit)
    except DataContractError as exc:
        raise ConfigError(f"{path}.canonical_length_unit: {exc}") from None

    image_size = _opt_list(raw, "image_size", path)
    if image_size is None:
        image_size = [64, 64]
    else:
        if len(image_size) != 2:
            raise ConfigError(f"{path}.image_size: [H, W] 형식이어야 합니다")
        for i, v in enumerate(image_size):
            if not _is_int(v):
                raise ConfigError(f"{path}.image_size[{i}]: 정수여야 합니다")
            if v <= 0 or v % 8 != 0:
                raise ConfigError(f"{path}.image_size[{i}]: 8로 나누어떨어지는 양수여야 합니다")

    ca_alignment = raw.get("ca_alignment", "exact")
    if ca_alignment not in CA_ALIGNMENT_VALUES:
        raise ConfigError(f"{path}.ca_alignment: {CA_ALIGNMENT_VALUES} 중 하나여야 합니다")

    hotspot_legend = _opt_dict(raw, "hotspot_legend", path, allow_none=True)
    if hotspot_legend is not None:
        _check_legend(hotspot_legend, f"{path}.hotspot_legend")

    return DataConfig(
        data_dir=data_dir,
        ca_grid=ca_grid,
        hotspot_sizes=hotspot_sizes,
        fab_grid=fab_grid,
        canonical_length_unit=canonical_length_unit,
        image_size=image_size,
        ca_alignment=ca_alignment,
        hotspot_legend=hotspot_legend,
    )


def _parse_caa(raw: dict, path: str = "caa") -> CAAConfig:
    """caa 절을 읽는다."""
    _check_unknown(raw, {
        "rule", "confirmed", "reference_distribution_id",
        "require_full_coverage", "process_layer_overrides",
    }, path)

    rule = _opt_str(raw, "rule", path, allow_none=True)
    confirmed = _opt_bool(raw, "confirmed", path)
    if confirmed is None:
        confirmed = False
    reference_distribution_id = _opt_str(raw, "reference_distribution_id", path, allow_none=True)
    require_full_coverage = _opt_bool(raw, "require_full_coverage", path)
    if require_full_coverage is None:
        require_full_coverage = True

    overrides_raw = _opt_dict(raw, "process_layer_overrides", path)
    process_layer_overrides: dict[str, str] = {}
    if overrides_raw is not None:
        for k, v in overrides_raw.items():
            if not isinstance(k, str):
                raise ConfigError(f"{path}.process_layer_overrides: 키는 문자열이어야 합니다")
            if not isinstance(v, str):
                raise ConfigError(
                    f"{path}.process_layer_overrides[{k!r}]: value must be str"
                )
            process_layer_overrides[k] = v

    return CAAConfig(
        rule=rule,
        confirmed=confirmed,
        reference_distribution_id=reference_distribution_id,
        require_full_coverage=require_full_coverage,
        process_layer_overrides=process_layer_overrides,
    )


def _parse_model(raw: dict, path: str = "model") -> ModelSection:
    """model 절을 읽는다."""
    _check_unknown(raw, {
        "enabled_inputs", "conv_channels", "modality_dim",
        "latent_dim", "loss_weights",
    }, path)

    enabled_raw = _opt_list(raw, "enabled_inputs", path)
    if enabled_raw is None:
        enabled_inputs = list(KNOWN_INPUTS)
    else:
        if len(enabled_raw) == 0:
            raise ConfigError(f"{path}.enabled_inputs: 비어 있을 수 없습니다")
        for i, v in enumerate(enabled_raw):
            if not isinstance(v, str):
                raise ConfigError(f"{path}.enabled_inputs[{i}]: 문자열이어야 합니다")
            if v not in KNOWN_INPUTS:
                raise ConfigError(f"{path}.enabled_inputs[{i}]: 알 수 없는 입력 {v!r}")
        enabled_inputs = list(enabled_raw)

    conv_raw = _opt_list(raw, "conv_channels", path)
    if conv_raw is None:
        conv_channels = [8, 16, 32]
    else:
        conv_channels = _check_int_list(conv_raw, f"{path}.conv_channels")
        if not conv_channels:
            raise ConfigError(f"{path}.conv_channels: 비어 있을 수 없습니다")

    modality_dim = _opt_int(raw, "modality_dim", path)
    if modality_dim is None:
        modality_dim = 16
    elif modality_dim <= 0:
        raise ConfigError(f"{path}.modality_dim: 양수여야 합니다")

    latent_dim = _opt_int(raw, "latent_dim", path)
    if latent_dim is None:
        latent_dim = 32
    elif latent_dim < 2 or latent_dim % 2 != 0:
        raise ConfigError(f"{path}.latent_dim: 2 이상의 짝수여야 합니다")

    loss_raw = _opt_dict(raw, "loss_weights", path)
    loss_weights: dict[str, float] = {}
    if loss_raw is not None:
        for k, v in loss_raw.items():
            if not isinstance(k, str):
                raise ConfigError(f"{path}.loss_weights: 키는 문자열이어야 합니다")
            if k not in enabled_inputs:
                raise ConfigError(f"{path}.loss_weights[{k!r}]: 활성화된 입력이 아닙니다")
            if not _is_float(v):
                raise ConfigError(
                    f"{path}.loss_weights[{k!r}]: {_number_error(v)}"
                )
            if v < 0:
                raise ConfigError(f"{path}.loss_weights[{k!r}]: 0 이상이어야 합니다")
            loss_weights[k] = float(v)

    return ModelSection(
        enabled_inputs=enabled_inputs,
        conv_channels=conv_channels,
        modality_dim=modality_dim,
        latent_dim=latent_dim,
        loss_weights=loss_weights,
    )


def _parse_train(raw: dict, path: str = "train") -> TrainConfig:
    """train 절을 읽는다."""
    _check_unknown(raw, {
        "epochs", "batch_size", "lr", "seed", "device", "balance_products",
    }, path)

    epochs = _opt_int(raw, "epochs", path)
    if epochs is None:
        epochs = 30
    elif epochs <= 0:
        raise ConfigError(f"{path}.epochs: 양수여야 합니다")

    batch_size = _opt_int(raw, "batch_size", path)
    if batch_size is None:
        batch_size = 16
    elif batch_size <= 0:
        raise ConfigError(f"{path}.batch_size: 양수여야 합니다")

    lr = _opt_float(raw, "lr", path)
    if lr is None:
        lr = 1e-3
    elif lr <= 0:
        raise ConfigError(f"{path}.lr: 양수여야 합니다")

    seed = _opt_int(raw, "seed", path)
    if seed is None:
        seed = 0

    device = raw.get("device", "cpu")
    if device not in DEVICE_VALUES:
        raise ConfigError(f"{path}.device: {DEVICE_VALUES} 중 하나여야 합니다")

    balance_products = _opt_bool(raw, "balance_products", path)
    if balance_products is None:
        balance_products = True

    return TrainConfig(
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        seed=seed,
        device=device,
        balance_products=balance_products,
    )


def _parse_split(raw: dict, path: str = "split") -> SplitConfig:
    """split 절을 읽는다."""
    _check_unknown(raw, {"test_fraction", "test_groups", "seed"}, path)

    test_fraction = _opt_float(raw, "test_fraction", path)
    if test_fraction is None:
        test_fraction = 0.25
    elif not (0 < test_fraction < 1):
        raise ConfigError(f"{path}.test_fraction: 0과 1 사이여야 합니다")

    test_groups = _opt_list(raw, "test_groups", path)
    if test_groups is None:
        test_groups = []
    else:
        for i, v in enumerate(test_groups):
            if not isinstance(v, str):
                raise ConfigError(f"{path}.test_groups[{i}]: 문자열이어야 합니다")

    seed = _opt_int(raw, "seed", path)
    if seed is None:
        seed = 0

    return SplitConfig(
        test_fraction=test_fraction,
        test_groups=test_groups,
        seed=seed,
    )


def _parse_regional(raw: dict, path: str = "analysis.regional") -> RegionalConfig:
    """지역 설정의 unknown/null/type/range를 검사한다. 경로는 load_config에서 푼다."""
    defaults = RegionalConfig()
    _check_unknown(raw, set(defaults.__dataclass_fields__), path)
    values = {}
    for key in ("enabled", "include_area_normalized"):
        value = _opt_bool(raw, key, path)
        values[key] = getattr(defaults, key) if value is None else value
    for key in ("correspondence_file", "source_manifest"):
        value = _opt_str(raw, key, path, allow_none=True)
        if value is not None and not value.strip():
            raise ConfigError(f"{path}.{key}: 비어 있지 않은 경로가 필요합니다")
        values[key] = value
    value = _opt_int(raw, "connectivity", path)
    values["connectivity"] = defaults.connectivity if value is None else value
    if values["connectivity"] not in (4, 8):
        raise ConfigError(f"{path}.connectivity: 4 또는 8이어야 합니다")
    for key in ("merge_gap_um", "display_context_margin_um", "conservation_rtol",
                "conservation_atol_um2", "coordinate_atol_um", "delta_atol_um2"):
        value = _opt_float(raw, key, path)
        if value is not None and value < 0:
            raise ConfigError(f"{path}.{key}: 0 이상이어야 합니다")
        values[key] = getattr(defaults, key) if value is None else value
    return RegionalConfig(**values)


def _parse_analysis(raw: dict, path: str = "analysis") -> AnalysisConfig:
    """analysis 절을 읽는다."""
    _check_unknown(raw, {
        "k_neighbors", "n_clusters", "layer_policy",
        "other_products_only", "filters", "size_tolerance", "loo_top_k",
        "neighborhood_space", "pair_near_margin", "regional",
    }, path)

    k_neighbors = _opt_int(raw, "k_neighbors", path)
    if k_neighbors is None:
        k_neighbors = 5
    elif k_neighbors <= 0:
        raise ConfigError(f"{path}.k_neighbors: 양수여야 합니다")

    n_clusters = _opt_int(raw, "n_clusters", path)
    if n_clusters is None:
        n_clusters = 4
    elif n_clusters <= 0:
        raise ConfigError(f"{path}.n_clusters: 양수여야 합니다")

    layer_policy = raw.get("layer_policy", "exact")
    if layer_policy not in LAYER_POLICY_VALUES:
        raise ConfigError(f"{path}.layer_policy: {LAYER_POLICY_VALUES} 중 하나여야 합니다")

    other_products_only = _opt_bool(raw, "other_products_only", path)
    if other_products_only is None:
        other_products_only = True

    filters_raw = _opt_dict(raw, "filters", path)
    filters: dict[str, list] = {}
    if filters_raw is not None:
        for k, v in filters_raw.items():
            if not isinstance(k, str):
                raise ConfigError(f"{path}.filters: 키는 문자열이어야 합니다")
            if not isinstance(v, list):
                raise ConfigError(f"{path}.filters[{k!r}]: 값은 리스트여야 합니다")
            filters[k] = v

    size_tolerance = _opt_float(raw, "size_tolerance", path, allow_none=True)
    if size_tolerance is not None and size_tolerance < 0:
        raise ConfigError(f"{path}.size_tolerance: 0 이상이어야 합니다")

    loo_top_k = _opt_int(raw, "loo_top_k", path)
    if loo_top_k is None:
        loo_top_k = 5
    elif loo_top_k <= 0:
        raise ConfigError(f"{path}.loo_top_k: 양수여야 합니다")

    neighborhood_space = raw.get("neighborhood_space", "with_risk_ae")
    if neighborhood_space not in NEIGHBORHOOD_SPACE_VALUES:
        raise ConfigError(f"{path}.neighborhood_space: {NEIGHBORHOOD_SPACE_VALUES} 중 하나여야 합니다")

    pair_near_margin = _opt_float(raw, "pair_near_margin", path)
    if pair_near_margin is None:
        pair_near_margin = 1.0
    elif pair_near_margin < 0:
        raise ConfigError(f"{path}.pair_near_margin: 0 이상이어야 합니다")

    return AnalysisConfig(
        k_neighbors=k_neighbors,
        n_clusters=n_clusters,
        layer_policy=layer_policy,
        other_products_only=other_products_only,
        filters=filters,
        size_tolerance=size_tolerance,
        loo_top_k=loo_top_k,
        neighborhood_space=neighborhood_space,
        pair_near_margin=pair_near_margin,
        regional=_parse_regional(_opt_dict(raw, "regional", path) or {}),
    )


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------

def config_from_dict(raw: dict) -> AppConfig:
    """dict를 검증해 AppConfig로 만든다. 절 사이 규칙(이미지 크기와 합성곱 층 수, real 모드 CAA 규칙)도 검사한다."""
    if not isinstance(raw, dict):
        raise ConfigError("설정 최상위는 매핑이어야 합니다")
    _check_unknown(raw, {
        "mode", "output_dir", "data", "caa", "model",
        "train", "split", "analysis",
    }, "")

    mode = _require_str(raw, "mode", "")
    if mode not in MODE_VALUES:
        raise ConfigError(f"mode: {MODE_VALUES} 중 하나여야 합니다")

    output_dir = _require_str(raw, "output_dir", "")

    if "data" not in raw:
        raise ConfigError("필수 설정 키 누락 'data'")
    data = _parse_data(raw["data"])

    caa_raw = raw.get("caa")
    if caa_raw is None:
        caa = CAAConfig()
    else:
        if not isinstance(caa_raw, dict):
            raise ConfigError("caa: 매핑이어야 합니다")
        caa = _parse_caa(caa_raw)

    model_raw = raw.get("model")
    if model_raw is None:
        model = ModelSection()
    else:
        if not isinstance(model_raw, dict):
            raise ConfigError("model: 매핑이어야 합니다")
        model = _parse_model(model_raw)

    train_raw = raw.get("train")
    if train_raw is None:
        train = TrainConfig()
    else:
        if not isinstance(train_raw, dict):
            raise ConfigError("train: 매핑이어야 합니다")
        train = _parse_train(train_raw)

    split_raw = raw.get("split")
    if split_raw is None:
        split = SplitConfig()
    else:
        if not isinstance(split_raw, dict):
            raise ConfigError("split: 매핑이어야 합니다")
        split = _parse_split(split_raw)

    analysis_raw = raw.get("analysis")
    if analysis_raw is None:
        analysis = AnalysisConfig()
    else:
        if not isinstance(analysis_raw, dict):
            raise ConfigError("analysis: 매핑이어야 합니다")
        analysis = _parse_analysis(analysis_raw)

    # 절 사이 규칙
    factor = 2 ** len(model.conv_channels)
    if any(v % factor for v in data.image_size):
        raise ConfigError(
            f"data.image_size {data.image_size}는 2**len(model.conv_channels) = {factor}로 나누어떨어져야 합니다"
        )
    if mode == "real":
        if caa.rule is not None:
            if caa.rule.startswith("demo_"):
                raise ConfigError(
                    "mode 'real'에서는 'demo_'로 시작하는 caa.rule을 사용할 수 없습니다"
                )
            if not caa.confirmed:
                raise ConfigError(
                    "mode 'real'에서 caa.rule을 쓰려면 caa.confirmed: true가 필요합니다"
                )

    return AppConfig(
        mode=mode,
        output_dir=output_dir,
        data=data,
        caa=caa,
        model=model,
        train=train,
        split=split,
        analysis=analysis,
    )


def load_config(path: str | Path) -> AppConfig:
    """YAML 파일을 읽어 AppConfig를 만든다. 상대경로 data_dir·output_dir은 YAML 파일 위치 기준으로 바꾼다."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {p}")
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("설정 최상위는 매핑이어야 합니다")
    cfg = config_from_dict(raw)

    # 상대경로 data_dir / output_dir은 YAML 파일이 있는 디렉터리 기준으로 바꾼다.
    parent = p.resolve().parent
    if cfg.data.data_dir and not Path(cfg.data.data_dir).is_absolute():
        cfg.data.data_dir = str((parent / cfg.data.data_dir).resolve())
    if cfg.output_dir and not Path(cfg.output_dir).is_absolute():
        cfg.output_dir = str((parent / cfg.output_dir).resolve())
    for key in ("correspondence_file", "source_manifest"):
        value = getattr(cfg.analysis.regional, key)
        if value is not None:
            resolved = (parent / value).resolve()
            if not resolved.is_file():
                raise ConfigError(f"analysis.regional.{key}: 파일을 찾을 수 없습니다: {resolved}")
            setattr(cfg.analysis.regional, key, str(resolved))
    return cfg


def config_to_dict(cfg: AppConfig) -> dict:
    """실행 기록용으로 AppConfig를 JSON에 쓸 수 있는 dict로 바꾼다."""
    return asdict(cfg)
