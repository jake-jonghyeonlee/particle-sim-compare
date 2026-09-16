"""다중 입력 마스크 복원 손실(torch).

입력별로 유효 영역·관측값만 사용해 샘플별 평균을 먼저 구하고, 그 입력이 없는 샘플은 제외한 뒤
샘플 평균을 낸다. 이렇게 하면 픽셀 수가 많은 이미지가 수치 입력을 압도하지 않는다.
마지막으로 입력별 손실을 가중 평균한다.
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
    """마스크 평균제곱오차 항.

    샘플 손실 = (원소 오차 × 가중치 합) / 유효 원소 수, 유효 원소가 없는 샘플은 제외하고
    포함된 샘플의 평균을 반환한다. 포함된 샘플이 없으면 None.
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
    """마스크 BCEWithLogits 항. 샘플별 축약 방식은 _masked_mse_term과 같다."""
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
    """out["recon"]에 있는 입력마다 마스크 복원 손실을 계산해 (전체 손실, 기록 dict)를 반환한다.

    - layout: 이진 이미지이므로 BCEWithLogits (유효 영역 × 전극 존재)
    - hotspot: 가공 강도이므로 sigmoid 출력과의 MSE (유효 영역 × 크기별 존재)
    - ca, geometry: 관측 마스크 MSE
    - fab: 분포 모양 MSE와 총량 MSE의 평균
    weights에 없는 입력의 가중치는 1이다. 계산된 입력이 하나도 없으면 0 손실을 반환한다.
    마스크 밖 목표값은 계산 전에 0으로 바꿔 NaN이 전파되지 않게 한다.
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
