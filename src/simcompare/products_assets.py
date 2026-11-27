"""Product assets (pasx/T5): generation store, staleness verdicts and `products-step` 01 / 02.

Per product, below BASE (the folder of products.yaml):

  data/products/<P>/hotspots/CURRENT.json          pointer {generation, complete_sha256}
  data/products/<P>/hotspots/g<NNNN>-<id8>/        manifest.json, assets/, expected_cases.txt, source_inventory.json, complete.json
  data/products/<P>/side/CURRENT.json
  data/products/<P>/side/g<NNNN>-<id8>/            side.json, complete.json
  data/products/<P>/summary.json                   one file (temporary file + os.replace), carries its own `deps`
  data/products/<P>/asset_log.jsonl                one line per job (append + flush + fsync)
  data/_runs/<RUN_TAG>/products/<P>/<kind>/        old generations and left-over staging folders (nothing is deleted)

Write order of one generation (PLAN-v2 3.2, v2.1 D/F):
  1. everything into `.<kind>.staging-<id8>/`; 2. complete.json (`asset_generation_v1`) last; 3. every file handle closed;
  4. folder renamed to `g<NNNN>-<id8>` (a new id8 when the name is taken); 5. CURRENT.json replaced through a temporary
  file = PUBLISHED. 6. Only then old generations (all but the current and the one before) move to `data/_runs/<RUN_TAG>`.
  A failed archive step never reverts the pointer; the next run retries it (`pending_archive` in asset_log).
  One writer at a time per repository (the editor check of the Run serialises it); there is no lock file.

Readers use `open_generation` only. It checks pointer -> folder -> complete.json (sha256 in the pointer) -> `files`
(size and sha256 of every payload file). A mask seal for `load_real_manifest(verify='seal')` is built from complete.json
by `Generation.seal()` and nowhere else: no mask is hashed or decoded when the verdict is read.

Verdicts (`check_assets`, `asset_status`): state in 정상 / 낡음 / 없음 / 손상, plus reason codes:
  01: source_changed, group_union_changed, import_options_changed, import_basis_changed, hotspot_root_changed
  side: hotspots_generation_changed, side_basis_changed, product_block_changed, pair_table_changed,
        distance_table_changed, side_binding_changed
  summary: side_changed, density_changed, model_group_changed, product_block_changed, cno_changed,
        diameter_subset_changed, summary_basis_changed
  없음: asset_missing / hotspots_unavailable / side_unavailable; 손상: asset_corrupt:<reason>
Warnings (not reasons): source_stat_changed_content_same.
"""
from __future__ import annotations

import datetime
import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import compare2_side, products_config as pc
from .adapters import real_hotspot
from .numeric_schema import NumericContractError

IMPORT_BASIS = 'import_hotspots_v1'
COMPLETE_SCHEMA = 'asset_generation_v1'
POINTER_SCHEMA = 'asset_pointer_v1'
INVENTORY_SCHEMA = 'source_inventory_v1'
KINDS = ('hotspots', 'side')
PAYLOAD = {'hotspots': ('manifest.json', 'expected_cases.txt', 'source_inventory.json'), 'side': ('side.json',)}
GEN_RE = re.compile(r'^g(\d{4,})-([0-9a-f]{8})$')
RUN_TAG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
POINTER = 'CURRENT.json'
COMPLETE = 'complete.json'
SUMMARY = 'summary.json'
ASSET_LOG = 'asset_log.jsonl'
OK, STALE, MISSING, CORRUPT = '정상', '낡음', '없음', '손상'
ACTIONS = ('build', 'skip', 'fail', 'pending_archive')
SOURCE_STAT_WARNING = 'source_stat_changed_content_same'


# import_hotspots runs with its own default; the default is part of the fingerprint (a changed default stales 01)
MAX_INPUT_PIXELS = inspect.signature(real_hotspot.import_hotspots).parameters['max_input_pixels'].default


class AssetError(NumericContractError):
    """code: asset_missing | asset_corrupt:<reason> | asset_* ; state: 없음 / 손상 when the error is a verdict."""

    def __init__(self, code: str, detail: str = '', state: Optional[str] = None):
        super().__init__(code, detail)
        self.state = state


def _corrupt(reason: str, detail: str = '') -> AssetError:
    return AssetError('asset_corrupt:' + reason, detail, CORRUPT)


# ----------------------------------------------------------------------------------------------------- small helpers

