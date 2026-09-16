# SPEC: simcompare (worker summary)

Purpose: compare and interpret ALREADY COMPUTED display particle-defect simulation
results across products. It is NOT a CA/hotspot predictor, not a layout fixer, not an
electrical model, and it never claims causality.

Inputs per analysis bundle (one product x design version x electrode-role pair x
failure type): layout images per electrode, processed hotspot images per particle
size, CA curve (critical area with contact probability) per particle size, Fab raw
particle distributions, optional thickness/shape metrics. CAA is a DERIVED value
(CA x Fab weights), never an independent input or ground truth.

Failure types: lateral_open (one electrode; electrode b = schema.NOT_APPLICABLE),
lateral_short and vertical_short (electrode pair). Three-electrode contact: out of scope.

Hard rules for all code:
- Python 3.11, numpy, pandas, scikit-learn, torch, Pillow, PyYAML only. CPU must work.
- Never replace missing values with 0. Keep NaN/None plus boolean masks.
- Never extrapolate outside observed particle-size ranges.
- Keep image aspect ratio; padding is marked by a valid-area mask.
- Normalization, PCA, autoencoder and clustering are fit on TRAIN rows only.
- Train/test split by product AND design lineage groups (variants never cross).
- Lengths are converted to the configured canonical unit (`um`). CA is a dimensionless
  probability-based aggregate in [0, 1], and CA/area is a separate risk index in 1/um^2.
- Uncertain physical meaning (bins, CAA formula, cross-product layer equivalence) ->
  raise an explicit error in mode "real"; mode "demo" may run labelled demo rules.
- Color->probability conversion only with an explicit legend.
- In real mode, layout and hotspot arrays must have identical shape, pixel size and origin.
- Vertical-short Fab layer is the upper layer resolved from product/version layer_stack.csv.
- Latent/attention values are never turned into importance or causal scores.
- Clusters ids have no physical meaning.
- User-facing text (errors, warnings, notes, disclaimers, CLI help) is Korean. Quoted English
  message texts in docs/arch describe the meaning; the implemented wording is Korean.
  JSON keys, CSV columns, enum values and identifiers stay English.
- Errors: raise schema.DataContractError (or a documented subclass) with a clear message.
- Tests use pytest, small synthetic arrays, run in seconds on CPU, no network.

Package layout: src/simcompare/<module>.py, tests/test_<module>.py.
Import style: `from simcompare.schema import ...`.
Each module contract lives in docs/arch/<module>.md; follow names/signatures exactly.
