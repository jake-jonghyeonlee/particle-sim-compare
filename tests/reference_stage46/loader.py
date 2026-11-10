"""Loader for the frozen 7bcb07a simcompare package (stage 4-6 equivalence oracle).

The frozen sources under ``simcompare_7bcb07a/`` are byte-exact copies of ``src/simcompare`` at commit 7bcb07a and are
never edited; ``DIGESTS.sha256`` pins their sha256 so the check works without git. They are imported under the name
``simcompare_7bcb07a`` with ONE import-time transformation (applied to the in-memory source, never to the stored bytes):
absolute ``from simcompare...`` / ``import simcompare...`` statements are rewired to the frozen package, so the whole
call graph (pipeline helpers, adapters, measurement, numeric_images, ...) is frozen code. Relative imports already
resolve inside the frozen package. No bytecode is cached.

Runtime (non-source) adjustment, documented in tests/test_stage46_equivalence.py:
``roi_export.__file__`` is pointed at the production checkout path so the producer_commit lookup (git ls-files/rev-parse
from ``Path(__file__).parents[2]``) resolves against the repository exactly as it does for production.
"""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
FROZEN_DIR = HERE / 'simcompare_7bcb07a'
FROZEN_NAME = 'simcompare_7bcb07a'
DIGEST_FILE = HERE / 'DIGESTS.sha256'
BASE_COMMIT = '7bcb07a4571ca242416f90b31644a9c4801fa7ba'
_REWIRE = re.compile(r'^(\s*)(from|import)(\s+)simcompare\b', re.MULTILINE)


def rewire(text: str) -> str:
    return _REWIRE.sub(lambda m: f'{m.group(1)}{m.group(2)}{m.group(3)}{FROZEN_NAME}', text)


def read_digests() -> dict:
    entries = {}
    for line in DIGEST_FILE.read_text(encoding='utf-8').splitlines():
        digest, _, name = line.partition('  ')
        entries[name] = digest
    return entries


def verify_digests() -> None:
    """Raise AssertionError unless the frozen tree is exactly the checked-in set of files with the checked-in bytes."""
    expected = read_digests()
    found = {p.relative_to(HERE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(FROZEN_DIR.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    assert set(found) == set(expected), (sorted(set(found) ^ set(expected)))
    changed = sorted(name for name in found if found[name] != expected[name])
    assert not changed, changed


_HASH_TERM = "manifest['source_payload_hash']+assignment_hash+(pair_hash or '')"


def with_case_exclusion_rule(text: str, path) -> str:
    """Second import-time transformation (legacy-calc-align/T2): production's configured input_hash now ends with the
    case-exclusion rule, so the frozen real_hotspot hashes the same way. Without it the asset manifests production writes
    would never match the frozen input_hash. The rule itself (which cases are dropped) is NOT applied to the frozen code."""
    if Path(path).name != 'real_hotspot.py' or Path(path).parent.name != 'adapters':
        return text
    from simcompare.adapters.real_hotspot import CASE_EXCLUSION_RULE
    assert _HASH_TERM in text
    return text.replace(_HASH_TERM, _HASH_TERM + f"+'{CASE_EXCLUSION_RULE}'")


_POLICY_TERM = "'source_policy': {'gray_nonzero_is_occupied': True,"


def with_hotspot_occupancy_rule(text: str, path) -> str:
    """Third import-time transformation (gray63/T1): production's manifests now record ``hotspot_occupancy`` (rule
    gray_gt_63) and ``source_policy.gray_gt_63_is_occupied``, and production's load_real_manifest refuses manifests
    without them. The frozen importer/loader are rewired to write/read the same two fields so frozen-written manifests
    load in production and production-written manifests load in the frozen code. The frozen occupancy computation
    (``!= 0``) is NOT changed; the equivalence fixtures only use gray values 0 and 255, where both rules agree."""
    if Path(path).name != 'real_hotspot.py' or Path(path).parent.name != 'adapters':
        return text
    assert _POLICY_TERM in text
    text = text.replace(_POLICY_TERM, "'hotspot_occupancy': {'rule': 'gray_gt_63', 'threshold': 63, "
                                      "'low_gray_pixels_total': 0}, 'source_policy': {'gray_nonzero_is_occupied': True,")
    return text.replace('gray_nonzero_is_occupied', 'gray_gt_63_is_occupied')


class _Loader(importlib.machinery.SourceFileLoader):
    def source_to_code(self, data, path, *, _optimize=-1):
        text = with_hotspot_occupancy_rule(with_case_exclusion_rule(rewire(bytes(data).decode('utf-8')), path), path)
        return super().source_to_code(text.encode('utf-8'), path, _optimize=_optimize)

    def get_code(self, fullname):
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != FROZEN_NAME and not fullname.startswith(FROZEN_NAME + '.'):
            return None
        relative = fullname.split('.')[1:]
        base = FROZEN_DIR.joinpath(*relative)
        if base.is_dir() and (base / '__init__.py').is_file():
            return importlib.util.spec_from_file_location(
                fullname, base / '__init__.py', loader=_Loader(fullname, str(base / '__init__.py')),
                submodule_search_locations=[str(base)])
        source = base.with_suffix('.py')
        if source.is_file():
            return importlib.util.spec_from_file_location(fullname, source, loader=_Loader(fullname, str(source)))
        return None


def load(production_roi_export: Path | None = None):
    """Import (once) and return the frozen package; verifies the digests first."""
    if FROZEN_NAME in sys.modules:
        return sys.modules[FROZEN_NAME]
    verify_digests()
    sys.meta_path.insert(0, _Finder())
    package = importlib.import_module(FROZEN_NAME)
    roi_export = importlib.import_module(FROZEN_NAME + '.roi_export')
    roi_export.__file__ = str(production_roi_export or HERE.parents[1] / 'src' / 'simcompare' / 'roi_export.py')
    for name in ('cli', 'pipeline', 'candidates', 'confirm_pairs', 'electrode_map', 'local_delta', 'local_delta_report',
                 'correspondence', 'numeric_schema', 'adapters.real_hotspot', 'adapters.electrode_png'):
        importlib.import_module(f'{FROZEN_NAME}.{name}')
    return package
