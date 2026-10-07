"""명시 대응/구조 부재와 unknown, 등록 정책을 검증한다."""
import copy
import json

import pytest

from simcompare.correspondence import load_correspondence, parse_correspondence, resolve_pair
from simcompare.schema import DataContractError
from fixtures.regional.builders import make_bundle, make_pair, pair_payload


def samples():
    return make_bundle("q", {1:[[1, 0]]}), make_bundle("r", {1:[[1, 0]]})


def test_matching_names_do_not_confirm_correspondence():
    q, r = samples()
    r.product_id, r.design_version, r.electrode_a_role = q.product_id, q.design_version, q.electrode_a_role
    resolution = resolve_pair(q, r)
    assert resolution.relationship == "unknown"
    assert resolution.identity_reason == "unconfirmed_correspondence"
    assert resolution.registration_reason == "unregistered"


@pytest.mark.parametrize("field", ["role", "layer", "process", "failure", "slot", "many_to_one"])
def test_mapping_validates_electrode_layer_failure_and_process(field):
    q, r = samples()
    payload = pair_payload(q, r)
    row = payload["pairs"][0]
    if field in ("role", "layer"):
        row["electrodes"][0]["reference"][field] = "different"
    elif field == "process":
        row["process"]["query_assigned_process_layer"] = "wrong"
    elif field == "failure":
        r.failure_type = "lateral_open"
    elif field == "slot":
        row["electrodes"][1]["slot"] = "a"
    else:
        row["electrodes"][1]["canonical_electrode_id"] = "electrode-a"
    with pytest.raises(DataContractError):
        parse_correspondence(payload, [q, r])


def test_explicit_mapping_enables_comparison_across_different_names(tmp_path):
    q, r = samples()
    r.electrode_a_role, r.layer_a, r.layer_b, r.assigned_process_layer = "different-role", "Other-L", "Other-L", "Other-S"
    payload = pair_payload(q, r)
    before = copy.deepcopy(payload)
    path = tmp_path/"pairs.json"
    path.write_text(json.dumps(payload))
    pair = load_correspondence(path, [q, r])[0]
    resolution = resolve_pair(q, r, pair)
    assert resolution.identity_reason is None and resolution.registration_reason is None
    assert payload == before


@pytest.mark.parametrize("relationship,missing_side", [("confirmed_added", "reference"), ("confirmed_removed", "query")])
def test_confirmed_structure_absence_requires_context_and_matching_null_side(relationship, missing_side):
    q, r = samples()
    q, r = (q, None) if missing_side == "reference" else (None, r)
    payload = pair_payload(q, r, relationship=relationship)
    bundles = [b for b in (q, r) if b]
    pair = parse_correspondence(payload, bundles)[0]
    assert getattr(pair, missing_side+"_context")["product_id"].startswith("declared-")
    row = payload["pairs"][0]
    del row[missing_side+"_context"]
    with pytest.raises(DataContractError, match="context"):
        parse_correspondence(payload, bundles)


@pytest.mark.parametrize("case", ["both_null", "relation_null", "duplicate", "unknown_id", "absent_declaration"])
def test_invalid_pair_contracts_are_rejected(case):
    q, r = samples()
    payload = pair_payload(q, r)
    row = payload["pairs"][0]
    if case == "both_null":
        row["query_sample_id"] = row["reference_sample_id"] = None
    elif case == "relation_null":
        row["relationship"] = "confirmed_added"
    elif case == "duplicate":
        payload["pairs"].append(copy.deepcopy(row))
    elif case == "unknown_id":
        row["query_sample_id"] = "missing"
    else:
        payload = pair_payload(q, None, relationship="confirmed_added")
        payload["pairs"][0]["electrodes"][0]["reference"] = dict(role="made-up", layer="M1")
    with pytest.raises(DataContractError):
        parse_correspondence(payload, [q, r])


def test_unknown_null_side_is_not_confirmed_absence():
    q, _ = samples()
    pair = make_pair(q, None, relationship="unknown")
    assert resolve_pair(q, None, pair).identity_reason == "unknown"


def test_registration_uses_units_and_rejects_rotation():
    q, r = samples()
    payload = pair_payload(q, r)
    registration = payload["pairs"][0]["registration"]
    registration.update(length_unit="nm", query_translation_xy=[1000, 2000], rotation_deg=0)
    pair = parse_correspondence(payload, [q, r])[0]
    assert pair.registration.query_translation_xy_um == (1, 2)
    assert resolve_pair(q, r, pair).registration_reason == "unsupported_registration"


def test_conflicting_layer_mapping_is_rejected():
    q, r = samples()
    payload = pair_payload(q, r)
    payload["pairs"][0]["electrodes"][1]["canonical_layer_id"] = "conflicting-layer"
    with pytest.raises(DataContractError, match="layer mapping"):
        parse_correspondence(payload, [q, r])
