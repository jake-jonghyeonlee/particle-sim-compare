"""Comparison summary mode: identical areas, top-N buckets equal the head of the full ordering, config and writer."""
import json
import random
from pathlib import Path

import pytest
import yaml

from simcompare import linepixel
from simcompare.linepixel import ClassShareSummary, compute_areas
from simcompare.numeric_schema import NumericContractError
from simcompare.pipeline import load_numeric_config, run_numeric_compare
from test_comparison_v2 import decode_v2_to_v1, fixture_config, summary_config  # noqa: F401 (module fixtures)
from test_linepixel_equivalence import random_cases


def _order(share):
    """Full-mode buckets of one class in the documented ranking: area descending, then the member-tuple order."""
    return sorted(share.exclusive + share.shared, key=lambda b: b.pair_ids)  # stable: ties keep member order


def _check(cases, top):
    full = compute_areas(cases)
    summary = compute_areas(cases, bucket_detail='summary', top_buckets=top)
    full_dict, summary_dict = full.to_dict(), summary.to_dict()
    for key, old in full.groups.items():
        new = summary.groups[key]
        for name in ('line', 'pixel'):
            old_share, new_share = getattr(old, name), getattr(new, name)
            assert isinstance(new_share, ClassShareSummary) and new_share.exclusive == new_share.shared == ()
            assert new_share.area == old_share.area
            assert new_share.bucket_count_exclusive == len(old_share.exclusive)
            assert new_share.bucket_count_shared == len(old_share.shared)
            ranked = sorted(_order(old_share), key=lambda b: -(b.area.observed_area_um2))[:top]
            assert list(new_share.top_buckets) == ranked
            total = sum(b.area.observed_area_um2 for b in old_share.exclusive + old_share.shared)
            assert total == pytest.approx(old_share.area.observed_area_um2, abs=1e-9)
        for name in ('transfer', 'union', 'source_cases', 'failure_pieces', 'fractions', 'pair_membership'):
            assert getattr(new, name) == getattr(old, name), name
        for name in ('line_mask', 'pixel_effective_mask', 'transfer_mask', 'union_mask'):
            assert (getattr(new, name) == getattr(old, name)).all()
    return full_dict, summary_dict


def test_summary_equals_full_on_60_randomized_cases():
    checked = 0
    for seed in range(120):
        cases = random_cases(random.Random(seed))
        try:
            compute_areas(cases)
        except NumericContractError:
            continue
        for top in (1, 2, 20):
            _check(cases, top)
        checked += 1
    assert checked >= 50


def test_summary_dict_drops_bucket_lists_and_full_dict_is_unchanged():
    cases = random_cases(random.Random(7))
    full, summary = _check(cases, 3)
    for group in full['groups']:
        assert set(group['line']) == {'area', 'exclusive', 'shared'}
    for group in summary['groups']:
        assert set(group['line']) == {'area', 'bucket_count_exclusive', 'bucket_count_shared', 'top_buckets'}


def test_summary_budget_counts_only_emitted_buckets():
    for seed in range(200):
        cases = random_cases(random.Random(seed))
        try:
            full = compute_areas(cases)
        except NumericContractError:
            continue
        if any(len(share.exclusive) + len(share.shared) > 1 for g in full.groups.values()
               for share in (g.line, g.pixel)):
            break
    emitted = max(sum(len(b.pair_ids) + len(b.pair_lineage_case_keys) + len(b.contributing_source_case_keys)
                      for share in (g.line, g.pixel) for b in share.exclusive + share.shared)
                  for g in full.groups.values())
    assert emitted > 1
    # the top-1 summary fits a limit that rejects the full output (the label partition itself is far smaller)
    top1 = compute_areas(cases, bucket_detail='summary', top_buckets=1, max_bucket_output_entries=emitted - 1)
    with pytest.raises(NumericContractError) as error:
        compute_areas(cases, max_bucket_output_entries=emitted - 1)
    assert error.value.code == 'oversized_real_bucket_output'
    assert top1.groups


