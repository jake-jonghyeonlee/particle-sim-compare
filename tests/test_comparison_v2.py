"""real_hotspot_comparison_v2: streaming writer, v2 -> v1 decoding, publish-on-success recovery."""
import errno
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

import reference_comparison_v1 as reference
from simcompare import linepixel, pipeline
from simcompare.pipeline import load_numeric_config, run_numeric_compare

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'make_compare_fixture.py'


def _fixture_module():
    spec = importlib.util.spec_from_file_location('make_compare_fixture', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def summary_config(tmp_path_factory):
    out = tmp_path_factory.mktemp('cmpfx')
    return _fixture_module().build(out, cases=6, size=24, seed=3, shared=2)


@pytest.fixture(scope='module')
def fixture_config(summary_config):
    """Same data, config opted in to full bucket lists (the T2 layout)."""
    cfg = yaml.safe_load(Path(summary_config).read_text())
    cfg['compare'] = {'full_bucket_detail': True}
    full = Path(summary_config).parent/'config_full.yaml'
    full.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return full


def decode_v2_to_v1(payload):
    """Test-only inverse of the v2 layout: re-inflate source_cases[*].raw from the side's cases_raw."""
    assert payload['schema_version'] == 'real_hotspot_comparison_v2'
    if payload.get('bucket_detail', 'full') != 'full':
        raise ValueError('summary comparison files carry only top buckets; they cannot be decoded to v1')
    decoded = {**payload, 'schema_version': 'real_hotspot_comparison_v1'}
    for name in ('reference', 'target'):
        side = payload[name]
        if side is None:
            continue
        side = {k: v for k, v in side.items() if k != 'cases_raw'}
        groups = []
        for group in side['groups']:
            sources = []
            for source in group['source_cases']:
                source = dict(source)
                source['raw'] = payload[name]['cases_raw'][source.pop('raw_ref')]
                sources.append(source)
            groups.append({**group, 'source_cases': sources})
        decoded[name] = {**side, 'groups': groups}
    return decoded


def _run(config_path, out):
    cfg = load_numeric_config(config_path)
    return run_numeric_compare(cfg, out=out)


def _no_temps(out):
    return not [p for p in Path(out).iterdir() if p.name.endswith('.tmp')]


def test_v2_decodes_to_reference_v1_payload_and_csv(fixture_config, tmp_path):
    result = _run(fixture_config, tmp_path/'v2')
    assert result['exit_code'] == 3 and _no_temps(tmp_path/'v2')
    assert [Path(p).name for p in result['outputs']] == ['comparison.json', 'comparison.csv']
    text = (tmp_path/'v2/comparison.json').read_text(encoding='utf-8')
    assert '\n' not in text and ': ' not in text[:200]  # compact separators, no indent
    v2 = json.loads(text)
    expected, rows = reference.build_v1(load_numeric_config(fixture_config))
    assert v2['schema_version'] == 'real_hotspot_comparison_v2'
    assert decode_v2_to_v1(v2) == expected
    # comparison.csv rows are unchanged: byte-equal to the v1 writer's csv.
    pipeline._write_inspection(tmp_path/'v1', 'comparison', {}, rows)
    assert (tmp_path/'v1/comparison.csv').read_bytes() == (tmp_path/'v2/comparison.csv').read_bytes()


def test_v2_layout_raw_once_per_case_and_lineage_unchanged(fixture_config, tmp_path):
    _run(fixture_config, tmp_path/'o')
    v2 = json.loads((tmp_path/'o/comparison.json').read_text(encoding='utf-8'))
    for name in ('reference', 'target'):
        side = v2[name]
        assert len(side['cases_raw']) == 6
        refs = set()
        for group in side['groups']:
            for source in group['source_cases']:
                assert 'raw' not in source
                refs.add(source['raw_ref'])
                assert source['raw_ref'] == source['identity']['case_id']
            for cls in ('line', 'pixel'):
                for bucket in group[cls]['exclusive'] + group[cls]['shared']:
                    assert isinstance(bucket['pair_lineage_case_keys'][0], str)  # no integer-index encoding
        assert refs == set(side['cases_raw'])
    assert any(len(b['pair_ids']) > 1 for g in v2['reference']['groups'] for b in g['line']['shared'] + g['pixel']['shared'])


def test_unconfigured_real_branch_stays_v1(fixture_config, tmp_path):
    cfg = yaml.safe_load(Path(fixture_config).read_text())
    for side in ('reference', 'target'):
        del cfg[side]['condition_assignment']
    unconfigured = Path(fixture_config).parent/'unconfigured.yaml'
    unconfigured.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert _run(unconfigured, tmp_path/'o')['exit_code'] == 3
    payload = json.loads((tmp_path/'o/comparison.json').read_text(encoding='utf-8'))
    assert payload['schema_version'] == 'real_hotspot_comparison_v1'
    assert payload['classified_comparison_status'] == 'unavailable'


def _seed_existing(out):
    out.mkdir(parents=True)
    (out/'comparison.json').write_text('{"previous":"report"}', encoding='utf-8')
    (out/'comparison.csv').write_text('previous\n', encoding='utf-8')


def _assert_kept(out):
    assert (out/'comparison.json').read_text() == '{"previous":"report"}'
    assert (out/'comparison.csv').read_text() == 'previous\n'
    assert _no_temps(out)


def _fail_on_second_compute(monkeypatch, exc):
    real = linepixel.compute_areas
    calls = []

    def compute(dataset, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise exc
        return real(dataset, *args, **kwargs)
    monkeypatch.setattr(linepixel, 'compute_areas', compute)
    return calls


@pytest.mark.parametrize('exc', [linepixel.NumericContractError('oversized_real_operation', 'target'),
                                 RuntimeError('target boom'), KeyboardInterrupt()])
def test_target_failure_or_interrupt_keeps_existing_report(fixture_config, tmp_path, monkeypatch, exc):
    _seed_existing(tmp_path/'o')
    calls = _fail_on_second_compute(monkeypatch, exc)
    with pytest.raises(type(exc)):
        _run(fixture_config, tmp_path/'o')
    assert len(calls) == 2
    _assert_kept(tmp_path/'o')


def test_target_failure_without_existing_report_publishes_nothing(fixture_config, tmp_path, monkeypatch):
    _fail_on_second_compute(monkeypatch, RuntimeError('target boom'))
    with pytest.raises(RuntimeError):
        _run(fixture_config, tmp_path/'o')
    assert list((tmp_path/'o').iterdir()) == []


@pytest.mark.parametrize('after', [3, 40, 400])
def test_enospc_mid_write_keeps_existing_report(fixture_config, tmp_path, monkeypatch, after):
    _seed_existing(tmp_path/'o')
    real = pipeline._dumps
    calls = []

    def dumps(value):
        calls.append(1)
        if len(calls) > after:
            raise OSError(errno.ENOSPC, 'No space left on device')
        return real(value)
    monkeypatch.setattr(pipeline, '_dumps', dumps)
    with pytest.raises(OSError) as info:
        _run(fixture_config, tmp_path/'o')
    assert info.value.errno == errno.ENOSPC
    _assert_kept(tmp_path/'o')


def test_enospc_on_flush_to_disk_keeps_existing_report(fixture_config, tmp_path, monkeypatch):
    _seed_existing(tmp_path/'o')

    def fsync(fd):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(pipeline.os, 'fsync', fsync)
    with pytest.raises(OSError):
        _run(fixture_config, tmp_path/'o')
    _assert_kept(tmp_path/'o')


def test_csv_failure_keeps_existing_json_and_csv(fixture_config, tmp_path, monkeypatch):
    _seed_existing(tmp_path/'o')

    def broken(path, rows):
        path.write_text('partial')
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(pipeline, '_write_csv', broken)
    with pytest.raises(OSError):
        _run(fixture_config, tmp_path/'o')
    _assert_kept(tmp_path/'o')


def test_interrupt_mid_write_keeps_existing_report(fixture_config, tmp_path, monkeypatch):
    _seed_existing(tmp_path/'o')
    real = pipeline._dumps
    calls = []

    def dumps(value):
        calls.append(1)
        if len(calls) > 25:
            raise KeyboardInterrupt()
        return real(value)
    monkeypatch.setattr(pipeline, '_dumps', dumps)
    with pytest.raises(KeyboardInterrupt):
        _run(fixture_config, tmp_path/'o')
    _assert_kept(tmp_path/'o')


def test_success_replaces_existing_report(fixture_config, tmp_path):
    _seed_existing(tmp_path/'o')
    assert _run(fixture_config, tmp_path/'o')['exit_code'] == 3
    assert json.loads((tmp_path/'o/comparison.json').read_text())['schema_version'] == 'real_hotspot_comparison_v2'
    assert (tmp_path/'o/comparison.csv').read_text(encoding='utf-8-sig').startswith('side,')
    assert _no_temps(tmp_path/'o')


# ---- R4: the json/csv pair is published all-or-nothing --------------------------------------------
def _replace_failing(monkeypatch, destination, exc, *, then_all=False):
    """os.replace that raises when a temp file is published over `destination` (not for rollback restores)."""
    real = pipeline.os.replace
    state = {'hit': False}

    def replace(src, dst, *args, **kwargs):
        name = Path(str(dst)).name
        publishing = Path(str(src)).name.endswith('.tmp')
        if (publishing and name == destination) or (then_all and state['hit']):
            state['hit'] = True
            raise exc
        return real(src, dst, *args, **kwargs)
    monkeypatch.setattr(pipeline.os, 'replace', replace)
    return state


@pytest.mark.parametrize('destination', ['comparison.json', 'comparison.csv'])
@pytest.mark.parametrize('exc', [OSError(errno.EIO, 'replace failed'), KeyboardInterrupt()])
def test_failure_or_interrupt_at_each_replacement_keeps_previous_pair(fixture_config, tmp_path, monkeypatch,
                                                                      destination, exc):
    _seed_existing(tmp_path/'o')
    state = _replace_failing(monkeypatch, destination, exc)
    with pytest.raises(type(exc)):
        _run(fixture_config, tmp_path/'o')
    assert state['hit']
    _assert_kept(tmp_path/'o')
    assert sorted(p.name for p in (tmp_path/'o').iterdir()) == ['comparison.csv', 'comparison.json']


@pytest.mark.parametrize('destination', ['comparison.json', 'comparison.csv'])
def test_failure_at_replacement_without_previous_pair_publishes_nothing(fixture_config, tmp_path, monkeypatch,
                                                                         destination):
    _replace_failing(monkeypatch, destination, OSError(errno.EIO, 'replace failed'))
    with pytest.raises(OSError):
        _run(fixture_config, tmp_path/'o')
    assert list((tmp_path/'o').iterdir()) == []


def test_killed_between_replacements_is_rolled_back_by_the_next_run(fixture_config, tmp_path, monkeypatch):
    """The process dies right after the json replacement and cannot even roll back (every later replace fails)."""
    _seed_existing(tmp_path/'o')
    _replace_failing(monkeypatch, 'comparison.csv', KeyboardInterrupt(), then_all=True)
    with pytest.raises(KeyboardInterrupt):
        _run(fixture_config, tmp_path/'o')
    monkeypatch.undo()
    assert json.loads((tmp_path/'o/comparison.json').read_text())['schema_version'] == 'real_hotspot_comparison_v2'
    assert (tmp_path/'o/comparison.csv').read_text() == 'previous\n'            # the torn NEW_JSON + OLD_CSV state
    stream = pipeline._ComparisonStream(tmp_path/'o')
    stream.begin()                                                               # next run recovers the old pair
    stream.abort()
    _assert_kept(tmp_path/'o')
    assert sorted(p.name for p in (tmp_path/'o').iterdir()) == ['comparison.csv', 'comparison.json']


# ---- B1: the commit decision is durable before any rollback material is deleted ----------------------
# Every filesystem step of the publication (journal write, backup link/copy, both replacements, the commit
# marker, each deletion of the rollback material) is made to fail, once as an ordinary error / Ctrl-C and once
# as a process death (that step and every later one fails), at every position, followed by the restart recovery
# of the next begin(), which is itself killed at every position. Whatever happens, the pair on disk is
# (OLD_JSON, OLD_CSV) or (NEW_JSON, NEW_CSV) once recovery has run.
class _Died(BaseException):
    """Simulated process death: the faulting step and every later filesystem step fail."""


def _publish_pair(out):
    stream = pipeline._ComparisonStream(out)
    stream.begin()
    try:
        stream.side('reference', None)
        stream.commit({'classified_comparison_status': 'done'}, [{'a': 1}])
    except BaseException:
        stream.abort()
        raise
    stream.abort()


def _recover_only(out):
    stream = pipeline._ComparisonStream(out)
    stream.begin()
    stream.abort()


def _pair_state(out):
    """'old' | 'new' | 'empty' | 'torn' for the comparison.json / comparison.csv pair."""
    js, cs = out/'comparison.json', out/'comparison.csv'
    if not js.exists() and not cs.exists():
        return 'empty'
    if not js.exists() or not cs.exists():
        return 'torn'
    old_json = js.read_text(encoding='utf-8') == '{"previous":"report"}'
    old_csv = cs.read_text(encoding='utf-8') == 'previous\n'
    new_json = 'real_hotspot_comparison_v2' in js.read_text(encoding='utf-8')
    new_csv = cs.read_bytes().startswith(b'\xef\xbb\xbfa')
    if old_json and old_csv:
        return 'old'
    if new_json and new_csv:
        return 'new'
    return 'torn'


def _fault_injection(monkeypatch, at, exc, dead, link):
    state = {'n': 0, 'dead': False}

    def wrap(owner, attr, *, static=False, error=None):
        real = getattr(owner, attr)

        def call(*args, **kwargs):
            if error is not None:
                raise error
            if state['dead']:
                raise exc
            index = state['n']
            state['n'] += 1
            if index == at:
                state['dead'] = dead
                raise exc
            return real(*args, **kwargs)
        monkeypatch.setattr(owner, attr, staticmethod(call) if static else call)
    wrap(pipeline.os, 'replace')
    wrap(pipeline._ComparisonStream, '_create', static=True)
    wrap(pipeline._ComparisonStream, '_unlink', static=True)
    wrap(pipeline.shutil, 'copyfile')
    if link:
        wrap(pipeline.os, 'link')
    else:                                       # no hard links: the copy fallback is the backup
        monkeypatch.setattr(pipeline.os, 'link', lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EPERM, 'no link')))
    return state


