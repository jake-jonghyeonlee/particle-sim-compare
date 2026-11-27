"""pasx/T8: `maps_src_v1` recording and replay (format, failure isolation, side.json bytes, replay conditions)."""
import errno
import hashlib
import json
import zlib
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from simcompare import compare2_side as cs, maps_source as ms
from simcompare.linepixel import GroupKey
from simcompare.numeric_schema import NumericContractError

CONDITIONS = {'maps_basis': ms.MAPS_BASIS, 'report_diameter_um': '3', 'lambda_diameters_um': ['1', '3'],
              'top_buckets': 20}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def group_key(diameter):
    return GroupKey('P', 'v', 'u', 's', 'step', 'exp', 'L1', 'L1', 'c1', 'frame', diameter)


class Frame(object):
    def __init__(self, shape, valid=True):
        self.frame_id = 'f'
        self.shape_yx = shape
        if valid:
            self.valid = np.ones(shape, dtype=bool)
            self.valid[0, 0] = False


def call(recorder, diameter, frame, geom, share_idx, share, number=0, kind='line'):
    item = {'pair_id': 'p%d' % number, 'failure_type': 'short', 'upper_layer': 'L1', 'lower_layer': 'L2',
            'endpoints': [{'electrode_id': 'A', 'actual_layer': 'L1'}]}
    recorder.sink(group_key=group_key(diameter), kind=kind, item_key=('p%d' % number, 'short', (('A', 'L1'),)),
                  item=item, frame=frame, geom_idx=np.asarray(geom, dtype=np.int64),
                  share_idx=np.asarray(share_idx, dtype=np.int64), share=np.asarray(share, dtype=np.float64))


def record_small(folder, **kwargs):
    recorder = ms.MapsRecorder(folder, CONDITIONS, **kwargs)
    assert recorder.open()
    frame, other = Frame((3, 4)), Frame((3, 4), valid=False)
    call(recorder, Decimal(3), frame, [1, 2, 5], [1, 2], [0.5, 0.25], 0)           # report diameter: geom + share stored
    call(recorder, Decimal(1), frame, [4, 5], [4], [1.0], 1, 'pixel')              # lambda only: share stored, geom length
    call(recorder, Decimal(2), other, [7, 8], [8], [0.125], 2)                     # neither: lengths only
    call(recorder, Decimal(3), other, [], [], [], 3)                               # an empty call is kept
    return recorder


def finish(recorder):
    return recorder.close(lambda: {'estimate_bytes': 123, 'input_hash': 'h', 'side_sha256': 's'})


def brief(summary):
    """status and reason of a summary (a failed one also carries calls and detail, R8:2)."""
    return {key: summary[key] for key in ('status', 'reason')}


def test_index_and_lines_describe_the_recording(tmp_path):
    folder = tmp_path / 'maps_src'
    recorder = record_small(folder)
    summary = finish(recorder)
    index = json.loads((folder / 'index.json').read_text())
    assert summary['status'] == 'complete' and summary['calls'] == 4 == index['calls']
    assert index['schema_version'] == 'maps_src_v2' and index['conditions'] == CONDITIONS
    assert index['estimate_bytes'] == 123 and index['input_hash'] == 'h' and index['frames'] == 2
    assert index['files'] == {name: (folder / name).stat().st_size for name in ms.DATA_FILES}
    assert index['stored_bytes'] == (folder / 'calls.bin').stat().st_size == summary['stored_bytes']
    lines = [json.loads(text) for text in (folder / 'calls.jsonl').read_text().splitlines()]
    assert [line['seq'] for line in lines] == [0, 1, 2, 3]
    assert 'share' in lines[0] and 'geom_idx' in lines[0]
    assert 'share' in lines[1] and 'geom_idx' not in lines[1] and lines[1]['geom_len'] == 2
    assert 'share' not in lines[2] and 'geom_idx' not in lines[2] and lines[2]['share_len'] == 1
    assert lines[3]['geom_len'] == 0 and lines[3]['share_len'] == 0 and 'geom_idx' in lines[3]
    entry = lines[0]['share_idx']
    assert set(entry) == {'offset', 'nbytes_stored', 'dtype', 'length', 'crc32'}
    assert entry['dtype'] == '<i8' and entry['length'] == 2
    raw = (folder / 'calls.bin').read_bytes()[entry['offset']:entry['offset'] + entry['nbytes_stored']]
    assert zlib.crc32(zlib.decompress(raw)) == entry['crc32']
    frames = [json.loads(text) for text in (folder / 'frames.jsonl').read_text().splitlines()]
    assert [f['has_valid'] for f in frames] == [True, False] and frames[0]['shape_yx'] == [3, 4]
    assert len(frames[0]['sha256']) == 64 and frames[1]['valid'] is None
    assert sum(1 for line in lines if line['frame_ref'] == 0) == 2
    # v2: group keys and items are tables, a call line only refers to them (first-seen order, shared where equal)
    groups = [json.loads(text) for text in (folder / 'groups.jsonl').read_text().splitlines()]
    items = [json.loads(text) for text in (folder / 'items.jsonl').read_text().splitlines()]
    assert [g['g'] for g in groups] == [0, 1, 2] and [i['i'] for i in items] == [0, 1, 2, 3]   # diameters 3, 1, 2 (3 again)
    assert [line['g'] for line in lines] == [0, 1, 2, 0] and [line['i'] for line in lines] == [0, 1, 2, 3]
    assert all('group_key' not in line and 'item' not in line and 'item_key' not in line for line in lines)
    assert groups[0]['group_key']['diameter_um'] != groups[1]['group_key']['diameter_um']
    assert '"pair_id","p0"' in json.dumps(items[0], separators=(',', ':'))
    assert index['groups'] == 3 and index['items'] == 4
    assert index['sha256'] == {name: sha(folder / name) for name in ms.LINE_FILES}
    assert index['line_bytes'] == sum((folder / name).stat().st_size for name in ms.LINE_FILES) == summary['line_bytes']


