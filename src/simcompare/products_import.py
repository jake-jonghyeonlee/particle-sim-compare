"""`products-import` (pasx/T6): put the two products of a real.yaml + compare2.yaml pair into products.yaml.

  products-import --real real.yaml --compare2 compare2.yaml --group G [--config products.yaml] [--update] [--check] [--no-cno]

Reads the two existing settings with their own loaders, builds the product sections of `reference` and `target`
(name = real.yaml `<role>.product_id`) and merges them into products.yaml (created when absent):
  - a product or common value that already exists and differs is listed and refused (exit 2, `products_import_conflict`)
    unless `--update`; an equal one is left alone and an unchanged file is not written at all (its mtime stays);
  - a human-written `note` stays; comments of the file are not kept (the first line says the file was fixed by this command);
  - the result is written to a temporary file next to the target, checked with `check_groups` (group compatibility) and
    only then moved with os.replace. A failing check leaves the target as it was. `--check` writes nothing to the target
    (the temporary file is removed again);
  - the YAML is written deterministically (fixed key order).
"""
import copy
import json
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import products_config as pc
from .numeric_schema import NumericContractError

HEADER = '# products-import 가 고친 파일 (simcompare_products_v1). 사람이 쓴 note 는 유지된다. 주석은 보존되지 않는다.\n'
COMMON_ORDER = ('density_dir', 'model_groups', 'report', 'lambda_diameters_um', 'numeric')
PRODUCT_ORDER = ('note', 'hotspot_root', 'include_cno', 'model_group', 'resolution', 'unit_size_um', 'dl1_model',
                 'condition_assignment')
ASSIGNMENT_ORDER = ('design_version', 'source_id', 'process_step_id', 'exposure_id', 'unit_id', 'layer_stack',
                    'pair_table_path', 'interlayer_distance_path', 'electrode_role_policy',
                    'max_materialized_source_cells', 'max_group_mask_cells', 'max_operation_bytes')
LAYER_ORDER = ('actual_layer', 'stack_order', 'density_process_layer')
DEFAULT_REVIEW_ROIS = {'map_region_ids': [], 'manual': []}
ROLES = ('reference', 'target')


def _error(code: str, detail: str) -> NumericContractError:
    return NumericContractError(code, detail)


# ------------------------------------------------------------------------------------------------------------ reading

def _rebase(value, source_dir: Path, target_dir: Path):
    """A path written relative to `source_dir`, written relative to `target_dir` (posix). Odd values stay as they are
    (products-config refuses absolute paths, '..' and backslashes itself)."""
    if not isinstance(value, str) or not value or os.path.isabs(value) or '\\' in value:
        return value
    return os.path.relpath(str(source_dir / value), str(target_dir)).replace(os.sep, '/')


def _read_yaml(path: Path) -> dict:
    import yaml
    try:
        data = yaml.safe_load(path.read_bytes().decode('utf-8'))
    except OSError as exc:
        raise _error('products_import_unreadable', '%s: %s' % (path, exc))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise _error('products_import_invalid', '%s: %s' % (path, exc))
    if not isinstance(data, dict):
        raise _error('products_import_invalid', '%s: not a mapping' % path)
    return data


def _load_sources(real_path: Path, compare2_path: Path) -> Tuple[dict, dict]:
    """(real.yaml, compare2.yaml) as plain values, after the existing loaders accepted them and they point at each other."""
    from .compare2_inputs import load_compare2_config
    from .pipeline import load_numeric_config
    try:
        real = load_numeric_config(real_path)
        compare2 = load_compare2_config(compare2_path)
    except OSError as exc:
        raise _error('products_import_unreadable', str(exc))
    if real.get('mode') != 'real':
        raise _error('products_import_invalid', '%s: mode must be real' % real_path)
    if compare2.numeric_config.resolve() != real_path.resolve():
        raise _error('products_import_config_mismatch', '%s numeric_config (%s) is not %s' % (
            compare2_path.name, compare2.numeric_config, real_path))
    raw = _read_yaml(compare2_path)
    for side in ROLES:
        section = real.get(side)
        if not isinstance(section, dict) or not isinstance(section.get('product_id'), str):
            raise _error('products_import_invalid', '%s: %s needs product_id' % (real_path.name, side))
        if not isinstance(section.get('condition_assignment'), dict):
            raise _error('products_import_invalid', '%s: %s.condition_assignment is required' % (real_path.name, side))
    return real, raw


