"""사례 특징 벡터를 만드는 작은 다중 입력 오토인코더(torch).

layout·hotspot은 각각 CNN encoder, CA·Fab·형상 수치는 각각 MLP encoder로 압축한 뒤
구조·시뮬레이션 결과 측 특징(z_struct)과 Fab 측 특징(z_fab)을 따로 만들고 이어 붙여
결합 특징(z)을 만든다. 복원은 특징 학습을 위한 내부 목적일 뿐 CA·hotspot 예측기가 아니며,
z_struct와 z_fab의 분리가 인과적 분리를 뜻하지도 않는다.
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
    """모델 구조 설정. 활성 입력, 채널 수, 입력 차원, 특징 벡터 크기를 정한다."""
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
        """체크포인트 저장용 dict(튜플은 리스트로)로 변환한다."""
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
        """to_dict 결과에서 ModelConfig를 복원한다."""
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
    """활성 입력, 이미지 크기의 2^(합성곱 층 수) 배수 조건, 짝수 latent_dim, 0차원 입력 여부를 검사한다."""
    if not cfg.enabled:
        raise ValueError("enabled는 MODALITIES의 비어 있지 않은 부분집합이어야 합니다")
    unknown = [m for m in cfg.enabled if m not in MODALITIES]
    if unknown:
        raise ValueError(f"enabled에 알 수 없는 입력이 있습니다: {unknown}")
    h, w = cfg.image_hw
    factor = 2 ** len(cfg.conv_channels)
    if h % factor != 0 or w % factor != 0:
        raise ValueError(
            f"image_hw {cfg.image_hw}는 {factor}로 나누어떨어져야 합니다"
        )
    if cfg.latent_dim % 2 != 0:
        raise ValueError("latent_dim은 짝수여야 합니다")
    dims = {
        "layout": cfg.layout_channels,
        "hotspot": cfg.hotspot_channels,
        "ca": cfg.ca_dim,
        "fab": cfg.fab_dim,
        "geometry": cfg.geom_dim,
    }
    for m in cfg.enabled:
        if dims[m] <= 0:
            raise ValueError(f"차원이 0인 입력 {m!r}은(는) 활성화할 수 없습니다")


class ImageEncoder(nn.Module):
    """이미지 입력을 고정 길이 벡터로 압축하는 CNN encoder.

    입력은 [이미지 × 유효 영역, 유효 영역]을 채널로 이어 붙인 것(2 × in_ch 채널)이다.
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
        """유효 영역을 곱한 이미지와 유효 영역 채널을 함께 넣어 (N, modality_dim) 벡터를 만든다."""
        inp = torch.cat([x * valid, valid], dim=1)
        feat = self.pool(self.net(inp)).flatten(1)
        return self.fc(feat)


class ImageDecoder(nn.Module):
    """특징 벡터를 (N, out_ch, H, W) 이미지 logit으로 복원하는 전치 합성곱 decoder."""

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
        """Linear로 가장 작은 특징 맵을 만든 뒤 합성곱 층 수만큼 2배 업샘플링한다."""
        n = z.shape[0]
        feat = F.relu(self.fc(z)).view(n, self.channels, *self.out_hw)
        return self.net(feat)


class VectorEncoder(nn.Module):
    """[값 × 마스크, 마스크]를 입력으로 받아 수치 입력을 고정 길이 벡터로 압축하는 MLP."""

    def __init__(self, in_dim: int, modality_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(2 * in_dim, 32),
            nn.ReLU(),
            nn.Linear(32, modality_dim),
        )

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """[값 × 마스크, 마스크]를 이어 붙여 압축한다. 누락 위치는 마스크로 구분된다."""
        return self.net(torch.cat([x * mask, mask], dim=1))


class VectorDecoder(nn.Module):
    """특징 벡터를 out_dim 길이 수치로 복원하는 MLP."""

    def __init__(self, modality_dim_in: int, out_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(modality_dim_in, 32),
            nn.ReLU(),
            nn.Linear(32, out_dim),
        )

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        """특징 벡터에서 수치 입력을 복원한다."""
        return self.net(z)


class MultiInputAE(nn.Module):
    """다중 입력 오토인코더.

    입력이 없는 샘플은 해당 입력의 embedding에 존재 여부(0/1)를 곱해 영향을 끈다.
    decoder는 결합 특징 z에서 활성화된 모든 입력을 복원한다.
    """

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
        """샘플별로 각 입력이 하나라도 존재하는지(0/1)를 계산한다."""
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
        """활성 입력별 encoder 출력에 존재 여부를 곱한 embedding을 만든다."""
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
        """z, z_struct, z_fab, 입력별 복원값(recon), 입력별 존재 여부(present)를 반환한다."""
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
        """forward와 같지만 복원값을 뺀 특징만 반환한다."""
        out = self.forward(batch)
        out.pop("recon", None)
        return out