def _count_steps(tmp_path, previous, link):
    from _pytest.monkeypatch import MonkeyPatch
    out = tmp_path/'count'
    if previous:
        _seed_existing(out)
    with MonkeyPatch.context() as mp:
        state = _fault_injection(mp, -1, OSError(errno.EIO, 'x'), False, link)
        _publish_pair(out)
    assert _pair_state(out) == 'new' and sorted(p.name for p in out.iterdir()) == ['comparison.csv', 'comparison.json']
    return state['n']


def _leftovers(out):
    return sorted(p.name for p in out.iterdir() if p.name not in ('comparison.json', 'comparison.csv'))


@pytest.mark.parametrize('link', [True, False], ids=['hardlink', 'copy_fallback'])
@pytest.mark.parametrize('previous', [True, False], ids=['has_previous', 'first_publication'])
@pytest.mark.parametrize('kind', ['oserror', 'interrupt', 'died'])
def test_every_publication_step_leaves_a_consistent_pair(tmp_path, kind, previous, link):
    from _pytest.monkeypatch import MonkeyPatch
    steps = _count_steps(tmp_path, previous, link)
    assert steps >= 8                                        # journal, backups, 2 replaces, marker x2, cleanup
    exc = {'oserror': OSError(errno.EIO, 'step failed'), 'interrupt': KeyboardInterrupt(), 'died': _Died()}[kind]
    dead = kind == 'died'
    committed_seen = []
    for at in range(steps):
        out = tmp_path/f'run{at}'
        if previous:
            _seed_existing(out)
        with MonkeyPatch.context() as mp:
            _fault_injection(mp, at, exc, dead, link)
            try:
                _publish_pair(out)
                raised = False
            except BaseException as caught:
                assert caught is exc
                raised = True
        committed = (out/'.comparison.committed').exists()
        if not dead:
            # an ordinary error / Ctrl-C was handled in-process: the pair is already whole
            assert _pair_state(out) in (('old' if previous else 'empty'), 'new'), (at, _leftovers(out))
        committed = committed or not raised                  # published and cleaned up in-process (a swallowed error)
        if raised:
            committed_seen.append(committed)
        expected = 'new' if committed else ('old' if previous else 'empty')
        # restart recovery, killed at every one of its own steps, then once more without faults
        recovery_steps = None
        for kill in range(0, 40):
            copy = tmp_path/f'run{at}_k{kill}'
            shutil.copytree(str(out), str(copy))
            with MonkeyPatch.context() as mp:
                state = _fault_injection(mp, kill, _Died(), True, link)
                try:
                    _recover_only(copy)
                except _Died:
                    pass
            fired = state['n'] > kill
            _recover_only(copy)
            assert _pair_state(copy) == expected, (at, kill, _leftovers(copy))
            assert _leftovers(copy) == [], (at, kill, _leftovers(copy))
            if not fired:
                recovery_steps = kill
                break
        assert recovery_steps is not None
        _recover_only(out)
        assert _pair_state(out) == expected and _leftovers(out) == []
        _publish_pair(out)                                   # the directory is not wedged
        assert _pair_state(out) == 'new' and _leftovers(out) == []
    # the decision point: faults before the marker rename never publish, faults after it never roll back
    assert committed_seen == sorted(committed_seen) and not committed_seen[0]
    assert kind != 'died' or committed_seen[-1]


