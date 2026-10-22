"""Phase B T4: the full-set publication transaction (src/simcompare/publication.py), exercised without any dataset.

A generation is the allowlisted file set compare-windows writes (json/csv/html/png). The previous generation holds files
that stay (changed), files that disappear and the new one adds files; unrelated files sit in the same directory. Every
filesystem step of publication and of the restart recovery is failed in turn (ordinary error, Ctrl-C, process death) and the
result must always be the previous set (nothing committed) or the new set (committed), with unrelated files untouched.
Docs/usage.md promises exactly this and nothing more: before the commit marker -> OLD set, after it -> NEW set,
unreadable recovery state -> error with the state left as found.
"""
import ast
import errno
import json
from pathlib import Path
import shutil

import pytest

from simcompare import publication
from simcompare.numeric_schema import NumericContractError
from simcompare.publication import LOCAL_DELTA_FILES, SetPublication

PNG_OLD = 'local_delta_thumb_aaaaaaaaaaaa.png'
PNG_NEW = 'local_delta_thumb_bbbbbbbbbbbb.png'
OLD = {'local_delta.json': b'old-json', 'local_delta.html': b'old-html', PNG_OLD: b'old-png',
       'window_sensitivity.json': b'old-ws'}
NEW = {'local_delta.json': b'new-json', 'local_delta_rows.csv': b'new-csv', PNG_NEW: b'new-png',
       'window_sensitivity.json': b'new-ws'}
UNRELATED = {'notes.txt': b'keep', 'local_delta_notes.csv': b'keep csv', 'window_sensitivity_extra.json': b'keep json',
             'local_delta.json.bak': b'bak', 'local_delta_thumb_XYZ.png': b'not hex', 'comparison.json': b'{}',
             '.hidden': b'h', 'sub/inner.txt': b'inner'}
CONTROL = ('.local_delta.publishing', '.local_delta.publishing.partial', '.local_delta.committed',
           '.local_delta.committed.partial', '.local_delta.prev')


class Died(BaseException):
    """Simulated process death: the faulting step and every later filesystem step fail."""


def seed(out, files=None, unrelated=True):
    out.mkdir(parents=True, exist_ok=True)
    for name, data in {**(OLD if files is None else files), **(UNRELATED if unrelated else {})}.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)


def generation(out):
    return {p.name: p.read_bytes() for p in sorted(out.iterdir()) if p.is_file() and LOCAL_DELTA_FILES.fullmatch(p.name)}


def unrelated(out):
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob('*'))
            if p.is_file() and not LOCAL_DELTA_FILES.fullmatch(p.name)
            and not any(part.startswith('.local_delta.') for part in p.relative_to(out).parts)}


def leftovers(out):
    return sorted(p.name for p in out.iterdir() if p.name.startswith('.local_delta.'))


def state(out, previous=True):
    found = generation(out)
    if found == NEW:
        return 'new'
    if found == (OLD if previous else {}):
        return 'old' if previous else 'empty'
    return 'torn'


def snapshot(out):
    """Everything on disk except the scratch directories (scratch is never part of the state)."""
    return {str(p.relative_to(out)): (p.read_bytes() if p.is_file() else None) for p in sorted(out.rglob('*'))
            if '.local_delta.staging.' not in str(p)}


def stage(pub, files=NEW):
    for name, data in files.items():
        (pub.staging / name).write_bytes(data)


def publish(out, files=NEW, note=None):
    pub = SetPublication(out)
    try:
        pub.begin()
        stage(pub, files)
        pub.commit()
        if note is not None:
            note['committed'] = True                         # commit() returned: the new generation is final
    finally:
        pub.abort()


def recover_only(out):
    pub = SetPublication(out)
    try:
        pub.begin()
    finally:
        pub.abort()


PRIMITIVES = ('_present', '_is_regular', '_read_text', '_listdir', '_create', '_fsync', '_replace', '_unlink', '_link',
              '_copy', '_makedirs', '_mkdir', '_rmdir', '_rmtree', '_sync_dir')


def inject(monkeypatch, at, exc, dead, link, log=None):
    """Fail the `at`-th filesystem step (0-based) with exc; dead=True also fails every later step. at=-1 only counts."""
    counter = {'n': 0, 'dead': False}
    for attr in PRIMITIVES:
        real = getattr(SetPublication, attr)

        def call(*args, _real=real, _attr=attr, **kwargs):
            if _attr == '_link' and not link:
                raise OSError(errno.EPERM, 'no hard links')
            if counter['dead']:
                raise exc
            index = counter['n']
            counter['n'] += 1
            if log is not None:
                log.append((index, _attr, tuple(Path(str(a)).name for a in args if isinstance(a, (str, Path)))))
            if index == at:
                counter['dead'] = dead
                raise exc
            return _real(*args, **kwargs)
        monkeypatch.setattr(SetPublication, attr, staticmethod(call) if isinstance(
            SetPublication.__dict__[attr], staticmethod) else call)
    return counter


