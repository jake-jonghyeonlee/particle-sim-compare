"""pexcl/T3: excluded pairs in the 07 input table, ledger.json inputs.pair_exclusions and ledger-report sections 1/3/7."""
import hashlib
import json
import re

import pytest

import test_compare2 as tc
import test_compare2_expected_zero as ez
from simcompare import ledger, ledger_report
from simcompare.cli import main

GHX = ('lateral_short', 'GH', 'L1', 'X', 'L1', {1: {1, 2}, 2: {1}, 3: {1}})      # registered GH-X (line): stays
GHY = ('lateral_short', 'GH', 'L1', 'Y', 'L1', {1: {2, 3}, 2: {2}, 3: {2}})      # unregistered GH-Y: left out
NNODE = ('lateral_short', 'N_NODE', 'L1', 'Z', 'L1', {1: {3}, 2: {3}})            # unregistered N_NODE-Z: left out
GH_CNO = {'lateralopen-GH-gcnt': tc.CNO_CSV}
REGISTERED = tc.PAIRS + [('GHX', 'GH', 'X', 'line')]


def base():
    return ez.pick('AB', 'DE', 'GH')


def sides(extra_ref, extra_tgt, pairs=None):
    pairs = REGISTERED if pairs is None else pairs
    return (base() + extra_ref, pairs), (base() + extra_tgt, pairs)


class World(object):
    pass


def build(tmp_path, monkeypatch, label, ref, tgt, **kwargs):
    code, out, doc = ez.run_pair_in(tmp_path, monkeypatch, label, ref, tgt, **kwargs)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    world = World()
    world.folder, world.out, world.c2 = tmp_path / label, out, doc
    return world


def report07(world, name='rep'):
    code = main(['compare2-report', '--compare2-dir', str(world.out), '--config', str(world.folder / 'compare2.yaml'),
                 '--out', str(world.folder / name)])
    assert code in (0, 3)
    return (world.folder / name / 'report.html').read_text(encoding='utf-8')


def run_ledger(world, name='ledger_out'):
    out = world.folder / name
    code = ledger.main_ledger(str(world.out), str(world.folder / 'compare2.yaml'), str(out), lambda record: None)
    assert code == 0, (out / 'error.json').read_text(encoding='utf-8') if (out / 'error.json').exists() else code
    return out, json.loads((out / 'ledger.json').read_text(encoding='utf-8'))


def run_ledger_report(ledger_out, name='lrep'):
    out = ledger_out.parent / name
    code = ledger_report.main_ledger_report(str(ledger_out), str(out), lambda record: None)
    assert code in (0, 3)
    return (out / 'ledger_report.html').read_text(encoding='utf-8')


def section(page, number):
    match = re.search(r'<section id="s%d">(.*?)</section>' % number, page, re.S)
    assert match, number
    return match.group(1)


def mixed(tmp_path, monkeypatch, label='mixed'):
    ref, tgt = sides([GHX, GHY, NNODE], [GHX, GHY, NNODE])
    return build(tmp_path, monkeypatch, label, ref, tgt, ref_cno=GH_CNO, tgt_cno=GH_CNO)


# ---- C1: mixed fixture --------------------------------------------------------------------------------------------------

def test_mixed_fixture_07_ledger_and_ledger_report(tmp_path, monkeypatch):
    world = mixed(tmp_path, monkeypatch)
    blocks = {role: world.c2['inputs']['manifests'][role]['pair_exclusions'] for role in ('reference', 'target')}
    assert blocks['reference']['by_reason'] == {'builtin:GH': 1, 'builtin:N_NODE': 1}
    # 07: the input table has the row, the counts per reason and the folded table
    page = report07(world)
    inputs = re.search(r'<table id="inputs">(.*?)</table></section>', page, re.S).group(1)
    assert '계산 제외 쌍' in inputs
    assert 'A: 제외 case 2개 (GH 1 · N_NODE 1)' in inputs and 'B: 제외 case 2개 (GH 1 · N_NODE 1)' in inputs
    assert '<summary>계산 제외 쌍 목록</summary>' in inputs
    assert '코드 규칙: GH 포함' in inputs and '코드 규칙: N_NODE 포함' in inputs and 'GH – Y' in inputs
    assert 'GH – X' not in inputs                        # the registered pair is computed, not left out
    # ledger.json copies the blocks as they are
    ledger_out, doc = run_ledger(world)
    assert doc['inputs']['pair_exclusions'] == blocks
    # ledger-report: 4 has GH-X (registered) and no GH-Y, 7 has one row per block pair, 3 has no "only excluded" mark
    html = run_ledger_report(ledger_out)
    s4, s7, s3, s1 = section(html, 4), section(html, 7), section(html, 3), section(html, 1)
    assert 'GH–X' in s4 and 'GH–Y' not in s4 and 'N_NODE' not in s4
    assert {tuple(row['electrodes']) for row in doc['pairs']} >= {('GH', 'X')} and not any(
        'Y' in row['electrodes'] or 'N_NODE' in row['electrodes'] for row in doc['pairs'])
    table = re.search(r'<table id="pair-excluded">(.*?)</table>', s7, re.S).group(1)
    assert table.count('<tr>') - 1 == sum(len(block['pairs']) for block in blocks.values()) == 4
    assert '기준 A' in table and '대상 B' in table and 'pairs.csv' not in table
    assert '계산에서 제외한 전극 쌍 2개 — 7절 참고' in s1
    assert '계산 제외 쌍만 있음' not in html            # GH keeps GH-X and its CNO
    assert 'GH' in {row['electrode'] for row in doc['electrodes']}


