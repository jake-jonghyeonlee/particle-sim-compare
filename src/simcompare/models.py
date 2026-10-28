"""compare-multiproduct T1: compare_models_v1 (models.yaml) loader.

models.yaml lists the models (products) a target product is compared with besides the config reference.
Each model section has the same keys and checks as the config reference/target sections (pipeline._real_source,
pipeline._configured_real, pipeline._numeric_input are reused). The file must sit in the folder of the config so the
relative paths in a model section resolve against the same base (cfg['_config_dir']) as the existing sections; no path
is rewritten. Every check raises NumericContractError (exit 2) before any output is written.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .numeric_schema import NumericContractError

SCHEMA = 'compare_models_v1'
KEY_PATTERN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_-])?')   # used with fullmatch: no trailing newline, no trailing '.'
CONFIGURED = 'configured'
ASSETS_PENDING = 'assets_pending'


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def section_product(section):
    """product_id a config/model section declares (product_id, else condition_assignment.product_id)."""
    if not isinstance(section, dict):
        return None
    value = section.get('product_id')
    if value is None and isinstance(section.get('condition_assignment'), dict):
        value = section['condition_assignment'].get('product_id')
    return value


def check_product_keys(keys):
    """Folder-safe model keys: pattern, no '.'/'..', no case-insensitive duplicates (models_product_id_unsafe)."""
    seen = {}
    for key in keys:
        if not isinstance(key, str) or KEY_PATTERN.fullmatch(key) is None or key in ('.', '..'):
            raise NumericContractError('models_product_id_unsafe', f'{key!r}: {KEY_PATTERN.pattern}')
        folded = key.lower()
        if folded in seen:
            raise NumericContractError('models_product_id_unsafe', f'{seen[folded]!r} and {key!r} differ only by case')
        seen[folded] = key


def _file_sha(root, relative, label):
    from .numeric_images import resolve_path
    try:
        return _sha256_bytes(resolve_path(Path(root), relative).read_bytes())
    except (OSError, NumericContractError) as exc:
        raise NumericContractError('models_asset_file_unreadable', f'{label}: {relative}: {exc}') from exc


def _reference_path(section, key):
    ref = section.get(key)
    if ref is None:
        return None
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str):
        raise NumericContractError('invalid_models_config', f'{key} must be an object with a path')
    return ref['path']


def load_models(models_path, cfg, config_real=None, config_products=None):
    """Validate models.yaml against the loaded config and load the datasets of the models whose data exists.

    Returns {'path', 'sha256', 'target_product_id', 'models': {key: entry}}. Entry keys: product_id, section, status
    (configured|assets_pending), dataset/root/manifest_path/product/real (configured only), data_dir (as written),
    manifest_sha256, asset_manifest_sha256, electrode_import_record_sha256 (configured only).
    config_real: whether the config reference/target are real_hotspot_v1 sources (a mixed model is refused).
    config_products: {'reference': product_id, 'target': product_id} of the datasets actually loaded; when given they
    replace the declared products in the target / config-side checks (a declaration that differs from the dataset
    fails earlier in the dataset load). Without it the declared products are used.
    A pending model's section is not inspected beyond data_dir/manifest_path/product_id (checked when its data exists)."""
    import yaml
    from . import pipeline
    from .correspondence import _dataset_product
    from .numeric_images import resolve_path
    path = Path(models_path).resolve()
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw.decode('utf-8'))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise NumericContractError('invalid_models_config', str(exc)) from exc
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA:
        raise NumericContractError('unsupported_models_config', f'schema_version must be {SCHEMA}')
    if set(data) - {'schema_version', 'target_product_id', 'models'}:
        raise NumericContractError('invalid_models_config', f'unknown keys {sorted(set(data) - {"schema_version", "target_product_id", "models"})}')
    config_dir = Path(cfg['_config_dir']).resolve()
    if path.parent != config_dir:
        raise NumericContractError('models_config_dir_mismatch', f'{path.parent} != {config_dir}')
    products = dict(config_products) if config_products else {name: section_product(cfg.get(name)) for name in ('reference', 'target')}
    target_product = products.get('target')
    if not isinstance(data.get('target_product_id'), str) or data['target_product_id'] != target_product:
        raise NumericContractError('models_target_mismatch', f'{data.get("target_product_id")!r} != config target {target_product!r}')
    sections = data.get('models')
    if not isinstance(sections, dict) or not sections:
        raise NumericContractError('invalid_models_config', 'models must be a non-empty mapping')
    check_product_keys(list(sections))
    config_side = {products.get('reference'), products.get('target')} - {None}
    for key in sections:
        if key in config_side:
            raise NumericContractError('models_product_is_config_side', key)
    models = {}
    for key in sorted(sections):
        section = sections[key]
        if not isinstance(section, dict) or not isinstance(section.get('data_dir'), str):
            raise NumericContractError('numeric_input_required', key)
        declared = section_product(section)
        if declared is not None and declared != key:
            raise NumericContractError('models_section_product_mismatch', f'{key}: section declares {declared!r}')
        manifest_path = section.get('manifest_path', 'manifest.json')
        root = pipeline._numeric_path(cfg, section['data_dir'])
        entry = {'product_id': key, 'section': section, 'data_dir': section['data_dir']}
        if not isinstance(manifest_path, str):
            raise NumericContractError('invalid_models_config', f'{key}.manifest_path must be a string')
        if not (Path(root)/manifest_path).exists():
            manifest_file = None   # no manifest.json yet: the 1st-round import has not run for this model
        else:
            manifest_file = resolve_path(root, manifest_path)   # refuses escaping or non-portable paths
        if manifest_file is None:
            entry['status'] = ASSETS_PENDING
            models[key] = entry
            continue
        real_source = pipeline._real_source(cfg, section)
        if config_real is not None and (real_source is not None) != bool(config_real):
            raise NumericContractError('mixed_real_and_numeric_source_formats', key)
        if real_source is not None:
            configured = pipeline._configured_real(cfg, section, real_source)
            if configured is None:
                raise NumericContractError('explicit_condition_assignments_required', key)
            root, dataset = configured
        else:
            root, dataset = pipeline._numeric_input(cfg, section)
        product = _dataset_product(dataset, key)
        if product != key:
            raise NumericContractError('models_dataset_product_mismatch', f'{key}: dataset product is {product!r}')
        entry.update(status=CONFIGURED, dataset=dataset, root=root, manifest_path=manifest_path, product=product,
                     real=real_source is not None, manifest_sha256=_sha256_bytes(manifest_file.read_bytes()))
        relative = _reference_path(section, 'asset_manifest')
        entry['asset_manifest_sha256'] = None if relative is None else _file_sha(root, relative, f'{key}.asset_manifest')
        relative = _reference_path(section, 'electrode_import_record')
        entry['electrode_import_record_sha256'] = None if relative is None else _file_sha(root, relative, f'{key}.electrode_import_record')
        models[key] = entry
    return {'path': str(path), 'sha256': _sha256_bytes(raw), 'target_product_id': data['target_product_id'], 'models': models}


