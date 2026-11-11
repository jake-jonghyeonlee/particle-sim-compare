"""compare2-report on the small real_hotspot_v1 trees of test_compare2 (4x4 cell frame, 0.1 um pitch)."""
import json
import re
import subprocess
import sys
from pathlib import Path

from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from PIL import Image

from simcompare.cli import main
from simcompare.compare2_link import build_link, compute_locations
from simcompare.compare2_report import build_report
from simcompare.numeric_schema import NumericContractError
from test_change_candidates import PRODUCTS, world  # noqa: F401  (world is a module-scoped fixture there)
from test_compare2 import CNO_CSV, CNO_NAME, PAIRS, base_cases, load, run, write_density
from test_compare2_link import dl1_doc, real_chain, write_json  # noqa: F401  (real_chain is a module-scoped fixture there)

TGT_CASES = base_cases(AB={1: {0, 1, 2, 3}, 2: {0, 1}, 3: {0, 1}}, GH={1: {0, 1, 2}, 2: {0}})


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


def changed(out):
    """Number of conditions whose delta_vi_line or delta_vi_pixel is not 0/None (they get cards and maps)."""
    conds = load(out)['delta']['conditions']
    return sum(1 for c in conds if any(c[k] not in (None, 0, 0.0) for k in ('delta_vi_line', 'delta_vi_pixel')))


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
    n = changed(out)
    assert 0 < n < 6  # DE/EF/IJ do not change vi in this scenario
    assert 'class="story"' not in html and '주요 결함 조건과 영향' not in html
    assert html.count('data:image/png;base64,') == 2 * n
    assert '변화 없음' in html  # unchanged conditions keep a row and a text-only detail
    assert '<td>100%</td>' in html
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert record['exit_code'] == 0 and record['warnings'] == [] and len(record['figures']) == n
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
    assert f'{vi * 100:.4f}%' in html
    assert exit_code == 3  # no dl1 root configured -> overlay warning
    delta = doc['delta']['indicators']['delta_vi_line']
    assert f'{delta * 100:+.4f}%p' in html


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


def test_report_shows_effective_resolution_row(tmp_path):
    code, out = build(tmp_path)
    assert code == 0
    exit_code, folder = report(tmp_path, out)
    assert exit_code in (0, 3)  # 3 = some report input is null in this fixture; the resolution table still renders
    assert '유효 해상도' in text(folder)


def test_report_refuses_a_stale_area_basis(tmp_path):
    code, out = build(tmp_path)
    assert code == 0
    path = out / 'compare2.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['inputs']['area_basis'] = 'crop_observed_area_div4_user_approved_2026_11_05'
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(NumericContractError) as info:
        build_report(out, tmp_path / 'compare2.yaml', tmp_path / 'stale')
    assert info.value.code == 'compare2_stale_area_basis' and '다시 실행' in str(info.value)


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
    assert html.count('data:image/png;base64,') == 2 * changed(out)


def test_without_dl1_the_overlay_is_skipped_with_exit_3(tmp_path):
    code, out = build(tmp_path)
    exit_code, folder = report(tmp_path, out)
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert exit_code == 3 and record['exit_code'] == 3
    assert {w['code'] for w in record['warnings']} == {'overlay_dl1_not_configured'}
    assert text(folder).count('data:image/png;base64,') == 2 * changed(out)


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
    assert text(folder).count('data:image/png;base64,') == 2 * changed(out)


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


def test_top_n_no_longer_makes_story_cards(tmp_path):
    code, _ = build(tmp_path)
    code, out = with_dl1(tmp_path, top_n=2)
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 0
    assert 'class="story"' not in text(folder)


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


