# config.py contract

YAML (yaml.safe_load) -> nested dataclasses. `class ConfigError(DataContractError)`.
Unknown keys at ANY level raise ConfigError naming the dotted path (e.g. "model.foo").

```python
@dataclass
class DataConfig:
    data_dir: str                      # required
    ca_grid: list[float]               # required, strictly increasing, > 0
    hotspot_sizes: list[float]         # required, strictly increasing, > 0 (may be empty list)
    fab_grid: list[float]              # required, strictly increasing, > 0
    image_size: list[int] = [64, 64]   # [H, W], each positive and divisible by 8
    ca_alignment: str = "exact"        # "exact" | "loglinear_within_range"
    hotspot_legend: dict | None = None # passed to preprocess.legend_to_values

@dataclass
class CAAConfig:
    rule: str | None = None            # e.g. "demo_probability_per_bin"
    confirmed: bool = False
    reference_distribution_id: str | None = None
    require_full_coverage: bool = True
    process_layer_overrides: dict[str, str] = {}   # "<product_id>|<failure_type>" -> layer

@dataclass
class ModelSection:
    enabled_inputs: list[str] = ["layout","hotspot","ca","fab","geometry"]
    conv_channels: list[int] = [8, 16, 32]
    modality_dim: int = 16
    latent_dim: int = 32               # even, >= 2
    loss_weights: dict[str, float] = {}   # keys subset of enabled names, values >= 0

@dataclass
class TrainConfig:
    epochs: int = 30; batch_size: int = 16; lr: float = 1e-3
    seed: int = 0; device: str = "cpu"  # "cpu" | "auto" | "cuda" | "mps"
    balance_products: bool = True

@dataclass
class SplitConfig:
    test_fraction: float = 0.25        # 0 < x < 1
    test_groups: list[str] = []
    seed: int = 0

@dataclass
class AnalysisConfig:
    k_neighbors: int = 5; n_clusters: int = 4
    layer_policy: str = "exact"        # "exact" | "ignore"
    other_products_only: bool = True
    filters: dict[str, list] = {}
    size_tolerance: float | None = None
    loo_top_k: int = 5

@dataclass
class AppConfig:
    mode: str                          # required: "demo" | "real"
    output_dir: str                    # required
    data: DataConfig
    caa: CAAConfig
    model: ModelSection
    train: TrainConfig
    split: SplitConfig
    analysis: AnalysisConfig
```
(Use `field(default_factory=...)` for list/dict defaults.)

Functions:
- `config_from_dict(raw: dict) -> AppConfig`
- `load_config(path: str | Path) -> AppConfig` (relative data_dir/output_dir resolved
  against the YAML file's parent directory, stored as str)
- `config_to_dict(cfg: AppConfig) -> dict` (dataclasses.asdict; JSON-serialisable)

Validation (ConfigError): missing required keys; wrong types (bool is not int);
enum values; grid rules above; enabled_inputs non-empty and known; loss_weights keys
known; mode "real" rejects any caa.rule starting with "demo_"; mode "real" with a
caa.rule set requires caa.confirmed true.
