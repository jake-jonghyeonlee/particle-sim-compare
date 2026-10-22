"""particle-sim ``export-simcompare`` 결과 여러 개를 simcompare data_dir 하나로 합친다.

원안과 재시뮬레이션한 변경본, 또는 여러 제품의 export를 한 번에 분석하기 위한 도구다.
export가 쓰는 파일만 다루고 모르는 파일이 있으면 멈춘다(조용히 빠뜨리지 않는다).
Fab 자료(fab.json, fab_measurements.csv)와 design_review_status.csv는 합친 뒤 data_dir에 추가한다.

- 배열 파일은 출처마다 arrays/<nn>_<폴더 이름>/ 아래로 복사하고 CSV의 path를 고친다
  (particle-sim의 layout 파일 이름은 전극 ID라 제품끼리 겹칠 수 있다).
- sample_id, geometry 키, sample_id_mapping이 겹치면 오류다.
- layer_stack.csv의 같은 (제품, 버전, layer) 행이 다르면 오류, 같으면 한 번만 쓴다.
- provenance의 mapping_contract_version과 is_synthetic은 모두 같아야 한다. calc_settings는 모두
  같은 키만 남기고 출처별 원본을 merged_sources에 보존한다.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from simcompare.schema import DataContractError

TABLES = ("bundles.csv", "layouts.csv", "hotspots.csv", "ca_records.csv", "layer_stack.csv")
KNOWN = set(TABLES) | {"geometry.json", "provenance.json", "export_warnings.json", "arrays"}
PATH_TABLES = ("layouts.csv", "hotspots.csv")


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError(f"{path}: JSON을 읽을 수 없습니다: {exc}") from exc


def _rebase(rel: str, prefix: str, source: Path) -> str:
    parts = Path(rel).parts
    if not parts or parts[0] != "arrays" or ".." in parts or Path(rel).is_absolute():
        raise DataContractError(f"{source}: arrays/ 아래 상대 경로만 합칠 수 있습니다: {rel!r}")
    return str(Path("arrays", prefix, *parts[1:]))


def merge_exports(sources: list[Path], out_dir: Path) -> dict:
    """export 폴더들을 out_dir 하나로 합치고 요약을 반환한다."""
    sources = [Path(s) for s in sources]
    out_dir = Path(out_dir)
    if len(sources) < 2:
        raise DataContractError("합칠 export 폴더를 두 개 이상 지정해야 합니다")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise DataContractError(f"출력 폴더가 비어 있지 않습니다: {out_dir}")
    for source in sources:
        if not (source / "bundles.csv").is_file():
            raise DataContractError(f"{source}: bundles.csv가 없습니다(export-simcompare 결과가 아님)")
        unknown = sorted(p.name for p in source.iterdir() if p.name not in KNOWN)
        if unknown:
            raise DataContractError(f"{source}: 합칠 수 없는 파일이 있습니다: {unknown}")

    fields: dict[str, list[str]] = {}
    rows: dict[str, list[dict]] = {name: [] for name in TABLES}
    geometry: dict = {}
    provenances, warnings, copies = [], [], []
    stack_seen: dict[tuple, dict] = {}
    sample_ids: set[str] = set()
    for index, source in enumerate(sources, start=1):
        prefix = f"{index:02d}_{source.name}"
        for name in TABLES:
            path = source / name
            if not path.is_file():
                continue
            header, table = _read_csv(path)
            if name in fields and fields[name] != header:
                raise DataContractError(f"{source}/{name}: 열 구성이 다른 export와 다릅니다")
            fields[name] = header
            for row in table:
                if name == "bundles.csv":
                    if row["sample_id"] in sample_ids:
                        raise DataContractError(f"sample_id가 겹칩니다: {row['sample_id']!r}")
                    sample_ids.add(row["sample_id"])
                if name in PATH_TABLES:
                    copies.append((source / row["path"], row["path"], prefix))
                    row = {**row, "path": _rebase(row["path"], prefix, source)}
                if name == "layer_stack.csv":
                    key = (row["product_id"], row["design_version"], row["layer_name"])
                    if key in stack_seen:
                        if stack_seen[key] != row:
                            raise DataContractError(f"layer_stack.csv {key} 행이 export마다 다릅니다")
                        continue
                    stack_seen[key] = row
                rows[name].append(row)
        if (source / "geometry.json").is_file():
            for key, value in _json(source / "geometry.json").items():
                if key in geometry:
                    raise DataContractError(f"geometry.json 키가 겹칩니다: {key!r}")
                geometry[key] = value
        if (source / "provenance.json").is_file():
            provenances.append((source, _json(source / "provenance.json"), prefix))
        if (source / "export_warnings.json").is_file():
            warnings += [f"[{source.name}] {w}" for w in _json(source / "export_warnings.json")]

    out_dir.mkdir(parents=True, exist_ok=True)
    for src, rel, prefix in dict.fromkeys(copies):
        if not src.is_file():
            raise DataContractError(f"배열 파일이 없습니다: {src}")
        dest = out_dir / _rebase(rel, prefix, src.parent)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    for name in TABLES:
        if name in fields:
            _write_csv(out_dir / name, fields[name], rows[name])
    if geometry:
        (out_dir / "geometry.json").write_text(json.dumps(geometry, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    if provenances:
        (out_dir / "provenance.json").write_text(
            json.dumps(_merge_provenance(provenances, len(sources)), indent=2, ensure_ascii=False),
            encoding="utf-8")
    warnings.append("merge-exports: Fab 자료는 합친 data_dir에 따로 추가해야 합니다")
    (out_dir / "export_warnings.json").write_text(json.dumps(warnings, indent=2, ensure_ascii=False),
                                                  encoding="utf-8")
    return {"output": str(out_dir), "sources": [str(s) for s in sources],
            "bundles": len(rows["bundles.csv"]), "arrays": len(dict.fromkeys(copies))}


def _merge_provenance(items: list[tuple[Path, dict, str]], n_sources: int) -> dict:
    if len(items) != n_sources:
        raise DataContractError("일부 export에만 provenance.json이 있습니다")
    payloads = [p for _, p, _ in items]
    merged: dict = {}
    for key in ("mapping_contract_version", "is_synthetic"):
        values = {json.dumps(p.get(key)) for p in payloads}
        if len(values) != 1:
            raise DataContractError(f"provenance.json {key}가 export마다 다릅니다: {sorted(values)}")
        merged[key] = payloads[0].get(key)
    versions = {p.get("calc_version") for p in payloads}
    merged["calc_version"] = versions.pop() if len(versions) == 1 else None
    settings = [p.get("calc_settings") or {} for p in payloads]
    common = {k: v for k, v in settings[0].items() if all(s.get(k) == v for s in settings[1:])}
    merged["calc_settings"] = {**common, "merged_sources": [
        {"source": str(source), **(payload.get("calc_settings") or {})} for source, payload, _ in items]}
    mapping = []
    for _, payload, _ in items:
        mapping += payload.get("sample_id_mapping") or []
    ids = [m.get("sample_id") for m in mapping]
    if len(ids) != len(set(ids)):
        raise DataContractError("provenance.json sample_id_mapping이 export끼리 겹칩니다")
    if mapping:
        merged["sample_id_mapping"] = mapping
    storage = {}
    for source, payload, prefix in items:
        formats = (payload.get("array_storage") or {}).get("formats") or {}
        storage.update({_rebase(k, prefix, source): v for k, v in formats.items()})
    if storage:
        merged["array_storage"] = {"format": "per-array", "formats": storage}
    known = {"mapping_contract_version", "is_synthetic", "calc_version", "calc_settings",
             "sample_id_mapping", "array_storage"}
    for key in sorted({k for p in payloads for k in p} - known):
        values = {json.dumps(p.get(key), sort_keys=True) for p in payloads}
        if len(values) != 1:
            raise DataContractError(f"provenance.json {key}가 export마다 다릅니다")
        merged[key] = payloads[0][key]
    return merged
