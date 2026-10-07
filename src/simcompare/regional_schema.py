"""지역 CA의 좌표·원천·측정 계약을 정의한다.

계산, 파일 읽기, 대응 추론은 하지 않는다. 필드 의미를 바꾸려면 이 모듈을,
적분 규칙은 regional_measurement, 생산자 연결은 adapters.regional을 수정한다.
배열은 [y,x], 원점은 [0,0] 셀의 좌상단 모서리이며 x 오른쪽/y 아래쪽이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from .regional_comparison import RegionalComparison

# ---------------------------------------------------------------------------
# 수치 오차 정책: float32 원천의 float64 적분 및 0 근처 면적 오차를 허용한다.
# ---------------------------------------------------------------------------
DEFAULT_CONSERVATION_RTOL = 1e-6
DEFAULT_CONSERVATION_ATOL_UM2 = 1e-9
SourceKind = Literal["particle_sim_nominal", "particle_sim_mean", "explicit_ca_density"]


@dataclass(frozen=True)
class SpatialFrame:
    """원본 격자. pitch와 origin은 length_unit 단위이며 None은 미확인이다.

    frame_id는 로컬 좌표계 이름으로 제품 간 등록 증거가 아니다.
    tuple pitch는 진단용으로 보존하되 v1 계산에서 지원하지 않는다.
    """
    pixel_size: float | tuple[float, float] | None
    origin_xy: tuple[float, float] | None
    length_unit: str = "um"
    frame_id: str | None = None


@dataclass(frozen=True)
class PhysicalWindow:
    """half-open [x0,x1) × [y0,y1) 물리 창. 좌표는 length_unit 단위다."""
    x0: float
    y0: float
    x1: float
    y1: float
    length_unit: str = "um"


@dataclass(frozen=True)
class SourceValidation:
    """전체 valid domain의 보존 gate와 원천 추적 정보. 실패 수치는 None이다."""
    status: str
    reason: str | None
    source_total_um2: float | None
    integrated_total_um2: float | None
    residual_um2: float | None
    conservation_rtol: float
    conservation_atol_um2: float
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpatialCASource:
    """동일 sample/step/unit/지름에서 CA를 생성한 원본 이산 density.

    density는 2D [y,x]의 셀 내 상수 [0,1], valid_mask는 동일 shape bool이다.
    nominal은 정확한 0/1이다. total_ca는 total_ca_unit 면적이며 valid domain과
    동일 계산의 scalar여야 한다. 결측·미검증은 unavailable_reason으로 남긴다.
    dataclass는 IO나 배열 복사/변경을 하지 않는다.
    """
    sample_id: str
    diameter_um: float
    source_kind: SourceKind
    source_mode: str
    semantics: str
    ca_source: str
    calculation_contract_id: str | None
    density: np.ndarray | None
    valid_mask: np.ndarray | None
    frame: SpatialFrame
    total_ca: float | None
    total_ca_unit: str = "um^2"
    provenance: dict = field(default_factory=dict)
    unavailable_reason: str | None = None
    validation: SourceValidation | None = None


@dataclass(frozen=True)
class RegionMeasurement:
    """한 원천의 창 또는 창 union 측정. raw/share/면적당 값은 별도 분모다.

    requested_windows_um은 요청 영역, clipped_bounds_um은 격자와의 bbox 교집합,
    valid_roi_area_um2는 hole을 제외한 실제 union 면적이다. unavailable은 0이 아니다.
    """
    status: str
    reason: str | None
    roi_ca_um2: float | None
    total_ca_um2: float | None
    contribution_share: float | None
    share_reason: str | None
    roi_ca_per_valid_area: float | None
    normalized_reason: str | None
    valid_roi_area_um2: float | None
    requested_windows_um: tuple[PhysicalWindow, ...]
    clipped_bounds_um: PhysicalWindow | None
    validation: SourceValidation


# ---------------------------------------------------------------------------
# 보고 계약/JSON 변환: 순수 공유 계층이라 계산기와 렌더러 순환 import를 막는다
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RegionalAnalysis:
    """배열 없는 순수 보고 결과. 원본 좌표/원천 추적 정보와 결측 사유를 보존한다."""
    schema_version: str
    algorithm_version: str
    config: dict
    fingerprint: str
    samples: tuple[dict, ...]
    comparisons: tuple[RegionalComparison, ...]
    source_validations: tuple[dict, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class RegionalOutput:
    """작성한 파일 목록과 기존 run_record에 추가할 지역 실행 기록."""
    outputs: tuple[Path, ...]
    run_record: dict


def json_safe(value: object) -> object:
    """보고 dict/list/NumPy scalar의 비유한 값을 null로 변환한다. IO/입력 변경은 없다.

    결측 reason은 계산 계약에 별도로 남으며 NaN을 수치 0으로 바꾸지 않는다.
    배열은 보고 계약에 넣지 않지만 설정/provenance의 작은 배열은 list로 변환한다.
    """
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, Path):
        return str(value)
    return value

