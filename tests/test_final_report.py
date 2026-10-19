"""simcompare report: demo end-to-end, 값 대조, 악화/개선 분류, 창 겹침, 입력 오류(exit 2), 입력 없음."""
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from simcompare.final_report import _overlap_area
from test_local_delta import correspondence_file


def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'simcompare', *map(str, args)], capture_output=True, text=True)


@pytest.fixture(scope='module')
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp('report_demo')
    data = root / 'demo'
    assert run_cli('demo-data', '--out', data).returncode == 0
    config = data / 'config.yaml'
    assert run_cli('compare', '--config', config).returncode == 0
    assert run_cli('electrode-map', '--config', config).returncode == 0
    assert run_cli('export-review-rois', '--config', config, '--out', data / 'rois', '--min-count', 2).returncode == 0
    correspondence = correspondence_file(root / 'pairs.json')
    assert run_cli('compare-windows', '--config', config, '--correspondence', correspondence, '--window-sizes', 1,
                   '--sort-diameter-um', 5, '--out', data / 'win').returncode == 0
    return {'data': data, 'compare': data / 'numeric_outputs', 'win': data / 'win', 'rois': data / 'rois'}


def report_args(demo, out, *extra, windows=True):
    args = ['report', '--compare-dir', demo['compare'], '--map-dir', demo['compare'], '--rois-dir', demo['rois'], '--out', out]
    if windows:
        args += ['--windows-dir', demo['win']]
    return args + list(extra)