def test_equal_calls_share_one_group_and_one_item(tmp_path):
    folder = tmp_path / 'maps_src'
    recorder = ms.MapsRecorder(folder, CONDITIONS)
    assert recorder.open()
    frame = Frame((3, 4))
    for _ in range(3):
        call(recorder, Decimal(3), frame, [1], [1], [0.5], 7)
    call(recorder, Decimal(3), frame, [1], [1], [0.5], 8)
    assert finish(recorder)['status'] == 'complete'
    assert len((folder / 'groups.jsonl').read_text().splitlines()) == 1
    assert len((folder / 'items.jsonl').read_text().splitlines()) == 2
    assert [c['item']['pair_id'] for c in collect(source_of(folder))] == ['p7', 'p7', 'p7', 'p8']


def collect(source):
    calls = []
    source.replay(lambda **kwargs: calls.append(kwargs))
    return calls


def source_of(folder):
    return ms.MapsSource(folder.parent, json.loads((folder / 'index.json').read_text()))


def test_replay_gives_the_recorded_arrays_lengths_and_frames(tmp_path):
    folder = tmp_path / 'maps_src'
    finish(record_small(folder))
    calls = collect(source_of(folder))
    assert len(calls) == 4
    first = calls[0]
    assert first['group_key'] == group_key(Decimal(3)) and first['kind'] == 'line'
    assert first['item_key'] == ('p0', 'short', (('A', 'L1'),))
    assert first['item']['endpoints'] == [{'electrode_id': 'A', 'actual_layer': 'L1'}]
    assert first['geom_idx'].tolist() == [1, 2, 5] and first['geom_idx'].dtype == np.int64
    assert first['share_idx'].tolist() == [1, 2] and first['share'].tolist() == [0.5, 0.25]
    assert first['geom_idx'].flags.writeable
    assert first['frame'].shape_yx == (3, 4) and not first['frame'].valid[0, 0] and first['frame'].valid.sum() == 11
    second = calls[1]
    assert len(second['geom_idx']) == 2 and isinstance(second['geom_idx'], ms.LengthOnly)
    assert second['share'].tolist() == [1.0] and second['kind'] == 'pixel'
    third = calls[2]
    assert [len(third[k]) for k in ('geom_idx', 'share_idx', 'share')] == [2, 1, 1]
    assert not hasattr(third['frame'], 'valid') and third['frame'].shape_yx == (3, 4)
    assert len(calls[3]['geom_idx']) == 0 and calls[3]['geom_idx'].dtype == np.int64


def test_length_only_objects_fail_on_any_other_access():
    stub = ms.LengthOnly(5)
    assert len(stub) == 5 and bool(stub)
    for access in (lambda: stub.nbytes, lambda: stub[0], lambda: list(stub), lambda: np.asarray(stub),
                   lambda: np.flatnonzero(stub), lambda: stub.astype(np.int32), lambda: stub.reshape(-1)):
        with pytest.raises(ms.StubAccessError):
            access()


