"""rexp/T5: the local PC runner (bundles/local/simcompare_job_local.py + run_local.cmd + README_local.md)."""
import re
import sys
from pathlib import Path

from test_etx_flow import _builder, run_runner, template_base

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / 'bundles/local'


def _edit_text(path):
    text = path.read_text(encoding='utf-8')
    return text[text.index('# ============================== EDIT BELOW'):text.index('# ============================== EDIT ABOVE')]


def test_local_runner_is_current_parses_and_has_the_local_edit_block(tmp_path):
    module = _builder()
    module.main(['--out-dir', str(tmp_path)])
    built = tmp_path / 'local/simcompare_job_local.py'
    committed = LOCAL / 'simcompare_job_local.py'
    assert committed.read_bytes() == built.read_bytes(), f'{committed} is stale; rebuild with --overwrite'
    text = built.read_text(encoding='utf-8')
    compile(text, str(built), 'exec')
    edit = _edit_text(built)
    assert re.search(r'^STEPS = \["01", "02", "08"\]', edit, re.M)
    assert re.search(r'^LOG_FILE = "runs/simcompare_job_local.log"', edit, re.M)
    assert 'simcompare_job_etx' not in edit
    assert '로컬 PC 통합 실행기(Windows·macOS). numpy·Pillow 가 있는 Python 으로 실행. 설치 불필요. 사용법: bundles/local/README_local.md' in text
    assert 'run_jobs(_flow_jobs)' in text and text.count('PAYLOAD_SHA256 = ') == 1


def test_local_edit_block_differs_from_the_etx_edit_block_only_in_steps_and_log_file():
    module = _builder()
    etx, local = module.FLOW_EDIT.splitlines(), module.LOCAL_EDIT.splitlines()
    assert len(etx) == len(local)
    changed = [(a.split(' =')[0], b.split(' =')[0]) for a, b in zip(etx, local) if a != b]
    assert changed == [('STEPS', 'STEPS'), ('LOG_FILE', 'LOG_FILE')]


def test_local_runner_dry_run_lists_the_01_02_08_jobs(tmp_path):
    module = _builder()
    base = template_base(tmp_path)
    out = tmp_path / 'runner/job.py'
    module.build(out, header=module.LOCAL_HEADER, flow=(module.LOCAL_EDIT, module.FLOW_FUNC))
    text = out.read_text(encoding='utf-8').replace('BASE_DIR = None', f'BASE_DIR = {str(base)!r}', 1)
    marker = '# ============================== EDIT ABOVE'
    out.write_text(text.replace(marker, 'DRY_RUN = True\n' + marker, 1), encoding='utf-8')
    result = run_runner(sys.executable, out, tmp_path)
    log = (base / 'runs/simcompare_job_local.log').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert 'flow: DRY_RUN = True' in log and 'start ' not in log
    for command in ('import-hotspots', 'compare2 ', 'ledger ', 'ledger-report'):
        assert re.search(r'  job \[\w+\] ' + re.escape(command), log), command
    for command in ('import-electrode-assets', 'candidates-change', 'compare-windows', 'compare2-report'):
        assert not re.search(r'  job \[\w+\] ' + re.escape(command), log), command