def test_cleanup_failure_after_commit_never_rolls_back(tmp_path, monkeypatch):
    """B1 reproduction: interruption after the first backup deletion used to leave NEW_JSON + OLD_CSV."""
    out = tmp_path/'o'
    _seed_existing(out)
    real = pipeline._ComparisonStream._unlink
    calls = []

    def unlink(path):
        calls.append(path.name)
        if path.name == '.comparison.csv.prev':               # after .comparison.json.prev, before .comparison.csv.prev
            raise KeyboardInterrupt()
        return real(path)
    monkeypatch.setattr(pipeline._ComparisonStream, '_unlink', staticmethod(unlink))
    with pytest.raises(KeyboardInterrupt):
        _publish_pair(out)
    monkeypatch.undo()
    assert '.comparison.json.prev' in calls and calls[-1] == '.comparison.csv.prev'
    assert _pair_state(out) == 'new'                          # committed: the new pair stays
    assert (out/'.comparison.committed').exists()
    _recover_only(out)                                        # the next run rolls forward and cleans up
    assert _pair_state(out) == 'new' and _leftovers(out) == []


def test_stale_marker_that_cannot_be_removed_blocks_publication(tmp_path, monkeypatch):
    out = tmp_path/'o'
    _seed_existing(out)
    (out/'.comparison.committed').write_text('committed')
    monkeypatch.setattr(pipeline._ComparisonStream, '_unlink',
                        staticmethod(lambda path: (_ for _ in ()).throw(OSError(errno.EACCES, 'denied'))
                                     if path.name == '.comparison.committed' else
                                     (path.unlink() if path.exists() else None)))
    with pytest.raises(OSError):
        _publish_pair(out)                                    # begin() cannot clear it: refuse instead of mixing generations
    assert _pair_state(out) == 'old'


