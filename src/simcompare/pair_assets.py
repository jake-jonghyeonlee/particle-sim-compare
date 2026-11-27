"""`pair-step` (pasx/T12): the pair stages of product mode, built on the product assets of T5.

  pair-step --config products.yaml --pair A B --step compose|ledger|maps|report [--run-tag T] [--cards|--no-cards]

Pair folder (BASE = folder of products.yaml), A is the reference and B the target:

  gen.pair.<A>__<B>.real.yaml / .compare2.yaml        generated (only `compose` writes them; the other steps read them)
  data/compare/<A>__<B>/compare2/                     compare2.json + CSV + compose.json (written last)
  data/compare/<A>__<B>/ledger | ledger_maps | ledger_report/
  data/compare/<A>__<B>/pair_log.jsonl                one line per step (append + flush + fsync)
  data/_runs/<RUN_TAG>/compare/<A>__<B>/<folder>/     the previous output of a folder that is made again (nothing is deleted)

Rules (PLAN-v2 3.2 / 3.5, v2.1 H):
  * A and B are registered, different, in the same group, and the hotspots and side assets of both are 정상
    (`asset_missing` / `asset_stale` with product and reason codes, exit 2). No mask is read.
  * compose is skipped (`skip fresh compose`) when the recorded `compose.json` deps equal the deps computed now and the
    sha256 of compare2.json equals the one compose.json names. Otherwise the old compare2/ moves to data/_runs and
    `compare2-compose` runs.
  * ledger / maps / report first judge the compose again. A stale compose is refused (`pair_compose_stale`, exit 2, the
    reason codes, "put 02 in COMPARE_STEPS"). A fresh one: the old folder of that step moves to data/_runs and the existing
    command runs (no `--candidates`). Running 08 again means all three steps again; there is no resume point.
  * maps leaves the cards out unless `--cards` is given (`ledger-maps --no-cards`, U2).
  * The exit code is the one of the inner command (0 and 3 continue, anything else stops).
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import compare2_side, products_assets as pa, products_config as pc
from .numeric_schema import NumericContractError

STEPS = ('compose', 'ledger', 'maps', 'report')
PAIR_LOG = 'pair_log.jsonl'
STEP_FOLDER = {'compose': 'compare2', 'ledger': 'ledger', 'maps': 'ledger_maps', 'report': 'ledger_report'}
CONTINUE_CODES = (0, 3)
STALE_HINT = 'COMPARE_STEPS 에 02 를 넣으세요 (또는 pair-step --step compose 를 다시 실행)'
_COMMON_REASON = {'model_groups': 'model_group_changed', 'lambda_diameters_um': 'diameter_subset_changed',
                  'top_buckets': 'top_buckets_changed'}
_DEPS_REASON = {'side_sha256': 'side_changed', 'density_sha256': 'density_changed', 'products': 'product_block_changed',
                'cno': 'cno_changed', 'compose_basis': 'compose_basis_changed'}


# --------------------------------------------------------------------------------------------------------------- log

class PairLog(object):
    def __init__(self, base, pair: str, run_tag: str):
        self.path = Path(base) / 'data' / 'compare' / pair / PAIR_LOG
        self.run_tag = run_tag

    def append(self, step: str, action: str, exit_code: int, error_code: Optional[str], elapsed_s: float,
               inputs: Optional[dict] = None, reasons: Sequence[str] = ()) -> dict:
        line = {'at': pa._utc_now(), 'run_tag': self.run_tag, 'step': step, 'action': action, 'exit_code': exit_code,
                'error_code': error_code, 'elapsed_s': round(elapsed_s, 3), 'reasons': list(reasons),
                'inputs': inputs or {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path), 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return line


# ------------------------------------------------------------------------------------------------------------ context

class PairContext(object):
    def __init__(self, config: pc.ProductsConfig, first: str, second: str, run_tag: str, out, err):
        self.config, self.first, self.second, self.run_tag, self.out, self.err = config, first, second, run_tag, out, err
        self.base = config.base
        self.pair = pc.pair_name(first, second)
        self.pair_dir = self.base / 'data' / 'compare' / self.pair
        self.log = PairLog(self.base, self.pair, run_tag)
        self.hotspots: Dict[str, pa.Generation] = {}
        self.sides: Dict[str, pa.Generation] = {}

    def folder(self, step: str) -> Path:
        return self.pair_dir / STEP_FOLDER[step]

    @property
    def compare2_yaml(self) -> Path:
        return self.base / ('gen.pair.%s.compare2.yaml' % self.pair)

    def inputs(self) -> dict:
        found = {'products_yaml_sha256': self.config.sha256,
                 'side_sha256': {p: g.file_sha256('side.json') for p, g in self.sides.items()},
                 'side_generation': {p: g.name for p, g in self.sides.items()},
                 'hotspots_generation': {p: g.name for p, g in self.hotspots.items()}}
        compare2_json = self.folder('compose') / 'compare2.json'
        if compare2_json.is_file():
            found['compare2_json_sha256'] = pa.sha256_file(compare2_json)
        return found


# ----------------------------------------------------------------------------------------------------------- checks

def check_pair(config: pc.ProductsConfig, first: str, second: str) -> None:
    """Both names registered, different, in one group (the same errors as `products-config --pair`)."""
    pc.pair_yaml(config, first, second, {first: '', second: ''})


def _verdict_text(product: str, kind: str, verdict: pa.Verdict) -> str:
    return '%s %s %s [%s]' % (product, kind, verdict.state, ', '.join(verdict.reasons))


def check_pair_assets(ctx: PairContext) -> None:
    """hotspots and side of both products must be 정상; fills ctx.hotspots and ctx.sides. Read only."""
    config = ctx.config
    results = pc.check_groups(config)
    missing, stale = [], []
    for product in (ctx.first, ctx.second):
        verdicts = pa.check_assets(ctx.base, product, config, results)
        for kind in ('hotspots', 'side'):
            verdict = verdicts[kind]
            if verdict.state == pa.OK:
                continue
            (missing if verdict.state == pa.MISSING else stale).append(_verdict_text(product, kind, verdict))
    if missing:
        raise NumericContractError('asset_missing', '; '.join(missing) + ' (run products-step 01 and 02 for the product)')
    if stale:
        raise NumericContractError('asset_stale', '; '.join(stale) + ' (run products-step 01 and 02 for the product)')
    for product in (ctx.first, ctx.second):
        ctx.hotspots[product] = pa.open_generation(ctx.base, product, 'hotspots')
        ctx.sides[product] = pa.open_generation(ctx.base, product, 'side')


def pair_settings(ctx: PairContext) -> Dict[str, str]:
    """{file name: text} of the two generated pair settings for the current hotspots generations (no write)."""
    data_dirs = {product: ctx.hotspots[product].relative_path for product in (ctx.first, ctx.second)}
    real, compare2 = pc.pair_yaml(ctx.config, ctx.first, ctx.second, data_dirs)
    return {'gen.pair.%s.real.yaml' % ctx.pair: real, 'gen.pair.%s.compare2.yaml' % ctx.pair: compare2}


def current_compose_deps(ctx: PairContext) -> dict:
    """The `deps` block `compare2-compose` would write now (same pieces as compare2_side.run_compose)."""
    cfg2, _cfg, top, tables = compare2_side.prepare_compare2(ctx.compare2_yaml)
    roles = compare2_side.ROLES
    deps = {
        'side_sha256': {'reference': ctx.sides[ctx.first].file_sha256('side.json'),
                        'target': ctx.sides[ctx.second].file_sha256('side.json')},
        'density_sha256': {role: {kind: tables[role][kind].sha256 for kind in ('line', 'pixel')} for role in roles},
        'products': {role: compare2_side._product_section(getattr(cfg2, role)) for role in roles},
        'common': {'model_groups': {name: asdict(group) for name, group in cfg2.model_groups.items()},
                   'lambda_diameters_um': compare2_side._listed(cfg2), 'top_buckets': top},
        'cno': {role: compare2_side._cno_deps(getattr(cfg2, role)) for role in roles},
        'compose_basis': compare2_side.COMPOSE_BASIS}
    return pa._json_normal(deps)


def _pair_config_changed(ctx: PairContext, compare2_path: Path) -> bool:
    """True when the config shas recorded in compare2.json differ from the bytes of the generated pair settings now
    (the ledger refuses such a compare2.json with `compare2_config_mismatch`). Unreadable = changed."""
    try:
        inputs = pa._parse_json(compare2_path.read_bytes()).get('inputs') or {}
        recorded = {'compare2_config': (inputs.get('compare2_config') or {}).get('sha256'),
                    'numeric_config': (inputs.get('numeric_config') or {}).get('sha256')}
        files = {'compare2_config': ctx.compare2_yaml,
                 'numeric_config': ctx.base / ('gen.pair.%s.real.yaml' % ctx.pair)}
        return any(recorded[key] != pa.sha256_file(path) for key, path in files.items())
    except (OSError, ValueError, AttributeError, TypeError):
        return True


def compose_reasons(ctx: PairContext) -> List[str]:
    """Why the compose of this pair is not fresh ([] = fresh). Read only."""
    files = pair_settings(ctx)
    if not pa.settings_on_disk(ctx.config, files):
        return ['pair_settings_changed']
    folder = ctx.folder('compose')
    compose_path, compare2_path = folder / compare2_side.COMPOSE_FILE, folder / 'compare2.json'
    if not compose_path.is_file():
        return ['compose_missing']
    try:
        recorded = pa._parse_json(compose_path.read_bytes())
        deps = recorded['deps']
        if recorded.get('schema_version') != compare2_side.COMPOSE_SCHEMA or not isinstance(deps, dict):
            raise ValueError('not a compose document')
    except (OSError, ValueError, KeyError, TypeError):
        return ['compose_unreadable']
    reasons: List[str] = []
    try:
        if pa.sha256_file(compare2_path) != recorded.get('compare2_json_sha256'):
            reasons.append('compare2_json_changed')
    except OSError:
        reasons.append('compare2_json_changed')
    if _pair_config_changed(ctx, compare2_path):
        reasons.append('pair_config_changed')
    now = current_compose_deps(ctx)
    for key in sorted(set(now) | set(deps)):
        if key == 'common':
            then, current = deps.get(key) or {}, now.get(key) or {}
            for sub in sorted(set(then) | set(current)):
                if pa._json_normal(then.get(sub)) != pa._json_normal(current.get(sub)):
                    reasons.append(_COMMON_REASON.get(sub, 'common_changed'))
        elif pa._json_normal(deps.get(key)) != pa._json_normal(now.get(key)):
            reasons.append(_DEPS_REASON.get(key, 'compose_basis_changed'))
    return pa._unique(reasons)


# ------------------------------------------------------------------------------------------------------- archiving

def archive_folder(ctx: PairContext, step: str) -> Optional[Path]:
    """Move the existing output folder of `step` to data/_runs/<RUN_TAG>/compare/<pair>/ (a free name; nothing is deleted)."""
    source = ctx.folder(step)
    if not source.exists():
        return None
    destination = ctx.base / 'data' / '_runs' / ctx.run_tag / 'compare' / ctx.pair
    destination.mkdir(parents=True, exist_ok=True)
    target, counter = destination / source.name, 1
    while target.exists():
        counter += 1
        target = destination / ('%s-%d' % (source.name, counter))
    pa._retry_permission(lambda: os.rename(str(source), str(target)))      # same file system: no copy fallback
    ctx.out('archived %s to data/_runs/%s/compare/%s/%s' % (source.relative_to(ctx.base).as_posix(), ctx.run_tag,
                                                          ctx.pair, target.name))
    return target


# ---------------------------------------------------------------------------------------------------------- the steps

def _inner(ctx: PairContext, step: str, cards: bool) -> int:
    err = pa._Stream(ctx.err)
    nothing = lambda record: None
    compare2_dir, config = str(ctx.folder('compose')), str(ctx.compare2_yaml)
    if step == 'compose':
        return compare2_side.main_compose(config, str(ctx.sides[ctx.first].path), str(ctx.sides[ctx.second].path),
                                          compare2_dir, nothing, err)
    from . import ledger, ledger_maps, ledger_report
    if step == 'ledger':
        return ledger.main_ledger(compare2_dir, config, str(ctx.folder('ledger')), nothing, error_stream=err)
    ledger_dir, maps_dir = ctx.folder('ledger'), ctx.folder('maps')
    if step == 'maps':
        return ledger_maps.main_ledger_maps(compare2_dir, config, str(ledger_dir), str(maps_dir), nothing,
                                            error_stream=err, cards=cards)
    return ledger_report.main_ledger_report(str(ledger_dir), str(ctx.folder('report')), nothing, maps_dir=str(maps_dir),
                                            error_stream=err)


def check_step_order(ctx: PairContext, step: str) -> None:
    """The earlier output a later step reads must exist. Runs before anything is archived."""
    if step == 'maps' and not (ctx.folder('ledger') / 'ledger.json').is_file():
        raise NumericContractError('pair_step_order', 'run pair-step --step ledger first: %s/ledger.json is missing'
                                   % ctx.folder('ledger'))
    if step == 'report':
        for name, first in (('ledger', 'ledger'), ('maps', 'maps')):
            document = 'ledger.json' if name == 'ledger' else 'ledger_maps.json'
            if not (ctx.folder(name) / document).is_file():
                raise NumericContractError('pair_step_order', 'run pair-step --step %s first: %s/%s is missing'
                                           % (first, ctx.folder(name), document))


def _compare2_status(ctx: PairContext) -> str:
    try:
        return str(pa._parse_json((ctx.folder('compose') / 'execution.json').read_bytes()).get('status'))
    except (OSError, ValueError, AttributeError):
        return 'unreadable'


def run_compose_step(ctx: PairContext, started: float, force: bool = False) -> int:
    pc.write_generated(ctx.config, pair_settings(ctx))
    reasons = compose_reasons(ctx)
    if not reasons and not force:
        status = _compare2_status(ctx)
        note = '' if status == 'available' else ' (compare2 status: %s)' % status
        ctx.out('skip fresh compose %s%s' % (ctx.pair, note))
        inputs = ctx.inputs()
        if note:
            inputs['compare2_status'] = status
        ctx.log.append('compose', 'skip', 0, None, time.time() - started, inputs)
        return 0
    return _build(ctx, 'compose', False, reasons or ['force'], started)


def run_later_step(ctx: PairContext, step: str, cards: bool, started: float) -> int:
    reasons = compose_reasons(ctx)
    if reasons:
        raise NumericContractError('pair_compose_stale', '%s: the compose is not fresh (%s); %s'
                                   % (ctx.pair, ', '.join(reasons), STALE_HINT))
    return _build(ctx, step, cards, ['rebuild'], started)


def _build(ctx: PairContext, step: str, cards: bool, reasons: Sequence[str], started: float) -> int:
    check_step_order(ctx, step)
    archive_folder(ctx, step)
    code = _inner(ctx, step, cards)
    inputs = ctx.inputs()
    if code in CONTINUE_CODES:
        suffix = ' (cards off)' if step == 'maps' and not cards else ''
        ctx.out('built %s %s%s exit %d' % (step, ctx.pair, suffix, code))
        ctx.log.append(step, 'build', code, None, time.time() - started, inputs, reasons)
    else:
        ctx.err('오류: %s of %s ended with exit %d' % (step, ctx.pair, code))
        ctx.log.append(step, 'fail', code, 'inner_exit_%d' % code, time.time() - started, inputs, reasons)
    return code


def run_pair_step(config_path, first: str, second: str, step: str, run_tag: Optional[str] = None, cards: bool = False,
                  out: Callable[[str], None] = print, err: Optional[Callable[[str], None]] = None,
                  force: bool = False) -> int:
    """pair-step: 0 = built or skipped fresh, 3 = built with a partial result, 2 = refused or error."""
    err = err or (lambda text: print(text, file=sys.stderr))
    if step not in STEPS:
        err('오류: invalid_step: step must be one of %s' % ', '.join(STEPS))
        return 2
    ctx = None
    started = time.time()
    try:
        run_tag = pa.check_run_tag(run_tag or pa.utc_run_tag())
        config = pc.load_products_config(config_path)
        check_pair(config, first, second)
        ctx = PairContext(config, first, second, run_tag, out, err)
        for product in (first, second):
            pa.check_safe(config.base, product)
        pc._reject_symlink_components(config.base, 'data/compare/%s' % ctx.pair)
        for name in (PAIR_LOG,) + tuple(STEP_FOLDER[s] for s in STEPS):
            pc._reject_symlink_components(config.base, 'data/compare/%s/%s' % (ctx.pair, name))
        check_pair_assets(ctx)
        if step == 'compose':
            return run_compose_step(ctx, started, force)
        return run_later_step(ctx, step, cards, started)
    except pc.GroupCompatibilityError as exc:
        err('오류: %s' % exc)
        _log_failure(ctx, step, started, 'group_incompatible', exc.codes)
        return 2
    except NumericContractError as exc:
        err('오류: %s' % exc)
        _log_failure(ctx, step, started, exc.code, [])
        return 2
    except Exception as exc:                                   # an interrupted job: the old output is in data/_runs
        err('오류: pair_internal_error: %s: %s' % (type(exc).__name__, exc))
        err(traceback.format_exc().rstrip())
        _log_failure(ctx, step, started, 'pair_internal_error', [type(exc).__name__])
        return 2


def _log_failure(ctx: Optional[PairContext], step: str, started: float, code: str, reasons: Sequence[str]) -> None:
    if ctx is None:
        return
    try:
        pc._reject_symlink_components(ctx.base, 'data/compare/%s' % ctx.pair)     # never log through a symlinked folder
        pc._reject_symlink_components(ctx.base, 'data/compare/%s/%s' % (ctx.pair, PAIR_LOG))
        ctx.log.append(step, 'fail', 2, code, time.time() - started, ctx.inputs() if ctx.sides else {}, reasons)
    except (OSError, NumericContractError):
        pass
