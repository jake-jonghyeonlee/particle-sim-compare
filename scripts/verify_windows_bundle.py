#!/usr/bin/env python3
"""Verify wheel presence and SHA256 values in a built offline bundle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(folder: Path) -> list[str]:
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "simcompare_windows_bundle_v1" or manifest.get("target") != "cp312-win_amd64":
        raise ValueError("unsupported or incorrect bundle target")
    entries = manifest.get("wheels")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest has no wheel records")
    expected = {entry["file"] for entry in entries}
    actual = {path.name for path in (folder / "wheelhouse").glob("*.whl")}
    if actual != expected:
        raise ValueError(f"wheel inventory differs; missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
    for entry in entries:
        path = folder / "wheelhouse" / entry["file"]
        if path.stat().st_size != entry["size_bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"wheel integrity check failed: {entry['file']}")
    return sorted(expected)


if __name__ == "__main__":
    try:
        names = verify(Path(sys.argv[1]).resolve())
        print(f"wheel integrity OK ({len(names)} files)")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"bundle verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
