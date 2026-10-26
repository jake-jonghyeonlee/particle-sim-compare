"""T3 refactor guard: the shared candidates helpers must not change a single output byte of `candidates`.

The reference bytes come from the code at BASELINE (1650cac): `git archive 1650cac src | tar -x -C <tmp>`, then the same
config is run with that code in a subprocess. Both runs read the same input folders, so absolute input paths match.
"""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from simcompare.candidates import RegionInfo, merge_regions
from test_candidates import demo, real_config

BASELINE = '1650cac'
REPO = Path(__file__).resolve().parents[1]
DRIVER = ("import sys; sys.path.insert(0, sys.argv[1]); from simcompare.cli import main; "
          "sys.exit(main(['candidates', '--config', sys.argv[2], '--min-count', sys.argv[3], '--window-um', '10', "
          "'--merge-overlap', '0.5', '--out', sys.argv[4]]))")


def baseline_src(tmp_path):
    folder = tmp_path/'baseline_src'
    folder.mkdir()
    archive = subprocess.run(['git', '-C', str(REPO), 'archive', BASELINE, 'src'], capture_output=True)
    if archive.returncode != 0:
        pytest.skip(f'{BASELINE} is not available in this checkout')
    subprocess.run(['tar', '-x', '-C', str(folder)], input=archive.stdout, check=True)
    return folder/'src'


def digests(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def assert_same_bytes(tmp_path, config, k):
    src = baseline_src(tmp_path)
    # The exporter records `git rev-parse HEAD` of its checkout (producer_commit). Both trees run outside git
    # ('producer_not_in_git_checkout') so that only code differences can change a byte.
    current = tmp_path/'current_src'
    shutil.copytree(REPO/'src', current, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    old, new = tmp_path/'old_out', tmp_path/'new_out'
    codes = []
    for tree, out in ((src, old), (current, new)):
        done = subprocess.run([sys.executable, '-c', DRIVER, str(tree), str(config), str(k), str(out)],
                              capture_output=True, text=True)
        assert done.returncode in (0, 3), done.stderr
        codes.append(done.returncode)
    assert codes[0] == codes[1]
    before, after = digests(old), digests(new)
    assert before and before == after


def test_numeric_v2_output_bytes_match_baseline(tmp_path):
    assert_same_bytes(tmp_path, demo(tmp_path)/'config.yaml', 4)


def test_real_hotspot_v1_output_bytes_match_baseline(tmp_path):
    assert_same_bytes(tmp_path, real_config(tmp_path), 1)


def region(region_id, cx, count, area=1.0, score=None):
    return RegionInfo(region_id, ('P', 'D', 'F'), (cx, 0.0), count, area, 'M2', (-100, -100, 100, 100), score)


def test_merge_regions_default_rank_key_is_unchanged():
    regions = [region('a', 0.0, 4), region('b', 1.0, 6), region('c', 50.0, 5)]
    default = merge_regions(regions, 4, 0.5)
    assert merge_regions(regions, 4, 0.5, rank_key=None) == default
    assert [m['representative'].region_id for m in default] == ['b', 'c']


def test_merge_regions_custom_rank_key_changes_representative_and_order():
    regions = [region('a', 0.0, 4, score=9), region('b', 1.0, 6, score=1), region('c', 50.0, 5, score=5)]
    by_score = merge_regions(regions, 4, 0.5, rank_key=lambda r: (-r.score, r.region_id))
    assert [m['representative'].region_id for m in by_score] == ['a', 'c']
    assert [m['members'] for m in by_score][0] == regions[:2]


def test_prepare_side_builds_side_without_maps_and_index_export_matches(tmp_path):
    from simcompare import candidates
    from simcompare.roi_export import AssetMemo
    from simcompare import pipeline
    cfg = pipeline.load_numeric_config(demo(tmp_path)/'config.yaml')
    lean = candidates.prepare_side(cfg, 'reference', 5)
    full = candidates._prepare(cfg, 'reference', 4, 5)
    assert lean.maps is None and lean.regions == [] and full.maps is not None and full.regions
    assert lean.product_ids == full.product_ids and lean.input_record == full.input_record
    rows = []
    for index, side in enumerate((lean, full)):
        track, cleanup = candidates.output_tracker()
        candidates.export_index(cfg, side, tmp_path/f'idx{index}', 5, track, AssetMemo(), 0,
                                min_count=None if side is lean else 4)
        rows.append(side.index['rows'])
    assert rows[0] and rows[0] == rows[1]
    assert candidates.index_coverage(lean) == candidates.index_coverage(full)
