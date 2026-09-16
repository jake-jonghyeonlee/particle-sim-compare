import numpy as np
import pytest
import torch

from simcompare.dataset import encode_bundles, fit_normalizers, to_model_arrays
from simcompare.train import (
    embed, load_checkpoint, model_config_from_arrays, save_checkpoint, select_device, train_model,
)


@pytest.fixture()
def arrays_meta(demo_loaded, demo_cfg):
    enc = encode_bundles(demo_loaded.bundles, demo_loaded.distributions, demo_cfg)
    train = np.arange(0, 15)
    return to_model_arrays(enc, fit_normalizers(enc, train)), enc.meta, train


def cfg_for(arrays, enabled=("layout", "hotspot", "ca", "fab", "geometry")):
    return model_config_from_arrays(arrays, enabled=enabled, conv_channels=[8, 16, 32],
                                    modality_dim=8, latent_dim=8)


def test_select_device():
    assert select_device("cpu").type == "cpu"
    assert select_device("auto").type in {"cpu", "cuda", "mps"}
    with pytest.raises(ValueError):
        select_device("tpu")
    if not torch.cuda.is_available():
        with pytest.raises(RuntimeError):
            select_device("cuda")


def test_training_never_touches_non_train_rows(arrays_meta):
    arrays, meta, train = arrays_meta
    poisoned = {k: v.copy() for k, v in arrays.items()}
    for key in ("layout", "hotspot", "ca", "fab", "fab_total", "geom"):
        poisoned[key][15:] = np.nan
    model, history = train_model(poisoned, meta, train, cfg_for(arrays), epochs=2, batch_size=4,
                                 lr=1e-3, seed=0)
    assert len(history) == 2 and all(np.isfinite(row["total"]) for row in history)
    assert {"layout", "hotspot", "ca", "fab", "geometry"} <= set(history[0])


def test_seeded_training_and_checkpoint_roundtrip(arrays_meta, tmp_path):
    arrays, meta, train = arrays_meta
    cfg = cfg_for(arrays)
    m1, h1 = train_model(arrays, meta, train, cfg, epochs=2, batch_size=4, lr=1e-3, seed=7)
    m2, h2 = train_model(arrays, meta, train, cfg, epochs=2, batch_size=4, lr=1e-3, seed=7)
    assert h1 == h2
    z1 = embed(m1, arrays)
    norms = {"ca": None}
    from simcompare.preprocess import Normalizer

    norms = {"ca": Normalizer(np.zeros(5), np.ones(5))}
    save_checkpoint(tmp_path / "m.pt", m1, cfg, norms, {"seed": 7, "ids": ["a"]})
    loaded, loaded_cfg, loaded_norms, extra = load_checkpoint(tmp_path / "m.pt")
    assert loaded_cfg == cfg and extra["seed"] == 7
    assert np.allclose(loaded_norms["ca"].std, 1.0)
    z2 = embed(loaded, arrays)
    for key in ("z", "z_struct", "z_fab"):
        assert np.array_equal(z1[key], z2[key])


def test_modality_ablation_configs_train(arrays_meta):
    arrays, meta, train = arrays_meta
    for enabled in (("layout", "hotspot"), ("ca", "fab", "geometry")):
        model, history = train_model(arrays, meta, train, cfg_for(arrays, enabled), epochs=1,
                                     batch_size=8, lr=1e-3, seed=0)
        z = embed(model, arrays)
        assert z["z"].shape[0] == len(meta)
        assert (z["z_fab"] is None) == ("fab" not in enabled)