def test_big_arrays_are_streamed_in_pieces_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, 'PIECE_BYTES', 64)
    monkeypatch.setattr(ms, 'READ_BLOCK', 32)
    folder = tmp_path / 'maps_src'
    recorder = ms.MapsRecorder(folder, CONDITIONS)
    assert recorder.open()
    rng = np.random.RandomState(1)
    geom = np.sort(rng.choice(10 ** 6, 1000, replace=False)).astype(np.int64)
    share = rng.rand(1000)
    call(recorder, Decimal(3), Frame((50, 50)), geom, geom[:700], share[:700], 0)
    assert finish(recorder)['status'] == 'complete'
    got, = collect(source_of(folder))
    assert np.array_equal(got['geom_idx'], geom) and np.array_equal(got['share_idx'], geom[:700])
    assert np.array_equal(got['share'], share[:700])


def test_one_changed_byte_is_a_replay_failure_and_the_sink_is_not_reached_for_it(tmp_path):
    folder = tmp_path / 'maps_src'
    finish(record_small(folder))
    path = folder / 'calls.bin'
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xff
    path.write_bytes(bytes(data))
    with pytest.raises(NumericContractError) as caught:
        collect(source_of(folder))
    assert caught.value.code == 'maps_src_replay_failed'


def swap_once(path, old, new):
    """Replace one text once, keeping the file size (and, as the tests assert, the JSON valid)."""
    assert len(old) == len(new)
    data = path.read_bytes()
    assert old in data
    path.write_bytes(data.replace(old, new, 1))
    for text in path.read_text().splitlines():
        json.loads(text)


@pytest.mark.parametrize('name, old, new', [
    ('calls.jsonl', b'"geom_len":2', b'"geom_len":3'),         # a wrong length-only value (nothing else checks it)
    ('calls.jsonl', b'"kind":"pixel"', b'"kind":"pixeo"'),       # a letter inside a string
    ('items.jsonl', b'["upper_layer","L1"]', b'["upper_layer","L9"]'),
    ('groups.jsonl', b'"analysis_layer":"L1"', b'"analysis_layer":"L8"'),
    ('frames.jsonl', b'"frame_id":"f"', b'"frame_id":"g"')])
def test_a_same_size_valid_json_edit_of_a_line_file_is_a_replay_failure(tmp_path, name, old, new):
    """R8:1: the per-file sha256 in the index catches what the structure checks cannot."""
    folder = tmp_path / 'maps_src'
    finish(record_small(folder))
    assert len(collect(source_of(folder))) == 4
    before = (folder / name).stat().st_size
    swap_once(folder / name, old, new)
    assert (folder / name).stat().st_size == before
    with pytest.raises(NumericContractError) as caught:
        collect(source_of(folder))
    assert caught.value.code == 'maps_src_replay_failed'


def test_line_bytes_count_against_the_cap(tmp_path):
    """R8:2: the arrays are tiny here; the cap trips on the line files alone."""
    recorder = ms.MapsRecorder(tmp_path / 'maps_src', CONDITIONS, max_bytes=400)
    assert recorder.open()
    for number in range(6):
        call(recorder, Decimal(2), Frame((3, 4), valid=False), [1], [1], [0.5], number)
    summary = finish(recorder)
    assert brief(summary) == {'status': 'failed', 'reason': 'cap_exceeded'}
    assert summary['calls'] < 6 and 'detail' in summary and 'line bytes' in summary['detail']


def test_a_full_disk_while_flushing_is_disk_full(tmp_path):
    """R8:5: ENOSPC from flush/fsync in close() is `disk_full`, any other error stays `finalize_failed`."""
    for error, reason in ((errno.ENOSPC, 'disk_full'), (errno.EIO, 'finalize_failed')):
        recorder = record_small(tmp_path / ('maps_%d' % error))

        def flush_all():
            raise OSError(error, 'flush failed')
        recorder._flush_all = flush_all
        assert brief(finish(recorder)) == {'status': 'failed', 'reason': reason}


@pytest.mark.parametrize('cut', ['bin', 'jsonl', 'frames', 'line'])
def test_truncated_files_are_replay_failures(tmp_path, cut):
    folder = tmp_path / 'maps_src'
    finish(record_small(folder))
    name = {'bin': 'calls.bin', 'jsonl': 'calls.jsonl', 'frames': 'frames.jsonl', 'line': 'calls.jsonl'}[cut]
    data = (folder / name).read_bytes()
    (folder / name).write_bytes(data[:-7] if cut != 'jsonl' else data[:data.index(b'\n') + 1])
    with pytest.raises(NumericContractError) as caught:
        collect(source_of(folder))
    assert caught.value.code == 'maps_src_replay_failed'


