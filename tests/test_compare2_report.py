"""compare2-report on the small real_hotspot_v1 trees of test_compare2 (4x4 cell frame, 0.1 um pitch)."""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from simcompare.cli import main
from test_compare2 import CNO_CSV, CNO_NAME, PAIRS, base_cases, load, run

TGT_CASES = base_cases(AB={1: {0, 1, 2, 3}, 2: {0, 1}, 3: {0, 1}})


def build(tmp, **kwargs):
    code, out = run(tmp, (base_cases(), PAIRS), (TGT_CASES, PAIRS), **kwargs)
    return code, out


def layer_png(root, model, layer, order, size=1004):
    folder = root / model / 'Layer'
    folder.mkdir(parents=True, exist_ok=True)
    (root / model / 'Merged_Layer').mkdir(exist_ok=True)
    array = np.full((size, size), 255, dtype=np.uint8)
    array[500:503, 500:503] = 0
    Image.fromarray(array).save(folder / f'{order}-{layer}.png')


def set_report(tmp, mutate):
    """Edit compare2.yaml, run compare2 again with it and return (code, out)."""
    path = tmp / 'compare2.yaml'
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    mutate(cfg)
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return main(['compare2', '--config', str(path)]), tmp / cfg['output_dir']


def with_dl1(tmp, **report):
    root = tmp / 'dl1'
    for model in ('ModelA', 'ModelB'):
        layer_png(root, model, 'L1', 1)
        layer_png(root, model, 'L2', 2)

    def mutate(cfg):
        cfg['products']['reference']['dl1_model'] = 'ModelA'
        cfg['products']['target']['dl1_model'] = 'ModelB'
        cfg['report'] = {'dl1_root': str(root), 'map_window_um': 2, **report}
    return set_report(tmp, mutate)


def report(tmp, out, name='rep', **kwargs):
    folder = tmp / name
    code = main(['compare2-report', '--compare2-dir', str(out), '--config', str(tmp / 'compare2.yaml'),
                 '--out', str(folder)])
    return code, folder


def text(folder):
    return (folder / 'report.html').read_text(encoding='utf-8')


def rows(html):
    table = html[html.index('<table id="conds">'):]
    table = table[:table.index('</table>')]
    return len(re.findall(r'<tr data-', table))


def test_full_report_structure(tmp_path):
    code, _ = build(tmp_path)
    assert code == 0
    code, out = with_dl1(tmp_path)
    assert code == 0
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 0, (folder / 'report.json').read_text(encoding='utf-8')
    html = text(folder)
    for banned in ('http://', 'https://', '<link', '<script src'):
        assert banned not in html
    assert rows(html) == 6 + 0 + 1 + 1
    assert html.count('class="story"') == 4
    assert html.count('data:image/png;base64,') == 2 * 6
    assert '<td>100%</td>' in html
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert record['exit_code'] == 0 and record['warnings'] == [] and len(record['figures']) == 6
    ab = [f for f in record['figures'] if f['condition_id'] == 'AB@L1'][0]
    assert ab['reference']['cells'] == 1 and ab['target']['cells'] == 2
    assert ab['reference']['overlay'] == 'ok' and ab['target']['overlay'] == 'ok'
    assert ab['same_position_diff_colours'] is True
    assert '0.01 → 0.02 µm²' in html  # 1 -> 2 cells at 0.1 um pitch


def test_numbers_come_from_compare2_json(tmp_path):
    code, out = build(tmp_path)
    assert code == 0
    exit_code, folder = report(tmp_path, out)
    doc = load(out)
    html = text(folder)
    vi = doc['sides']['target']['vi_line']
    assert f'{vi:.6f}' in html
    assert exit_code == 3  # no dl1 root configured -> overlay warning
    delta = doc['delta']['indicators']['delta_vi_line']
    assert f'{delta:+.6f}' in html


def test_crp_row_and_cno_rows(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS), (TGT_CASES, PAIRS), tgt_group='other',
                    ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    assert code == 0
    exit_code, folder = report(tmp_path, out)
    html = text(folder)
    assert rows(html) == 6 + 1 + 1 + 1 + 1
    assert '제품군 보정 차이' in html and 'CNO</span>' in html


def test_glass_pending_shows_waiting_tiles(tmp_path):
    code, out = build(tmp_path, glass=None)
    assert code == 3
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 3
    html = text(folder)
    assert '대기' in html and 'glass 면적 대기' in html
    assert '합계' in html and html.count('<td>—</td>') >= 1


def test_missing_diameter_says_input_missing(tmp_path):
    code, _ = build(tmp_path)
    assert code == 0
    code, out = with_dl1(tmp_path, diameter_um=2.5)
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 3
    html = text(folder)
    assert '2.5 µm 입력 없음' in html
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert {w['code'] for w in record['warnings']} == {'map_input_missing'}
    assert html.count('data:image/png;base64,') == 2 * 6