# ---- RA3 B1: recovery fails closed -------------------------------------------------------------------
# An unreadable journal, marker or backup lookup during recovery must never be read as "nothing was replaced":
# the rollback material stays exactly as found, the error says what to do, and the next fault-free recovery repairs.
_JOURNAL_BOTH = json.dumps({'comparison.json': True, 'comparison.csv': True})


def _recover_direct(out):
    pipeline._ComparisonStream(out)._recover()


def _snapshot(out):
    return {p.name: p.read_bytes() for p in sorted(out.iterdir())}


def _torn_state(out, committed=False):
    """Process died mid-publication: NEW_JSON + OLD_CSV (or NEW + NEW with the marker when committed)."""
    _seed_existing(out)
    (out/'.comparison.publishing').write_text(_JOURNAL_BOTH, encoding='utf-8')
    for name in ('comparison.json', 'comparison.csv'):
        shutil.copyfile(str(out/name), str(out/f'.{name}.prev'))
    (out/'comparison.json').write_text('{"schema_version":"real_hotspot_comparison_v2"}', encoding='utf-8')
    if committed:
        (out/'comparison.csv').write_bytes(b'\xef\xbb\xbfa\n')
        (out/'.comparison.committed').write_text('committed', encoding='utf-8')
    assert _pair_state(out) == ('new' if committed else 'torn')


