"""compare2 input layer: config, density CSV tables, lambda coefficients, CRP and CNO collection.

Validation and coefficients only; no rate is computed here. Every error is a NumericContractError(code, detail).
"""
from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .numeric_schema import NumericContractError, diameter_key, finite

CONFIG_SCHEMA = 'compare2_config_v1'
CNO_FACTOR = math.pi / 4
VI_LINE_DIVISOR = 1.2159
VI_PIXEL_DIVISOR = 1.5462
ROUND_DIGITS = 6
CRP_DEFAULT_OTHER = 2.92
CRP_ONE_GROUPS = ('mobile', 'watch')
AREA_UNIT_TO_UM2 = {'mm2': 1e6, 'um2': 1.0, 'm2': 1e12}
KINDS = ('line', 'pixel')
REPORT_DEFAULTS = {'dl1_root': None, 'map_window_um': 30, 'diameter_um': 3, 'top_n': 4}
_SIZE_SUFFIXES = ('µm', 'μm', 'um')


@dataclass(frozen=True)
class GroupConfig:
    name: str
    glass_area_um2: Optional[float]
    weight_line: float
    weight_pixel: float
    glass_area_reason: Optional[str] = None

    @property
    def crp(self) -> float:
        return crp_for_group(self.name)

    def weight(self, kind: str) -> float:
        _kind(kind)
        return self.weight_line if kind == 'line' else self.weight_pixel


@dataclass(frozen=True)
class ProductConfig:
    role: str
    model_group: str
    resolution: Tuple[int, int]
    hotspot_root: Path
    unit_size_um: Optional[Tuple[float, float]] = None
    dl1_model: Optional[str] = None


@dataclass(frozen=True)
class ReportConfig:
    dl1_root: Optional[Path]
    map_window_um: float
    diameter_um: float
    top_n: int


@dataclass(frozen=True)
class Compare2Config:
    numeric_config: Path
    output_dir: Path
    density_dir: Path
    model_groups: Dict[str, GroupConfig]
    reference: ProductConfig
    target: ProductConfig
    report: ReportConfig
    config_path: Path
    config_sha256: str


def _invalid(detail: str):
    return NumericContractError('invalid_compare2_config', detail)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise _invalid(f'kind must be line or pixel: {kind!r}')
    return kind


def _mapping(value, name: str, allowed: set, required: set) -> dict:
    if not isinstance(value, dict):
        raise _invalid(f'{name} must be a mapping')
    unknown = sorted(str(key) for key in set(value) - allowed)
    if unknown:
        raise _invalid(f'{name}: unknown keys {", ".join(unknown)}')
    missing = sorted(required - set(value))
    if missing:
        raise _invalid(f'{name}: missing keys {", ".join(missing)}')
    return value


def _string(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f'{name} must be a non-empty string')
    return value


def _positive(value, name: str) -> float:
    try:
        return finite(value, name, positive=True)
    except NumericContractError:
        raise _invalid(f'{name} must be a positive finite number') from None


def _pair(value, name: str, *, integer: bool) -> tuple:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _invalid(f'{name} must be a list of two values')
    if integer:
        if any(type(item) is not int or item <= 0 for item in value):
            raise _invalid(f'{name} must be two positive integers')
        return tuple(value)
    return tuple(_positive(item, name) for item in value)


def _path(base: Path, value, name: str) -> Path:
    path = Path(_string(value, name))
    return path if path.is_absolute() else base / path


def crp_for_group(name: str) -> float:
    """CRP: 1.0 for the mobile and watch groups (any case), 2.92 for every other group."""
    return 1.0 if str(name).lower() in CRP_ONE_GROUPS else CRP_DEFAULT_OTHER


def _group(name, raw) -> GroupConfig:
    if not isinstance(name, str) or not name.strip() or '/' in name or '\\' in name or '..' in name:
        raise _invalid(f'model group name is not usable as a file name: {name!r}')
    raw = _mapping(raw, f'model_groups.{name}', {'glass_area', 'weight_line', 'weight_pixel'},
                   {'glass_area', 'weight_line', 'weight_pixel'})
    area = raw['glass_area']
    if not isinstance(area, dict):
        raise _invalid(f'model_groups.{name}.glass_area must be a mapping')
    area = _mapping(area, f'model_groups.{name}.glass_area', {'value', 'unit'}, {'value'})
    unit = area.get('unit')
    if unit is not None and unit not in AREA_UNIT_TO_UM2:
        raise NumericContractError('invalid_glass_area', f'{name}: unit must be one of mm2, um2, m2')
    if area['value'] is None:
        glass, reason = None, 'glass_area_pending'
    else:
        if unit is None:
            raise NumericContractError('invalid_glass_area', f'{name}: unit is required when value is set')
        try:
            value = finite(area['value'], 'glass_area', positive=True)
            glass = finite(value * AREA_UNIT_TO_UM2[unit], 'glass_area', positive=True)
        except NumericContractError:
            raise NumericContractError('invalid_glass_area', f'{name}: value must be a positive finite number') from None
        reason = None
    return GroupConfig(name, glass, _positive(raw['weight_line'], f'{name}.weight_line'),
                       _positive(raw['weight_pixel'], f'{name}.weight_pixel'), reason)


