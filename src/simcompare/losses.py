"""Masked multi-input reconstruction loss (torch).

Contract: docs/arch/losses.md
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

LOSS_KINDS: dict[str, str] = {
    "layout": "bce_binary",
    "hotspot": "mse_processed_image",
    "ca": "mse_masked",
    "fab": "mse_masked",
    "geometry": "mse_masked",
}


def _masked_mse_term(
    recon: torch.Tensor, target: torch.Tensor, weight: torch.Tensor
) -> torch.Tensor | None:
    """Per-term masked MSE.

    per-sample loss = sum(elem * weight) / count(weight > 0) over that sample;
    samples with count 0 are EXCLUDED; term loss = mean over included samples.
    Returns None if no sample is included.
    """
    target = torch.where(weight > 0, target, torch.zeros_like(target))
    elem = (recon - target) ** 2
    weighted = elem * weight
    count = (weight > 0).sum(dim=tuple(range(1, weight.dim())))
    included = count > 0
    if not included.any():
        return None
    per_sample = weighted.sum(dim=tuple(range(1, weight.dim()))) / count
    return per_sample[included].mean()


def _masked_bce_term(
    recon: torch.Tensor, target: torch.Tensor, weight: torch.Tensor
) -> torch.Tensor | None:
    """Per-term masked BCEWithLogits.

    Same per-sample reduction as _masked_mse_term.
    """
    target = torch.where(weight > 0, target, torch.zeros_like(target))
    elem = F.binary_cross_entropy_with_logits(recon, target, reduction="none")
    weighted = elem * weight
    count = (weight > 0).sum(dim=tuple(range(1, weight.dim())))
    included = count > 0
    if not included.any():
        return None
    per_sample = weighted.sum(dim=tuple(range(1, weight.dim()))) / count
    return per_sample[included].mean()


def masked_losses(
    batch: dict,
    out: dict,
    weights: dict[str, float] | None = None,
) -> tuple[torch.Tensor, dict[str, float]]:
    """Compute masked reconstruction losses for each modality in out["recon"].

    Args:
        batch: Batch dict with keys as in docs/arch/model.md.
        out: MultiInputAE.forward output dict with "recon" key.
        weights: Optional per-modality weights; missing -> 1.0.

    Returns:
        (total_loss, log_dict) where log_dict maps modality -> float loss
        plus "total" -> float(total).
    """
    if weights is None:
        weights = {}

    recon = out["recon"]
    log: dict[str, float] = {}
    included_losses: list[torch.Tensor] = []
    included_weights: list[float] = []

    for modality in ("layout", "hotspot", "ca", "fab", "geometry"):
        if modality not in recon:
            continue
        w = weights.get(modality, 1.0)
        if w <= 0:
            continue

        if modality == "layout":
            weight = batch["layout_valid"] * batch["layout_present"][:, :, None, None]
            loss = _masked_bce_term(recon["layout"], batch["layout"], weight)
        elif modality == "hotspot":
            weight = batch["hotspot_valid"] * batch["hotspot_present"][:, :, None, None]
            loss = _masked_mse_term(
                torch.sigmoid(recon["hotspot"]), batch["hotspot"], weight
            )
        elif modality == "ca":
            loss = _masked_mse_term(recon["ca"], batch["ca"], batch["ca_mask"])
        elif modality == "geometry":
            loss = _masked_mse_term(recon["geometry"], batch["geom"], batch["geom_mask"])
        elif modality == "fab":
            loss_shape = _masked_mse_term(
                recon["fab"], batch["fab"], batch["fab_mask"]
            )
            loss_total = _masked_mse_term(
                recon["fab_total"], batch["fab_total"], batch["fab_total_mask"]
            )
            terms = [t for t in (loss_shape, loss_total) if t is not None]
            if not terms:
                continue
            loss = torch.stack(terms).mean()
        else:
            continue

        if loss is None:
            continue

        included_losses.append(loss)
        included_weights.append(w)
        log[modality] = float(loss.detach())

    if not included_losses:
        total = torch.zeros((), requires_grad=True)
    else:
        total = sum(
            w * l for w, l in zip(included_weights, included_losses)
        ) / sum(included_weights)

    log["total"] = float(total.detach())
    return total, log