def test_a_sink_exception_passes_through_unchanged(tmp_path):
    folder = tmp_path / 'maps_src'
    finish(record_small(folder))

    def sink(**kwargs):
        raise NumericContractError('oversized_real_operation', 'x')
    with pytest.raises(NumericContractError) as caught:
        source_of(folder).replay(sink)
    assert caught.value.code == 'oversized_real_operation'


# ---- failure isolation (PLAN-v2.1 E) ---------------------------------------------------------------------------------

def enospc(*args, **kwargs):
    raise OSError(errno.ENOSPC, 'No space left on device')


def test_persistent_enospc_marks_the_recording_failed_and_later_calls_are_ignored(tmp_path, monkeypatch):
    folder = tmp_path / 'maps_src'
    recorder = ms.MapsRecorder(folder, CONDITIONS)
    assert recorder.open()
    monkeypatch.setattr(recorder, '_write', enospc)
    call(recorder, Decimal(3), Frame((3, 4)), [1], [1], [1.0])
    call(recorder, Decimal(3), Frame((3, 4)), [1], [1], [1.0])
    assert recorder.status == 'failed' and recorder.reason == 'disk_full' and not recorder.recording
    assert brief(recorder.close()) == {'status': 'failed', 'reason': 'disk_full'}
    assert [p.name for p in folder.iterdir()] == ['index.json'] or not list(folder.iterdir())


def test_other_write_errors_are_write_error(tmp_path, monkeypatch):
    recorder = ms.MapsRecorder(tmp_path / 'maps_src', CONDITIONS)
    assert recorder.open()

    def broken(*args):
        raise OSError(errno.EIO, 'io')
    monkeypatch.setattr(recorder, '_write', broken)
    call(recorder, Decimal(3), Frame((3, 4)), [1], [1], [1.0])
    summary = finish(recorder)
    assert brief(summary) == {'status': 'failed', 'reason': 'write_error'}
    assert summary['calls'] == 0 and 'OSError' in summary['detail']


@pytest.mark.parametrize('what', ['bytes', 'calls', 'array'])
def test_caps_stop_only_the_recording(tmp_path, what):
    limits = {'bytes': {'max_bytes': 100}, 'calls': {'max_calls': 2}, 'array': {'max_array_elements': 2}}[what]
    recorder = ms.MapsRecorder(tmp_path / 'maps_src', CONDITIONS, **limits)
    assert recorder.open()
    for number in range(3):
        call(recorder, Decimal(3), Frame((3, 4)), [1, 2, 3], [1, 2, 3], [1.0, 1.0, 1.0], number)
    assert brief(finish(recorder)) == {'status': 'failed', 'reason': 'cap_exceeded'}
    index = json.loads((tmp_path / 'maps_src' / 'index.json').read_text())
    assert index['status'] == 'failed' and index['reason'] == 'cap_exceeded'


def test_an_unstorable_value_is_a_write_error_not_an_exception(tmp_path):
    recorder = ms.MapsRecorder(tmp_path / 'maps_src', CONDITIONS)
    assert recorder.open()
    recorder.sink(group_key=group_key(Decimal(3)), kind='line', item_key=('p', 's', ()), item={'x': object()},
                  frame=Frame((3, 4)), geom_idx=np.zeros(1, dtype=np.int64), share_idx=np.zeros(1, dtype=np.int64),
                  share=np.zeros(1))
    assert recorder.status == 'failed' and recorder.reason == 'write_error'


def test_open_failure_is_a_failed_recording(tmp_path):
    blocker = tmp_path / 'maps_src'
    blocker.write_text('a file where the folder should be')
    recorder = ms.MapsRecorder(blocker, CONDITIONS)
    assert not recorder.open()
    assert brief(recorder.close()) == {'status': 'failed', 'reason': 'write_error'}


def test_close_failure_is_finalize_failed(tmp_path, monkeypatch):
    recorder = record_small(tmp_path / 'maps_src')

    class Bad(object):
        def __init__(self, real):
            self.real = real

        def flush(self):
            self.real.flush()

        def fileno(self):
            return self.real.fileno()

        def close(self):
            self.real.close()
            raise OSError(errno.EIO, 'close failed')
    recorder._calls = Bad(recorder._calls)
    assert brief(finish(recorder)) == {'status': 'failed', 'reason': 'finalize_failed'}


