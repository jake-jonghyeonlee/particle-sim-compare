"""예시 파일 어댑터: CSV/JSON 메타데이터와 PNG/NPY 이미지를 공통 schema 묶음으로 변환한다.

실제 자료를 연결할 때는 이 모듈을 참고해 같은 LoadedDataset을 반환하는 어댑터를 새로 만들면 된다.
필수 파일은 bundles.csv와 Fab 입력(fab.json 또는 fab_measurements.csv)이고,
layouts.csv, hotspots.csv, ca_records.csv, layer_stack.csv, geometry.json,
provenance.json은 조건부 또는 선택 입력이다. 형식은 docs/data_contract.md를 따른다.

누락값은 None/NaN으로 유지하고 0으로 바꾸지 않는다.
계약 위반은 해당 sample_id를 담은 DataContractError로 알린다.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from simcompare import schema
from .regional import exported_particle_sim_sources
from simcompare.preprocess import legend_to_values

REAL_PARTICLE_SIZE_GRID_UM = (*range(1, 31), 40, 50)

__all__ = ["LoadedDataset", "load_dataset", "read_image"]


@dataclass
class LoadedDataset:
    """분석 묶음, Fab 분포, 경고와 선택적 외부 설계 검토 상태를 담는 어댑터 출력."""
    bundles: list[schema.AnalysisBundle]
    distributions: dict[str, schema.FabDistribution]
    warnings: list[str]
    design_reviews: list[dict] = field(default_factory=list)


def _load_layer_stack(data_dir: Path) -> dict[tuple[str, str, str], tuple[int, str]]:
    """선택적 layer_stack.csv를 (product, version|*, layer) 키로 읽는다."""
    path = data_dir / "layer_stack.csv"
    if not path.is_file():
        return {}
    rows = _read_csv(path)
    _require_columns(
        rows,
        ["product_id", "design_version", "layer_name", "stack_order", "fab_process_layer"],
        "layer_stack.csv",
    )
    result: dict[tuple[str, str, str], tuple[int, str]] = {}
    for row in rows:
        product = _text(row.get("product_id"))
        version = _text(row.get("design_version"))
        layer = _text(row.get("layer_name"))
        fab_layer = _text(row.get("fab_process_layer"))
        order = _req_float(row.get("stack_order"), "layer_stack.csv stack_order")
        if not product or not version or not layer or not fab_layer:
            raise schema.DataContractError("layer_stack.csv: 필수 문자열 값이 비어 있습니다")
        if not float(order).is_integer():
            raise schema.DataContractError("layer_stack.csv: stack_order는 정수여야 합니다")
        key = (product, version, layer)
        if key in result:
            raise schema.DataContractError(f"layer_stack.csv: 행이 중복되었습니다 {key}")
        result[key] = (int(order), fab_layer)
    return result


def _apply_vertical_layer(
    bundle: schema.AnalysisBundle,
    layer_stack: dict[tuple[str, str, str], tuple[int, str]],
    mode: str,
) -> list[str]:
    """vertical short의 상부 layer를 제품별 적층표로 결정한다."""
    if bundle.failure_type != "vertical_short":
        return []
    entries = []
    for layer in (bundle.layer_a, bundle.layer_b):
        exact = (bundle.product_id, bundle.design_version, layer)
        wildcard = (bundle.product_id, "*", layer)
        value = layer_stack.get(exact, layer_stack.get(wildcard))
        if value is None:
            message = f"{bundle.sample_id}: layer_stack.csv에서 layer {layer!r}의 적층 순서를 찾을 수 없습니다"
            if mode == "real":
                raise schema.DataContractError(message)
            return [message]
        entries.append((value[0], value[1], layer))
    if entries[0][0] == entries[1][0]:
        raise schema.DataContractError(
            f"{bundle.sample_id}: vertical short 대상 layer의 stack_order가 같습니다"
        )
    upper = max(entries, key=lambda item: item[0])
    if bundle.mapping_contract_version == "1.2":
        bundle.provenance.calc_settings["upper_physical_layer"] = upper[2]
        return []
    if bundle.assigned_process_layer and bundle.assigned_process_layer != upper[1]:
        message = (
            f"{bundle.sample_id}: assigned_process_layer {bundle.assigned_process_layer!r}가 "
            f"적층표에서 결정한 상부 Fab layer {upper[1]!r}와 다릅니다"
        )
        if mode == "real":
            raise schema.DataContractError(message)
        return [message]
    bundle.assigned_process_layer = upper[1]
    return []


# --------------------------------------------------------------------------- #
# 이미지 읽기
# --------------------------------------------------------------------------- #
def read_image(path: Path, *, kind: str, legend: dict | None) -> tuple[np.ndarray, str, str | None]:
    """이미지 파일 하나를 읽어 (float32 배열, 값 의미, 범례 ID)를 반환한다.

    - .npy: allow_pickle=False로 읽으며 2차원이어야 한다.
    - 범례 없는 hotspot은 정확한 0/1이면 binary, 그 외 [0,1] 값은 processed_intensity다.
    - 정수 NPY의 정확한 0/255 인코딩과 PNG는 0/1로 복원한다. 범례 PNG는 probability다.
    - layout은 0.5 기준으로 이진화(binary)한다.
    읽기 실패나 범례 대응 실패는 DataContractError로 알린다.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    binary_hotspot = False
    if suffix == ".npy":
        try:
            arr = np.load(path, allow_pickle=False)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise schema.DataContractError(f"이미지 {path}를 읽을 수 없습니다: {exc!r}") from None
        if arr.ndim != 2:
            raise schema.DataContractError(
                f"이미지 {path}는 2차원이어야 합니다(현재 {arr.ndim}차원)"
            )
        if kind == "hotspot" and legend is None:
            # 파일 형식만 다르다는 이유로 같은 binary 입력의 CA·학습 강도가 달라지면 안 된다.
            integer_binary = np.issubdtype(arr.dtype, np.integer) and np.all((arr == 0) | (arr == 255))
            if integer_binary:
                arr = arr / 255.0
            if np.any((arr < 0) | (arr > 1)):
                raise schema.DataContractError(
                    f"hotspot 이미지 {path}: 범례 없는 NPY 값은 [0,1] 또는 정수 0/255여야 합니다"
                )
        binary_hotspot = bool(np.all((arr == 0) | (arr == 1)))
        arr = np.asarray(arr, dtype=np.float32)
    elif suffix == ".png":
        try:
            with Image.open(path) as im:
                if kind == "hotspot" and legend is not None:
                    rgb = np.asarray(im.convert("RGB"))
                    arr = legend_to_values(rgb, legend).astype(np.float32)
                    return arr, "probability", str(legend.get("legend_id", ""))
                gray = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
                arr = gray
                binary_hotspot = bool(np.all((arr == 0) | (arr == 1)))
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise schema.DataContractError(f"이미지 {path}를 읽을 수 없습니다: {exc!r}") from None
    else:
        raise schema.DataContractError(f"지원하지 않는 이미지 확장자 {suffix!r}: {path}")

    if kind == "layout":
        arr = (arr > 0.5).astype(np.float32)
        return arr, "binary", None
    if kind == "hotspot" and legend is None and binary_hotspot:
        return arr, "binary", None
    return arr, "processed_intensity", None