def test_run_local_cmd_content():
    path = LOCAL / 'run_local.cmd'
    raw = path.read_bytes()
    raw.decode('ascii')                                   # ASCII only: no code page issue on any Windows
    text = raw.decode('ascii')
    assert raw.count(b'\r\n') == raw.count(b'\n'), 'run_local.cmd must use CRLF line ends'
    order = ['call :try "%SIMCOMPARE_PYTHON%"', "where python.exe", r'call :try "%USERPROFILE%\anaconda3\python.exe"',
             r'call :try "%USERPROFILE%\miniconda3\python.exe"', r'call :try "%ProgramData%\anaconda3\python.exe"',
             r'call :try "%LOCALAPPDATA%\anaconda3\python.exe"', r'call :try "%ProgramData%\miniconda3\python.exe"']
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)
    assert 'where python\n' not in text.replace('\r\n', '\n') and "where python " not in text
    assert r'\WindowsApps\\' not in text and r'\WindowsApps\=' in text           # Store alias is skipped
    assert '"%~1" -c "import numpy, PIL"' in text                                # every candidate is tested
    assert 'SIMCOMPARE_PYTHON does not exist' in text                            # warning for a missing file
    assert '-X utf8 "%~dp0simcompare_job_local.py"' in text
    assert 'echo [simcompare] Python: "%PY%"' in text
    assert 'exit /b %RC%' in text and text.count('pause') >= 3
    assert r'runs\simcompare_job_local.log' in text
    for forbidden in ('setx', 'reg add', 'reg.exe', 'runas', 'pip install'):
        assert forbidden not in text.lower()


def test_run_local_cmd_flow_is_static_consistent():
    lines = (LOCAL / 'run_local.cmd').read_text(encoding='ascii').splitlines()
    labels = [line[1:].strip() for line in lines if line.startswith(':') and not line.startswith('::')]
    assert labels == ['nopython', 'run', 'try']
    for target in re.findall(r'goto :(\w+)', '\n'.join(lines)):
        assert target in labels
    for target in re.findall(r'call :(\w+)', '\n'.join(lines)):
        assert target in labels
    body = '\n'.join(lines)
    assert 'exit /b 1' in body and 'exit /b 2' in body
    try_block = body[body.index(':try\n'):]
    assert try_block.rstrip().endswith('exit /b 0')                              # the subroutine always returns
    assert 'set "PY=%~1"' in try_block and 'goto :eof' not in body              # PY is set only after the import test
    assert not re.search(r'^\s*for .*python(?!\.exe)', body, re.M)              # only python.exe, never .bat/.cmd shims
    assert try_block.index('import numpy, PIL') < try_block.index('set "PY=%~1"')


def test_gitattributes_keeps_cmd_files_as_stored_crlf():
    # `-text`: no EOL conversion, so the stored blob keeps CRLF and a raw GitHub download has CRLF too
    assert '*.cmd -text' in (ROOT / '.gitattributes').read_text(encoding='utf-8').splitlines()
    for path in (ROOT / 'bundles/local/run_local.cmd', ROOT / 'scripts/install_offline.cmd'):
        raw = path.read_bytes()
        assert raw.count(b'\r\n') == raw.count(b'\n') > 0, path


def test_builder_treats_a_relative_out_dir_like_the_default(tmp_path, monkeypatch):
    module = _builder()
    seen = []
    monkeypatch.setattr(module, '_build_all', lambda out_dir, overwrite, local_dir: seen.append(local_dir) or [])
    monkeypatch.chdir(ROOT)
    module.main(['--out-dir', 'bundles/etx'])                 # relative path = the default folder
    module.main(['--out-dir', str(ROOT / 'bundles/etx')])
    module.main(['--out-dir', str(tmp_path)])                 # anything else keeps <out-dir>/local
    assert seen == [str(ROOT / 'bundles/local')] * 2 + [None]


def test_readme_local_covers_the_agreed_points():
    text = (LOCAL / 'README_local.md').read_text(encoding='utf-8')
    for needle in ('numpy', 'Pillow', 'real.yaml', 'compare2.yaml', 'BASE_DIR', 'run_local.cmd', 'python simcompare_job_local.py',
                   'data/ledger_report/ledger_report.html', '260', '32 GB', '.simcompare_pkg_', 'C:/'):
        assert needle in text, needle
    assert 'real.yaml 의 경로는 BASE_DIR 안의 상대 경로만 쓴다(절대 경로·`..`·백슬래시는 거부된다)' in text
    assert '`hotspot_root`·`dl1_root`' in text and '`python.exe`' in text and r'\WindowsApps\ ' not in text
