"""Comparison baseline: explicit features + standardisation + PCA.

Uses the same retrieval procedure as the autoencoder so that the need for deep
learning can be checked. Missing values are imputed only inside BaselinePCA and
recorded with missing-indicator columns; the raw feature table keeps NaN.
"""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA

from simcompare.preprocess import Normalizer


def _layout_features(image: np.ndarray, valid: np.ndarray) -> tuple[float, float, float]:
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
        for j in range(enc.fab_shape.shape[1]):
            columns.append(np.where(enc.fab_mask[:, j], enc.fab_shape[:, j], np.nan))
            names.append(f"fab_shape:{j}")
        columns.append(np.where(enc.fab_total_mask[:, 0], enc.fab_total_log[:, 0], np.nan))
        names.append("fab_total_log")
    x = np.column_stack(columns).astype(float) if columns else np.zeros((n, 0))
    mask = np.isfinite(x)
    return x, mask, names


class BaselinePCA:
    def __init__(self, n_components: int = 8):
        self.n_components = n_components
        self.normalizer: Normalizer | None = None
        self.keep: np.ndarray | None = None
        self.indicator: np.ndarray | None = None
        self.pca: PCA | None = None

    def _matrix(self, x: np.ndarray, mask: np.ndarray) -> np.ndarray:
        z = self.normalizer.transform(np.where(mask, x, 0.0), mask)[:, self.keep]
        indicators = (~mask[:, self.indicator]).astype(float)
        return np.hstack([z, indicators])

    def fit(self, x: np.ndarray, mask: np.ndarray, train_pos) -> "BaselinePCA":
        rows = np.asarray(train_pos, dtype=int)
        self.normalizer = Normalizer.fit(np.where(mask, x, 0.0), mask, rows)
        observed = mask[rows]
        self.keep = observed.any(axis=0)
        self.indicator = self.keep & ~observed.all(axis=0)
        matrix = self._matrix(x, mask)
        n_comp = min(self.n_components, len(rows) - 1, matrix.shape[1])
        if n_comp < 1:
            raise ValueError("baseline PCA needs at least 2 training rows and 1 observed feature")
        self.pca = PCA(n_components=n_comp, random_state=0).fit(matrix[rows])
        return self

    def transform(self, x: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if self.pca is None:
            raise RuntimeError("BaselinePCA is not fitted")
        return self.pca.transform(self._matrix(x, mask))

    @property
    def explained_variance_ratio_(self) -> np.ndarray:
        return self.pca.explained_variance_ratio_