def test_demo_end_to_end(demo, tmp_path):
    out = tmp_path / 'out'
    result = run_cli(*report_args(demo, out, '--rank-window-um', 1))
    assert result.returncode == 0, result.stderr
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert not re.search(r'https?:', html) and '<script' not in html
    assert (out / 'execution.json').is_file()
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert payload['schema_version'] == 'final_report_v1' and payload['reasons'] == []
    assert set(payload['section_status'].values()) == {'available'}
    # 입력 파일 SHA256은 실제 파일과 같다
    import hashlib
    for path, info in payload['inputs'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == info['sha256']
    for section in ('A0', 'A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8'):
        assert f'id="{section.lower()}"' in html or section == 'A0'
    assert 'data:image/png;base64,' in html and '지표 읽는 법' in html and '임시 기준값' in html


def test_values_match_sources(demo, tmp_path):
    out = tmp_path / 'out'
    assert run_cli(*report_args(demo, out, '--rank-window-um', 1)).returncode == 0
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    fractions = list(csv.DictReader(open(demo['compare'] / 'comparison_fractions.csv', encoding='utf-8')))
    delta = {r['metric']: r for r in fractions if r['side'] == 'delta' and r['diameter_um'] == '5.0'}
    b1 = payload['sections']['B1'][0]['at_diameter']
    for metric, row in delta.items():
        assert b1[metric]['delta'] == float(row['value'])
        assert b1[metric]['a'] == float(row['reference_value']) and b1[metric]['b'] == float(row['target_value'])
    a1 = {(k['metric']): k for k in payload['sections']['A1']['kpi']}
    assert a1['union']['delta'] == float(delta['union']['value'])
    summary = {(r['class']): r for r in csv.DictReader(open(demo['compare'] / 'comparison_summary.csv', encoding='utf-8'))
               if r['side'] == 'delta' and r['domain'] == 'panel'}
    assert a1['line']['delta'] == float(summary['line']['value']) and a1['pixel']['delta'] == float(summary['pixel']['value'])
    local = json.loads((demo['win'] / 'local_delta.json').read_text(encoding='utf-8'))
    a2 = payload['sections']['A2']
    row = next(r for r in local['rows'] if r['window_size_um'] == 1.0 and r['diameter_um'] == 5.0)
    assert a2['zero_count'] == 1 and a2['worse'] == [] and a2['better'] == []
    assert row['window']['delta']['union']['value'] == 0.0
    card = payload['sections']['B3'][0]
    assert card['entry']['delta'] == 0.0 and card['class'] == 'Δ = 0'
    assert payload['sections']['A0']['rank_window_status'] == '임시 기준값'


def make_windows(folder, pairs, values, *, size=20.0, frame=(0.0, 0.0), shape=(100, 100), extra_rows=()):
    """합성 compare-windows 출력. pairs: {id: (ax, ay, bx, by, status)}, values: {id: Δ_union} (None이면 값 없음)."""
    folder.mkdir()
    products = {'a': {'product_id': 'reference', 'frames': {'reference_frame': {'pixel_pitch_um': 1.0, 'origin_xy_um': list(frame),
                                                                                  'shape_yx': list(shape), 'frame_area_um2': 1.0}}},
                'b': {'product_id': 'target', 'frames': {'target_frame': {'pixel_pitch_um': 1.0, 'origin_xy_um': list(frame),
                                                                           'shape_yx': list(shape), 'frame_area_um2': 1.0}}}}

    def side(product, frame_id, x, y):
        return {'product_id': product, 'frame_id': frame_id, 'center_xy_um': [x, y], 'analysis_layer': 'M2', 'failure_type': 'lateral_short'}
    payload = {'schema_version': 'local_delta_v1', 'status': 'available', 'reasons': [], 'window_sizes_um': [size],
               'products': products, 'provenance': {'is_synthetic': True, 'n_pairs': len(pairs), 'n_ready': 0, 'n_blocked': 0},
               'pairs': [{'pair_id': pid, 'a': side('reference', 'reference_frame', ax, ay), 'b': side('target', 'target_frame', bx, by),
                          'status': status, 'reasons': [] if status == 'ready' else ['why_blocked']} for pid, (ax, ay, bx, by, status) in pairs.items()],
               'rows': None}
    (folder / 'local_delta.json').write_text(json.dumps(payload), encoding='utf-8')
    header = ['pair_id', 'window_size_um', 'diameter_um', 'kind', 'side_um', 'metric', 'a_value_um2', 'a_partial_value_um2', 'a_status',
              'a_fraction_of_window', 'b_value_um2', 'b_partial_value_um2', 'b_status', 'b_fraction_of_window', 'delta_um2',
              'delta_fraction', 'delta_status', 'delta_reasons']
    with open(folder / 'local_delta_rows.csv', 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for pid, value in values.items():
            status = 'available' if value is not None else 'partial'
            a = 10.0
            writer.writerow([pid, size, 5.0, 'window', size, 'union', a, 'null', 'available', 0.1, '' if value is None else a + value,
                             'null', 'available', 0.1, '' if value is None else value, '' if value is None else value / 400, status,
                             '[]' if value is not None else '["window_clipped_a"]'])
        for row in extra_rows:
            writer.writerow(row)
    return folder


def synthetic_dirs(demo, tmp_path, pairs, values, **kwargs):
    windows = make_windows(tmp_path / 'win', pairs, values, **kwargs)
    return {**demo, 'win': windows}


def test_worse_better_split_and_order(demo, tmp_path):
    pairs = {f'P{i}': (10.0 + 30 * i, 10.0, 10.0 + 30 * i, 10.0, 'ready') for i in range(1, 8)}
    pairs['PB'] = (5.0, 90.0, 5.0, 90.0, 'blocked')
    values = {'P1': 3.0, 'P2': -4.0, 'P3': 0.0, 'P4': 9.0, 'P5': -1.0, 'P6': None, 'P7': 5.0}
    dirs = synthetic_dirs(demo, tmp_path, pairs, values)
    out = tmp_path / 'out'
    result = run_cli(*report_args(dirs, out, '--rank-window-um', 20, '--top-n', 2))
    assert result.returncode == 3  # 값 없는 쌍(P6)과 blocked 쌍
    a2 = json.loads((out / 'report.json').read_text(encoding='utf-8'))['sections']['A2']
    assert [e['pair_id'] for e in a2['worse']] == ['P4', 'P7']          # Δ 내림차순, 상위 2쌍
    assert a2['worse_total'] == 3 and a2['better_total'] == 2
    assert [e['pair_id'] for e in a2['better']] == ['P2', 'P5']          # Δ 오름차순
    assert a2['zero_count'] == 1 and 'P3' not in {e['pair_id'] for e in a2['worse'] + a2['better']}
    assert [e['pair_id'] for e in a2['unresolved']] == ['P6'] and a2['blocked'][0]['pair_id'] == 'PB'
    cards = [c['pair_id'] for c in json.loads((out / 'report.json').read_text(encoding='utf-8'))['sections']['B3']]
    assert cards == ['P4', 'P7', 'P2', 'P5', 'P1', 'P3', 'P6']          # 악화, 개선, 그 밖의 ready 쌍
    # 10쌍보다 적으면 있는 만큼만
    out2 = tmp_path / 'out2'
    run_cli(*report_args(dirs, out2, '--rank-window-um', 20))
    a2 = json.loads((out2 / 'report.json').read_text(encoding='utf-8'))['sections']['A2']
    assert len(a2['worse']) == 3 and len(a2['better']) == 2
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert 'class="worse">+9' in html and 'class="better">-4' in html


def test_overlap_detection(demo, tmp_path):
    pairs = {'P1': (50.0, 50.0, 50.0, 50.0, 'ready'), 'P2': (60.0, 50.0, 200.0, 50.0, 'ready'), 'P3': (90.0, 50.0, 300.0, 50.0, 'ready')}
    dirs = synthetic_dirs(demo, tmp_path, pairs, {'P1': 2.0, 'P2': 1.0, 'P3': -1.0})
    out = tmp_path / 'out'
    run_cli(*report_args(dirs, out, '--rank-window-um', 20))
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    overlaps = payload['sections']['A2']['overlaps']
    # a 쪽: P1 [40,60] × P2 [50,70] 는 10×20 = 200 µm². P3 [80,100]은 닿지도 않는다. b 쪽은 겹침 없음
    assert [(o['side'], o['pair_a'], o['pair_b'], o['overlap_um2']) for o in overlaps] == [('a', 'P1', 'P2', 200.0)]
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert '창 겹침: P2(겹침 200 µm²)' in html and '겹친 쌍의 Δ를 단순 합산하지 마세요' in html
    assert payload['sections']['B7']['top_level']['local_delta.json'] == 'available'
    # 값은 그대로
    assert {e['pair_id']: e['delta'] for e in payload['sections']['A2']['worse']} == {'P1': 2.0, 'P2': 1.0}
    assert _overlap_area([0, 0, 10, 10], [10, 0, 20, 10]) == 0.0  # 변이 닿기만 하면 겹침이 아니다


def assert_exit2_no_report(result, out, text=None):
    assert result.returncode == 2, result.stdout
    assert not (out / 'report.html').exists() and not (out / 'report.json').exists()
    assert (out / 'error.json').is_file() and (out / 'execution.json').is_file()
    if text:
        assert text in result.stderr


def test_rank_window_missing_lists_available(demo, tmp_path):
    out = tmp_path / 'out'
    assert_exit2_no_report(run_cli(*report_args(demo, out, '--rank-window-um', 20)), out, '1')
    assert 'window_sizes_um' in json.loads((out / 'error.json').read_text(encoding='utf-8'))['detail']
    assert '사용 가능한 창(µm): 1' in run_cli(*report_args(demo, tmp_path / 'again', '--rank-window-um', 20)).stderr


def test_exit2_leaves_no_stale_report(demo, tmp_path):
    out = tmp_path / 'out'
    assert run_cli(*report_args(demo, out, '--rank-window-um', 1)).returncode == 0
    assert (out / 'report.html').is_file()
    assert_exit2_no_report(run_cli(*report_args(demo, out, '--rank-window-um', 20)), out)


def copy_dir(source, target):
    shutil.copytree(source, target)
    return target


def test_product_mismatch(demo, tmp_path):
    windows = copy_dir(demo['win'], tmp_path / 'win')
    payload = json.loads((windows / 'local_delta.json').read_text(encoding='utf-8'))
    payload['products']['b']['product_id'] = 'other'
    (windows / 'local_delta.json').write_text(json.dumps(payload), encoding='utf-8')
    out = tmp_path / 'out'
    assert_exit2_no_report(run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1)), out, 'product')