@pytest.mark.parametrize('kwargs', [{'bucket_detail': 'x'}, {'bucket_detail': 'summary', 'top_buckets': 0},
                                    {'bucket_detail': 'summary', 'top_buckets': True}])
def test_invalid_summary_options(kwargs):
    with pytest.raises(NumericContractError):
        compute_areas(random_cases(random.Random(1)), **kwargs)


def _config(path, compare):
    cfg = yaml.safe_load(Path(path).read_text())
    if compare is not None:
        cfg['compare'] = compare
    out = Path(path).parent / 'config_variant.yaml'
    out.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return out


@pytest.mark.parametrize('compare', [{'full_bucket_detail': 'yes'}, {'top_buckets': 0}, {'top_buckets': True},
                                     {'top_buckets': 2.5}, {'unknown': 1}, [], 'summary'])
def test_compare_options_are_validated(summary_config, compare):  # noqa: F811
    with pytest.raises(NumericContractError) as error:
        load_numeric_config(_config(summary_config, compare))
    assert error.value.code == 'invalid_compare_options'


def test_summary_is_default_and_csv_equals_full(summary_config, fixture_config, tmp_path):  # noqa: F811
    for name, config in (('s', summary_config), ('f', fixture_config)):
        assert run_numeric_compare(load_numeric_config(config), out=tmp_path/name)['exit_code'] == 3
    summary = json.loads((tmp_path/'s/comparison.json').read_text(encoding='utf-8'))
    full = json.loads((tmp_path/'f/comparison.json').read_text(encoding='utf-8'))
    assert summary['bucket_detail'] == 'summary' and summary['top_buckets'] == 20
    assert 'bucket_detail' not in full
    assert (tmp_path/'s/comparison.csv').read_bytes() == (tmp_path/'f/comparison.csv').read_bytes()
    assert summary['records'] == full['records']
    for name in ('reference', 'target'):
        for old, new in zip(full[name]['groups'], summary[name]['groups']):
            for cls in ('line', 'pixel'):
                assert new[cls]['area'] == old[cls]['area']
                assert new[cls]['bucket_count_exclusive'] == len(old[cls]['exclusive'])
                assert new[cls]['bucket_count_shared'] == len(old[cls]['shared'])
                assert 'exclusive' not in new[cls] and 'shared' not in new[cls]
                ranked = sorted(old[cls]['exclusive'] + old[cls]['shared'], key=lambda b: b['pair_ids'])
                ranked = sorted(ranked, key=lambda b: -(b['area']['observed_area_um2']))[:20]
                assert new[cls]['top_buckets'] == ranked
            assert new['source_cases'] == old['source_cases']
    with pytest.raises(ValueError, match='summary'):
        decode_v2_to_v1(summary)


def test_top_buckets_option_limits_output(summary_config, tmp_path):  # noqa: F811
    config = _config(summary_config, {'top_buckets': 1})
    run_numeric_compare(load_numeric_config(config), out=tmp_path/'o')
    payload = json.loads((tmp_path/'o/comparison.json').read_text(encoding='utf-8'))
    assert payload['top_buckets'] == 1
    assert all(len(g[c]['top_buckets']) <= 1 for g in payload['reference']['groups'] for c in ('line', 'pixel'))


def test_summary_estimate_bounds_emitted_entries():
    from simcompare.adapters.real_hotspot import estimate_area_bytes
    full = estimate_area_bytes(10**6, {'L': 300}, 9600, 5_000_000)
    summary = estimate_area_bytes(10**6, {'L': 300}, 9600, 5_000_000, 20)
    emitted = 2*20*3*300
    assert summary['retained'] < full['retained'] and summary['transient'] < full['transient']
    assert full['output'] - summary['output'] == 32 * (5_000_000 - emitted) * 100       # kept by every group
    assert full['transient'] - summary['transient'] == (5_000_000 - emitted) * 200      # one group serialized
