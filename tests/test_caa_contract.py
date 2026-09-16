"""Contract tests for caa (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys
    import numpy as np
    from simcompare.caa import CAAError, compute_caa, interpret_fab, select_distribution, fab_shape_and_total
    from simcompare.schema import AnalysisBundle, CACurve, FabDistribution
    def dist(did, layer, kind="probability", family="F1", ref=False):
        return FabDistribution(did, "FAB_A", family, layer, np.array([0.5, 1.5]), np.array([1.5, 2.5]),
                               np.array([0.25, 0.75]), np.array([True, True]), kind, "1", "um",
                               surveyed_area=1.0 if kind == "count" else None, area_unit="um^2" if kind == "count" else None,
                               is_reference=ref)
    b = AnalysisBundle("S", "P1", "F1", "v1", "L1", "data", "pix", "M2", "ITO", "lateral_short", "FAB_A",
                       assigned_process_layer="ITO",
                       ca=CACurve(np.array([1.0, 2.0]), np.array([4.0, 8.0]), np.array([True, True]), "um^2"))
    ds = {"d1": dist("d1", "ITO"), "d2": dist("d2", "M2"), "ref": dist("ref", "ITO", family=None, ref=True)}
    assert select_distribution(b, ds, {"P9|lateral_short": "M2"}).fab_distribution_id == "d1"
    assert select_distribution(b, ds, {"P1|lateral_short": "M2"}).fab_distribution_id == "d2"
    w = interpret_fab(ds["d1"], "demo_probability_per_bin", mode="demo")
    r = compute_caa(b.ca, w)
    assert np.isclose(r.value, 0.25 * 4 + 0.75 * 8) and r.is_demo
    for args in ((ds["d1"], "demo_probability_per_bin", "real"), (dist("c", "ITO", "count"), "demo_probability_per_bin", "demo"),
                 (ds["d1"], None, "demo"), (ds["d1"], "made_up", "demo")):
        try:
            interpret_fab(args[0], args[1], mode=args[2]); raise AssertionError(f"accepted {args[1:]}")
        except CAAError:
            pass
    b.assigned_process_layer = None
    try:
        select_distribution(b, ds, {}); raise AssertionError("missing layer accepted")
    except CAAError:
        pass
    shape, total, unit = fab_shape_and_total(ds["d1"])
    assert np.isclose(total, 1.0) and np.allclose(shape, [0.25, 0.75])