def count_steps(tmp_path, previous, link):
    from _pytest.monkeypatch import MonkeyPatch
    out = tmp_path / 'count'
    if previous:
        seed(out)
    log = []
    with MonkeyPatch.context() as mp:
        counter = inject(mp, -1, OSError(errno.EIO, 'x'), False, link, log)
        publish(out)
    assert state(out, previous) == 'new' and leftovers(out) == []
    return counter['n'], log


# ---- 1. a clean publication ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('previous', [True, False], ids=['has_previous', 'first_publication'])
def test_clean_publication_replaces_the_whole_generation_and_nothing_else(tmp_path, previous):
    out = tmp_path / 'o'
    if previous:
        seed(out)
    else:
        seed(out, files={})
    publish(out)
    assert generation(out) == NEW                          # the old-only files (html, old png) are gone, new-only added
    assert unrelated(out) == UNRELATED
    assert leftovers(out) == []
    publish(out, {'local_delta.json': b'third'})           # the next generation again replaces everything
    assert generation(out) == {'local_delta.json': b'third'} and leftovers(out) == []


def test_first_publication_creates_the_output_directory_and_a_failure_removes_it_again(tmp_path):
    out = tmp_path / 'a' / 'b'
    publish(out)
    assert generation(out) == NEW
    out2 = tmp_path / 'c'
    pub = SetPublication(out2)
    pub.begin()
    assert out2.is_dir()
    pub.abort()                                            # nothing published: the directory this run created goes away
    assert not out2.exists()
    seed(out2, files={})
    pub = SetPublication(out2)
    pub.begin()
    pub.abort()
    assert out2.is_dir()                                   # one that existed before is never removed


def test_staging_rejects_names_outside_the_allowlist_and_nothing_is_published(tmp_path):
    out = tmp_path / 'o'
    seed(out)
    before = snapshot(out)
    for bad in ('notes.txt', 'local_delta.json.tmp', 'local_delta_thumb_ABC.png', 'comparison.json', '.local_delta.publishing'):
        pub = SetPublication(out)
        pub.begin()
        stage(pub)
        (pub.staging / bad).write_bytes(b'x')
        with pytest.raises(ValueError):
            pub.commit()
        pub.abort()
        assert snapshot(out) == before, bad
    pub = SetPublication(out)
    pub.begin()
    with pytest.raises(ValueError):                        # an empty generation is a programming error, not a way to delete
        pub.commit()
    pub.abort()
    assert snapshot(out) == before


def test_an_allowlisted_name_that_is_not_a_regular_file_refuses_before_anything_changes(tmp_path):
    out = tmp_path / 'o'
    seed(out)
    (out / 'local_delta_rows.csv').mkdir()
    before = snapshot(out)
    with pytest.raises(OSError):
        publish(out)
    assert snapshot(out) == before


def test_allowlist_matches_exactly_what_the_writers_name():
    good = ['local_delta.json', 'local_delta_rows.csv', 'window_sensitivity.json', 'window_sensitivity_rows.csv',
            'window_sensitivity_rank_agreement.csv', 'local_delta.html', 'local_delta_thumbs.json',
            'local_delta_thumb_0123456789ab.png', 'local_delta_overview_a_0123456789ab.png',
            'local_delta_overview_b_0123456789ab.png']
    assert all(LOCAL_DELTA_FILES.fullmatch(name) for name in good)
    bad = ['local_delta.json\n', 'local_delta_thumb_0123456789a.png', 'local_delta_thumb_0123456789AB.png',
           'local_delta_overview_c_0123456789ab.png', 'local_delta_x.csv', 'comparison.json', 'sub/local_delta.json',
           '.local_delta.json.1.tmp', 'local_delta.html.bak']
    assert not any(LOCAL_DELTA_FILES.fullmatch(name) for name in bad)


