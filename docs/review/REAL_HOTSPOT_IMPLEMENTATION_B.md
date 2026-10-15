# Real hotspot implementation B — Windows bundle and resource evidence

## Deliverable

Task B first built the ignored offline bundle at `outputs/windows_cp312_win_amd64.zip`; Task C rebuilt that archive after source fixes and smoke updates. The final archive size and SHA-256 are recorded in `docs/review/REAL_HOTSPOT_FIX_C.md`. It contains 16 wheels: the `simcompare` project wheel and target-compatible wheels for the full pinned runtime dependency closure. All six declared direct dependencies remain present: NumPy, pandas, PyArrow, Zarr, Pillow and PyYAML. `requirements-windows.txt` pins the resolved graph with hashes; `manifest.json` records each wheel filename, size and SHA-256.

The connected-machine builder is `scripts/build_windows_bundle.py`; it requires uv 0.11.23 and builds with Hatchling 1.27.0 from `scripts/windows-build-constraints.txt`. It resolves `scripts/windows-runtime.in` for CPython 3.12 `x86_64-pc-windows-msvc`, requires binary-only wheels and downloads with pip's `win_amd64`/CPython 3.12 target selectors and hash checking. The bundle includes the real-style fixture, expected incomplete results, wheel verifier and `install_and_smoke.ps1`; offline installation uses only venv pip, `--no-index`, `--only-binary=:all:` and `--require-hashes`.

## Evidence

- `python scripts/build_windows_bundle.py` — built the wheelhouse and archive; manifest verifier reported 16 intact wheels.
- `unzip -t outputs/windows_cp312_win_amd64.zip` — archive integrity passed.
- `python scripts/verify_windows_bundle.py outputs/windows_cp312_win_amd64` — passed; tampered-wheel and missing-wheel cases also passed in `pytest tests/test_windows_bundle.py -q` (3 tests).
- Wheel metadata check using `packaging.tags` — all 16 wheels match CPython 3.12 Windows x64 compatible tags; all six direct dependency pins were found in the lock.
- The original local smoke exercised a raw fixture and is superseded by Task C's configured fixture: it supplies a complete expected-case inventory, explicit line pair and condition assignments; its diameter-1 union and map count are checked. Electrode polarity limits review ROI only; the map uses observed hotspot occupancy and explicit case grouping without polarity.
- `python scripts/benchmark_real_hotspot_compare_map.py` — macOS 26.5.2 arm64, Python 3.11.15: 8 cases × 32 observed diameters = 256 1270×1270 PNGs, expected inventory, eight explicit line pairs and one explicit layer; import 0.714 s, configured compare 4.512 s/1,154.5 MiB peak RSS, configured map 1.202 s/576.2 MiB. Assertions confirmed the diameter-1 union at 0.08 µm² and eight map cells with count 2. The command exits 3 for documented incomplete rate outputs; the map result itself is available. This is a Mac synthetic resource measurement, not Windows or real-data acceptance.
- The earlier `scripts/benchmark_real_hotspots.py --cases 312` probe covered 312 cases with only one observed diameter and the 250-case aggregate had no pair table. It only establishes import/group-cap behavior and is not a substitute for the full diameter configured benchmark above.
- `git diff --check` — passed.

## Pending Windows acceptance

Windows execution remains pending; no Windows host or real data was used. The earlier resource run did not cover all observed diameters; Task C adds a configured 8-case × 32-diameter, 256-PNG compare/map benchmark. On Windows x64, extract the rebuilt archive into a Unicode/spaced path and run:

```powershell
Expand-Archive -LiteralPath '.\windows_cp312_win_amd64.zip' -DestinationPath '.\폐쇄망 테스트'
Set-Location '.\폐쇄망 테스트\windows_cp312_win_amd64'
Set-ExecutionPolicy -Scope Process Bypass
.\install_and_smoke.ps1
```

Expected: interpreter check confirms CPython 3.12 x64; wheel manifest verification, offline hash-checked installation, `pip check`, imports and fixture commands succeed. `import-hotspots`, `validate-data`, `compare` and `electrode-map` each return documented incomplete exit 3; the configured comparison's diameter-1 union is 0.01 µm² and available, and the map histogram contains one cell with two endpoint hits. Any other nonzero exit or mismatch fails the script. The measured local benchmark is synthetic and does not certify 16 GB Windows behavior or real-data execution.
