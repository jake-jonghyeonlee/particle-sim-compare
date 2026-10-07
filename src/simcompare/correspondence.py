"""명시 대응표의 구조·공정·좌표 등록 자격을 검증한다.

이름/embedding으로 대응을 추론하거나 CA를 계산하지 않는다. JSON 형식 변경은
parse_correspondence, identity 정책은 resolve_pair, translation 정책은 _registration을 수정한다.
confirmed_removed는 query=null, confirmed_added는 reference=null이며 부재 쪽의
product_id/design_version context 선언이 필수다. unknown의 null은 부재 증거가 아니다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from numbers import Real
from pathlib import Path
from typing import Sequence

import numpy as np

from .schema import AnalysisBundle, DataContractError, convert_length

# ---------------------------------------------------------------------------
# 순수 계약: origin은 각 셀의 좌상단, common_xy=local_xy + translation_xy (um)
# ---------------------------------------------------------------------------
RELATIONSHIPS = {"confirmed_same_structure", "confirmed_added", "confirmed_removed", "unknown"}


@dataclass(frozen=True)
class Registration:
    """명시 공통 frame 및 translation(um). reason이 있으면 정량 등록은 불가하다."""
    frame_id: str
    query_translation_xy_um: tuple[float, float]
    reference_translation_xy_um: tuple[float, float]
    evidence: str
    reason: str | None = None


@dataclass(frozen=True)
class CorrespondencePair:
    """한 명시 pair의 방향·canonical 구조·부재 context. nullable은 수치 0이 아니다."""
    comparison_id: str
    query_sample_id: str | None
    reference_sample_id: str | None
    relationship: str
    electrodes: tuple[dict, ...]
    process: dict
    evidence: str
    query_context: dict | None
    reference_context: dict | None
    registration: Registration | None = None


@dataclass(frozen=True)
class PairResolution:
    """identity와 등록을 별도로 판정한다. global 자격에 공간 등록을 강제하지 않는다."""
    pair: CorrespondencePair | None
    query: AnalysisBundle | None
    reference: AnalysisBundle | None
    relationship: str
    identity_reason: str | None
    registration_reason: str | None


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataContractError(f"correspondence {name}: nonempty string required")
    return value


def _fields(row: object, required: set[str], optional: set[str], name: str) -> dict:
    if not isinstance(row, dict) or not required <= set(row) or set(row) - required - optional:
        raise DataContractError(f"correspondence {name}: missing or unknown fields")
    return row


def _translation(value: object, unit: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise DataContractError("registration translation must be [x,y]")
    if any(not isinstance(v, Real) or isinstance(v, bool) or not np.isfinite(v) for v in value):
        raise DataContractError("registration translation must be finite")
    return tuple(map(float, convert_length(value, unit)))


def _registration(raw: object) -> Registration | None:
    if raw is None:
        return None
    required = {"frame_id", "length_unit", "query_translation_xy", "reference_translation_xy", "evidence"}
    unsupported = {"rotation_deg", "scale", "shear", "matrix"}
    row = _fields(raw, required, unsupported, "registration")
    frame_id = _text(row["frame_id"], "frame_id")
    unit = _text(row["length_unit"], "length_unit")
    evidence = _text(row["evidence"], "registration evidence")
    query = _translation(row["query_translation_xy"], unit)
    reference = _translation(row["reference_translation_xy"], unit)
    reason = "unsupported_registration" if set(row) & unsupported else None
    return Registration(frame_id, query, reference, evidence, reason)


def _side_context(row: dict, side: str, bundle: AnalysisBundle | None) -> dict | None:
    context = row.get(f"{side}_context")
    if context is None:
        if bundle is None:
            raise DataContractError(f"absent {side} requires explicit product/design context")
        return None
    _fields(context, {"product_id", "design_version"}, set(), f"{side}_context")
    for key in ("product_id", "design_version"):
        _text(context[key], key)
        if bundle is not None and context[key] != getattr(bundle, key):
            raise DataContractError(f"{side} context disagrees with bundle")
    return copy.deepcopy(context)


def _electrodes(raw: object, query: AnalysisBundle | None, reference: AnalysisBundle | None) -> tuple[dict, ...]:
    present = query or reference
    slots = {"a"} if present.is_open else {"a", "b"}
    if not isinstance(raw, list):
        raise DataContractError("electrodes must be a slot list")
    seen, canonical = set(), set()
    layer_to_canonical = {"query": {}, "reference": {}}
    canonical_to_layer = {"query": {}, "reference": {}}
    result = []
    for entry in raw:
        _fields(entry, {"slot", "query", "reference", "canonical_electrode_id", "canonical_layer_id"},
            set(), "electrode")
        slot = _text(entry["slot"], "slot")
        if slot not in slots or slot in seen:
            raise DataContractError("duplicate or invalid electrode slot")
        seen.add(slot)
        electrode = _text(entry["canonical_electrode_id"], "canonical_electrode_id")
        canonical_layer = _text(entry["canonical_layer_id"], "canonical_layer_id")
        if electrode in canonical:
            raise DataContractError("ambiguous many-to-one canonical electrodes")
        canonical.add(electrode)
        for side, bundle in (("query", query), ("reference", reference)):
            declaration = entry[side]
            if bundle is None:
                if declaration is not None:
                    raise DataContractError("absent electrode declaration must be null")
                continue
            _fields(declaration, {"role", "layer"}, set(), side)
            actual = {"role": getattr(bundle, f"electrode_{slot}_role"), "layer": getattr(bundle, f"layer_{slot}")}
            if declaration != actual:
                raise DataContractError(f"{side} electrode/layer disagrees with bundle")
            layer = actual["layer"]
            forward = layer_to_canonical[side]
            reverse = canonical_to_layer[side]
            ambiguous = (layer in forward and forward[layer] != canonical_layer or
                         canonical_layer in reverse and reverse[canonical_layer] != layer)
            if ambiguous:
                raise DataContractError("ambiguous or conflicting canonical layer mapping")
            forward[layer] = canonical_layer
            reverse[canonical_layer] = layer
        result.append(copy.deepcopy(entry))
    if seen != slots:
        raise DataContractError("electrodes must cover every applicable slot")
    return tuple(sorted(result, key=lambda item: item["slot"]))


def _process(raw: object, query: AnalysisBundle | None, reference: AnalysisBundle | None) -> dict:
    keys = {"query_assigned_process_layer", "reference_assigned_process_layer", "canonical_process_id"}
    row = _fields(raw, keys, set(), "process")
    _text(row["canonical_process_id"], "canonical_process_id")
    for side, bundle in (("query", query), ("reference", reference)):
        value = row[f"{side}_assigned_process_layer"]
        if bundle is None:
            if value is not None:
                raise DataContractError("absent process declaration must be null")
        elif value is None or value != bundle.assigned_process_layer:
            raise DataContractError(f"{side} assigned_process_layer unconfirmed or inconsistent")
    return copy.deepcopy(row)


# ---------------------------------------------------------------------------
# JSON IO는 load에만 두고 parse/resolve는 입력을 변경하지 않는다
# ---------------------------------------------------------------------------


def parse_correspondence(payload: dict, bundles: Sequence[AnalysisBundle]) -> list[CorrespondencePair]:
    """version 1 pair JSON을 실제 bundle과 대조한다. 배열·단위 변환은 등록 좌표만 수행한다.

    electrodes는 slot/query/reference(role,layer)/canonical ID, process는 양측 assigned
    layer/canonical_process_id 형식이다. null 쪽은 context가 필요하고 CA는 생성하지 않는다.
    불일치/중복/다대일은 DataContractError, 입력 변경이나 IO는 없다.
    """
    _fields(payload, {"schema_version", "pairs"}, set(), "root")
    if payload["schema_version"] != "1" or not isinstance(payload["pairs"], list):
        raise DataContractError("correspondence requires schema_version '1' and pairs list")
    by_id = {b.sample_id: b for b in bundles}
    if len(by_id) != len(bundles):
        raise DataContractError("duplicate bundle sample_id")
    required = {"comparison_id", "query_sample_id", "reference_sample_id", "relationship",
                "electrodes", "process", "evidence"}
    optional = {"registration", "query_context", "reference_context"}
    seen, result = set(), []
    for row in payload["pairs"]:
        _fields(row, required, optional, "pair")
        cid = _text(row["comparison_id"], "comparison_id")
        if cid in seen:
            raise DataContractError("duplicate comparison_id")
        seen.add(cid)
        relation = _text(row["relationship"], "relationship")
        if relation not in RELATIONSHIPS:
            raise DataContractError("unknown relationship")
        query_id, reference_id = row["query_sample_id"], row["reference_sample_id"]
        for sid in (query_id, reference_id):
            if sid is not None and (_text(sid, "sample_id") not in by_id):
                raise DataContractError("correspondence references unknown sample_id")
        present = (query_id is not None, reference_id is not None)
        expected = {"confirmed_same_structure": (True, True), "confirmed_added": (True, False),
                    "confirmed_removed": (False, True)}
        if present == (False, False) or (relation in expected and present != expected[relation]):
            raise DataContractError("relationship conflicts with null sample side")
        query, reference = by_id.get(query_id), by_id.get(reference_id)
        if query and reference and query.failure_type != reference.failure_type:
            raise DataContractError("failure types differ")
        if (query or reference).failure_type not in ("lateral_open", "lateral_short", "vertical_short"):
            raise DataContractError("unsupported failure type")
        result.append(CorrespondencePair(
            cid, query_id, reference_id, relation, _electrodes(row["electrodes"], query, reference),
            _process(row["process"], query, reference), _text(row["evidence"], "evidence"),
            _side_context(row, "query", query), _side_context(row, "reference", reference),
            _registration(row.get("registration")),
        ))
    return sorted(result, key=lambda pair: pair.comparison_id)


def load_correspondence(path: str | Path, bundles: Sequence[AnalysisBundle]) -> list[CorrespondencePair]:
    """JSON을 읽어 parse_correspondence로 검증한다. 쓰기나 bundle 변경은 없다.

    등록 단위/원점은 명시 선언만 사용하고, 누락 대응을 이름에서 추정하지 않는다.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataContractError(f"cannot read correspondence: {exc}") from exc
    return parse_correspondence(payload, bundles)


def resolve_pair(query: AnalysisBundle | None, reference: AnalysisBundle | None,
                 pair: CorrespondencePair | None = None) -> PairResolution:
    """방향이 있는 pair의 identity/등록 상태를 순수하게 반환한다. 배열/CA 계산은 없다.

    같은 이름/shape여도 선언 없이는 unknown이다. null은 결측 또는 명시 구조 부재다.
    좌표는 명시 translation만 쓰고, 등록 미확인 시에도 global scalar는 남는다. IO는 없다.
    """
    if pair is None:
        return PairResolution(None, query, reference, "unknown", "unconfirmed_correspondence", "unregistered")
    actual_ids = (query.sample_id if query else None, reference.sample_id if reference else None)
    if actual_ids != (pair.query_sample_id, pair.reference_sample_id):
        raise DataContractError("resolved bundles disagree with correspondence direction")
    # 대응과 등록은 별도 증거가 필요하다. 이미지 크기가 같아도 공통 원점은 아니다.
    identity = None if pair.relationship == "confirmed_same_structure" else pair.relationship
    registration = pair.registration
    reason = registration.reason if registration else "unregistered"
    return PairResolution(pair, query, reference, pair.relationship, identity, reason)
