"""화면 인코딩(EUC-KR 등)에 없는 글자(µ)가 있어도 실행기·CLI가 죽지 않는다.

재현 명령으로 `candidates-change --help`를 쓴다: 도움말 본문에 `µm`이 있어 화면(stdout)에 µ가 나가고,
합성 자료가 필요 없으며 exit 0으로 끝난다(07회차 compare2-report의 `µm 입력 없음` 경고와 같은 종류의 출력).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = [('candidates-change', ['--help'])]


def _build(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        from build_single_file import build
    finally:
        sys.path.pop(0)
    out = tmp_path / 'job.py'
    build(out)
    text = out.read_text(encoding='utf-8')
    start, end = text.index('JOBS = ['), text.index('LOG_FILE =')
    out.write_text(text[:start] + f'JOBS = {JOBS!r}\n' + f'LOG_FILE = {str(tmp_path / "job.log")!r}\n'
                   + text[text.index('WORK_DIR =', end):], encoding='utf-8')
    return out


def _env(encoding):
    env = dict(os.environ, PYTHONIOENCODING=encoding)
    env.pop('PYTHONUTF8', None)
    env.pop('PYTHONSTARTUP', None)
    return env


def test_runner_survives_unencodable_char_on_euc_kr_screen(tmp_path):
    script = _build(tmp_path)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, cwd=tmp_path, env=_env('euc_kr'))
    stderr = result.stderr.decode('euc_kr')
    assert result.returncode == 0, stderr
    assert 'Traceback' not in stderr and 'UnicodeEncodeError' not in stderr
    assert b'\\xb5' in result.stdout                      # 화면에는 µ 대신 escape
    assert '두 제품'.encode('euc_kr') in result.stdout     # 한글은 그대로 euc_kr 로 나간다
    log = (tmp_path / 'job.log').read_text(encoding='utf-8')
    assert 'µ' in log                                      # 로그 파일에는 UTF-8 원문


def test_runner_utf8_screen_keeps_original_char(tmp_path):
    script = _build(tmp_path)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, cwd=tmp_path, env=_env('utf-8'))
    assert result.returncode == 0, result.stderr.decode('utf-8')
    assert 'µ'.encode('utf-8') in result.stdout
    assert b'\\xb5' not in result.stdout


def test_cli_main_survives_unencodable_char_on_euc_kr_screen(tmp_path):
    env = _env('euc_kr')
    env['PYTHONPATH'] = str(ROOT / 'src')
    result = subprocess.run([sys.executable, '-m', 'simcompare', 'candidates-change', '--help'],
                            capture_output=True, cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr.decode('euc_kr', 'replace')
    assert b'\\xb5' in result.stdout and b'Traceback' not in result.stderr
