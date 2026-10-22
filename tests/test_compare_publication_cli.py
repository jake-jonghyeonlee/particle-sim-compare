"""Phase B T5: stage-2 `compare` through the CLI keeps the previous comparison generation on failures after its publication opened.

pipeline._ComparisonStream preserves comparison.json / comparison.csv / comparison_masks.zip until its commit; cli.py's failure handler
used to delete them on any exit-2 error. The contract (same as compare-windows): a failure from stream.begin() on keeps the managed
reports; input / config errors before that keep the documented "remove managed reports" rule."""
import errno
import json
from pathlib import Path

import pytest

import test_stage46_equivalence as eq
from test_stage6_scale import compare_config, stage46, pinned_clock, files  # noqa: F401  (fixtures)
from simcompare import linepixel, pipeline
from simcompare.numeric_schema import NumericContractError

GENERATION = ('comparison.csv', 'comparison.json', 'comparison_masks.zip')
UNRELATED = {'notes.txt': b'my notes', 'comparison_notes.zip': b'user zip', 'sub/inner.txt': b'inner'}


def generation(folder):
    return {n: (Path(folder) / n).read_bytes() for n in GENERATION}


def run(cfg):
    return eq.run_cli('simcompare', ['compare', '--config', cfg])


def published(stage46, label):
    out, cfg, corr, cmp_dir, ref, tgt = compare_config(stage46, 'shared', label)
    assert run(cfg)[0] == 3
    for name, data in UNRELATED.items():
        (cmp_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (cmp_dir / name).write_bytes(data)
    return cfg, cmp_dir, generation(cmp_dir)


def assert_kept(cmp_dir, before, stderr=''):
    assert generation(cmp_dir) == before, stderr
    assert not [p.name for p in cmp_dir.iterdir() if p.name.startswith('.')]          # no temp / control files left
    for name, data in UNRELATED.items():
        assert (cmp_dir / name).read_bytes() == data
    err = json.loads((cmp_dir / 'error.json').read_text(encoding='utf-8'))
    assert err['exit_code'] == 2 and err['command'] == 'compare'


class Counter:
    def __init__(self, monkeypatch, owner, attr, at, exc):
        self.n, self.at = 0, at
        real = getattr(owner, attr)

        def call(*args, **kwargs):
            self.n += 1
            if self.n == self.at:
                raise exc
            return real(*args, **kwargs)
        monkeypatch.setattr(owner, attr, call)


FAULTS = [
    ('target_compute_contract_error', linepixel, 'compute_areas', 2, NumericContractError('boom', 'target compute failed')),
    ('target_compute_value_error', linepixel, 'compute_areas', 2, ValueError('bad target')),
    ('target_side_write_enospc', pipeline._ComparisonStream, 'side', 2, OSError(errno.ENOSPC, 'disk full')),
    ('reference_side_write_enospc', pipeline._ComparisonStream, 'side', 1, OSError(errno.ENOSPC, 'disk full')),
    ('commit_enospc', pipeline._ComparisonStream, 'commit', 1, OSError(errno.ENOSPC, 'disk full')),
]


@pytest.mark.parametrize('fault', FAULTS, ids=[f[0] for f in FAULTS])
def test_failure_after_the_publication_opened_keeps_the_previous_generation(stage46, monkeypatch, fault):
    _, owner, attr, at, exc = fault
    cfg, cmp_dir, before = published(stage46, 'cmp_fault_' + fault[0])
    state = Counter(monkeypatch, owner, attr, at, exc)
    code, _, stderr = run(cfg)
    assert state.n >= at and code == 2, stderr
    monkeypatch.undo()
    assert_kept(cmp_dir, before, stderr)
    assert run(cfg)[0] == 3                                                            # not wedged: the next run publishes
    assert not (cmp_dir / 'error.json').exists()


def test_unreadable_recovery_state_is_an_error_and_everything_is_left_as_found(stage46):
    cfg, cmp_dir, before = published(stage46, 'cmp_unreadable')
    (cmp_dir / '.comparison.publishing').write_text('{ not json')
    code, _, stderr = run(cfg)
    assert code == 2 and 'comparison_recovery_unreadable' in stderr
    assert generation(cmp_dir) == before and (cmp_dir / '.comparison.publishing').read_text() == '{ not json'
    err = json.loads((cmp_dir / 'error.json').read_text(encoding='utf-8'))
    assert err['reasons'] == ['comparison_recovery_unreadable']


def test_publication_failure_after_the_journal_rolls_back_and_keeps_the_reports(stage46, monkeypatch):
    cfg, cmp_dir, before = published(stage46, 'cmp_midpublish')
    real = pipeline.os.replace
    state = {'n': 0}

    def replace(source, destination, *args, **kwargs):
        if Path(str(source)).name.startswith('.comparison') and str(source).endswith('.tmp'):
            state['n'] += 1
            if state['n'] == 2:                                                        # after the journal and the backups
                raise OSError(errno.ENOSPC, 'disk full')
        return real(source, destination, *args, **kwargs)
    monkeypatch.setattr(pipeline.os, 'replace', replace)
    code, _, stderr = run(cfg)
    monkeypatch.undo()
    assert code == 2 and state['n'] >= 2, stderr
    assert_kept(cmp_dir, before, stderr)


def test_input_errors_before_the_publication_still_remove_the_managed_reports(stage46):
    cfg, cmp_dir, before = published(stage46, 'cmp_input_error')
    text = Path(cfg).read_text(encoding='utf-8').replace('pair_table_path: pairs.csv', 'pair_table_path: missing_pairs.csv', 1)
    broken = Path(cfg).with_name('broken.yaml')
    broken.write_text(text, encoding='utf-8')
    code, _, _ = run(broken)
    assert code == 2
    assert not (cmp_dir / 'comparison.json').exists() and not (cmp_dir / 'comparison.csv').exists()
