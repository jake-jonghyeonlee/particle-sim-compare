import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _build(tmp_path, jobs):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        from build_single_file import build
    finally:
        sys.path.pop(0)
    out = tmp_path / 'job.py'
    build(out)
    text = out.read_text(encoding='utf-8')
    start, end = text.index('JOBS = ['), text.index('LOG_FILE =')
    out.write_text(text[:start] + f'JOBS = {jobs!r}\n' + f'LOG_FILE = {str(tmp_path / "job.log")!r}\n'
                   + text[text.index('WORK_DIR =', end):], encoding='utf-8')
    return out


def test_single_file_runs_jobs_continues_on_partial_and_stops_on_error(tmp_path):
    demo = tmp_path / 'demo'
    jobs = [('demo-data', ['--out', str(demo)]),
            ('validate-data', ['--config', str(demo / 'config.yaml')]),
            ('validate-data', ['--config', str(tmp_path / 'missing.yaml')]),
            ('compare', ['--config', str(demo / 'config.yaml')])]
    script = _build(tmp_path, jobs)
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    log = (tmp_path / 'job.log').read_text(encoding='utf-8')
    assert result.returncode == 2
    assert 'validate-data exit 3' in log and 'validate-data exit 2' in log
    assert 'start compare' not in log
    assert '.simcompare_pkg_' in log
    assert '=====' not in log and '요약' not in log        # rexp/T4: 2-tuple jobs have no steps, so no step lines


def test_rexp_t4_three_tuple_jobs_get_step_lines_and_a_summary(tmp_path):
    demo = tmp_path / 'demo'
    jobs = [('A', 'demo-data', ['--out', str(demo)]),
            ('A', 'validate-data', ['--config', str(demo / 'config.yaml')]),
            ('B', 'validate-data', ['--config', str(tmp_path / 'missing.yaml')]),
            ('C', 'compare', ['--config', str(demo / 'config.yaml')])]
    script = _build(tmp_path, jobs)
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    log = (tmp_path / 'job.log').read_text(encoding='utf-8')
    assert result.returncode == 2
    assert '===== [A] A 시작 ' in log and '(job 2 개) =====' in log and ' · exit demo-data 0, validate-data' in log
    assert '===== [B] 실패 · validate-data exit 2 · 이후 단계는 실행하지 않음 =====' in log
    assert '[simcompare_job]   [B] 2  ' in log and '[simcompare_job]   [C] -  (실행 안 함)' in log and '전체 ' in log

def test_r2_unhandled_exception_still_logs_the_step_failure_and_summary(tmp_path):
    jobs = [('A', 'demo-data', ['--out', str(tmp_path / 'demo')]), ('B', 'boom', []), ('C', 'compare', [])]
    script = _build(tmp_path, jobs)
    text = script.read_text(encoding='utf-8')
    stub = ("from simcompare.cli import main as _real_main\n"
            "    def main(argv):\n"
            "        if argv[0] == 'boom':\n"
            "            raise MemoryError('boom')\n"
            "        return _real_main(argv)\n")
    assert text.count('    from simcompare.cli import main\n') == 1
    script.write_text(text.replace('    from simcompare.cli import main\n', '    ' + stub, 1), encoding='utf-8')
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    log = (tmp_path / 'job.log').read_text(encoding='utf-8')
    assert result.returncode == 1                       # same exit code as an uncaught exception
    assert 'MemoryError' in log and 'boom exit 1' in log
    assert '===== [B] 실패 · boom exit 1 · 이후 단계는 실행하지 않음 =====' in log
    assert '[simcompare_job]   [B] 1  ' in log and '[simcompare_job]   [C] -  (실행 안 함)' in log
    assert 'start compare' not in log


def test_relative_paths_resolve_from_runner_folder_not_launch_cwd(tmp_path):
    work = tmp_path / 'runner'
    work.mkdir()
    script = _build(work, [('demo-data', ['--out', 'demo']), ('validate-data', ['--config', 'demo/config.yaml'])])
    text = script.read_text(encoding='utf-8').replace(repr(str(work / 'job.log')), "'logs/job.log'")
    script.write_text(text, encoding='utf-8')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=elsewhere)
    assert result.returncode == 0, result.stderr
    assert (work / 'demo' / 'config.yaml').is_file() and not (elsewhere / 'demo').exists()
    log = (work / 'logs' / 'job.log').read_text(encoding='utf-8')
    assert f'relative paths resolve from {work.resolve()}' in log


