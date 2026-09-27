"""Contract tests for caa (derived from the orchestrator smoke gate reproducing review findings)."""

import json
from pathlib import Path
from dataclasses import replace
import itertools
import numpy as np
import pytest
from simcompare.schema import AnalysisBundle, FabDistribution, CACurve
from simcompare.caa import select_distribution, CAAError, compute_caa, interpret_fab


def test_contract_gate():
    import numpy as np
    from simcompare.caa import (
        CAAError,
        compute_caa,
        interpret_fab,
        select_distribution,
        fab_shape_and_total,
    )
    from simcompare.schema import AnalysisBundle, CACurve, FabDistribution

    def dist(did, layer, kind="probability", family="F1", ref=False):
        return FabDistribution(
            did,
            "FAB_A",
            family,
            layer,
            np.array([0.5, 1.5]),
            np.array([1.5, 2.5]),
            np.array([0.25, 0.75]),
            np.array([True, True]),
            kind,
            "1",
            "um",
            surveyed_area=1.0 if kind == "count" else None,
            area_unit="um^2" if kind == "count" else None,
            is_reference=ref,
        )

    b = AnalysisBundle(
        "S",
        "P1",
        "F1",
        "v1",
        "L1",
        "data",
        "pix",
        "M2",
        "ITO",
        "lateral_short",
        "FAB_A",
        assigned_process_layer="ITO",
        ca=CACurve(
            np.array([1.0, 2.0]), np.array([0.04, 0.08]), np.array([True, True]), "1"
        ),
    )
    ds = {
        "d1": dist("d1", "ITO"),
        "d2": dist("d2", "M2"),
        "ref": dist("ref", "ITO", family=None, ref=True),
    }
    assert (
        select_distribution(b, ds, {"P9|lateral_short": "M2"}).fab_distribution_id
        == "d1"
    )
    assert (
        select_distribution(b, ds, {"P1|lateral_short": "M2"}).fab_distribution_id
        == "d2"
    )
    w = interpret_fab(ds["d1"], "demo_probability_per_bin", mode="demo")
    r = compute_caa(b.ca, w)
    assert np.isclose(r.value, 0.25 * 0.04 + 0.75 * 0.08) and r.is_demo
    for args in (
        (ds["d1"], "demo_probability_per_bin", "real"),
        (dist("c", "ITO", "count"), "demo_probability_per_bin", "demo"),
        (ds["d1"], None, "demo"),
        (ds["d1"], "made_up", "demo"),
    ):
        try:
            interpret_fab(args[0], args[1], mode=args[2])
            raise AssertionError(f"accepted {args[1:]}")
        except CAAError:
            pass
    b.assigned_process_layer = None
    try:
        select_distribution(b, ds, {})
        raise AssertionError("missing layer accepted")
    except CAAError:
        pass
    shape, total, unit = fab_shape_and_total(ds["d1"])
    assert np.isclose(total, 1.0) and np.allclose(shape, [0.25, 0.75])


CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures/particle_sim_mapping_v1.json").read_text()
)


def f4_bundle(**changes):
    values = dict(
        sample_id="S",
        product_id="P",
        product_family="F",
        design_version="V",
        design_lineage_id="L",
        electrode_a_role="a",
        electrode_b_role="b",
        layer_a="A",
        layer_b="B",
        failure_type="lateral_short",
        fab_id="FAB",
        assigned_process_layer="SOURCE",
        fab_pair_key="U:A-B",
        mapping_contract_version="1.2",
    )
    values.update(changes)
    return AnalysisBundle(**values)


def f4_dist(did="D", **changes):
    values = dict(
        fab_distribution_id=did,
        fab_id="FAB",
        product_family="F",
        process_layer="SOURCE",
        bin_lower=np.array([0.5, 1.5]),
        bin_upper=np.array([1.5, 2.5]),
        raw_values=np.array([0.25, 0.75]),
        observed=np.array([True, True]),
        value_kind="probability",
        value_unit="1",
        size_unit="um",
    )
    values.update(changes)
    return FabDistribution(**values)


@pytest.mark.parametrize(
    "vector", CONTRACT["selector"]["vectors"], ids=lambda v: v["name"]
)
def test_f4_selector_vectors(vector):
    bundle = f4_bundle(**vector.get("bundle", {}))
    entries = [
        f4_dist(row["id"], **{k: v for k, v in row.items() if k != "id"})
        for row in vector["candidates"]
    ]
    for ordered in itertools.permutations(entries):
        distributions = {d.fab_distribution_id: d for d in ordered}
        if vector["result"] in ("ambiguity", "no-match"):
            with pytest.raises(CAAError, match="explicit fab_distribution_id" if vector["result"] == "no-match" else "후보가 여러 개"):
                select_distribution(bundle, distributions)
        else:
            assert (
                select_distribution(bundle, distributions).fab_distribution_id
                == vector["result"]
            )


@pytest.mark.parametrize(
    "changes",
    [
        dict(fab_pair_key="other:A-B"),
        dict(product_family="OTHER"),
        dict(fab_id="OTHER"),
        dict(process_layer="OTHER"),
        dict(is_reference=True),
    ],
)
def test_f4_explicit_rejects_mismatch(changes):
    with pytest.raises(CAAError):
        select_distribution(
            f4_bundle(fab_distribution_id="D"), {"D": f4_dist(**changes)}
        )


def test_f4_explicit_and_override_and_single_caa():
    pair = f4_dist("pair", fab_pair_key="U:A-B")
    generic = f4_dist("generic", raw_values=np.array([0.9, 0.1]))
    ds = {"pair": pair, "generic": generic}
    b = f4_bundle(
        ca=CACurve(np.array([1.0, 2.0]), np.array([0.04, 0.08]), np.ones(2, bool), "1")
    )
    assert select_distribution(b, ds) is generic
    assert select_distribution(replace(b, fab_distribution_id="pair"), ds) is pair
    assert select_distribution(replace(b, fab_distribution_id="generic"), ds) is generic
    assert select_distribution(b, ds, {"P|lateral_short": "SOURCE"}) is generic
    with pytest.raises(CAAError, match="conflicts"):
        select_distribution(b, ds, {"P|lateral_short": "OTHER"})
    with pytest.raises(CAAError):
        select_distribution(replace(b, fab_distribution_id="missing"), ds)
    with pytest.raises(CAAError):
        select_distribution(
            replace(
                b,
                mapping_contract_version=None,
                fab_pair_key=None,
                fab_distribution_id="pair",
            ),
            ds,
        )
    chosen = select_distribution(replace(b, fab_distribution_id="pair"), ds)
    result = compute_caa(
        b.ca, interpret_fab(chosen, "demo_probability_per_bin", mode="demo")
    )
    assert result.value == pytest.approx(0.25 * 0.04 + 0.75 * 0.08)
    missing = replace(
        pair, raw_values=np.array([np.nan, 0.75]), observed=np.array([False, True])
    )
    assert select_distribution(replace(b, fab_distribution_id="pair"), dict(ds, pair=missing)) is missing
