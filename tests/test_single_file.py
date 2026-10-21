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
