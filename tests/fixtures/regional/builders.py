"""지역 계산의 작은 합성 fixture를 만든다. 실제 정확도/수율 근거를 생성하지 않는다.

원천/대응표 반례를 바꾸려면 make_bundle/pair_payload를 수정한다. 파일 IO는 없다.
"""
from typing import Mapping, Sequence

import numpy as np

from simcompare.schema import AnalysisBundle, CACurve, ImageInput, Provenance
from simcompare.regional_schema import SpatialCASource, SpatialFrame
from simcompare.correspondence import CorrespondencePair, parse_correspondence

ArrayInput = np.ndarray | Sequence[Sequence[float]]


def make_bundle(
    sid: str, maps: Mapping[float, ArrayInput], *, pitch: float = 1., origin: tuple[float, float] = (0., 0.),
    masks: Mapping[float, ArrayInput] | None = None, valid: ArrayInput | None = None,
    role: str = "A", layer: str = "M1",
) -> AnalysisBundle:
    """diameter→2D density로 동일-domain scalar와 원본 위치 근거를 만든다."""
    sources, hotspots, totals = [], [], []
    settings = dict(assumption_set="fixture-assumptions", legacy_kernel=False, post_step_policy="fixture-post",
                    ca_source="ca_nominal_um2", config_hash=sid, run_id="run-"+sid)
    for diameter, array in sorted(maps.items()):
        array = np.asarray(array, dtype=float)
        valid_mask = np.ones(array.shape, bool) if valid is None else np.asarray(valid, bool)
        total = float(array[valid_mask].sum()*pitch**2)
        totals.append(total)
        sources.append(SpatialCASource(sid, float(diameter), "explicit_ca_density", "nominal", "ca_density",
            "ca_nominal_um2", "fixture-calculation-v1", array.copy(), valid_mask.copy(),
            SpatialFrame(pitch, origin, "um", "local:"+sid), total, provenance={
                "producer":"synthetic-fixture", "producer_version":"fixture-v1", "settings":dict(settings),
                "source_step":"S", "unit_id":sid, "source_files":{"fixture":"synthetic-only"}}))
        support = array > 0 if masks is None else np.asarray(masks[diameter])
        hotspots.append(ImageInput("hotspot", np.asarray(support, dtype=float), pixel_size=pitch, origin_xy=origin,
                                   particle_size=float(diameter), particle_size_unit="um"))
    shape = sources[0].density.shape
    return AnalysisBundle(sid, "product-"+sid, "family", "v1", "lineage", role, "B", layer, layer, "lateral_short", "fab",
        assigned_process_layer="S", unit_width=shape[1]*pitch, unit_height=shape[0]*pitch, physical_length_unit="um",
        hotspots=hotspots, ca=CACurve(np.array(sorted(maps), float), np.array(totals), np.ones(len(maps), bool), "um^2",
                                   calc_settings=dict(settings)), ca_spatial_sources=sources,
        provenance=Provenance("synthetic-fixture", calc_version="fixture-v1", calc_settings=dict(settings), is_synthetic=True))


def pair_payload(
    query: AnalysisBundle | None, reference: AnalysisBundle | None, *,
    relationship: str = "confirmed_same_structure", registration: bool = True,
    query_translation: tuple[float, float] = (0., 0.), reference_translation: tuple[float, float] = (0., 0.),
) -> dict:
    """양측 bundle의 실제 role/layer/process를 canonical ID에 명시적으로 연결한다."""
    present = query or reference
    electrodes = []
    for slot in (["a"] if present.is_open else ["a", "b"]):
        entry = dict(slot=slot, canonical_electrode_id="electrode-"+slot, canonical_layer_id="canonical-M1")
        for side, bundle in (("query", query), ("reference", reference)):
            entry[side] = None if bundle is None else dict(role=getattr(bundle, "electrode_"+slot+"_role"),
                                                          layer=getattr(bundle, "layer_"+slot))
        electrodes.append(entry)
    row = dict(comparison_id="comparison", query_sample_id=query.sample_id if query else None,
               reference_sample_id=reference.sample_id if reference else None, relationship=relationship,
               electrodes=electrodes, process=dict(query_assigned_process_layer=query.assigned_process_layer if query else None,
                   reference_assigned_process_layer=reference.assigned_process_layer if reference else None,
                   canonical_process_id="canonical-process"), evidence="Synthetic explicit correspondence")
    if query is None:
        row["query_context"] = dict(product_id="declared-query-product", design_version="v2")
    if reference is None:
        row["reference_context"] = dict(product_id="declared-reference-product", design_version="v0")
    if registration:
        row["registration"] = dict(frame_id="confirmed-frame", length_unit="um",
            query_translation_xy=list(query_translation), reference_translation_xy=list(reference_translation),
            evidence="Synthetic explicit translation")
    return dict(schema_version="1", pairs=[row])


def make_pair(query: AnalysisBundle | None, reference: AnalysisBundle | None, **kwargs) -> CorrespondencePair:
    """합성 명시 pair를 검증해 반환한다. 등록은 좌상단 기준 um이고 IO/수치 보정은 없다."""
    return parse_correspondence(pair_payload(query, reference, **kwargs), [b for b in (query, reference) if b])[0]
