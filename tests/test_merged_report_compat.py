"""mrep/T9: the merged report on top of existing 02 / 04 / 07 outputs (STEPS = ['08'] only) and the runner's 08 arguments.

The 02, 04 and 07 code is byte-identical to the baseline c194d0e (only ledger*.py, cli.py and etx_flow.py changed), so the outputs the
chain fixture makes with this tree are the outputs the baseline code makes. A copy of that working folder gets only step 08: the
02 / 04 / 07 output files must stay byte-identical, the new ledger_report.html must link 07 with a relative path.
"""
import hashlib
import json
import shutil

from simcompare import ledger_report
from test_etx_chain import _exit_codes, _run_flow, chain  # noqa: F401  (fixture)
from test_ledger_report import run_report, sample_doc

KEPT = ('data/compare2', 'data/changes', 'data/compare2_report')


def digest(base):
    found = {}
    for folder in KEPT:
        for path in sorted((base / folder).rglob('*')):
            if path.is_file():
                found[path.relative_to(base).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_step_08_alone_keeps_02_04_07_and_writes_the_merged_report_with_a_relative_07_link(chain, tmp_path):  # noqa: F811
    base = tmp_path / 'work'
    shutil.copytree(chain.base, base)
    for folder in ('data/ledger', 'data/ledger_maps', 'data/ledger_report', 'data/_runs'):      # a user starts from 02-07 only
        if (base / folder).exists():
            shutil.rmtree(base / folder)
    before = digest(base)
    assert any(name.startswith('data/compare2_report/') for name in before) and any(name.startswith('data/changes/') for name in before)
    text, result = _run_flow(chain.python, base, tmp_path, 'only08', STEPS=['08'], RUN_TAG='ONLY08')
    assert [name for name, _ in _exit_codes(text)] == ['ledger', 'ledger-maps', 'ledger-report'], text[-2500:]
    assert result.returncode in (0, 3), text[-2500:]
    assert ' --report07-dir data/compare2_report ' in text
    assert digest(base) == before                                              # 02, 04, 07 outputs: not touched, not moved
    page = (base / 'data/ledger_report/ledger_report.html').read_text(encoding='utf-8')
    assert 'id="report-data"' in page and '<h2>부록 근거 · 검산</h2>' in page
    record = json.loads((base / 'data/ledger_report/report.json').read_text(encoding='utf-8'))
    assert record['report07'] == 'linked'
    link = base / 'data/ledger_report' / '../compare2_report/report.html'
    assert link.resolve() == (base / 'data/compare2_report/report.html').resolve()
    assert '"href": "../compare2_report/report.html' in page or '"href":"../compare2_report/report.html' in page
    assert str(base) not in page                                               # no absolute path in the page


def test_ledger_maps_off_still_links_07_and_has_no_changes_cards(chain, tmp_path):  # noqa: F811
    python = chain.python
    base = tmp_path / 'work'
    shutil.copytree(chain.base, base)
    text, result = _run_flow(python, base, tmp_path, 'maps_off', STEPS=['08'], RUN_TAG='OFF', LEDGER_MAPS=False)
    assert [name for name, _ in _exit_codes(text)] == ['ledger', 'ledger-report'] and result.returncode in (0, 3), text[-2500:]
    assert ' start ledger-report --ledger-dir data/ledger --report07-dir data/compare2_report --out data/ledger_report' in text
    page = (base / 'data/ledger_report/ledger_report.html').read_text(encoding='utf-8')
    assert 'id="band-no-maps"' in page
    assert json.loads((base / 'data/ledger_report/report.json').read_text(encoding='utf-8'))['report07'] == 'linked'


def test_old_ledger_and_old_maps_are_refused_with_exit_2(tmp_path):
    old = sample_doc()
    del old['kinds']['line']['item_breakdown']
    code, out = run_report(tmp_path, old, name='old_ledger')
    assert code == 2 and json.loads((out / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_report_input_outdated'
    from test_ledger_report_maps import ledger_sha, write_maps
    from test_ledger_report import write_ledger
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), schema='ledger_maps_v1')
    out = tmp_path / 'out_v1'
    code = ledger_report.main_ledger_report(str(folder), str(out), lambda record: None, maps_dir=str(maps))
    assert code == 2 and json.loads((out / 'error.json').read_text(encoding='utf-8'))['code'] == 'ledger_maps_schema_mismatch'
