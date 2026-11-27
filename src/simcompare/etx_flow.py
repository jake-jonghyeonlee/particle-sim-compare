"""ETX 통합 실행기(bundles/etx/simcompare_job_etx.py)의 계획 로직.

실행기 EDIT 블록의 값(STEPS 등)과 설정 파일 두 개(real.yaml, compare2.yaml)에서 JOBS 를 만들고,
선택한 단계의 이전 출력 폴더를 data/_runs/<RUN_TAG>/ 로 옮긴다(삭제·복사 없음, 같은 파일 시스템 안 os.rename).
계산 코드는 건드리지 않는다: 이 모듈은 `python -m simcompare <명령> <인자...>` 의 인자 목록만 만든다.
Python 3.8 문법, 표준 라이브러리와 번들 PyYAML 만 쓴다.
제품 모드는 함수 안에서 패키지 모듈(numpy 포함)을 늦게 import 한다.
"""
import os
import sys
import time
from pathlib import Path

STEP_ORDER = ('01', '03', '02', 'diag', '04', '05', '06', '07', '08')     # 실행 순서(STEPS 의 순서가 아니다)
DEFAULT_STEPS = ('01', '03', '02', 'diag', '04', '06', '07', '08')       # 05(DL2 확정)는 단계로 남기되 기본에서 뺀다
STEP_LABELS = {'01': 'hotspot import', '03': '전극 PNG import', '02': 'compare2', 'diag': 'diag 검사·비교', '04': '변경 위치(ROI)',
               '05': 'DL2 확정', '06': '창별 ΔCA', '07': 'compare2 보고서', '08': '장부·지도·보고서',
               # 제품 모드(MODE = "products"): P = 제품 자산, C = 쌍 비교
               'P01': 'hotspot 자산', 'P02': 'side 자산', 'C02': 'compare2 조합', 'C08': '장부·지도·보고서'}      # 로그의 단계 이름표
PRODUCT_ASSET_STEPS = ('01', '02')
PRODUCT_COMPARE_STEPS = ('02', '08')
MODES = ('pair', 'products')


class Job(tuple):
    """(명령, 인자 목록) 2-tuple 과 똑같이 비교·풀어쓰기 되고, 어느 단계의 job 인지(step, label)만 더 가진다."""

    def __new__(cls, name, args, step=None):
        job = tuple.__new__(cls, (name, args))
        job.step = step
        job.label = STEP_LABELS.get(step, step)
        return job

    def __getnewargs__(self):       # copy·pickle 이 __new__(cls, name, args, step) 로 다시 만들 수 있게 한다
        return (self[0], self[1], self.step)


class FlowJobs(list):
    """JOBS 목록(list 와 같이 비교된다)과, 본 job 이 성공하든 실패로 멈추든 run_jobs 가 마지막에 돌리는 마무리 job(finalizers).
    마무리 job 의 실패는 경고만 남기고 원래 종료 코드를 바꾸지 않는다."""

    def __init__(self, jobs=(), finalizers=()):
        super().__init__(jobs)
        self.finalizers = list(finalizers)


RUNS_DIR = 'data/_runs'
SOURCE_VERSION = 'LAYOUT_V1'
ASSET_MANIFEST_NAME = 'review_assets.json'
ASSET_RECORD_NAME = 'electrode_asset_import_record.json'
MANAGED_04_FLAGS = ('--config', '--cnt-from-dl1-root', '--electrode-png-root', '--window-um', '--diameter-um', '--out')
LOG_PREFIX = '[simcompare_job]'

DEFAULTS = {
    'steps': DEFAULT_STEPS,
    'run_tag': None,
    'real_config': 'real.yaml',
    'compare2_config': 'compare2.yaml',
    'include_cno': True,
    'max_asset_memory_mb': 16384,
    'align_args': (),
    'extra_window_sizes_um': (),
    'jitter_um': 0.5,
    'dl2_match_args': (),
    'dl2_decision_args': (),
    'changes_dir': 'data/changes',
    'confirmed_dir': 'data/confirmed',
    'local_dir': 'data/local',
    'report_dir': 'data/compare2_report',
    'ledger_dir': 'data/ledger',
    'ledger_report_dir': 'data/ledger_report',
    'ledger_maps': True,
    'ledger_maps_dir': 'data/ledger_maps',
    'ledger_maps_args': (),
    'ledger_maps_cards': None,      # None = 모드 기본(pair 켬, products 끔). False = 08 의 ledger-maps / pair-step maps 에 --no-cards
    # ---- mode == 'products' 일 때만 읽는다(pair 모드는 읽지 않는다) ----
    'mode': 'pair',
    'products_config': 'products.yaml',
    'products': None,
    'asset_steps': PRODUCT_ASSET_STEPS,
    'compare': (),
    'compare_steps': PRODUCT_COMPARE_STEPS,
    'force_assets': (),
    'record_maps': True,                       # 02: 지도 원자료 녹화(maps_src_v2). False = products-step 02 --no-record-maps
    'maps_src_max_bytes': 32 * 1024 ** 3,      # 02: 녹화 비압축 합 상한. 기본값이면 인자를 붙이지 않는다
}
DEFAULT_MAPS_SRC_MAX_BYTES = 32 * 1024 ** 3


