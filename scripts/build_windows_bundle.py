#!/usr/bin/env python3
"""Connected-machine builder for the offline CPython 3.12 win_amd64 bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.11.23"
TARGET = "x86_64-pc-windows-msvc"


def run(*args: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def png_gray(path: Path, width: int, height: int, white_at: tuple[int, int] | None = None) -> None:
    """Write a deterministic 8-bit grayscale PNG without requiring Pillow."""
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        line = bytearray(width)
        if white_at is not None and white_at[1] == y:
            line[white_at[0]] = 255
        rows.extend(line)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(bytes(rows), 6))
    payload += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def create_fixture(folder: Path) -> None:
    case = folder / "input" / "lateral_short-A-L1-B-L1"
    png_gray(case / "1.png", 2270, 2270)
    png_gray(case / "2.png", 2270, 2270)
    png_gray(case / "hotspot_lateral_short01.png", 1270, 1270, (0, 0))
    result = case / "result" / "lateral_short.csv"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("defect_size(um),Area(um2)\n1,0.01\n", encoding="utf-8-sig")
    (folder / "expected_cases.txt").write_text("lateral_short-A-L1-B-L1\n", encoding="utf-8")
    (folder / "pairs.csv").write_text(
        "pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n"
        "AB,A,B,line,pair-v1\n", encoding="utf-8")
    (folder / "expected.json").write_text(json.dumps({
        "is_synthetic": False,
        "crop_shape_yx": [1270, 1270],
        "diameter_1_area_um2": 0.01,
        "diameter_1_csv_status": "consistent",
        "other_diameters": "missing",
        "expected_command_exit_codes": {"import-hotspots": 3, "validate-data": 3, "compare": 3, "electrode-map": 3},
        "rates": "unavailable_without_confirmed_physical_metadata_and_assignments",
        "diameter_1_union_status": "available",
        "diameter_1_union_um2": 0.01,
        "diameter_1_map_status": "available",
        "diameter_1_map_max_count": 2,
        "diameter_1_map_cells_at_count_2": 1,
        "unknown_electrode_polarity_blocks": "review ROI only"
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "windows_cp312_win_amd64")
    args = parser.parse_args()
    output = args.output.resolve()
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install the pinned uv 0.11.23 executable on a connected build machine first.")
    version = subprocess.check_output([uv, "--version"], text=True).strip()
    if len(version.split()) < 2 or version.split()[1] != UV_VERSION:
        raise SystemExit(f"uv {UV_VERSION} required; found {version}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    wheelhouse = output / "wheelhouse"
    wheelhouse.mkdir()
    with tempfile.TemporaryDirectory(prefix="simcompare-win-build-") as temporary:
        built = Path(temporary) / "built"
        run(uv, "build", "--wheel", "--out-dir", str(built), "--build-constraints",
            str(ROOT / "scripts" / "windows-build-constraints.txt"), cwd=ROOT)
        app_wheel = next(built.glob("simcompare-*.whl"))
        shutil.copy2(app_wheel, wheelhouse / app_wheel.name)

        requirements = output / "requirements-windows.txt"
        run(uv, "pip", "compile", "scripts/windows-runtime.in", "--no-header",
            "--python-platform", TARGET, "--python-version", "3.12", "--only-binary", ":all:",
            "--generate-hashes", "--output-file", str(requirements))
        with requirements.open("a", encoding="utf-8") as f:
            f.write(f"\nsimcompare==0.1.0 --hash=sha256:{digest(wheelhouse / app_wheel.name)}\n")

        # pip's target tags select only cp312/abi3/none wheels for Windows x64.
        pip = shutil.which("pip3") or shutil.which("pip")
        if not pip:
            raise SystemExit("pip is required on this connected build machine to download target wheels.")
        run(pip, "download", "--require-hashes", "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312", "--abi", "abi3",
            "--abi", "none", "--find-links", str(wheelhouse), "--dest", str(wheelhouse), "-r", str(requirements))

    (output / "fixtures").mkdir()
    create_fixture(output / "fixtures" / "real_hotspot")
    for name in ("install_offline.cmd", "verify_windows_bundle.py"):
        shutil.copy2(ROOT / "scripts" / name, output / name)
    shutil.copy2(ROOT / "docs" / "usage.md", output / "INSTALL_CMD.md")
    wheel_entries = []
    for wheel in sorted(wheelhouse.glob("*.whl")):
        wheel_entries.append({"file": wheel.name, "sha256": digest(wheel), "size_bytes": wheel.stat().st_size})
    manifest = {"schema_version": "simcompare_windows_bundle_v1", "target": "cp312-win_amd64",
                "uv_builder_version": UV_VERSION, "build_constraints": "hatchling==1.27.0",
                "wheels": wheel_entries}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    run(sys.executable, str(output / "verify_windows_bundle.py"), str(output))
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(output.parent))
    print(json.dumps({"bundle": str(archive), "size_bytes": archive.stat().st_size,
                      "sha256": digest(archive), "wheel_count": len(wheel_entries)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