def test_cleanup_failure_is_only_noted(tmp_path, monkeypatch):
    from pathlib import Path
    recorder = record_small(tmp_path / 'maps_src')
    monkeypatch.setattr(recorder, '_write', enospc)
    call(recorder, Decimal(3), Frame((3, 4)), [1], [1], [1.0])
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == 'calls.bin':
            raise PermissionError('held open')
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    summary = finish(recorder)
    assert brief(summary) == {'status': 'failed', 'reason': 'disk_full'}
    assert any('calls.bin' in text for text in recorder.cleanup_errors)


@pytest.mark.parametrize('step', ['frames', 'index', 'estimate'])
def test_finalize_steps_that_fail_leave_a_failed_recording(tmp_path, monkeypatch, step):
    folder = tmp_path / 'maps_src'
    recorder = record_small(folder)
    if step == 'frames':
        real = recorder._flush_all

        def flush_all():
            recorder._frames.flush()
            raise OSError(errno.EIO, 'frames could not be flushed')
        monkeypatch.setattr(recorder, '_flush_all', flush_all)
        extra = lambda: {}
    elif step == 'index':
        monkeypatch.setattr(recorder, '_publish_index', enospc)
        extra = lambda: {}
    else:
        def extra():
            raise ValueError('estimate failed')
    assert brief(recorder.close(extra)) == {'status': 'failed', 'reason': 'finalize_failed'}


def test_keyboard_interrupt_is_not_swallowed(tmp_path, monkeypatch):
    recorder = ms.MapsRecorder(tmp_path / 'maps_src', CONDITIONS)
    assert recorder.open()

    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(recorder, '_write', interrupt)
    with pytest.raises(KeyboardInterrupt):
        call(recorder, Decimal(3), Frame((3, 4)), [1], [1], [1.0])
    recorder.abort()
    assert recorder.status == 'failed'


def test_abort_cleans_up_and_never_raises(tmp_path):
    folder = tmp_path / 'maps_src'
    recorder = record_small(folder)
    recorder.abort()
    assert not list(folder.iterdir())
    recorder.abort()


# ---- compare2-side recording and ledger-maps replay on the small real fixtures -------------------------------------------

import yaml
from simcompare import ledger_maps
from simcompare.adapters import real_hotspot as rh
from test_compare2 import PAIRS, base_cases
from test_ledger_maps import chain, maps_direct


def file_entry(folder, rel):
    return {'path': rel, 'size': (folder / rel).stat().st_size, 'sha256': sha(folder / rel)}


def make_side(tmp, role, name, config='compare2.yaml', **kwargs):
    """compare2-side with a recording, plus the complete.json a products-step 02 generation would carry."""
    out = tmp / name
    record = cs.run_side(tmp / config, role, out, record_maps=out / 'maps_src', **kwargs)
    files = [file_entry(out, 'side.json')]
    if record['maps_src']['status'] == 'complete':
        files.append(file_entry(out, 'maps_src/index.json'))
    (out / 'complete.json').write_text(json.dumps({'schema_version': 'asset_generation_v1', 'product': 'X',
                                                   'kind': 'side', 'generation': name, 'depends': [], 'files': files,
                                                   'maps_src': record['maps_src']}), encoding='utf-8')
    return out, record


def sides(tmp):
    return make_side(tmp, 'reference', 'side_ref')[0], make_side(tmp, 'target', 'side_tgt')[0]


def outputs(out):
    return {path.name: path.read_bytes() for path in sorted(out.iterdir()) if path.name != 'execution.json'}


def assert_same_outputs(direct, replay):
    a, b = outputs(direct), outputs(replay)
    assert sorted(a) == sorted(b)
    for name in a:
        assert a[name] == b[name], name


def execution(out):
    return json.loads((out / 'execution.json').read_text(encoding='utf-8'))


