import numpy as np
import pytest

from simcompare.baseline import BaselinePCA, explicit_features
from simcompare.dataset import encode_bundles, fit_normalizers, subset_arrays, to_model_arrays


@pytest.fixture()
def enc(demo_loaded, demo_cfg):
    return encode_bundles(demo_loaded.bundles, demo_loaded.distributions, demo_cfg)


def pos(enc, sid):
    return int(np.flatnonzero(enc.meta["sample_id"] == sid)[0])


def test_masks_distinguish_missing_from_zero(enc):
    i = pos(enc, "P2-v1-OPEN")
    assert not enc.ca_mask[i, -1] and np.isnan(enc.ca_log[i, -1])
    assert enc.layout_present[i, 1] == 0  # open: slot b not applicable
    p4 = pos(enc, "P4-v1-LSHORT")
    assert enc.hotspot_present[p4].tolist() == [1, 1, 0]
    p6 = pos(enc, "P6-v1-LSHORT")
    assert not enc.ca_area_mask[p6].any() and enc.ca_mask[p6].any()
    assert not enc.geom_mask[p6, :2].any()
    p3 = pos(enc, "P3-v1-OPEN")  # grid [1,3,4,8,16] vs configured [1,2,4,8,16]
    assert enc.ca_mask[p3].tolist() == [True, False, True, True, True]
    assert any("면적 대비 CA 계산 불가" in note for note in enc.notes)


def test_letterbox_keeps_aspect_with_valid_mask(enc):
    i = pos(enc, "P3-v1-LSHORT")  # 96 x 64 source -> 32 x 32 target
    valid_cols = enc.layout_valid[i, 0].max(axis=0)
    assert 0 < valid_cols.sum() < valid_cols.size


def test_normalizers_use_train_rows_only(enc):
    train = np.arange(0, 9)
    norms = fit_normalizers(enc, train)
    shifted = enc.ca_log.copy()
    shifted[9:] += 100.0
    enc2 = type(enc)(**{**enc.__dict__, "ca_log": shifted})
    assert np.allclose(fit_normalizers(enc2, train)["ca"].mean, norms["ca"].mean)
    arrays = to_model_arrays(enc, norms)
    assert all(np.isfinite(v).all() for v in arrays.values())
    assert subset_arrays(arrays, [0, 2])["ca"].shape[0] == 2


def test_fab_unavailable_is_masked_not_zero(demo_loaded, demo_cfg):
    bundle = demo_loaded.bundles[0]
    original = bundle.assigned_process_layer
    try:
        bundle.assigned_process_layer = "NO_SUCH_LAYER"
        enc = encode_bundles([bundle], demo_loaded.distributions, demo_cfg)
    finally:
        bundle.assigned_process_layer = original
    assert not enc.fab_mask.any() and not enc.fab_total_mask.any()
    assert any("Fab 분포 사용 불가" in note for note in enc.notes)


def test_baseline_pca_fits_on_train_rows_only(enc):
    x, mask, names = explicit_features(enc, include_risk=True)
    assert "layout0_fill" in names and "fab_total_log" in names
    assert np.isnan(x[~mask]).all()
    train = np.arange(12)
    base = BaselinePCA(4).fit(x, mask, train)
    changed = x.copy()
    changed[12:] = np.where(mask[12:], x[12:] * 50.0, np.nan)
    other = BaselinePCA(4).fit(changed, mask, train)
    assert np.allclose(base.transform(x, mask)[train], other.transform(changed, mask)[train])
    shape_x, _, shape_names = explicit_features(enc, include_risk=False)
    assert not any(n.startswith(("ca_log", "hot", "fab")) for n in shape_names)
    with pytest.raises(ValueError):
        BaselinePCA(4).fit(x, mask, np.array([0]))
