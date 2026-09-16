"""Example file adapter: turn CSV/JSON metadata plus PNG/NPY images into bundles.

This adapter is the reference implementation of the ``docs/arch/adapter_example.md``
contract.  It reads a directory of ``bundles.csv`` / ``fab.json`` (required) plus the
optional ``layouts.csv`` / ``hotspots.csv`` / ``ca_records.csv`` / ``geometry.json`` /
``provenance.json`` files and returns a :class:`LoadedDataset` of schema objects.

Missing values are kept as ``None`` / ``NaN`` with explicit masks and are never
replaced by 0.  Contract violations raise :class:`simcompare.schema.DataContractError`
with the offending sample ids in the message.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from simcompare import schema
from simcompare.preprocess import legend_to_values

__all__ = ["LoadedDataset", "load_dataset", "read_image"]


@dataclass
class LoadedDataset:
    bundles: list[schema.AnalysisBundle]
    distributions: dict[str, schema.FabDistribution]
    warnings: list[str]


# --------------------------------------------------------------------------- #
# image loading
# --------------------------------------------------------------------------- #
def read_image(path: Path, *, kind: str, legend: dict | None) -> tuple[np.ndarray, str, str | None]:
    """Load one image file and return ``(float32 array, semantics, legend_id)``.

    ``kind`` is ``"layout"`` or ``"hotspot"``.  ``.npy`` files are loaded with
    ``allow_pickle=False`` and must be 2-D.  ``.png`` files are decoded with Pillow:
    a hotspot with an explicit legend is mapped through :func:`legend_to_values`
    (semantics ``"probability"``); every other PNG is read as grayscale ``L`` and
    scaled to ``[0, 1]``.  Layout arrays are thresholded to a binary ``{0, 1}``
    mask.  Any other suffix is a contract error.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".npy":
        try:
            arr = np.load(path, allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise schema.DataContractError(f"image {path}: {exc}") from None
        if arr.ndim != 2:
            raise schema.DataContractError(
                f"image {path} must be 2-D, got {arr.ndim}-D"
            )
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
        except (OSError, ValueError) as exc:
            raise schema.DataContractError(f"image {path}: {exc}") from None
    else:
        raise schema.DataContractError(f"unsupported image suffix {suffix!r} for {path}")

    if kind == "layout":
        arr = (arr > 0.5).astype(np.float32)
        return arr, "binary", None
    return arr, "processed_intensity", None


# --------------------------------------------------------------------------- #
# small CSV / JSON helpers
# --------------------------------------------------------------------------- #
def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _text(value: object) -> str | None:
    if _blank(value):
        return None
    return str(value).strip()


def _float(value: object, what: str = "value") -> float | None:
    if _blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise schema.DataContractError(f"{what}: not a number: {value!r}") from None


def _req_float(value: object, what: str) -> float:
    result = _float(value, what)
    if result is None:
        raise schema.DataContractError(f"{what}: required number is blank")
    return result


class _Rows(list):
    """CSV rows that remember the header, so header-only files are still checked."""

    fieldnames: list[str] = []


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = _Rows(reader)
        rows.fieldnames = list(reader.fieldnames or [])
    return rows


def _require_columns(rows: list[dict], required: list[str], what: str) -> None:
    present = set(getattr(rows, "fieldnames", None) or (rows[0].keys() if rows else []))
    missing = [name for name in required if name not in present]
    if missing:
        raise schema.DataContractError(f"{what}: missing required column(s) {missing}")


def _resolve(data_dir: Path, raw: str, sid: str) -> Path:
    """Resolve a CSV path relative to ``data_dir`` and keep it inside the tree."""
    path = Path(raw)
    if path.is_absolute():
        raise schema.DataContractError(f"{sid}: image path must be relative, got {raw!r}")
    resolved = (data_dir / path).resolve()
    root = data_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise schema.DataContractError(f"{sid}: image path escapes data_dir: {raw!r}")
    if not resolved.is_file():
        raise schema.DataContractError(f"{sid}: image file missing: {raw!r}")
    return resolved


# --------------------------------------------------------------------------- #
# fab.json
# --------------------------------------------------------------------------- #
def _load_distributions(data_dir: Path) -> dict[str, schema.FabDistribution]:
    fab_path = data_dir / "fab.json"
    if not fab_path.is_file():
        raise schema.DataContractError("fab.json is missing")
    payload = json.loads(fab_path.read_text())
    entries = payload.get("distributions")
    if not isinstance(entries, list):
        raise schema.DataContractError("fab.json: 'distributions' must be a list")

    distributions: dict[str, schema.FabDistribution] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise schema.DataContractError("fab.json: each distribution must be an object")
        did = _text(entry.get("fab_distribution_id")) or "<missing id>"

        def numbers(key: str, allow_null: bool) -> list:
            raw = entry.get(key)
            if not isinstance(raw, list):
                raise schema.DataContractError(f"{did}: {key} must be a list")
            out = []
            for item in raw:
                if item is None and allow_null:
                    out.append(None)
                elif isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise schema.DataContractError(f"{did}: {key} contains non-number {item!r}")
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
            raise schema.DataContractError(f"{did}: is_reference must be true/false")
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
            is_reference=is_reference,
        )
        schema.validate_fab(dist)
        if dist.fab_distribution_id in distributions:
            raise schema.DataContractError(
                f"duplicate fab_distribution_id {dist.fab_distribution_id!r}"
            )
        distributions[dist.fab_distribution_id] = dist
    return distributions


