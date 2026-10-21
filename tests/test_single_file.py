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


def test_base_dir_overrides_runner_folder(tmp_path):
    work, base = tmp_path / 'runner', tmp_path / 'base'
    work.mkdir(); base.mkdir()
    script = _build(work, [('demo-data', ['--out', 'demo'])])
    text = script.read_text(encoding='utf-8').replace('BASE_DIR = None', f'BASE_DIR = {str(base)!r}', 1)
    script.write_text(text, encoding='utf-8')
    result = subprocess.run([sys.executable, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (base / 'demo' / 'config.yaml').is_file() and not (work / 'demo').exists()