def test_reference_only_over_limit_is_refused_before_any_mask_is_read(tmp_path, monkeypatch):
    code, _ = build(tmp_path)
    code, out = set_report(tmp_path, lambda cfg: cfg.update(report={'map_window_um': 2}))
    numeric = yaml.safe_load((tmp_path / 'numeric.yaml').read_text(encoding='utf-8'))
    numeric['reference']['condition_assignment']['max_operation_bytes'] = 1024 ** 2  # target keeps the default
    (tmp_path / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    import simcompare.compare2_report as module

    def fail(*args, **kwargs):
        raise AssertionError('a mask was read before the guards passed')
    monkeypatch.setattr(module, '_scan_side', fail)
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 2
    error = json.loads((folder / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'oversized_real_operation' and 'reference' in error['detail']


def test_card_score_uses_one_scale_per_kind():
    from simcompare.compare2_report import _cond_score, _score_scales
    conds = [{'share_line': 0.5, 'share_pixel': None, 'delta_vi_line': 1.0, 'delta_vi_pixel': 3.0},
             {'share_line': None, 'share_pixel': None, 'delta_vi_line': 1.0, 'delta_vi_pixel': 1.0},
             {'share_line': -0.25, 'share_pixel': None, 'delta_vi_line': 0.0, 'delta_vi_pixel': 0.0}]
    scales = _score_scales(conds)
    assert scales == ({'line': 2.0, 'pixel': 4.0}, 3.0)
    assert _cond_score(conds[0], scales) == 0.5 + 3.0 / 4.0      # share, then a ratio: never the raw 3.0
    assert _cond_score(conds[1], scales) == 1.0 / 2.0 + 1.0 / 4.0
    assert _cond_score(conds[2], scales) == 0.25
    zero = {'line': 0.0, 'pixel': 0.0}
    assert _cond_score({'share_line': None, 'share_pixel': None, 'delta_vi_line': 2.0, 'delta_vi_pixel': 0.0},
                       (zero, 4.0)) == 0.5                        # no kind total: normalised by the largest |Δvi|


def test_cno_card_score_falls_back_like_conditions():
    from simcompare.compare2_report import _cond_score, _score_scales
    conds = [{'share_line': None, 'share_pixel': None, 'delta_vi_line': 0.0, 'delta_vi_pixel': 1.0}]
    cno = [{'share_pixel': None, 'delta_vi_pixel': 3.0}, {'share_pixel': 0.5, 'delta_vi_pixel': 2.0}]
    scales = _score_scales(conds, cno)
    assert scales[0]['pixel'] == 6.0
    assert _cond_score(cno[0], scales) == 0.5   # |Δvi| / total, not the raw 3.0
    assert _cond_score(cno[1], scales) == 0.5   # share wins


def test_ranking_and_card_order_use_the_same_scale():
    from simcompare.compare2_report import _changed_ranking, _cond_score, _score_scales
    mk = lambda pid, line, pixel: {'pair_id': pid, 'layer': 'L1', 'share_line': None, 'share_pixel': None,
                                   'delta_vi_line': line, 'delta_vi_pixel': pixel}
    conds = [mk('A', 1.0, 0.0), mk('B', 0.0, 3.0), mk('C', 0.5, 0.5)]
    cno = [{'share_pixel': None, 'delta_vi_pixel': 10.0}]  # the CNO row changes the pixel total used by both orders
    scales = _score_scales(conds, cno)
    card_order = sorted((i for i in range(len(conds))), key=lambda i: (-_cond_score(conds[i], scales), conds[i]['pair_id']))
    assert _changed_ranking(conds, cno) == card_order == [0, 2, 1]
    assert _changed_ranking(conds) == [1, 0, 2]  # without the CNO row the pixel total is smaller and B leads


def test_relative_position_includes_the_500px_border():
    from simcompare.compare2_report import _proportional
    big, small = {'unit_px': (5000.0, 5000.0)}, {'unit_px': (4000.0, 4000.0)}
    assert _proportional((100.0, 100.0), big, small) == (-20.0, -20.0)  # (100+500)/5000*4000-500
    assert _proportional((-20.0, -20.0), small, big) == (100.0, 100.0)  # reverse mapping
    assert _proportional((7.0, 9.0), big, big) == (7.0, 9.0)


def test_guard_counts_labelling_memory():
    from simcompare.compare2_report import (FIXED_BYTES, FRAME_BYTES_PER_CELL, LABEL_BYTES_PER_CELL,
                                            guard_stage_compare2_report)
    import pytest
    from simcompare.numeric_schema import NumericContractError
    assert FRAME_BYTES_PER_CELL >= 3 + LABEL_BYTES_PER_CELL >= 10
    cells = 1000
    exact = FIXED_BYTES + cells * FRAME_BYTES_PER_CELL
    guard_stage_compare2_report('t', cells, 0, 1, exact)  # exactly at the limit passes
    with pytest.raises(NumericContractError) as info:
        guard_stage_compare2_report('t', cells, 0, 1, exact - 1)
    assert info.value.code == 'oversized_real_operation'


def test_source_payload_hash_mismatch_is_refused(tmp_path):
    code, out = build(tmp_path)
    doc = load(out)
    doc['inputs']['manifests']['target']['source_payload_hash'] = 'x' * 64
    (out / 'compare2.json').write_text(json.dumps(doc), encoding='utf-8')
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 2
    assert json.loads((folder / 'error.json').read_text(encoding='utf-8'))['code'] == 'compare2_input_mismatch'


def test_transfer_sentence_needs_a_changed_transfer_area(tmp_path):
    from simcompare.compare2_report import _sentences
    cond = {'condition_id': 'X@L1', 'class': 'line', 'presence': 'both', 'delta_vi_line': 1e-5, 'delta_vi_pixel': 0.0,
            'share_line': 0.5, 'share_pixel': None, 'delta_lambda_transfer': 1e-4, 'removal_b': None}
    fig = {role: {'area_um2': 1.0, 'components': 1, 'status': 'ok'} for role in ('reference', 'target')}
    same = {'reference': {'X@L1': {'area_transfer_um2': 0.5}}, 'target': {'X@L1': {'area_transfer_um2': 0.5}}}
    assert not any('함께 나타난 영향' in s for s in _sentences(cond, fig, same, 3))
    moved = {'reference': {'X@L1': {'area_transfer_um2': 0.5}}, 'target': {'X@L1': {'area_transfer_um2': 0.75}}}
    assert any('0.25 µm²' in s for s in _sentences(cond, fig, moved, 3))


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


# ---- V4: mockup v5 layout, rounds 4/5/6 and DL1 ----------------------------------------------------------------------------

OLD_RECORD_KEYS = {'command', 'schema_version', 'status', 'compare2_status', 'exit_code', 'compare2_json_sha256', 'config',
                   'compare2_dir', 'diameter_um', 'map_window_um', 'top_n', 'conditions', 'cno_rows', 'maps', 'figures',
                   'warnings', 'outputs', 'started_at', 'finished_at', 'elapsed_seconds'}
BANNED = ('http://', 'https://', '<link', '<script src')
TIMES = ('started_at', 'finished_at', 'elapsed_seconds')


@pytest.fixture(scope='module')
def linked(real_chain):
    """The real producer chain with a compare2 whose two sides differ in density, so the ROI has a non-zero delta-vi."""
    base = real_chain.base
    write_density(base / 'density', 'grp2', {'P1': {5: 3.0}}, {'P1': {5: 2.0}})
    cfg = yaml.safe_load((base / 'compare2.yaml').read_text(encoding='utf-8'))
    cfg['model_groups']['grp2'] = dict(cfg['model_groups']['grp'])
    cfg['products']['target']['model_group'] = 'grp2'
    cfg.update(output_dir=str(base / 'c2_rep'), lambda_diameters_um=[5], report={'map_window_um': 10, 'diameter_um': 5})
    config = base / 'compare2_rep.yaml'
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(config)]) in (0, 3)
    doc = json.loads((base / 'c2_rep/compare2.json').read_text(encoding='utf-8'))
    return SimpleNamespace(base=base, c2=base / 'c2_rep', config=config, cand=real_chain.cand, win=real_chain.windows, doc=doc)


def linked_report(linked, tmp_path, **kwargs):
    kwargs = {'candidates_dir': linked.cand, 'windows_dir': linked.win, **kwargs}
    files, record = build_report(linked.c2, linked.config, tmp_path / 'out', **kwargs)
    return dict(files)['report.html'], record


def test_a_without_links_the_report_has_the_v5_sections_and_the_old_json_keys(tmp_path):
    code, out = build(tmp_path)
    exit_code, folder = report(tmp_path, out)
    html = text(folder)
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    for section in ('s1', 's2', 's3', 's4', 's5', 's6', 's9'):
        assert f'id="{section}"' in html
    assert html.count('4·6회차 결과 없음') == 3             # the summary counts and the two ROI sections
    assert '<details><summary>6. 결함 조건 전체' in html and 'id="dist-layer"' in html
    assert set(record) == OLD_RECORD_KEYS                    # no linked input: no new key
    _files, built = build_report(out, tmp_path / 'compare2.yaml', tmp_path / 'again')
    skipped = set(TIMES) | {'outputs'}                       # visual/time fields and the --out dependent paths
    built = json.loads(json.dumps(built))
    assert {k: v for k, v in record.items() if k not in skipped} == {k: v for k, v in built.items() if k not in skipped}
    assert 'class="roi"' not in html
    assert '전체 = 선택 범위 (이물 크기 필터 없음)' in html     # no full_range in this compare2


def test_b_real_chain_report_has_cards_tables_and_locations(linked, tmp_path):
    html, record = linked_report(linked, tmp_path)
    link = build_link(linked.doc, candidates_dir=linked.cand, windows_dir=linked.win, map_window_um=10.0)
    loc = compute_locations(link, linked.doc)
    assert loc['counts']['worse'] == 1
    expected = {key: loc[key] for key in ('counts', 'top_worse', 'top_improved', 'explained', 'diameters_um', 'approximation_note')}
    assert record['locations'] == json.loads(json.dumps(expected))
    assert set(record) == OLD_RECORD_KEYS | {'locations', 'link_inputs', 'roi_figures', 'options'}
    assert record['options'] == {'top_regions': 5, 'top_improved': 3, 'table_top_n': 10, 'max_pairs_per_condition': 3}
    assert set(record['link_inputs']) == {'candidates', 'windows'}
    assert html.count('class="roi"') == 1
    for needle in ('<b>영향</b>', '<b>순위</b>', '<b>변경 목록</b>', '상위 1곳의 위치 근사합은', '근사합으로 설명되지 않는 잔차',
                   'unit 안 위치', '×1</td>', '위험 증가 <b>1</b>곳', '상충 <b>0</b>곳', '추가', '제거'):
        assert needle in html, needle
    assert len(record['roi_figures']) == 2 == len(loc['rois'][0]['map_pairs'])
    assert html.count('data:image/png;base64,') == 2 * len(record['roi_figures'])      # no condition maps beside the ROI figures
    assert all(len(f['png_sha256']['reference']) == 64 and f['aligned_diff_colours'] for f in record['roi_figures'])
    assert {w['code'] for w in record['warnings']} == {'overlay_dl1_not_configured'} and record['exit_code'] == 3
    for banned in BANNED:
        assert banned not in html


def test_c_options_change_the_counts_and_bad_values_are_refused(linked, tmp_path):
    html, record = linked_report(linked, tmp_path, max_pairs_per_condition=1)
    assert len(record['roi_figures']) == 1 and '쌍 2개 중 영향이 큰 1개만' in html
    def electrode_rows(html):
        section = html[html.index('<section id="s4">'):html.index('<section id="s5">')]
        return section.count('<td class="l"><b>')
    full = electrode_rows(linked_report(linked, tmp_path)[0])
    assert full == 3                                                                  # (electrode, layer) rows, top_n 10 -> 20 rows
    assert electrode_rows(linked_report(linked, tmp_path, top_n=1)[0]) == min(2, full)
    assert '(--top-regions 1)' in linked_report(linked, tmp_path, top_regions=1)[0]
    assert '(--top-improved 7)' in linked_report(linked, tmp_path, top_improved=7)[0]
    for name in ('top_regions', 'top_improved', 'top_n', 'max_pairs_per_condition'):
        for bad in (0, -1, 1.5, '3', True, None):
            with pytest.raises(NumericContractError) as info:
                build_report('x', 'y', 'z', **{name: bad})
            assert info.value.code == 'invalid_compare2_report_argument'


def test_d_uncomputable_roi_is_explained_and_a_null_ratio_prints_a_dash(linked, tmp_path):
    html, record = linked_report(linked, tmp_path, windows_dir=None)             # round 4 alone: no window rows at all
    assert record['locations']['counts']['uncomputable'] == 1
    assert '계산 불가 변경 위치 1곳' in html and '6회차 창 결과에 이 쌍의 named_pair 행이 없습니다' in html
    assert any(w['code'] == 'location_uncomputable' and 'named_pair_missing' in w['detail'] for w in record['warnings'])
    assert 'class="roi"' not in html
    from simcompare.compare2_report import _cover, _reason_sentence
    assert '5회차' in _reason_sentence('confirm_named_pair_unavailable')
    assert '한쪽에만' in _reason_sentence('one_sided_pair_no_endpoints')
    assert '3 µm' in _reason_sentence('window_row_missing:3') and '기준 A' in _reason_sentence('condition_unmapped:reference')
    empty = {'ratio_top_worse': None, 'ratio_top_improved': None, 'residual_unexplained_by_location_sum': None}
    cover = _cover({'loc': {'explained': {'line': empty, 'pixel': empty}}, 'base': {'line': 0.5, 'pixel': 0.5}}, ['k'], 'worse')
    assert cover.count('<b>—</b>') == 2 and 'Δvi_line —' in cover


def test_e_full_range_and_diameter_filter_are_shown(linked, tmp_path):
    html, _ = linked_report(linked, tmp_path)
    assert linked.doc['full_range'] and linked.doc['inputs']['diameter_filter']['diameters_um'] == [5.0]
    assert '전체 범위 (모든 이물 크기)' in html and '선택 범위 (5 µm)' in html and '선택 범위: 이물 5 µm.' in html
    assert '전체 = 선택' not in html


def test_f_dl1_table_marks_the_selected_product_and_prints_a_dict_reason(linked, tmp_path):
    rankings = [{'product_id': PRODUCTS['reference'], 'score': 0.9, 'input_identity': '1' * 64},
                {'product_id': 'OTHER', 'score': 0.5, 'input_identity': '2' * 64}]
    path = write_json(tmp_path / 'dl1' / 'search.json', dl1_doc(query_id=PRODUCTS['target'], rankings=rankings))
    html, record = linked_report(linked, tmp_path, dl1_search=[path])
    assert 'DL1 추천 후보' in html and 'search.json' in html and '선택됨 (compare2 제품)' in html
    assert html.count('선택됨 (compare2 제품)') == 1 and 'fine: dict reason' in html and 'X (no_assets)' in html
    assert record['link_inputs']['dl1']['files'] and 'dl1_query_mismatch' not in {w['code'] for w in record['warnings']}
    only, record = linked_report(linked, tmp_path, candidates_dir=None, windows_dir=None, dl1_search=[path])
    assert 'DL1 추천 후보' in only and only.count('4·6회차 결과 없음') == 3
    assert 'dl1_query_unverified' in {w['code'] for w in record['warnings']}


def test_g_linked_report_is_deterministic(linked, tmp_path):
    first_html, first = linked_report(linked, tmp_path)
    second_html, second = linked_report(linked, tmp_path)
    assert first_html == second_html
    strip = lambda r: {k: v for k, v in r.items() if k not in TIMES}
    assert json.dumps(strip(first), sort_keys=True) == json.dumps(strip(second), sort_keys=True)


def test_h_unlinked_report_is_byte_stable_and_has_no_script_src(tmp_path):
    code, out = build(tmp_path)
    first = build_report(out, tmp_path / 'compare2.yaml', tmp_path / 'o')[0]
    second = build_report(out, tmp_path / 'compare2.yaml', tmp_path / 'o')[0]
    assert first[0] == second[0]
    for banned in BANNED:
        assert banned not in first[0][1]


def test_i_no_condition_maps_are_built_when_the_rois_carry_the_maps(linked, tmp_path):
    html, record = linked_report(linked, tmp_path)
    assert changed(linked.c2) > 0
    assert record['maps'] == 0 and record['figures'] == [] and len(record['roi_figures']) == 2
    assert 'class="mapslot"' not in html and '불량 영역</b>' not in html            # the detail pane is text only
    plain, plain_record = linked_report(linked, tmp_path, candidates_dir=None, windows_dir=None)
    assert plain_record['maps'] == changed(linked.c2) and plain.count('class="mapslot"') >= plain_record['maps']


# ---- V5: CLI link options and review follow-ups --------------------------------------------------------------------------

def cli_report(linked, folder, *options):
    return main(['compare2-report', '--compare2-dir', str(linked.c2), '--config', str(linked.config), '--out', str(folder),
                 *options])


def test_cli_link_options_reach_build_report(linked, tmp_path):
    folder = tmp_path / 'linked'
    code = cli_report(linked, folder, '--candidates', str(linked.cand), '--windows', str(linked.win),
                      '--top-regions', '2', '--top-improved', '1', '--top-n', '3', '--max-pairs-per-condition', '1')
    assert code in (0, 3)
    record = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    assert record['locations']['counts']['worse'] == 1 and len(record['roi_figures']) == 1
    assert record['options'] == {'top_regions': 2, 'top_improved': 1, 'table_top_n': 3, 'max_pairs_per_condition': 1}
    assert record['top_n'] == 4                       # config report.top_n (default), not --top-n
    plain = tmp_path / 'plain'
    assert cli_report(linked, plain) in (0, 3)
    assert set(json.loads((plain / 'report.json').read_text(encoding='utf-8'))) == OLD_RECORD_KEYS
    for bad in ('0', '-2'):
        refused = tmp_path / f'bad{bad}'
        assert cli_report(linked, refused, '--top-n', bad) == 2
        assert json.loads((refused / 'error.json').read_text(encoding='utf-8'))['code'] == 'invalid_compare2_report_argument'


def test_adv1_missing_centre_gives_an_empty_window_with_center_missing(monkeypatch):
    import simcompare.compare2_report as cr
    import simcompare.pipeline as pipeline
    monkeypatch.setattr(pipeline, '_compare_options', lambda cfg: (None, None))

    def scan(cfg, info, top, fids, diameter, size, origin_for, states, key_of=None):
        for fid in fids:
            state = cr._new_state()
            state.update(status='ok', crop=np.ones((size, size), dtype=bool))
            state['origin'] = origin_for(fid, state)
            states[fid] = state
    monkeypatch.setattr(cr, '_scan_side', scan)
    monkeypatch.setattr(cr, '_load_overlays', lambda *a, **k: None)
    frame = SimpleNamespace(shape_yx=(100, 100), origin_xy_um=(0.0, 0.0))
    info = {'pitch': 1.0, 'frame': frame, 'limit': 1 << 40}
    cfg2 = SimpleNamespace(report=SimpleNamespace(diameter_um=5.0, map_window_um=10))
    spec = {role: {'key': None, 'layer': None, 'center_um': (50.0, 50.0)} for role in ('reference', 'target')}
    spec['target'] = {'key': None, 'layer': None, 'center_um': None}
    states = cr._build_figures(None, cfg2, {'reference': info, 'target': info}, {'f': spec}, [])
    assert states['reference']['f']['status'] == 'ok' and states['reference']['f']['crop'].all()
    assert states['target']['f']['status'] == 'center_missing' and not states['target']['f']['crop'].any()


def test_t17_a_side_without_centre_gets_no_layer_overlay(monkeypatch):
    import simcompare.compare2_report as cr
    import simcompare.pipeline as pipeline
    monkeypatch.setattr(pipeline, '_compare_options', lambda cfg: (None, None))

    def scan(cfg, info, top, fids, diameter, size, origin_for, states, key_of=None):
        for fid in fids:
            state = cr._new_state()
            state.update(status='ok', crop=np.ones((size, size), dtype=bool))
            state['origin'] = origin_for(fid, state)
            states[fid] = state
    seen = {}
    monkeypatch.setattr(cr, '_scan_side', scan)
    monkeypatch.setattr(cr, '_load_overlays',
                        lambda cfg2, info, keys, states, size, warnings, layer_of=None: seen.setdefault(info['role'], layer_of))
    frame = SimpleNamespace(shape_yx=(100, 100), origin_xy_um=(0.0, 0.0))
    infos = {role: {'pitch': 1.0, 'frame': frame, 'limit': 1 << 40, 'role': role} for role in ('reference', 'target')}
    cfg2 = SimpleNamespace(report=SimpleNamespace(diameter_um=5.0, map_window_um=10))
    spec = {'reference': {'key': None, 'layer': 'L1', 'center_um': (50.0, 50.0)},
            'target': {'key': None, 'layer': 'L1', 'center_um': None}}
    cr._build_figures(None, cfg2, infos, {'f': spec}, [])
    assert seen == {'reference': {'f': 'L1'}, 'target': {'f': None}}


def test_adv2_added_and_removed_pairs_show_one_sided_enlarged_maps(linked, tmp_path):
    _html, record = linked_report(linked, tmp_path)
    assert len(record['roi_figures']) == 2
    for fig in record['roi_figures']:
        reference, target = fig['reference']['cells'], fig['target']['cells']
        assert (reference > 0) != (target > 0), (reference, target)      # one side has the pattern, the other none


def test_adv3_rank_table_columns_say_approximate(linked, tmp_path):
    html, _ = linked_report(linked, tmp_path)
    assert 'Δvi_line (근사)' in html and 'Δvi_pixel (근사)' in html and 'net (근사)' in html


def test_adv5_pitch_only_difference_has_its_own_sentence():
    from simcompare.compare2_report import _mapset
    figs = {role: {'area_um2': 0.0, 'components': 0, 'status': 'ok', 'overlay': None, 'png': b''} for role in ('reference', 'target')}
    pitch = _mapset(None, figs, False, 5.0, 10, aligned=True, pitch_differs=True)
    size = _mapset(None, figs, False, 5.0, 10, aligned=True)
    assert 'A·B 픽셀 크기(pitch)가 달라' in pitch and 'A·B 창 크기가 달라' in size


def test_adv7_report_build_time_with_and_without_the_link(linked, tmp_path, capsys):
    import time
    timings = {}
    for name, kwargs in (('without', {}), ('with', {'candidates_dir': linked.cand, 'windows_dir': linked.win})):
        started = time.time()
        build_report(linked.c2, linked.config, tmp_path / name, **kwargs)
        timings[name] = round(time.time() - started, 3)
    with capsys.disabled():
        print(f'\nV5 adv7 build_report seconds on the real chain fixture: {timings}')
    assert all(value >= 0 for value in timings.values())
