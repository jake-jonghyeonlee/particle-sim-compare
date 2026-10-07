"""위치 중심 보고서의 결정적 합성 입력과 실행 예제를 만든다.

학습/실제 생산자 결과/수율 근거는 만들지 않는다. 사례는 build_demo,
명시 JSON/NPY 출력은 write_demo_inputs, 실행 경로는 main을 수정한다.
테스트도 동일 builder를 사용하므로 예제와 검증 사례가 분리되지 않는다.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from simcompare.config import RegionalConfig
from simcompare.regional import write_regional_analysis
from simcompare.schema import AnalysisBundle, CACurve, ImageInput, Provenance

# ---------------------------------------------------------------------------
# 합성 계약: 셀 한 개가 1 um², 모든 배열은 [y,x], 좌상단 기준이다
# ---------------------------------------------------------------------------
SHAPE = (12, 30)
PITCH_UM = 1.
ORIGIN_UM = (10., 20.)
SETTINGS = dict(ca_source="ca_nominal_um2", assumption_set="synthetic-demo",
                legacy_kernel=False, post_step_policy="synthetic-no-post-step")


@dataclass(frozen=True)
class DemoInputs:
    """IO 없는 합성 bundle과 상대 경로 NPY/명시 대응·원천 문서."""
    bundles: tuple[AnalysisBundle, ...]
    arrays: dict[str, np.ndarray]
    source_manifest: dict
    correspondence: dict


def _bundle(sid: str, maps: dict[float, np.ndarray], origin: tuple[float, float]) -> AnalysisBundle:
    sizes = sorted(maps)
    hotspots = [ImageInput("hotspot", maps[d].copy(), pixel_size=PITCH_UM, origin_xy=origin,
                           particle_size=d, particle_size_unit="um") for d in sizes]
    layout_a, layout_b = np.zeros(SHAPE), np.zeros(SHAPE)
    layout_a[:, 3:5], layout_b[:, 14:16] = 1., 1.
    layouts = [ImageInput("a", layout_a, pixel_size=PITCH_UM, origin_xy=origin),
               ImageInput("b", layout_b, pixel_size=PITCH_UM, origin_xy=origin)]
    return AnalysisBundle(
        sid, "synthetic-product-" + sid, "synthetic-family", "v1", "synthetic-lineage",
        "electrode-a", "electrode-b", "M1", "M1", "lateral_short", "synthetic-fab",
        assigned_process_layer="S", unit_width=SHAPE[1] * PITCH_UM, unit_height=SHAPE[0] * PITCH_UM,
        physical_length_unit="um", layouts=layouts, hotspots=hotspots,
        ca=CACurve(np.array(sizes), np.array([maps[d].sum() * PITCH_UM**2 for d in sizes]),
                   np.ones(len(sizes), bool), "um^2", calc_settings=dict(SETTINGS)),
        provenance=Provenance("synthetic-regional-demo", calc_version="synthetic-v1",
                              calc_settings=dict(SETTINGS), is_synthetic=True),
    )


def _pair(cid: str, query: AnalysisBundle | None, reference: AnalysisBundle | None,
          relationship: str = "confirmed_same_structure", registered: bool = True) -> dict:
    electrodes = []
    for slot in ("a", "b"):
        row = dict(slot=slot, canonical_electrode_id="canonical-" + slot, canonical_layer_id="canonical-M1")
        for side, bundle in (("query", query), ("reference", reference)):
            row[side] = None if bundle is None else dict(role=getattr(bundle, "electrode_" + slot + "_role"),
                                                        layer=getattr(bundle, "layer_" + slot))
        electrodes.append(row)
    result = dict(comparison_id=cid, query_sample_id=query.sample_id if query else None,
                  reference_sample_id=reference.sample_id if reference else None, relationship=relationship,
                  electrodes=electrodes, evidence="합성 명시 대응 · 실제 생산 자료 아님",
                  process=dict(query_assigned_process_layer="S" if query else None,
                               reference_assigned_process_layer="S" if reference else None,
                               canonical_process_id="synthetic-S"))
    for side, bundle in (("query", query), ("reference", reference)):
        if bundle is None:
            result[side + "_context"] = dict(product_id="synthetic-absent-context", design_version="declared-v0")
    if registered:
        result["registration"] = dict(
            frame_id="synthetic-common-" + cid, length_unit="um", query_translation_xy=[0., 0.],
            reference_translation_xy=[0., 0.], evidence="합성 동일 격자 선언",
        )
    return result


def build_demo() -> DemoInputs:
    """원본 binary nominal 배열과 명시 pair/manifest를 결정적으로 반환한다.

    1 um 등방 셀, [y,x], 좌상단 (10,20) um이다. 전체 100→80인데 고정 창 20→30,
    지름 곡선 교차, 이동/신규/소멸, 원천 누락, 미등록, 구조 추가/소멸을 포함한다.
    누락 원천을 0으로 만들지 않는다. 파일 IO/학습/입력 변경은 없다.
    """
    arrays, rows, bundles = {}, [], []

    def add(sid: str, maps: dict[float, np.ndarray], *, origin=ORIGIN_UM, available=True) -> AnalysisBundle:
        bundle = _bundle(sid, maps, origin)
        bundles.append(bundle)
        if available:
            for diameter, array in sorted(maps.items()):
                path = f"maps/{sid}-{diameter:g}.npy"
                arrays[path] = array.copy()
                rows.append(dict(
                    sample_id=sid, diameter=diameter, diameter_unit="um",
                    kind="explicit_ca_density", mode="nominal", semantics="ca_density", ca_source="ca_nominal_um2",
                    calculation_contract_id="synthetic-nominal-v1", density_path=path,
                    origin_xy=list(origin), pixel_size=PITCH_UM, length_unit="um", frame_id="local:" + sid,
                    producer="synthetic-regional-demo", version="synthetic-v1", source_files=[path],
                    evidence="합성 binary plane의 전체 합과 동일 CA · 실제 particle-sim 결과 아님",
                    source_step="S", unit_id=sid, settings=dict(SETTINGS),
                ))
        return bundle

    def nominal(local_height: int, distant_height: int) -> np.ndarray:
        array = np.zeros(SHAPE)
        array[:local_height, :10], array[:distant_height, 20:] = 1., 1.
        return array

    reference = add("nominal-reference", {1.: nominal(2, 8), 2.: nominal(4, 6), 3.: nominal(3, 7)})
    query = add("nominal-query", {1.: nominal(3, 5), 2.: nominal(2, 9), 3.: nominal(5, 4)})

    def spots(locations: tuple[tuple[int, int], ...]) -> np.ndarray:
        array = np.zeros(SHAPE)
        for y, x in locations:
            array[y:y + 2, x:x + 2] = 1.
        return array

    moved_ref = add("moved-reference", {1.: spots(((2, 2), (8, 20))), 2.: spots(((2, 2), (8, 20)))})
    moved_query = add("moved-query", {1.: spots(((2, 10), (8, 26))), 2.: spots(((3, 10), (8, 26)))})
    add("source-unavailable", {1.: spots(((3, 4),))}, available=False)
    unregistered_ref = add("unregistered-reference", {1.: spots(((2, 2),))})
    unregistered_query = add("unregistered-query", {1.: spots(((2, 10),))}, origin=(100., 120.))
    added = add("confirmed-added", {1.: spots(((4, 6),))})
    removed = add("confirmed-removed", {1.: spots(((4, 6),))})
    pairs = [_pair("total-down-local-up", query, reference), _pair("moved-new-disappeared", moved_query, moved_ref),
             _pair("unregistered", unregistered_query, unregistered_ref, registered=False),
             _pair("added", added, None, "confirmed_added", registered=False),
             _pair("removed", None, removed, "confirmed_removed", registered=False)]
    return DemoInputs(
        tuple(bundles), arrays, dict(schema_version="1", sources=rows), dict(schema_version="1", pairs=pairs),
    )


# ---------------------------------------------------------------------------
# 예제 파일 IO와 실행: training 없이 동일 regional 진입점을 호출한다
# ---------------------------------------------------------------------------
def write_demo_inputs(inputs: DemoInputs, output_dir: Path) -> RegionalConfig:
    """합성 NPY/JSON을 output_dir/inputs에 쓰고 실행 설정을 반환한다.

    manifest 경로는 JSON 디렉터리 상대 경로, 단위/원점은 builder 그대로다.
    기존 입력이나 repo 데이터를 바꾸지 않고 지정 디렉터리만 생성/덮어쓴다.
    """
    root = Path(output_dir) / "inputs"
    (root / "maps").mkdir(parents=True, exist_ok=True)
    for path, array in inputs.arrays.items():
        np.save(root / path, array, allow_pickle=False)
    for name, payload in (
        ("source_manifest.json", inputs.source_manifest), ("correspondence.json", inputs.correspondence),
    ):
        (root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return RegionalConfig(source_manifest=str(root / "source_manifest.json"),
                          correspondence_file=str(root / "correspondence.json"),
                          display_context_margin_um=2., include_area_normalized=True)


def main() -> None:
    """기본 outputs/regional-demo에 합성 보고서를 쓰고 생성 파일 목록을 출력한다."""
    parser = argparse.ArgumentParser(description="학습 없는 합성 지역 CA 보고서")
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "outputs/regional-demo",
    )
    args = parser.parse_args()
    inputs = build_demo()
    cfg = write_demo_inputs(inputs, args.output_dir)
    result = write_regional_analysis(inputs.bundles, cfg, args.output_dir)
    print(json.dumps({"synthetic": True, "outputs": [str(p) for p in result.outputs]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
