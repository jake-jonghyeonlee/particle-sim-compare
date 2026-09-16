"""CLI 명령 구현. 모든 결과는 cfg.output_dir 아래에 저장한다.

- run_validate: 데이터 점검 보고서(validation.json)
- run_train: 그룹 분리, 학습 행 정규화, 오토인코더 학습, 체크포인트·기록 저장
- run_embed: 기준 PCA 공간과 오토인코더 특징 벡터 저장
- run_analyze: 위험 표·순위, 공간별 유사 사례, 군집, 제품 제외 안정성, 검토 카드
- run_evaluate: 평가 행 복원 손실, 입력 구성 비교, Fab·제품군 제외 검증
"""
from __future__ import annotations

import json
import math
import platform
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
import torch
from PIL import Image, ImageDraw

from simcompare import __version__
from simcompare.adapters.example import load_dataset
from simcompare.analysis import (
    CLUSTER_NOTE, cluster_embeddings, jaccard, leave_one_product_out, neighbor_sets,
    held_out_product_evidence, rank_agreement, ranking_comparison, risk_table,
)
from simcompare.baseline import BaselinePCA, explicit_features
from simcompare.cards import build_review_cards
from simcompare.checks import run_checks
from simcompare.config import config_to_dict
from simcompare.dataset import encode_bundles, fit_normalizers, to_model_arrays
from simcompare.losses import LOSS_KINDS, masked_losses
from simcompare.plots import write_analysis_figures
from simcompare.retrieval import search_all
from simcompare.schema import DataContractError
from simcompare.split import (
    assert_no_leakage, confounding_report, group_ids, group_split, leave_one_group_out,
)
from simcompare.train import (
    embed, load_checkpoint, model_config_from_arrays, save_checkpoint, select_device, to_tensors,
    train_model,
)

SYNTHETIC_NOTE = "합성/demo 결과는 코드 동작 확인용이며 연구 근거가 아닙니다."
RECON_NOTE = "복원 오차는 해석이나 검색 품질을 보장하지 않습니다."
RISK_SPACES = ("with_risk_baseline", "with_risk_ae")