def derive_config(cfg, loaded, key):
    """compare-windows --model: a shallow copy of cfg with reference = the original target section, target = the model section.

    No path is rewritten (models.yaml shares the config folder, so _config_dir resolves both sections); output_dir, map,
    review_rois and compare stay as they are. The model must be configured."""
    entry = loaded['models'].get(key)
    if entry is None:
        raise NumericContractError('model_not_in_models', key)
    if entry['status'] != CONFIGURED:
        raise NumericContractError('model_assets_pending', key)
    derived = dict(cfg)
    derived['reference'] = cfg['target']
    derived['target'] = entry['section']
    return derived


def model_provenance(loaded, key):
    """local_delta.json provenance.model for a configured model (inputs of the model side)."""
    entry = loaded['models'][key]
    return {'product_id': entry['product_id'], 'models_sha256': loaded['sha256'], 'manifest_sha256': entry['manifest_sha256'],
            'asset_manifest_sha256': entry['asset_manifest_sha256'],
            'electrode_import_record_sha256': entry['electrode_import_record_sha256']}


def _existing_products(path):
    """(a product_id, b product_id) of an existing local_delta.json, or None when it cannot be read as one."""
    import json
    try:
        products = json.loads(Path(path).read_text(encoding='utf-8'))['products']
        return products['a']['product_id'], products['b']['product_id']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare_model_run(cfg, models_path, key, out):
    """Everything compare-windows --model checks before it may write anything. Returns (derived cfg, provenance.model).

    Raises NumericContractError (model_out_required, models errors, model_not_in_models, model_assets_pending,
    model_out_conflict); the caller writes no file for these."""
    from . import pipeline
    if out is None:
        raise NumericContractError('model_out_required', '--model 은 --out 이 필요합니다(기본 출력 폴더는 reference 용)')
    loaded = load_models(models_path, cfg)
    derived = derive_config(cfg, loaded, key)
    target_product = section_product(cfg.get('target'))
    out_path = Path(out).resolve()
    if cfg.get('output_dir') is not None and out_path == Path(pipeline._numeric_path(cfg, cfg['output_dir'])).resolve():
        raise NumericContractError('model_out_conflict', f'--out {out} 은 config 기본 compare-windows 출력 폴더입니다')
    existing = out_path/'local_delta.json'
    if existing.exists() and _existing_products(existing) != (target_product, key):
        raise NumericContractError('model_out_conflict', f'{existing} 의 제품 쌍이 ({target_product}, {key}) 와 다릅니다')
    return derived, model_provenance(loaded, key)


def model_pair_id(direction, query_roi_id, rank, product_id, centre):
    """Hash of direction, query roi_id, rank, model product and candidate centre (no time, score or user id)."""
    raw = '|'.join(['model', direction, query_roi_id, str(rank), product_id, repr(float(centre[0])), repr(float(centre[1]))])
    return 'confirmed_model_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
