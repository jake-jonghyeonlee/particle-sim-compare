"""Contract tests for config (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    from pathlib import Path
    import sys, copy
    import yaml
    from simcompare.config import ConfigError, config_from_dict
    base = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "configs" / "demo.yaml"))
    config_from_dict(copy.deepcopy(base))
    def bad(path, value):
        raw = copy.deepcopy(base); node = raw
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        try:
            config_from_dict(raw)
        except ConfigError:
            return
        raise AssertionError(f"accepted {path}={value!r}")
    nan = float("nan"); inf = float("inf")
    bad(("data", "ca_grid"), [nan]); bad(("data", "fab_grid"), [1.0, inf]); bad(("train", "lr"), nan)
    bad(("model", "loss_weights"), {"layout": nan}); bad(("train", "epochs"), None); bad(("train", "seed"), None)
    bad(("model", "latent_dim"), None); bad(("analysis", "k_neighbors"), None); bad(("data", "image_size"), None)
    bad(("split", "test_fraction"), nan); bad(("analysis", "size_tolerance"), nan)
    raw = copy.deepcopy(base); raw["analysis"]["size_tolerance"] = None; raw["caa"]["reference_distribution_id"] = None
    config_from_dict(raw)


def test_regional_config_defaults_and_strict_values():
    import copy
    from pathlib import Path
    import pytest
    import yaml
    from simcompare.config import ConfigError, config_from_dict
    base = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs/demo.yaml").read_text())
    regional = config_from_dict(base).analysis.regional
    assert regional.enabled and regional.connectivity == 8
    assert regional.source_manifest is None and regional.correspondence_file is None
    assert regional.conservation_rtol == 1e-6 and regional.conservation_atol_um2 == 1e-9
    assert not regional.include_area_normalized
    invalid = [{"unknown":1}, {"enabled":None}, {"enabled":1}, {"connectivity":5},
               {"connectivity":True}, {"connectivity":None}, {"merge_gap_um":-1},
               {"conservation_rtol":float("nan")}, {"coordinate_atol_um":float("inf")},
               {"delta_atol_um2":None}, {"source_manifest":False}, {"source_manifest":""},
               {"include_area_normalized":None}]
    for values in invalid:
        raw = copy.deepcopy(base)
        raw.setdefault("analysis",{})["regional"] = values
        with pytest.raises(ConfigError):
            config_from_dict(raw)
    raw = copy.deepcopy(base)
    raw.setdefault("analysis",{})["regional"] = None
    with pytest.raises(ConfigError):
        config_from_dict(raw)
    raw["analysis"]["regional"] = {"source_manifest":None,"correspondence_file":None,"enabled":False,"connectivity":4}
    assert config_from_dict(raw).analysis.regional.connectivity == 4


def test_regional_config_paths_resolve_from_yaml_and_require_files(tmp_path):
    from pathlib import Path
    import pytest
    import yaml
    from simcompare.config import ConfigError, load_config
    base = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs/demo.yaml").read_text())
    base.setdefault("analysis",{})["regional"] = {"source_manifest":"source.json","correspondence_file":"pairs.json"}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(base))
    with pytest.raises(ConfigError,match="source_manifest|correspondence_file"):
        load_config(path)
    for name in ("source.json","pairs.json"):
        (tmp_path/name).write_text("{}")
    cfg = load_config(path)
    assert cfg.analysis.regional.source_manifest == str(tmp_path / "source.json")
    assert cfg.analysis.regional.correspondence_file == str(tmp_path / "pairs.json")
