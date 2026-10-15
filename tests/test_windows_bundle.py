import importlib.util
from pathlib import Path
import shutil

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_windows_bundle.py"
SPEC = importlib.util.spec_from_file_location("verify_windows_bundle", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _bundle(root):
    wheels = root / "wheelhouse"
    wheels.mkdir()
    wheel = wheels / "sample-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes")
    (root / "manifest.json").write_text(
        __import__("json").dumps({"schema_version": "simcompare_windows_bundle_v1", "target": "cp312-win_amd64",
            "wheels": [{"file": wheel.name, "sha256": MODULE.sha256(wheel), "size_bytes": wheel.stat().st_size}]}),
        encoding="utf-8")
    return wheel


def test_bundle_hash_verifier_accepts_matching_inventory(tmp_path):
    wheel = _bundle(tmp_path)
    assert MODULE.verify(tmp_path) == [wheel.name]


@pytest.mark.parametrize("damage", ["tamper", "missing"])
def test_bundle_hash_verifier_rejects_tampered_or_missing_wheel(tmp_path, damage):
    wheel = _bundle(tmp_path)
    if damage == "tamper":
        wheel.write_bytes(b"changed")
    else:
        wheel.unlink()
    with pytest.raises(ValueError):
        MODULE.verify(tmp_path)