def _read_faults(monkeypatch, at, exc):
    state = {'n': 0}
    for attr in ('_present', '_read_text'):
        real = getattr(pipeline._ComparisonStream, attr)

        def call(path, _real=real):
            index = state['n']
            state['n'] += 1
            if index == at:
                raise exc
            return _real(path)
        monkeypatch.setattr(pipeline._ComparisonStream, attr, staticmethod(call))
    return state


@pytest.mark.parametrize('committed', [False, True], ids=['rollback', 'roll_forward'])
@pytest.mark.parametrize('errno_', [errno.EIO, errno.EACCES])
def test_recovery_read_fault_fails_closed_then_second_recovery_repairs(tmp_path, monkeypatch, committed, errno_):
    from _pytest.monkeypatch import MonkeyPatch
    from simcompare.numeric_schema import NumericContractError
    counting = tmp_path/'count'
    _torn_state(counting, committed)
    with MonkeyPatch.context() as mp:
        state = _read_faults(mp, -1, None)
        _recover_direct(counting)
    reads = state['n']
    assert reads >= (1 if committed else 4)                  # marker check; rollback adds journal + 2 backup lookups
    for at in range(reads):
        out = tmp_path/f'r{at}'
        _torn_state(out, committed)
        before = _snapshot(out)
        with MonkeyPatch.context() as mp:
            _read_faults(mp, at, OSError(errno_, 'injected'))
            with pytest.raises(NumericContractError) as caught:
                _recover_direct(out)
        assert caught.value.code == 'comparison_recovery_unreadable'
        assert str(out) in str(caught.value) and 'by hand' in str(caught.value)
        assert _snapshot(out) == before, at                  # not one backup, journal or marker was touched
        _recover_only(out)                                   # second, fault-free recovery
        assert _pair_state(out) == ('new' if committed else 'old') and _leftovers(out) == [], at