def test_mixed_synthetic(demo, tmp_path):
    windows = copy_dir(demo['win'], tmp_path / 'win')
    payload = json.loads((windows / 'local_delta.json').read_text(encoding='utf-8'))
    payload['provenance']['is_synthetic'] = False
    (windows / 'local_delta.json').write_text(json.dumps(payload), encoding='utf-8')
    out = tmp_path / 'out'
    assert_exit2_no_report(run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1)), out, 'is_synthetic')


def test_unexpected_schema_version(demo, tmp_path):
    compare = copy_dir(demo['compare'], tmp_path / 'cmp')
    payload = json.loads((compare / 'comparison.json').read_text(encoding='utf-8'))
    payload['schema_version'] = 'numeric_comparison_report_v0'
    (compare / 'comparison.json').write_text(json.dumps(payload), encoding='utf-8')
    out = tmp_path / 'out'
    result = run_cli('report', '--compare-dir', compare, '--out', out)
    assert_exit2_no_report(result, out, 'schema_version')


def test_missing_compare_files(demo, tmp_path):
    compare = copy_dir(demo['compare'], tmp_path / 'cmp')
    (compare / 'comparison_fractions.csv').unlink()
    out = tmp_path / 'out'
    assert_exit2_no_report(run_cli('report', '--compare-dir', compare, '--out', out), out, 'comparison_fractions.csv')
    empty = tmp_path / 'empty'
    empty.mkdir()
    out2 = tmp_path / 'out2'
    assert_exit2_no_report(run_cli('report', '--compare-dir', empty, '--out', out2), out2)


