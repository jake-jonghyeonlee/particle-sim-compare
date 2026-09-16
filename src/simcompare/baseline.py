"""비교 기준 모델: 명시적 특징 + 표준화 + PCA.

딥러닝이 실제로 필요한지 확인하기 위해 오토인코더와 같은 검색 절차에 넣어 비교한다.
누락값 대체는 BaselinePCA 내부에서만 하고 누락 지시 열로 기록한다.
원래 특징 표는 NaN을 그대로 유지한다.
"""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA

from simcompare.preprocess import Normalizer


def _layout_features(image: np.ndarray, valid: np.ndarray) -> tuple[float, float, float]:
    """전극 이미지 하나의 채움 비율, 경계 밀도, 전경 bounding box 종횡비를 계산한다(유효 영역 안만 사용)."""
    inside = valid > 0.5
    count = inside.sum()
    fill = float(image[inside].mean()) if count else np.nan
    pairs = diffs = 0
    for a, b, va, vb in (
        (image[:, :-1], image[:, 1:], inside[:, :-1], inside[:, 1:]),
        (image[:-1, :], image[1:, :], inside[:-1, :], inside[1:, :]),
    ):
        both = va & vb
        pairs += int(both.sum())
        diffs += int(((a != b) & both).sum())
    edge = diffs / pairs if pairs else np.nan
    rows, cols = np.nonzero((image > 0.5) & inside)
    if rows.size:
        aspect = (rows.max() - rows.min() + 1) / (cols.max() - cols.min() + 1)
    else:
        aspect = np.nan
    return fill, edge, float(aspect)


def explicit_features(enc, *, include_risk: bool) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """명시적 특징 행렬, 관측 마스크, 열 이름을 반환한다.

    include_risk가 False이면 형상 특징(layout 요약, 실제 크기, 두께·형상 수치)만 쓴다.
    True이면 hotspot 요약, CA 곡선, Fab 모양·총량까지 포함한다. 이 경우 위험 수치를
    입력으로 재사용하므로 그 공간의 유사성은 독립적인 발견이 아니다.
    """
    n = len(enc.meta)
    columns: list[np.ndarray] = []
    names: list[str] = []
    for slot in range(enc.layout.shape[1]):
        feats = np.full((n, 3), np.nan)
        for i in range(n):
            if enc.layout_present[i, slot] > 0:
                feats[i] = _layout_features(enc.layout[i, slot], enc.layout_valid[i, slot])
        for j, label in enumerate(("fill", "edge", "aspect")):
            columns.append(feats[:, j])
            names.append(f"layout{slot}_{label}")
    for j, name in enumerate(enc.geom_names):
        columns.append(np.where(enc.geom_mask[:, j], enc.geom[:, j], np.nan))
        names.append(f"geom:{name}")
    if include_risk:
        for k in range(enc.hotspot.shape[1]):
            feats = np.full((n, 3), np.nan)
            for i in range(n):
                if enc.hotspot_present[i, k] > 0:
                    inside = enc.hotspot_valid[i, k] > 0.5
                    values = enc.hotspot[i, k][inside]
                    if values.size:
                        feats[i] = (values.mean(), values.max(), (values > 0.5).mean())
            for j, label in enumerate(("mean", "max", "frac50")):
                columns.append(feats[:, j])
                names.append(f"hot{k}_{label}")
        for j in range(enc.ca_log.shape[1]):
            columns.append(np.where(enc.ca_mask[:, j], enc.ca_log[:, j], np.nan))
            names.append(f"ca_log:{j}")
        for j in range(enc.ca_area_log.shape[1]):
            columns.append(np.where(enc.ca_area_mask[:, j], enc.ca_area_log[:, j], np.nan))
            names.append(f"ca_per_area_log:{j}")
        for j in range(enc.fab_shape.shape[1]):
            columns.append(np.where(enc.fab_mask[:, j], enc.fab_shape[:, j], np.nan))
            names.append(f"fab_shape:{j}")
        columns.append(np.where(enc.fab_total_mask[:, 0], enc.fab_total_log[:, 0], np.nan))
        names.append("fab_total_log")
    x = np.column_stack(columns).astype(float) if columns else np.zeros((n, 0))
    mask = np.isfinite(x)
    return x, mask, names


class BaselinePCA:
    """학습 행으로만 표준화와 PCA를 적합하는 기준 임베딩."""
    def __init__(self, n_components: int = 8):
        self.n_components = n_components
        self.normalizer: Normalizer | None = None
        self.keep: np.ndarray | None = None
        self.indicator: np.ndarray | None = None
        self.pca: PCA | None = None

    def _matrix(self, x: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """표준화 값(누락은 학습 평균 위치인 0)과 누락 지시 열을 이어 붙인 행렬을 만든다."""
        z = self.normalizer.transform(np.where(mask, x, 0.0), mask)[:, self.keep]
        indicators = (~mask[:, self.indicator]).astype(float)
        return np.hstack([z, indicators])

    def fit(self, x: np.ndarray, mask: np.ndarray, train_pos) -> "BaselinePCA":
        """학습 행으로 표준화 통계, 사용할 특징, 누락 지시 열, PCA를 적합한다.

        학습 행에서 한 번도 관측되지 않은 특징은 제외한다. 학습 행이 2개 미만이면 ValueError.
        """
        rows = np.asarray(train_pos, dtype=int)
        # 표준화 통계는 학습 행에서만 계산한다.
        self.normalizer = Normalizer.fit(np.where(mask, x, 0.0), mask, rows)
        observed = mask[rows]
        self.keep = observed.any(axis=0)
        self.indicator = self.keep & ~observed.all(axis=0)
        matrix = self._matrix(x, mask)
        n_comp = min(self.n_components, len(rows) - 1, matrix.shape[1])
        if n_comp < 1:
            raise ValueError("기준 PCA에는 학습 행 2개 이상과 관측된 특징 1개 이상이 필요합니다")
        # PCA도 학습 행으로만 적합한다.
        self.pca = PCA(n_components=n_comp, random_state=0).fit(matrix[rows])
        return self

    def transform(self, x: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """적합된 기준으로 전체 행을 PCA 좌표로 변환한다."""
        if self.pca is None:
            raise RuntimeError("BaselinePCA가 아직 학습되지 않았습니다")
        return self.pca.transform(self._matrix(x, mask))

    @property
    def explained_variance_ratio_(self) -> np.ndarray:
        """PCA 성분별 설명 분산 비율."""
        return self.pca.explained_variance_ratio_