def _json_safe(value):
    """NumPy 타입, NaN/inf를 JSON에 쓸 수 있는 값으로 재귀 변환한다."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    return value


def _write_json(path: Path, payload) -> Path:
    """한글을 유지한 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _out(cfg) -> Path:
    """출력 디렉터리를 만들고 경로를 반환한다."""
    path = Path(cfg.output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare(cfg):
    """설정의 데이터 경로를 예시 어댑터로 읽고 모델 입력 배열로 변환한다."""
    loaded = load_dataset(cfg.data.data_dir, mode=cfg.mode, hotspot_legend=cfg.data.hotspot_legend)
    return loaded, encode_bundles(loaded.bundles, loaded.distributions, cfg)


def _training_fingerprint(cfg) -> str:
    """입력 파일과 학습 의미를 바꾸는 설정의 SHA-256 fingerprint를 만든다."""
    digest = hashlib.sha256()
    root = Path(cfg.data.data_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"데이터 디렉터리가 없습니다: {root}")
    # 사람 검토 상태는 분석 카드에 실행 시점 그대로 연결되며 모델 입력이 아니다. 상태 갱신만으로
    # 재학습을 요구하지 않도록 제외하고, 나머지 입력(참조 이미지 포함)은 보수적으로 모두 해시한다.
    non_training_files = {"design_review_status.csv"}
    for path in sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).as_posix() not in non_training_files
    ):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    contract = {
        "data": config_to_dict(cfg)["data"],
        "caa": config_to_dict(cfg)["caa"],
        "model": config_to_dict(cfg)["model"],
        "train": config_to_dict(cfg)["train"],
        "split": config_to_dict(cfg)["split"],
    }
    digest.update(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def _assert_checkpoint_compatible(cfg, extra: dict) -> None:
    """현재 입력·학습 계약이 체크포인트 생성 시점과 같지 않으면 재학습을 요구한다."""
    expected = extra.get("training_fingerprint")
    current = _training_fingerprint(cfg)
    if expected is None:
        raise DataContractError("체크포인트에 데이터 fingerprint가 없습니다. 'simcompare train'을 다시 실행하세요")
    if expected != current:
        raise DataContractError(
            "학습 이후 데이터 또는 단위·격자·모델·분리 설정이 변경되었습니다. "
            "오래된 체크포인트를 사용하지 말고 'simcompare train'을 다시 실행하세요"
        )


def make_split(cfg, enc):
    """제품·설계 계열 그룹 단위로 학습/평가 행을 나누고 누수가 없는지 확인한다."""
    try:
        train_pos, test_pos = group_split(
            enc.meta, test_fraction=cfg.split.test_fraction, seed=cfg.split.seed,
            test_groups=tuple(cfg.split.test_groups),
        )
    except ValueError as exc:
        # 독립 그룹 부족이나 잘못된 test_groups는 데이터·설정 문제로 보고한다.
        raise DataContractError(f"학습/평가 분리 실패: {exc}") from None
    assert_no_leakage(enc.meta, train_pos, test_pos)
    return train_pos, test_pos


def _versions() -> dict:
    """재현성 기록용 라이브러리 버전 문자열."""
    return {
        "simcompare": __version__, "python": platform.python_version(), "numpy": str(np.__version__),
        "pandas": str(pd.__version__), "scikit-learn": str(sklearn.__version__), "torch": str(torch.__version__),
    }


def run_validate(cfg) -> dict:
    """데이터 점검을 실행해 validation.json을 저장한다."""
    loaded, enc = prepare(cfg)
    report = run_checks(loaded, enc, cfg)
    path = _write_json(_out(cfg) / "validation.json", report)
    return {"outputs": [str(path)], "n_bundles": len(enc), "n_warnings": len(report["warnings"]),
            "confounding": report["confounding"]["status"]}


def _train(cfg, enc, arrays, train_pos, enabled, seed=None):
    """설정의 모델·학습 옵션으로 지정한 입력 조합의 오토인코더를 학습한다."""
    model_cfg = model_config_from_arrays(
        arrays, enabled=enabled, conv_channels=cfg.model.conv_channels,
        modality_dim=cfg.model.modality_dim, latent_dim=cfg.model.latent_dim,
    )
    model, history = train_model(
        arrays, enc.meta, train_pos, model_cfg, epochs=cfg.train.epochs,
        batch_size=cfg.train.batch_size, lr=cfg.train.lr,
        seed=cfg.train.seed if seed is None else seed, device=cfg.train.device,
        balance_products=cfg.train.balance_products,
        loss_weights={k: v for k, v in cfg.model.loss_weights.items() if k in enabled} or None,
    )
    return model, model_cfg, history


def run_train(cfg) -> dict:
    """학습을 실행하고 model.pt, train_history.csv, split.json, run_record.json을 저장한다.

    정규화 통계와 모델은 학습 행만으로 적합한다. 체크포인트에는 seed, 전체 설정,
    학습/평가 sample_id, 손실 종류, 장치, 라이브러리 버전을 함께 기록한다.
    """
    out = _out(cfg)
    loaded, enc = prepare(cfg)
    train_pos, test_pos = make_split(cfg, enc)
    # 정규화 통계는 학습 행만으로 계산한다.
    norms = fit_normalizers(enc, train_pos)
    arrays = to_model_arrays(enc, norms)
    model, model_cfg, history = _train(cfg, enc, arrays, train_pos, cfg.model.enabled_inputs)
    ids = enc.meta["sample_id"]
    extra = {
        "seed": cfg.train.seed,
        "config": _json_safe(config_to_dict(cfg)),
        "train_sample_ids": list(ids.iloc[train_pos]),
        "test_sample_ids": list(ids.iloc[test_pos]),
        "loss_kinds": dict(LOSS_KINDS),
        "device": str(select_device(cfg.train.device)),
        "versions": _versions(),
        "is_synthetic": any(b.provenance.is_synthetic for b in loaded.bundles),
        "training_fingerprint": _training_fingerprint(cfg),
    }
    save_checkpoint(out / "model.pt", model, model_cfg, norms, extra)
    pd.DataFrame(history).to_csv(out / "train_history.csv", index=False)
    groups = group_ids(enc.meta)
    _write_json(out / "split.json", {
        "group_by": "product_id와 design_lineage_id로 연결된 그룹 단위 분리",
        "train": [{"sample_id": ids.iloc[i], "group": groups[i]} for i in train_pos],
        "test": [{"sample_id": ids.iloc[i], "group": groups[i]} for i in test_pos],
    })
    _write_json(out / "run_record.json", {
        "command": "train", "created_at": datetime.now(timezone.utc).isoformat(),
        **{k: v for k, v in extra.items() if k not in ("train_sample_ids", "test_sample_ids")},
        "n_train": len(train_pos), "n_test": len(test_pos), "final_epoch": history[-1] if history else None,
        "note": RECON_NOTE,
    })
    names = ["model.pt", "train_history.csv", "split.json", "run_record.json"]
    return {"outputs": [str(out / n) for n in names], "n_train": len(train_pos),
            "n_test": len(test_pos), "final_losses": history[-1] if history else None}


def _load_trained(cfg):
    """output_dir의 model.pt를 읽는다. 없으면 train을 먼저 실행하라는 오류를 낸다."""
    path = Path(cfg.output_dir) / "model.pt"
    if not path.exists():
        raise FileNotFoundError(f"{path}이(가) 없습니다. 먼저 'simcompare train'을 실행하세요")
    return load_checkpoint(path, cfg.train.device)


def _positions(enc, sample_ids) -> np.ndarray:
    """체크포인트에 기록된 sample_id를 현재 데이터의 행 위치로 바꾼다. 없는 ID가 있으면 재학습이 필요하다."""
    lookup = {sid: i for i, sid in enumerate(enc.meta["sample_id"])}
    missing = [s for s in sample_ids if s not in lookup]
    if missing:
        raise FileNotFoundError(f"체크포인트의 샘플이 현재 데이터에 없습니다(재학습 필요): {missing[:5]}")
    return np.array(sorted(lookup[s] for s in sample_ids), dtype=int)


def _baseline_space(enc, train_pos, include_risk: bool, n_components: int = 8):
    """학습 행으로 적합한 기준 PCA 좌표를 전체 행에 대해 계산한다."""
    x, mask, _ = explicit_features(enc, include_risk=include_risk)
    return BaselinePCA(n_components).fit(x, mask, train_pos).transform(x, mask)


def compute_spaces(cfg, enc, train_pos, checkpoint) -> dict[str, np.ndarray]:
    """비교할 특징 공간들을 계산한다.

    shape_only(형상 특징 PCA), with_risk_baseline(위험 수치 포함 PCA),
    with_risk_ae(오토인코더 결합 특징), ae_struct, ae_fab(분리 추출 특징).
    """
    model, _model_cfg, norms, _extra = checkpoint
    spaces = {
        "shape_only": _baseline_space(enc, train_pos, False),
        "with_risk_baseline": _baseline_space(enc, train_pos, True),
    }
    z = embed(model, to_model_arrays(enc, norms), cfg.train.device)
    spaces["with_risk_ae"] = z["z"]
    if z["z_struct"] is not None:
        spaces["ae_struct"] = z["z_struct"]
    if z["z_fab"] is not None:
        spaces["ae_fab"] = z["z_fab"]
    return spaces


def run_embed(cfg) -> dict:
    """특징 공간을 embeddings.npz로, 메타데이터와 학습/평가 구분을 embedding_meta.csv로 저장한다."""
    out = _out(cfg)
    checkpoint = _load_trained(cfg)
    _assert_checkpoint_compatible(cfg, checkpoint[3])
    _loaded, enc = prepare(cfg)
    train_pos = _positions(enc, checkpoint[3]["train_sample_ids"])
    spaces = compute_spaces(cfg, enc, train_pos, checkpoint)
    np.savez(out / "embeddings.npz", **spaces)
    meta = enc.meta.copy()
    meta["split"] = "test"
    meta.loc[train_pos, "split"] = "train"
    meta.to_csv(out / "embedding_meta.csv", index=False)
    return {"outputs": [str(out / "embeddings.npz"), str(out / "embedding_meta.csv")],
            "spaces": {k: list(v.shape) for k, v in spaces.items()}}


def _search_kwargs(cfg) -> dict:
    """설정의 검색 제약(layer 조건, 다른 제품만, 필터, 크기 허용 비율)을 모은다."""
    return {
        "layer_policy": cfg.analysis.layer_policy,
        "other_products_only": cfg.analysis.other_products_only,
        "filters": dict(cfg.analysis.filters) or None,
        "size_tolerance": cfg.analysis.size_tolerance,
    }


def _scatter_png(path: Path, emb: np.ndarray, labels: np.ndarray, train_mask: np.ndarray) -> None:
    """특징 공간의 처음 두 주성분 산점도를 PNG로 저장한다(학습=채움, 평가=테두리)."""
    size, pad = 480, 30
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    centered = emb - emb.mean(axis=0)
    if centered.shape[1] >= 2 and len(centered) >= 2:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        xy = centered @ vt[:2].T
    else:
        xy = np.column_stack([centered[:, 0], np.zeros(len(centered))])
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    pts = pad + (xy - lo) / span * (size - 2 * pad)
    palette = ["#2a6fdb", "#e0782f", "#2e9e5b", "#c23b5a", "#7a55c7", "#8a6d3b", "#1f9aa8", "#666666"]
    for (x, y), label, is_train in zip(pts, labels, train_mask):
        color = palette[int(label) % len(palette)]
        box = [x - 5, size - y - 5, x + 5, size - y + 5]
        draw.ellipse(box, fill=color if is_train else None, outline=color, width=2)
    draw.text((8, 8), "with_risk_ae PC1/PC2", fill="black")  # Pillow 기본 글꼴은 한글을 지원하지 않아 영문 축 이름만 표시한다
    img.save(path)


def run_analyze(cfg) -> dict:
    """분석을 실행하고 결과 파일을 저장한다.

    ranking.csv, risk_table.csv, rank_agreement.json, neighbors.csv, clusters.json,
    cluster_map.png, loo_stability.csv, shape_features.csv, review_cards.json과
    figures/ 아래 SVG 그림(rank_shift.svg, ca_curves.svg, loo_stability.svg)을 만든다.
    neighbors.csv의 reuses_risk_inputs가 True인 공간은 위험 입력을 재사용한 결과다.
    """
    out = _out(cfg)
    checkpoint = _load_trained(cfg)
    _assert_checkpoint_compatible(cfg, checkpoint[3])
    loaded, enc = prepare(cfg)
    train_pos = _positions(enc, checkpoint[3]["train_sample_ids"])
    spaces = compute_spaces(cfg, enc, train_pos, checkpoint)
    kwargs = _search_kwargs(cfg)
    ranked = ranking_comparison(risk_table(loaded.bundles, loaded.distributions, cfg))
    ranked.to_csv(out / "ranking.csv", index=False)
    ranked.drop(columns=[c for c in ranked.columns if c.startswith("rank")]).to_csv(
        out / "risk_table.csv", index=False)
    _write_json(out / "rank_agreement.json", rank_agreement(ranked))

    neighbors = pd.concat(
        [search_all(emb, enc.meta, k=cfg.analysis.k_neighbors, space=name, **kwargs)
         for name, emb in spaces.items()],
        ignore_index=True,
    )
    # 위험 수치(CA·hotspot)를 입력으로 쓴 공간은 독립적인 발견이 아님을 표시한다.
    neighbors["reuses_risk_inputs"] = neighbors["space"].isin(RISK_SPACES + ("ae_struct", "ae_fab"))
    neighbors.to_csv(out / "neighbors.csv", index=False)

    clusters = cluster_embeddings(spaces["with_risk_ae"], train_pos, cfg.analysis.n_clusters, cfg.train.seed)
    ids = enc.meta["sample_id"].tolist()
    cluster_json = {
        "space": "with_risk_ae", "note": CLUSTER_NOTE,
        "labels": {ids[i]: int(label) for i, label in enumerate(clusters["labels"])},
        "representatives": {int(c): ids[p] for c, p in clusters["representatives"].items()},
        "counter_examples": {int(c): (None if p is None else ids[p])
                             for c, p in clusters["counter_examples"].items()},
    }
    _write_json(out / "clusters.json", cluster_json)
    train_mask = np.zeros(len(ids), bool)
    train_mask[train_pos] = True
    _scatter_png(out / "cluster_map.png", spaces["with_risk_ae"], clusters["labels"], train_mask)

    x_shape, mask_shape, _ = explicit_features(enc, include_risk=False)

    def shape_embed(fit_pos):
        """제품 제외 안정성 비교용: fit_pos로 형상 PCA를 다시 적합해 전체 행을 변환한다."""
        return BaselinePCA(8).fit(x_shape, mask_shape, fit_pos).transform(x_shape, mask_shape)

    loo = leave_one_product_out(
        ranked, enc.meta, shape_embed, k=cfg.analysis.k_neighbors, top_k=cfg.analysis.loo_top_k,
        search_kwargs={**kwargs, "other_products_only": False}, train_pos=train_pos,
    )
    loo.to_csv(out / "loo_stability.csv", index=False)
    figures = write_analysis_figures(out, ranked, loo)

    x, mask, names = explicit_features(enc, include_risk=False)
    features = pd.DataFrame(np.where(mask, x, np.nan), columns=names)
    features.insert(0, "sample_id", ids)
    features.to_csv(out / "shape_features.csv", index=False)
    cards = build_review_cards(
        ranked, neighbors, cluster_json, features, design_reviews=loaded.design_reviews
    )
    _write_json(out / "review_cards.json", {
        "note": "전문가 검토용 근거 요약이며 중요 인자 자동 판정이 아닙니다.",
        "synthetic": any(b.provenance.is_synthetic for b in loaded.bundles),
        "cards": cards,
    })
    names_out = ["ranking.csv", "risk_table.csv", "rank_agreement.json", "neighbors.csv",
                 "clusters.json", "cluster_map.png", "loo_stability.csv", "shape_features.csv",
                 "review_cards.json"]
    return {"outputs": [str(out / n) for n in names_out] + [str(f) for f in figures], "n_cards": len(cards),
            "n_neighbor_rows": len(neighbors)}


def _test_losses(model, arrays, pos, device) -> dict:
    """지정한 행(평가 행)에 대한 입력별 마스크 복원 손실을 계산한다."""
    if len(pos) == 0:
        return {}
    model.eval()
    with torch.no_grad():
        batch = to_tensors(arrays, pos, select_device(device))
        _total, logs = masked_losses(batch, model(batch))
    return logs


def _mean_space_jaccard(a: pd.DataFrame, b: pd.DataFrame) -> float | None:
    """두 검색 결과의 질의별 이웃 집합 Jaccard 평균."""
    sa, sb = neighbor_sets(a), neighbor_sets(b)
    scores = [jaccard(sa.get(q, set()), sb.get(q, set())) for q in sorted(set(sa) | set(sb))]
    scores = [s for s in scores if s is not None]
    return float(np.mean(scores)) if scores else None


def _pca_heldout_error(enc, fit_pos, test_pos) -> float | None:
    """적합 행으로 만든 위험 포함 PCA가 제외 행을 얼마나 복원하는지(평균 제곱 오차) 계산한다."""
    x, mask, _ = explicit_features(enc, include_risk=True)
    try:
        pca = BaselinePCA(8).fit(x, mask, fit_pos)
    except ValueError:
        return None
    matrix = pca._matrix(x, mask)[test_pos]
    recon = pca.pca.inverse_transform(pca.pca.transform(matrix))
    return float(np.mean((matrix - recon) ** 2))


def run_evaluate(cfg) -> dict:
    """평가를 실행해 evaluation.json을 저장한다.

    - 학습 모델의 평가 행 입력별 복원 손실(해석 품질을 보장하지 않음)
    - 이미지 단독·수치 단독·전체 입력 구성을 같은 seed로 학습해 비교
    - Fab·제품군 그룹 제외 검증과 교락 판정(그룹 부족이면 평가 불가, 완전 교락이면 효과 분리 불가)
    """
    out = _out(cfg)
    model, _model_cfg, norms, extra = _load_trained(cfg)
    _assert_checkpoint_compatible(cfg, extra)
    loaded, enc = prepare(cfg)
    train_pos = _positions(enc, extra["train_sample_ids"])
    test_pos = _positions(enc, extra["test_sample_ids"])
    assert_no_leakage(enc.meta, train_pos, test_pos)
    arrays = to_model_arrays(enc, norms)
    kwargs = _search_kwargs(cfg)
    k = cfg.analysis.k_neighbors

    shape_space = _baseline_space(enc, train_pos, False)
    shape_nb = search_all(shape_space, enc.meta, k=k, space="shape_only", **kwargs)
    ranked = ranking_comparison(risk_table(loaded.bundles, loaded.distributions, cfg))
    held_out = held_out_product_evidence(
        shape_space, enc.meta, ranked, train_pos, test_pos, k=k, search_kwargs=kwargs
    )
    ablations = {}
    enabled = list(cfg.model.enabled_inputs)
    variants = {
        "image_only": [m for m in ("layout", "hotspot") if m in enabled],
        "numeric_only": [m for m in ("ca", "fab", "geometry") if m in enabled],
        "full": enabled,
    }
    for name, modalities in variants.items():
        if not modalities:
            ablations[name] = {"status": "건너뜀: 이 구성에 활성화된 입력이 없습니다"}
            continue
        variant_norms = fit_normalizers(enc, train_pos)
        variant_arrays = to_model_arrays(enc, variant_norms)
        variant, _, history = _train(cfg, enc, variant_arrays, train_pos, modalities)
        z = embed(variant, variant_arrays, cfg.train.device)["z"]
        nb = search_all(z, enc.meta, k=k, space=name, **kwargs)
        ablations[name] = {
            "modalities": modalities,
            "final_train_losses": history[-1] if history else None,
            "test_losses": _test_losses(variant, variant_arrays, test_pos, cfg.train.device),
            "mean_neighbor_jaccard_vs_shape_only_baseline": _mean_space_jaccard(nb, shape_nb),
        }

    logo = {}
    for column, other in (("fab_id", "product_family"), ("product_family", "fab_id")):
        report = confounding_report(enc.meta, column, other)
        if report["status"] == "insufficient_groups":
            verdict = "평가 불가: 그룹이 2개 미만입니다"
        elif report["status"] == "fully_confounded":
            verdict = f"효과 분리 불가: {column}와 {other}가 완전히 겹칩니다"
        else:
            verdict = f"주의해서 평가 가능 ({report['status']})"
        folds = []
        for fold in leave_one_group_out(enc.meta, column):
            if fold["status"] != "ok" or len(fold["train_pos"]) < 2:
                folds.append({"held_out": fold["held_out"], "status": "no_train_rows"})
                continue
            folds.append({
                "held_out": fold["held_out"], "status": "ok",
                "n_train": len(fold["train_pos"]), "n_test": len(fold["test_pos"]),
                "baseline_pca_heldout_mse": _pca_heldout_error(enc, fold["train_pos"], fold["test_pos"]),
            })
        logo[column] = {"confounding": report, "verdict": verdict, "folds": folds}

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "synthetic": any(b.provenance.is_synthetic for b in loaded.bundles),
        "notes": [SYNTHETIC_NOTE, RECON_NOTE,
                  "with_risk 공간은 CA·hotspot 입력을 재사용하므로 그 유사성은 독립적인 발견이 아닙니다"],
        "split": {"n_train": len(train_pos), "n_test": len(test_pos), "leakage_check": "passed",
                  "n_groups": int(len(set(group_ids(enc.meta))))},
        "trained_model_test_losses": _test_losses(model, arrays, test_pos, cfg.train.device),
        "held_out_product_evidence": held_out,
        "ablation": ablations,
        "leave_one_group_out": logo,
        "seed": cfg.train.seed,
        "versions": _versions(),
    }
    path = _write_json(out / "evaluation.json", report)
    return {"outputs": [str(path)], "ablation": {k: v.get("test_losses", v.get("status"))
                                                 for k, v in ablations.items()}}