def _product(base: Path, role: str, raw, groups: dict) -> ProductConfig:
    raw = _mapping(raw, f'products.{role}', {'model_group', 'resolution', 'hotspot_root', 'unit_size_um', 'dl1_model'},
                   {'model_group', 'resolution', 'hotspot_root'})
    group = _string(raw['model_group'], f'products.{role}.model_group')
    if group not in groups:
        raise _invalid(f'products.{role}.model_group {group!r} is not in model_groups')
    unit = raw.get('unit_size_um')
    dl1_model = raw.get('dl1_model')
    return ProductConfig(role, group, _pair(raw['resolution'], f'products.{role}.resolution', integer=True),
                         _path(base, raw['hotspot_root'], f'products.{role}.hotspot_root'),
                         None if unit is None else _pair(unit, f'products.{role}.unit_size_um', integer=False),
                         None if dl1_model is None else _string(dl1_model, f'products.{role}.dl1_model'))


def _report(base: Path, raw) -> ReportConfig:
    if raw is None:
        raw = {}
    raw = _mapping(raw, 'report', set(REPORT_DEFAULTS), set())
    top_n = raw.get('top_n', REPORT_DEFAULTS['top_n'])
    if type(top_n) is not int or top_n <= 0:
        raise _invalid('report.top_n must be a positive integer')
    dl1 = raw.get('dl1_root')
    return ReportConfig(None if dl1 is None else _path(base, dl1, 'report.dl1_root'),
                        _positive(raw.get('map_window_um', REPORT_DEFAULTS['map_window_um']), 'report.map_window_um'),
                        _positive(raw.get('diameter_um', REPORT_DEFAULTS['diameter_um']), 'report.diameter_um'), top_n)


def load_compare2_config(path) -> Compare2Config:
    import yaml
    path = Path(path).resolve()
    raw_bytes = path.read_bytes()
    try:
        cfg = yaml.safe_load(raw_bytes.decode('utf-8'))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise _invalid(str(exc)) from exc
    cfg = _mapping(cfg, 'config', {'schema_version', 'numeric_config', 'output_dir', 'density_dir', 'model_groups',
                                   'products', 'report'},
                   {'schema_version', 'numeric_config', 'output_dir', 'density_dir', 'model_groups', 'products'})
    if cfg['schema_version'] != CONFIG_SCHEMA:
        raise NumericContractError('unsupported_compare2_config', f'use schema_version: {CONFIG_SCHEMA}')
    base = path.parent
    if not isinstance(cfg['model_groups'], dict) or not cfg['model_groups']:
        raise _invalid('model_groups must be a non-empty mapping')
    groups = {name: _group(name, raw) for name, raw in cfg['model_groups'].items()}
    products = _mapping(cfg['products'], 'products', {'reference', 'target'}, {'reference', 'target'})
    return Compare2Config(
        numeric_config=_path(base, cfg['numeric_config'], 'numeric_config'),
        output_dir=_path(base, cfg['output_dir'], 'output_dir'),
        density_dir=_path(base, cfg['density_dir'], 'density_dir'),
        model_groups=groups,
        reference=_product(base, 'reference', products['reference'], groups),
        target=_product(base, 'target', products['target'], groups),
        report=_report(base, cfg.get('report')),
        config_path=path, config_sha256=hashlib.sha256(raw_bytes).hexdigest())


def lambda_factor(product: ProductConfig, group: GroupConfig, kind: str) -> Optional[float]:
    """resolution_x * resolution_y * weight_kind / (4 * glass_area_um2); None while the glass area is pending.

    lambda = sum(density * CA / 4 / A_unit * N * A_unit / A_glass * w): the unit area cancels (PLAN section 7, D3).
    Use it as: lambda += density(count/glass) * CA(um^2, observed in the 2x2 crop) * factor.
    The factor already holds the /4; never divide CA by the 2x2 area again.
    """
    _kind(kind)
    if group.glass_area_um2 is None:
        return None
    width, height = product.resolution
    return width * height * group.weight(kind) / (4 * group.glass_area_um2)


def cno_lambda_factor(product: ProductConfig, group: GroupConfig) -> Optional[float]:
    factor = lambda_factor(product, group, 'pixel')
    return None if factor is None else factor * CNO_FACTOR


@dataclass(frozen=True)
class DensityTable:
    path: Path
    sha256: str
    sizes: Tuple[Decimal, ...]
    values: Dict[str, Dict[Decimal, Optional[float]]] = field(repr=False)

    @property
    def layers(self) -> Tuple[str, ...]:
        return tuple(self.values)

    def lookup(self, layer: str, diameter) -> Tuple[Optional[float], Optional[str]]:
        """(value, None), or (None, reason) with reason density_layer_missing / density_size_missing / density_value_missing."""
        row = self.values.get(layer)
        if row is None:
            return None, 'density_layer_missing'
        key = size_key(diameter)
        if key not in row:
            return None, 'density_size_missing'
        value = row[key]
        return (value, None) if value is not None else (None, 'density_value_missing')

    def get(self, layer: str, diameter) -> Optional[float]:
        return self.lookup(layer, diameter)[0]


