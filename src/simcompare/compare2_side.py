"""compare2 split into a per-product side asset, a per-product summary and a pair composition (pasx/T2).

compare2-side     one product's `_load_side` result (extracted + meta) -> side.json (`side_asset_v1`)
compare2-summary  side.json + density -> summary.json (product-only indicators, dependency fingerprint)
compare2-compose  two side.json + the pair config -> the classic compare2 outputs + compose.json

Nothing here changes a number: the same `_load_side`, `_compute_side` and `assemble_compare2` run, only the
hand-over between them is a file. A side file does not name its role: the same product section gives the same bytes
whether it was made as reference or as target. A side is bound (`binding`) to the files and constants it was measured
from; compose and summary recompute that binding from the current config and refuse a stale side
(`compare2_side_stale`) without reading any mask.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from .adapters.real_hotspot import CASE_EXCLUSION_RULE
from .compare2 import (AREA_BASIS, LEDGER_BASIS, ROLES, _compute_side,
                       _diameter_json, _json_text, _load_side, _publish, _side_json, assemble_compare2,
                       load_pair_config, main_compare2, prepare_compare2, run_compare2)
from .compare2_inputs import collect_cno, load_density
from .numeric_schema import NumericContractError
from .pair_exclusion import PAIR_EXCLUSION_RULE

SIDE_SCHEMA = 'side_asset_v1'
SUMMARY_SCHEMA = 'side_summary_v1'
COMPOSE_SCHEMA = 'pair_compose_v1'
SIDE_BASIS = 'side_extract_v1'
SUMMARY_BASIS = 'side_summary_v1'
COMPOSE_BASIS = 'pair_compose_v1'
SIDE_COMMAND = 'compare2-side'
SUMMARY_COMMAND = 'compare2-summary'
SIDE_FILE = 'side.json'
SUMMARY_FILE = 'summary.json'
COMPOSE_FILE = 'compose.json'
TAGS = ('$dict', '$ddict_float', '$tuple', '$set', '$dec')


# ---- serialisation (PLAN-v2.1 B) -------------------------------------------------------------------------------

def _unsupported(detail):
    return NumericContractError('compare2_side_unsupported_value', detail)


def _invalid(detail):
    return NumericContractError('compare2_side_invalid', detail)


def canonical(value):
    """Compact, deterministic JSON text of an already encoded value."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def encode(value):
    """A Python value to the tagged JSON form. Every JSON object is a one-key tag object; a type outside the list is refused."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return value
    if kind is float:
        if not math.isfinite(value):
            raise _unsupported(f'non-finite float {value!r}')
        return value
    if kind is list:
        return [encode(item) for item in value]
    if kind is tuple:
        return {'$tuple': [encode(item) for item in value]}
    if kind is dict:
        return {'$dict': [[encode(key), encode(item)] for key, item in value.items()]}
    if kind is defaultdict:
        if value.default_factory is not float:
            raise _unsupported(f'defaultdict with default factory {value.default_factory!r}')
        return {'$ddict_float': [[encode(key), encode(item)] for key, item in value.items()]}
    if kind is set:
        return {'$set': sorted((encode(item) for item in value), key=canonical)}
    if kind is Decimal:
        if not value.is_finite():
            raise _unsupported(f'non-finite Decimal {value!r}')
        return {'$dec': str(value)}
    raise _unsupported(f'type {kind.__module__}.{kind.__qualname__}')


def decode(value):
    """Inverse of `encode`: same types, same key order."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str or kind is float:
        return value
    if kind is list:
        return [decode(item) for item in value]
    if kind is not dict or len(value) != 1:
        raise _invalid('a JSON object must be a one-key tag object')
    (tag, body), = value.items()
    if tag == '$tuple':
        return tuple(decode(item) for item in _list(body, tag))
    if tag in ('$dict', '$ddict_float'):
        result = {} if tag == '$dict' else defaultdict(float)
        for entry in _list(body, tag):
            if type(entry) is not list or len(entry) != 2:
                raise _invalid(f'{tag}: entries are [key, value] pairs')
            result[decode(entry[0])] = decode(entry[1])
        return result
    if tag == '$set':
        return {decode(item) for item in _list(body, tag)}
    if tag == '$dec':
        if type(body) is not str:
            raise _invalid('$dec: a string is required')
        return Decimal(body)
    raise _invalid(f'unknown tag {tag!r}')


