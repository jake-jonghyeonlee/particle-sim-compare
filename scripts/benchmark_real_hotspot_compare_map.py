#!/usr/bin/env python3
"""Benchmark configured compare and map for 8 cases × all 32 observed diameters."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_windows_bundle import png_gray


def mib(ru_maxrss: int) -> float:
    return ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)


def main() -> int:
    work_obj = tempfile.TemporaryDirectory(prefix="simcompare-8x32-bench-")
    work = Path(work_obj.name)
    root, imported = work / "input", work / "imported"
    expected, pair_path = work / "expected_cases.txt", imported / "pairs.csv"
    names = [f"lateral_short-A{i:03d}-L1-B{i:03d}-L1" for i in range(8)]
    expected.write_text("\n".join(names) + "\n", encoding="utf-8")
    for index, name in enumerate(names):
        case = root / name
        png_gray(case / "1.png", 2270, 2270)
        png_gray(case / "2.png", 2270, 2270)
        result = case / "result" / "lateral_short.csv"
        result.parent.mkdir(parents=True)
        with result.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("defect_size(um)", "Area(um2)"))
            for diameter in (*range(1, 31), 40, 50):
                writer.writerow((diameter, "0.01"))
        for ordinal, diameter in enumerate((*range(1, 31), 40, 50)):
            # Eight separate occupied cells produce an auditable nonzero union.
            png_gray(case / f"hotspot_lateral_short{diameter:02d}.png", 1270, 1270,
                     (index * 2, index * 2))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    import_start = time.perf_counter()
    imported_run = subprocess.run([sys.executable, "-m", "simcompare", "import-hotspots",
        "--root", str(root), "--out", str(imported), "--expected-cases", str(expected)],
        cwd=ROOT, env=env, capture_output=True, text=True)
    import_seconds = time.perf_counter() - import_start
    if imported_run.returncode != 3:
        raise RuntimeError(f"import expected documented incomplete exit 3, got {imported_run.returncode}: {imported_run.stderr}")
    with pair_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("pair_id", "electrode_a_id", "electrode_b_id", "class", "pair_table_version"))
        for index in range(8):
            writer.writerow((f"P{index:03d}", f"A{index:03d}", f"B{index:03d}", "line", "bench-pairs-v1"))
    from simcompare.adapters.real_hotspot import load_real_manifest
    load_real_manifest(imported)
    assignment = {"product_id": "BENCH", "design_version": "BENCH", "source_id": "BENCH",
        "process_step_id": "STEP", "exposure_id": "EXP", "unit_id": "UNIT",
        "pair_table_path": "pairs.csv",
        "layer_stack": [{"actual_layer": "L1", "stack_order": 1, "density_process_layer": "P1"}]}
    cfg = {"schema_version": "numeric_config_v1", "mode": "real", "output_dir": "reports",
        "reference": {"data_dir": str(imported), "condition_assignment": assignment},
        "target": {"data_dir": str(imported), "condition_assignment": assignment},
        "map": {"input": {"data_dir": str(imported), "condition_assignment": assignment}, "diameter_um": 1}}
    config = work / "bench config.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    helper = r'''import json,resource,sys,time
from simcompare.pipeline import load_numeric_config,run_numeric_compare,run_numeric_map
cfg=load_numeric_config(sys.argv[1]); mode=sys.argv[2]; start=time.perf_counter()
r=run_numeric_compare(cfg) if mode=='compare' else run_numeric_map(cfg)
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rss/=1024*1024 if sys.platform=='darwin' else 1024
print(json.dumps({'mode':mode,'status':r['status'],'exit_code':r['exit_code'],
 'wall_seconds':round(time.perf_counter()-start,3),'peak_rss_mib':round(rss,1)}))
'''
    measurements = {}
    for mode in ("compare", "map"):
        run = subprocess.run([sys.executable, "-c", helper, str(config), mode], cwd=ROOT,
                             env=env, capture_output=True, text=True)
        if run.returncode != 0:
            raise RuntimeError(f"configured {mode} failed: {run.stderr}")
        measurements[mode] = json.loads(run.stdout)
    comparison = json.loads((work / "reports" / "comparison.json").read_text(encoding="utf-8"))
    group = next(g for g in comparison["reference"]["groups"] if g["group"]["diameter_um"] == 1)
    if group["union"]["status"] != "available" or abs(group["union"]["value"] - 0.08) > 1e-9:
        raise RuntimeError(f"unexpected configured union: {group['union']}")
    mapped = json.loads((work / "reports" / "electrode_map.json").read_text(encoding="utf-8"))
    layer = mapped["layers"][0]
    if layer["status"] != "available" or int(layer["histogram"].get("2", 0)) != 8:
        raise RuntimeError(f"unexpected configured map output: {layer.get('histogram')}")
    result = {"benchmark": "configured_real_hotspot_compare_and_map", "platform": platform.platform(),
        "python": sys.version.split()[0], "cases": 8, "observed_pngs": 256,
        "hotspot_png_shape_yx": [1270, 1270], "explicit_pairs": 8,
        "import_exit_code": imported_run.returncode, "import_wall_seconds": round(import_seconds, 3),
        "compare": measurements["compare"], "map": measurements["map"],
        "verified_union_um2": group["union"]["value"], "verified_map_cells_at_count_2": int(layer["histogram"]["2"]),
        "windows_16gb_gate": "pending_windows_execution"}
    print(json.dumps(result, indent=2))
    work_obj.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