# ------------------------------------------------------------------------------------------------------ desired values

def _desired_common(real: dict, compare2: dict, compare2_dir: Path, config_dir: Path) -> dict:
    report = copy.deepcopy(compare2.get('report'))
    if isinstance(report, dict) and report.get('dl1_root') is not None:
        report['dl1_root'] = _rebase(report['dl1_root'], compare2_dir, config_dir)
    numeric = {'mode': real['mode']}
    section = {key: copy.deepcopy(value) for key, value in (real.get('map') or {}).items() if key != 'input'}
    if section:
        numeric['map'] = section
    if real.get('review_rois') is not None and real['review_rois'] != DEFAULT_REVIEW_ROIS:
        numeric['review_rois'] = copy.deepcopy(real['review_rois'])
    if real.get('compare') is not None:
        numeric['compare'] = copy.deepcopy(real['compare'])
    return {'density_dir': _rebase(compare2['density_dir'], compare2_dir, config_dir),
            'model_groups': copy.deepcopy(compare2['model_groups']), 'report': report,
            'lambda_diameters_um': copy.deepcopy(compare2.get('lambda_diameters_um')), 'numeric': numeric}


def _desired_product(role: str, real: dict, compare2: dict, real_dir: Path, compare2_dir: Path, config_dir: Path,
                     include_cno: bool) -> Tuple[str, dict]:
    name = real[role]['product_id']
    pc.check_name(name, 'product')
    p2 = compare2['products'][role]
    assignment = copy.deepcopy(real[role]['condition_assignment'])
    if assignment.pop('product_id', name) != name:
        raise _error('products_import_invalid', '%s.condition_assignment.product_id differs from %s.product_id %r' % (
            role, role, name))
    if assignment.get('source_id') == '%s_hotspot' % name:
        del assignment['source_id']             # products-config makes this value itself
    for key in ('pair_table_path', 'interlayer_distance_path'):
        if assignment.get(key) is not None:
            assignment[key] = _rebase(assignment[key], real_dir, config_dir)
    section = {'hotspot_root': _rebase(p2['hotspot_root'], compare2_dir, config_dir), 'include_cno': bool(include_cno),
               'model_group': p2['model_group'], 'resolution': list(p2['resolution'])}
    if p2.get('unit_size_um') is not None:
        section['unit_size_um'] = list(p2['unit_size_um'])
    if p2.get('dl1_model') is not None:
        section['dl1_model'] = p2['dl1_model']
    section['condition_assignment'] = assignment
    return name, section


# --------------------------------------------------------------------------------------------------------- comparison