def test_filesystem_mutations_only_happen_in_the_fault_injectable_steps():
    """Every os/shutil mutation of publication.py sits in a SetPublication static step (which the fault tests wrap) or in
    staged_file (the writers' own temp+rename)."""
    tree = ast.parse(Path(publication.__file__).read_text(encoding='utf-8'))
    mutating = {'replace', 'unlink', 'link', 'mkdir', 'makedirs', 'rmdir', 'rmtree', 'copyfile', 'rename', 'remove',
                'fsync', 'write_text', 'write_bytes', 'open', 'symlink', 'truncate'}
    allowed = set(PRIMITIVES) | {'staged_file'}
    offenders = []

    class Visitor(ast.NodeVisitor):
        stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in mutating and isinstance(func.value, ast.Name) \
                    and func.value.id in ('os', 'shutil', 'Path'):
                if not any(name in allowed for name in self.stack):
                    offenders.append((func.value.id, func.attr, node.lineno))
            if isinstance(func, ast.Name) and func.id == 'open' and not any(name in allowed for name in self.stack):
                offenders.append(('', 'open', node.lineno))
            self.generic_visit(node)
    Visitor().visit(tree)
    assert offenders == []
    # and the wrapped list is really every static step the class defines
    steps = {name for name, value in SetPublication.__dict__.items() if isinstance(value, staticmethod)}
    assert steps | {'_sync_dir'} == set(PRIMITIVES)


# ---- 2. fault at every filesystem step, then restart recovery killed at every one of its steps -------------------

@pytest.mark.parametrize('link', [True, False], ids=['hardlink', 'copy_fallback'])
@pytest.mark.parametrize('previous', [True, False], ids=['has_previous', 'first_publication'])
@pytest.mark.parametrize('kind', ['oserror', 'interrupt', 'died'])
def test_every_step_leaves_the_old_or_the_new_generation(tmp_path, kind, previous, link):
    from _pytest.monkeypatch import MonkeyPatch
    if not previous and not link:
        pytest.skip('without a previous generation there is nothing to back up')
    steps, _ = count_steps(tmp_path, previous, link)
    assert steps >= 30
    exc = {'oserror': OSError(errno.EIO, 'step failed'), 'interrupt': KeyboardInterrupt(), 'died': Died()}[kind]
    dead = kind == 'died'
    committed_seen = []
    for at in range(steps):
        out = tmp_path / f'run{at}'
        seed(out, files=None if previous else {})
        note = {}
        with MonkeyPatch.context() as mp:
            inject(mp, at, exc, dead, link)
            try:
                publish(out, note=note)
                raised = False
            except BaseException as caught:
                # a read that fails inside a recovery is reported as the fail-closed error whose cause is the fault
                assert caught is exc or (isinstance(caught, NumericContractError) and caught.__cause__ is exc), (at, caught)
                raised = True
        committed = (out / '.local_delta.committed').exists() or 'committed' in note
        if not dead:
            # handled in-process: whole generations only, scratch gone, nothing of ours left behind except after a late fault
            assert state(out, previous) in (('old' if previous else 'empty'), 'new'), (at, generation(out))
        if raised:
            committed_seen.append(committed)
        expected = 'new' if committed else ('old' if previous else 'empty')
        assert unrelated(out) == UNRELATED
        # restart recovery: killed at each of its own steps (died kinds), then run fault-free
        for kill in range(0, 80):
            copy = tmp_path / f'run{at}_k{kill}'
            shutil.copytree(str(out), str(copy))
            fired = False
            if dead:
                with MonkeyPatch.context() as mp:
                    counter = inject(mp, kill, Died(), True, link)
                    try:
                        recover_only(copy)
                    except (Died, NumericContractError):
                        pass
                    fired = counter['n'] > kill
            recover_only(copy)
            assert state(copy, previous) == expected, (at, kill, generation(copy), leftovers(copy))
            assert leftovers(copy) == [], (at, kill, leftovers(copy))
            assert unrelated(copy) == unrelated(out)
            if not fired:
                break
        else:
            pytest.fail('recovery never finished')
        recover_only(out)
        assert state(out, previous) == expected and leftovers(out) == [], at
        publish(out)                                       # the directory is not wedged
        assert state(out, previous) == 'new' and leftovers(out) == []
    # the decision point: faults before the marker rename never publish, faults after it never roll back
    assert committed_seen == sorted(committed_seen) and not committed_seen[0]
    assert kind != 'died' or committed_seen[-1]


def test_faults_are_injected_into_every_kind_of_step(tmp_path):
    """The step log of a clean publication with a previous generation names every primitive the fault tests rely on."""
    _, log = count_steps(tmp_path, True, True)
    used = {attr for _, attr, _ in log}
    assert used >= {'_present', '_is_regular', '_listdir', '_create', '_fsync', '_replace', '_unlink', '_link', '_mkdir',
                    '_rmdir', '_rmtree', '_sync_dir', '_makedirs'}
    _, copy_log = count_steps(tmp_path / 'c', True, False)
    assert '_copy' in {attr for _, attr, _ in copy_log}
    # reads of recovery: a publication that is rolled back reads the journal
    out = torn_state(tmp_path, 'r', False)
    log = []
    with pytest.MonkeyPatch.context() as mp:
        inject(mp, -1, OSError(), False, True, log)
        recover_only(out)
    assert '_read_text' in {attr for _, attr, _ in log}


