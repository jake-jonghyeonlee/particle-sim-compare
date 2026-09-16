"""Contract tests for model (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys
    import torch
    from simcompare.model import ModelConfig, MultiInputAE, ImageDecoder
    def batch(n=3, cfg=ModelConfig()):
        h, w = cfg.image_hw
        b = {"layout": torch.rand(n, cfg.layout_channels, h, w), "layout_valid": torch.ones(n, cfg.layout_channels, h, w),
             "layout_present": torch.tensor([[1., 1.], [1., 0.], [0., 0.]]),
             "hotspot": torch.rand(n, cfg.hotspot_channels, h, w), "hotspot_valid": torch.ones(n, cfg.hotspot_channels, h, w),
             "hotspot_present": torch.ones(n, cfg.hotspot_channels),
             "ca": torch.randn(n, cfg.ca_dim), "ca_mask": torch.ones(n, cfg.ca_dim),
             "fab": torch.rand(n, cfg.fab_dim), "fab_mask": torch.ones(n, cfg.fab_dim),
             "fab_total": torch.randn(n, 1), "fab_total_mask": torch.ones(n, 1),
             "geom": torch.randn(n, cfg.geom_dim), "geom_mask": torch.zeros(n, cfg.geom_dim)}
        return b
    assert ImageDecoder(2, (8, 16, 32), 16, (64, 64))(torch.randn(1, 16)).shape == (1, 2, 64, 64)
    for enabled, has_struct, has_fab in [(("layout", "hotspot", "ca", "fab", "geometry"), True, True),
                                         (("layout", "hotspot"), True, False), (("ca", "fab", "geometry"), True, True),
                                         (("fab",), False, True)]:
        cfg = ModelConfig(enabled=enabled)
        out = MultiInputAE(cfg)(batch(cfg=cfg))
        assert (out["z_struct"] is not None) == has_struct and (out["z_fab"] is not None) == has_fab
        if "layout" in enabled:
            assert out["recon"]["layout"].shape == (3, 2, 64, 64)
        if "fab" in enabled:
            assert out["recon"]["fab"].shape == (3, 5) and out["recon"]["fab_total"].shape == (3, 1)
        assert set(out["present"]) == set(enabled)
    cfg = ModelConfig(image_hw=(32, 48), conv_channels=(4, 8), latent_dim=8)
    out = MultiInputAE(cfg)(batch(cfg=cfg))
    assert out["z"].shape == (3, 8) and out["recon"]["hotspot"].shape == (3, 3, 32, 48)
    assert ModelConfig.from_dict(cfg.to_dict()) == cfg
    for bad in (dict(enabled=()), dict(image_hw=(30, 30)), dict(latent_dim=7), dict(enabled=("nope",))):
        try:
            MultiInputAE(ModelConfig(**bad)); raise AssertionError(f"no ValueError for {bad}")
        except ValueError:
            pass
