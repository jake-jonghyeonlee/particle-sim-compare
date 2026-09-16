import json

import numpy as np

from simcompare.schema import NOT_APPLICABLE


def test_demo_data_covers_required_conditions(demo_dir, demo_loaded):
    summary = json.loads((demo_dir / "summary.json").read_text())
    assert "not physical" in summary["notes"][0]
    bundles = {b.sample_id: b for b in demo_loaded.bundles}
    assert len(bundles) == summary["n_bundles"] == 21
    assert {(b.unit_width, b.unit_height) for b in bundles.values()} >= {(60.0, 90.0), (45.0, 70.0), (None, None)}
    opened = bundles["P1-v1-OPEN"]
    assert opened.electrode_b_role == NOT_APPLICABLE and len(opened.layouts) == 1
    assert len(bundles["P1-v1-LSHORT"].layouts) == 2
    assert bundles["P1-v2-OPEN"].parent_design_id == "P1-v1"
    assert bundles["P1-v2-OPEN"].design_lineage_id == bundles["P1-v1-OPEN"].design_lineage_id
    p2 = bundles["P2-v1-OPEN"].ca
    assert not p2.observed[-1] and np.isnan(p2.values[-1])
    assert all(h.particle_size != 8.0 for h in bundles["P4-v1-OPEN"].hotspots)
    assert bundles["P4-v1-OPEN"].geometry is None
    assert not bundles["P6-v1-VSHORT"].has_physical_scale()
    assert all(b.provenance.is_synthetic for b in bundles.values())
    assert bundles["P5-v1-VSHORT"].fab_id != bundles["P6-v1-VSHORT"].fab_id
    assert any("physical" in w for w in demo_loaded.warnings)


def test_generation_is_deterministic(tmp_path):
    from simcompare.synthetic import generate_demo_data

    generate_demo_data(tmp_path / "a", seed=3)
    generate_demo_data(tmp_path / "b", seed=3)
    for name in ("ca_records.csv", "fab.json", "bundles.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