def test_t17_runner_log_resolves_link_option_paths(tmp_path):
    work = tmp_path / 'runner'
    work.mkdir()
    args = ['--config', 'compare2.yaml', '--windows', 'data/local', '--candidates', 'data/changes',
            '--confirm', 'data/confirmed', '--compare2-dir', 'data/compare2',
            '--dl1-search', 'dl1a', '--dl1-search', 'dl1b', '--out', 'data/report']
    script = _build(work, [('compare2-report', args)])
    subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    log = (work / 'job.log').read_text(encoding='utf-8')
    base = work.resolve()
    for flag, value, path in (('--windows', 'data/local', 'data/local'), ('--candidates', 'data/changes', 'data/changes'),
                              ('--confirm', 'data/confirmed', 'data/confirmed'), ('--compare2-dir', 'data/compare2', 'data/compare2'),
                              ('--dl1-search', 'dl1a', 'dl1a'), ('--dl1-search', 'dl1b', 'dl1b')):
        assert f'{flag} {value!r} -> {base / path}' in log, (flag, value)


def test_t24_equals_split_is_per_command(tmp_path):
    """T17 adv1/adv2: only KEY=PATH options (model-report --windows, confirm-pairs --match) split at '='; compare2-report
    --windows is a plain path, with or without a '=' in it."""
    cases = [('compare2-report', 'data/local', 'data/local'),               # no '='
             ('model-report', 'QA4_R00=data/local', 'data/local'),          # KEY=PATH: the part after '=' is the path
             ('compare2-report', 'odd=dir/local', 'odd=dir/local')]        # the whole value is the path
    for i, (command, value, path) in enumerate(cases):
        work = tmp_path / f'runner{i}'      # a failing job stops the runner, so every case has its own runner
        work.mkdir()
        script = _build(work, [(command, ['--windows', value, '--out', 'data/out'])])
        subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
        log = (work / 'job.log').read_text(encoding='utf-8')
        assert f"--windows {value!r} -> {work.resolve() / path}" in log, (command, value)


def test_base_dir_overrides_runner_folder(tmp_path):
    work, base = tmp_path / 'runner', tmp_path / 'base'
    work.mkdir(); base.mkdir()
    script = _build(work, [('demo-data', ['--out', 'demo'])])
    text = script.read_text(encoding='utf-8').replace('BASE_DIR = None', f'BASE_DIR = {str(base)!r}', 1)
    script.write_text(text, encoding='utf-8')
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (base / 'demo' / 'config.yaml').is_file() and not (work / 'demo').exists()


# ---- pasx/T6: 마무리 job(finalizers). 대체 finalizer = 실제 명령 중 효과가 보이는 것(demo-data = 폴더를 만든다, 없는 설정의
# validate-data = 종료 코드 2). 실제 products-sheet 연결 검증은 T7 수용이다. -------------------------------------------------------

def _build_finalized(tmp_path, jobs, finalizers, stub=None):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        from build_single_file import build
    finally:
        sys.path.pop(0)
    out = tmp_path / 'job.py'
    edit = f'BASE_DIR = {str(tmp_path)!r}\nLOG_FILE = "job.log"\nWORK_DIR = None\n'
    func = ('def _flow_jobs():\n    from simcompare import etx_flow\n'
            f'    return etx_flow.FlowJobs({jobs!r}, finalizers={finalizers!r})\n')
    build(out, flow=(edit, func))
    if stub is not None:
        text = out.read_text(encoding='utf-8')
        out.write_text(text.replace('    from simcompare.cli import main\n', '    ' + stub, 1), encoding='utf-8')
    return out


def _run_finalized(tmp_path, script):
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    return result, (tmp_path / 'job.log').read_text(encoding='utf-8')


GOOD_FINALIZER = ('demo-data', ['--out', 'finalized'])
BAD_FINALIZER = ('validate-data', ['--config', 'missing-finalizer.yaml'])
GOOD_JOB = ('demo-data', ['--out', 'main-ok'])
BAD_JOB = ('validate-data', ['--config', 'missing.yaml'])