# --------------------------------------------------------------------------- #
# per-bundle image / curve assembly
# --------------------------------------------------------------------------- #
def _build_layouts(
    rows: list[dict],
    sid: str,
    bundle: schema.AnalysisBundle,
    data_dir: Path,
) -> None:
    by_slot: dict[str, dict] = {}
    for row in rows:
        if _text(row.get("sample_id")) != sid:
            continue
        slot = _text(row.get("slot"))
        if slot not in ("a", "b"):
            raise schema.DataContractError(f"{sid}: invalid layout slot {slot!r}")
        if slot in by_slot:
            raise schema.DataContractError(f"{sid}: duplicate layout slot {slot!r}")
        if bundle.is_open and slot == "b":
            raise schema.DataContractError(f"{sid}: open failure cannot have layout slot 'b'")
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
    selected = [row for row in rows if _text(row.get("sample_id")) == sid]
    selected.sort(key=lambda row: _req_float(row["particle_size"], f"{sid}: hotspot particle_size"))
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
                particle_size=_req_float(row["particle_size"], f"{sid}: hotspot particle_size"),
                value_semantics=semantics,
                legend_id=legend_id,
            )
        )


def _build_ca(
    rows: list[dict],
    sid: str,
    bundle: schema.AnalysisBundle,
) -> None:
    records = [
        {
            "sample_id": sid,
            "particle_size": _req_float(row["particle_size"], f"{sid}: CA particle_size"),
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
    entry = payload.get(sid)
    if not isinstance(entry, dict):
        return
    thickness = {str(k): _req_float(v, f"{sid}: thickness {k}") for k, v in (entry.get("thickness") or {}).items()}
    shape = {str(k): _req_float(v, f"{sid}: shape metric {k}") for k, v in (entry.get("shape_metrics") or {}).items()}
    bundle.geometry = schema.Geometry(
        thickness=thickness,
        shape_metrics=shape,
        length_unit=_text(entry.get("length_unit")),
    )


# --------------------------------------------------------------------------- #
# bundle row parsing
# --------------------------------------------------------------------------- #
def _parse_bundle_row(row: dict, sid: str) -> schema.AnalysisBundle:
    b_role = _text(row.get("electrode_b_role"))
    b_layer = _text(row.get("layer_b"))
    failure_type = _text(row.get("failure_type"))
    if failure_type is None:
        raise schema.DataContractError(f"{sid}: failure_type is missing")

    if failure_type in schema.OPEN_TYPES:
        if b_role is not None and b_role != schema.NOT_APPLICABLE:
            raise schema.DataContractError(
                f"{sid}: open failure requires electrode_b_role blank or N/A"
            )
        if b_layer is not None and b_layer != schema.NOT_APPLICABLE:
            raise schema.DataContractError(
                f"{sid}: open failure requires layer_b blank or N/A"
            )
        b_role = schema.NOT_APPLICABLE
        b_layer = schema.NOT_APPLICABLE
    else:
        if failure_type not in schema.FAILURE_TYPES:
            raise schema.DataContractError(f"{sid}: unknown failure_type {failure_type!r}")
        if b_role is None:
            raise schema.DataContractError(f"{sid}: short failure requires electrode_b_role")
        if b_layer is None:
            raise schema.DataContractError(f"{sid}: short failure requires layer_b")

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
        assigned_process_layer=_text(row.get("assigned_process_layer")),
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
    for row in bundle_rows:
        if _text(row.get("sample_id")) == sid:
            version = _text(row.get("calc_version"))
            if version is not None:
                return version
    return _text(provenance_payload.get("calc_version"))


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def load_dataset(
    data_dir,
    *,
    mode: str,
    hotspot_legend: dict | None = None,
) -> LoadedDataset:
    """Load one analysis directory into a :class:`LoadedDataset`."""
    data_dir = Path(data_dir)
    if mode not in schema.MODES:
        raise schema.DataContractError(f"unknown mode {mode!r}")

    bundles_path = data_dir / "bundles.csv"
    if not bundles_path.is_file():
        raise schema.DataContractError("bundles.csv is missing")

    source_files = ["bundles.csv"]
    warnings: list[str] = []

    distributions = _load_distributions(data_dir)
    source_files.append("fab.json")

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
            raise schema.DataContractError("bundles.csv: sample_id is missing")
        if sid in seen:
            raise schema.DataContractError(f"duplicate sample_id {sid!r}")
        seen.add(sid)
        bundles.append(_parse_bundle_row(row, sid))

    # optional per-sample files
    layout_rows: list[dict] = []
    hotspot_rows: list[dict] = []
    ca_rows: list[dict] = []
    geometry_payload: dict = {}
    provenance_payload: dict = {}

    layouts_path = data_dir / "layouts.csv"
    if layouts_path.is_file():
        layout_rows = _read_csv(layouts_path)
        _require_columns(layout_rows, ["sample_id", "slot", "path"], "layouts.csv")
        source_files.append("layouts.csv")

    hotspots_path = data_dir / "hotspots.csv"
    if hotspots_path.is_file():
        hotspot_rows = _read_csv(hotspots_path)
        _require_columns(
            hotspot_rows, ["sample_id", "particle_size", "path"], "hotspots.csv"
        )
        source_files.append("hotspots.csv")

    ca_path = data_dir / "ca_records.csv"
    if ca_path.is_file():
        ca_rows = _read_csv(ca_path)
        _require_columns(
            ca_rows, ["sample_id", "particle_size", "ca_value", "ca_unit"], "ca_records.csv"
        )
        source_files.append("ca_records.csv")

    geometry_path = data_dir / "geometry.json"
    if geometry_path.is_file():
        geometry_payload = json.loads(geometry_path.read_text())
        source_files.append("geometry.json")

    provenance_path = data_dir / "provenance.json"
    if provenance_path.is_file():
        provenance_payload = json.loads(provenance_path.read_text())
        source_files.append("provenance.json")

    for row in layout_rows + hotspot_rows + ca_rows:
        ref = _text(row.get("sample_id"))
        if ref is None:
            raise schema.DataContractError("blank sample_id in layouts/hotspots/ca_records")
        if ref not in seen:
            raise schema.DataContractError(f"unknown sample_id {ref!r} in optional records")
    for ref in geometry_payload:
        if ref not in seen:
            raise schema.DataContractError(f"unknown sample_id {ref!r} in geometry.json")

    for bundle in bundles:
        sid = bundle.sample_id
        if bundle.fab_distribution_id is not None and bundle.fab_distribution_id not in distributions:
            raise schema.DataContractError(
                f"{sid}: fab_distribution_id {bundle.fab_distribution_id!r} not present in fab.json"
            )
        _build_layouts(layout_rows, sid, bundle, data_dir)
        _build_hotspots(hotspot_rows, sid, bundle, data_dir, hotspot_legend)
        _build_ca(ca_rows, sid, bundle)
        _build_geometry(geometry_payload, sid, bundle)

        # calc_version comes from the bundle row, falling back to provenance.json
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

        warnings.extend(schema.validate_bundle(bundle, mode))

    return LoadedDataset(
        bundles=bundles,
        distributions=distributions,
        warnings=warnings,
    )