# --------------------------------------------------------------------------- #
# CSV / JSON 도우미
# --------------------------------------------------------------------------- #
def _blank(value: object) -> bool:
    """None, NaN, 공백 문자열을 '값 없음'으로 판단한다."""
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _text(value: object) -> str | None:
    """빈 값은 None, 나머지는 앞뒤 공백을 제거한 문자열로 바꾼다."""
    if _blank(value):
        return None
    return str(value).strip()


def _float(value: object, what: str = "value") -> float | None:
    """빈 값은 None, 숫자가 아니면 what을 담은 DataContractError를 낸다."""
    if _blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise schema.DataContractError(f"{what}: 숫자가 아닙니다: {value!r}") from None


def _req_float(value: object, what: str) -> float:
    """값이 반드시 있어야 하는 숫자 필드를 읽는다."""
    result = _float(value, what)
    if result is None:
        raise schema.DataContractError(f"{what}: 필수 숫자 값이 비어 있습니다")
    return result


class _Rows(list):
    """헤더(fieldnames)를 기억하는 CSV 행 목록. 헤더만 있는 파일도 필수 열을 검사할 수 있다."""

    fieldnames: list[str] = []


def _read_csv(path: Path) -> list[dict]:
    """UTF-8(BOM 허용) CSV를 읽어 헤더를 기억하는 행 목록으로 반환한다."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = _Rows(reader)
        rows.fieldnames = list(reader.fieldnames or [])
    return rows


def _require_columns(rows: list[dict], required: list[str], what: str) -> None:
    """필수 열이 헤더에 모두 있는지 확인한다."""
    present = set(getattr(rows, "fieldnames", None) or (rows[0].keys() if rows else []))
    missing = [name for name in required if name not in present]
    if missing:
        raise schema.DataContractError(f"{what}: 필수 열 누락 {missing}")


def _resolve(data_dir: Path, raw: str, sid: str) -> Path:
    """CSV의 이미지 경로를 data_dir 기준 상대경로로 해석하고, 절대경로·data_dir 밖·없는 파일을 거부한다."""
    path = Path(raw)
    if path.is_absolute():
        raise schema.DataContractError(f"{sid}: 이미지 경로는 상대경로여야 합니다(현재 {raw!r})")
    resolved = (data_dir / path).resolve()
    root = data_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise schema.DataContractError(f"{sid}: 이미지 경로가 data_dir 밖을 가리킵니다: {raw!r}")
    if not resolved.is_file():
        raise schema.DataContractError(f"{sid}: 이미지 파일이 없습니다: {raw!r}")
    return resolved


# --------------------------------------------------------------------------- #
# fab.json 읽기
# --------------------------------------------------------------------------- #
def _load_distributions(data_dir: Path) -> dict[str, schema.FabDistribution]:
    """fab.json의 분포 목록을 FabDistribution으로 읽고 검증한다. null 값은 미관측으로 둔다."""
    fab_path = data_dir / "fab.json"
    if not fab_path.is_file():
        return {}
    payload = json.loads(fab_path.read_text(encoding="utf-8"))
    entries = payload.get("distributions")
    if not isinstance(entries, list):
        raise schema.DataContractError("fab.json: 'distributions'는 리스트여야 합니다")

    distributions: dict[str, schema.FabDistribution] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise schema.DataContractError("fab.json: 각 분포는 객체여야 합니다")
        did = _text(entry.get("fab_distribution_id")) or "<missing id>"

        # 숫자 리스트를 float 리스트로 읽는다(allow_null이면 null은 미관측, bool·문자열은 거부).
        def numbers(key: str, allow_null: bool) -> list:
            raw = entry.get(key)
            if not isinstance(raw, list):
                raise schema.DataContractError(f"{did}: {key}는 리스트여야 합니다")
            out = []
            for item in raw:
                if item is None and allow_null:
                    out.append(None)
                elif isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise schema.DataContractError(f"{did}: {key}에 숫자가 아닌 값이 있습니다 {item!r}")
                else:
                    out.append(float(item))
            return out

        lower = np.asarray(numbers("bin_lower", False), dtype=float)
        upper = np.asarray(numbers("bin_upper", False), dtype=float)
        raw = numbers("raw_values", True)
        observed = np.array([v is not None for v in raw], dtype=bool)
        values = np.array([np.nan if v is None else v for v in raw], dtype=float)
        is_reference = entry.get("is_reference", False)
        if not isinstance(is_reference, bool):
            raise schema.DataContractError(f"{did}: is_reference는 true/false여야 합니다")
        dist = schema.FabDistribution(
            fab_distribution_id=_text(entry.get("fab_distribution_id")) or "",
            fab_id=_text(entry.get("fab_id")) or "",
            product_family=_text(entry.get("product_family")),
            process_layer=_text(entry.get("process_layer")) or "",
            bin_lower=lower,
            bin_upper=upper,
            raw_values=values,
            observed=observed,
            value_kind=_text(entry.get("value_kind")) or "",
            value_unit=_text(entry.get("value_unit")) or "",
            size_unit=_text(entry.get("size_unit")) or "",
            surveyed_area=_float(entry.get("surveyed_area"), f"{did}: surveyed_area"),
            area_unit=_text(entry.get("area_unit")),
            period=_text(entry.get("period")),
            averaging_basis=_text(entry.get("averaging_basis")),
            is_reference=is_reference,
            fab_pair_key=entry.get("fab_pair_key") or None,
        )
        schema.validate_fab(dist)
        if dist.fab_distribution_id in distributions:
            raise schema.DataContractError(
                f"fab_distribution_id가 중복되었습니다 {dist.fab_distribution_id!r}"
            )
        distributions[dist.fab_distribution_id] = dist
    return distributions


def _load_processed_fab_measurements(
    data_dir: Path, mode: str
) -> dict[str, schema.FabDistribution]:
    """glass당 평균 검출 개수 가공 CSV를 FabDistribution 목록으로 변환한다."""
    path = data_dir / "fab_measurements.csv"
    if not path.is_file():
        return {}
    rows = _read_csv(path)
    required = [
        "source_id", "fab_id", "product_family", "process_layer", "particle_size_um",
        "avg_detected_count", "surveyed_area_um2", "averaging_basis", "period_start", "period_end",
    ]
    _require_columns(rows, required, "fab_measurements.csv")
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        source_id = _text(row.get("source_id"))
        if not source_id:
            raise schema.DataContractError("fab_measurements.csv: source_id가 비어 있습니다")
        grouped.setdefault(source_id, []).append(row)
    distributions: dict[str, schema.FabDistribution] = {}
    for source_id, group in grouped.items():
        def one_text(key: str, *, required_value: bool = True) -> str | None:
            values = {(row.get(key) if row.get(key) and row.get(key).strip() else None)
                      if key == "fab_pair_key" else _text(row.get(key)) for row in group}
            if len(values) != 1:
                raise schema.DataContractError(f"{source_id}: {key} 값이 한 분포 안에서 섞여 있습니다")
            value = values.pop()
            if required_value and not value:
                raise schema.DataContractError(f"{source_id}: {key}가 비어 있습니다")
            return value

        fab_id = one_text("fab_id")
        family = one_text("product_family")
        layer = one_text("process_layer")
        basis = one_text("averaging_basis")
        start = one_text("period_start")
        end = one_text("period_end")
        areas = {_req_float(row.get("surveyed_area_um2"), f"{source_id}: surveyed_area_um2") for row in group}
        if len(areas) != 1:
            raise schema.DataContractError(f"{source_id}: surveyed_area_um2가 한 분포 안에서 섞여 있습니다")
        by_size: dict[int, float | None] = {}
        for row in group:
            size = _req_float(row.get("particle_size_um"), f"{source_id}: particle_size_um")
            if not float(size).is_integer() or not 1 <= int(size) <= 50:
                raise schema.DataContractError(f"{source_id}: 이물 지름은 1..50 um 정수여야 합니다")
            size_int = int(size)
            if size_int in by_size:
                raise schema.DataContractError(f"{source_id}: 이물 지름 {size_int} um가 중복되었습니다")
            by_size[size_int] = _float(row.get("avg_detected_count"), f"{source_id}: avg_detected_count")
        if mode == "real" and tuple(sorted(by_size)) != REAL_PARTICLE_SIZE_GRID_UM:
            missing = sorted(set(REAL_PARTICLE_SIZE_GRID_UM) - set(by_size))
            extra = sorted(set(by_size) - set(REAL_PARTICLE_SIZE_GRID_UM))
            raise schema.DataContractError(
                f"{source_id}: real 모드 Fab 가공자료는 1–30 um 1 um 간격 + 40, 50 um "
                f"(32개) 전체가 필요합니다(누락 {missing}, 격자 밖 {extra})"
            )
        sizes = sorted(by_size)
        observed = np.array([by_size[size] is not None for size in sizes], dtype=bool)
        raw_values = np.array([
            np.nan if by_size[size] is None else by_size[size] for size in sizes
        ], dtype=float)
        dist = schema.FabDistribution(
            fab_distribution_id=source_id,
            fab_id=fab_id,
            product_family=family,
            fab_pair_key=one_text("fab_pair_key", required_value=False),
            process_layer=layer,
            bin_lower=np.asarray(sizes, dtype=float) - 0.5,
            bin_upper=np.asarray(sizes, dtype=float) + 0.5,
            raw_values=raw_values,
            observed=observed,
            value_kind="average_count_per_glass",
            value_unit="particles",
            size_unit="um",
            surveyed_area=areas.pop(),
            area_unit="um^2",
            period=f"{start}/{end}",
            averaging_basis=basis,
            is_reference=False,
        )
        schema.validate_fab(dist)
        distributions[source_id] = dist
    return distributions


def _load_design_reviews(data_dir: Path) -> list[dict]:
    """선택적 외부 시뮬레이션·설계 검토 상태 CSV를 읽는다."""
    path = data_dir / "design_review_status.csv"
    if not path.is_file():
        return []
    rows = _read_csv(path)
    required = [
        "candidate_id", "comparison_group_id", "hypothesis", "resimulation_status",
        "resistance_status", "capacitance_status", "coupling_status", "signal_status",
        "design_rule_status", "short_open_status", "external_result_ref", "status",
    ]
    _require_columns(rows, required, "design_review_status.csv")
    result = []
    seen = set()
    for row in rows:
        candidate_id = _text(row.get("candidate_id"))
        group_id = _text(row.get("comparison_group_id"))
        if not candidate_id or not group_id:
            raise schema.DataContractError(
                "design_review_status.csv: candidate_id와 comparison_group_id가 필요합니다"
            )
        if candidate_id in seen:
            raise schema.DataContractError(f"design_review_status.csv: candidate_id 중복 {candidate_id!r}")
        seen.add(candidate_id)
        result.append({str(k): (_text(v) if _text(v) is not None else None) for k, v in row.items()})
    return result


# --------------------------------------------------------------------------- #
# 묶음별 이미지·곡선 조립
# --------------------------------------------------------------------------- #
def _build_layouts(
    rows: list[dict],
    sid: str,
    bundle: schema.AnalysisBundle,
    data_dir: Path,
) -> None:
    """layouts.csv에서 해당 샘플의 전극 이미지를 slot(a, b) 순서로 붙인다. open의 slot b는 거부한다."""
    by_slot: dict[str, dict] = {}
    for row in rows:
        if _text(row.get("sample_id")) != sid:
            continue
        slot = _text(row.get("slot"))
        if slot not in ("a", "b"):
            raise schema.DataContractError(f"{sid}: 잘못된 layout slot {slot!r}")
        if slot in by_slot:
            raise schema.DataContractError(f"{sid}: layout slot이 중복되었습니다 {slot!r}")
        if bundle.is_open and slot == "b":
            raise schema.DataContractError(f"{sid}: open 불량에는 layout slot 'b'가 있을 수 없습니다")
        by_slot[slot] = row

    for slot in sorted(by_slot):
        row = by_slot[slot]
        path = _resolve(data_dir, str(row["path"]), sid)
        arr, semantics, _legend = read_image(path, kind="layout", legend=None)
        role = bundle.electrode_a_role if slot == "a" else bundle.electrode_b_role
        origin_x = _float(row.get("origin_x"))
        origin_y = _float(row.get("origin_y"))
        bundle.layouts.append(
            schema.ImageInput(
                role=role,
                array=arr,
                path=str(row["path"]),
                pixel_size=_float(row.get("pixel_size")),
                origin_xy=(origin_x, origin_y)
                if origin_x is not None and origin_y is not None
                else None,
                value_semantics=semantics,
            )
        )


def _build_hotspots(
    rows: list[dict],
    sid: str,
    bundle: schema.AnalysisBundle,
    data_dir: Path,
    legend: dict | None,
) -> None:
    """hotspots.csv에서 해당 샘플의 hotspot 이미지를 이물 크기 순서로 붙인다."""
    selected = [row for row in rows if _text(row.get("sample_id")) == sid]
    selected.sort(key=lambda row: _req_float(row["particle_size"], f"{sid}: hotspot 이물 크기"))
    for row in selected:
        path = _resolve(data_dir, str(row["path"]), sid)
        arr, semantics, legend_id = read_image(
            path, kind="hotspot", legend=legend
        )
        origin_x = _float(row.get("origin_x"))
        origin_y = _float(row.get("origin_y"))
        bundle.hotspots.append(
            schema.ImageInput(
                role="hotspot",
                array=arr,
                path=str(row["path"]),
                pixel_size=_float(row.get("pixel_size")),
                origin_xy=(origin_x, origin_y)
                if origin_x is not None and origin_y is not None
                else None,
                particle_size=_req_float(row["particle_size"], f"{sid}: hotspot 이물 크기"),
                particle_size_unit=_text(row.get("particle_size_unit")) or "",
                value_semantics=semantics,
                legend_id=legend_id,
            )
        )


def _build_ca(
    rows: list[dict],
    sid: str,
    bundle: schema.AnalysisBundle,
) -> None:
    """ca_records.csv의 크기별 기록을 CACurve로 묶는다. 빈 ca_value는 미관측이다."""
    records = [
        {
            "sample_id": sid,
            "particle_size": _req_float(row["particle_size"], f"{sid}: CA 이물 크기"),
            "particle_size_unit": row.get("particle_size_unit"),
            "ca_value": row.get("ca_value"),
            "ca_unit": row.get("ca_unit"),
        }
        for row in rows
        if _text(row.get("sample_id")) == sid
    ]
    if not records:
        return
    curves = schema.group_size_records(records)
    bundle.ca = curves[sid]


def _build_geometry(
    payload: dict,
    sid: str,
    bundle: schema.AnalysisBundle,
) -> None:
    """geometry.json에서 해당 샘플의 두께·형상 수치를 읽는다."""
    entry = payload.get(sid)
    if not isinstance(entry, dict):
        return
    thickness = {str(k): _req_float(v, f"{sid}: 두께 {k}") for k, v in (entry.get("thickness") or {}).items()}
    shape = {str(k): _req_float(v, f"{sid}: 형상 수치 {k}") for k, v in (entry.get("shape_metrics") or {}).items()}
    bundle.geometry = schema.Geometry(
        thickness=thickness,
        shape_metrics=shape,
        length_unit=_text(entry.get("length_unit")),
    )


# --------------------------------------------------------------------------- #
# bundles.csv 행 해석
# --------------------------------------------------------------------------- #
def _parse_bundle_row(row: dict, sid: str) -> schema.AnalysisBundle:
    """bundles.csv 한 행을 AnalysisBundle로 만든다. open의 두 번째 전극은 해당 없음으로 둔다."""
    b_role = _text(row.get("electrode_b_role"))
    b_layer = _text(row.get("layer_b"))
    failure_type = _text(row.get("failure_type"))
    if failure_type is None:
        raise schema.DataContractError(f"{sid}: failure_type이 없습니다")

    if failure_type in schema.OPEN_TYPES:
        if b_role is not None and b_role != schema.NOT_APPLICABLE:
            raise schema.DataContractError(
                f"{sid}: open 불량의 electrode_b_role은 비우거나 N/A여야 합니다"
            )
        if b_layer is not None and b_layer != schema.NOT_APPLICABLE:
            raise schema.DataContractError(
                f"{sid}: open 불량의 layer_b는 비우거나 N/A여야 합니다"
            )
        b_role = schema.NOT_APPLICABLE
        b_layer = schema.NOT_APPLICABLE
    else:
        if failure_type not in schema.FAILURE_TYPES:
            raise schema.DataContractError(f"{sid}: 알 수 없는 failure_type {failure_type!r}")
        if b_role is None:
            raise schema.DataContractError(f"{sid}: short 불량에는 electrode_b_role이 필요합니다")
        if b_layer is None:
            raise schema.DataContractError(f"{sid}: short 불량에는 layer_b가 필요합니다")

    return schema.AnalysisBundle(
        sample_id=sid,
        product_id=_text(row.get("product_id")) or "",
        product_family=_text(row.get("product_family")) or "",
        design_version=_text(row.get("design_version")) or "",
        design_lineage_id=_text(row.get("design_lineage_id")) or "",
        electrode_a_role=_text(row.get("electrode_a_role")) or "",
        electrode_b_role=b_role,
        layer_a=_text(row.get("layer_a")) or "",
        layer_b=b_layer,
        failure_type=failure_type,
        fab_id=_text(row.get("fab_id")) or "",
        fab_distribution_id=_text(row.get("fab_distribution_id")),
        assigned_process_layer=row.get("assigned_process_layer") or None,
        fab_pair_key=row.get("fab_pair_key") or None,
        unit_width=_float(row.get("unit_width")),
        unit_height=_float(row.get("unit_height")),
        physical_length_unit=_text(row.get("physical_length_unit")),
        parent_design_id=_text(row.get("parent_design_id")),
        change_description=_text(row.get("change_description")),
    )


def _calc_version_for(
    bundle: schema.AnalysisBundle,
    sid: str,
    bundle_rows: list[dict],
    provenance_payload: dict,
) -> str | None:
    """계산 버전은 bundles.csv 행 값을 우선하고, 없으면 provenance.json 값을 쓴다."""
    for row in bundle_rows:
        if _text(row.get("sample_id")) == sid:
            version = _text(row.get("calc_version"))
            if version is not None:
                return version
    return _text(provenance_payload.get("calc_version"))


def _validate_image_registration(bundle: schema.AnalysisBundle, mode: str) -> list[str]:
    """같은 묶음의 layout·hotspot이 픽셀 단위로 1:1 대응하는지 검사한다."""
    images = [image for image in bundle.layouts + bundle.hotspots if image.array is not None]
    if not images:
        return []
    sid = bundle.sample_id
    reference = images[0]
    issues: list[str] = []
    for image in images:
        if image.array.shape != reference.array.shape:
            issues.append(f"이미지 크기 불일치 {reference.array.shape} != {image.array.shape}")
        if image.pixel_size is None or reference.pixel_size is None:
            issues.append("pixel_size 누락")
        elif not np.isclose(image.pixel_size, reference.pixel_size, rtol=1e-6, atol=0):
            issues.append(f"pixel_size 불일치 {reference.pixel_size} != {image.pixel_size}")
        if image.origin_xy is None or reference.origin_xy is None:
            issues.append("origin_xy 누락")
        elif not np.allclose(image.origin_xy, reference.origin_xy, rtol=0, atol=1e-9):
            issues.append(f"origin_xy 불일치 {reference.origin_xy} != {image.origin_xy}")
    if bundle.has_physical_scale() and reference.pixel_size is not None:
        height_px, width_px = reference.array.shape
        image_width = width_px * reference.pixel_size
        image_height = height_px * reference.pixel_size
        if not np.isclose(image_width, bundle.unit_width, rtol=0.02, atol=0):
            issues.append(
                f"이미지 물리 가로 {image_width:g}가 unit_width {bundle.unit_width:g}와 다름"
            )
        if not np.isclose(image_height, bundle.unit_height, rtol=0.02, atol=0):
            issues.append(
                f"이미지 물리 세로 {image_height:g}가 unit_height {bundle.unit_height:g}와 다름"
            )
    issues = list(dict.fromkeys(issues))
    if not issues:
        return []
    message = f"{sid}: layout과 hotspot의 픽셀 1:1 대응을 확인할 수 없습니다: {issues}"
    if mode == "real":
        raise schema.DataContractError(message)
    return [message]


# --------------------------------------------------------------------------- #
# 공개 진입점
# --------------------------------------------------------------------------- #
def load_dataset(
    data_dir,
    *,
    mode: str,
    hotspot_legend: dict | None = None,
    canonical_length_unit: str = "um",
) -> LoadedDataset:
    """분석 디렉터리 하나를 읽어 LoadedDataset으로 반환한다.

    모든 부속 기록의 sample_id가 bundles.csv에 있는지, 참조한 분포 ID가 Fab 입력에 있는지 확인하고
    묶음마다 validate_bundle을 적용한다. mode가 real이면 더 엄격한 규칙이 적용된다.
    """
    data_dir = Path(data_dir)
    if mode not in schema.MODES:
        raise schema.DataContractError(f"알 수 없는 mode {mode!r}")

    bundles_path = data_dir / "bundles.csv"
    if not bundles_path.is_file():
        raise schema.DataContractError("bundles.csv 파일이 없습니다")

    source_files = ["bundles.csv"]
    warnings: list[str] = []

    provenance_payload: dict = {}
    provenance_path = data_dir / "provenance.json"
    if provenance_path.is_file():
        provenance_payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        source_files.append("provenance.json")
    if not isinstance(provenance_payload, dict):
        raise schema.DataContractError("provenance.json must be an object")
    # 잘못된 설정 문자열을 dict로 바꾸기 전에 계약 오류로 설명한다.
    declared_settings = provenance_payload.get("calc_settings")
    if declared_settings is not None and not isinstance(declared_settings, dict):
        raise schema.DataContractError("provenance calc_settings must be a mapping")
    if provenance_payload.get("mapping_contract_version") not in (None, "1.1", "1.2"):
        raise schema.DataContractError(f"unknown mapping_contract_version {provenance_payload.get('mapping_contract_version')!r}")

    distributions = _load_distributions(data_dir)
    if distributions:
        source_files.append("fab.json")
    processed = _load_processed_fab_measurements(data_dir, mode)
    duplicates = set(distributions) & set(processed)
    if duplicates:
        raise schema.DataContractError(f"Fab 분포 ID가 파일 사이에 중복되었습니다: {sorted(duplicates)}")
    distributions.update(processed)
    if processed:
        source_files.append("fab_measurements.csv")
    if not distributions:
        raise schema.DataContractError("fab.json 또는 fab_measurements.csv 파일이 필요합니다")
    design_reviews = _load_design_reviews(data_dir)
    if design_reviews:
        source_files.append("design_review_status.csv")
    layer_stack = _load_layer_stack(data_dir)
    if layer_stack:
        source_files.append("layer_stack.csv")

    bundle_rows = _read_csv(bundles_path)
    _require_columns(
        bundle_rows,
        [
            "sample_id", "product_id", "product_family", "design_version",
            "design_lineage_id", "electrode_a_role", "electrode_b_role",
            "layer_a", "layer_b", "failure_type", "fab_id",
        ],
        "bundles.csv",
    )

    seen: set[str] = set()
    bundles: list[schema.AnalysisBundle] = []
    for row in bundle_rows:
        sid = _text(row.get("sample_id"))
        if sid is None:
            raise schema.DataContractError("bundles.csv: sample_id가 비어 있습니다")
        if sid in seen:
            raise schema.DataContractError(f"sample_id가 중복되었습니다 {sid!r}")
        seen.add(sid)
        bundles.append(_parse_bundle_row(row, sid))

    # 선택 파일(샘플별 부속 자료)
    layout_rows: list[dict] = []
    hotspot_rows: list[dict] = []
    ca_rows: list[dict] = []
    geometry_payload: dict = {}

    layouts_path = data_dir / "layouts.csv"
    if layouts_path.is_file():
        layout_rows = _read_csv(layouts_path)
        _require_columns(layout_rows, ["sample_id", "slot", "path"], "layouts.csv")
        source_files.append("layouts.csv")

    hotspots_path = data_dir / "hotspots.csv"
    if hotspots_path.is_file():
        hotspot_rows = _read_csv(hotspots_path)
        _require_columns(
            hotspot_rows, ["sample_id", "particle_size", "particle_size_unit", "path"], "hotspots.csv"
        )
        source_files.append("hotspots.csv")

    ca_path = data_dir / "ca_records.csv"
    if ca_path.is_file():
        ca_rows = _read_csv(ca_path)
        _require_columns(
            ca_rows, ["sample_id", "particle_size", "particle_size_unit", "ca_value", "ca_unit"], "ca_records.csv"
        )
        source_files.append("ca_records.csv")

    geometry_path = data_dir / "geometry.json"
    if geometry_path.is_file():
        geometry_payload = json.loads(geometry_path.read_text(encoding="utf-8"))
        source_files.append("geometry.json")


    version = provenance_payload.get("mapping_contract_version")
    if version not in (None, "1.1", "1.2"):
        raise schema.DataContractError(f"unknown mapping_contract_version {version!r}")
    identities = provenance_payload.get("sample_id_mapping")
    identity_map = {}
    if version == "1.2" and identities is not None:
        if not isinstance(identities, list):
            raise schema.DataContractError("sample_id_mapping must be a list")
        for entry in identities:
            if not isinstance(entry, dict) or entry.get("sample_id") in identity_map:
                raise schema.DataContractError("invalid or duplicate sample_id_mapping entry")
            identity_map[entry.get("sample_id")] = entry
        if set(identity_map) != seen:
            raise schema.DataContractError("sample_id_mapping must cover every bundle exactly")
    pair_registry = {}
    for bundle in bundles:
        bundle.mapping_contract_version = version
        if version != "1.2":
            bundle.assigned_process_layer = _text(bundle.assigned_process_layer)
            bundle.fab_pair_key = _text(bundle.fab_pair_key)
        schema.validate_mapping_contract(bundle)
        if identity_map:
            entry = identity_map[bundle.sample_id]
            raw = entry.get("raw_tuple")
            if not isinstance(raw, list) or len(raw) != 4 or raw[:2] != [bundle.product_id, bundle.design_version]:
                raise schema.DataContractError(f"{bundle.sample_id}: invalid raw identity mapping")
            if any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in raw):
                raise schema.DataContractError(f"{bundle.sample_id}: invalid raw identity components")
            expected_id = "".join(c if c.isascii() and (c.isalnum() or c in "_.-") else "_"
                                  for c in "-".join(raw))
            if expected_id != bundle.sample_id:
                raise schema.DataContractError(f"{bundle.sample_id}: raw identity sample ID mismatch")
            key = schema.make_fab_pair_key(raw[3], bundle.layer_a, bundle.layer_b, pair_registry)
            if key != bundle.fab_pair_key or entry.get("fab_pair_key") != key or entry.get("assigned_process_layer") != bundle.assigned_process_layer:
                raise schema.DataContractError(f"{bundle.sample_id}: identity mapping disagrees with bundle")
    if version == "1.2" and any(d.is_reference and d.fab_pair_key is not None for d in distributions.values()):
        raise schema.DataContractError("1.2 reference distributions cannot carry fab_pair_key")

    for row in layout_rows + hotspot_rows + ca_rows:
        ref = _text(row.get("sample_id"))
        if ref is None:
            raise schema.DataContractError("layouts/hotspots/ca_records에 빈 sample_id가 있습니다")
        if ref not in seen:
            raise schema.DataContractError(f"부속 기록에 알 수 없는 sample_id {ref!r}가 있습니다")
    for ref in geometry_payload:
        if ref not in seen:
            raise schema.DataContractError(f"geometry.json에 알 수 없는 sample_id {ref!r}가 있습니다")

    for bundle in bundles:
        sid = bundle.sample_id
        if bundle.fab_distribution_id is not None and bundle.fab_distribution_id not in distributions:
            raise schema.DataContractError(
                f"{sid}: fab_distribution_id {bundle.fab_distribution_id!r}가 Fab 입력에 없습니다"
            )
        _build_layouts(layout_rows, sid, bundle, data_dir)
        _build_hotspots(hotspot_rows, sid, bundle, data_dir, hotspot_legend)
        _build_ca(ca_rows, sid, bundle)
        _build_geometry(geometry_payload, sid, bundle)

        # 계산 버전은 bundles.csv 행 값을 우선하고 없으면 provenance.json 값을 쓴다
        calc_version = _calc_version_for(bundle, sid, bundle_rows, provenance_payload)
        calc_settings = dict(provenance_payload.get("calc_settings") or {})
        is_synthetic = bool(provenance_payload.get("is_synthetic", False))
        bundle.provenance = schema.Provenance(
            adapter="example",
            source_files=list(source_files),
            calc_settings=calc_settings,
            calc_version=calc_version,
            is_synthetic=is_synthetic,
        )

        warnings.extend(_apply_vertical_layer(bundle, layer_stack, mode))
        warnings.extend(schema.validate_bundle(bundle, mode))
        warnings.extend(_validate_image_registration(bundle, mode))

    for bundle in bundles:
        bundle.ca_spatial_sources = exported_particle_sim_sources(bundle, data_dir, provenance_payload)

    warnings.extend(schema.prepare_ca_bundles(bundles, mode, canonical_length_unit))

    return LoadedDataset(
        bundles=bundles,
        distributions=distributions,
        warnings=warnings,
        design_reviews=design_reviews,
    )
