"""T6a: 라벨 위치가 전극 수 지도 영역 안에 드는지 (읽기 전용 점검)."""
import json
from pathlib import Path
import subprocess
import sys

from test_local_delta import run_cli

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_candidate_coverage.py'


def correspondence(path, centres):
    pairs = []
    for index, (x, y) in enumerate(centres, 1):
        pairs.append({'pair_id': f'P{index}',
                      'a': {'product_id': 'reference', 'design_version': 'synthetic_design', 'frame_id': 'reference_frame',
                            'center_xy_um': [x, y], 'analysis_layer': 'M2'},
                      'b': {'product_id': 'target', 'design_version': 'synthetic_design', 'frame_id': 'target_frame',
                            'center_xy_um': [100.5, 200.5], 'analysis_layer': 'M2'},
                      'source': {'kind': 'manual', 'labelled_by': 't', 'labelled_at': '2026-10-18'}, 'status': 'ready', 'reasons': []})
    path.write_text(json.dumps({'schema_version': 'roi_correspondence_v1',
                                'producer': {'kind': 'manual', 'version': '1'}, 'pairs': pairs}), encoding='utf-8')
    return path


def test_demo_reference_counts_cover_labels_only_above_their_threshold(tmp_path):
    # demo reference at 5 um: distinct electrodes per cell = [2, 4, 3, 4, 3, 2] (cells 0..5, x = 100..106)
    data = tmp_path / 'demo'
    assert run_cli('demo-data', '--out', data).returncode == 0
    pairs = correspondence(tmp_path / 'c.json', [(102.5, 200.5), (100.5, 200.5)])
    run = subprocess.run([sys.executable, str(SCRIPT), '--config', data / 'config.yaml', '--correspondence', pairs,
                          '--min-counts', '2', '3', '5', '--out', tmp_path / 'cov'], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    report = json.loads((tmp_path / 'cov' / 'coverage.json').read_text())
    assert report['product_id'] == 'reference' and report['n_labelled_centres'] == 2
    by = report['by_min_count']
    assert by['2']['covered_by_region_cells'] == 2 and by['2']['n_regions'] == 1
    assert by['3']['covered_by_region_cells'] == 1 and by['3']['covered_by_region_bbox'] == 1
    assert by['5']['covered_by_region_cells'] == 0 and by['5']['n_regions'] == 0
    rows = {(r['min_count'], r['pair_id']): r for r in report['rows']}
    assert rows[(3, 'P1')]['inside_region_cells'] is True and rows[(3, 'P2')]['inside_region_cells'] is False
    assert rows[(3, 'P2')]['nearest_region_center_distance_um'] == 2.5  # region cells 1..4 → centre x=103
    assert rows[(5, 'P1')]['nearest_region_id'] is None
    assert (tmp_path / 'cov' / 'coverage.csv').read_text().splitlines()[0].startswith('min_count,pair_id,side')
    other = correspondence(tmp_path / 'none.json', [])
    run = subprocess.run([sys.executable, str(SCRIPT), '--config', data / 'config.yaml', '--correspondence', other,
                          '--min-counts', '2', '--out', tmp_path / 'empty'], capture_output=True, text=True)
    assert run.returncode == 3 and json.loads(run.stdout)['reasons'] == ['no_labelled_centres_for_product']