@pytest.mark.parametrize('content', ['', '{"comparison.json": tr', '[]', '{"comparison.json": 1, "comparison.csv": true}',
                                     '{"comparison.json": true}', b'\xff\xfe\x00bad'],
                         ids=['empty', 'truncated', 'not_object', 'not_bool', 'missing_key', 'not_utf8'])
def test_corrupt_journal_is_never_discarded(tmp_path, content):
    from simcompare.numeric_schema import NumericContractError
    out = tmp_path/'o'
    _torn_state(out)
    journal = out/'.comparison.publishing'
    journal.write_bytes(content if isinstance(content, bytes) else content.encode())
    before = _snapshot(out)
    with pytest.raises(NumericContractError) as caught:
        _recover_only(out)
    assert caught.value.code == 'comparison_recovery_unreadable'
    assert _snapshot(out) == before
    with pytest.raises(NumericContractError):                # still blocked on the next run, nothing is lost
        _publish_pair(out)
    assert _snapshot(out) == before
    journal.write_text(_JOURNAL_BOTH, encoding='utf-8')      # the operator repairs the journal by hand
    _recover_only(out)
    assert _pair_state(out) == 'old' and _leftovers(out) == []


def test_marker_stat_error_is_not_read_as_not_committed(tmp_path, monkeypatch):
    """lstat failing with EACCES must raise, not look like a missing marker (Path.exists() says False on 3.12+)."""
    from simcompare.numeric_schema import NumericContractError
    out = tmp_path/'o'
    _torn_state(out, committed=True)
    before = _snapshot(out)
    real = pipeline.os.lstat
    monkeypatch.setattr(pipeline.os, 'lstat', lambda p, *a, **k: (_ for _ in ()).throw(OSError(errno.EACCES, 'denied'))
                        if Path(p).name == '.comparison.committed' else real(p, *a, **k))
    with pytest.raises(NumericContractError):
        _recover_only(out)
    monkeypatch.undo()
    assert _snapshot(out) == before
    _recover_only(out)
    assert _pair_state(out) == 'new' and _leftovers(out) == []


def test_in_process_rollback_with_unreadable_journal_keeps_the_material(tmp_path, monkeypatch):
    """A failure at the csv replacement triggers the in-process rollback; its journal read fails: stay as found."""
    from simcompare.numeric_schema import NumericContractError
    out = tmp_path/'o'
    _seed_existing(out)
    real_replace, real_read = pipeline.os.replace, pipeline._ComparisonStream._read_text
    failed = []

    def replace(src, dst):
        if Path(dst).name == 'comparison.csv':
            failed.append(True)
            raise OSError(errno.EIO, 'csv replace failed')
        return real_replace(src, dst)

    def read_text(path):                                      # recovery during begin() is fault-free
        if failed:
            raise OSError(errno.EIO, 'journal unreadable')
        return real_read(path)
    monkeypatch.setattr(pipeline.os, 'replace', replace)
    monkeypatch.setattr(pipeline._ComparisonStream, '_read_text', staticmethod(read_text))
    with pytest.raises(NumericContractError):
        _publish_pair(out)
    monkeypatch.undo()
    assert _pair_state(out) == 'torn'                         # NEW_JSON + OLD_CSV, but with its way back intact
    assert (out/'.comparison.publishing').exists()
    assert (out/'.comparison.json.prev').exists() and (out/'.comparison.csv.prev').exists()
    _recover_only(out)
    assert _pair_state(out) == 'old' and _leftovers(out) == []