def test_recording_does_not_change_side_json_and_the_index_matches(tmp_path):
    chain(tmp_path)
    plain = tmp_path / 'plain'
    assert cs.main_side(tmp_path / 'compare2.yaml', 'reference', plain, lambda record: None) == 0
    out, record = make_side(tmp_path, 'reference', 'rec')
    assert (out / 'side.json').read_bytes() == (plain / 'side.json').read_bytes()
    assert record['maps_src']['status'] == 'complete'
    index = json.loads((out / 'maps_src' / 'index.json').read_text())
    assert index['calls'] == record['maps_src']['calls'] > 0
    assert index['raw_bytes'] == record['maps_src']['raw_bytes']
    assert index['side_sha256'] == sha(out / 'side.json') and index['input_hash']
    assert index['conditions'] == {'maps_basis': 'maps_src_v2', 'report_diameter_um': '3',
                                   'lambda_diameters_um': None, 'top_buckets': index['conditions']['top_buckets']}
    assert index['estimate_bytes'] > 0
    lines = (out / 'maps_src' / 'calls.jsonl').read_text().splitlines()
    assert len(lines) == index['calls']
    stored = sum(len(json.loads(line)['share']) and 1 for line in lines if 'share' in json.loads(line))
    assert stored > 0


@pytest.mark.parametrize('cards', [True, False])
@pytest.mark.parametrize('budget', ['wide', 'occurrence_yields', 'cards_evicted', 'too_small'])
def test_replay_equals_direct_for_budget_and_cards(tmp_path, monkeypatch, budget, cards):
    """E5: ledger_maps.json and every PNG are the same bytes, direct or replayed, also where the budget makes the
    occurrence metric give its memory back (limit = the exact peak of the existing operations), evicts cards (a card
    step that does not fit) or refuses the run (exit 2 with the same error code)."""
    chain(tmp_path)
    ref, tgt = sides(tmp_path)
    code, wide = maps_direct(tmp_path, 'wide')
    peak = execution(wide)['max_reserved_bytes']
    kwargs = {'cards': cards}
    if budget == 'occurrence_yields':
        kwargs['max_operation_bytes'] = peak
    elif budget == 'cards_evicted':
        monkeypatch.setattr(ledger_maps, 'COMPONENT_BYTES_PER_MEMBER', 10 ** 9)
        kwargs['max_operation_bytes'] = peak
    elif budget == 'too_small':
        kwargs['max_operation_bytes'] = peak - 40
    code_d, direct = maps_direct(tmp_path, 'direct', **kwargs)
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)), **kwargs)
    assert code_d == code_r
    assert_same_outputs(direct, replay)
    if budget == 'too_small':
        assert code_d == 2
        assert json.loads((direct / 'error.json').read_text())['code'] == json.loads(
            (replay / 'error.json').read_text())['code'] == 'oversized_real_operation'
        return
    doc = json.loads((replay / 'ledger_maps.json').read_text())
    assert execution(replay)['maps_source'] == {'reference': 'replay', 'target': 'replay'}
    assert 'maps_source' not in execution(direct)
    assert execution(replay)['max_reserved_bytes'] == execution(direct)['max_reserved_bytes']
    if budget == 'occurrence_yields':
        assert 'occurrence_budget_reduced' in doc['reasons']
    if budget == 'cards_evicted' and cards:
        assert 'cards_budget_reduced' in doc['reasons'] and doc['cards_dropped_for_budget']
    if budget == 'wide':
        assert code_d == 0 and (bool(doc['cards']) == cards)


def test_replay_reads_no_mask_and_runs_no_compute_areas(tmp_path, monkeypatch):
    chain(tmp_path)
    ref, tgt = sides(tmp_path)
    numeric = yaml.safe_load((tmp_path / 'numeric.yaml').read_text())
    seals = {str(ref.resolve()): rh.make_mask_seal(numeric['reference']['data_dir']),
             str(tgt.resolve()): rh.make_mask_seal(numeric['target']['data_dir'])}
    monkeypatch.setattr(ledger_maps, '_seal_of', lambda side_dir: seals[str(side_dir.resolve())
                                                                            if hasattr(side_dir, 'resolve') else
                                                                            str(__import__('pathlib').Path(side_dir).resolve())])
    code_d, direct = maps_direct(tmp_path, 'direct')
    counts = {'mask': 0, 'areas': 0}
    real_mask, real_areas = rh._load_mask, __import__('simcompare.linepixel', fromlist=['x']).compute_areas

    def mask(*args, **kwargs):
        counts['mask'] += 1
        return real_mask(*args, **kwargs)

    def areas(*args, **kwargs):
        counts['areas'] += 1
        return real_areas(*args, **kwargs)
    monkeypatch.setattr(rh, '_load_mask', mask)
    import simcompare.linepixel as lp
    monkeypatch.setattr(lp, 'compute_areas', areas)
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)))
    assert code_r == code_d
    assert counts == {'mask': 0, 'areas': 0}
    assert_same_outputs(direct, replay)
    # without the replay the same counters are not zero (the counting works)
    counts.update(mask=0, areas=0)
    maps_direct(tmp_path, 'again')
    assert counts['areas'] == 2 and counts['mask'] > 0
    assert execution(replay)['elapsed_by_stage']['reference']['read_replay'] >= 0.0


