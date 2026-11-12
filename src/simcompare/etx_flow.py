"""ETX 통합 실행기(bundles/etx/simcompare_job_etx.py)의 계획 로직.

실행기 EDIT 블록의 값(STEPS 등)과 설정 파일 두 개(real.yaml, compare2.yaml)에서 JOBS 를 만들고,
선택한 단계의 이전 출력 폴더를 data/_runs/<RUN_TAG>/ 로 옮긴다(삭제·복사 없음, 같은 파일 시스템 안 os.rename).
계산 코드는 건드리지 않는다: 이 모듈은 `python -m simcompare <명령> <인자...>` 의 인자 목록만 만든다.
Python 3.8 문법, 표준 라이브러리와 번들 PyYAML 만 쓴다.
"""
import os
import sys
import time
from pathlib import Path

STEP_ORDER = ('01', '03', '02', 'diag', '04', '05', '06', '07')     # 실행 순서(STEPS 의 순서가 아니다)
DEFAULT_STEPS = ('01', '03', '02', 'diag', '04', '06', '07')       # 05(DL2 확정)는 단계로 남기되 기본에서 뺀다
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
}


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
    return jobs


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
    """JOBS 를 만들고 이전 출력을 보관한 뒤 JOBS 를 돌려준다. dry_run 이면 계획만 찍고 빈 목록을 돌려준다."""
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
    for name, args in jobs:
        echo('  job %s %s' % (name, ' '.join(args)))
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