def _plain(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _diff(prefix: str, old, new) -> List[str]:
    """Changed cells (one line each); mappings go down to their keys, everything else is compared whole."""
    if isinstance(old, dict) and isinstance(new, dict):
        lines: List[str] = []
        for key in list(old) + [k for k in new if k not in old]:
            where = '%s.%s' % (prefix, key)
            if key not in old:
                lines.append('%s: (absent) -> %s' % (where, _plain(new[key])))
            elif key not in new:
                lines.append('%s: %s -> (absent)' % (where, _plain(old[key])))
            else:
                lines += _diff(where, old[key], new[key])
        return lines
    if _plain(old) != _plain(new):
        return ['%s: %s -> %s' % (prefix, _plain(old), _plain(new))]
    return []


def _normal_common(base: Path, raw) -> dict:
    common = pc._load_common(base, raw)
    numeric = common.get('numeric') or {}
    if numeric.get('review_rois') == DEFAULT_REVIEW_ROIS:
        numeric = {k: v for k, v in numeric.items() if k != 'review_rois'}
    common['numeric'] = numeric
    return common


def _normal_product(name: str, raw, common: dict) -> dict:
    product = pc._load_product(name, raw, common)
    product.pop('note', None)
    return product


# ----------------------------------------------------------------------------------------------------------- writing

def _ordered(value, order: Tuple[str, ...] = ()):
    if not isinstance(value, dict):
        return value
    keys = [k for k in order if k in value] + sorted(k for k in value if k not in order)
    return {k: value[k] for k in keys}


def _ordered_product(raw: dict) -> dict:
    out = _ordered(raw, PRODUCT_ORDER)
    if isinstance(out.get('condition_assignment'), dict):
        assignment = _ordered(out['condition_assignment'], ASSIGNMENT_ORDER)
        if isinstance(assignment.get('layer_stack'), list):
            assignment['layer_stack'] = [_ordered(item, LAYER_ORDER) for item in assignment['layer_stack']]
        out['condition_assignment'] = assignment
    return out


def _dump(doc: dict) -> str:
    import yaml

    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    scalar = (str, int, float, bool, type(None))

    def represent_list(dumper, data):
        flow = all(isinstance(item, scalar) for item in data)
        return dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=flow)

    def represent_dict(dumper, data):
        flow = bool(data) and all(isinstance(value, scalar) for value in data.values())
        return dumper.represent_mapping('tag:yaml.org,2002:map', data, flow_style=flow)

    Dumper.add_representer(list, represent_list)
    Dumper.add_representer(dict, represent_dict)
    return HEADER + yaml.dump(doc, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=1000)


class _Merge(object):
    def __init__(self):
        self.doc: dict = {}
        self.conflicts: List[str] = []         # existing values that differ from the desired ones
        self.added: List[str] = []             # new products / group members
        self.updated: List[str] = []           # existing values replaced (only with --update)
        self.notes: Dict[str, str] = {}

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated)


def _merge(existing: Optional[dict], config_dir: Path, common: dict, products: Dict[str, dict], group: str,
           update: bool) -> _Merge:
    merge = _Merge()
    if existing is None:
        doc = {'schema_version': pc.SCHEMA_VERSION, 'common': copy.deepcopy(common), 'groups': {},
               'products': {}}
        merge.added.append('file')
    else:
        doc = copy.deepcopy(existing)
        for key in ('common', 'groups', 'products'):
            if not isinstance(doc.get(key), dict):
                raise _error('invalid_products_config', '%s: %s must be a mapping' % ('products.yaml', key))
    merge.doc = doc
    new_common = _normal_common(config_dir, common)
    if existing is not None:
        try:
            old_common = _normal_common(config_dir, doc['common'])
        except NumericContractError as exc:
            raise _error('invalid_products_config', 'existing common does not load: %s' % exc)
        for key in COMMON_ORDER:
            lines = _diff('common.%s' % key, old_common.get(key), new_common.get(key))
            if lines:
                merge.conflicts += lines
                if update:
                    doc['common'][key] = copy.deepcopy(common[key])
                    merge.updated.append('common.%s' % key)
    for name, section in products.items():
        current = doc['products'].get(name)
        if current is None:
            doc['products'][name] = copy.deepcopy(section)
            merge.added.append('products.%s' % name)
            continue
        file_common = _normal_common(config_dir, doc['common'])      # the common values the file has after this run
        try:
            old = _normal_product(name, current, file_common)
        except NumericContractError as exc:
            old = {'(does not load)': str(exc)}
        new = _normal_product(name, section, file_common)
        lines = _diff('products.%s' % name, old, new)
        if lines:
            merge.conflicts += lines
            if update:
                replaced = copy.deepcopy(section)
                if isinstance(current, dict) and current.get('note') is not None:
                    replaced['note'] = current['note']          # a human-written note stays
                doc['products'][name] = replaced
                merge.updated.append('products.%s' % name)
    for name in products:
        owner = [g for g, members in doc['groups'].items() if isinstance(members, list) and name in members]
        if owner and owner[0] != group:
            raise _error('products_import_group_conflict', 'product %s is already in group %s (asked for group %s); '
                         'move it by hand' % (name, owner[0], group))
    members = doc['groups'].setdefault(group, [])
    if not isinstance(members, list):
        raise _error('invalid_products_config', 'groups.%s must be a list' % group)
    for name in products:
        if name not in members:
            members.append(name)
            merge.added.append('groups.%s: %s' % (group, name))
    return merge