@pytest.mark.parametrize('budget', ['wide', 'occurrence_yields'])
def test_items_made_of_several_pieces_replay_the_same(tmp_path, monkeypatch, budget):
    """The chain fixture has one group per item, so both the recording and the direct run get a `_load_side` that cuts
    every non-empty report-diameter call into two pieces (the share arrays go to the first piece)."""
    chain(tmp_path)
    real = ledger_maps._load_side

    def cutting(cfg, role, top, share_sink=None):
        def sink(**call):
            geom = call['geom_idx']
            if len(geom) < 2:
                return share_sink(**call)
            half = len(geom) // 2
            share_sink(**dict(call, geom_idx=geom[:half]))
            share_sink(**dict(call, geom_idx=geom[half:], share_idx=call['share_idx'][:0], share=call['share'][:0]))
        return real(cfg, role, top, share_sink=sink if share_sink is not None else None)
    monkeypatch.setattr(cs, '_load_side', cutting)
    monkeypatch.setattr(ledger_maps, '_load_side', cutting)
    ref, tgt = sides(tmp_path)
    lines = [json.loads(text) for text in (ref / 'maps_src' / 'calls.jsonl').read_text().splitlines()]
    pieces = {}
    for line in lines:
        if 'geom_idx' in line and line['geom_len']:
            pieces[line['i']] = pieces.get(line['i'], 0) + 1
    assert max(pieces.values()) >= 2
    code, wide = maps_direct(tmp_path, 'wide')
    kwargs = {} if budget == 'wide' else {'max_operation_bytes': execution(wide)['max_reserved_bytes']}
    code_d, direct = maps_direct(tmp_path, 'direct', **kwargs)
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)), **kwargs)
    assert code_d == code_r
    assert_same_outputs(direct, replay)


def test_report_diameter_outside_the_lambda_subset_replays_the_same(tmp_path):
    chain(tmp_path, diameters=[1, 2], report={'diameter_um': 3})
    ref, tgt = sides(tmp_path)
    index = json.loads((ref / 'maps_src' / 'index.json').read_text())
    assert index['conditions']['lambda_diameters_um'] == ['1', '2'] and index['conditions']['report_diameter_um'] == '3'
    code_d, direct = maps_direct(tmp_path, 'direct')
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)))
    assert code_d == code_r
    assert_same_outputs(direct, replay)
    assert execution(replay)['maps_source'] == {'reference': 'replay', 'target': 'replay'}


def test_a_changed_byte_gives_replay_failed_without_output(tmp_path):
    chain(tmp_path)
    ref, tgt = sides(tmp_path)
    path = ref / 'maps_src' / 'calls.bin'
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    # sizes still match, so the structure check passes and only the crc catches it
    code, out = maps_direct(tmp_path, 'bad', maps_src=(str(ref), str(tgt)))
    assert code == 2
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']
    assert json.loads((out / 'error.json').read_text())['code'] == 'maps_src_replay_failed'


@pytest.mark.parametrize('change', ['no_index', 'index_not_complete', 'side_changed', 'size', 'status_none'])
def test_a_recording_that_does_not_fit_is_ignored_and_the_run_is_direct(tmp_path, change):
    chain(tmp_path)
    ref, tgt = sides(tmp_path)
    if change == 'no_index':
        (ref / 'maps_src' / 'index.json').unlink()
    elif change == 'index_not_complete':
        index = json.loads((ref / 'maps_src' / 'index.json').read_text())
        index['status'] = 'failed'
        (ref / 'maps_src' / 'index.json').write_text(json.dumps(index))
    elif change == 'side_changed':
        (ref / 'side.json').write_bytes((ref / 'side.json').read_bytes() + b' ')
    elif change == 'size':
        (ref / 'maps_src' / 'calls.jsonl').write_bytes((ref / 'maps_src' / 'calls.jsonl').read_bytes() + b'\n')
    else:
        complete = json.loads((ref / 'complete.json').read_text())
        complete['maps_src'] = {'status': 'failed', 'reason': 'disk_full'}
        (ref / 'complete.json').write_text(json.dumps(complete))
    code_d, direct = maps_direct(tmp_path, 'direct')
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)))
    assert code_r == code_d
    assert_same_outputs(direct, replay)
    shown = execution(replay)['maps_source']
    assert shown['reference'].startswith('direct (') and shown['target'] == 'replay'