def test_finalizer_runs_after_a_successful_run(tmp_path):
    result, log = _run_finalized(tmp_path, _build_finalized(tmp_path, [GOOD_JOB], [GOOD_FINALIZER]))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'finalized/config.yaml').is_file()
    assert '마무리 job demo-data exit 0' in log


def test_finalizer_runs_after_the_main_job_stops_with_exit_2_and_keeps_that_code(tmp_path):
    result, log = _run_finalized(tmp_path, _build_finalized(tmp_path, [GOOD_JOB, BAD_JOB, ('demo-data', ['--out', 'never'])],
                                                            [GOOD_FINALIZER]))
    assert result.returncode == 2
    assert (tmp_path / 'finalized/config.yaml').is_file() and not (tmp_path / 'never').exists()
    assert '마무리 job demo-data exit 0' in log and '경고' not in log


def test_a_failing_finalizer_only_warns_and_never_changes_the_exit_code(tmp_path):
    result, log = _run_finalized(tmp_path, _build_finalized(tmp_path, [GOOD_JOB, BAD_JOB], [BAD_FINALIZER, GOOD_FINALIZER]))
    assert result.returncode == 2                                           # the main job's code
    assert '경고: 마무리 job validate-data 이(가) exit 2 로 실패했다' in log
    assert (tmp_path / 'finalized/config.yaml').is_file()                    # the next finalizer still ran
    tmp2 = tmp_path / 'ok'
    tmp2.mkdir()
    result, log = _run_finalized(tmp2, _build_finalized(tmp2, [GOOD_JOB], [BAD_FINALIZER]))
    assert result.returncode == 0, result.stderr                             # a failed finalizer does not fail a good run
    assert '경고: 마무리 job validate-data' in log


def test_finalizer_does_not_run_on_keyboard_interrupt(tmp_path):
    stub = ("from simcompare.cli import main as _real_main\n"
            "    def main(argv):\n"
            "        if argv[0] == 'validate-data':\n"
            "            raise KeyboardInterrupt()\n"
            "        return _real_main(argv)\n")
    script = _build_finalized(tmp_path, [GOOD_JOB, BAD_JOB], [GOOD_FINALIZER], stub=stub)
    result, log = _run_finalized(tmp_path, script)
    assert result.returncode != 0 and 'KeyboardInterrupt' in log
    assert not (tmp_path / 'finalized').exists() and '마무리 job' not in log


def test_plain_job_lists_and_pair_mode_have_no_finalizer(tmp_path):
    script = _build(tmp_path, [GOOD_JOB])
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0
    assert '마무리' not in (tmp_path / 'job.log').read_text(encoding='utf-8')


# ---- pasx/T6b (R6:8): an Exception (not an exit code) in a finalizer or in the main job -------------------------------------

BOOM_STUB = ("from simcompare.cli import main as _real_main\n"
             "    def main(argv):\n"
             "        if '--out' in argv and argv[argv.index('--out') + 1] == 'boom':\n"
             "            raise RuntimeError('boom')\n"
             "        return _real_main(argv)\n")


def test_a_finalizer_that_raises_an_exception_only_warns_and_the_next_finalizer_still_runs(tmp_path):
    boom = ('demo-data', ['--out', 'boom'])
    script = _build_finalized(tmp_path, [GOOD_JOB], [boom, GOOD_FINALIZER], stub=BOOM_STUB)
    result, log = _run_finalized(tmp_path, script)
    assert result.returncode == 0, result.stderr                              # the main job's code
    assert 'RuntimeError: boom' in log and '경고: 마무리 job demo-data 이(가) exit 1 로 실패했다' in log
    assert (tmp_path / 'finalized/config.yaml').is_file() and not (tmp_path / 'boom').exists()


def test_the_main_job_that_raises_an_exception_exits_1_and_the_finalizer_still_runs(tmp_path):
    script = _build_finalized(tmp_path, [GOOD_JOB, ('demo-data', ['--out', 'boom']), ('demo-data', ['--out', 'never'])],
                              [GOOD_FINALIZER], stub=BOOM_STUB)
    result, log = _run_finalized(tmp_path, script)
    assert result.returncode == 1
    assert 'RuntimeError: boom' in log and '마무리 job demo-data exit 0' in log
    assert (tmp_path / 'finalized/config.yaml').is_file() and not (tmp_path / 'never').exists()
