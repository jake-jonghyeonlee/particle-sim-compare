# caa.py contract (Fab interpretation + CAA; separate from models)

Imports: numpy, schema (CACurve, FabDistribution, AnalysisBundle, DataContractError,
validate_fab). `class CAAError(DataContractError)`.

```python
DEMO_RULE = "demo_probability_per_bin"
DEMO_LABEL = "DEMO CAA: 예시용 가중치 계산이며 실제 불량률·수율이 아님"

@dataclass
class FabWeights:
    sizes: np.ndarray      # bin midpoints
    weights: np.ndarray    # NaN where the bin is unobserved
    size_unit: str
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
```

- `bin_midpoints(dist) -> np.ndarray`: (lower + upper) / 2 as float64.
- `fab_shape_and_total(dist) -> tuple[np.ndarray, float, str]`: validate_fab first.
  probability / rate_per_area: mass = raw value; count and average_count_per_glass
  are divided by inspected area; density: mass = value *
  (upper - lower) and unit f"{value_unit}*{size_unit}"; otherwise unit = value_unit.
  total = sum of observed mass; shape = mass / total (NaN where unobserved).
  total == 0 -> shape all NaN, total 0.0. No observed bin -> CAAError.
  Shape (distribution form) and total (amount) are returned separately on purpose.
- `interpret_fab(dist, rule: str | None, *, mode: str, confirmed: bool = False) -> FabWeights`:
  validate_fab first. rule None -> CAAError("CAA rule not configured").
  DEMO_RULE: mode must be "demo" else CAAError; value_kind must be "probability"
  else CAAError mentioning that the conversion for that kind is not confirmed;
  weights = raw_values copy, sizes = bin_midpoints, weight_unit "probability/bin",
  is_demo True. Any other rule -> CAAError(f"unknown or unconfirmed CAA rule {rule}")
  (real formulas are intentionally not implemented; `confirmed` is accepted for the
  future hook and must be True for any non-demo rule).
- `select_distribution(bundle, distributions: dict[str, FabDistribution],
  overrides: dict[str, str] | None = None) -> FabDistribution`:
  layer = overrides[f"{product_id}|{failure_type}"] if present else
  bundle.assigned_process_layer. For vertical_short the adapter resolves this from
  the upper layer in layer_stack.csv. Reference distributions (is_reference) are
  never selected here. If bundle.fab_distribution_id: must exist (else CAAError) and
  its fab_id and process_layer must equal the bundle's fab_id and layer (else CAAError).
  Otherwise candidates = non-reference distributions with same fab_id and
  process_layer and (product_family == bundle.product_family or None); prefer exact
  family match; zero or more than one final candidate -> CAAError.
- `compute_caa(ca: CACurve, weights: FabWeights, *, require_full_coverage=True) -> CAAResult`:
  consider weight sizes whose weight is finite (none -> CAAError). For each, find a
  CA size with np.isclose(rtol=1e-6, atol=0) that is observed; NO interpolation,
  NO conversion between different bins. Missing sizes are listed. If any missing and
  require_full_coverage: value None, reason "missing CA at sizes [...]". Else value =
  sum(weight * ca) over matched (reason notes partial coverage if missing).
  unit = "relative_index". Particle diameter units are explicitly converted before
  exact-size matching. label = DEMO_LABEL if is_demo else
  "CAA (configured rule)".