class FlowError(ValueError):
    """실행 전에 알 수 있는 입력 오류. 실행기는 `오류: <code>: <메시지>` 를 찍고 종료 코드 2 로 멈춘다."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Context:
    def __init__(self, base, settings, real_path, real, compare2_path, compare2):
        self.base = base
        self.settings = settings
        self.real_path = real_path
        self.real_dir = real_path.parent
        self.real = real
        self.compare2_path = compare2_path
        self.compare2 = compare2


def _echo(message):
    print('%s %s' % (LOG_PREFIX, message), file=sys.stderr, flush=True)


def _merged(settings):
    unknown = sorted(set(settings) - set(DEFAULTS))
    if unknown:
        raise FlowError('etx_unknown_setting', 'unknown settings: %s' % ', '.join(unknown))
    merged = dict(DEFAULTS)
    merged.update(settings)
    return merged


def _steps(settings):
    steps = settings['steps']
    if isinstance(steps, str) or not steps:
        raise FlowError('etx_steps_invalid', 'STEPS must be a non-empty list of step names')
    unknown = [step for step in steps if step not in STEP_ORDER]
    if unknown:
        raise FlowError('etx_unknown_step', 'unknown step %s; allowed: %s' % (
            ', '.join(repr(step) for step in unknown), ', '.join(STEP_ORDER)))
    return [step for step in STEP_ORDER if step in steps]       # 고정 순서, 중복 제거


def _mode(settings):
    mode = settings['mode']
    if mode not in MODES:
        raise FlowError('etx_mode_invalid', 'MODE must be one of %s, got %r' % (', '.join(repr(m) for m in MODES), mode))
    return mode


def _cards(settings):
    """LEDGER_MAPS_CARDS: None(모드 기본), True, False."""
    value = settings['ledger_maps_cards']
    if value is not None and type(value) is not bool:
        raise FlowError('etx_setting_invalid', 'LEDGER_MAPS_CARDS must be True, False or None, got %r' % (value,))
    return value


def _record_args(settings):
    """RECORD_MAPS / MAPS_SRC_MAX_BYTES -> extra args of `products-step 02` (none for the defaults)."""
    record, limit = settings['record_maps'], settings['maps_src_max_bytes']
    if type(record) is not bool:
        raise FlowError('etx_setting_invalid', 'RECORD_MAPS must be True or False, got %r' % (record,))
    if type(limit) is not int or limit <= 0:
        raise FlowError('etx_setting_invalid', 'MAPS_SRC_MAX_BYTES must be a positive integer, got %r' % (limit,))
    args = [] if record else ['--no-record-maps']
    if record and limit != DEFAULT_MAPS_SRC_MAX_BYTES:
        args += ['--maps-src-max-bytes', str(limit)]
    return args


def _text_list(value, name):
    if isinstance(value, str) or not all(isinstance(item, str) for item in value):
        raise FlowError('etx_setting_invalid', '%s must be a list of strings' % name)
    return list(value)


def _number(value):
    """10.0 -> '10', 0.5 -> '0.5' (CLI 인자 문자열)."""
    number = float(value)
    return str(int(number)) if number == int(number) and abs(number) < 1e15 else repr(number)


def _under(base, value):
    """BASE_DIR 기준 경로(절대 경로도 허용). 심볼릭 링크는 풀지 않는다."""
    path = Path(value)
    return Path(os.path.normpath(str(path if path.is_absolute() else base / path)))


def _arg(base, path):
    """JOBS 인자용 경로: BASE_DIR 안이면 상대 경로(실행기가 BASE_DIR 로 chdir 한다), 밖이면 절대 경로."""
    try:
        return Path(path).relative_to(base).as_posix()
    except ValueError:
        return str(path)


def _load_real(path):
    import yaml
    try:
        data = yaml.safe_load(path.read_bytes().decode('utf-8'))
    except OSError as exc:
        raise FlowError('etx_config_unreadable', '%s: %s' % (path, exc))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise FlowError('etx_config_invalid', '%s: %s' % (path, exc))
    if not isinstance(data, dict) or data.get('schema_version') != 'numeric_config_v1':
        raise FlowError('etx_config_invalid', '%s: schema_version must be numeric_config_v1' % path)
    for side in ('reference', 'target'):
        section = data.get(side)
        if (not isinstance(section, dict) or not isinstance(section.get('product_id'), str)
                or not isinstance(section.get('data_dir'), str)):
            raise FlowError('etx_config_invalid', '%s: %s needs product_id and data_dir' % (path, side))
    return data


def load_context(base_dir, settings):
    from simcompare.compare2_inputs import load_compare2_config
    from simcompare.numeric_schema import NumericContractError
    base = Path(base_dir).resolve()
    settings = _merged(settings)
    real_path = _under(base, settings['real_config']).resolve()
    compare2_path = _under(base, settings['compare2_config']).resolve()
    real = _load_real(real_path)
    try:
        compare2 = load_compare2_config(compare2_path)
    except OSError as exc:
        raise FlowError('etx_config_unreadable', '%s: %s' % (compare2_path, exc))
    except NumericContractError as exc:
        raise FlowError(exc.code, '%s: %s' % (compare2_path, exc))
    if compare2.numeric_config.resolve() != real_path:
        raise FlowError('etx_numeric_config_mismatch', '%s numeric_config (%s) is not the real config of this run (%s)' % (
            compare2_path.name, compare2.numeric_config, real_path))
    return Context(base, settings, real_path, real, compare2_path, compare2)


def _real_path(ctx, value):
    path = Path(value)
    return Path(os.path.normpath(str(path if path.is_absolute() else ctx.real_dir / path)))


def _asset_folder(ctx, side):
    """03 의 --out: real.yaml 의 asset_manifest.path 와 electrode_import_record.path 가 가리키는 같은 폴더."""
    section = ctx.real[side]
    found = {}
    for key, name in (('asset_manifest', ASSET_MANIFEST_NAME), ('electrode_import_record', ASSET_RECORD_NAME)):
        entry = section.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise FlowError('etx_asset_path_missing', '%s: %s.%s.path is required for step 03 (the 03 output folder)' % (
                ctx.real_path.name, side, key))
        path = _real_path(ctx, entry['path'])
        if path.name != name:
            raise FlowError('etx_asset_folder_mismatch', '%s.%s.path must end with %s (step 03 writes that file name): %s' % (
                side, key, name, entry['path']))
        found[key] = path.parent
    if found['asset_manifest'] != found['electrode_import_record']:
        raise FlowError('etx_asset_folder_mismatch', '%s: %s asset_manifest.path and electrode_import_record.path are in '
                        'different folders (%s, %s); step 03 writes both into one --out folder' % (
                            ctx.real_path.name, side, found['asset_manifest'], found['electrode_import_record']))
    return found['asset_manifest']


def _dl1_root(ctx):
    root = ctx.compare2.report.dl1_root
    if root is None:
        raise FlowError('etx_dl1_root_required', '%s: report.dl1_root is required for steps 03 and 04' % ctx.compare2_path.name)
    return root


def _roles():
    return (('reference', 'target'), ('target', 'reference'))


def plan_jobs(base_dir, settings):
    """[(명령, [인자...])]: 선택한 단계를 고정 순서로. 설정 파일을 읽을 뿐 아무것도 쓰지 않는다."""
    if _mode(_merged(settings)) == 'products':
        return plan_product_jobs(base_dir, settings)
    ctx = load_context(base_dir, settings)
    cfg, base, c2 = ctx.settings, ctx.base, ctx.compare2
    steps = _steps(cfg)
    real_arg, compare2_arg = cfg['real_config'], cfg['compare2_config']
    changes, confirmed = _under(base, cfg['changes_dir']), _under(base, cfg['confirmed_dir'])
    local, report = _under(base, cfg['local_dir']), _under(base, cfg['report_dir'])
    rel = lambda path: _arg(base, path)
    products = {'reference': c2.reference, 'target': c2.target}
    diameter, window = _number(c2.report.diameter_um), _number(c2.report.map_window_um)
    jobs = []
    for step in steps:
        first = len(jobs)
        if step == '01':
            for side, other in _roles():
                args = ['--root', rel(products[side].hotspot_root), '--expected-cases-from-roots', rel(products[other].hotspot_root)]
                if cfg['include_cno']:
                    args.append('--include-cno')
                jobs.append(('import-hotspots', args + ['--out', rel(_real_path(ctx, ctx.real[side]['data_dir']))]))
        elif step == '03':
            dl1 = _dl1_root(ctx)
            for side, _ in _roles():
                jobs.append(('import-electrode-assets', [
                    '--source-kind', 'hotspot_case_electrode', '--root', rel(products[side].hotspot_root),
                    '--config', real_arg, '--side', side, '--cnt-from-dl1-root', rel(dl1),
                    '--source-version', SOURCE_VERSION, '--max-asset-memory-mb', _number(cfg['max_asset_memory_mb']),
                    '--out', rel(_asset_folder(ctx, side))]))
        elif step == '02':
            jobs.append(('compare2', ['--config', compare2_arg, '--out', rel(c2.output_dir)]))
        elif step == 'diag':
            jobs.append(('validate-data', ['--config', real_arg]))
            jobs.append(('compare', ['--config', real_arg]))
        elif step == '04':
            dl1 = rel(_dl1_root(ctx))
            align = _text_list(cfg['align_args'], 'ALIGN_ARGS')
            clash = [flag for flag in align if flag in MANAGED_04_FLAGS]
            if clash:
                raise FlowError('etx_align_args_managed', 'ALIGN_ARGS must not contain %s: the flow sets them from the config files' %
                                ', '.join(clash))
            jobs.append(('candidates-change', ['--config', real_arg, '--cnt-from-dl1-root', dl1] + align + [
                '--electrode-png-root', dl1, '--window-um', window, '--diameter-um', diameter, '--out', rel(changes)]))
        elif step == '05':
            match = _text_list(cfg['dl2_match_args'], 'DL2_MATCH_ARGS')
            decide = _text_list(cfg['dl2_decision_args'], 'DL2_DECISION_ARGS')
            if not match or not decide:
                raise FlowError('etx_dl2_args_required', 'step 05 needs DL2_MATCH_ARGS and DL2_DECISION_ARGS')
            jobs.append(('confirm-pairs', ['--list', '--config', real_arg, '--candidates', rel(changes)] + match))
            jobs.append(('confirm-pairs', ['--config', real_arg, '--candidates', rel(changes)] + match + decide + [
                '--out', rel(confirmed)]))
        elif step == '06':
            correspondence = (confirmed / 'roi_correspondence.json') if '05' in steps else (changes / 'direct/roi_correspondence.json')
            sizes, seen = [], set()
            for value in [c2.report.map_window_um] + [float(v) for v in cfg['extra_window_sizes_um']]:
                if float(value) not in seen:
                    seen.add(float(value))
                    sizes.append(_number(value))
            jobs.append(('compare-windows', ['--config', real_arg, '--correspondence', rel(correspondence), '--window-sizes'] + sizes + [
                '--jitter-um', _number(cfg['jitter_um']), '--sort-diameter-um', diameter, '--out', rel(local)]))
        elif step == '07':
            args = ['--compare2-dir', rel(c2.output_dir), '--config', compare2_arg]
            if '04' in steps or changes.is_dir():
                args += ['--candidates', rel(changes)]
            if '06' in steps or local.is_dir():
                args += ['--windows', rel(local)]
            if '05' in steps:
                args += ['--confirm', rel(confirmed)]
            jobs.append(('compare2-report', args + ['--out', rel(report)]))
        elif step == '08':
            if '02' not in steps and not (c2.output_dir / 'compare2.json').is_file():
                raise FlowError('etx_compare2_output_missing', 'step 08 needs the 02 output: %s is missing; add 02 to STEPS '
                                '(STEPS = ["02", "08"]) or run 02 first' % (c2.output_dir / 'compare2.json'))
            ledger, ledger_report = _under(base, cfg['ledger_dir']), _under(base, cfg['ledger_report_dir'])
            jobs.append(('ledger', ['--compare2-dir', rel(c2.output_dir), '--config', compare2_arg, '--out', rel(ledger)]))
            if cfg['ledger_maps']:
                maps = _under(base, cfg['ledger_maps_dir'])
                maps_extra = _text_list(cfg['ledger_maps_args'], 'LEDGER_MAPS_ARGS')
                maps_args = ['--compare2-dir', rel(c2.output_dir), '--config', compare2_arg, '--ledger-dir', rel(ledger)]
                if '04' in steps or changes.is_dir():
                    maps_args += ['--candidates', rel(changes)]
                maps_args += ['--out', rel(maps)]
                if _cards(cfg) is False:
                    maps_args.append('--no-cards')
                jobs.append(('ledger-maps', maps_args + maps_extra))
                jobs.append(('ledger-report', ['--ledger-dir', rel(ledger), '--maps-dir', rel(maps), '--report07-dir', rel(report),
                                               '--out', rel(ledger_report)]))
            else:
                jobs.append(('ledger-report', ['--ledger-dir', rel(ledger), '--report07-dir', rel(report), '--out', rel(ledger_report)]))
        jobs[first:] = [Job(name, args, step) for name, args in jobs[first:]]       # 단계 표시(비교는 2-tuple 과 같다)
    return jobs


# ---- 제품 모드(MODE = "products"): products.yaml 의 제품 자산 + 쌍 비교 ----------------------------------------------------

class ProductPlan(object):
    """제품 모드의 검증된 계획. 아무것도 쓰지 않고 만든다."""

    def __init__(self, config, products, pairs, asset_steps, compare_steps, force, tag, jobs, record_maps=True):
        self.record_maps = record_maps
        self.config, self.products, self.pairs = config, products, pairs
        self.asset_steps, self.compare_steps, self.force, self.tag, self.jobs = asset_steps, compare_steps, force, tag, jobs


def _load_products(base, cfg):
    from simcompare.numeric_schema import NumericContractError
    from simcompare.products_config import load_products_config
    path = _under(base, cfg['products_config']).resolve()
    try:
        return load_products_config(path)
    except OSError as exc:
        raise FlowError('etx_config_unreadable', '%s: %s' % (path, exc))
    except NumericContractError as exc:
        raise FlowError(exc.code, '%s: %s' % (path, exc))


def _names(value, name):
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise FlowError('etx_setting_invalid', '%s must be a list of product names' % name)
    if len(set(value)) != len(value):
        raise FlowError('etx_setting_invalid', '%s lists a product twice: %s' % (name, ', '.join(value)))
    return list(value)


def _product_steps(value, allowed, name, code):
    if not isinstance(value, (list, tuple)) or any(step not in allowed for step in value) or len(set(value)) != len(value):
        raise FlowError(code, '%s must be a list of %s (no repeats), got %r' % (
            name, ' / '.join(repr(step) for step in allowed), value))
    return [step for step in allowed if step in value]        # 고정 순서


def _product_run_tag(value):
    from simcompare.numeric_schema import NumericContractError
    from simcompare.products_assets import check_run_tag, utc_run_tag
    try:
        return check_run_tag(utc_run_tag() if value is None else value)
    except NumericContractError as exc:
        raise FlowError('etx_run_tag_invalid', 'RUN_TAG: %s' % exc)


def _product_plan(base_dir, settings):
    cfg = _merged(settings)
    if _mode(cfg) != 'products':
        raise FlowError('etx_mode_invalid', 'product jobs need MODE = "products"')
    base = Path(base_dir).resolve()
    config = _load_products(base, cfg)
    registered = list(config.products)
    selected = registered if cfg['products'] is None else _names(cfg['products'], 'PRODUCTS')
    for name in selected:
        if name not in config.products:
            raise FlowError('etx_product_unknown', 'PRODUCTS: %r is not registered in %s (registered: %s)' % (
                name, config.path.name, ', '.join(registered)))
    asset_steps = _product_steps(cfg['asset_steps'], PRODUCT_ASSET_STEPS, 'ASSET_STEPS', 'etx_asset_steps_invalid')
    compare_steps = _product_steps(cfg['compare_steps'], PRODUCT_COMPARE_STEPS, 'COMPARE_STEPS', 'etx_compare_steps_invalid')
    if not isinstance(cfg['compare'], (list, tuple)):
        raise FlowError('etx_setting_invalid', 'COMPARE must be a list of (reference, target) pairs')
    pairs = []
    for item in cfg['compare']:
        if (not isinstance(item, (list, tuple)) or len(item) != 2 or not all(isinstance(name, str) for name in item)):
            raise FlowError('etx_setting_invalid', 'COMPARE entries must be (reference, target) pairs of product names, got %r' % (item,))
        first, second = item
        for name in (first, second):
            if name not in config.products:
                raise FlowError('etx_product_unknown', 'COMPARE: %r is not registered in %s (registered: %s)' % (
                    name, config.path.name, ', '.join(registered)))
        if first == second:
            raise FlowError('etx_compare_same_product', 'COMPARE: %r cannot be compared with itself' % first)
        if config.group_of(first) != config.group_of(second):
            raise FlowError('etx_compare_cross_group', 'COMPARE: %s is in group %s but %s is in group %s; only products of '
                            'one group can be compared' % (first, config.group_of(first), second, config.group_of(second)))
        if (first, second) in pairs:
            raise FlowError('etx_setting_invalid', 'COMPARE lists the pair (%s, %s) twice' % (first, second))
        pairs.append((first, second))
    force = _names(cfg['force_assets'], 'FORCE_ASSETS')
    for name in force:
        if name not in config.products:
            raise FlowError('etx_product_unknown', 'FORCE_ASSETS: %r is not registered in %s' % (name, config.path.name))
        if name not in selected or not asset_steps:
            raise FlowError('etx_force_not_selected', 'FORCE_ASSETS: %r is not built by this run (it must be in PRODUCTS and '
                            'ASSET_STEPS must not be empty)' % name)
    cards = _cards(cfg) is True            # 제품 모드 기본 끔
    record_args = _record_args(cfg)
    tag = _product_run_tag(cfg['run_tag'])
    config_arg = cfg['products_config']
    jobs = []
    for step in asset_steps:
        for name in selected:
            args = ['--config', config_arg, '--product', name, '--step', step]
            if name in force:
                args.append('--force')
            if step == '02':
                args += record_args
            jobs.append(Job('products-step', args + ['--run-tag', tag], 'P' + step))
    pair_args = lambda first, second, step: ['--config', config_arg, '--pair', first, second, '--step', step]
    if '02' in compare_steps:
        for first, second in pairs:
            jobs.append(Job('pair-step', pair_args(first, second, 'compose') + ['--run-tag', tag], 'C02'))
    if '08' in compare_steps:
        for first, second in pairs:
            for step in ('ledger', 'maps', 'report'):
                extra = ['--cards' if cards else '--no-cards'] if step == 'maps' else []
                jobs.append(Job('pair-step', pair_args(first, second, step) + ['--run-tag', tag] + extra, 'C08'))
    if not jobs:
        raise FlowError('etx_no_jobs', 'nothing to do: ASSET_STEPS is empty and COMPARE has no pair for COMPARE_STEPS %s' % (
            compare_steps,))
    return ProductPlan(config, selected, pairs, asset_steps, compare_steps, force, tag, jobs,
                       record_maps=cfg['record_maps'] is True)


def plan_product_jobs(base_dir, settings):
    """제품 모드 JOBS: 제품마다 products-step 01 → 제품마다 02 → 쌍마다 compose → 쌍마다 ledger·maps·report.
    products.yaml 을 읽을 뿐 아무것도 쓰지 않는다. 입력 오류는 FlowError."""
    return _product_plan(base_dir, settings).jobs


def product_finalizers(config_arg, record_maps=True):
    """제품 모드 마무리 job(본 job 뒤에 항상 돈다): `products-sheet --config <products.yaml>` 로 자산 관리 시트를 다시 만든다.
    RECORD_MAPS 가 False 면 `--no-record-maps` 를 넘겨 녹화 없는 side 를 낡음으로 보지 않는다.
    실패해도 경고만 남기고 원래 종료 코드는 그대로다(run_jobs)."""
    return [('products-sheet', ['--config', str(config_arg)] + ([] if record_maps else ['--no-record-maps']))]


def _product_status_lines(plan):
    """DRY_RUN 용: 제품·쌍의 현재 자산 상태(읽기 전용). 판정 실패는 줄에 그대로 적는다."""
    from simcompare import pair_assets, products_assets
    lines = []
    wanted = list(plan.products)
    for first, second in plan.pairs:
        for name in (first, second):
            if name not in wanted:
                wanted.append(name)
    for name in wanted:
        try:
            state, reasons = products_assets.asset_status(plan.config.base, name, plan.config, record_maps=plan.record_maps)
            lines.append('  asset %s: %s%s' % (name, state, ' [%s]' % ', '.join(reasons) if reasons else ''))
        except Exception as exc:        # 읽기 전용 판정이 실패해도 계획 출력은 막지 않는다
            lines.append('  asset %s: 판정 실패 %s: %s' % (name, type(exc).__name__, exc))
    for first, second in plan.pairs:
        label = 'pair %s__%s compose' % (first, second)
        try:
            ctx = pair_assets.PairContext(plan.config, first, second, plan.tag, print, print)
            pair_assets.check_pair_assets(ctx)
            reasons = pair_assets.compose_reasons(ctx)
            lines.append('  %s: %s' % (label, '정상(건너뜀)' if not reasons else '다시 만듦 [%s]' % ', '.join(reasons)))
        except Exception as exc:
            lines.append('  %s: 판정 불가 %s' % (label, exc))
    return lines


def _prepare_products(base_dir, settings, dry_run, echo):
    plan = _product_plan(base_dir, settings)
    cfg_name = plan.config.path.name
    echo('flow: mode products; %s; products %s; asset steps %s; compare %s; compare steps %s; run tag %s' % (
        plan.config.path, ', '.join(plan.products) or '-', ', '.join(plan.asset_steps) or '-',
        ', '.join('%s->%s' % pair for pair in plan.pairs) or '-', ', '.join(plan.compare_steps) or '-', plan.tag))
    finalizers = product_finalizers(_merged(settings)['products_config'], plan.record_maps)
    echo('flow: %d job(s):' % len(plan.jobs))
    for job in plan.jobs:
        echo('  job [%s] %s %s' % (job.step, job[0], ' '.join(job[1])))
    for name, args in finalizers:
        echo('  finalizer %s %s' % (name, ' '.join(args)))
    if dry_run:                     # 자산 판정은 읽기 전용이지만 시간이 든다: DRY_RUN 일 때만 계산·출력
        echo('flow: asset state of %s (read only):' % cfg_name)
        for line in _product_status_lines(plan):
            echo(line)
    echo('flow: no output folder is moved by the runner: products-step and pair-step keep and archive their own generations')
    if dry_run:
        echo('flow: DRY_RUN = True: nothing was run or moved')
        return []
    return FlowJobs(plan.jobs, finalizers)


def output_folders(ctx, steps):
    """단계가 만드는 출력 폴더(고정 경로): [(단계, 절대 경로)]. 01 = real.yaml 양쪽 data_dir, 03 = 양쪽 asset 폴더,
    02 = compare2.yaml output_dir, diag = real.yaml output_dir, 04–07 = 실행기 상수."""
    cfg, base = ctx.settings, ctx.base
    found = []
    for step in steps:
        if step == '01':
            found += [(step, _real_path(ctx, ctx.real[side]['data_dir'])) for side in ('reference', 'target')]
        elif step == '03':
            found += [(step, _asset_folder(ctx, side)) for side in ('reference', 'target')]
        elif step == '02':
            found.append((step, ctx.compare2.output_dir))
        elif step == 'diag':
            output_dir = ctx.real.get('output_dir')
            if not isinstance(output_dir, str):
                raise FlowError('etx_config_invalid', '%s: output_dir is required for step diag' % ctx.real_path.name)
            found.append((step, _real_path(ctx, output_dir)))
        elif step == '04':
            found.append((step, _under(base, cfg['changes_dir'])))
        elif step == '05':
            found.append((step, _under(base, cfg['confirmed_dir'])))
        elif step == '06':
            found.append((step, _under(base, cfg['local_dir'])))
        elif step == '07':
            found.append((step, _under(base, cfg['report_dir'])))
        elif step == '08':
            found.append((step, _under(base, cfg['ledger_dir'])))
            if cfg['ledger_maps']:
                found.append((step, _under(base, cfg['ledger_maps_dir'])))
            found.append((step, _under(base, cfg['ledger_report_dir'])))
    return found


def _run_tag(value):
    tag = time.strftime('%Y%m%d-%H%M%S', time.gmtime()) if value is None else value
    if not isinstance(tag, str) or not tag or tag in ('.', '..') or any(c in tag for c in '/\\') or tag != tag.strip():
        raise FlowError('etx_run_tag_invalid', 'RUN_TAG must be a plain folder name (no / or \\), got %r' % (value,))
    return tag


def plan_archive(base_dir, settings, run_tag=None, ctx=None):
    """[(원래 폴더, 보관 위치)]: 선택한 단계의 출력 폴더 중 이미 있는 것만. 아무것도 옮기지 않는다."""
    ctx = ctx or load_context(base_dir, settings)
    base = ctx.base
    tag = _run_tag(run_tag if run_tag is not None else ctx.settings['run_tag'])
    runs_root = base / RUNS_DIR / tag
    sources = []
    for step, path in output_folders(ctx, _steps(ctx.settings)):
        if path not in sources:
            sources.append(path)
    moves = []
    for src in sources:
        try:
            relative = src.relative_to(base)
        except ValueError:
            raise FlowError('etx_output_outside_base', 'output folder %s is outside BASE_DIR %s; move it by hand' % (src, base))
        if not os.path.lexists(str(src)):
            continue
        if src.is_symlink() or not src.is_dir():
            raise FlowError('etx_output_not_a_folder', 'output path %s exists but is not a plain folder; move it by hand' % src)
        if runs_root == src or runs_root.parent == src or src in runs_root.parents:
            raise FlowError('etx_archive_inside_output', 'the archive folder %s would be inside the output folder %s' % (runs_root, src))
        moves.append((src, runs_root / relative))
    for index, (src, _) in enumerate(moves):
        for other, _ in moves[index + 1:]:
            if src in other.parents or other in src.parents:
                raise FlowError('etx_output_nested', 'output folders overlap: %s and %s' % (src, other))
    for _, dst in moves:
        if os.path.lexists(str(dst)):
            raise FlowError('etx_archive_target_exists', 'archive target already exists, nothing was moved: %s '
                            '(use another RUN_TAG)' % dst)
    return moves


def archive_outputs(base_dir, settings, run_tag=None):
    """plan_archive 의 폴더를 data/_runs/<RUN_TAG>/ 로 옮긴다. 같은 파일 시스템 안 os.rename 만 쓴다(복사·삭제 없음).
    대상이 이미 있으면 아무것도 옮기지 않고 FlowError. 옮긴 (원래, 보관) 목록을 돌려준다."""
    moves = plan_archive(base_dir, settings, run_tag)
    done = []
    for src, dst in moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(src), str(dst))
        except OSError as exc:
            moved = ''.join('\n  moved: %s -> %s' % pair for pair in done)
            raise FlowError('etx_archive_failed', 'cannot move %s -> %s: %s (same file system required; nothing is deleted)%s' % (
                src, dst, exc, moved))
        done.append((src, dst))
    return done


def prepare(base_dir, settings, dry_run=False, echo=_echo):
    """JOBS 를 만들고 이전 출력을 보관한 뒤 JOBS 를 돌려준다. dry_run 이면 계획만 찍고 빈 목록을 돌려준다.
    MODE = "products" 는 보관을 하지 않는다(products-step·pair-step 이 자기 세대를 판단·보관한다)."""
    if _mode(_merged(settings)) == 'products':
        return _prepare_products(base_dir, settings, dry_run, echo)
    ctx = load_context(base_dir, settings)
    steps = _steps(ctx.settings)
    jobs = plan_jobs(base_dir, settings)
    tag = _run_tag(ctx.settings['run_tag'])
    moves = plan_archive(base_dir, settings, tag, ctx=ctx)
    echo('flow: steps %s (fixed order); real=%s compare2=%s' % (', '.join(steps), ctx.real_path, ctx.compare2_path))
    for side in ('reference', 'target'):
        hotspot = (ctx.compare2.reference if side == 'reference' else ctx.compare2.target).hotspot_root
        if hotspot.name != ctx.real[side]['product_id']:
            echo('flow: warning: %s product_id %r differs from the hotspot folder name %r' % (
                side, ctx.real[side]['product_id'], hotspot.name))
    echo('flow: %d job(s):' % len(jobs))
    for job in jobs:
        name, args = job
        echo('  job [%s] %s %s' % (job.step, name, ' '.join(args)))
    if moves:
        echo('flow: previous outputs to move to %s/%s (nothing is deleted):' % (RUNS_DIR, tag))
        for src, dst in moves:
            echo('  move %s -> %s' % (src, dst))
    else:
        echo('flow: no previous output folder to move')
    if dry_run:
        echo('flow: DRY_RUN = True: nothing was run or moved')
        return []
    archive_outputs(base_dir, settings, tag)
    return jobs