# ---- D6 mark: the electrode has only left-out pairs ---------------------------------------------------------------------

def test_electrode_with_only_left_out_pairs_is_marked_where_it_is_listed(tmp_path, monkeypatch):
    ref, tgt = sides([GHY], [GHY], pairs=tc.PAIRS)        # GH has no registered pair and no CNO
    world = build(tmp_path, monkeypatch, 'only', ref, tgt)
    ledger_out, doc = run_ledger(world)
    assert 'GH' not in {e for row in doc['pairs'] for e in row['electrodes']}
    html = run_ledger_report(ledger_out)
    s3 = section(html, 3)
    # observed names only: a left-out electrode has no ledger row of its own (no DL1 list here)
    assert 'GH' not in {row['electrode'] for row in doc['electrodes']}
    assert '계산 제외 쌍만 있음' not in s3
    assert 'GHY' not in section(html, 4)


def test_marked_when_the_dl1_list_keeps_the_electrode(tmp_path):
    import test_ledger as tl
    pairs = [tl.pair('P1', ['Data', 'ELVDD'], 'line', [tl.lateral(['Data', 'ELVDD'], .01, 'SD1')],
                     [tl.lateral(['Data', 'ELVDD'], .02, 'SD1')])]
    block = {'rule': 'r', 'excluded_cases': 1, 'excluded_expected_cases': 0, 'by_reason': {'builtin:GH': 1},
             'pairs': [{'electrodes': ['GH', 'Vint'], 'pair_id': None, 'reason': 'builtin:GH', 'cases': 1,
                        'analysis_layers': ['SD1']}],
             'cases': [{'case': 'c', 'electrodes': ['GH', 'Vint'], 'reason': 'builtin:GH', 'presence': 'discovered'}]}

    def mutate(doc):
        doc['inputs']['manifests'] = {'reference': {'pair_exclusions': block}, 'target': {}}
        doc['inputs']['manifests']['reference']['pair_exclusions']['rule'] = __import__(
            'simcompare.pair_exclusion', fromlist=['x']).PAIR_EXCLUSION_RULE

    world = tl.make_world(tmp_path, pairs, dl1={'reference': {'Data': ['SD1'], 'ELVDD': ['SD1'], 'GH': ['SD1']},
                                                'target': {'Data': ['SD1'], 'ELVDD': ['SD1'], 'GH': ['SD1']}},
                          mutate=mutate)
    assert tl.go(world) == 0
    doc = tl.result(world)
    assert [row['electrode'] for row in doc['electrodes']] == ['Data', 'ELVDD', 'GH']
    html = run_ledger_report(world.out)
    s3 = section(html, 3)
    assert 'GH (계산 제외 쌍만 있음)' in s3
    assert 'Data (계산 제외 쌍만 있음)' not in s3 and 'ELVDD (계산 제외 쌍만 있음)' not in s3
    assert s3.count('계산 제외 쌍만 있음') == 2          # GH is listed once per kind (line, pixel) in the "no pair" fold


# ---- ledger.json key and no-exclusion bytes ------------------------------------------------------------------------------

def test_without_exclusions_nothing_new_appears(tmp_path, monkeypatch):
    ref, tgt = sides([GHX], [GHX])
    world = build(tmp_path, monkeypatch, 'plain', ref, tgt)
    assert 'pair_exclusions' not in json.dumps(world.c2)
    ledger_out, doc = run_ledger(world)
    assert 'pair_exclusions' not in doc['inputs'] and 'pair_exclusions' not in (ledger_out / 'ledger.json').read_text(encoding='utf-8')
    page = report07(world)
    assert '계산 제외 쌍' not in page
    html = run_ledger_report(ledger_out)
    assert 'pair-excluded' not in html and '계산에서 제외한 전극 쌍' not in html and '계산 제외 쌍만 있음' not in html


