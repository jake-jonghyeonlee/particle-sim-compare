"""생산자 원본을 typed 공간 CA 원천으로 연결하고 명시 manifest를 읽는다.

ROI 계산, 이미지 보정, 등록 추론은 하지 않는다. 생산자 승격 조건은
particle_sim_sources, 외부 JSON 형식/경로 정책은 load_source_manifest를 수정한다.
일반 hotspot 밝기나 전역 CA를 분할해서 density를 만들지 않는다.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from numbers import Real
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from .. import schema
from ..regional_measurement import diameter_key, frame_geometry_um, geometry_um, validate_source
from ..regional_schema import SpatialCASource, SpatialFrame

# ---------------------------------------------------------------------------
# 공통 scalar 연결과 추적 정보 (bundle은 변경하지 않는다)
# ---------------------------------------------------------------------------
NOMINAL_EXPORT_SEMANTICS = "nominal binary geometric contact risk"
NOMINAL_BINARY_ENCODING = "8-bit grayscale PNG, 0/255, compression level 9"


def scalar_observation(
    bundle: schema.AnalysisBundle | None, diameter_um: float,
) -> tuple[float | None, str | None, float | None, str | None]:
    """um 지름 키의 원 scalar/단위/um² 값/결측 사유를 반환한다. IO/변경은 없다.

    원점/배열을 사용하지 않는다. 관측 누락은 null, legacy는 원 값만 남긴다.
    어댑터와 비교기가 같은 키/결측 정책을 사용해 단위 표현 차이를 중복으로 만들지
    않는다.
    """
    if bundle is None:
        return None, None, None, "missing_side"
    ca = bundle.ca
    if ca is None:
        return None, None, None, "missing_total_ca"
    keys = [diameter_key(v, ca.particle_size_unit) for v in ca.particle_sizes]
    indexes = [i for i, key in enumerate(keys) if key == diameter_key(diameter_um)]
    if len(indexes) != 1:
        return None, ca.unit, None, "missing_size"
    index = indexes[0]
    if not ca.observed[index] or not np.isfinite(ca.values[index]):
        return None, ca.unit, None, "missing_total_ca"
    raw = float(ca.values[index])
    try:
        area = float(schema.convert_area(raw, ca.unit))
    except schema.DataContractError:
        return raw, ca.unit, None, "legacy_ca_not_area"
    return raw, ca.unit, area, None


def source_for_diameter(bundle: schema.AnalysisBundle, diameter_um: float) -> tuple[SpatialCASource | None, str | None]:
    """um 지름의 typed 원천을 exact match로 찾는다. 보간/IO/입력 변경은 없다.

    없으면 missing_size/source 또는 legacy_ca_not_area를 반환한다. 원래 scalar CA는
    bundle에 그대로 남고 이 helper가 0을 만들지 않는다. 배열 좌표는 source.frame이다.
    """
    matches = [source for source in bundle.ca_spatial_sources
               if diameter_key(source.diameter_um) == diameter_key(diameter_um)]
    if len(matches) > 1:
        raise schema.DataContractError("duplicate regional source diameter key")
    if matches:
        source = matches[0]
        return source, source.validation.reason if source.validation else source.unavailable_reason
    _, unit, _, reason = scalar_observation(bundle, diameter_um)
    if unit is not None and schema.is_dimensionless_unit(unit):
        return None, "legacy_ca_not_area"
    return None, reason or "missing_source"


def source_frame_reason(bundle: schema.AnalysisBundle, source: SpatialCASource, atol_um: float) -> str | None:
    """동일 sample/지름 hotspot과 원천의 [y,x] shape·좌상단·등방 pitch를 대조한다.

    비교 단위는 um이다. ImageInput에는 frame_id 선언이 없으므로 이름은 provenance로만
    보존한다. 원천 격자 오류와 이미지 기하 불일치는 구분하여 사유로 반환한다.
    없는 이미지는 추론하지 않으며 IO/입력 변경은 없다.
    """
    try:
        actual = frame_geometry_um(source)
    except ValueError as exc:
        return str(exc)
    for image in bundle.hotspots:
        if image.particle_size is None or not image.particle_size_unit or image.array is None:
            continue
        if diameter_key(image.particle_size, image.particle_size_unit) != diameter_key(source.diameter_um):
            continue
        try:
            expected = geometry_um(SpatialFrame(image.pixel_size, image.origin_xy, bundle.physical_length_unit))
        except ValueError:
            return "incompatible_source_frame"
        # 보존 합이 같아도 위치가 다르면 다른 픽셀을 적분한다. 원천을 이동시켜
        # 맞추지 않는다.
        if source.density is None or image.array.shape != source.density.shape:
            return "incompatible_source_frame"
        if any(abs(a - b) > atol_um for a, b in zip(actual, expected)):
            return "incompatible_source_frame"
    return None


def _with_validation(source: SpatialCASource) -> SpatialCASource:
    return replace(source, validation=validate_source(source))


# ---------------------------------------------------------------------------
# particle-sim 직접 run / lossless nominal export 승격
# ---------------------------------------------------------------------------


def particle_sim_sources(
    bundle: schema.AnalysisBundle, *, map_mode: str, ca_mode: str,
    source_step: str | None, unit_id: str | None,
    data_dir: Path | None = None, array_storage: dict | None = None,
    grid_pitch_xy: tuple[float, float] | None = None,
) -> list[SpatialCASource]:
    """검증된 particle-sim 원본 2D plane을 nominal/mean CA와 연결한다.

    직접 run은 producer plane을, export는 선언된 lossless PNG/NPY를 사용한다.
    pitch/origin은 이미지의 길이 단위(직접 run um, export bundle 단위)다. source_step,
    unit_id, version이 없으면 승격하지 않는다. 모드 mismatch는 원천 reason에 보존한다.
    grid_pitch_xy는 직접 producer의 um pitch이며 anisotropy를 숨기지 않고 보존한다.
    배열을 수정하지 않으며 data_dir이 있을 때만 원본 파일 읽기/해시 IO가 있다.
    """
    version = bundle.provenance.calc_version
    if not isinstance(version, str) or not version.startswith("particle-sim-") or not source_step or not unit_id:
        return []
    if map_mode not in ("nominal", "mean") or ca_mode not in ("nominal", "mean"):
        return []
    settings = copy.deepcopy(bundle.provenance.calc_settings)
    if not isinstance(settings, dict):
        raise schema.DataContractError("regional producer calc_settings must be a mapping")
    settings.update(source_step=source_step, failure_unit_id=unit_id, ca_mode=ca_mode,
                    ca_source=f"ca_{ca_mode}_um2", failure_type=bundle.failure_type)
    results = []
    for image in bundle.hotspots:
        if image.particle_size is None or not image.particle_size_unit:
            continue
        diameter = diameter_key(image.particle_size, image.particle_size_unit)
        total, unit, _, reason = scalar_observation(bundle, diameter)
        unit = unit or "um^2"
        raw = image.array
        files = {}
        if data_dir is not None:
            storage = array_storage or {}
            if not isinstance(storage, dict) or not isinstance(storage.get("formats", {}), dict):
                raise schema.DataContractError("regional array_storage/formats must be mappings")
            format = storage.get("formats", {}).get(image.path)
            if storage.get("format") != "per-array" or format not in ("png", "npy"):
                continue
            path = _relative_file(data_dir, image.path)
            if format == "png":
                if storage.get("binary_encoding") != NOMINAL_BINARY_ENCODING:
                    continue
                with Image.open(path) as png:
                    if png.mode != "L":
                        continue
                    encoded = np.asarray(png)
                if not np.isin(encoded, [0, 255]).all():
                    continue
                raw = encoded.astype(np.float32) / 255.0
            else:
                raw = _load_array(path)
            files[str(image.path)] = _sha256(path)
        elif raw is not None:
            # store 경로뿐 아니라 선택한 원본 plane 내용도 지문으로 남긴다.
            files[str(image.path)] = hashlib.sha256(np.ascontiguousarray(raw).tobytes()).hexdigest()
        if map_mode != ca_mode:
            reason = "source_mode_mismatch"
        kind = "particle_sim_nominal" if map_mode == "nominal" else "particle_sim_mean"
        length_unit = "um" if data_dir is None else bundle.physical_length_unit
        if length_unit is None and reason is None:
            reason = "missing_grid"
        pitch = image.pixel_size
        if grid_pitch_xy is not None:
            # producer의 x/y pitch가 다르면 scalar 평균으로 숨기지 않고 unsupported로 남긴다.
            pitch = grid_pitch_xy[0] if grid_pitch_xy[0] == grid_pitch_xy[1] else grid_pitch_xy
        source = SpatialCASource(
            bundle.sample_id, diameter, kind, ca_mode,
            "binary" if map_mode == "nominal" else "probability", f"ca_{ca_mode}_um2",
            f"{version}:{bundle.failure_type}:{map_mode}", raw,
            None if raw is None else np.ones(np.shape(raw), dtype=bool),
            SpatialFrame(pitch, image.origin_xy, length_unit or "um", f"local:{bundle.sample_id}"),
            total, unit,
            {"producer": "particle-sim", "producer_version": version, "settings": settings,
             "source_files": files, "source_step": source_step, "unit_id": unit_id,
             "map_mode": map_mode, "domain": "full_rectangular_grid",
             "hash_basis": "file_bytes" if data_dir is not None else "selected_plane_bytes"}, reason,
        )
        results.append(_with_validation(source))
    return results


def exported_particle_sim_sources(bundle: schema.AnalysisBundle, data_dir: Path,
                                  payload: dict) -> list[SpatialCASource]:
    """exporter 선언과 raw identity가 있는 nominal 원본만 연결한다.

    2D 배열 원점/pitch는 기존 CSV 값을 보존한다. 일반 processed_intensity/범례 영상은
    승격하지 않으며 없는 선언은 빈 목록이다. 원본 파일을 읽되 bundle은 변경하지 않는다.
    """
    if not isinstance(payload, dict):
        raise schema.DataContractError("regional export provenance must be a mapping")
    settings = payload.get("calc_settings") or {}
    if not isinstance(settings, dict):
        raise schema.DataContractError("regional calc_settings must be a mapping")
    entries = payload.get("sample_id_mapping") or []
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise schema.DataContractError("regional sample_id_mapping must contain mappings")
    storage = payload.get("array_storage") or {}
    if not isinstance(storage, dict) or not isinstance(storage.get("formats", {}), dict):
        raise schema.DataContractError("regional array_storage/formats must be mappings")
    if settings.get("hotspot_semantics") != NOMINAL_EXPORT_SEMANTICS:
        return []
    mode = settings.get("ca_mode")
    if mode not in ("nominal", "mean") or settings.get("ca_source") != f"ca_{mode}_um2":
        return []
    entries = payload.get("sample_id_mapping") or []
    raw = next((e.get("raw_tuple") for e in entries if e.get("sample_id") == bundle.sample_id), None)
    if not isinstance(raw, list) or len(raw) != 4:
        return []
    return particle_sim_sources(bundle, map_mode="nominal", ca_mode=mode,
                                source_step=raw[2], unit_id=raw[3], data_dir=data_dir,
                                array_storage=payload.get("array_storage"))


# ---------------------------------------------------------------------------
# 명시 manifest IO: strict shape/type/경로, scalar는 bundle에서만 가져온다
# ---------------------------------------------------------------------------


def _relative_file(base: Path, value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise schema.DataContractError("regional source path must be a nonempty relative path")
    rel = Path(value)
    path = (base / rel).resolve()
    if rel.is_absolute() or ".." in rel.parts or not path.is_relative_to(base.resolve()):
        raise schema.DataContractError(f"regional source outside manifest directory: {value}")
    if not path.is_file():
        raise schema.DataContractError(f"regional source file missing: {value}")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_array(path: Path) -> np.ndarray:
    if path.suffix.lower() != ".npy":
        raise schema.DataContractError("regional manifest arrays must be NPY")
    try:
        array = np.load(path, allow_pickle=False)
    except (ValueError, OSError) as exc:
        raise schema.DataContractError(f"invalid regional NPY: {path}: {exc}") from exc
    if not isinstance(array, np.ndarray) or array.ndim != 2 or not array.size:
        raise schema.DataContractError("regional NPY must be a nonempty 2D array")
    return array


def _validate_source_row(row: dict) -> float:
    """행의 형식·단위·의미를 확인하고 canonical 지름(um)을 반환한다."""
    required = {"sample_id", "diameter", "diameter_unit", "kind", "mode", "semantics", "ca_source",
                "calculation_contract_id", "density_path", "origin_xy", "pixel_size", "length_unit",
                "frame_id", "producer", "version", "source_files", "evidence"}
    optional = {"valid_mask_path", "source_step", "unit_id", "settings"}
    if not isinstance(row, dict) or not required <= set(row) or set(row) - required - optional:
        raise schema.DataContractError("regional source row has missing or unknown fields")
    for key in required - {"diameter", "pixel_size", "origin_xy", "source_files"}:
        if not isinstance(row[key], str) or not row[key].strip():
            raise schema.DataContractError(f"regional source {key} must be nonempty string")
    for key in ("diameter", "pixel_size"):
        if not isinstance(row[key], Real) or isinstance(row[key], bool) or not np.isfinite(row[key]) or row[key] <= 0:
            raise schema.DataContractError(f"regional source {key} must be finite positive scalar")
    origin = row["origin_xy"]
    valid_origin_shape = isinstance(origin, list) and len(origin) == 2
    finite_origin = valid_origin_shape and all(
        isinstance(v, Real) and not isinstance(v, bool) and np.isfinite(v) for v in origin)
    if not finite_origin:
        raise schema.DataContractError("regional source origin_xy must be two finite coordinates")
    schema.normalize_length_unit(row["length_unit"])
    diameter = diameter_key(row["diameter"], row["diameter_unit"])
    kind = row["kind"]
    if kind not in ("particle_sim_nominal", "particle_sim_mean", "explicit_ca_density"):
        raise schema.DataContractError("unsupported regional source kind")
    expected_semantics = {"particle_sim_nominal": "binary", "particle_sim_mean": "probability",
                          "explicit_ca_density": "ca_density"}[kind]
    valid_mode = row["mode"] in ("nominal", "mean")
    correct_source = row["ca_source"] == f"ca_{row['mode']}_um2"
    if not valid_mode or row["semantics"] != expected_semantics or not correct_source:
        raise schema.DataContractError("invalid regional source mode/semantics")
    files = row["source_files"]
    valid_files = isinstance(files, list) and files and all(isinstance(v, str) for v in files)
    if not valid_files:
        raise schema.DataContractError("regional source_files must be a nonempty list of relative paths")
    settings = row.get("settings", {})
    if not isinstance(settings, dict):
        raise schema.DataContractError("regional settings must be a mapping")
    for key in ("source_step", "unit_id"):
        if key in row and (not isinstance(row[key], str) or not row[key].strip()):
            raise schema.DataContractError(f"regional {key} must be nonempty string")
    return diameter


def _load_source_arrays(base: Path, row: dict) -> tuple[np.ndarray, np.ndarray, dict[str, Path]]:
    """원본 NPY와 bool valid mask만 읽는다. 값 보정이나 scalar 연결은 하지 않는다."""
    kind = row["kind"]
    paths = {value: _relative_file(base, value) for value in row["source_files"]}
    density_path = _relative_file(base, row["density_path"])
    paths[row["density_path"]] = density_path
    density = _load_array(density_path)
    if density.dtype.kind not in "bifu" or not np.isfinite(density).all() or np.any((density < 0) | (density > 1)):
        raise schema.DataContractError("invalid_source_values: density must be finite [0,1]")
    if kind == "particle_sim_nominal" and not np.isin(density, [0, 1]).all():
        raise schema.DataContractError("invalid nominal binary values")
    valid = np.ones(density.shape, dtype=bool)
    if row.get("valid_mask_path") is not None:
        mask_path = _relative_file(base, row["valid_mask_path"])
        paths[row["valid_mask_path"]] = mask_path
        valid = _load_array(mask_path)
        if valid.dtype != np.bool_ or valid.shape != density.shape:
            raise schema.DataContractError("valid mask must be bool with density shape")
    return density, valid, paths


def _link_manifest_scalar(bundle: schema.AnalysisBundle, row: dict,
                          diameter: float) -> tuple[float | None, str, str | None]:
    """동일 크기·계산 field의 bundle scalar를 연결하며 결측을 0으로 채우지 않는다."""
    total, unit, _, reason = scalar_observation(bundle, diameter)
    unit = unit or "um^2"
    scalar_settings = bundle.provenance.calc_settings | (bundle.ca.calc_settings if bundle.ca else {})
    # bundle이 어느 scalar field인지 모르면 전역 값을 새 선언에 임의 연결하지 않는다.
    if scalar_settings.get("ca_source") != row["ca_source"]:
        reason = "source_mode_mismatch" if scalar_settings.get("ca_source") else "unverified_scalar_source"
    return total, unit, reason


def _build_manifest_source(p: Path, row: dict, bundle: schema.AnalysisBundle, diameter: float) -> SpatialCASource:
    """검증된 행의 파일·scalar·추적 정보를 조합하여 보존 gate를 저장한다."""
    density, valid, paths = _load_source_arrays(p.parent, row)
    total, unit, reason = _link_manifest_scalar(bundle, row, diameter)
    provenance = {"producer": row["producer"], "producer_version": row["version"],
                  "source_files": {value: _sha256(file) for value, file in paths.items()},
                  "manifest_path": str(p.resolve()), "manifest_sha256": _sha256(p),
                  "evidence": row["evidence"], "settings": copy.deepcopy(row.get("settings", {})),
                  "source_step": row.get("source_step"), "unit_id": row.get("unit_id"),
                  "domain": "explicit_valid_mask" if row.get("valid_mask_path") else "full_rectangular_grid"}
    source = SpatialCASource(bundle.sample_id, diameter, row["kind"], row["mode"], row["semantics"],
                             row["ca_source"], row["calculation_contract_id"], density, valid,
                             SpatialFrame(
                                 row["pixel_size"], tuple(row["origin_xy"]), row["length_unit"], row["frame_id"],
                             ),
                             total, unit, provenance, reason)
    return _with_validation(source)


def load_source_manifest(path: str | Path, bundles: Sequence[schema.AnalysisBundle]) -> list[SpatialCASource]:
    """version 1 JSON과 상대 NPY를 읽어 새 typed 원천 목록을 반환한다.

    density는 2D [y,x] [0,1], valid mask는 같은 shape bool, origin은 좌상단이며
    pixel_size/diameter의 단위는 행에 명시한다. scalar는 동일 bundle/지름/ca_source에
    연결한다. 결측 scalar와 보존 실패는 reason, 형식/경로/중복은 DataContractError다.
    파일 읽기/해시만 수행하며 bundles/배열 변경, IO 쓰기, 자동 override는 없다.
    """
    p = Path(path)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise schema.DataContractError(f"regional manifest cannot be read: {exc}") from exc
    valid_root = isinstance(payload, dict) and set(payload) == {"schema_version", "sources"}
    valid_version = valid_root and payload["schema_version"] == "1"
    if not valid_version or not isinstance(payload["sources"], list):
        raise schema.DataContractError("regional manifest requires schema_version '1' and sources list")
    by_id = {b.sample_id: b for b in bundles}
    keys = {(b.sample_id, diameter_key(s.diameter_um)) for b in bundles for s in b.ca_spatial_sources}
    result = []
    for row in payload["sources"]:
        diameter = _validate_source_row(row)
        key = (row["sample_id"], diameter)
        if key in keys:
            raise schema.DataContractError("duplicate regional source or embedded source conflict")
        keys.add(key)
        bundle = by_id.get(row["sample_id"])
        if bundle is None:
            raise schema.DataContractError("regional source references unknown sample_id")
        result.append(_build_manifest_source(p, row, bundle, diameter))
    return result
