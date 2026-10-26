"""change-roi/T2: stage-1 runner behaviour (expected_cases.txt, log contents, explicit missing-path errors).

The generated runner file is executed as a subprocess under the current interpreter and, when SIMCOMPARE_ETX_PY is set,
under the ETX interpreter (Python 3.9 with older numpy/Pillow)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from simcompare.adapters.real_hotspot import import_hotspots, load_real_manifest

from test_real_hotspot import _source

ROOT = Path(__file__).resolve().parents[1]
CASE_AB, CASE_AC = 'lateral_short-A-L1-B-L1', 'lateral_short-A-L1-C-L1'
SHAPE = (1002, 1002)

PYTHONS = [
    pytest.param(sys.executable, id='current'),
    pytest.param(os.environ.get('SIMCOMPARE_ETX_PY'), id='etx39', marks=pytest.mark.skipif(
        not os.environ.get('SIMCOMPARE_ETX_PY'),
        reason='SIMCOMPARE_ETX_PY is not set (path of the ETX Python 3.9 interpreter)')),
]


def _make_runner(folder, jobs, *, log_file, base_dir=None, work_dir=None):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        from build_single_file import build
    finally:
        sys.path.pop(0)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / 'job.py'
    build(out)
    text = out.read_text(encoding='utf-8')
    start, end = text.index('JOBS = ['), text.index('LOG_FILE = ')
    text = text[:start] + f'JOBS = {jobs!r}\n' + text[end:]
    text = text.replace('LOG_FILE = "runs/simcompare_job.log"', f'LOG_FILE = {log_file!r}', 1)
    text = text.replace('WORK_DIR = None', f'WORK_DIR = {work_dir!r}', 1)
    text = text.replace('BASE_DIR = None', f'BASE_DIR = {base_dir!r}', 1)
    out.write_text(text, encoding='utf-8')
    return out


def _roots(base):
    ref, tgt = base / 'ref', base / 'tgt'
    _source(ref, CASE_AB, shape=SHAPE)
    _source(ref, CASE_AC, shape=SHAPE)
    _source(tgt, CASE_AB, shape=SHAPE)
    return ref, tgt


def _run(python, script, cwd):
    return subprocess.run([python, '-I', str(script)], capture_output=True, text=True, cwd=cwd)


def _payload_sha(script):
    return re.search(r'^PAYLOAD_SHA256 = "([0-9a-f]{64})"', script.read_text(encoding='utf-8'), re.M).group(1)


def _txt_cases(path):
    return [line for line in path.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]


@pytest.mark.parametrize('python', PYTHONS)
def test_normal_jobs_write_expected_cases_and_log_identifies_the_run(tmp_path, python):
    ref, tgt = _roots(tmp_path)
    log = tmp_path / 'job.log'
    jobs = [('import-hotspots', ['--root', str(ref), '--expected-cases-from-roots', str(tgt), '--out', str(tmp_path / 'o_ref')]),
            ('import-hotspots', ['--root', str(tgt), '--expected-cases-from-roots', str(ref), '--out', str(tmp_path / 'o_tgt')])]
    script = _make_runner(tmp_path / 'runner', jobs, log_file=str(log))
    result = _run(python, script, tmp_path)
    text = log.read_text(encoding='utf-8')
    assert result.returncode == 0 or 'exit 3' in text, result.stderr
    for side in ('o_ref', 'o_tgt'):
        out = tmp_path / side
        manifest = load_real_manifest(out)
        assert manifest['expected_case_coverage_status'] == 'confirmed'
        assert manifest['expected_cases'] == [CASE_AB, CASE_AC]
        assert _txt_cases(out / 'expected_cases.txt') == manifest['expected_cases']
        assert 'expected_cases.txt' not in manifest['source_files']
        header = [line for line in (out / 'expected_cases.txt').read_text(encoding='utf-8').splitlines() if line.startswith('#')]
        assert any('union_of_discovered_roots' in line for line in header) and any('limitation' in line for line in header)
    assert f'payload_sha256={_payload_sha(script)}' in text
    assert str(script) in text and 'numpy' in text and 'Pillow' in text
    assert 'BASE_DIR requested=None' in text and 'WORK_DIR requested=None' in text and 'package extracted to' in text
    assert f"--root {str(ref)!r} -> {ref}" in text and f"--expected-cases-from-roots {str(tgt)!r} -> {tgt}" in text
    assert text.count('expected_cases: 2 (source=union_of_discovered_roots; roots=') == 2
    assert 'import-hotspots exit 3' in text


@pytest.mark.parametrize('python', PYTHONS)
def test_old_jobs_without_flag_leave_expected_cases_null_and_no_file(tmp_path, python):
    ref, _ = _roots(tmp_path)
    log = tmp_path / 'job.log'
    script = _make_runner(tmp_path / 'runner', [('import-hotspots', ['--root', str(ref), '--out', str(tmp_path / 'o')])],
                          log_file=str(log))
    _run(python, script, tmp_path)
    assert load_real_manifest(tmp_path / 'o')['expected_cases'] is None
    assert not (tmp_path / 'o' / 'expected_cases.txt').exists()
    assert 'expected_cases: none (coverage unconfirmed; stage-4 ROIs will be blocked)' in log.read_text(encoding='utf-8')


@pytest.mark.parametrize('python', PYTHONS)
def test_base_dir_changes_resolution_and_is_logged(tmp_path, python):
    base = tmp_path / 'base'
    _roots(base)
    runner_dir = tmp_path / 'runner'
    script = _make_runner(runner_dir, [('import-hotspots', ['--root', 'ref', '--out', 'out'])],
                          log_file='runs/job.log', base_dir=str(base))
    _run(python, script, tmp_path)
    assert (base / 'out' / 'manifest.json').is_file() and not (runner_dir / 'out').exists()
    text = (base / 'runs' / 'job.log').read_text(encoding='utf-8')
    assert f'BASE_DIR requested={str(base)!r} resolved={base.resolve()}' in text
    assert f"--root 'ref' -> {base.resolve() / 'ref'}" in text


@pytest.mark.parametrize('python', PYTHONS)
def test_unwritable_work_dir_falls_back_and_says_so(tmp_path, python):
    ref, _ = _roots(tmp_path)
    log = tmp_path / 'job.log'
    blocker = tmp_path / 'not_a_dir'
    blocker.write_text('x', encoding='utf-8')
    bad = str(blocker / 'simcompare_work')   # a path below a regular file: mkdir fails with an OSError on every platform
    script = _make_runner(tmp_path / 'runner', [('import-hotspots', ['--root', str(ref), '--out', str(tmp_path / 'o')])],
                          log_file=str(log), work_dir=bad)
    result = _run(python, script, tmp_path)
    text = log.read_text(encoding='utf-8')
    assert (tmp_path / 'o' / 'manifest.json').is_file(), result.stderr
    assert f'WORK_DIR requested={bad!r}' in text
    assert f'cannot extract into {bad}:' in text
    extracted = re.search(r'package extracted to (\S+)', text).group(1)
    assert not extracted.startswith(bad) and '.simcompare_pkg_' in extracted


@pytest.mark.parametrize('python', PYTHONS)
def test_missing_expected_cases_root_stops_with_code_2_and_names_the_argument(tmp_path, python):
    ref, _ = _roots(tmp_path)
    log = tmp_path / 'job.log'
    missing = 'data/input/hotspot/NOPE'
    script = _make_runner(tmp_path / 'runner',
                          [('import-hotspots', ['--root', str(ref), '--expected-cases-from-roots', missing, '--out', str(tmp_path / 'o')])],
                          log_file=str(log), base_dir=str(tmp_path))
    result = _run(python, script, tmp_path)
    text = log.read_text(encoding='utf-8')
    assert result.returncode == 2
    assert (f'오류: expected_case_root_missing: --expected-cases-from-roots 경로가 없습니다: {missing} '
            f'(resolved: {tmp_path.resolve() / missing})') in text
    assert 'import-hotspots exit 2' in text
    assert not (tmp_path / 'o' / 'manifest.json').exists()
    # C1: a missing input path leaves no --out folder, error.json or execution.json behind
    assert not (tmp_path / 'o').exists()
    # fixing the path and running again with the same --out succeeds
    (tmp_path / 'data' / 'input' / 'hotspot').mkdir(parents=True)
    (tmp_path / 'tgt').rename(tmp_path / 'data' / 'input' / 'hotspot' / 'NOPE')
    rerun = _run(python, script, tmp_path)
    assert rerun.returncode in (0, 3), rerun.stderr
    assert (tmp_path / 'o' / 'manifest.json').is_file()
    assert not (tmp_path / 'o' / 'error.json').exists()


# ---- function level -------------------------------------------------------------------------------------------------

def test_null_expected_list_writes_no_file_but_empty_confirmed_list_writes_header_only(tmp_path):
    ref, _ = _roots(tmp_path)
    none_out = tmp_path / 'none'
    import_hotspots(ref, none_out)
    assert not (none_out / 'expected_cases.txt').exists()
    empty_names = tmp_path / 'empty.txt'
    empty_names.write_text('# nothing expected\n', encoding='utf-8')
    out = tmp_path / 'empty_out'
    import_hotspots(ref, out, expected_cases=empty_names)
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))   # load_real_manifest rejects it later (cases not in list)
    assert manifest['expected_cases'] == [] and manifest['expected_case_coverage_status'] == 'confirmed'
    lines = (out / 'expected_cases.txt').read_text(encoding='utf-8').splitlines()
    assert lines and all(line.startswith('#') for line in lines)


def test_expected_cases_file_source_and_manifest_bytes_do_not_depend_on_the_txt(tmp_path):
    ref, _ = _roots(tmp_path)
    names = tmp_path / 'names.txt'
    names.write_text(f'{CASE_AC}\n{CASE_AB}\n', encoding='utf-8')
    out = tmp_path / 'o'
    import_hotspots(ref, out, expected_cases=names)
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['expected_cases'] == [CASE_AC, CASE_AB]   # file order is kept
    assert _txt_cases(out / 'expected_cases.txt') == manifest['expected_cases']
    assert manifest['expected_case_source'] == {'kind': 'file'}
    assert 'expected_cases.txt' not in json.dumps(manifest['source_files'])


@pytest.mark.parametrize('kwargs, code, argument', [
    ({'root': 'nope'}, 'real_hotspot_root_missing', '--root'),
    ({'expected_cases': 'nope.txt'}, 'expected_cases_file_missing', '--expected-cases'),
    ({'expected_case_roots': ['nope']}, 'expected_case_root_missing', '--expected-cases-from-roots'),
])
def test_missing_paths_are_explicit_and_write_nothing(tmp_path, kwargs, code, argument):
    ref, _ = _roots(tmp_path)
    out = tmp_path / 'o'
    root = tmp_path / kwargs.pop('root') if 'root' in kwargs else ref
    kwargs = {k: ([tmp_path / v for v in val] if isinstance(val, list) else tmp_path / val) for k, val in kwargs.items()}
    with pytest.raises(FileNotFoundError) as caught:
        import_hotspots(root, out, **kwargs)
    message = str(caught.value)
    assert message.startswith(f'{code}: {argument} 경로가 없습니다: ') and '(resolved: ' in message
    assert not out.exists() and not list(tmp_path.glob('o.tmp-*'))