def test_without_dl1_the_overlay_is_skipped_with_exit_3(tmp_path):
    code, out = build(tmp_path)
    exit_code, folder = report(tmp_path, out)
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert exit_code == 3 and record['exit_code'] == 3
    assert {w['code'] for w in record['warnings']} == {'overlay_dl1_not_configured'}
    assert text(folder).count('data:image/png;base64,') == 12


def test_layer_png_size_mismatch_drops_only_that_overlay(tmp_path):
    code, _ = build(tmp_path)
    code, out = with_dl1(tmp_path)
    layer_png(tmp_path / 'dl1', 'ModelB', 'L2', 2, size=1010)
    exit_code, folder = report(tmp_path, out)
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert exit_code == 3
    codes = [(w['code'], w['role'], w['layer']) for w in record['warnings']]
    assert codes == [('overlay_layer_unusable', 'target', 'L2')]
    gh = [f for f in record['figures'] if f['condition_id'] == 'GH@L2'][0]
    ab = [f for f in record['figures'] if f['condition_id'] == 'AB@L1'][0]
    assert gh['target']['overlay'] == 'overlay_layer_unusable' and gh['reference']['overlay'] == 'ok'
    assert ab['target']['overlay'] == 'ok'
    assert text(folder).count('data:image/png;base64,') == 12


def test_zero_conditions_gives_summary_and_empty_table(tmp_path):
    code, out = build(tmp_path)
    doc = load(out)
    doc['delta']['conditions'] = []
    doc['delta']['cno'] = []
    (out / 'compare2.json').write_text(json.dumps(doc), encoding='utf-8')
    exit_code, folder = report(tmp_path, out)
    html = text(folder)
    assert exit_code == 0
    assert rows(html) == 2 and html.count('class="story"') == 0 and 'data:image/png' not in html
    assert 'vi_line' in html


def test_top_n_limits_the_cards(tmp_path):
    code, _ = build(tmp_path)
    code, out = with_dl1(tmp_path, top_n=2)
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 0
    assert text(folder).count('class="story"') == 2


def test_output_and_config_rules(tmp_path):
    code, out = build(tmp_path)
    existing = tmp_path / 'exists'
    existing.mkdir()
    (existing / 'keep.txt').write_text('mine', encoding='utf-8')
    argv = ['compare2-report', '--compare2-dir', str(out), '--config', str(tmp_path / 'compare2.yaml')]
    assert main(argv + ['--out', str(existing)]) == 2
    assert [p.name for p in existing.iterdir()] == ['keep.txt']
    assert main(argv + ['--out', str(out / 'inner')]) == 2
    assert not (out / 'inner').exists()
    cfg = yaml.safe_load((tmp_path / 'compare2.yaml').read_text(encoding='utf-8'))
    cfg['report'] = {'top_n': 3}
    other = tmp_path / 'other.yaml'
    other.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    bad = tmp_path / 'bad_out'
    assert main(['compare2-report', '--compare2-dir', str(out), '--config', str(other), '--out', str(bad)]) == 2
    error = json.loads((bad / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'compare2_config_mismatch' and error['exit_code'] == 2
    assert not (bad / 'report.html').exists()


def test_memory_guard_refuses_before_reading_masks(tmp_path):
    code, _ = build(tmp_path)

    def mutate(cfg):
        cfg['report'] = {'map_window_um': 2}
    code, out = set_report(tmp_path, mutate)
    numeric = yaml.safe_load((tmp_path / 'numeric.yaml').read_text(encoding='utf-8'))
    for role in ('reference', 'target'):
        numeric[role]['condition_assignment']['max_operation_bytes'] = 1024 ** 2
    (tmp_path / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 2
    error = json.loads((folder / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'oversized_real_operation'


def test_report_html_is_deterministic_in_content(tmp_path):
    code, out = build(tmp_path)
    _, first = report(tmp_path, out, 'r1')
    _, second = report(tmp_path, out, 'r2')
    strip = lambda t: re.sub(r'생성 \d{4}-\d{2}-\d{2}', '', t)
    assert strip(text(first)) == strip(text(second))


def test_cli_runs_as_module(tmp_path):
    code, out = build(tmp_path)
    folder = tmp_path / 'viamod'
    result = subprocess.run([sys.executable, '-m', 'simcompare', 'compare2-report', '--compare2-dir', str(out),
                             '--config', str(tmp_path / 'compare2.yaml'), '--out', str(folder)],
                            capture_output=True, text=True, env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
                                                                 'PATH': '/usr/bin:/bin'})
    assert result.returncode == 3, result.stderr
    assert (folder / 'report.html').is_file()
