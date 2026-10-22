"""Test-only copy of the pre-T2 configured real `compare` payload (real_hotspot_comparison_v1), built the old way:
full AreaResult.to_dict() per side, one payload dict, json.dumps. Never imported by src."""
import json

from simcompare.linepixel import compute_areas
from simcompare.pipeline import _configured_real, _real_source


def _area_rows(side, result):
    rows = []
    for group in result.groups.values():
        row = {'side': side, 'group': group.key.__dict__, 'status': group.union.status,
               'union_um2': group.union.value, 'partial_union_um2': group.union.partial_value,
               'line_um2': group.line.area.value, 'pixel_effective_um2': group.pixel.area.value,
               'transfer_um2': group.transfer.value, 'unmapped_pair_count': group.coverage.unmapped,
               'missing_count': group.coverage.missing, 'is_synthetic': False}
        for source in group.source_cases:
            raw_records = source.raw.get('records', []) if isinstance(source.raw, dict) else []
            raw_record = next((r for r in raw_records if r.get('diameter_um') == source.diameter_um), {})
            csv_area = raw_record.get('csv_area') or {}
            rows.append({**row, 'case_id': source.identity.case_id, 'diameter_um': source.diameter_um,
                         'case_area_um2': source.measurement.ca_um2,
                         'case_partial_area_um2': source.measurement.partial_value,
                         'input_status': source.input_status,
                         'csv_status': raw_record.get('csv_status'),
                         'csv_area_um2': csv_area.get('value'),
                         'csv_raw_value': csv_area.get('raw_area'),
                         'area_difference_um2': raw_record.get('area_difference_um2'),
                         'area_tolerance_um2': raw_record.get('area_tolerance_um2'),
                         'reasons': list(source.measurement.reasons)})
    return rows


def build_v1(cfg):
    """Return (payload, rows) exactly as the v1 writer serialised them, or None when neither side has cases."""
    sides = {}
    unresolved = []
    for name in ('reference', 'target'):
        # v1 is the full-detail payload: admit full ({}), so that compute_areas' default (= the admitted detail) is full
        configured = _configured_real(cfg, cfg[name], _real_source(cfg, cfg[name]), {})
        result = compute_areas(configured[1]) if configured[1].cases else None
        sides[name] = (result.to_dict() if result is not None else None,
                       _area_rows(name, result) if result is not None else [])
        unresolved.append(configured[1].provenance.get('unresolved_same_name_cases', []))
    if sides['reference'][0] is None and sides['target'][0] is None:
        return None
    rows = sides['reference'][1] + sides['target'][1]
    payload = {'schema_version': 'real_hotspot_comparison_v1', 'source_contract_version': 'real_hotspot_v1',
               'is_synthetic': False, 'status': 'partial',
               'reasons': ['density_contract_unconfirmed', 'unit_area_basis_unconfirmed',
                           'same_name_cases_unresolved' if unresolved[0] or unresolved[1] else ''],
               'raw_case_areas_status': 'available',
               'classified_line_pixel_areas_status': 'partial' if rows else 'unavailable',
               'weighted_rates_status': 'unavailable', 'deltas_status': 'unavailable',
               'reference': sides['reference'][0], 'target': sides['target'][0], 'records': rows}
    payload['reasons'] = sorted(filter(None, payload['reasons']))
    return json.loads(json.dumps(payload, ensure_ascii=False, indent=2, default=str)), rows
