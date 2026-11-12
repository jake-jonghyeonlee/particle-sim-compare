"""thk_info-<model>.txt: the distance between two layers, used to drop vertical-short hotspots (2026-11-12).

File: tab separated text. Header `layers<TAB>distance` (case-insensitive). Every later line is
`<layerA><layerB><TAB><distance>`: the two layer names written one after the other, in either order. A hotspot of a
vertical short between the layers A and B is dropped for the diameters d <= distance (equal diameters are dropped too).

The key is never split. For every unordered pair of the layer stack the strings A+B and B+A are looked up. Distances
are read as Decimal (never float) and compared with the diameter as Decimal; the unit is micrometre (assumed).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from .numeric_schema import NumericContractError

DISTANCE_FILTER_RULE = 'vertical_short_diameter_le_layer_distance_2026_11_12'
COMPARISON = 'diameter_um <= distance_um'
HEADER = ('layers', 'distance')
# Case reasons that still hold for a diameter the rule dropped (pair / inventory facts, not "this diameter is missing").
KEPT_CASE_REASONS = frozenset({'unmapped_pair', 'same_electrode_name_both_endpoints_short_unresolved',
                               'expected_case_inventory_unprovided'})


class DistanceTable(NamedTuple):
    pairs: Dict[Tuple[str, str], Decimal]   # sorted (layerA, layerB) -> distance
    table_sha256: str                       # sha256 of the normalised table (layer pairs and canonical distances)
    file_name: str
    file_sha256: str                        # of the original bytes (a record only, not part of the input hash)
    ignored_rows: int                       # rows that match no pair of two different stack layers (same-layer rows ...)

    def distance(self, layer_a, layer_b) -> Optional[Decimal]:
        return self.pairs.get(tuple(sorted((layer_a, layer_b))))

    def summary(self) -> dict:
        return {'rule': DISTANCE_FILTER_RULE, 'comparison': COMPARISON, 'unit': 'um (assumed)',
                'file_name': self.file_name, 'file_sha256': self.file_sha256, 'table_sha256': self.table_sha256,
                'ignored_rows': self.ignored_rows,
                'distances_um': {'+'.join(pair): canonical(value) for pair, value in sorted(self.pairs.items())}}


def canonical(value: Decimal) -> str:
    """One spelling per value: 3, 3.0 and 3e0 are all '3'."""
    if value == 0:
        return '0'
    return format(value.normalize(), 'f')


def _error(line_number, message):
    return NumericContractError('invalid_interlayer_distance_file', f'line {line_number}: {message}')


def _distance(text, line_number) -> Decimal:
    if not text.isascii() or '_' in text:
        raise _error(line_number, f'distance {text!r} is not a plain number')
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _error(line_number, f'distance {text!r} is not a number') from None
    if not value.is_finite():
        raise _error(line_number, f'distance {text!r} is not finite')
    if value < 0:
        raise _error(line_number, f'distance {text!r} is negative')
    return value


def parse_rows(data: bytes) -> List[Tuple[int, str, Decimal]]:
    """(line number, key, distance) of every data row; the header and blank lines are consumed here."""
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise NumericContractError('invalid_interlayer_distance_file', f'not UTF-8: {exc}') from None
    rows, header_seen = [], False
    for number, line in enumerate(text.split('\n'), start=1):
        if line.endswith('\r'):
            line = line[:-1]
        if not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) != 2:
            raise _error(number, f'{len(fields)} tab-separated fields, expected 2 (is the file tab separated?)')
        fields = [field.strip() for field in fields]
        if not all(fields):
            raise _error(number, 'empty field')
        if not header_seen:
            if tuple(field.lower() for field in fields) != HEADER:
                raise _error(number, f'header must be layers<TAB>distance, got {fields!r}')
            header_seen = True
            continue
        rows.append((number, fields[0], _distance(fields[1], number)))
    if not header_seen:
        raise NumericContractError('invalid_interlayer_distance_file', 'no header line')
    return rows


def build_table(data: bytes, layer_names: Iterable[str], file_name: str = '') -> DistanceTable:
    rows = parse_rows(data)
    by_key = {}
    for number, key, value in rows:
        earlier = by_key.get(key)
        if earlier is not None and earlier[1] != value:
            raise _error(number, f'{key!r} repeats with a different distance (line {earlier[0]})')
        by_key.setdefault(key, (number, value))
    names = sorted(set(layer_names))
    owners = {}   # key string -> the unordered pairs that can write it
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            for key in (first + second, second + first):
                owners.setdefault(key, set()).add((first, second))
    for key in sorted(by_key):
        if len(owners.get(key, ())) > 1:
            pairs = ', '.join('+'.join(pair) for pair in sorted(owners[key]))
            raise NumericContractError('interlayer_distance_key_ambiguous',
                                       f'{key!r} (line {by_key[key][0]}) can be the layer pairs {pairs}')
    pairs, used = {}, set()
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            found = [(key, by_key[key]) for key in (first + second, second + first) if key in by_key]
            used.update(key for key, _ in found)
            if not found:
                continue
            if len({value for _, (_, value) in found}) > 1:
                raise NumericContractError(
                    'invalid_interlayer_distance_file',
                    f'{first}+{second}: the two orders have different distances '
                    f'({found[0][0]!r} line {found[0][1][0]}, {found[1][0]!r} line {found[1][1][0]})')
            pairs[(first, second)] = found[0][1][1]
    normalised = json.dumps([[a, b, canonical(v)] for (a, b), v in sorted(pairs.items())],
                            separators=(',', ':'), ensure_ascii=False)
    return DistanceTable(pairs, hashlib.sha256(normalised.encode('utf-8')).hexdigest(), file_name,
                         hashlib.sha256(data).hexdigest(), len(set(by_key) - used))


def load_table(path, layer_names: Iterable[str]) -> DistanceTable:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise NumericContractError('input_unreadable', f'{path}: {exc}') from exc
    return build_table(data, layer_names, path.name)


def is_filtered(table: Optional[DistanceTable], layer_a, layer_b, diameter_key_value) -> Optional[dict]:
    """The applicability evidence when the diameter is dropped for the layer pair, else None.
    diameter_key_value is the Decimal of numeric_schema.diameter_key (no float comparison)."""
    if table is None:
        return None
    distance = table.distance(layer_a, layer_b)
    if distance is None or Decimal(diameter_key_value) > distance:
        return None
    return {'source': 'interlayer_distance_table', 'rule': DISTANCE_FILTER_RULE,
            'layers': sorted((layer_a, layer_b)), 'distance_um': canonical(distance), 'comparison': COMPARISON}