def _final_doc(merge: _Merge) -> dict:
    doc = merge.doc
    common = _ordered(doc['common'], COMMON_ORDER)
    return {'schema_version': doc.get('schema_version', pc.SCHEMA_VERSION), 'common': common,
            'groups': {g: list(m) for g, m in doc['groups'].items()},
            'products': {n: _ordered_product(p) for n, p in doc['products'].items()}}


# ------------------------------------------------------------------------------------------------------------- command

def run_products_import(real_path, compare2_path, group: str, config_path='products.yaml', update: bool = False,
                        check: bool = False, include_cno: bool = True, out: Callable[[dict], None] = None,
                        err: Optional[Callable[[str], None]] = None) -> int:
    """0 = written / unchanged / would work (--check), 2 = refused or error. Nothing of the target changes on exit 2."""
    out = out or (lambda result: print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)))
    err = err or (lambda text: print(text, file=sys.stderr))
    temp = None
    try:
        pc.check_name(group, 'group')
        real_path, compare2_path = Path(real_path).resolve(), Path(compare2_path).resolve()
        config_path = Path(config_path).resolve()
        config_dir = config_path.parent
        real, compare2 = _load_sources(real_path, compare2_path)
        common = _desired_common(real, compare2, compare2_path.parent, config_dir)
        products: Dict[str, dict] = {}
        for role in ROLES:
            name, section = _desired_product(role, real, compare2, real_path.parent, compare2_path.parent, config_dir,
                                             include_cno)
            if name in products:
                raise _error('products_import_invalid', 'reference and target have the same product_id %r' % name)
            products[name] = section
        existing = None
        if config_path.exists():
            if config_path.is_symlink() or not config_path.is_file():
                raise _error('products_import_invalid', '%s is not a plain file' % config_path)
            existing = _read_yaml(config_path)
            if existing.get('schema_version') != pc.SCHEMA_VERSION:
                raise _error('unsupported_products_config', '%s: use schema_version: %s' % (config_path, pc.SCHEMA_VERSION))
        merge = _merge(existing, config_dir, common, products, group, update)
        if merge.conflicts and not update:
            err('오류: products_import_conflict: %s differs from %s / %s; use --update to overwrite:'
                % (config_path.name, real_path.name, compare2_path.name))
            for line in merge.conflicts:
                err('  ' + line)
            out({'status': 'conflict', 'conflicts': merge.conflicts})
            return 2
        result = {'path': str(config_path), 'group': group, 'products': list(products)}
        if not merge.changed:
            result['status'] = 'unchanged'
            out(result)
            return 0
        text = _dump(_final_doc(merge))
        temp = config_dir / ('.products-import.%d.tmp' % os.getpid())
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(str(temp), 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        try:
            checked = pc.load_products_config(temp)
            pc.check_groups(checked)
        except pc.GroupCompatibilityError as exc:
            err('오류: group_incompatible: %s (%s is unchanged)' % (exc, config_path.name))
            out({'status': 'incompatible', 'violations': exc.violations})
            return 2
        result.update({'added': merge.added, 'updated': merge.updated, 'conflicts_overwritten': merge.conflicts})
        if check:
            result['status'] = 'would_write'
        else:
            os.replace(str(temp), str(config_path))
            temp = None
            result['status'] = 'written'
        out(result)
        return 0
    except NumericContractError as exc:
        err('오류: %s' % exc)
        return 2
    except OSError as exc:
        err('오류: products_import_io: %s' % exc)
        return 2
    finally:
        if temp is not None:
            try:
                os.unlink(str(temp))
            except OSError:
                pass