def size_key(value) -> Decimal:
    """Canonical size key: 3, 3.0, '3' and Decimal('3.0') all give Decimal('3'); 1.5 gives Decimal('1.5')."""
    if isinstance(value, Decimal):
        if not value.is_finite() or value <= 0:
            raise NumericContractError('invalid_number', 'diameter')
        number = value
    else:
        number = diameter_key(value)
    if number == number.to_integral_value():
        return Decimal(int(number))
    return number.normalize()


def _size_header(cell: str, path: Path) -> Decimal:
    text = cell.replace(' ', '').replace('\t', '')
    for suffix in _SIZE_SUFFIXES:
        if text.lower().endswith(suffix.lower()):
            text = text[:-len(suffix)]
            break
    try:
        return size_key(float(text))
    except (ValueError, NumericContractError):
        raise NumericContractError('invalid_density_csv', f'{path.name}: bad size header {cell!r}') from None


def _read_density(path: Path) -> DensityTable:
    if not path.is_file():
        raise NumericContractError('density_file_missing', str(path))
    raw = path.read_bytes()
    try:
        rows = list(csv.reader(raw.decode('utf-8-sig').splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise NumericContractError('invalid_density_csv', f'{path.name}: {exc} (CSV UTF-8 로 저장하세요)') from exc
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows or len(rows[0]) < 2:
        raise NumericContractError('invalid_density_csv', f'{path.name}: header needs at least one size column')
    sizes: List[Decimal] = []
    for cell in rows[0][1:]:
        size = _size_header(cell, path)
        if size in sizes:
            raise NumericContractError('duplicate_density_size', f'{path.name}: {cell.strip()}')
        sizes.append(size)
    values: Dict[str, Dict[Decimal, Optional[float]]] = {}
    for row in rows[1:]:
        if len(row) != len(rows[0]):
            raise NumericContractError('invalid_density_csv', f'{path.name}: expected {len(rows[0])} columns, got {len(row)}')
        layer = row[0].strip()
        if not layer:
            raise NumericContractError('invalid_density_csv', f'{path.name}: empty layer name')
        if layer in values:
            raise NumericContractError('duplicate_density_layer', f'{path.name}: {layer}')
        parsed: Dict[Decimal, Optional[float]] = {}
        for size, cell in zip(sizes, row[1:]):
            cell = cell.strip()
            if not cell:
                parsed[size] = None
                continue
            try:
                parsed[size] = finite(float(cell), 'density', nonnegative=True)
            except (ValueError, NumericContractError):
                raise NumericContractError('invalid_density_value', f'{path.name}: {layer}/{size}: {cell!r}') from None
        values[layer] = parsed
    return DensityTable(path, hashlib.sha256(raw).hexdigest(), tuple(sizes), values)


def load_density(density_dir, group: str) -> Dict[str, DensityTable]:
    """{'line': table, 'pixel': table} from <group>_line.csv and <group>_pixel.csv."""
    base = Path(density_dir)
    return {kind: _read_density(_find_density(base, group, kind)) for kind in KINDS}


def _find_density(base: Path, group: str, kind: str) -> Path:
    """<group>_<kind>.csv exactly; else the single file in the folder that matches it ignoring case."""
    exact = base / f'{group}_{kind}.csv'
    wanted = exact.name.lower()
    try:  # the listing gives the real file name even on a case-insensitive file system
        found = sorted((path for path in base.iterdir() if path.name.lower() == wanted and path.is_file()),
                       key=lambda path: path.name)
    except OSError:
        return exact  # the reader reports density_file_missing
    for path in found:
        if path.name == exact.name:
            return path
    if len(found) > 1:
        raise NumericContractError('ambiguous_density_file', ', '.join(path.name for path in found))
    return found[0] if found else exact


@dataclass(frozen=True)
class CnoCase:
    case_name: str
    source_relative_path: str
    electrode_id: str
    density_layer: str
    rows: Dict[float, float]
    csv_relative_path: Optional[str]
    csv_sha256: Optional[str]
    status: str
    reason: Optional[str]
    detail: Optional[str] = None


def collect_cno(hotspot_root) -> List[CnoCase]:
    """CNO (3-part lateralopen) folders of a hotspot root, in folder-name order. Images are not read."""
    from .adapters.real_hotspot import discover_cno_cases
    cases = []
    for item in discover_cno_cases(Path(hotspot_root)):
        info = item['csv']
        rows = {float(key): row['value'] for key, row in info['rows'].items()}
        cases.append(CnoCase(item['case_name'], item['source_relative_path'], item['electrode_id'],
                             item['actual_layer'], rows, info.get('relative_path'), info['sha256'],
                             info['status'], info['reason'], info.get('detail')))
    return cases