def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(str(path), 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(document) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def _parse_json(raw: bytes):
    return json.loads(raw.decode('utf-8'), parse_constant=compare2_side._refuse_constant,
                      object_pairs_hook=compare2_side._no_duplicate_keys)


def _new_id8() -> str:
    return uuid.uuid4().hex[:8]


def utc_run_tag() -> str:
    return datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')


def _utc_now() -> str:
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def check_run_tag(tag: str) -> str:
    if not isinstance(tag, str) or not RUN_TAG_RE.fullmatch(tag):
        raise NumericContractError('invalid_run_tag', 'run tag %r: use ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' % (tag,))
    return tag


def _write_file(path: Path, data: bytes) -> None:
    with open(str(path), 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _write_atomic(path: Path, data: bytes) -> None:
    """Temporary file + os.replace in the same folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.%s.tmp-%d-%s' % (path.name, os.getpid(), _new_id8()))
    try:
        _write_file(tmp, data)
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


_CODE_SHA: Dict[str, str] = {}


def code_sha256() -> str:
    """Display value only (never used for a verdict): sha256 over the sorted relative paths and bytes of the package."""
    if 'value' not in _CODE_SHA:
        root = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        for path in sorted(root.rglob('*.py')):
            digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0' + path.read_bytes() + b'\0')
        _CODE_SHA['value'] = digest.hexdigest()
    return _CODE_SHA['value']


# ----------------------------------------------------------------------------------------------------------- layout

def products_dir(base, product: str) -> Path:
    return Path(base) / 'data' / 'products' / product


def kind_dir(base, product: str, kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError('kind must be one of %s: %r' % (KINDS, kind))
    return products_dir(base, product) / kind


def check_safe(base, product: str) -> None:
    """The product name follows the T3 rules; no existing component of data/products/<P> or data/_runs is a symlink."""
    pc.check_name(product, 'product')
    pc._reject_symlink_components(Path(base), 'data/products/%s' % product)
    pc._reject_symlink_components(Path(base), 'data/_runs')


def _safe_relative(rel) -> str:
    if (not isinstance(rel, str) or not rel or rel.startswith('/') or '\\' in rel
            or any(part in ('', '.', '..') for part in rel.split('/'))):
        raise _corrupt('unsafe_path', repr(rel))
    return rel


# --------------------------------------------------------------------------------------------------------- generation

class Generation(object):
    """A published generation, opened and checked by `open_generation`."""

    def __init__(self, base: Path, product: str, kind: str, path: Path, complete: dict, complete_sha256: str):
        self.base, self.product, self.kind, self.path = base, product, kind, path
        self.complete, self.complete_sha256 = complete, complete_sha256

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def relative_path(self) -> str:
        return 'data/products/%s/%s/%s' % (self.product, self.kind, self.name)

    def file_sha256(self, rel: str) -> str:
        for entry in self.complete['files']:
            if entry['path'] == rel:
                return entry['sha256']
        raise KeyError(rel)

    def seal(self) -> dict:
        """The mask seal of a hotspots generation, made from complete.json only (never stored anywhere else)."""
        if self.kind != 'hotspots':
            raise ValueError('only a hotspots generation has a mask seal')
        verification = self.complete['verification']
        return {'schema': 'mask_seal_v1', 'manifest_sha256': verification['manifest_sha256'],
                'masks': [dict(entry) for entry in self.complete['masks']], 'verification': verification['masks']}


def _read_pointer(kdir: Path) -> Optional[dict]:
    path = kdir / POINTER
    if path.is_symlink():
        raise _corrupt('pointer_symlink')
    if not path.exists():
        return None
    try:
        pointer = _parse_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise _corrupt('pointer_unreadable', str(exc)) from exc
    if (not isinstance(pointer, dict) or not isinstance(pointer.get('generation'), str)
            or not isinstance(pointer.get('complete_sha256'), str)):
        raise _corrupt('pointer_fields')
    if not GEN_RE.fullmatch(pointer['generation']):
        raise _corrupt('pointer_generation_name', pointer['generation'])
    return pointer


def open_generation(base, product: str, kind: str) -> Generation:
    """Pointer -> generation folder -> complete.json -> payload files. Raises AssetError: state 없음 (no pointer) or
    손상 (`asset_corrupt:<reason>`). The mask files are not touched."""
    base = Path(base)
    kdir = kind_dir(base, product, kind)
    pointer = _read_pointer(kdir)
    if pointer is None:
        raise AssetError('asset_missing', '%s/%s has no %s' % (product, kind, POINTER), MISSING)
    gdir = kdir / pointer['generation']
    if gdir.is_symlink() or not gdir.is_dir():
        raise _corrupt('generation_folder_missing', pointer['generation'])
    try:
        raw = (gdir / COMPLETE).read_bytes()
    except OSError as exc:
        raise _corrupt('complete_missing', str(exc)) from exc
    if sha256_bytes(raw) != pointer['complete_sha256']:
        raise _corrupt('complete_sha256_mismatch')
    try:
        complete = _parse_json(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise _corrupt('complete_unreadable', str(exc)) from exc
    if not isinstance(complete, dict) or complete.get('schema_version') != COMPLETE_SCHEMA:
        raise _corrupt('complete_schema')
    if complete.get('generation') != gdir.name:
        raise _corrupt('generation_name_mismatch', '%r != %r' % (complete.get('generation'), gdir.name))
    if complete.get('product') != product:
        raise _corrupt('product_mismatch', repr(complete.get('product')))
    if complete.get('kind') != kind:
        raise _corrupt('kind_mismatch', repr(complete.get('kind')))
    files = complete.get('files')
    if not isinstance(files, list) or not all(isinstance(e, dict) for e in files):
        raise _corrupt('files_field')
    seen = set()
    for entry in files:
        rel = _safe_relative(entry.get('path'))
        seen.add(rel)
        target = gdir / rel
        try:
            if target.stat().st_size != entry.get('size'):
                raise _corrupt('file_size_mismatch', rel)
            if sha256_file(target) != entry.get('sha256'):
                raise _corrupt('file_sha256_mismatch', rel)
        except OSError as exc:
            raise _corrupt('payload_file_missing', '%s: %s' % (rel, exc)) from exc
    for rel in PAYLOAD[kind]:
        if rel not in seen:
            raise _corrupt('payload_not_listed', rel)
    if kind == 'hotspots':
        verification, masks = complete.get('verification'), complete.get('masks')
        if (not isinstance(verification, dict) or verification.get('masks') != 'full'
                or not isinstance(masks, list) or not all(isinstance(m, dict) for m in masks)):
            raise _corrupt('verification_fields')
        manifest_sha = [e['sha256'] for e in files if e['path'] == 'manifest.json'][0]
        if verification.get('manifest_sha256') != manifest_sha:
            raise _corrupt('seal_manifest_mismatch')
    return Generation(base, product, kind, gdir, complete, pointer['complete_sha256'])


def load_hotspots_manifest(base, product: str, verify: str = 'seal'):
    """(Generation, manifest) of the current hotspots generation. verify='seal' uses the seal of `open_generation`."""
    from .adapters.real_hotspot import load_real_manifest
    generation = open_generation(base, product, 'hotspots')
    seal = generation.seal() if verify == 'seal' else None
    return generation, load_real_manifest(generation.path, verify=verify, seal=seal)


# ---------------------------------------------------------------------------------------- publish (steps 1-6) and fault points
# The functions below are separate module-level names so that a test can raise at each point.

def _write_complete(staging: Path, document: dict) -> str:
    raw = _json_bytes(document)
    _write_file(staging / COMPLETE, raw)
    return sha256_bytes(raw)


def _rename_generation(staging: Path, target: Path) -> None:
    os.replace(str(staging), str(target))


def _write_pointer(kdir: Path, generation: str, complete_sha256: str) -> None:
    _write_atomic(kdir / POINTER, _json_bytes({'schema_version': POINTER_SCHEMA, 'generation': generation,
                                               'complete_sha256': complete_sha256}))


def _move_to_runs(base: Path, source: Path, run_tag: str, product: str, kind: str) -> Path:
    destination = Path(base) / 'data' / '_runs' / run_tag / 'products' / product / kind
    destination.mkdir(parents=True, exist_ok=True)
    target, counter = destination / source.name, 1
    while target.exists():
        counter += 1
        target = destination / ('%s-%d' % (source.name, counter))
    shutil.move(str(source), str(target))
    return target


def _generation_numbers(kdir: Path) -> Dict[str, int]:
    found = {}
    if kdir.is_dir():
        for entry in kdir.iterdir():
            match = GEN_RE.fullmatch(entry.name)
            if match and entry.is_dir() and not entry.is_symlink():
                found[entry.name] = int(match.group(1))
    return found


def _file_entry(root: Path, rel: str) -> dict:
    path = root / rel
    return {'path': rel, 'size': path.stat().st_size, 'sha256': sha256_file(path)}


class _Stream(object):
    """A text stream that hands every non-empty write to `sink` (the inner commands print their errors to a stream)."""

    def __init__(self, sink: Callable[[str], None]):
        self.sink = sink

    def write(self, text: str) -> int:
        if text.strip():
            self.sink(text.rstrip('\n'))
        return len(text)

    def flush(self) -> None:
        pass


def publish_generation(base, product: str, kind: str, build: Callable[[Path], Optional[dict]], *, depends: Sequence[dict],
                       fingerprint: Optional[dict] = None, started: Optional[float] = None,
                       extra_fields: Optional[dict] = None) -> Generation:
    """Steps 1-5 of the write order. `build(staging)` must create the staging folder with the payload files and may
    return extra complete.json fields (fingerprint, verification, masks, maps_src, optional_files = extra payload paths
    to list in `files`). The archive step (6) is the caller's."""
    base = Path(base)
    kdir = kind_dir(base, product, kind)
    kdir.mkdir(parents=True, exist_ok=True)
    started = time.time() if started is None else started
    staging = kdir / ('.%s.staging-%s' % (kind, _new_id8()))
    extras = dict(build(staging) or {})
    if not staging.is_dir():
        raise AssetError('asset_publish_failed', 'build made no staging folder: %s' % staging)
    for rel in PAYLOAD[kind]:
        if not (staging / rel).is_file():
            raise AssetError('asset_publish_failed', 'payload file missing in staging: %s' % rel)
    pointer = _read_pointer_or_none(kdir)
    numbers = [number for number in _generation_numbers(kdir).values()]
    if pointer is not None:
        numbers.append(int(GEN_RE.fullmatch(pointer['generation']).group(1)))
    number = max(numbers) + 1 if numbers else 1
    payload = list(PAYLOAD[kind])
    optional = [rel for rel in extras.pop('optional_files', [])]
    files = [_file_entry(staging, rel) for rel in payload + optional]
    for _attempt in range(8):
        name = 'g%04d-%s' % (number, _new_id8())
        target = kdir / name
        if target.exists():
            continue
        document = {'schema_version': COMPLETE_SCHEMA, 'product': product, 'kind': kind, 'generation': name,
                    'created_at': _utc_now(), 'depends': list(depends), 'files': files, 'fingerprint': fingerprint or {},
                    'elapsed_s': round(time.time() - started, 3), 'code_sha256': code_sha256()}
        document.update(extra_fields or {})
        document.update(extras)
        complete_sha256 = _write_complete(staging, document)
        try:
            _rename_generation(staging, target)
        except OSError:
            if target.exists():
                continue            # the name was taken between the check and the rename: a new id8
            raise
        break
    else:
        raise AssetError('asset_publish_failed', 'no free generation name in %s' % kdir)
    _write_pointer(kdir, name, complete_sha256)          # published
    return open_generation(base, product, kind)


def _read_pointer_or_none(kdir: Path) -> Optional[dict]:
    """The pointer when it is readable; a corrupt pointer does not stop a new publish (it will be replaced)."""
    try:
        return _read_pointer(kdir)
    except AssetError:
        return None


def archive_names(kdir: Path) -> List[str]:
    """Generation folders to archive: all but the current and the one before it. With a valid pointer, folders newer
    than the pointer (never published) go too; with no pointer at all every generation folder is an orphan. A corrupt
    pointer archives nothing."""
    try:
        pointer = _read_pointer(kdir)
    except AssetError:
        return []
    numbers = _generation_numbers(kdir)
    if pointer is None:
        return sorted(numbers)
    if pointer['generation'] not in numbers:
        return []
    current = numbers[pointer['generation']]
    keep = {pointer['generation']}
    older = [name for name, number in numbers.items() if number < current]
    if older:
        keep.add(max(older, key=lambda name: numbers[name]))
    return sorted(name for name in numbers if name not in keep)


def archive_old(base, product: str, kind: str, run_tag: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Move every archivable generation. Returns (moved names, [(name, error)]); an error is not raised."""
    kdir = kind_dir(base, product, kind)
    moved, failed = [], []
    for name in archive_names(kdir):
        try:
            _move_to_runs(Path(base), kdir / name, run_tag, product, kind)
            moved.append(name)
        except Exception as exc:                      # a failed move is retried by the next run
            failed.append((name, '%s: %s' % (type(exc).__name__, exc)))
    return moved, failed


def sweep_leftovers(base, product: str, run_tag: str) -> List[str]:
    """Staging folders and temporary files of an interrupted run move to data/_runs (nothing is deleted)."""
    moved = []
    pdir = products_dir(base, product)
    places = [(kind_dir(base, product, kind), kind) for kind in KINDS] + [(pdir, 'summary')]
    for folder, kind in places:
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if entry.name.startswith('.') and ('.staging-' in entry.name or '.tmp-' in entry.name):
                _move_to_runs(Path(base), entry, run_tag, product, kind)
                moved.append('%s/%s' % (kind, entry.name))
    return moved


# ------------------------------------------------------------------------------------------------------------ asset_log

class AssetLog(object):
    def __init__(self, base, product: str, run_tag: str):
        self.path = products_dir(base, product) / ASSET_LOG
        self.run_tag = run_tag

    def append(self, step: str, action: str, status: str, reasons: Sequence[str] = (), generation: Optional[str] = None,
               elapsed_s: float = 0.0, exit_code: int = 0, error_code: Optional[str] = None, **extra) -> dict:
        if action not in ACTIONS:
            raise ValueError(action)
        line = {'at': _utc_now(), 'run_tag': self.run_tag, 'step': step, 'action': action, 'status': status,
                'reasons': list(reasons), 'generation': generation, 'elapsed_s': round(elapsed_s, 3),
                'exit_code': exit_code, 'error_code': error_code}
        line.update(extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path), 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return line


# --------------------------------------------------------------------------------------------------- source inventory

def _walk_source(root: Path) -> Tuple[List[str], Dict[str, Tuple[int, int]]]:
    """(sorted folder paths, {file path: (size, mtime_ns)}), relative and posix. Symlinks are not followed."""
    folders, files = [], {}
    for current, dirnames, filenames in os.walk(str(root), followlinks=False):
        dirnames.sort()
        for name in dirnames:
            folders.append((Path(current) / name).relative_to(root).as_posix())
        for name in sorted(filenames):
            path = Path(current) / name
            stat = path.stat()
            files[path.relative_to(root).as_posix()] = (int(stat.st_size), int(stat.st_mtime_ns))
    return sorted(folders), files


def build_inventory(root: Path, rel_root: str, walked=None) -> dict:
    folders, files = walked if walked is not None else _walk_source(root)
    return {'schema_version': INVENTORY_SCHEMA, 'root': rel_root, 'folders': folders,
            'files': [{'path': rel, 'size': files[rel][0], 'mtime_ns': files[rel][1], 'sha256': sha256_file(root / rel)}
                      for rel in sorted(files)]}


def compare_inventory(root: Path, inventory: dict) -> Tuple[bool, List[str]]:
    """(changed, warnings). Same folder set and file set; each file has the same size and mtime or the same sha256."""
    if not root.is_dir():
        return True, []
    folders, files = _walk_source(root)
    if folders != inventory.get('folders') or sorted(files) != [e['path'] for e in inventory.get('files', [])]:
        return True, []
    warnings = []
    for entry in inventory['files']:
        size, mtime_ns = files[entry['path']]
        if size == entry['size'] and mtime_ns == entry['mtime_ns']:
            continue
        if size != entry['size'] or sha256_file(root / entry['path']) != entry['sha256']:
            return True, []
        if SOURCE_STAT_WARNING not in warnings:
            warnings.append(SOURCE_STAT_WARNING)
    return False, warnings


# ------------------------------------------------------------------------------------------------------------ verdicts

class Verdict(object):
    def __init__(self, state: str, reasons: Sequence[str] = (), warnings: Sequence[str] = (),
                 generation: Optional[Generation] = None):
        self.state, self.reasons, self.warnings, self.generation = state, list(reasons), list(warnings), generation

    def as_tuple(self) -> Tuple[str, List[str]]:
        return self.state, list(self.reasons)

    def __repr__(self):
        return 'Verdict(%s, %s)' % (self.state, self.reasons)


def _unique(items: Sequence[str]) -> List[str]:
    result: List[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def hotspots_fingerprint(config: pc.ProductsConfig, product: str, results: dict) -> dict:
    """PLAN-v2 3.5 / v2.1 C, 01 row: import basis, root, options, group union, rule constants."""
    from .adapters import real_hotspot as rh
    group = results[config.group_of(product)]
    return {'import_basis': IMPORT_BASIS,
            'hotspot_root': config.products[product]['hotspot_root'],
            'import_options': {'include_cno': bool(config.products[product]['include_cno']),
                               'max_input_pixels': MAX_INPUT_PIXELS,
                               'expected_cases_sha256': sha256_bytes(group.expected_cases_text().encode('utf-8'))},
            'group_union_sha256': group.union_sha256,
            'rules': {'occupancy_rule': rh.HOTSPOT_OCCUPANCY_RULE, 'occupancy_threshold': rh.HOTSPOT_GRAY_THRESHOLD,
                      'contract': rh.CONTRACT_VERSION, 'case_exclusion_rule': rh.CASE_EXCLUSION_RULE}}


def _hotspots_reasons(recorded: dict, now: dict) -> List[str]:
    reasons = []
    if recorded.get('import_basis') != now['import_basis'] or recorded.get('rules') != now['rules']:
        reasons.append('import_basis_changed')
    if recorded.get('hotspot_root') != now['hotspot_root']:
        reasons.append('hotspot_root_changed')
    old, new = recorded.get('import_options') or {}, now['import_options']
    if recorded.get('group_union_sha256') != now['group_union_sha256'] \
            or old.get('expected_cases_sha256') != new['expected_cases_sha256']:
        reasons.append('group_union_changed')
    if any(old.get(key) != new[key] for key in ('include_cno', 'max_input_pixels')):
        reasons.append('import_options_changed')
    return reasons


def hotspots_verdict(base, config: pc.ProductsConfig, product: str, results: dict) -> Verdict:
    try:
        generation = open_generation(base, product, 'hotspots')
    except AssetError as exc:
        return Verdict(exc.state, ['asset_missing' if exc.state == MISSING else exc.code])
    reasons = _hotspots_reasons(generation.complete.get('fingerprint') or {}, hotspots_fingerprint(config, product, results))
    try:
        inventory = _parse_json((generation.path / 'source_inventory.json').read_bytes())
        changed, warnings = compare_inventory(Path(base) / config.products[product]['hotspot_root'], inventory)
    except (OSError, ValueError) as exc:
        return Verdict(CORRUPT, ['asset_corrupt:source_inventory_unreadable'], generation=generation)
    if changed:
        reasons.append('source_changed')
    return Verdict(STALE if reasons else OK, _unique(reasons), warnings, generation)


def product_compare2_yaml(config: pc.ProductsConfig, product: str) -> str:
    """gen.product.<P>.compare2.yaml: the pair schema with the product in both sections (R3:5). The side file does
    not name its role, so a side made from this file equals the side made from a pair file."""
    doc = {'schema_version': 'compare2_config_v1', 'numeric_config': 'gen.product.%s.real.yaml' % product,
           'output_dir': 'data/products/%s/compare2' % product, 'density_dir': config.common['density_dir'],
           'model_groups': json.loads(json.dumps(config.common['model_groups'])),
           'products': {'reference': pc._compare2_product(config, product),
                        'target': pc._compare2_product(config, product)}}
    if config.common.get('report') is not None:
        doc['report'] = json.loads(json.dumps(config.common['report']))
    if config.common.get('lambda_diameters_um') is not None:
        doc['lambda_diameters_um'] = json.loads(json.dumps(config.common['lambda_diameters_um']))
    return pc._dump_yaml(doc)


def product_settings_names(product: str) -> Tuple[str, str]:
    return 'gen.product.%s.real.yaml' % product, 'gen.product.%s.compare2.yaml' % product


def ensure_product_settings(config: pc.ProductsConfig, product: str, hotspots: Generation) -> Path:
    """Write (only when the bytes change) the two generated settings of `product` for its current hotspots generation.
    Returns the compare2 settings path. Run again whenever the generation changes."""
    real_name, compare2_name = product_settings_names(product)
    pc.write_generated(config, {
        real_name: pc.product_real_yaml(config, product, {product: hotspots.relative_path}),
        compare2_name: product_compare2_yaml(config, product)})
    return config.base / compare2_name


def side_fingerprint(hotspots: Generation, binding: dict) -> dict:
    return {'hotspots_generation': hotspots.name, 'hotspots_manifest_sha256': hotspots.file_sha256('manifest.json'),
            'binding': binding, 'side_basis': compare2_side.SIDE_BASIS}


def _json_normal(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


_BINDING_REASON = {'manifest_sha256': 'hotspots_generation_changed', 'section_sha256': 'product_block_changed',
                   'pair_table_sha256': 'pair_table_changed', 'distance_table_sha256': 'distance_table_changed',
                   'top_buckets': 'side_binding_changed', 'side_basis': 'side_basis_changed',
                   'rules': 'side_basis_changed'}


def _side_reasons(recorded: dict, now: dict) -> List[str]:
    reasons = []
    if (recorded.get('hotspots_generation') != now['hotspots_generation']
            or recorded.get('hotspots_manifest_sha256') != now['hotspots_manifest_sha256']):
        reasons.append('hotspots_generation_changed')
    if recorded.get('side_basis') != now['side_basis']:
        reasons.append('side_basis_changed')
    then, current = recorded.get('binding') or {}, now['binding']
    for key in sorted(set(then) | set(current)):
        if _json_normal(then.get(key)) == _json_normal(current.get(key)):
            continue
        if key == 'section_sha256' and 'hotspots_generation_changed' in reasons:
            continue          # the section holds the generation path: it moves with the generation
        reasons.append(_BINDING_REASON.get(key, 'side_binding_changed'))
    return _unique(reasons)


def current_side_binding(cfg_pair) -> dict:
    _cfg2, cfg, top = cfg_pair
    return compare2_side.side_binding(cfg, 'reference', top)


def side_verdict(base, config: pc.ProductsConfig, product: str, hotspots: Generation, cfg_pair) -> Verdict:
    try:
        generation = open_generation(base, product, 'side')
    except AssetError as exc:
        return Verdict(exc.state, ['asset_missing' if exc.state == MISSING else exc.code])
    now = side_fingerprint(hotspots, current_side_binding(cfg_pair))
    reasons = _side_reasons(generation.complete.get('fingerprint') or {}, now)
    return Verdict(STALE if reasons else OK, reasons, generation=generation)


def current_summary_deps(cfg2, side_sha256: str) -> dict:
    """The `deps` block compare2-summary would write now (same pieces as compare2_side.run_summary)."""
    from .compare2_inputs import load_density
    product = cfg2.reference
    tables = load_density(cfg2.density_dir, product.model_group)
    group = cfg2.model_groups[product.model_group]
    deps = {'side_sha256': side_sha256,
            'density_sha256': {kind: tables[kind].sha256 for kind in ('line', 'pixel')},
            'model_group': {'name': product.model_group, 'config': asdict(group)},
            'product': compare2_side._product_section(product),
            'lambda_diameters_um': compare2_side._listed(cfg2),
            'cno': compare2_side._cno_deps(product),
            'summary_basis': compare2_side.SUMMARY_BASIS}
    return json.loads(json.dumps(deps, allow_nan=False, default=str))


_SUMMARY_REASON = {'side_sha256': 'side_changed', 'density_sha256': 'density_changed',
                   'model_group': 'model_group_changed', 'product': 'product_block_changed',
                   'lambda_diameters_um': 'diameter_subset_changed', 'cno': 'cno_changed',
                   'summary_basis': 'summary_basis_changed'}


def summary_verdict(base, product: str, side: Generation, cfg_pair) -> Verdict:
    path = products_dir(base, product) / SUMMARY
    if not path.exists():
        return Verdict(MISSING, ['asset_missing'])
    try:
        document = _parse_json(path.read_bytes())
        recorded = document['deps']
        if not isinstance(recorded, dict) or document.get('schema_version') != compare2_side.SUMMARY_SCHEMA:
            raise ValueError('not a summary document')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Verdict(CORRUPT, ['asset_corrupt:summary_unreadable'])
    now = current_summary_deps(cfg_pair[0], side.file_sha256('side.json'))
    reasons = [_SUMMARY_REASON.get(key, 'summary_basis_changed') for key in now
               if _json_normal(recorded.get(key)) != _json_normal(now[key])]
    return Verdict(STALE if reasons else OK, _unique(reasons))


def _load_config(base, config) -> pc.ProductsConfig:
    if isinstance(config, pc.ProductsConfig):
        return config
    return pc.load_products_config(Path(config) if config is not None else Path(base) / 'products.yaml')


def check_assets(base, product: str, config=None, results: Optional[dict] = None) -> Dict[str, Verdict]:
    """{'hotspots': Verdict, 'side': Verdict, 'summary': Verdict}. Reads names, small files and the source tree stat
    (and a source file's bytes only when its size or mtime moved); no mask is read. Writes only the two generated
    settings of the product (same bytes -> no write)."""
    base = Path(base)
    config = _load_config(base, config)
    if product not in config.products:
        raise NumericContractError('pair_unknown_product', 'unknown product %r' % product)
    check_safe(config.base, product)
    results = results if results is not None else pc.check_groups(config)
    hotspots = hotspots_verdict(config.base, config, product, results)
    if hotspots.generation is None:
        pending = Verdict(MISSING, ['hotspots_unavailable'])
        return {'hotspots': hotspots, 'side': pending, 'summary': Verdict(MISSING, ['hotspots_unavailable'])}
    compare2_path = ensure_product_settings(config, product, hotspots.generation)
    cfg_pair = compare2_side.load_pair_config(compare2_path)
    side = side_verdict(config.base, config, product, hotspots.generation, cfg_pair)
    if side.generation is None:
        return {'hotspots': hotspots, 'side': side, 'summary': Verdict(MISSING, ['side_unavailable'])}
    return {'hotspots': hotspots, 'side': side, 'summary': summary_verdict(config.base, product, side.generation, cfg_pair)}


def asset_status(base, product: str, config=None) -> Tuple[str, List[str]]:
    """(state, reason codes) of one product: the worst of the three assets (손상 > 없음 > 낡음 > 정상)."""
    verdicts = check_assets(base, product, config)
    order = (CORRUPT, MISSING, STALE, OK)
    state = min((v.state for v in verdicts.values()), key=order.index)
    reasons = _unique([r for key in ('hotspots', 'side', 'summary') for r in verdicts[key].reasons])
    return state, reasons


# ---------------------------------------------------------------------------------------------------------- the steps

class StepContext(object):
    def __init__(self, config: pc.ProductsConfig, product: str, run_tag: str, log: AssetLog, out, err):
        self.config, self.product, self.run_tag, self.log, self.out, self.err = config, product, run_tag, log, out, err
        self.base = config.base


def _recover(ctx: StepContext) -> None:
    """Start of every run: left-over staging to data/_runs, then archive what is due (retry of pending_archive)."""
    started = time.time()
    moved = sweep_leftovers(ctx.base, ctx.product, ctx.run_tag)
    for item in moved:
        ctx.out('moved left-over %s to data/_runs/%s' % (item, ctx.run_tag))
    _archive_all(ctx, started)


def _archive_all(ctx: StepContext, started: float) -> None:
    for kind in KINDS:
        moved, failed = archive_old(ctx.base, ctx.product, kind, ctx.run_tag)
        for name in moved:
            ctx.out('archived %s/%s/%s' % (ctx.product, kind, name))
        for name, error in failed:
            ctx.err('경고: 보관 실패(다음 실행에서 다시): %s/%s/%s: %s' % (ctx.product, kind, name, error))
            ctx.log.append(kind, 'pending_archive', OK, [], name, time.time() - started, 0, 'archive_failed', detail=error)


def _hotspots_build(ctx: StepContext, fingerprint: dict, results: dict, started: float) -> Generation:
    from .adapters import real_hotspot as rh
    config, product = ctx.config, ctx.product
    rel_root = config.products[product]['hotspot_root']
    root = ctx.base / rel_root
    group = config.group_of(product)
    expected_file = ctx.base / 'data' / 'products' / '_groups' / group / 'expected_cases.txt'
    if sha256_file(expected_file) != fingerprint['import_options']['expected_cases_sha256']:
        raise AssetError('group_files_stale', 'run products-config: %s does not match products.yaml' % expected_file)

    def build(staging: Path) -> dict:
        before = _walk_source(root)
        rh.import_hotspots(root, staging, expected_cases=expected_file, include_cno=fingerprint['import_options']['include_cno'])
        inventory = build_inventory(root, rel_root, before)
        if _walk_source(root) != before:
            raise AssetError('source_changed_during_import', rel_root)
        _write_file(staging / 'source_inventory.json', _json_bytes(inventory))
        seal = rh.make_mask_seal(staging)                    # full verification of every mask, once
        return {'verification': {'masks': seal['verification'], 'manifest_sha256': seal['manifest_sha256']},
                'masks': seal['masks']}

    return publish_generation(ctx.base, product, 'hotspots', build, depends=[], fingerprint=fingerprint, started=started)


def run_step_01(ctx: StepContext, force: bool) -> int:
    started = time.time()
    results = pc.check_groups(ctx.config)
    verdict = hotspots_verdict(ctx.base, ctx.config, ctx.product, results)
    for warning in verdict.warnings:
        ctx.err('경고: %s (%s)' % (warning, ctx.product))
    if verdict.state == OK and not force:
        ctx.out('skip fresh 01 %s' % ctx.product)
        ctx.log.append('01', 'skip', OK, [], verdict.generation.name, time.time() - started, 0, None, warnings=verdict.warnings)
        return 0
    reasons = verdict.reasons if verdict.state != OK else ['forced']
    fingerprint = hotspots_fingerprint(ctx.config, ctx.product, results)
    generation = _hotspots_build(ctx, fingerprint, results, started)
    ctx.out('built 01 %s %s' % (ctx.product, generation.name))
    ctx.log.append('01', 'build', OK, reasons, generation.name, time.time() - started, 0, None, warnings=verdict.warnings)
    _archive_all(ctx, started)
    return 0


def _build_side(ctx: StepContext, hotspots: Generation, compare2_path: Path, cfg_pair, started: float) -> Generation:
    def build(staging: Path) -> dict:
        code = compare2_side.main_side(compare2_path, 'reference', staging, lambda record: None, _Stream(ctx.err))
        if code != 0:
            raise AssetError('compare2_side_failed', 'compare2-side exit %s (%s)' % (code, staging))
        side_document, _sha = compare2_side.read_side(staging)
        binding = side_document['binding']
        if _json_normal(binding) != _json_normal(current_side_binding(cfg_pair)):
            raise AssetError('compare2_side_failed', 'the side binding moved while the side was measured')
        return {'fingerprint': side_fingerprint(hotspots, binding), 'maps_src': {'status': 'none'}}

    depends = [{'kind': 'hotspots', 'generation': hotspots.name, 'complete_sha256': hotspots.complete_sha256}]
    return publish_generation(ctx.base, ctx.product, 'side', build, depends=depends, started=started)


def _build_summary(ctx: StepContext, compare2_path: Path, side: Generation) -> int:
    pdir = products_dir(ctx.base, ctx.product)
    staging = pdir / ('.summary.staging-%s' % _new_id8())
    code = compare2_side.main_summary(compare2_path, 'reference', side.path, staging, lambda record: None, _Stream(ctx.err))
    if code not in (0, 3):
        raise AssetError('compare2_summary_failed', 'compare2-summary exit %s (%s)' % (code, staging))
    raw = (staging / compare2_side.SUMMARY_FILE).read_bytes()
    _write_atomic(pdir / SUMMARY, raw)
    try:
        (staging / compare2_side.SUMMARY_FILE).unlink()
        staging.rmdir()
    except OSError:
        pass                                           # left for the next run's sweep
    return code


def run_step_02(ctx: StepContext, force: bool) -> int:
    started = time.time()
    try:
        hotspots = open_generation(ctx.base, ctx.product, 'hotspots')
    except AssetError as exc:
        raise AssetError('asset_missing', '01 of %s is %s (%s): run products-step 01 first'
                         % (ctx.product, exc.state, exc.code), exc.state) from exc
    compare2_path = ensure_product_settings(ctx.config, ctx.product, hotspots)
    cfg_pair = compare2_side.load_pair_config(compare2_path)
    exit_code = 0
    side = side_verdict(ctx.base, ctx.config, ctx.product, hotspots, cfg_pair)
    if side.state == OK and not force:
        ctx.out('skip fresh 02 side %s' % ctx.product)
        ctx.log.append('02', 'skip', OK, [], side.generation.name, time.time() - started, 0, None, part='side')
        side_generation = side.generation
    else:
        side_started = time.time()
        side_generation = _build_side(ctx, hotspots, compare2_path, cfg_pair, side_started)
        ctx.out('built 02 side %s %s' % (ctx.product, side_generation.name))
        ctx.log.append('02', 'build', OK, side.reasons if side.state != OK else ['forced'], side_generation.name,
                       time.time() - side_started, 0, None, part='side')
        _archive_all(ctx, side_started)
    summary = summary_verdict(ctx.base, ctx.product, side_generation, cfg_pair)
    if summary.state == OK and not force:
        ctx.out('skip fresh 02 summary %s' % ctx.product)
        ctx.log.append('02', 'skip', OK, [], side_generation.name, 0.0, 0, None, part='summary')
        return exit_code
    summary_started = time.time()
    code = _build_summary(ctx, compare2_path, side_generation)
    exit_code = 3 if code == 3 else 0
    ctx.out('built 02 summary %s' % ctx.product)
    ctx.log.append('02', 'build', OK, summary.reasons if summary.state != OK else ['forced'], side_generation.name,
                   time.time() - summary_started, exit_code, None, part='summary')
    return exit_code


def run_products_step(config_path, product: str, step: str, force: bool = False, run_tag: Optional[str] = None,
                      out: Callable[[str], None] = print, err: Optional[Callable[[str], None]] = None) -> int:
    """products-step: 0 = built or skipped fresh, 2 = error, 3 = built with a partial summary."""
    err = err or (lambda text: print(text, file=sys.stderr))
    if step not in ('01', '02'):
        err('오류: invalid_step: step must be 01 or 02')
        return 2
    log = None
    started = time.time()
    try:
        run_tag = check_run_tag(run_tag or utc_run_tag())
        config = pc.load_products_config(config_path)
        if product not in config.products:
            raise NumericContractError('pair_unknown_product', 'unknown product %r' % product)
        check_safe(config.base, product)
        log = AssetLog(config.base, product, run_tag)
        ctx = StepContext(config, product, run_tag, log, out, err)
        pc.run_products_config(config.path)                  # group files only (no product settings)
        _recover(ctx)
        return run_step_01(ctx, force) if step == '01' else run_step_02(ctx, force)
    except pc.GroupCompatibilityError as exc:
        err('오류: %s' % exc)
        _log_failure(log, step, started, 'group_incompatible', exc.codes)
        return 2
    except NumericContractError as exc:
        err('오류: %s' % exc)
        _log_failure(log, step, started, exc.code, [])
        return 2
    except Exception as exc:                                 # an interrupted job: the next run recovers
        err('오류: asset_internal_error: %s: %s' % (type(exc).__name__, exc))
        err(traceback.format_exc().rstrip())
        _log_failure(log, step, started, 'asset_internal_error', [type(exc).__name__])
        return 2


def _log_failure(log: Optional[AssetLog], step: str, started: float, code: str, reasons: Sequence[str]) -> None:
    if log is None:
        return
    try:
        log.append(step, 'fail', 'fail', reasons, None, time.time() - started, 2, code)
    except OSError:
        pass