def test_changed_conditions_run_direct_and_say_why(tmp_path):
    chain(tmp_path)
    config = yaml.safe_load((tmp_path / 'compare2.yaml').read_text())
    config['report']['diameter_um'] = 2                 # the sides are recorded for another report diameter
    (tmp_path / 'compare2_other.yaml').write_text(yaml.safe_dump(config))
    ref, _ = make_side(tmp_path, 'reference', 'side_ref', config='compare2_other.yaml')
    tgt, _ = make_side(tmp_path, 'target', 'side_tgt', config='compare2_other.yaml')
    code_d, direct = maps_direct(tmp_path, 'direct')
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)))
    assert code_r == code_d
    assert_same_outputs(direct, replay)
    assert execution(replay)['maps_source'] == {'reference': 'direct (conditions_changed:report_diameter_um)',
                                                 'target': 'direct (conditions_changed:report_diameter_um)'}


def test_another_estimate_runs_direct(tmp_path):
    chain(tmp_path)
    ref, tgt = sides(tmp_path)
    path = ref / 'maps_src' / 'index.json'
    index = json.loads(path.read_text())
    index['estimate_bytes'] += 1
    path.write_text(json.dumps(index))
    complete = json.loads((ref / 'complete.json').read_text())
    for entry in complete['files']:
        if entry['path'] == 'maps_src/index.json':
            entry['sha256'] = sha(path)
    (ref / 'complete.json').write_text(json.dumps(complete))
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)))
    assert execution(replay)['maps_source'] == {'reference': 'direct (estimate_changed)', 'target': 'replay'}


def test_another_input_hash_runs_direct(tmp_path):
    chain(tmp_path)
    ref, tgt = sides(tmp_path)
    path = tgt / 'maps_src' / 'index.json'
    index = json.loads(path.read_text())
    index['input_hash'] = 'other'
    path.write_text(json.dumps(index))
    complete = json.loads((tgt / 'complete.json').read_text())
    for entry in complete['files']:
        if entry['path'] == 'maps_src/index.json':
            entry['sha256'] = sha(path)
    (tgt / 'complete.json').write_text(json.dumps(complete))
    code_r, replay = maps_direct(tmp_path, 'replay', maps_src=(str(ref), str(tgt)))
    assert execution(replay)['maps_source'] == {'reference': 'replay', 'target': 'direct (input_hash_changed)'}


def test_a_failed_recording_leaves_the_side_published(tmp_path, monkeypatch):
    chain(tmp_path)
    monkeypatch.setattr(ms.MapsRecorder, '_write', enospc)
    out, record = make_side(tmp_path, 'reference', 'side_fail')
    assert brief(record['maps_src']) == {'status': 'failed', 'reason': 'disk_full'}
    assert (out / 'side.json').is_file()
    complete = json.loads((out / 'complete.json').read_text())
    assert [entry['path'] for entry in complete['files']] == ['side.json']
    assert ms.load_source(out)[0] is None


def test_a_side_json_write_failure_is_not_a_recording_failure(tmp_path, monkeypatch, capsys):
    chain(tmp_path)

    def broken(out, files):
        raise OSError(errno.ENOSPC, 'side.json could not be written')
    monkeypatch.setattr(cs, '_publish', broken)
    code = cs.main_side(tmp_path / 'compare2.yaml', 'reference', tmp_path / 'side_x', lambda record: None,
                        record_maps=tmp_path / 'side_x' / 'maps_src')
    assert code == 2
    assert not (tmp_path / 'side_x' / 'side.json').exists()


def test_the_recorder_failing_never_fails_the_side_computation(tmp_path, monkeypatch):
    chain(tmp_path)
    plain = tmp_path / 'plain'
    cs.main_side(tmp_path / 'compare2.yaml', 'reference', plain, lambda record: None)
    for target in ('_record', '_write_index', '_flush_all', '_write_array'):
        def boom(self, *args, **kwargs):
            raise RuntimeError('recorder broke')
        monkeypatch.setattr(ms.MapsRecorder, target, boom)
        out, record = make_side(tmp_path, 'reference', 'side_%s' % target)
        assert record['maps_src']['status'] == 'failed'
        assert (out / 'side.json').read_bytes() == (plain / 'side.json').read_bytes()
        monkeypatch.undo()