def _list(body, tag):
    if type(body) is not list:
        raise _invalid(f'{tag}: a list is required')
    return body


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


# ---- binding -----------------------------------------------------------------------------------------------------

def _rules():
    return {'area_basis': AREA_BASIS, 'ledger_basis': LEDGER_BASIS, 'case_exclusion_rule': CASE_EXCLUSION_RULE,
            'pair_exclusion_rule': PAIR_EXCLUSION_RULE}


def side_binding(cfg, role, top):
    """What a side measured from, recomputed from the numeric config of `role`: file hashes, section, constants.
    Reads the manifest.json bytes and the pair/distance table bytes only (never a mask)."""
    from .numeric_images import locate_file, resolve_path
    from .pipeline import _numeric_path
    section = cfg.get(role)
    if not isinstance(section, dict) or not isinstance(section.get('data_dir'), str):
        raise NumericContractError('numeric_input_required')
    root = _numeric_path(cfg, section['data_dir'])
    manifest = resolve_path(root, section.get('manifest_path', 'manifest.json'))
    resolved = dict(section)
    resolved['data_dir'] = str(Path(root).resolve())
    resolved['manifest_path'] = str(manifest)
    pair_sha = distance_sha = None
    assignment = section.get('condition_assignment')
    if isinstance(assignment, dict):
        assignment = dict(assignment)
        config_dir = cfg['_config_dir']
        if assignment.get('pair_table_path'):
            table = locate_file(config_dir, root, assignment['pair_table_path'], 'pair_table_path').path
            assignment['pair_table_path'] = str(table)
            pair_sha = _sha256_file(table)
        if assignment.get('interlayer_distance_path') is not None:
            table = locate_file(config_dir, None, assignment['interlayer_distance_path'], 'interlayer_distance_path').path
            assignment['interlayer_distance_path'] = str(table)
            distance_sha = _sha256_file(table)
        resolved['condition_assignment'] = assignment
    section_text = json.dumps(resolved, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return {'manifest_sha256': _sha256_file(manifest), 'section_sha256': sha256_bytes(section_text.encode('utf-8')),
            'pair_table_sha256': pair_sha, 'distance_table_sha256': distance_sha, 'top_buckets': top,
            'side_basis': SIDE_BASIS, 'rules': _rules()}


def _check_binding(doc, cfg, role, top, where):
    now = side_binding(cfg, role, top)
    then = doc.get('binding')
    if then != now:
        differ = sorted(key for key in set(now) | set(then or {}) if (then or {}).get(key) != now.get(key))
        raise NumericContractError('compare2_side_stale', f'{where}: {role} side no longer matches the config: {", ".join(differ)}')


# ---- side files --------------------------------------------------------------------------------------------------

def side_text(extracted, meta, binding):
    document = {'schema_version': SIDE_SCHEMA, 'binding': binding, 'meta': meta, 'extracted': extracted}
    return canonical(encode(document))


def read_side(side_dir):
    """(decoded document, sha256 of the side.json bytes)."""
    path = Path(side_dir) / SIDE_FILE
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NumericContractError('compare2_side_missing', f'{path}: {exc}') from exc
    try:
        document = decode(json.loads(raw.decode('utf-8')))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _invalid(f'{path}: {exc}') from exc
    if type(document) is not dict or document.get('schema_version') != SIDE_SCHEMA:
        raise _invalid(f'{path}: schema_version must be {SIDE_SCHEMA}')
    for key in ('binding', 'meta', 'extracted'):
        if type(document.get(key)) is not dict:
            raise _invalid(f'{path}: {key} is missing')
    return document, sha256_bytes(raw)


def _owned(out, names, command):
    out = Path(out)
    if not out.exists():
        return True
    if not out.is_dir():
        return False
    for path in sorted(out.iterdir()):
        if path.name.startswith('.compare2-'):
            continue
        if path.name not in names:
            return False
        if path.name == 'error.json':
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('command') != command:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
    return True


def _check_owned(out, names, command):
    if not _owned(out, names, command):
        raise NumericContractError('compare2_output_not_owned', f'{out}: use a new folder or a previous {command} folder')


def run_side(config_path, role, out):
    """Measure one role and publish side.json into out; returns a small record."""
    if role not in ROLES:
        raise NumericContractError('invalid_compare2_role', f'role must be reference or target: {role!r}')
    out = Path(out)
    _check_owned(out, {SIDE_FILE, 'error.json'}, SIDE_COMMAND)
    _cfg2, cfg, top = load_pair_config(config_path)
    binding = side_binding(cfg, role, top)
    extracted, meta = _load_side(cfg, role, top)
    text = side_text(extracted, meta, binding)
    _publish(out, [(SIDE_FILE, text)])
    (out / 'error.json').unlink(missing_ok=True)
    return {'command': SIDE_COMMAND, 'status': 'available', 'output': str(out / SIDE_FILE),
            'side_sha256': sha256_bytes(text.encode('utf-8')), 'exit_code': 0}


# ---- summary -----------------------------------------------------------------------------------------------------

def _cno_deps(product):
    return [{'case_name': case.case_name, 'csv_relative_path': case.csv_relative_path,
             'csv_sha256': case.csv_sha256, 'status': case.status} for case in collect_cno(product.hotspot_root)]


def _listed(cfg2):
    return None if cfg2.lambda_diameters_um is None else [str(key) for key in cfg2.lambda_diameters_um]


def _product_section(product):
    return {'model_group': product.model_group, 'resolution': list(product.resolution),
            'unit_size_um': None if product.unit_size_um is None else list(product.unit_size_um)}


def run_summary(config_path, role, side_dir, out):
    """Product-only indicators of one side (summary.json); the side is recomputed through `_compute_side`."""
    if role not in ROLES:
        raise NumericContractError('invalid_compare2_role', f'role must be reference or target: {role!r}')
    out = Path(out)
    _check_owned(out, {SUMMARY_FILE, 'error.json'}, SUMMARY_COMMAND)
    cfg2, cfg, top = load_pair_config(config_path)
    product = getattr(cfg2, role)
    if not Path(product.hotspot_root).is_dir():
        raise NumericContractError('compare2_hotspot_root_missing', str(product.hotspot_root))
    tables = load_density(cfg2.density_dir, product.model_group)
    document, side_sha = read_side(side_dir)
    _check_binding(document, cfg, role, top, SUMMARY_COMMAND)
    extracted, meta = document['extracted'], document['meta']
    group = cfg2.model_groups[product.model_group]
    listed = cfg2.lambda_diameters_um
    subset = None if listed is None else set(listed)
    args = (role, product, group, tables, extracted, meta)
    side = _compute_side(*args, diameters=subset)
    full = side if subset is None else _compute_side(*args)  # observed sizes are those of the unfiltered side
    side_doc, status, reasons = _side_json(side)
    conditions = {'line': 0, 'pixel': 0}
    for cond in side['conds'].values():
        conditions[cond['class']] += 1
    summary = {
        'schema_version': SUMMARY_SCHEMA,
        'status': status, 'reasons': reasons,
        'lambda': side_doc['lambda'],
        'caa_line': side_doc['caa_line'], 'caa_pixel': side_doc['caa_pixel'],
        'vi_line': side_doc['vi_line'], 'vi_pixel': side_doc['vi_pixel'],
        'observed_diameters_um': [_diameter_json(key) for key in sorted(full['observed'])],
        'conditions': conditions,
        'absent_expected_cases': len(side['absent_cases']),
        'case_exclusions': meta.get('case_exclusions'),
        'deps': {
            'side_sha256': side_sha,
            'density_sha256': {kind: tables[kind].sha256 for kind in ('line', 'pixel')},
            'model_group': {'name': product.model_group, 'config': asdict(group)},
            'product': _product_section(product),
            'lambda_diameters_um': _listed(cfg2),
            'cno': _cno_deps(product),
            'summary_basis': SUMMARY_BASIS}}
    _publish(out, [(SUMMARY_FILE, _json_text(summary))])
    (out / 'error.json').unlink(missing_ok=True)
    return {'command': SUMMARY_COMMAND, 'status': status, 'output': str(out / SUMMARY_FILE),
            'exit_code': 0 if status == 'available' else 3}


# ---- compose -----------------------------------------------------------------------------------------------------

def run_compose(config_path, side_reference, side_target, out=None):
    """The classic compare2 outputs from two side files, then compose.json (last)."""
    dirs = {'reference': side_reference, 'target': side_target}
    state = {}

    def build(path):
        cfg2, cfg, top, tables = prepare_compare2(path)
        extracted, metas, shas = {}, {}, {}
        for role in ROLES:  # the binding is checked before any number is built; no mask is read
            document, shas[role] = read_side(dirs[role])
            _check_binding(document, cfg, role, top, 'compare2-compose')
            extracted[role], metas[role] = document['extracted'], document['meta']
        state.update(cfg2=cfg2, top=top, tables=tables, shas=shas)
        return assemble_compare2(cfg2, cfg, tables, extracted, metas)

    def after(_output, compare2_text):
        cfg2, tables = state['cfg2'], state['tables']
        compose = {
            'schema_version': COMPOSE_SCHEMA,
            'compare2_json_sha256': sha256_bytes(compare2_text.encode('utf-8')),
            'deps': {
                'side_sha256': state['shas'],
                'density_sha256': {role: {kind: tables[role][kind].sha256 for kind in ('line', 'pixel')} for role in ROLES},
                'products': {role: _product_section(getattr(cfg2, role)) for role in ROLES},
                'common': {'model_groups': {name: asdict(group) for name, group in cfg2.model_groups.items()},
                           'lambda_diameters_um': _listed(cfg2), 'top_buckets': state['top']},
                'cno': {role: _cno_deps(getattr(cfg2, role)) for role in ROLES},
                'compose_basis': COMPOSE_BASIS}}
        return _json_text(compose)

    return run_compare2(config_path, out, build=build, after=after)


def main_compose(config, side_reference, side_target, out, print_result, error_stream=None):
    return main_compare2(config, out, print_result, error_stream,
                         runner=lambda cfg, o: run_compose(cfg, side_reference, side_target, o))


def _main_single(command, names, run, out, print_result, error_stream):
    error_stream = error_stream or sys.stderr
    try:
        record = run()
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, 'code', 'input_or_config_error')
        print(f'오류: {exc}', file=error_stream)
        if code != 'compare2_output_not_owned' and _owned(out, names, command):
            try:
                for name in names - {'error.json'}:
                    (Path(out) / name).unlink(missing_ok=True)
                _publish(Path(out), [('error.json', _json_text({'command': command, 'code': code,
                                                                'detail': str(exc), 'exit_code': 2}))])
            except Exception as publish_error:
                print(f'오류: error.json 을 쓰지 못했습니다 (원래 오류 {code}): {publish_error}', file=error_stream)
        return 2
    print_result(record)
    return record['exit_code']


def main_side(config, role, out, print_result, error_stream=None):
    return _main_single(SIDE_COMMAND, {SIDE_FILE, 'error.json'}, lambda: run_side(config, role, out), out,
                        print_result, error_stream)


def main_summary(config, role, side_dir, out, print_result, error_stream=None):
    return _main_single(SUMMARY_COMMAND, {SUMMARY_FILE, 'error.json'}, lambda: run_summary(config, role, side_dir, out),
                        out, print_result, error_stream)
