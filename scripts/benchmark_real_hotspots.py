#!/usr/bin/env python3
"""Measure real-hotspot import RSS/time and exercise the configured group cap."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_windows_bundle import png_gray


def peak_rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return value / (1024 * 1024 if sys.platform == "darwin" else 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=312)
    parser.add_argument("--keep", type=Path, help="retain generated data for inspection")
    args = parser.parse_args()
    if args.cases < 312:
        raise SystemExit("At least 312 observed cases are required to cross the default 500,000,000-cell group cap.")
    temporary = None
    if args.keep:
        work = args.keep.resolve()
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="simcompare-real-bench-")
        work = Path(temporary.name)
    root, imported = work / "input", work / "imported"
    case_seed = work / "seed" / "lateral_short-A-L1-B-L1"
    png_gray(case_seed / "1.png", 2270, 2270)
    png_gray(case_seed / "2.png", 2270, 2270)
    png_gray(case_seed / "hotspot_lateral_short01.png", 1270, 1270, (0, 0))
    result_seed = case_seed / "result" / "lateral_short.csv"
    result_seed.parent.mkdir(parents=True)
    result_seed.write_text("defect_size(um),Area(um2)\n1,0.01\n", encoding="utf-8-sig")
    source_case = case_seed
    for index in range(args.cases):
        case = root / f"lateral_short-A{index:04d}-L1-B{index:04d}-L1"
        case.mkdir(parents=True)
        shutil.copy2(source_case / "1.png", case / "1.png")
        shutil.copy2(source_case / "2.png", case / "2.png")
        shutil.copy2(source_case / "hotspot_lateral_short01.png", case / "hotspot_lateral_short01.png")
        result = case / "result" / "lateral_short.csv"
        result.parent.mkdir()
        shutil.copy2(result_seed, result)
    source_files = sum(1 for p in root.rglob("*") if p.is_file())
    import_started = time.perf_counter()
    import_run = subprocess.run([sys.executable, "-m", "simcompare", "import-hotspots", "--root", str(root), "--out", str(imported)],
                                cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    import_seconds = time.perf_counter() - import_started
    import_peak = peak_rss_mib()
    if import_run.returncode != 3:
        raise RuntimeError(f"import expected partial exit 3; got {import_run.returncode}: {import_run.stderr}")
    guard_code = r'''
import json, sys
from pathlib import Path
from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
from simcompare.numeric_schema import NumericContractError
folder=Path(sys.argv[1]); manifest=load_real_manifest(folder)
assignment={'product_id':'P','design_version':'D','source_id':'S','process_step_id':'STEP','exposure_id':'E','unit_id':'U',
 'layer_stack':[{'actual_layer':'L1','stack_order':1,'density_process_layer':'P1'}]}
try: configured_dataset(folder,manifest,assignment)
except NumericContractError as exc:
 print(json.dumps({'guard':getattr(exc,'code',str(exc)),'detail':str(exc)})); sys.exit(0)
raise SystemExit('oversized group was not rejected')
'''
    guard_started = time.perf_counter()
    guard_run = subprocess.run([sys.executable, "-c", guard_code, str(imported)], cwd=ROOT, capture_output=True, text=True,
                               env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    guard_seconds = time.perf_counter() - guard_started
    if guard_run.returncode:
        raise RuntimeError(f"group guard failed: {guard_run.stderr}")
    guard = json.loads(guard_run.stdout)
    if guard.get("guard") != "oversized_real_mask_group":
        raise RuntimeError(f"unexpected group guard result: {guard}")
    # Exercise a grouped reduction just below the default group threshold too.
    aggregate_root, aggregate_import = work / "aggregate_input", work / "aggregate_import"
    for index in range(250):
        source = root / f"lateral_short-A{index:04d}-L1-B{index:04d}-L1"
        case = aggregate_root / source.name
        case.mkdir(parents=True)
        for relative in ("1.png", "2.png", "hotspot_lateral_short01.png", "result/lateral_short.csv"):
            (case / relative).parent.mkdir(parents=True, exist_ok=True)
            os.link(source / relative, case / relative)
    aggregate_import_run = subprocess.run([sys.executable, "-m", "simcompare", "import-hotspots", "--root", str(aggregate_root), "--out", str(aggregate_import)],
                                         cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    if aggregate_import_run.returncode != 3:
        raise RuntimeError(f"aggregate fixture import expected partial exit 3; got {aggregate_import_run.returncode}: {aggregate_import_run.stderr}")
    aggregate_code = r'''
import json, resource, sys
from pathlib import Path
from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
from simcompare.linepixel import compute_areas
folder=Path(sys.argv[1]); manifest=load_real_manifest(folder)
assignment={'product_id':'P','design_version':'D','source_id':'S','process_step_id':'STEP','exposure_id':'E','unit_id':'U',
 'layer_stack':[{'actual_layer':'L1','stack_order':1,'density_process_layer':'P1'}]}
dataset=configured_dataset(folder,manifest,assignment); result=compute_areas(dataset)
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if sys.platform == 'darwin': rss /= 1024*1024
else: rss /= 1024
print(json.dumps({'cases':len(dataset.cases),'groups':len(result.groups),'status_counts':{
 status:sum(group.union.status==status for group in result.groups.values()) for status in ('available','partial','unavailable')},
 'peak_rss_mib':round(rss,1)}))
'''
    aggregate_started = time.perf_counter()
    aggregate_run = subprocess.run([sys.executable, "-c", aggregate_code, str(aggregate_import)], cwd=ROOT, capture_output=True, text=True,
                                   env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    aggregate_seconds = time.perf_counter() - aggregate_started
    if aggregate_run.returncode:
        raise RuntimeError(f"bounded aggregation run failed: {aggregate_run.stderr}")
    aggregate = json.loads(aggregate_run.stdout)
    print(json.dumps({"benchmark": "real_hotspot_import", "platform": platform.platform(), "python": sys.version.split()[0],
                      "cases": args.cases, "source_files": source_files, "source_png_shape_yx": [1270, 1270],
                      "import_exit": import_run.returncode, "import_wall_seconds": round(import_seconds, 3),
                      "import_child_peak_rss_mib": round(import_peak, 1), "group_guard_wall_seconds": round(guard_seconds, 3),
                      "group_guard": guard, "policy_group_limit_cells": 500_000_000,
                      "below_limit_aggregation_wall_seconds": round(aggregate_seconds, 3),
                      "below_limit_aggregation": aggregate,
                      "windows_16gb_gate": "not_run_mac_or_local_measurement_is_not_windows_acceptance"}, indent=2))
    if temporary:
        temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
