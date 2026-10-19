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


# ---- FIX1 (Astra review of 801c8da) --------------------------------------------------------------------------------

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload), encoding='utf-8')


def user_files(folder):
    paths = [folder / 'report_notes.json', folder / 'report_backup.csv', folder / 'report_customer.png', folder / 'comparison_mine.json']
    for path in paths:
        path.write_text('user data', encoding='utf-8')
    return paths


def test_item1_compare_error_keeps_unrelated_report_files(tmp_path):
    out = tmp_path / 'out'
    out.mkdir()
    kept = user_files(out)[:3]
    result = run_cli('compare', '--config', tmp_path / 'nonexistent.yaml', '--out', out)
    assert result.returncode == 2
    assert all(path.read_text(encoding='utf-8') == 'user data' for path in kept)
    assert (out / 'error.json').is_file()


def test_item1_report_error_keeps_unrelated_files_and_removes_only_owned_report(demo, tmp_path):
    out = tmp_path / 'out'
    assert run_cli(*report_args(demo, out, '--rank-window-um', 1)).returncode == 0
    assert (out / 'report.html').is_file() and (out / 'report.json').is_file()
    kept = user_files(out)
    assert_exit2_no_report(run_cli(*report_args(demo, out, '--rank-window-um', 20)), out)
    assert all(path.read_text(encoding='utf-8') == 'user data' for path in kept)  # incl. comparison_mine.json: report never owned it


def test_item2_hostile_provenance_count_is_rejected_not_rendered(demo, tmp_path):
    for field, value in (('n_ready', '<script>alert(1)</script>'), ('n_blocked', {'x': '<script>'}), ('n_ready', -1), ('n_blocked', True)):
        windows = copy_dir(demo['win'], tmp_path / f'win_{field}_{abs(hash(str(value)))}')
        payload = read_json(windows / 'local_delta.json')
        payload['provenance'][field] = value
        write_json(windows / 'local_delta.json', payload)
        out = tmp_path / f'out_{field}_{abs(hash(str(value)))}'
        result = run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1))
        assert_exit2_no_report(result, out)  # invalid count type → exit 2, nothing rendered
        assert 'invalid_count_value' in (out / 'error.json').read_text(encoding='utf-8')


def test_item2_hostile_strings_in_other_metadata_never_reach_html_raw(demo, tmp_path):
    evil = '<script>alert(1)</script>"\'><img src=x onerror=alert(2)>'
    pairs = {evil: (50.0, 50.0, 50.0, 50.0, 'ready')}
    dirs = synthetic_dirs(demo, tmp_path, pairs, {evil: 2.0})
    payload = read_json(dirs['win'] / 'local_delta.json')
    payload['pairs'][0]['a']['analysis_layer'] = evil
    payload['pairs'][0]['a']['failure_type'] = evil
    payload['pairs'][0]['a']['frame_id'] = 'reference_frame'
    payload['provenance']['correspondence_sha256'] = evil
    payload['status'] = evil
    write_json(dirs['win'] / 'local_delta.json', payload)
    compare = copy_dir(demo['compare'], tmp_path / 'cmp')
    comparison = read_json(compare / 'comparison.json')
    comparison['fraction_of_frame_note'] = evil
    comparison['applied_gate_decisions'] = {'G1': {'status': evil, 'answer': evil, 'reason': evil}}
    write_json(compare / 'comparison.json', comparison)
    out = tmp_path / 'out'
    result = run_cli(*report_args({**dirs, 'compare': compare}, out, '--rank-window-um', 20))
    assert result.returncode in (0, 3), result.stderr
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert '<script' not in html and '<img src=x' not in html and 'onerror=alert' not in html.replace('onerror=alert(2)&gt;', '')
    assert '&lt;script&gt;' in html


def test_item2_hostile_geometry_values_are_rejected(demo, tmp_path):
    pairs = {'P1': (50.0, 50.0, 50.0, 50.0, 'ready')}
    for mutate in (lambda p: p['pairs'][0]['a'].update(center_xy_um=['"/><script>x</script>', 1]),
                   lambda p: p['pairs'][0]['a'].update(center_xy_um=[float('nan'), 1]),
                   lambda p: p['products']['a']['frames']['reference_frame'].update(origin_xy_um=['<script>', 0]),
                   lambda p: p['products']['a']['frames']['reference_frame'].update(pixel_pitch_um='1"><script>'),
                   lambda p: p['products']['a']['frames']['reference_frame'].update(shape_yx=[100, '<script>'])):
        folder = tmp_path / f'case{id(mutate)}'
        folder.mkdir()
        dirs = synthetic_dirs(demo, folder, pairs, {'P1': 2.0})
        payload = read_json(dirs['win'] / 'local_delta.json')
        mutate(payload)
        (dirs['win'] / 'local_delta.json').write_text(json.dumps(payload), encoding='utf-8')  # NaN allowed by json.dumps
        out = folder / 'out'
        result = run_cli(*report_args(dirs, out, '--rank-window-um', 20))
        assert_exit2_no_report(result, out)
        assert 'invalid_geometry_value' in (out / 'error.json').read_text(encoding='utf-8')