def test_out_may_not_be_an_input_dir(demo):
    before = sorted(p.name for p in demo['compare'].iterdir())
    result = run_cli('report', '--compare-dir', demo['compare'], '--out', demo['compare'])
    assert result.returncode == 2 and sorted(p.name for p in demo['compare'].iterdir()) == before


def test_optional_dirs_missing_say_no_input(demo, tmp_path):
    out = tmp_path / 'out'
    result = run_cli('report', '--compare-dir', demo['compare'], '--out', out)
    assert result.returncode == 0, result.stderr   # 직접 생략한 폴더는 exit에 영향을 주지 않는다
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert html.count('입력 없음: ') >= 5
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert {payload['section_status'][k] for k in ('A2', 'B3', 'B4', 'B5', 'B6')} == {'no_input'}
    assert payload['section_status']['B1'] == 'available'


def test_partial_input_gives_exit3(demo, tmp_path):
    compare = copy_dir(demo['compare'], tmp_path / 'cmp')
    payload = json.loads((compare / 'comparison.json').read_text(encoding='utf-8'))
    payload['status'] = 'partial'
    (compare / 'comparison.json').write_text(json.dumps(payload), encoding='utf-8')
    out = tmp_path / 'out'
    result = run_cli('report', '--compare-dir', compare, '--out', out)
    assert result.returncode == 3 and (out / 'report.html').is_file()


def test_text_is_escaped(demo, tmp_path):
    pairs = {'<b>P1': (50.0, 50.0, 50.0, 50.0, 'ready')}
    dirs = synthetic_dirs(demo, tmp_path, pairs, {'<b>P1': 2.0})
    out = tmp_path / 'out'
    run_cli(*report_args(dirs, out, '--rank-window-um', 20))
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert '&lt;b&gt;P1' in html and '<b>P1' not in html
