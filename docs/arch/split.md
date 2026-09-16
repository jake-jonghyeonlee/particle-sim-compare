# split.py contract

Imports: numpy, pandas. `class LeakageError(ValueError)`.
meta: DataFrame with at least sample_id, product_id, design_lineage_id, and (for
confounding) fab_id, product_family. Positions are 0..N-1 row positions.

- `group_ids(meta) -> np.ndarray` (dtype object): union-find over rows linking rows
  that share product_id OR design_lineage_id. Label of a component = smallest
  design_lineage_id string in it.
- `group_split(meta, *, test_fraction: float, seed: int, test_groups=())
  -> tuple[np.ndarray, np.ndarray]` sorted int arrays (train_pos, test_pos).
  If test_groups: every row whose product_id or design_lineage_id is listed goes to
  test together with its whole component; unknown names -> ValueError; all rows in
  test -> ValueError. Else: fewer than 2 components -> ValueError("cannot split:
  fewer than 2 independent groups"); shuffle component labels (sorted first) with
  np.random.default_rng(seed); add components to test until test rows >=
  ceil(test_fraction*N); always keep at least one component in train.
- `assert_no_leakage(meta, train_pos, test_pos) -> None`: LeakageError if positions
  overlap, or any product_id or design_lineage_id occurs in both.
- `leave_one_group_out(meta, column: str) -> list[dict]`: for each sorted unique
  value v of column: test = all rows of the components that contain a row with
  column == v; train = remaining rows. Item: {"held_out": v, "train_pos": arr,
  "test_pos": arr, "status": "ok" | "no_train_rows"}.
- `confounding_report(meta, a="fab_id", b="product_family") -> dict`:
  {"a": a, "b": b, "n_a": int, "n_b": int, "status": str, "detail": str,
  "crosstab": {a_value: {b_value: count}}} with status "insufficient_groups" if
  n_a < 2 or n_b < 2; "fully_confounded" if the a<->b mapping is one-to-one;
  "partially_confounded" if each a maps to one b or each b maps to one a;
  otherwise "ok". detail explains that effects cannot be separated when confounded.
- `product_balance_weights(meta, train_pos) -> np.ndarray`: for each train position
  weight 1 / (number of train rows with the same product_id); normalised to sum 1.