def add_second_context(compare):
    """comparison_fractions / summary / failure_pieces에 같은 layer(M2)의 두 번째 비교 문맥(step_M2b/exposure_b)을 덧붙인다.
    값은 원래 문맥과 다르게 하고, 두 번째 문맥에서는 target의 pixel 행을 뺀다(한쪽 제품만 있는 행)."""
    def second(text):
        data = json.loads(text)
        data.update(process_step_id='step_M2b', exposure_id='exposure_b')
        return json.dumps(data, separators=(',', ':'))
    for name, column, value_columns in (('comparison_summary.csv', 'context', ('value',)), ('comparison_failure_pieces.csv', 'context', ('value',)),
                                        ('comparison_fractions.csv', 'comparison_key', ('value', 'reference_value', 'target_value'))):
        path = compare / name
        with open(path, newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        extra = []
        for row in rows:
            if not row[column] or (name == 'comparison_fractions.csv' and row['side'] != 'delta'):
                continue
            if name == 'comparison_summary.csv' and row['side'] == 'target' and row['class'] == 'pixel':
                continue
            new = dict(row)
            new[column] = second(row[column])
            for key in value_columns:
                if new[key] not in ('', 'null'):
                    new[key] = repr(float(new[key]) * 3 + 1)
            extra.append(new)
        with open(path, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows + extra)
    return compare


def test_item3_two_contexts_on_one_layer_are_never_merged(demo, tmp_path):
    compare = add_second_context(copy_dir(demo['compare'], tmp_path / 'cmp'))
    out = tmp_path / 'out'
    result = run_cli('report', '--compare-dir', compare, '--out', out)
    assert result.returncode == 0, result.stderr
    payload = read_json(out / 'report.json')
    label1, label2 = 'M2 · step_M2/synthetic_exposure', 'M2 · step_M2b/exposure_b'

    def source(name, step, side, cls):
        rows = [r for r in csv.DictReader(open(compare / name, encoding='utf-8')) if r['side'] == side and r['domain'] == 'panel'
                and r['class'] == cls and json.loads(r['context'])['process_step_id'] == step]
        assert len(rows) == 1
        return float(rows[0]['value'])
    kpi = {(k['group'], k['metric']): k for k in payload['sections']['A1']['kpi']}
    for label, step in ((label1, 'step_M2'), (label2, 'step_M2b')):
        for cls in ('line', 'pixel'):
            entry = kpi[(label, cls)]
            assert entry['delta'] == source('comparison_summary.csv', step, 'delta', cls)
            assert entry['a'] == source('comparison_summary.csv', step, 'reference', cls)
            if (label, cls) == (label2, 'pixel'):
                assert entry['b'] is None                       # 한쪽 제품에만 있는 문맥은 b를 만들어 내지 않는다
            else:
                assert entry['b'] == source('comparison_summary.csv', step, 'target', cls)
    assert kpi[(label1, 'line')]['delta'] != kpi[(label2, 'line')]['delta']
    fractions = [r for r in csv.DictReader(open(compare / 'comparison_fractions.csv', encoding='utf-8'))
                 if r['side'] == 'delta' and r['metric'] == 'union' and r['diameter_um'] == '5.0']
    by_step = {json.loads(r['comparison_key'])['process_step_id']: r for r in fractions}
    assert kpi[(label1, 'union')]['delta'] == float(by_step['step_M2']['value'])
    assert kpi[(label2, 'union')]['delta'] == float(by_step['step_M2b']['value'])
    assert kpi[(label2, 'union')]['a'] == float(by_step['step_M2b']['reference_value'])
    b1 = {g['group']: g for g in payload['sections']['B1']}
    assert set(b1) == {label1, label2}
    assert b1[label2]['at_diameter']['union']['delta'] == float(by_step['step_M2b']['value'])
    b2 = {g['group']: g for g in payload['sections']['B2']}
    assert set(b2) == {label1, label2}                          # B1과 같은 개수·같은 이름
    for label, step in ((label1, 'step_M2'), (label2, 'step_M2b')):
        assert b2[label]['classes']['a']['line'] == source('comparison_summary.csv', step, 'reference', 'line')
        assert b2[label]['classes']['b']['pixel'] == (None if step == 'step_M2b' else source('comparison_summary.csv', step, 'target', 'pixel'))
        assert b2[label]['pieces']['a']['lateral_only'] == source('comparison_failure_pieces.csv', step, 'reference', 'lateral_only')
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert f'<h3>{label1}</h3>' in html and f'<h3>{label2}</h3>' in html
    assert html.count(f'<b>{label2} · line Δ (panel)</b>') == 1


def test_item3_duplicate_context_rows_are_an_error_not_an_overwrite(demo, tmp_path):
    compare = copy_dir(demo['compare'], tmp_path / 'cmp')
    path = compare / 'comparison_summary.csv'
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    path.write_text(''.join(lines) + lines[2], encoding='utf-8')   # reference M2 line panel 행 한 줄 더
    out = tmp_path / 'out'
    assert_exit2_no_report(run_cli('report', '--compare-dir', compare, '--out', out), out, '같은 비교 문맥')


def blocked_correspondence(path):
    correspondence_file(path)
    payload = read_json(path)
    payload['pairs'][0].update(status='blocked', reasons=['position_outside_source_frame_a'])
    write_json(path, payload)
    return path


def test_item4_all_blocked_windows_result_gives_report_with_exit3(demo, tmp_path):
    windows = tmp_path / 'win'
    config = demo['data'] / 'config.yaml'
    produced = run_cli('compare-windows', '--config', config, '--correspondence', blocked_correspondence(tmp_path / 'c.json'),
                       '--window-sizes', 1, '--sort-diameter-um', 5, '--out', windows)
    assert produced.returncode == 3, produced.stderr
    assert read_json(windows / 'local_delta.json')['rows'] in ([], None) or not list(csv.DictReader(open(windows / 'local_delta_rows.csv', encoding='utf-8')))
    out = tmp_path / 'out'
    result = run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1))
    assert result.returncode == 3, result.stderr
    payload = read_json(out / 'report.json')
    assert payload['sections']['A2']['blocked'] == [{'pair_id': 'P1', 'reasons': ['position_outside_source_frame_a']}]
    assert payload['section_status']['A2'] == 'partial'
    html = (out / 'report.html').read_text(encoding='utf-8')
    b7 = html[html.index('id="b7"'):html.index('id="b8"')]
    a2 = html[html.index('id="a2"'):html.index('id="a3"')]
    for part in (a2, b7):
        assert 'P1' in part and 'blocked' in part and 'position_outside_source_frame_a' in part


