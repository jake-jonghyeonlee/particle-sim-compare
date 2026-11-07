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
