"""다중 입력 오토인코더 학습, 체크포인트 저장·불러오기, 특징 벡터 추출.

학습에는 학습 행만 사용하고, 제품별 표본 수 편중을 줄이기 위해 제품 균형 샘플링을 쓴다.
seed와 설정은 체크포인트에 함께 저장한다.
"""
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
    """cpu, auto(CUDA→MPS→CPU), cuda, mps 중 실행 장치를 고른다. 요청한 가속기가 없으면 오류."""
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
            raise RuntimeError("device 'cuda'를 요청했지만 CUDA를 사용할 수 없습니다")
        return torch.device("cuda")
    if name == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("device 'mps'를 요청했지만 MPS를 사용할 수 없습니다")
        return torch.device("mps")
    raise ValueError(f"알 수 없는 device {name!r}")


def set_seed(seed: int) -> None:
    """random, NumPy, PyTorch의 난수 seed를 고정한다."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def model_config_from_arrays(
    arrays: dict, *, enabled, conv_channels, modality_dim: int, latent_dim: int
) -> ModelConfig:
    """모델 입력 배열의 모양에서 이미지 크기·채널 수·수치 차원을 읽어 ModelConfig를 만든다."""
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
    """배열 dict에서 지정한 행을 골라 장치의 float32 텐서 dict로 만든다."""
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
    """학습 행만으로 오토인코더를 학습하고 에폭별 손실 기록을 반환한다.

    balance_products가 True이면 제품별 표본 수의 역수 가중으로 복원 추출한다.
    기록에는 에폭별 전체 손실과 입력별 손실이 따로 남는다.
    """
    set_seed(seed)
    train_pos = np.asarray(train_pos, dtype=int)
    if train_pos.size == 0:
        raise ValueError("학습 행이 없습니다")
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
    """학습된 모델로 전체 행의 결합 특징(z), 구조측 특징(z_struct), Fab측 특징(z_fab)을 추출한다."""
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
    """모델 가중치, 모델 설정, 정규화 통계, 추적 정보(extra)를 한 파일로 저장한다."""
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
    """weights_only 모드로 체크포인트를 읽어 평가 모드의 모델과 설정·정규화 통계·추적 정보를 반환한다."""
    dev = select_device(device)
    # 체크포인트는 CPU 텐서로 저장한다. 먼저 CPU에서 복원한 뒤 모델 전체를 요청 장치로
    # 옮겨야 evaluate처럼 곧바로 모델을 호출하는 경로에서도 입력과 가중치 장치가 일치한다.
    payload = torch.load(path, map_location="cpu", weights_only=True)
    model_cfg = ModelConfig.from_dict(payload["model_config"])
    model = MultiInputAE(model_cfg)
    model.load_state_dict(payload["state_dict"])
    model.to(dev).eval()
    normalizers = {k: Normalizer.from_dict(v) for k, v in payload["normalizers"].items()}
    return model, model_cfg, normalizers, payload["extra"]
