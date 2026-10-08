"""Explicit synthetic per-bin density adapter; G1 real interpretation stays pending."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Iterable

from .numeric_schema import DIAMETERS_UM, DensityRow, NumericContractError, diameter_key, finite, text


@dataclass(frozen=True)
class DensityContract:
    distribution_id: str
    process_layer: str
    period: str
    basis_version: str
    is_synthetic: bool
    bin_semantics: str
    value_unit: str

    def __post_init__(self):
        for name in ('distribution_id', 'process_layer', 'period', 'basis_version', 'bin_semantics', 'value_unit'):
            text(getattr(self, name), name)
        if type(self.is_synthetic) is not bool:
            raise NumericContractError('invalid_synthetic_marker')

    @property
    def reasons(self) -> tuple[str, ...]:
        if (not self.is_synthetic or self.bin_semantics != 'per_bin'
                or self.value_unit not in ('um^-2', 'nm^-2', 'mm^-2')):
            return ('density_contract_unconfirmed',)
        return ()


def density_per_um2(value: float, unit: str) -> float:
    """Inverse-area unit conversion, never probability normalisation/bin integration."""
    factors = {'um^-2': Decimal('1'), 'nm^-2': Decimal('1000000'), 'mm^-2': Decimal('0.000001')}
    finite(value, 'density', nonnegative=True)
    if unit not in factors:
        raise NumericContractError('unsupported_density_unit', unit)
    result = float(Decimal(str(value)) * factors[unit])
    return finite(result, 'converted density', nonnegative=True)


@dataclass(frozen=True)
class DensityBin:
    diameter_um: float
    raw_value: float | None
    observed: bool
    value_per_um2: float | None
    reasons: tuple[str, ...]


@dataclass
class DensityDistribution:
    distribution_id: str
    process_layer: str
    contract: DensityContract | None
    bins: dict[float, DensityBin]
    reasons: tuple[str, ...]
    raw_records: tuple[dict, ...]

    @property
    def coverage(self) -> dict:
        return {'expected': len(DIAMETERS_UM), 'observed': sum(b.observed for b in self.bins.values()),
                'canonical_available': sum(b.value_per_um2 is not None for b in self.bins.values()),
                'missing': [d for d,b in self.bins.items() if not b.observed]}

    def to_dict(self) -> dict:
        return {'distribution_id': self.distribution_id, 'process_layer': self.process_layer,
                'contract': None if self.contract is None else asdict(self.contract),
                'canonical_unit': 'um^-2' if not self.reasons else None,
                'reasons': self.reasons, 'coverage': self.coverage,
                'bins': [asdict(self.bins[d]) for d in sorted(self.bins)],
                'raw_records':self.raw_records}


def adapt_density(records: Iterable[dict | DensityRow], *, contract: DensityContract | None = None) -> DensityDistribution:
    """Read explicit raw rows; every omitted bin is missing, never zero.

    A synthetic contract must agree with each row's raw unit/identity. count/glass,
    per-um and all real declarations stay blocked, even if a survey area exists.
    """
    records = tuple(dict(r.raw) if isinstance(r,DensityRow) else dict(r) for r in records)
    if not records:
        raise NumericContractError('missing_density_records')
    required={'distribution_id','process_layer','period','bin_semantics','raw_value_unit','observed'}
    if any(not required <= set(row) or ('diameter_um' not in row and not {'diameter','diameter_unit'} <= set(row)) for row in records):
        raise NumericContractError('missing_density_fields')
    first = records[0]
    distribution = text(first['distribution_id'], 'distribution_id')
    process = text(first['process_layer'], 'process_layer')
    if contract is not None and (contract.distribution_id != distribution or contract.process_layer != process):
        raise NumericContractError('density_contract_identity_mismatch')
    reasons = ('density_contract_unconfirmed',) if contract is None else contract.reasons
    bins, signature = {}, None
    for row in records:
        meta = tuple(row.get(k) for k in ('distribution_id','process_layer','raw_value_unit','period','bin_semantics'))
        if signature is not None and meta != signature:
            raise NumericContractError('inconsistent_density_distribution')
        signature = meta
        text(row['period'],'density period')
        text(row['raw_value_unit'],'raw density unit')
        def optional_number(name,positive=False):
            value=row.get(name)
            if value is None or value=='':return None
            try:value=float(value) if isinstance(value,str) else value
            except ValueError as exc:raise NumericContractError('invalid_density_metadata',name) from exc
            return finite(value,name,positive=positive,nonnegative=not positive)
        optional_number('surveyed_area_um2',positive=True)
        lower,upper=optional_number('bin_lower_um'),optional_number('bin_upper_um')
        if (lower is None)!=(upper is None) or (lower is not None and lower>=upper):
            raise NumericContractError('invalid_density_bin')
        if contract is not None and (row['raw_value_unit'] != contract.value_unit
                or row.get('period') != contract.period or row.get('bin_semantics') != contract.bin_semantics):
            raise NumericContractError('density_contract_metadata_mismatch')
        if 'diameter_um' in row:
            if row.get('diameter_unit','um') != 'um':
                raise NumericContractError('canonical_diameter_unit_mismatch')
            raw_d, unit = row['diameter_um'], 'um'
        else:
            raw_d, unit = row['diameter'], row['diameter_unit']
        if isinstance(raw_d,str):
            try: raw_d = float(raw_d)
            except ValueError as exc: raise NumericContractError('invalid_density_diameter') from exc
        diameter = float(diameter_key(raw_d,unit))
        if diameter not in DIAMETERS_UM or diameter in bins:
            raise NumericContractError('invalid_or_duplicate_density_diameter')
        marker = row['observed']
        if isinstance(marker,str) and marker in ('true','false'): marker = marker == 'true'
        if type(marker) is not bool:
            raise NumericContractError('invalid_density_observation')
        raw = row.get('raw_value')
        if raw is None or raw == '': raw = None
        elif isinstance(raw,str):
            try: raw = float(raw)
            except ValueError as exc: raise NumericContractError('invalid_density_value') from exc
        if raw is not None: raw = finite(raw,'density',nonnegative=True)
        if marker != (raw is not None):
            raise NumericContractError('density_observation_mismatch')
        errors = tuple(sorted(set(reasons + (() if marker else ('missing_density_input',)))))
        canonical = None if errors else density_per_um2(raw,contract.value_unit)
        bins[diameter] = DensityBin(diameter,raw,marker,canonical,errors)
    for diameter in DIAMETERS_UM:
        if diameter not in bins:
            bins[diameter] = DensityBin(diameter,None,False,None,tuple(sorted(set((*reasons,'missing_density_input')))))
    return DensityDistribution(distribution,process,contract,dict(sorted(bins.items())),reasons,records)