def step_index(log, attr, name):
    return next(index for index, a, names in log if a == attr and name in names)


def torn_state(tmp_path, label, committed):
    """A process that died mid-publication: rollback = after the first new file replaced; roll-forward = just after the marker."""
    from _pytest.monkeypatch import MonkeyPatch
    _, log = count_steps(tmp_path / f'c{label}', True, True)
    at = (step_index(log, '_replace', '.local_delta.committed') + 1) if committed else step_index(log, '_replace', 'local_delta_rows.csv')
    out = tmp_path / label
    seed(out)
    with MonkeyPatch.context() as mp:
        inject(mp, at, Died(), True, True)
        with pytest.raises(Died):
            publish(out)
    assert (out / '.local_delta.committed').exists() == committed
    assert state(out) == ('new' if committed else 'torn')
    return out


# ---- 3. recovery fails closed ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('committed', [False, True], ids=['rollback', 'roll_forward'])
@pytest.mark.parametrize('errno_', [errno.EIO, errno.EACCES])
def test_read_fault_during_recovery_fails_closed_and_the_next_recovery_repairs(tmp_path, committed, errno_):
    from _pytest.monkeypatch import MonkeyPatch
    counting = torn_state(tmp_path, 'count', committed)
    log = []
    with MonkeyPatch.context() as mp:
        inject(mp, -1, OSError(), False, True, log)
        SetPublication(counting)._recover()
    reads = [index for index, attr, _ in log if attr in ('_present', '_read_text', '_listdir', '_is_regular')]
    assert len(reads) >= (1 if committed else 6)           # marker; rollback adds journal + per-name backup lookups
    for number, at in enumerate(reads):
        out = torn_state(tmp_path, f'f{number}', committed)
        before = snapshot(out)
        with MonkeyPatch.context() as mp:
            inject(mp, at, OSError(errno_, 'injected'), False, True)
            try:
                SetPublication(out)._recover()
                failed = False
            except NumericContractError as caught:
                failed = True
                assert caught.code == 'local_delta_recovery_unreadable' and str(out) in str(caught) and 'by hand' in str(caught)
            except OSError:                                # roll-forward discards through best-effort deletion
                failed = True
        if failed:
            assert snapshot(out) == before, at             # not one backup, journal, marker or output file was touched
        recover_only(out)                                  # second, fault-free recovery
        assert state(out) == ('new' if committed else 'old') and leftovers(out) == [], at
        assert unrelated(out) == UNRELATED


JOURNAL = {'schema': publication.JOURNAL_SCHEMA, 'old': sorted(OLD), 'new': sorted(NEW)}


@pytest.mark.parametrize('content', [
    '', '{"schema": "set_publication_v1", "old": [', '[]', json.dumps({**JOURNAL, 'schema': 'v0'}),
    json.dumps({**JOURNAL, 'old': 'local_delta.json'}), json.dumps({**JOURNAL, 'old': [1]}),
    json.dumps({**JOURNAL, 'old': ['notes.txt']}), json.dumps({**JOURNAL, 'new': ['../local_delta.json']}),
    json.dumps({**JOURNAL, 'old': ['local_delta.json', 'local_delta.json']}),
    json.dumps({'schema': publication.JOURNAL_SCHEMA, 'old': []}), b'\xff\xfe\x00bad'],
    ids=['empty', 'truncated', 'not_object', 'old_schema', 'not_list', 'not_names', 'unrelated_name', 'path_escape', 'duplicate',
         'missing_key', 'not_utf8'])
def test_corrupt_journal_is_never_discarded_and_never_deletes_unrelated_files(tmp_path, content):
    out = torn_state(tmp_path, 'o', False)
    journal = out / '.local_delta.publishing'
    journal.write_bytes(content if isinstance(content, bytes) else content.encode())
    before = snapshot(out)
    with pytest.raises(NumericContractError) as caught:
        recover_only(out)
    assert caught.value.code == 'local_delta_recovery_unreadable'
    assert snapshot(out) == before
    with pytest.raises(NumericContractError):              # still blocked on the next run, nothing is lost
        publish(out)
    assert snapshot(out) == before
    journal.write_text(json.dumps(JOURNAL), encoding='utf-8')   # the operator repairs the journal by hand
    recover_only(out)
    assert state(out) == 'old' and leftovers(out) == [] and unrelated(out) == UNRELATED


