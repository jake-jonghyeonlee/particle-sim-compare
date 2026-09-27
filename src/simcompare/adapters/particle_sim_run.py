"""particle-sim 실행 결과를 simcompare 데이터 계약으로 점검한다(읽기 전용).

particle-sim `simulate-nominal` / `simulate-probability` 결과 디렉터리(run_manifest.json,
ca.parquet, hotspots.zarr 또는 probability.zarr)를 직접 읽는다. 결과 파일은 고치지 않는다.

점검은 세 단계다.
1. integrity: run_manifest와 ca.parquet의 schema·열·키·값 범위, unit ID 형식,
   hotspot/probability 저장소와 CA 표의 면적 일치(zarr가 있을 때).
2. mapping: particle-sim 불량 단위(unit_kind, failure_mode)를 simcompare failure_type에 대응한다.
   대응이 없는 unit은 이유와 함께 unsupported로 남긴다.
3. contract: 대응된 unit마다 AnalysisBundle을 만들어 simcompare.schema.validate_bundle로 검사한다.

대응표
- --mapping YAML은 기본 대응표의 진단용 덮어쓰기다.
- 대상 failure_type은 현재 schema.FAILURE_TYPES 안에 있어야 한다.
- CNO는 기본적으로 미지원이며 exporter/분석 계약 확장은 이 검사기의 범위 밖이다.
- vertical_short는 제품 설정의 적층 순서로 상부 layer를 검증하며, 설정이 없으면 미검증 경고를 유지한다.

종료 코드: 0 통과, 1 무결성·계약 오류(--strict면 unsupported unit 포함), 2 사용법·입력 오류.
실행: bash scripts/5_check_particle_sim_run.sh RUN_DIR [...]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from simcompare import schema

SUPPORTED_SCHEMAS = ("1.0", "2.0")
NOT_APPLICABLE = "N/A"
NOT_APPLICABLE_STEP = "not_applicable_source_step"
EXCLUDED_OPEN_CLASSIFICATIONS = frozenset({"baseline_open", "terminal_lost", "insufficient_terminals"})
CNO_PREFIX = "cno:"
AUTO_OPEN_PREFIX = "auto_open:"
SAMPLE_ID_ALLOWED = re.compile(r"[^A-Za-z0-9_.-]")
PROBABILITY_LEGEND_ID = "particle_sim_probability_0_1"

BASE_COLUMNS = (
    "source_step", "particle_size_um", "pair_id", "failure_mode", "electrode_a", "electrode_b",
    "ca_nominal_um2", "ca_nominal_normalized",
)
V2_COLUMNS = (
    "failure_unit_id", "unit_kind", "electrode_a_layer", "electrode_b_layer", "classification",
    "fab_distribution_layer",
)
MANIFEST_KEYS = (
    "schema_version", "run_id", "product_id", "design_version", "simulator_version",
    "source_steps", "particle_sizes_um",
)


@dataclass(frozen=True)
class UnitMapping:
    """particle-sim 불량 단위 하나의 simcompare 대응. failure_type이 None이면 대응 없음."""
    failure_type: str | None
    note: str


FAILURE_MAPPING: dict[tuple[str, str], UnitMapping] = {
    ("electrode_pair", "lateral_short"): UnitMapping("lateral_short", "전극쌍 lateral short"),
    ("electrode_pair", "vertical_short"): UnitMapping("vertical_short", "전극쌍 vertical short"),
    ("open_path", "lateral_open"): UnitMapping("lateral_open", "명시 open 경로(전극 1개)"),
    ("open_electrode", "lateral_open"): UnitMapping(
        "lateral_open", "contact 기반 자동 단자 open(전극 1개)"
    ),
    ("contact", "contact_not_open"): UnitMapping(
        None,
        "CNO(contact not open)는 contact 하나와 상·하부 전극 2개로 정의된다. "
        "simcompare failure_type(lateral_open/lateral_short/vertical_short)에 해당 항목이 없다",
    ),
}


@dataclass
class Finding:
    level: str  # error | warning | info
    stage: str  # input | integrity | mapping | contract
    code: str
    message: str
    unit: str | None = None


@dataclass
class RunReport:
    run_dir: str
    schema_version: str | None = None
    run_id: str | None = None
    run_type: str | None = None  # nominal | probability
    findings: list[Finding] = field(default_factory=list)
    units: list[dict] = field(default_factory=list)
    bundles: list[str] = field(default_factory=list)

    def add(self, level: str, stage: str, code: str, message: str, unit: str | None = None) -> None:
        self.findings.append(Finding(level, stage, code, message, unit))

    def count(self, level: str, stage: str | None = None) -> int:
        return sum(1 for f in self.findings if f.level == level and (stage is None or f.stage == stage))


class InputError(Exception):
    """결과 디렉터리를 읽을 수 없을 때(종료 코드 2)."""


@dataclass
class RunData:
    run_dir: Path
    manifest: dict
    ca: pd.DataFrame
    schema_version: str
    store_path: Path | None
    run_type: str
    pixel_area_um2: float | None
    pixel_size_um: float | None
    roi_area_um2: float | None
    roi_width_um: float | None
    roi_height_um: float | None


# ---------------------------------------------------------------- 입력

def load_run(run_dir: Path) -> RunData:
    """결과 디렉터리를 읽어 공통 열(unit_id, unit_kind)을 갖춘 표로 정리한다."""
    if not run_dir.is_dir():
        raise InputError(f"결과 디렉터리가 없습니다: {run_dir}")
    manifest_path = run_dir / "run_manifest.json"
    ca_path = run_dir / "ca.parquet"
    for path in (manifest_path, ca_path):
        if not path.is_file():
            raise InputError(f"필수 파일이 없습니다: {path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InputError(f"run_manifest.json을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(manifest, dict):
        raise InputError("run_manifest.json은 객체여야 합니다")
    try:
        ca = pd.read_parquet(ca_path)
    except ImportError as exc:
        raise InputError(
            "parquet을 읽으려면 pyarrow가 필요합니다. scripts/5_check_particle_sim_run.sh로 실행하세요"
        ) from exc
    except (ValueError, OSError) as exc:
        raise InputError(f"ca.parquet을 읽을 수 없습니다: {exc}") from exc
    version = str(manifest.get("schema_version", ""))
    if "failure_unit_id" in ca.columns:
        ca["unit_id"] = ca["failure_unit_id"]
    elif "pair_id" in ca.columns:
        ca["unit_id"] = ca["pair_id"]
    if "unit_kind" not in ca.columns and "failure_mode" in ca.columns:
        # schema 1.0에는 unit_kind가 없다. 당시 unit은 전극쌍과 명시 open 경로뿐이었다.
        ca["unit_kind"] = np.where(ca["failure_mode"] == "lateral_open", "open_path", "electrode_pair")
    store_path = None
    run_type = "unknown"
    for name, kind in (("hotspots.zarr", "nominal"), ("probability.zarr", "probability")):
        if (run_dir / name).exists():
            store_path, run_type = run_dir / name, kind
            break
    px_x, px_y = manifest.get("pixel_size_x_um"), manifest.get("pixel_size_y_um")
    shape = manifest.get("shape_yx")
    pixel_area = float(px_x) * float(px_y) if px_x and px_y else None
    roi_area = roi_w = roi_h = None
    if pixel_area and shape and len(shape) == 2:
        roi_h, roi_w = float(shape[0]) * float(px_y), float(shape[1]) * float(px_x)
        roi_area = roi_h * roi_w
    pixel_size = float(px_x) if px_x and px_y and math.isclose(float(px_x), float(px_y)) else None
    return RunData(run_dir, manifest, ca, version, store_path, run_type, pixel_area, pixel_size,
                   roi_area, roi_w, roi_h)


def load_product_config(path: Path | None) -> dict | None:
    if path is None:
        return None
    if not path.is_file():
        raise InputError(f"제품 manifest가 없습니다: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise InputError(f"제품 manifest를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"제품 manifest 형식이 올바르지 않습니다: {path}")
    return data


def load_mapping(path: Path | None) -> dict[tuple[str, str], UnitMapping]:
    """기본 대응표에 --mapping YAML(항목 목록)을 덮어쓴다."""
    mapping = dict(FAILURE_MAPPING)
    if path is None:
        return mapping
    if not path.is_file():
        raise InputError(f"대응표 파일이 없습니다: {path}")
    try:
        entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except (yaml.YAMLError, OSError) as exc:
        raise InputError(f"대응표를 읽을 수 없습니다: {exc}") from exc
    if isinstance(entries, dict):
        entries = entries.get("mappings", [])
    if not isinstance(entries, list):
        raise InputError("대응표는 항목 목록이어야 합니다")
    for entry in entries:
        try:
            key = (str(entry["unit_kind"]), str(entry["failure_mode"]))
        except (KeyError, TypeError) as exc:
            raise InputError(f"대응표 항목에 unit_kind와 failure_mode가 필요합니다: {entry!r}") from exc
        target = entry.get("failure_type")
        mapping[key] = UnitMapping(None if target in (None, "") else str(target),
                                   str(entry.get("note", "--mapping으로 지정")))
    return mapping


def open_store(path: Path | None):
    """zarr 저장소를 연다. zarr가 없거나 저장소가 없으면 None."""
    if path is None:
        return None
    try:
        import zarr
    except ImportError:
        return None
    return zarr.open_group(str(path), mode="r")


# ---------------------------------------------------------------- 1. 무결성

def check_schema(run: RunData, report: RunReport, store) -> None:
    if run.schema_version not in SUPPORTED_SCHEMAS:
        report.add("error", "integrity", "schema_version",
                   f"지원하지 않는 schema_version {run.schema_version!r} (지원: {', '.join(SUPPORTED_SCHEMAS)})")
    missing = [k for k in MANIFEST_KEYS if k not in run.manifest]
    if missing:
        report.add("error", "integrity", "manifest_keys", f"run_manifest.json 필수 키 누락: {missing}")
    required = BASE_COLUMNS + (V2_COLUMNS if run.schema_version == "2.0" else ())
    missing_cols = [c for c in required if c not in run.ca.columns]
    if missing_cols:
        report.add("error", "integrity", "ca_columns", f"ca.parquet 필수 열 누락: {missing_cols}")
    if "schema_version" in run.ca.columns:
        table_versions = sorted({str(v) for v in run.ca["schema_version"].dropna().unique()})
        if table_versions and table_versions != [run.schema_version]:
            report.add("error", "integrity", "schema_mismatch",
                       f"ca.parquet schema_version {table_versions}가 run_manifest {run.schema_version!r}와 다릅니다")
    if run.store_path is None:
        report.add("warning", "integrity", "store_missing",
                   "hotspots.zarr/probability.zarr가 없어 면적 대조와 hotspot 이미지를 생략합니다")
    elif store is None:
        report.add("warning", "integrity", "zarr_unavailable",
                   "zarr를 불러올 수 없어 면적 대조와 hotspot 이미지를 생략합니다")


def check_keys(run: RunData, report: RunReport, store) -> None:
    ca = run.ca
    if "unit_id" not in ca.columns:
        return
    key = ["source_step", "particle_size_um", "unit_id"]
    dup = ca[ca.duplicated(key, keep=False)]
    if not dup.empty:
        report.add("error", "integrity", "duplicate_rows",
                   f"(source_step, particle_size_um, unit) 중복 행 {len(dup)}개")
    sizes_manifest = sorted(float(s) for s in run.manifest.get("particle_sizes_um", []))
    sizes_table = sorted(float(s) for s in ca["particle_size_um"].unique())
    if sizes_manifest and sizes_table != sizes_manifest:
        report.add("error", "integrity", "sizes_mismatch",
                   f"ca.parquet 이물 크기 {sizes_table}가 run_manifest {sizes_manifest}와 다릅니다")
    steps_manifest = set(run.manifest.get("source_steps", []))
    extra_steps = sorted(set(ca["source_step"]) - steps_manifest) if steps_manifest else []
    if extra_steps:
        report.add("error", "integrity", "steps_mismatch",
                   f"run_manifest에 없는 source_step: {extra_steps}")
    expected = len(set(ca["particle_size_um"]))
    for (step, unit), group in ca.groupby(["source_step", "unit_id"], sort=False):
        if len(group) != expected:
            report.add("warning", "integrity", "incomplete_sizes",
                       f"{step}: 이물 크기 행이 {len(group)}/{expected}개뿐입니다", unit)
    identity = [c for c in ("unit_kind", "failure_mode", "electrode_a", "electrode_b",
                            "electrode_a_layer", "electrode_b_layer") if c in ca.columns]
    for unit, group in ca.groupby("unit_id", sort=False):
        varying = [c for c in identity if group[c].nunique(dropna=False) > 1]
        if varying:
            report.add("error", "integrity", "unit_identity",
                       f"같은 unit ID에서 {varying} 값이 행마다 다릅니다(서로 다른 unit이 합쳐졌을 수 있음)", unit)


def check_values(run: RunData, report: RunReport, store) -> None:
    ca = run.ca
    for column, upper in (("ca_nominal_um2", None), ("ca_nominal_normalized", 1.0)):
        if column not in ca.columns:
            continue
        values = pd.to_numeric(ca[column], errors="coerce").to_numpy(float)
        bad = ~np.isfinite(values) | (values < 0)
        if upper is not None:
            bad |= values > upper
        if bad.any():
            limit = "0 이상 1 이하" if upper else "0 이상"
            report.add("error", "integrity", "value_range",
                       f"{column}: {int(bad.sum())}개 행이 {limit}의 유한값이 아닙니다")
    if run.roi_area_um2 and {"ca_nominal_um2", "ca_nominal_normalized"} <= set(ca.columns):
        expected = ca["ca_nominal_um2"].astype(float) / run.roi_area_um2
        diff = np.abs(expected - ca["ca_nominal_normalized"].astype(float))
        if (diff > 1e-9 + 1e-9 * np.abs(expected)).any():
            report.add("error", "integrity", "normalized_mismatch",
                       f"ca_nominal_normalized가 ca_nominal_um2 / ROI 면적({run.roi_area_um2:g} um^2)과 "
                       f"다른 행 {int((diff > 1e-9).sum())}개")
    elif not run.roi_area_um2:
        report.add("info", "integrity", "roi_unknown",
                   "run_manifest에 pixel 크기·shape_yx가 없어(schema 1.0 등) 면적 정규화 대조를 생략합니다")
    quantiles = ["ca_p05_um2", "ca_p50_um2", "ca_p95_um2"]
    if run.run_type == "probability" and set(quantiles) <= set(ca.columns):
        q = ca[quantiles].astype(float).to_numpy()
        ok = np.isfinite(q).all(axis=1)
        disorder = ok & ((q[:, 0] > q[:, 1] + 1e-9) | (q[:, 1] > q[:, 2] + 1e-9))
        if disorder.any():
            report.add("error", "integrity", "quantile_order",
                       f"p05 <= p50 <= p95가 아닌 행 {int(disorder.sum())}개")


def check_unit_ids(run: RunData, report: RunReport, store) -> None:
    ca = run.ca
    if "unit_id" not in ca.columns:
        return
    units = ca.drop_duplicates("unit_id")
    electrodes = set(units["electrode_a"]) | set(units["electrode_b"])
    risky = sorted(e for e in electrodes if isinstance(e, str) and "__" in e)
    for row in units.itertuples(index=False):
        uid, kind = str(row.unit_id), str(row.unit_kind)
        if kind == "contact":
            rest = uid[len(CNO_PREFIX):] if uid.startswith(CNO_PREFIX) else None
            contact, _, link = (rest or "").partition(":")
            if rest is None or not contact or link != f"{row.electrode_a}__{row.electrode_b}":
                report.add("error", "integrity", "cno_id",
                           f"CNO unit ID가 'cno:<contact>:{row.electrode_a}__{row.electrode_b}' 형식이 아닙니다", uid)
        elif kind == "open_electrode" and uid != f"{AUTO_OPEN_PREFIX}{row.electrode_a}":
            report.add("error", "integrity", "auto_open_id",
                       f"자동 open unit ID가 '{AUTO_OPEN_PREFIX}{row.electrode_a}'가 아닙니다", uid)
    if risky and (units["unit_kind"] == "contact").any():
        report.add("warning", "integrity", "electrode_id_separator",
                   f"전극 ID에 '__'가 있습니다 {risky}. particle-sim 3290d3e 이전 결과라면 CNO 링크가 "
                   "잘못 합쳐졌을 수 있습니다")


def check_store(run: RunData, report: RunReport, store) -> None:
    """저장소 축 이름·순서가 CA 표와 같고, 픽셀 합 × 픽셀 면적이 CA와 같은지 본다."""
    if store is None:
        return
    attrs = dict(store.attrs)
    ids = attrs.get("failure_unit_ids") or attrs.get("unit_ids") or attrs.get("pair_ids")
    steps = list(attrs.get("source_steps", []))
    sizes = [float(s) for s in attrs.get("particle_sizes_um", [])]
    if not ids or not steps or not sizes:
        report.add("error", "integrity", "store_attrs", f"{run.store_path.name}: unit·공정·크기 축 정보가 없습니다")
        return
    if set(steps) != set(run.ca["source_step"]) or set(sizes) != set(run.ca["particle_size_um"]):
        report.add("error", "integrity", "store_axes", "저장소 공정·크기 축이 CA 표와 다릅니다")
        return
    table_units = list(dict.fromkeys(run.ca["unit_id"]))
    if sorted(table_units) != sorted(ids):
        report.add("error", "integrity", "store_units",
                   f"{run.store_path.name}의 unit {sorted(ids)}가 ca.parquet {sorted(table_units)}와 다릅니다")
        return
    array_name, ca_column = (("nominal_failure", "ca_nominal_um2") if run.run_type == "nominal"
                             else ("probability", "ca_mean_um2"))
    if array_name not in store or ca_column not in run.ca.columns:
        report.add("error", "integrity", "store_array", f"{run.store_path.name}에 {array_name} 배열이 없습니다")
        return
    array = store[array_name]
    if array.ndim != 5 or array.shape[:3] != (len(steps), len(sizes), len(ids)):
        report.add("error", "integrity", "store_shape",
                   f"{array_name} shape {array.shape}가 축 길이({len(steps)}, {len(sizes)}, {len(ids)})와 맞지 않습니다")
        return
    shape = run.manifest.get("shape_yx")
    if shape and tuple(array.shape[3:]) != tuple(shape):
        report.add("error", "integrity", "store_yx",
                   f"{array_name} 공간 크기 {array.shape[3:]}가 run_manifest shape_yx {shape}와 다릅니다")
    if not run.pixel_area_um2:
        return
    rel_tol = 1e-6 if run.run_type == "nominal" else 1e-4  # probability는 float32 저장
    mismatches = 0
    for row in run.ca.itertuples(index=False):
        value = getattr(row, ca_column)
        if value is None or not np.isfinite(value):
            continue
        plane = array[steps.index(row.source_step), sizes.index(float(row.particle_size_um)), ids.index(row.unit_id)]
        area = float(np.asarray(plane, dtype=np.float64).sum()) * run.pixel_area_um2
        if not math.isclose(area, float(value), rel_tol=rel_tol, abs_tol=1e-6):
            mismatches += 1
            if mismatches <= 5:
                report.add("error", "integrity", "store_area",
                           f"{row.source_step} {row.particle_size_um:g}um: {array_name} 합 × 픽셀 면적 {area:g} "
                           f"≠ {ca_column} {float(value):g}", str(row.unit_id))
    if mismatches > 5:
        report.add("error", "integrity", "store_area", f"면적 불일치 행이 총 {mismatches}개입니다")


INTEGRITY_CHECKS = (check_schema, check_keys, check_values, check_unit_ids, check_store)


# ---------------------------------------------------------------- 2·3. 대응과 계약

def map_units(run: RunData, report: RunReport, mapping: dict) -> dict[str, UnitMapping]:
    mapped: dict[str, UnitMapping] = {}
    if "unit_id" not in run.ca.columns:
        return mapped
    for row in run.ca.drop_duplicates("unit_id").itertuples(index=False):
        key = (str(row.unit_kind), str(row.failure_mode))
        rule = mapping.get(key)
        entry = {"unit_id": str(row.unit_id), "unit_kind": key[0], "failure_mode": key[1],
                 "electrode_a": str(row.electrode_a), "electrode_b": str(row.electrode_b)}
        if rule is None:
            entry.update(status="unsupported", failure_type=None, note="대응 규칙 없음")
            report.add("warning", "mapping", "no_rule",
                       f"(unit_kind={key[0]}, failure_mode={key[1]})에 대한 대응 규칙이 없습니다", entry["unit_id"])
        elif rule.failure_type is None:
            entry.update(status="unsupported", failure_type=None, note=rule.note)
            report.add("warning", "mapping", "unsupported", rule.note, entry["unit_id"])
        else:
            entry.update(status="mapped", failure_type=rule.failure_type, note=rule.note)
            mapped[entry["unit_id"]] = rule
        report.units.append(entry)
    return mapped


def _product_meta(run: RunData, config: dict | None, args, report: RunReport) -> dict:
    manifest = run.manifest
    meta = {"product_id": str(manifest.get("product_id", "")),
            "design_version": str(manifest.get("design_version", ""))}
    electrodes: dict[str, dict] = {}
    if config is not None:
        for key in ("product_id", "design_version"):
            if str(config.get(key, "")) != meta[key]:
                report.add("error", "contract", "config_mismatch",
                           f"제품 manifest {key} {config.get(key)!r}가 결과 {meta[key]!r}와 다릅니다")
        electrodes = {str(e.get("electrode_id")): e for e in config.get("electrodes", []) or []}
    for key in ("product_family", "design_lineage_id", "fab_id"):
        value = getattr(args, key) or (config or {}).get(key)
        if not value:
            value = "UNKNOWN"
            report.add("warning", "contract", "placeholder",
                       f"{key}가 없어 'UNKNOWN'으로 둡니다. --product-config 또는 --{key.replace('_', '-')}를 주세요")
        meta[key] = str(value)
    meta["is_synthetic"] = bool((config or {}).get("is_synthetic", False))
    if not config:
        report.add("warning", "contract", "no_roles",
                   "--product-config가 없어 전극 역할(electrode_role) 대신 전극 ID를 씁니다")
    meta["electrodes"] = electrodes
    meta["stack"] = {}
    if config is not None:
        stack = config.get("stack")
        if not isinstance(stack, list) or not stack or any(not isinstance(x, dict) for x in stack):
            report.add("error", "contract", "config_stack", "stack must be a nonempty list of layers")
        else:
            names = [x.get("layer_name") for x in stack]
            orders = [x.get("process_order") for x in stack]
            if (any(not isinstance(x, str) or not x.strip() for x in names)
                    or any(type(x) is not int for x in orders)):
                report.add("error", "contract", "config_stack", "stack requires layer names and integer process_order")
            elif len(set(names)) != len(names) or len(set(orders)) != len(orders):
                report.add("error", "contract", "config_stack", "stack layer names and process_order must be unique")
            else:
                meta["stack"] = dict(zip(names, orders))
    return meta


def _sample_id(meta: dict, step: str, unit: str) -> str:
    return SAMPLE_ID_ALLOWED.sub("_", f"{meta['product_id']}-{meta['design_version']}-{step}-{unit}")


def build_bundles(run: RunData, report: RunReport, mapped: dict[str, UnitMapping], store,
                  config: dict | None, args,
                  seen_ids: dict[str, tuple[str, tuple[str, str, str, str]]]) -> list[schema.AnalysisBundle]:
    if not mapped and config is None:
        return []
    meta = _product_meta(run, config, args, report)
    if not mapped or report.count("error"):
        return []
    version = getattr(args, "mapping_contract_version", "1.2")
    modern = version == "1.2"
    ca = run.ca
    pair_registry = {}
    if modern:
        steps = (config or {}).get("particle_source_steps", [])
        step_map = {}
        for source in steps:
            if not isinstance(source, dict) or source.get("step_id") in step_map:
                report.add("error", "contract", "source_steps", "duplicate or invalid configured source step")
                return []
            step_map[source.get("step_id")] = source.get("fab_distribution_layer")
        for step, rows in ca.groupby("source_step", sort=False):
            labels = rows.get("fab_distribution_layer")
            if labels is None or not labels.map(lambda x: isinstance(x, str) and bool(x.strip()) and x == x.strip()).all() or labels.nunique() != 1:
                report.add("error", "contract", "source_fab_layer", f"{step!r}: nonblank uniform source Fab label required; for legacy runs whose CA lacks source Fab labels, use --mapping-contract-version 1.1")
            elif config is not None and (step not in step_map or step_map[step] != labels.iloc[0]):
                report.add("error", "contract", "source_fab_layer", f"{step!r}: CA source Fab label disagrees with configured source step")
        if report.count("error"):
            return []
        if config is None:
            report.add("warning", "contract", "geometry-config-unverified", "No product config: geometry/config conformance is unverified")
    use_mean = args.ca == "mean"
    if use_mean and ("ca_mean_um2" not in ca.columns or ca["ca_mean_um2"].isna().all()):
        report.add("error", "contract", "ca_mean_missing", "--ca mean이지만 ca_mean_um2 값이 없습니다(nominal run?)")
        return []
    if use_mean and not run.roi_area_um2:
        report.add("error", "contract", "ca_mean_roi", "--ca mean에는 ROI 면적(pixel 크기·shape_yx)이 필요합니다")
        return []
    attrs = dict(store.attrs) if store is not None else {}
    store_ids = attrs.get("failure_unit_ids") or attrs.get("unit_ids") or attrs.get("pair_ids") or []
    store_steps = list(attrs.get("source_steps", []))
    store_sizes = [float(s) for s in attrs.get("particle_sizes_um", [])]
    array_name = "nominal_failure" if run.run_type == "nominal" else "probability"
    array = store[array_name] if store is not None and array_name in store else None
    semantics = "binary" if run.run_type == "nominal" else "probability"
    bundles: list[schema.AnalysisBundle] = []
    skipped = 0
    for (step, unit), rows in ca[ca["unit_id"].isin(mapped)].groupby(["source_step", "unit_id"], sort=False):
        rule = mapped[unit]
        classes = rows["classification"] if "classification" in rows.columns else pd.Series(dtype=str)
        not_applicable = (classes == NOT_APPLICABLE_STEP).to_numpy(bool) if len(classes) else np.zeros(len(rows), bool)
        if len(rows) and not_applicable.all():
            skipped += 1
            continue
        if len(classes) and classes.nunique(dropna=False) != 1:
            report.add("error", "contract", "mixed_applicability",
                       f"{step}: classification이 크기마다 다릅니다", unit)
            continue
        first = rows.iloc[0]
        sid = _sample_id(meta, step, unit)
        raw_tuple = (meta["product_id"], meta["design_version"], str(step), str(unit))
        owner = seen_ids.get(sid)
        if owner is not None and owner[1] != raw_tuple:
            # 서로 다른 전체 raw tuple이 같은 sample_id가 됐다.
            report.add("error", "contract", "sample_id_collision",
                       f"sample_id {sid}: 서로 다른 raw tuple {owner[1]!r} 및 {raw_tuple!r}가 겹칩니다", unit)
            continue
        if owner is not None and owner[0] != str(run.run_dir):
            report.add("warning", "contract", "sample_id_across_runs",
                       f"sample_id {sid}가 다른 run({owner[0]})에도 있습니다. 한 simcompare 자료에는 "
                       "같은 제품·설계 버전의 run을 하나만 넣어야 합니다", unit)
        seen_ids.setdefault(sid, (str(run.run_dir), raw_tuple))
        is_open = rule.failure_type in schema.OPEN_TYPES

        if is_open and len(classes) and classes.iloc[0] in EXCLUDED_OPEN_CLASSIFICATIONS:
            report.add("warning", "contract", "excluded_open",
                       f"{unit!r}: step={step!r}, classification={classes.iloc[0]}; "
                       "CA 0 reflects an excluded baseline condition, not low risk", unit)

        def role_layer(electrode: str, layer_col: str) -> tuple[str, str]:
            info = meta["electrodes"].get(electrode, {})
            role = str(info.get("electrode_role") or electrode)
            layer = first.get(layer_col) if layer_col in rows.columns else None
            if not layer or layer == NOT_APPLICABLE:
                layer = info.get("layer_name")
            if not layer:
                report.add("warning", "contract", "layer_unknown",
                           f"전극 {electrode}의 layer를 알 수 없어 'UNKNOWN'으로 둡니다", unit)
                layer = "UNKNOWN"
            return role, str(layer)

        role_a, layer_a = role_layer(str(first["electrode_a"]), "electrode_a_layer")
        if is_open:
            role_b, layer_b = NOT_APPLICABLE, NOT_APPLICABLE
        else:
            role_b, layer_b = role_layer(str(first["electrode_b"]), "electrode_b_layer")
        # Match exported layer_stack.csv: stack_order=process_order, fab_process_layer=layer_name.
        vertical = rule.failure_type == "vertical_short"
        assigned = None if vertical and not modern else (str(first.get("fab_distribution_layer") or "") or None)
        if vertical and config is not None:
            declared = []
            valid = True
            for slot in ("a", "b"):
                electrode = str(first[f"electrode_{slot}"])
                info = meta["electrodes"].get(electrode)
                layer = info.get("layer_name") if info else None
                if not isinstance(layer, str) or layer not in meta["stack"]:
                    report.add("error", "contract", "config_electrode_layer",
                               f"electrode {electrode!r}: declared layer {layer!r} is absent from stack", unit)
                    valid = False
                    continue
                col = f"electrode_{slot}_layer"
                if col in rows and not rows[col].eq(layer).all():
                    report.add("error", "contract", "config_ca_layer",
                               f"electrode {electrode!r}: CA {col} disagrees with config layer {layer!r}", unit)
                    valid = False
                declared.append(layer)
            if not valid:
                continue
            layer_a, layer_b = declared
            if meta["stack"][layer_a] == meta["stack"][layer_b]:
                report.add("error", "contract", "config_layer_tie",
                           f"layers {layer_a!r}, {layer_b!r} have equal process_order", unit)
                continue
            upper = max(declared, key=meta["stack"].get)
            if not modern:
                assigned = upper
            report.add("info", "contract", "vertical_layer_verified",
                       f"{sid}: ({layer_a}, {layer_b})->upper physical layer {upper}; assigned Fab layer={assigned}; validation mode={args.mode}", unit)
            source_layer = first.get("fab_distribution_layer")
            if not modern and source_layer and source_layer != assigned:
                report.add("warning", "contract", "vertical_source_layer",
                           f"source step {step!r} (fab layer {source_layer!r}) differs from "
                           f"upper electrode layer {assigned!r}; "
                           "these have different semantics", unit)
        elif vertical:
            report.add("warning", "contract", "vertical-layer-unverified",
                       f"{sid}: no --product-config; vertical_short layer_stack.csv upper layer is unverified", unit)

        pair_key = None
        if modern:
            try:
                pair_key = schema.make_fab_pair_key(str(unit), layer_a, layer_b, pair_registry)
            except schema.DataContractError as exc:
                report.add("error", "contract", "fab_pair_key", str(exc), unit)
                continue
        records = []
        for row, na in zip(rows.itertuples(index=False), not_applicable):
            value = None
            if not na:
                raw = row.ca_mean_um2 if use_mean else row.ca_nominal_um2
                value = None if raw is None or not np.isfinite(raw) else float(raw)
            records.append({"sample_id": sid, "particle_size": float(row.particle_size_um),
                            "particle_size_unit": "um", "ca_value": value, "ca_unit": "um^2"})
        try:
            curve = schema.group_size_records(records)[sid]
        except schema.DataContractError as exc:
            report.add("error", "contract", "ca_curve", str(exc), unit)
            continue
        curve.calc_settings = {"ca_source": "ca_mean_um2" if use_mean else "ca_nominal_um2",
                               "run_type": run.run_type}

        hotspots = []
        if array is not None and unit in store_ids and step in store_steps:
            for size in store_sizes:
                plane = np.asarray(array[store_steps.index(step), store_sizes.index(size), store_ids.index(unit)],
                                   dtype=np.float32)
                hotspots.append(schema.ImageInput(
                    role=f"hotspot@{size:g}um", array=plane, path=str(run.store_path),
                    pixel_size=run.pixel_size_um, origin_xy=(0.0, 0.0), particle_size=size,
                    particle_size_unit="um", value_semantics=semantics,
                    legend_id=PROBABILITY_LEGEND_ID if semantics == "probability" else None,
                ))
        bundle = schema.AnalysisBundle(
            sample_id=sid, product_id=meta["product_id"], product_family=meta["product_family"],
            design_version=meta["design_version"], design_lineage_id=meta["design_lineage_id"],
            electrode_a_role=role_a, electrode_b_role=role_b, layer_a=layer_a, layer_b=layer_b,
            failure_type=rule.failure_type, fab_id=meta["fab_id"], assigned_process_layer=assigned,
            fab_pair_key=pair_key, mapping_contract_version=version,
            unit_width=run.roi_width_um, unit_height=run.roi_height_um,
            physical_length_unit="um" if run.roi_width_um else None,
            hotspots=hotspots, ca=curve,
            provenance=schema.Provenance(
                adapter="particle_sim_run",
                source_files=[str(run.run_dir / "ca.parquet")] + ([str(run.store_path)] if run.store_path else []),
                calc_settings={k: run.manifest.get(k) for k in (
                    "run_id", "config_hash", "assumption_set", "legacy_kernel", "post_step_policy")}
                | {"failure_unit_id": unit, "unit_kind": str(first["unit_kind"])},
                calc_version=f"particle-sim-{run.manifest.get('simulator_version')}",
                is_synthetic=meta["is_synthetic"],
            ),
        )
        mode = args.mode
        if vertical and config is None and mode == "real" and not modern:
            # real 모드의 assigned_process_layer 필수 검사만 simcompare 적층표 단계로 넘긴다.
            mode = "demo"
        try:
            warnings = schema.validate_bundle(bundle, mode=mode)
        except schema.DataContractError as exc:
            report.add("error", "contract", "validate_bundle", str(exc), unit)
            continue
        for message in warnings:
            if vertical and "assigned_process_layer" in message:
                continue  # The named vertical-layer-unverified finding already explains this gap.
            if "layout 이미지 누락" in message:
                continue  # layout은 particle-sim 입력(mask)이라 결과 디렉터리에 없다. 아래 info로 한 번만 알린다.
            report.add("warning", "contract", "bundle_warning", message, unit)
        bundles.append(bundle)
        report.bundles.append(sid)
    if bundles:
        report.add("info", "contract", "layouts_absent",
                   "layout 이미지는 particle-sim 입력 mask라 결과 디렉터리에 없습니다. 비교 분석에는 별도 연결이 필요합니다")
    if skipped:
        report.add("info", "contract", "not_applicable_skipped",
                   f"모든 크기가 {NOT_APPLICABLE_STEP}인 (공정, unit) {skipped}개는 묶음을 만들지 않았습니다")
    return bundles


# ---------------------------------------------------------------- 실행

def check_run(run_dir: Path, args, mapping, config, seen_ids) -> RunReport:
    report = RunReport(run_dir=str(run_dir))
    try:
        run = load_run(run_dir)
    except InputError as exc:
        report.add("error", "input", "load", str(exc))
        return report
    report.schema_version, report.run_id, report.run_type = run.schema_version, run.manifest.get("run_id"), run.run_type
    try:
        store = open_store(run.store_path)
    except (ValueError, OSError) as exc:
        report.add("error", "input", "store_load", str(exc))
        return report
    check_schema(run, report, store)
    if report.count("error", "integrity"):
        return report
    for check in INTEGRITY_CHECKS[1:]:
        check(run, report, store)
    if report.count("error", "integrity"):
        return report
    for key, rule in mapping.items():
        if rule.failure_type is not None and rule.failure_type not in schema.FAILURE_TYPES:
            report.add("error", "contract", "mapping_target", f"지원하지 않는 mapping target: {key} -> {rule.failure_type}")
    if report.count("error", "contract"):
        return report
    mapped = map_units(run, report, mapping)
    build_bundles(run, report, mapped, store, config, args, seen_ids)
    return report


def print_summary(report: RunReport) -> None:
    unsupported = Counter(u["failure_mode"] for u in report.units if u["status"] == "unsupported")
    mapped = sum(1 for u in report.units if u["status"] == "mapped")
    print(f"[{report.run_dir}] schema {report.schema_version} · {report.run_type} · run_id {report.run_id}")
    for stage, label in (("input", "입력"), ("integrity", "무결성"), ("mapping", "unit 대응"), ("contract", "계약")):
        errors, warns = report.count("error", stage), report.count("warning", stage)
        if stage == "mapping":
            detail = ", ".join(f"{k} {v}" for k, v in unsupported.items()) or "없음"
            print(f"  {label}: unit {len(report.units)}개 중 대응 {mapped}, 미지원 {sum(unsupported.values())} ({detail})")
        elif stage == "contract":
            print(f"  {label}: 묶음 {len(report.bundles)}개, 오류 {errors}, 경고 {warns}")
        elif errors or warns or stage == "integrity":
            print(f"  {label}: 오류 {errors}, 경고 {warns}")
    for finding in report.findings:
        if finding.level == "error":
            where = f" [{finding.unit}]" if finding.unit else ""
            print(f"    오류 {finding.stage}/{finding.code}{where}: {finding.message}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="particle-sim 결과를 simcompare 계약으로 점검한다(읽기 전용)")
    parser.add_argument("runs", nargs="+", type=Path, help="particle-sim 결과 디렉터리(여러 개 가능)")
    parser.add_argument("--product-config", type=Path, help="particle-sim 제품 manifest YAML(역할·제품군·Fab)")
    parser.add_argument("--product-family")
    parser.add_argument("--design-lineage-id")
    parser.add_argument("--fab-id")
    parser.add_argument("--mode", choices=schema.MODES, default="real", help="simcompare 검사 모드(기본 real)")
    parser.add_argument("--mapping-contract-version", choices=("1.1", "1.2"), default="1.2",
                        help="G4 interpretation (default 1.2 source-step Fab assignment; 1.1 legacy upper layer)")
    parser.add_argument("--ca", choices=("nominal", "mean"), default="nominal",
                        help="CA 곡선 값: nominal=ca_nominal_um2, mean=ca_mean_um2(확률 run)")
    parser.add_argument("--mapping", type=Path, help="unit 대응표 YAML(기본 대응표에 덮어씀)")
    parser.add_argument("--output", type=Path, help="JSON 보고서 경로")
    parser.add_argument("--strict", action="store_true", help="미지원 unit이 있으면 실패로 본다")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        mapping = load_mapping(args.mapping)
        config = load_product_config(args.product_config)
    except InputError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    seen_ids: dict[str, tuple[str, tuple[str, str, str, str]]] = {}
    reports = [check_run(run_dir, args, mapping, config, seen_ids) for run_dir in args.runs]
    for report in reports:
        print_summary(report)
    input_failed = any(r.count("error", "input") for r in reports)
    errors = sum(r.count("error") for r in reports)
    unsupported = sum(1 for r in reports for u in r.units if u["status"] == "unsupported")
    passed = errors == 0 and not (args.strict and unsupported)
    verdict = "통과" if passed else "실패"
    print(f"판정: {verdict} (오류 {errors}, 미지원 unit {unsupported}{', --strict' if args.strict else ''})")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "verdict": "pass" if passed else "fail",
            "strict": args.strict,
            "mode": args.mode,
            "ca": args.ca,
            "mapping_contract_version": args.mapping_contract_version,
            "simcompare_failure_types": list(schema.FAILURE_TYPES),
            "mapping": [{"unit_kind": k[0], "failure_mode": k[1], **asdict(v)} for k, v in mapping.items()],
            "runs": [asdict(r) for r in reports],
        }
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"보고서: {args.output}")
    if input_failed:
        return 2
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
