"""YAML -> nested dataclass configuration with strict validation.

Unknown keys at any level raise :class:`ConfigError` naming the dotted path.
Missing values are never silently filled; required fields must be present.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

from simcompare.schema import DataContractError


class ConfigError(DataContractError):
    """Raised when a configuration file violates the config contract."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DataConfig:
    data_dir: str
    ca_grid: list[float]
    hotspot_sizes: list[float]
    fab_grid: list[float]
    image_size: list[int] = field(default_factory=lambda: [64, 64])
    ca_alignment: str = "exact"
    hotspot_legend: dict | None = None


@dataclass
class CAAConfig:
    rule: str | None = None
    confirmed: bool = False
    reference_distribution_id: str | None = None
    require_full_coverage: bool = True
    process_layer_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class ModelSection:
    enabled_inputs: list[str] = field(
        default_factory=lambda: ["layout", "hotspot", "ca", "fab", "geometry"]
    )
    conv_channels: list[int] = field(default_factory=lambda: [8, 16, 32])
    modality_dim: int = 16
    latent_dim: int = 32
    loss_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class TrainConfig:
    epochs: int = 30
    batch_size: int = 16
    lr: float = 1e-3
    seed: int = 0
    device: str = "cpu"
    balance_products: bool = True


@dataclass
class SplitConfig:
    test_fraction: float = 0.25
    test_groups: list[str] = field(default_factory=list)
    seed: int = 0


@dataclass
class AnalysisConfig:
    k_neighbors: int = 5
    n_clusters: int = 4
    layer_policy: str = "exact"
    other_products_only: bool = True
    filters: dict[str, list] = field(default_factory=dict)
    size_tolerance: float | None = None
    loo_top_k: int = 5


@dataclass
class AppConfig:
    mode: str
    output_dir: str
    data: DataConfig
    caa: CAAConfig
    model: ModelSection
    train: TrainConfig
    split: SplitConfig
    analysis: AnalysisConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KNOWN_INPUTS = ("layout", "hotspot", "ca", "fab", "geometry")
CA_ALIGNMENT_VALUES = ("exact", "loglinear_within_range")
MODE_VALUES = ("demo", "real")
DEVICE_VALUES = ("cpu", "auto", "cuda", "mps")
LAYER_POLICY_VALUES = ("exact", "ignore")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_float(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _number_error(value: Any) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        return f"유한한 숫자여야 합니다(현재 {value})"
    return f"숫자여야 합니다(현재 {type(value).__name__})"


def _check_unknown(raw: dict, allowed: set[str], path: str) -> None:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: 매핑이어야 합니다")
    for key in raw:
        if key not in allowed:
            dotted = f"{path}.{key}" if path else str(key)
            raise ConfigError(f"알 수 없는 설정 키 {dotted!r}")


def _require_str(raw: dict, key: str, path: str) -> str:
    if key not in raw:
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"필수 설정 키 누락 {dotted!r}")
    value = raw[key]
    if not isinstance(value, str):
        dotted = f"{path}.{key}" if path else key
        raise ConfigError(f"{dotted}: 문자열이어야 합니다(현재 {type(value).__name__})")
    return value


def _opt_str(raw: dict, key: str, path: str, *, allow_none: bool = False) -> str | None:
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
    """Legend = {legend_id: str, entries: [{rgb: [r, g, b], value: number}], tolerance: int}."""
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
# Section parsers
# ---------------------------------------------------------------------------

def _parse_data(raw: dict, path: str = "data") -> DataConfig:
    _check_unknown(raw, {
        "data_dir", "ca_grid", "hotspot_sizes", "fab_grid",
        "image_size", "ca_alignment", "hotspot_legend",
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
        image_size=image_size,
        ca_alignment=ca_alignment,
        hotspot_legend=hotspot_legend,
    )


def _parse_caa(raw: dict, path: str = "caa") -> CAAConfig:
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


def _parse_analysis(raw: dict, path: str = "analysis") -> AnalysisConfig:
    _check_unknown(raw, {
        "k_neighbors", "n_clusters", "layer_policy",
        "other_products_only", "filters", "size_tolerance", "loo_top_k",
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

    return AnalysisConfig(
        k_neighbors=k_neighbors,
        n_clusters=n_clusters,
        layer_policy=layer_policy,
        other_products_only=other_products_only,
        filters=filters,
        size_tolerance=size_tolerance,
        loo_top_k=loo_top_k,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def config_from_dict(raw: dict) -> AppConfig:
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

    # Cross-section rules
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

    # Resolve relative data_dir / output_dir against the YAML file's parent.
    parent = p.resolve().parent
    if cfg.data.data_dir and not Path(cfg.data.data_dir).is_absolute():
        cfg.data.data_dir = str((parent / cfg.data.data_dir).resolve())
    if cfg.output_dir and not Path(cfg.output_dir).is_absolute():
        cfg.output_dir = str((parent / cfg.output_dir).resolve())
    return cfg


def config_to_dict(cfg: AppConfig) -> dict:
    return asdict(cfg)
