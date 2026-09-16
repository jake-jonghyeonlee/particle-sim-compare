"""Small multi-input autoencoder for case feature vectors (torch).

Reconstruction is an internal training objective only; this module is not a
CA/hotspot predictor and does not claim causality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

MODALITIES = ("layout", "hotspot", "ca", "fab", "geometry")
STRUCT_MODALITIES = ("layout", "hotspot", "ca", "geometry")

_IMAGE_MODALITIES = ("layout", "hotspot")
_VECTOR_MODALITIES = ("ca", "fab", "geometry")


@dataclass
class ModelConfig:
    image_hw: tuple[int, int] = (64, 64)
    layout_channels: int = 2
    hotspot_channels: int = 3
    ca_dim: int = 5
    fab_dim: int = 5
    geom_dim: int = 4
    enabled: tuple[str, ...] = MODALITIES
    conv_channels: tuple[int, ...] = (8, 16, 32)
    modality_dim: int = 16
    latent_dim: int = 32

    def to_dict(self) -> dict:
        return {
            "image_hw": list(self.image_hw),
            "layout_channels": self.layout_channels,
            "hotspot_channels": self.hotspot_channels,
            "ca_dim": self.ca_dim,
            "fab_dim": self.fab_dim,
            "geom_dim": self.geom_dim,
            "enabled": list(self.enabled),
            "conv_channels": list(self.conv_channels),
            "modality_dim": self.modality_dim,
            "latent_dim": self.latent_dim,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        return cls(
            image_hw=tuple(d["image_hw"]),
            layout_channels=int(d["layout_channels"]),
            hotspot_channels=int(d["hotspot_channels"]),
            ca_dim=int(d["ca_dim"]),
            fab_dim=int(d["fab_dim"]),
            geom_dim=int(d["geom_dim"]),
            enabled=tuple(d["enabled"]),
            conv_channels=tuple(d["conv_channels"]),
            modality_dim=int(d["modality_dim"]),
            latent_dim=int(d["latent_dim"]),
        )


def _validate_config(cfg: ModelConfig) -> None:
    if not cfg.enabled:
        raise ValueError("enabled must be a non-empty subset of MODALITIES")
    unknown = [m for m in cfg.enabled if m not in MODALITIES]
    if unknown:
        raise ValueError(f"enabled contains unknown modalities: {unknown}")
    h, w = cfg.image_hw
    factor = 2 ** len(cfg.conv_channels)
    if h % factor != 0 or w % factor != 0:
        raise ValueError(
            f"image_hw {cfg.image_hw} must be divisible by {factor}"
        )
    if cfg.latent_dim % 2 != 0:
        raise ValueError("latent_dim must be even")
    dims = {
        "layout": cfg.layout_channels,
        "hotspot": cfg.hotspot_channels,
        "ca": cfg.ca_dim,
        "fab": cfg.fab_dim,
        "geometry": cfg.geom_dim,
    }
    for m in cfg.enabled:
        if dims[m] <= 0:
            raise ValueError(f"modality {m!r} cannot be enabled with dim 0")


class ImageEncoder(nn.Module):
    """Encode an image modality to a fixed-size vector.

    Input is ``cat([x * valid, valid], dim=1)`` (2 * in_ch channels).
    """

    def __init__(self, in_ch: int, conv_channels: tuple[int, ...], modality_dim: int):
        super().__init__()
        layers: list[nn.Module] = []
        prev = 2 * in_ch
        for c in conv_channels:
            layers.append(nn.Conv2d(prev, c, kernel_size=3, stride=2, padding=1))
            layers.append(nn.ReLU())
            prev = c
        self.net = nn.Sequential(*layers)
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(prev, modality_dim)

    def forward(self, x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        inp = torch.cat([x * valid, valid], dim=1)
        feat = self.pool(self.net(inp)).flatten(1)
        return self.fc(feat)


class ImageDecoder(nn.Module):
    """Decode a latent vector to image logits of shape (N, out_ch, H, W)."""

    def __init__(
        self,
        out_ch: int,
        conv_channels: tuple[int, ...],
        modality_dim: int,
        image_hw: tuple[int, int],
    ):
        super().__init__()
        h, w = image_hw
        levels = len(conv_channels)
        self.fc = nn.Linear(modality_dim, conv_channels[-1] * (h // 2**levels) * (w // 2**levels))
        self.out_hw = (h // 2**levels, w // 2**levels)
        self.channels = conv_channels[-1]
        targets = list(reversed(conv_channels))[1:] + [out_ch]
        blocks: list[nn.Module] = []
        prev = conv_channels[-1]
        for i, c in enumerate(targets):
            blocks.append(nn.ConvTranspose2d(prev, c, kernel_size=4, stride=2, padding=1))
            if i < len(targets) - 1:
                blocks.append(nn.ReLU())
            prev = c
        self.net = nn.Sequential(*blocks)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        n = z.shape[0]
        feat = F.relu(self.fc(z)).view(n, self.channels, *self.out_hw)
        return self.net(feat)


class VectorEncoder(nn.Module):
    """Encode a masked vector modality to a fixed-size vector."""

    def __init__(self, in_dim: int, modality_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(2 * in_dim, 32),
            nn.ReLU(),
            nn.Linear(32, modality_dim),
        )

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([x * mask, mask], dim=1))


class VectorDecoder(nn.Module):
    """Decode a latent vector to a vector of ``out_dim`` values."""

    def __init__(self, modality_dim_in: int, out_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(modality_dim_in, 32),
            nn.ReLU(),
            nn.Linear(32, out_dim),
        )

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        return self.net(z)


class MultiInputAE(nn.Module):
    """Small multi-input autoencoder producing case feature vectors."""

    def __init__(self, cfg: ModelConfig):
        super().__init__()
        _validate_config(cfg)
        self.cfg = cfg
        h, w = cfg.image_hw
        m = cfg.modality_dim

        struct_enabled = [s for s in STRUCT_MODALITIES if s in cfg.enabled]
        self._struct_enabled = struct_enabled
        self._fab_enabled = "fab" in cfg.enabled
        half = cfg.latent_dim // 2
        z_dim = half * (int(bool(struct_enabled)) + int(self._fab_enabled))
        if struct_enabled:
            self.struct_head = nn.Linear(len(struct_enabled) * m, half)
        if self._fab_enabled:
            self.fab_head = nn.Linear(m, half)

        if "layout" in cfg.enabled:
            self.layout_encoder = ImageEncoder(cfg.layout_channels, cfg.conv_channels, m)
            self.layout_decoder = ImageDecoder(cfg.layout_channels, cfg.conv_channels, z_dim, (h, w))
        if "hotspot" in cfg.enabled:
            self.hotspot_encoder = ImageEncoder(cfg.hotspot_channels, cfg.conv_channels, m)
            self.hotspot_decoder = ImageDecoder(cfg.hotspot_channels, cfg.conv_channels, z_dim, (h, w))
        if "ca" in cfg.enabled:
            self.ca_encoder = VectorEncoder(cfg.ca_dim, m)
            self.ca_decoder = VectorDecoder(z_dim, cfg.ca_dim)
        if "fab" in cfg.enabled:
            self.fab_encoder = VectorEncoder(cfg.fab_dim + 1, m)
            self.fab_decoder = VectorDecoder(z_dim, cfg.fab_dim + 1)
        if "geometry" in cfg.enabled:
            self.geometry_encoder = VectorEncoder(cfg.geom_dim, m)
            self.geometry_decoder = VectorDecoder(z_dim, cfg.geom_dim)

    def _presence(self, batch: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        present: dict[str, torch.Tensor] = {}
        cfg = self.cfg
        if "layout" in cfg.enabled:
            present["layout"] = batch["layout_present"].float().amax(dim=1)
        if "hotspot" in cfg.enabled:
            present["hotspot"] = batch["hotspot_present"].float().amax(dim=1)
        if "ca" in cfg.enabled:
            present["ca"] = batch["ca_mask"].float().amax(dim=1)
        if "fab" in cfg.enabled:
            fab_p = batch["fab_mask"].float().amax(dim=1)
            fab_total_p = batch["fab_total_mask"].float().amax(dim=1)
            present["fab"] = torch.max(fab_p, fab_total_p)
        if "geometry" in cfg.enabled:
            present["geometry"] = batch["geom_mask"].float().amax(dim=1)
        return present

    def _embeddings(self, batch: dict[str, torch.Tensor], present: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        cfg = self.cfg
        emb: dict[str, torch.Tensor] = {}
        if "layout" in cfg.enabled:
            emb["layout"] = self.layout_encoder(batch["layout"], batch["layout_valid"]) * present["layout"].unsqueeze(1)
        if "hotspot" in cfg.enabled:
            emb["hotspot"] = self.hotspot_encoder(batch["hotspot"], batch["hotspot_valid"]) * present["hotspot"].unsqueeze(1)
        if "ca" in cfg.enabled:
            emb["ca"] = self.ca_encoder(batch["ca"], batch["ca_mask"].float()) * present["ca"].unsqueeze(1)
        if "fab" in cfg.enabled:
            x = torch.cat([batch["fab"], batch["fab_total"]], dim=1)
            mask = torch.cat([batch["fab_mask"], batch["fab_total_mask"]], dim=1).float()
            emb["fab"] = self.fab_encoder(x, mask) * present["fab"].unsqueeze(1)
        if "geometry" in cfg.enabled:
            emb["geometry"] = self.geometry_encoder(batch["geom"], batch["geom_mask"].float()) * present["geometry"].unsqueeze(1)
        return emb

    def forward(self, batch: dict[str, torch.Tensor]) -> dict[str, Any]:
        present = self._presence(batch)
        emb = self._embeddings(batch, present)

        z_struct = None
        if self._struct_enabled:
            parts = [emb[s] for s in self._struct_enabled]
            z_struct = self.struct_head(torch.cat(parts, dim=1))

        z_fab = None
        if self._fab_enabled:
            z_fab = self.fab_head(emb["fab"])

        if z_struct is not None and z_fab is not None:
            z = torch.cat([z_struct, z_fab], dim=1)
        elif z_struct is not None:
            z = z_struct
        else:
            z = z_fab

        recon: dict[str, torch.Tensor] = {}
        cfg = self.cfg
        if "layout" in cfg.enabled:
            recon["layout"] = self.layout_decoder(z)
        if "hotspot" in cfg.enabled:
            recon["hotspot"] = self.hotspot_decoder(z)
        if "ca" in cfg.enabled:
            recon["ca"] = self.ca_decoder(z)
        if "fab" in cfg.enabled:
            fab_out = self.fab_decoder(z)
            recon["fab"] = fab_out[:, : cfg.fab_dim]
            recon["fab_total"] = fab_out[:, cfg.fab_dim:]
        if "geometry" in cfg.enabled:
            recon["geometry"] = self.geometry_decoder(z)

        return {
            "z": z,
            "z_struct": z_struct,
            "z_fab": z_fab,
            "recon": recon,
            "present": present,
        }

    def encode(self, batch: dict[str, torch.Tensor]) -> dict[str, Any]:
        out = self.forward(batch)
        out.pop("recon", None)
        return out
