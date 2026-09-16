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
