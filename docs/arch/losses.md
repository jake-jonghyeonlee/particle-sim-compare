# losses.py contract (masked multi-input reconstruction loss, torch)

`masked_losses(batch: dict, out: dict, weights: dict[str, float] | None = None)
-> tuple[torch.Tensor, dict[str, float]]`
(batch keys as in docs/arch/model.md; out = MultiInputAE.forward output; missing
weight -> 1.0)

For each modality present in out["recon"]:
- "layout": elementwise BCEWithLogits(recon, target) (reduction="none"); element
  weight = layout_valid * layout_present[:, :, None, None].
- "hotspot": elementwise (sigmoid(recon) - hotspot)**2; weight = hotspot_valid *
  hotspot_present[:, :, None, None]. (Processed hotspot intensity is continuous.)
- "ca": (recon - ca)**2 with weight ca_mask. "geometry": same with geom/geom_mask.
- "fab": two terms, shape (recon["fab"] vs fab, fab_mask) and total
  (recon["fab_total"] vs fab_total, fab_total_mask); modality loss = mean of the
  per-term losses that have at least one included sample.

Per-term reduction (prevents large images from dominating numeric inputs):
per-sample loss = sum(elem * weight) / count(weight > 0) over that sample;
samples with count 0 are EXCLUDED; term loss = mean over included samples;
no included sample -> the term/modality is skipped (not logged, no contribution).
Target values where weight == 0 may be NaN: replace targets by 0 with
torch.where(weight > 0, target, 0) before computing, so NaN never propagates.

total = sum(w_m * L_m) / sum(w_m) over included modalities with w_m > 0.
If none included: total = torch.zeros((), requires_grad=True).
Log dict: {modality: float(L_m)} for included modalities plus "total": float(total).

Also `LOSS_KINDS = {"layout": "bce_binary", "hotspot": "mse_processed_image",
"ca": "mse_masked", "fab": "mse_masked", "geometry": "mse_masked"}` for run records.