def test_a_lost_file_without_its_backup_is_reported_instead_of_guessed(tmp_path):
    out = torn_state(tmp_path, 'o', False)
    # an old file that is neither in place nor backed up (outside interference): rolling back cannot restore it
    (out / '.local_delta.prev' / 'local_delta.html').unlink()
    (out / 'local_delta.html').unlink()
    before = snapshot(out)
    with pytest.raises(NumericContractError):
        recover_only(out)
    assert snapshot(out) == before


def test_marker_stat_error_is_not_read_as_not_committed(tmp_path, monkeypatch):
    out = torn_state(tmp_path, 'o', True)
    before = snapshot(out)
    import os
    real = os.lstat
    monkeypatch.setattr(os, 'lstat', lambda p, *a, **k: (_ for _ in ()).throw(OSError(errno.EACCES, 'denied'))
                        if Path(str(p)).name == '.local_delta.committed' else real(p, *a, **k))
    with pytest.raises(NumericContractError):
        recover_only(out)
    monkeypatch.undo()
    assert snapshot(out) == before
    recover_only(out)
    assert state(out) == 'new' and leftovers(out) == []


def test_cleanup_failure_after_commit_never_rolls_back(tmp_path, monkeypatch):
    out = tmp_path / 'o'
    seed(out)
    real = SetPublication._unlink
    seen = []

    def unlink(path):
        if Path(str(path)).parent.name == '.local_delta.prev':
            seen.append(Path(str(path)).name)
            if len(seen) == 2:                              # after the first backup deletion, before the second
                raise KeyboardInterrupt()
        return real(path)
    monkeypatch.setattr(SetPublication, '_unlink', staticmethod(unlink))
    with pytest.raises(KeyboardInterrupt):
        publish(out)
    monkeypatch.undo()
    assert state(out) == 'new' and (out / '.local_delta.committed').exists()       # committed: the new set stays
    recover_only(out)                                       # the next run rolls forward and cleans up
    assert state(out) == 'new' and leftovers(out) == []
    assert unrelated(out) == UNRELATED


def test_stale_marker_that_cannot_be_removed_blocks_publication(tmp_path, monkeypatch):
    out = tmp_path / 'o'
    seed(out)
    publish(out)
    (out / '.local_delta.committed').write_text('committed')           # a marker no recovery can remove
    real = SetPublication._unlink
    monkeypatch.setattr(SetPublication, '_unlink', staticmethod(
        lambda path: (_ for _ in ()).throw(OSError(errno.EACCES, 'denied')) if Path(str(path)).name == '.local_delta.committed'
        else real(path)))
    with pytest.raises(OSError):
        publish(out, {'local_delta.json': b'x'})            # refuse instead of mixing generations
    assert generation(out) == NEW
    monkeypatch.undo()
    publish(out, {'local_delta.json': b'x'})
    assert generation(out) == {'local_delta.json': b'x'} and leftovers(out) == []


def test_rolled_back_but_not_cleaned_up_state_blocks_publication_until_cleaned(tmp_path, monkeypatch):
    out = torn_state(tmp_path, 'o', False)
    real = SetPublication._unlink
    monkeypatch.setattr(SetPublication, '_unlink', staticmethod(
        lambda path: (_ for _ in ()).throw(OSError(errno.EACCES, 'denied')) if Path(str(path)).name == '.local_delta.publishing'
        else real(path)))
    with pytest.raises(OSError):
        publish(out)                                         # rolled back, but the journal cannot be removed
    assert state(out) == 'old'
    monkeypatch.undo()
    publish(out)
    assert state(out) == 'new' and leftovers(out) == [] and unrelated(out) == UNRELATED


def test_stale_scratch_of_a_killed_run_is_removed_by_the_next_run_and_is_never_a_generation(tmp_path):
    from _pytest.monkeypatch import MonkeyPatch
    out = tmp_path / 'o'
    seed(out)
    _, log = count_steps(tmp_path / 'c', True, True)
    with MonkeyPatch.context() as mp:
        inject(mp, step_index(log, '_create', '.local_delta.publishing.partial'), Died(), True, True)
        with pytest.raises(Died):
            pub = SetPublication(out)
            pub.begin()
            stage(pub)
            pub.commit()
    assert any(p.name.startswith('.local_delta.staging.') for p in out.iterdir())
    assert generation(out) == OLD
    publish(out)
    assert leftovers(out) == [] and generation(out) == NEW