def test_item4_diameter_not_available_still_exit2_when_rows_exist(demo, tmp_path):
    out = tmp_path / 'out'
    result = run_cli(*report_args(demo, out, '--rank-window-um', 1, '--diameter-um', 7.5))
    assert_exit2_no_report(result, out)
    assert 'diameter_not_available' in (out / 'error.json').read_text(encoding='utf-8')


def two_pair_windows(demo, tmp_path):
    """P1은 endpoints가 있고 P2는 없는(→ layout crop 없음) compare-windows 출력."""
    correspondence_file(tmp_path / 'c.json')
    payload = read_json(tmp_path / 'c.json')
    second = json.loads(json.dumps(payload['pairs'][0]))
    second.update(pair_id='P2')
    for key in ('a', 'b'):
        second[key].pop('endpoints'); second[key].pop('failure_type')
    second['a']['center_xy_um'] = [104.5, 200.5]
    payload['pairs'].append(second)
    write_json(tmp_path / 'c.json', payload)
    windows = tmp_path / 'win2'
    produced = run_cli('compare-windows', '--config', demo['data'] / 'config.yaml', '--correspondence', tmp_path / 'c.json',
                       '--window-sizes', 1, '--sort-diameter-um', 5, '--out', windows)
    assert produced.returncode == 0, produced.stderr
    return {**demo, 'win': windows}


