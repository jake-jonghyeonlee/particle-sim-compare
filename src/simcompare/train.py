"""Autoencoder training, checkpointing and embedding extraction."""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import torch

from simcompare.losses import masked_losses
from simcompare.model import ModelConfig, MultiInputAE
from simcompare.preprocess import Normalizer
from simcompare.split import product_balance_weights


def select_device(name: str) -> torch.device:
    if name == "cpu":
        return torch.device("cpu")
    if name == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if name == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("device 'cuda' requested but CUDA is not available")
        return torch.device("cuda")
    if name == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("device 'mps' requested but MPS is not available")
        return torch.device("mps")
    raise ValueError(f"unknown device {name!r}")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def model_config_from_arrays(
    arrays: dict, *, enabled, conv_channels, modality_dim: int, latent_dim: int
) -> ModelConfig:
    _, layout_channels, h, w = arrays["layout"].shape
    return ModelConfig(
        image_hw=(int(h), int(w)),
        layout_channels=int(layout_channels),
        hotspot_channels=int(arrays["hotspot"].shape[1]),
        ca_dim=int(arrays["ca"].shape[1]),
        fab_dim=int(arrays["fab"].shape[1]),
        geom_dim=int(arrays["geom"].shape[1]),
        enabled=tuple(enabled),
        conv_channels=tuple(int(c) for c in conv_channels),
        modality_dim=int(modality_dim),
        latent_dim=int(latent_dim),
    )


def to_tensors(arrays: dict, pos, device) -> dict[str, torch.Tensor]:
    idx = np.asarray(pos, dtype=int)
    return {
        key: torch.as_tensor(np.ascontiguousarray(value[idx]), dtype=torch.float32, device=device)
        for key, value in arrays.items()
    }


def train_model(
    arrays: dict,
    meta,
    train_pos,
    model_cfg: ModelConfig,
    *,
    epochs: int,
    batch_size: int,
    lr: float,
    seed: int,
    device: str = "cpu",
    balance_products: bool = True,
    loss_weights: dict | None = None,
) -> tuple[MultiInputAE, list[dict]]:
    set_seed(seed)
    train_pos = np.asarray(train_pos, dtype=int)
    if train_pos.size == 0:
        raise ValueError("no training rows")
    dev = select_device(device)
    model = MultiInputAE(model_cfg).to(dev)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    generator = torch.Generator().manual_seed(seed)
    weights = torch.as_tensor(product_balance_weights(meta, train_pos), dtype=torch.float64)
    history: list[dict] = []
    for epoch in range(1, epochs + 1):
        model.train()
        if balance_products:
            order = torch.multinomial(weights, len(train_pos), replacement=True, generator=generator)
        else:
            order = torch.randperm(len(train_pos), generator=generator)
        drawn = train_pos[order.numpy()]
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for start in range(0, len(drawn), batch_size):
            batch = to_tensors(arrays, drawn[start:start + batch_size], dev)
            out = model(batch)
            total, logs = masked_losses(batch, out, loss_weights)
            optimizer.zero_grad()
            total.backward()
            optimizer.step()
            for key, value in logs.items():
                sums[key] = sums.get(key, 0.0) + float(value)
                counts[key] = counts.get(key, 0) + 1
        row = {"epoch": epoch}
        row.update({key: sums[key] / counts[key] for key in sorted(sums)})
        history.append(row)
    model.eval()
    return model, history


@torch.no_grad()
def embed(model: MultiInputAE, arrays: dict, device: str = "cpu", batch_size: int = 64) -> dict:
    dev = select_device(device)
    model = model.to(dev).eval()
    n = len(next(iter(arrays.values())))
    parts: dict[str, list[np.ndarray]] = {"z": [], "z_struct": [], "z_fab": []}
    for start in range(0, n, batch_size):
        out = model(to_tensors(arrays, np.arange(start, min(n, start + batch_size)), dev))
        for key in parts:
            if out.get(key) is not None:
                parts[key].append(out[key].detach().cpu().numpy())
    return {key: (np.concatenate(value) if value else None) for key, value in parts.items()}


def save_checkpoint(path, model: MultiInputAE, model_cfg: ModelConfig, normalizers: dict, extra: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
            "model_config": model_cfg.to_dict(),
            "normalizers": {k: n.to_dict() for k, n in normalizers.items()},
            "extra": extra,
        },
        path,
    )


def load_checkpoint(path, device: str = "cpu"):
    payload = torch.load(path, map_location=select_device(device), weights_only=True)
    model_cfg = ModelConfig.from_dict(payload["model_config"])
    model = MultiInputAE(model_cfg)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    normalizers = {k: Normalizer.from_dict(v) for k, v in payload["normalizers"].items()}
    return model, model_cfg, normalizers, payload["extra"]
