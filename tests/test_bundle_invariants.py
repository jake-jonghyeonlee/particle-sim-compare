"""pasx/T10: regenerated bundles keep the EDIT block and the JOBS of the base commit 274cfea (pair mode is unchanged).

The head of a bundle (everything up to the `EDIT ABOVE` line) holds the EDIT block and, for the stage runners, the JOBS list.
The embedded package payload and the shared `run_jobs` template follow it and are not compared here.
The integrated ETX and local runners may only add the product-mode values (MODE, LEDGER_MAPS_CARDS and the product block).
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = '274cfea'
MARK = 'EDIT ABOVE'
INTEGRATED = ('bundles/etx/simcompare_job_etx.py', 'bundles/local/simcompare_job_local.py')
ADDED_NAMES = {'MODE', 'LEDGER_MAPS_CARDS', 'PRODUCTS_CONFIG', 'PRODUCTS', 'ASSET_STEPS', 'COMPARE', 'COMPARE_STEPS',
               'FORCE_ASSETS', 'PRODUCTS_IMPORT', 'PRODUCTS_IMPORT_UPDATE', 'RECORD_MAPS', 'MAPS_SRC_MAX_BYTES'}
STAGE_RUNNERS = sorted(
    str(path.relative_to(ROOT)) for path in list((ROOT / 'bundles/etx').glob('simcompare_job_*.py'))
    + list((ROOT / 'bundles/etx/dv').glob('simcompare_job_*.py')) + list((ROOT / 'bundles/local').glob('simcompare_job_*.py'))
    + [ROOT / 'bundles/simcompare_job.py'])


def _head(text):
    """Lines up to and including the EDIT ABOVE line, without the generated docstring line that carries the source hash."""
    lines = text.split('\n')
    end = next(index for index, line in enumerate(lines) if MARK in line)
    return [line for line in lines[:end + 1] if not line.startswith('"""simcompare 단일 파일 실행기 (자동 생성')]


def _base_head(path):
    try:
        have_base = subprocess.run(['git', 'cat-file', '-e', BASE + '^{commit}'], cwd=str(ROOT), capture_output=True).returncode == 0
        result = subprocess.run(['git', 'show', '%s:%s' % (BASE, path)], cwd=str(ROOT), capture_output=True) if have_base else None
    except OSError:                       # no git executable here (FileNotFoundError is an OSError)
        pytest.skip('git is not available here')
    if not have_base:
        pytest.skip('base commit %s is not available here' % BASE)
    assert result.returncode == 0, 'base commit %s has no %s: %s' % (BASE, path, result.stderr.decode('utf-8', 'replace'))
    return _head(result.stdout.decode('utf-8'))


def _is_added_line(line):
    if not line.strip():
        return True
    name = re.match(r'^([A-Z][A-Z0-9_]*) = ', line)
    if name:
        return name.group(1) in ADDED_NAMES
    return line.startswith('# ---- 제품 모드') or line.startswith('# 제품 모드는 아래 제품 값과')


def test_there_are_bundles_to_compare():
    assert len(STAGE_RUNNERS) >= 14, STAGE_RUNNERS


@pytest.mark.parametrize('path', [name for name in STAGE_RUNNERS if name not in INTEGRATED])
def test_stage_runner_head_is_byte_identical_to_the_base(path):
    new = _head((ROOT / path).read_text(encoding='utf-8'))
    assert new == _base_head(path), path


@pytest.mark.parametrize('path', INTEGRATED)
def test_integrated_runner_keeps_every_pair_mode_value(path):
    new = _head((ROOT / path).read_text(encoding='utf-8'))
    base = _base_head(path)
    position, extra = 0, []
    for line in new:
        if position < len(base) and line == base[position]:
            position += 1
        else:
            extra.append(line)
    assert position == len(base), 'a base line is missing or changed near: %r' % base[position]
    unexpected = [line for line in extra if not _is_added_line(line)]
    assert not unexpected, unexpected
    added = {re.match(r'^([A-Z][A-Z0-9_]*) = ', line).group(1) for line in extra if re.match(r'^[A-Z][A-Z0-9_]* = ', line)}
    assert added == ADDED_NAMES, sorted(ADDED_NAMES ^ added)