def test_item5_missing_crops_make_b3_partial_with_reasons(demo, tmp_path):
    dirs = two_pair_windows(demo, tmp_path)
    out = tmp_path / 'out'
    result = run_cli(*report_args(dirs, out, '--rank-window-um', 1))
    assert result.returncode == 3, result.stderr
    payload = read_json(out / 'report.json')
    assert payload['section_status']['B3'] == 'partial'
    cards = {c['pair_id']: c for c in payload['sections']['B3']}
    assert cards['P1']['missing_crops'] == []
    assert sorted(m.split(':')[0] for m in cards['P2']['missing_crops']) == ['P2/a/layout', 'P2/b/layout']
    assert any(r.startswith('B3: ') and 'P2/a/layout' in r for r in payload['reasons'])


def test_item5_null_file_with_note_versus_nonexistent_referenced_file(demo, tmp_path):
    # null + note: 알려진 누락 → partial(exit 3)
    windows = copy_dir(demo['win'], tmp_path / 'win_null')
    thumbs = read_json(windows / 'local_delta_thumbs.json')
    crop = next(c for c in thumbs['crops'] if c['kind'] == 'hotspot' and c['side'] == 'a')
    crop.update(file=None, note='hotspot_not_available_for_test')
    write_json(windows / 'local_delta_thumbs.json', thumbs)
    out = tmp_path / 'out_null'
    result = run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1))
    assert result.returncode == 3, result.stderr
    card = read_json(out / 'report.json')['sections']['B3'][0]
    assert card['missing_crops'] == ['P1/a/hotspot: hotspot_not_available_for_test']
    # 기록 자체가 없는 crop도 partial
    windows = copy_dir(demo['win'], tmp_path / 'win_gone')
    thumbs = read_json(windows / 'local_delta_thumbs.json')
    thumbs['crops'] = [c for c in thumbs['crops'] if not (c['kind'] == 'layout' and c['side'] == 'b')]
    write_json(windows / 'local_delta_thumbs.json', thumbs)
    out = tmp_path / 'out_gone'
    assert run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1)).returncode == 3
    # 가리키는 파일이 없다 = 입력이 서로 맞지 않음 → exit 2
    windows = copy_dir(demo['win'], tmp_path / 'win_dangling')
    thumbs = read_json(windows / 'local_delta_thumbs.json')
    next(c for c in thumbs['crops'] if c['file'])['file'] = 'no_such_crop.png'
    write_json(windows / 'local_delta_thumbs.json', thumbs)
    out = tmp_path / 'out_dangling'
    assert_exit2_no_report(run_cli(*report_args({**demo, 'win': windows}, out, '--rank-window-um', 1)), out, 'no_such_crop.png')


def test_item7_b3_html_grows_linearly_with_pair_count(demo, tmp_path):
    def build(count):
        folder = tmp_path / f'n{count}'
        folder.mkdir()
        pairs = {f'P{i:03d}': (5.0 + 10 * (i % 10), 5.0 + 10 * (i // 10), 5.0 + 10 * (i % 10), 5.0 + 10 * (i // 10), 'ready') for i in range(count)}
        dirs = synthetic_dirs(demo, folder, pairs, {pid: float(i % 7 + 1) for i, pid in enumerate(pairs)}, size=2.0)
        out = folder / 'out'
        assert run_cli(*report_args(dirs, out, '--rank-window-um', 2)).returncode == 3   # PNG 없는 합성 입력 → B3 partial
        assert not list(out.glob('*.png'))
        return (out / 'report.html').read_text(encoding='utf-8')
    small, large = build(50), build(100)
    assert len(large.encode('utf-8')) < 1_500_000
    assert large.count('<rect') <= 12 * 100                     # 801c8da: 20,616 rects for 100 pairs
    assert len(large) < 2.4 * len(small)                        # 선형 (801c8da는 약 4배)
    # 카드의 작은 위치도는 그 쌍의 창 하나만 그린다
    card = large.split('<div class="pair" id=')[1]
    assert card.count('<circle') == 2 and card.count('stroke-width="2"') == 2
    b3 = large[large.index('id="b3"'):large.index('id="b4"')]
    assert b3.count('<circle') == 2 * 100 + 2 * 100             # 전체 frame 위치도 한 번(쌍당 2) + 카드(쌍당 2)


def test_item8_overview_source_pair_and_limit_are_disclosed(demo, tmp_path):
    out = tmp_path / 'out'
    assert run_cli(*report_args(demo, out, '--rank-window-um', 1)).returncode == 0
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert '배경 그림: 쌍 P1의 두 전극만 그린 것입니다' in html and '창 사각형의 위치는 정확합니다' in html
    thumbs = read_json(demo['win'] / 'local_delta_thumbs.json')
    assert {s['overview_source_pair_id'] for s in thumbs['sides']} == {'P1'}