BASELINE = 'd510dd952fbefeb5eb0082f358cf1da703dc6531'


def baseline_module(name):
    """The module `simcompare.<name>` as of the baseline commit (before pexcl), loaded under another name."""
    import importlib.util
    import subprocess
    import sys
    import types
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run(['git', '-C', str(root), 'show', '%s:src/simcompare/%s.py' % (BASELINE, name)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if done.returncode != 0:
        pytest.skip('baseline commit %s is not available in this clone' % BASELINE[:7])
    module = types.ModuleType('simcompare._baseline_' + name)
    module.__package__ = 'simcompare'
    module.__file__ = '<%s:%s>' % (BASELINE[:7], name)
    exec(compile(done.stdout.decode('utf-8'), module.__file__, 'exec'), module.__dict__)
    return module


def test_without_exclusions_07_ledger_and_ledger_report_bytes_equal_the_baseline_code(tmp_path, monkeypatch):
    """Whole-file sha256 of report.html, ledger.json and ledger_report.html against the baseline modules (the existing tests
    compare fields only). 07 and ledger-report are checked with and without a CNO row."""
    ref, tgt = sides([GHX], [GHX])
    world = build(tmp_path, monkeypatch, 'plain', ref, tgt, ref_cno=GH_CNO, tgt_cno=GH_CNO)
    assert 'pair_exclusions' not in json.dumps(world.c2)
    old_report, old_ledger, old_lrep = (baseline_module(n) for n in ('compare2_report', 'ledger', 'ledger_report'))
    from simcompare import compare2_report as new_report
    digests = {}
    for tag, rep, led, lrep in (('old', old_report, old_ledger, old_lrep), ('new', new_report, ledger, ledger_report)):
        out07 = world.folder / ('rep_' + tag)
        assert rep.main_compare2_report(str(world.out), str(world.folder / 'compare2.yaml'), str(out07),
                                        lambda record: None) in (0, 3)
        led_out = world.folder / ('led_' + tag)
        assert led.main_ledger(str(world.out), str(world.folder / 'compare2.yaml'), str(led_out), lambda record: None) == 0
        lrep_out = world.folder / ('lrep_' + tag)
        assert lrep.main_ledger_report(str(led_out), str(lrep_out), lambda record: None) in (0, 3)
        digests[tag] = {
            'report.html': hashlib.sha256((out07 / 'report.html').read_bytes()).hexdigest(),
            'ledger.json': hashlib.sha256((led_out / 'ledger.json').read_bytes()).hexdigest(),
            'ledger_report.html': hashlib.sha256((lrep_out / 'ledger_report.html').read_bytes()).hexdigest()}
    assert digests['old'] == digests['new']


def test_one_sided_block_shows_none_for_the_other_product(tmp_path, monkeypatch):
    ref, tgt = sides([GHY], [])
    world = build(tmp_path, monkeypatch, 'one', ref, tgt, inventory='own')
    page = report07(world)
    assert 'A: 제외 case 1개 (GH 1)' in page and 'B: 없음' in page
    ledger_out, doc = run_ledger(world)
    assert list(doc['inputs']['pair_exclusions']) == ['reference']
    table = re.search(r'<table id="pair-excluded">(.*?)</table>', run_ledger_report(ledger_out), re.S).group(1)
    assert table.count('<tr>') - 1 == 1 and '기준 A' in table and '대상 B' not in table


def test_table_exclusion_and_expected_only_counts(tmp_path, monkeypatch):
    ref, tgt = sides([GHY], [], pairs=REGISTERED)
    world = build(tmp_path, monkeypatch, 'union', ref, tgt, inventory='union')
    ledger_out, doc = run_ledger(world)
    html = run_ledger_report(ledger_out)
    table = re.search(r'<table id="pair-excluded">(.*?)</table>', section(html, 7), re.S).group(1)
    rows = re.findall(r'<tr><td class="l">(.*?)</tr>', table, re.S)
    assert len(rows) == 2
    assert '<td>1 / 0</td>' in rows[0] and '<td>0 / 1</td>' in rows[1]       # reference: found; target: expected only


def test_names_are_escaped(tmp_path):
    from simcompare.compare2_report import _pair_exclusion_html
    block = {'excluded_cases': 1, 'excluded_expected_cases': 0, 'by_reason': {'pairs_csv:excluded': 1},
             'pairs': [{'electrodes': ['A<b>', 'C&D'], 'pair_id': 'P"1', 'reason': 'pairs_csv:excluded', 'cases': 1,
                        'analysis_layers': ['L<1>']}], 'cases': []}
    text = _pair_exclusion_html({'inputs': {'manifests': {'reference': {'pair_exclusions': block}, 'target': {}}}})
    assert '<b>' not in text and 'A&lt;b&gt; – C&amp;D' in text and 'P&quot;1' in text and 'L&lt;1&gt;' in text
    assert 'pairs.csv: excluded' in text and 'pairs.csv 1' in text


# ---- B2 read checks -----------------------------------------------------------------------------------------------------

def test_electrode_asset_import_skips_a_left_out_case_and_keeps_the_others_unchanged(tmp_path):
    """03 (hotspot_case_electrode) converts dataset.cases only: a left-out GH case has no asset entry, and the entries of
    the kept cases equal those of an import without the GH case (so the 04 ROI of a kept case does not change)."""
    import shutil
    import test_electrode_png as tep
    from simcompare.adapters.electrode_png import import_electrode_assets
    from simcompare.adapters.real_hotspot import configured_dataset, import_hotspots, load_real_manifest

    results = {}
    for tag, with_gh in (('control', False), ('mixed', True)):
        base = tmp_path / tag
        base.mkdir()
        root = tep.real_source(base)
        if with_gh:
            shutil.copytree(root / 'images' / tep.CASE, root / 'images' / 'lateral_short-GH-L1-Y-L1')
        imported = base / 'imported'
        import_hotspots(root, imported)
        (imported / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
                                            'AB,A,B,line,pair-v1\n', encoding='utf-8')
        assignment = {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE',
                      'process_step_id': 'STEP', 'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT',
                      'pair_table_path': 'pairs.csv',
                      'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
        path = base / 'assignment.json'
        path.write_text(json.dumps(assignment), encoding='utf-8')
        out = base / 'out'
        record = import_electrode_assets('hotspot_case_electrode', root, out, numeric_data_dir=imported,
                                         condition_assignment=path, source_version='v', max_asset_memory_mb=256,
                                         cnt_from_dl1_root=tep.cnt_root(base))
        assert record['exit_code'] == 0
        dataset = configured_dataset(imported, load_real_manifest(imported), assignment)
        manifest = json.loads((out / 'review_assets.json').read_text(encoding='utf-8'))
        results[tag] = (manifest, dataset)
    mixed_manifest, mixed_dataset = results['mixed']
    control_manifest, control_dataset = results['control']
    assert [c['identity']['case_id'] for c in mixed_manifest['cases']] == [tep.CASE]
    assert mixed_manifest['cases'] == control_manifest['cases']
    assert 'pair_exclusions' in mixed_dataset.provenance and 'pair_exclusions' not in control_dataset.provenance
    assert mixed_manifest['source_manifest_hash'] == mixed_dataset.provenance['input_hash']      # 03 and 04 stay linked
    assert control_manifest['source_manifest_hash'] == control_dataset.provenance['input_hash']
    assert mixed_manifest['source_manifest_hash'] != control_manifest['source_manifest_hash']


def test_ledger_maps_never_draws_a_left_out_pair(tmp_path):
    import test_ledger_maps as tlm
    ref, tgt = sides([GHX, GHY, NNODE], [GHX, GHY, NNODE])
    tlm.chain(tmp_path, ref, tgt, ref_cno=GH_CNO, tgt_cno=GH_CNO)
    code, out = tlm.run_maps(tmp_path)
    assert code == 0, (out / 'error.json').read_text() if (out / 'error.json').exists() else code
    maps = tlm.load_maps(out)
    assert maps['status'] == 'ok' and maps['checks']['status'] == 'passed'
    ledger_pairs = {row['pair_id'] for row in json.loads((tmp_path / 'led' / 'ledger.json').read_text())['pairs']}
    for role in ('reference', 'target'):
        assert maps['checks']['item_sum'][role]['status'] == 'passed'
        assert all(row['status'] == 'passed' for row in maps['checks']['kind_sum'][role].values())
        drawn = set(tlm.items_by_pair(maps, role))
        assert 'GHX' in drawn                           # the registered GH pair is drawn
        assert drawn <= ledger_pairs                    # nothing beyond the ledger rows: no left-out pair
    assert 'N_NODE' not in json.dumps(maps)
