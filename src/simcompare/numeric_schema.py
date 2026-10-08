"""Independent numeric_v2 types; raw identities never become classification keys."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from numbers import Real
from typing import Any, Literal
import hashlib

import numpy as np

DIAMETERS_UM = (*range(1, 31), 40, 50)
Status = Literal['available', 'partial', 'unavailable', 'not_applicable']


class NumericContractError(ValueError):
    def __init__(self, code: str, detail: str = ''):
        self.code = code
        super().__init__(f'{code}: {detail}' if detail else code)


class NumericPathError(NumericContractError):
    pass


class NumericFrameError(NumericContractError):
    pass


def text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NumericContractError('missing_identifier', name)
    return value  # Preserve whitespace, punctuation, case and Unicode in raw IDs.


def finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise NumericContractError('invalid_number', name)
    value = float(value)
    if not np.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise NumericContractError('invalid_number', name)
    return value


def _factor(unit: str) -> Decimal:
    factors = {'nm': Decimal('0.001'), 'um': Decimal('1'), 'mm': Decimal('1000')}
    if not isinstance(unit, str) or unit not in factors:
        raise NumericContractError('unsupported_length_unit', str(unit))
    return factors[unit]


def convert_length(value: Any, from_unit: str, to_unit: str = 'um'):
    factor = float(_factor(from_unit) / _factor(to_unit))
    arr = np.asarray(value)
    if arr.dtype.kind not in 'ifu' or not np.isfinite(arr).all():
        raise NumericContractError('invalid_length')
    result = arr.astype(float) * factor
    if not np.isfinite(result).all():
        raise NumericContractError('invalid_length')
    return float(result) if result.ndim == 0 else result


def convert_area(value: Any, from_unit: str) -> float:
    if not isinstance(from_unit, str) or not from_unit.endswith('^2'):
        raise NumericContractError('unsupported_area_unit', str(from_unit))
    result = finite(value, 'area', nonnegative=True) * float(_factor(from_unit[:-2])) ** 2
    return finite(result, 'converted area', nonnegative=True)


def diameter_key(value: Any, unit: str = 'um') -> Decimal:
    finite(value, 'diameter', positive=True)
    return Decimal(str(value)) * _factor(unit)


def binary_mask(value: Any) -> np.ndarray:
    """Accept exact binary only; integer 0/255 is an encoding, never a threshold."""
    try:
        arr = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise NumericContractError('invalid_mask_shape') from exc
    if arr.ndim != 2 or not arr.size:
        raise NumericContractError('invalid_mask_shape')
    if arr.dtype.kind not in 'bifu' or not np.isfinite(arr).all() or np.any(arr < 0):
        raise NumericContractError('invalid_mask_values')
    if np.all((arr == 0) | (arr == 1)):
        result = arr.astype(bool, copy=True)
    elif arr.dtype.kind in 'iu' and np.all((arr == 0) | (arr == 255)):
        result = (arr == 255)
    else:
        raise NumericContractError('nonbinary_nominal_mask')
    result.setflags(write=False)
    return result


@dataclass(frozen=True, eq=False)
class Frame:
    frame_id: str
    pixel_pitch_um: float
    origin_xy_um: tuple[float, float]
    shape_yx: tuple[int, int]
    valid: np.ndarray
    axis_order: str = 'yx'
    origin_convention: str = 'top_left_cell_edge'

    def __post_init__(self):
        text(self.frame_id, 'frame_id')
        object.__setattr__(self, 'pixel_pitch_um', finite(self.pixel_pitch_um, 'pitch', positive=True))
        if len(self.origin_xy_um) != 2:
            raise NumericFrameError('invalid_origin')
        object.__setattr__(self, 'origin_xy_um', tuple(finite(v, 'origin') for v in self.origin_xy_um))
        if len(self.shape_yx) != 2 or any(isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n <= 0 for n in self.shape_yx):
            raise NumericFrameError('invalid_frame_shape')
        object.__setattr__(self, 'shape_yx', tuple(self.shape_yx))
        finite(self.pixel_pitch_um * self.pixel_pitch_um, 'cell area', positive=True)
        if not all(np.isfinite(v) for v in self.bbox_um):
            raise NumericFrameError('invalid_physical_domain')
        valid = binary_mask(self.valid)
        if valid.shape != self.shape_yx:
            raise NumericFrameError('valid_shape_mismatch')
        object.__setattr__(self, 'valid', valid)
        if self.axis_order != 'yx' or self.origin_convention != 'top_left_cell_edge':
            raise NumericFrameError('unsupported_frame_convention')

    @property
    def bbox_um(self) -> tuple[float, float, float, float]:
        x, y = self.origin_xy_um
        h, w = self.shape_yx
        return x, y, x + w * self.pixel_pitch_um, y + h * self.pixel_pitch_um

    @property
    def valid_hash(self) -> str:
        return hashlib.sha256(self.valid.astype('uint8').tobytes()).hexdigest()


def require_same_frame(first: Frame, second: Frame) -> None:
    if (first.frame_id, first.pixel_pitch_um, first.origin_xy_um, first.shape_yx) != (
        second.frame_id, second.pixel_pitch_um, second.origin_xy_um, second.shape_yx
    ) or not np.array_equal(first.valid, second.valid):
        raise NumericFrameError('incompatible_source_frame')


@dataclass(frozen=True)
class Endpoint:
    electrode_id: str
    role: str
    actual_layer: str

    def __post_init__(self):
        for name in ('electrode_id', 'role', 'actual_layer'):
            text(getattr(self, name), name)


@dataclass(frozen=True)
class CaseIdentity:
    product_id: str
    design_version: str
    source_id: str
    process_step_id: str
    exposure_id: str
    case_id: str
    frame_id: str
    source_contract_version: str

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            text(getattr(self, name), name)


@dataclass(frozen=True)
class SourceCA:
    value_um2: float
    frame_id: str
    valid_hash: str
    basis: str = 'full_mask_valid'

    def __post_init__(self):
        finite(self.value_um2, 'source CA', nonnegative=True)
        text(self.frame_id, 'source CA frame')
        text(self.valid_hash, 'source CA valid hash')
        if self.basis != 'full_mask_valid':
            raise NumericContractError('source_ca_domain_unconfirmed')


@dataclass(frozen=True, eq=False)
class MaskRecord:
    diameter_um: float
    frame: Frame
    mask: np.ndarray | None
    input_status: str = 'observed'
    reasons: tuple[str, ...] = ()
    source_ca: SourceCA | None = None
    identity: CaseIdentity | None = None
    analysis_layer: str | None = None

    def __post_init__(self):
        if self.identity is not None and self.identity.frame_id != self.frame.frame_id:
            raise NumericFrameError('record_identity_frame_mismatch')
        if not isinstance(self.reasons, tuple) or any(not isinstance(r, str) or not r for r in self.reasons):
            raise NumericContractError('invalid_record_reasons')
        finite(self.diameter_um, 'diameter', positive=True)
        if self.input_status not in ('observed', 'missing', 'not_applicable'):
            raise NumericContractError('invalid_input_status')
        if self.input_status == 'observed':
            mask = binary_mask(self.mask)
            if mask.shape != self.frame.shape_yx:
                raise NumericFrameError('mask_shape_mismatch')
            object.__setattr__(self, 'mask', mask)
        elif self.mask is not None or not self.reasons:
            raise NumericContractError('invalid_unobserved_record')


@dataclass(frozen=True)
class Pair:
    pair_id: str
    electrode_ids: frozenset[str]
    classification: str
    table_version: str

    def __post_init__(self):
        text(self.pair_id, 'pair_id')
        text(self.table_version, 'pair_table_version')
        if len(self.electrode_ids) != 2 or self.classification not in ('line', 'pixel'):
            raise NumericContractError('invalid_pair')
        for electrode in self.electrode_ids:
            text(electrode, 'electrode_id')


@dataclass
class NumericCase:
    identity: CaseIdentity
    endpoints: tuple[Endpoint, Endpoint]
    failure_type: str
    pair_id: str | None
    classification: str | None
    analysis_layer: str
    density_process_layer: str
    records: dict[float, MaskRecord]
    raw: dict[str, Any]
    metadata: dict[str, Any]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DensityRow:
    distribution_id: str
    diameter_um: float
    raw_value: float | None
    raw_value_unit: str
    observed: bool
    surveyed_area_um2: float | None
    bin_lower_um: float | None
    bin_upper_um: float | None
    density_per_um2: float | None
    status: Status
    reasons: tuple[str, ...]
    raw: dict[str, Any]


@dataclass
class NumericDataset:
    cases: list[NumericCase]
    pairs: dict[frozenset[str], Pair]
    frames: dict[str, Frame]
    density: list[DensityRow]
    unit_domain: dict[str, Any]
    panel: dict[str, Any]
    gates: dict[str, Any]
    provenance: dict[str, Any]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Measurement:
    ca_um2: float | None
    valid_area_um2: float
    requested_area_um2: float
    ca_per_valid_area: float | None
    status: Status
    reasons: tuple[str, ...] = ()
    residual_um2: float | None = None
    source_ca_um2: float | None = None
    observed_ca_um2: float = 0.0
    identity: CaseIdentity | None = None
    basis: str = 'full_mask_valid'
    partial_value: float | None = None
    partial_ca_per_valid_area: float | None = None


@dataclass(frozen=True)
class PhysicalWindow:
    x0: float
    y0: float
    x1: float
    y1: float
    length_unit: str = 'um'

    def bounds_um(self) -> tuple[float, float, float, float]:
        bounds = tuple(convert_length([self.x0, self.y0, self.x1, self.y1], self.length_unit))
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise NumericContractError('invalid_window_bounds')
        return bounds


def current_gate_decisions():
    """2026-10-08 사용자 답변: 원천 manifest gate 선언과 적용 결정을 구별한다.

    G1/G2의 미답변 단위 및 unit 환산은 여전히 pending이다. G7a 답변은
    기록만 하며 조각 지표 구현은 별도 Task다. 근사 grid를 frame 기본값으로 쓰지 않는다.
    """
    return {
        'G1': {'status':'pending','answer':None,'reason':'density_contract_unconfirmed',
               'answered_parts':{'bin_semantics':'per_bin','meaning':'one_value_per_diameter',
                                 'bin_width_required':False,'decided_on':'2026-10-08'}},
        'G2': {'status':'pending','answer':None,'reason':'unit_area_basis_unconfirmed',
               'answered_parts':{'same_pitch_across_products':True,
                                 'same_layer_size_within_product':True,
                                 'different_pixel_counts_across_products':True,
                                 'approximate_2x2_um':100,'approximate_2x2_px':2000,
                                 'decided_on':'2026-10-08'}},
        'G4': {'status':'pending','answer':None,'reason':'crop_recipe_unconfirmed'},
        'G7a': {'status':'answered','answer':'implement_lateral_only_vertical_only_simultaneous',
                'decided_on':'2026-10-08','implementation_status':'pending_separate_task'},
        'G7b': {'status':'answered','answer':'short_only','authority':'user','decided_on':'2026-10-08'},
    }
