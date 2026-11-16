"""ledger-report --maps-dir (mrep/T5): reading a `ledger_maps_v2` folder — the checks (stale, schema, missing files), the bands the
report shows for it, the bundle's maps / changes / cards meta, and the CLI.

The old section 2 (canvas script) and section 5 (location cards) are gone: T7 draws the maps and T8 the cards. Their tests are
replaced by these data-level tests; the old ones are in git history (6aeb027)."""
import hashlib
import io
import json
import re

import numpy as np
import pytest
from PIL import Image

from simcompare import ledger_report
from simcompare.cli import main
from test_ledger_report import bundle_of, html_of, sample_doc, section, write_ledger

ROLES = (('reference', 'a', ('both', 'gone')), ('target', 'b', ('both', 'new')))


# ---- a synthetic ledger-maps folder -------------------------------------------------------------------------------------

def png_of(num):
    rgb = np.zeros(num.shape + (3,), dtype=np.uint8)
    rgb[..., 0] = (num & 0xFF).astype(np.uint8)
    rgb[..., 1] = (num >> 8).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format='PNG')
    return buffer.getvalue()


def counts_of(num):
    counts = np.bincount(num.reshape(-1))
    return {str(i): int(c) for i, c in enumerate(counts) if i and c}


def write_maps(tmp, doc, ledger_sha, name='maps', status='ok', schema='ledger_maps_v2', cards=None, bands=None, changes=None,
               overlay=None, dropped=None):
    """Item picture + electrode picture per layer and product, ledger_maps.json (v2)."""
    out = tmp / name
    out.mkdir(parents=True, exist_ok=True)
    sides = {}
    for role, key, present in ROLES:
        items, layers = [], []
        for pair in doc['pairs']:
            for item in pair['items']:
                if item['presence'] in present and item['analysis_layer'] not in layers:
                    layers.append(item['analysis_layer'])
        maps = []
        for index, layer in enumerate(sorted(layers), 1):
            for kind in ('it', 'el'):
                num = np.zeros((6, 12), dtype=np.uint16)
                num[1:4, 1:3] = 1
                file = 'map_%s_%03d_%s.png' % (role, index, kind)
                (out / file).write_bytes(png_of(num))
                maps.append({'file': file, 'layer': layer, 'kind': kind, 'px_um': 0.2, 'shape_yx': [6, 12], 'origin_px': [0, 0],
                             'numbers': counts_of(num)})
        sides[role] = {'items': [], 'maps': maps, 'frame': {'shape_yx': [6, 12], 'origin_um': [0.0, 0.0], 'pitch_um': 0.2},
                       'electrodes': [], 'hidden': {'item_px': 0, 'electrode_overlap_px': 0}}
    document = {'schema_version': schema, 'status': status,
                'reasons': ['ledger_maps_value_check_failed'] if status == 'partial' else [],
                'inputs': {'ledger_json_sha256': ledger_sha}, 'options': {'tile_um': 1.0, 'top_cards': 5}, 'report': {'diameter_um': 5.0},
                'sides': sides, 'cards': cards or [], 'scale_steps': [], 'bands': bands or [], 'cards_dropped_for_budget': dropped or [],
                'changes': changes, 'overlay': overlay or {'available': False, 'offset_px': None, 'reason': 'coords_no_candidates'}}
    if changes is None:
        del document['changes']
    (out / 'ledger_maps.json').write_text(json.dumps(document, sort_keys=True), encoding='utf-8')
    return out


def ok_changes(pairs=None):
    """A `changes` block like ledger_changes.build_changes + roi_map_rects write it (README 29)."""
    pairs = pairs if pairs is not None else {'P1': {'roi_keys': ['roi:R1'], 'tags': ['shifted', 'reshaped']},
                                             'P3': {'roi_keys': ['roi:R1'], 'tags': ['reshaped']}}
    return {'link_status': 'ok', 'coords_status': 'ok', 'apply_align': 'stretch', 'detail': None,
            'rois': [{'roi_key': 'roi:R1', 'query_id': 'q1', 'region_ids': ['R1'], 'layers': ['L1'], 'score_um2': 3.0, 'status': 'ready',
                      'center_xy_um': [1.0, 2.0], 'window_um': 2.0, 'change_kinds': ['shifted', 'reshaped'], 'change_kind_text': ['위치 이동', '형상 변경'],
                      'cases': [], 'map': {'status': 'ok'}}],
            'pairs': pairs, 'items': {}, 'cno': {}, 'outside_map': [], 'warnings': [],
            'summary': {'roi_count': 1, 'linked_pairs': 2, 'linked_no_change_pairs': 0, 'unlinked_pairs': 2, 'pair_denominator': 4,
                        'linked_cases': 2, 'unlinked_cases': 0, 'ambiguous_cases': 0,
                        'tag_pair_counts': {'shifted': {'line': 1, 'pixel': 0}, 'reshaped': {'line': 1, 'pixel': 1}}}}


def ledger_sha(folder):
    return hashlib.sha256((folder / 'ledger.json').read_bytes()).hexdigest()


def setup(tmp_path, doc=None, **kwargs):
    doc = doc or sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), **kwargs)
    return doc, folder, maps


def report(tmp_path, folder, maps, name='out', **kwargs):
    out = tmp_path / name
    code = ledger_report.main_ledger_report(str(folder), str(out), lambda record: None, maps_dir=None if maps is None else str(maps),
                                            **kwargs)
    return code, out


# ---- bands and bundle -------------------------------------------------------------------------------------------------------

def test_without_maps_dir_the_page_has_the_no_maps_band_and_empty_bundle_parts(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    code, out = report(tmp_path, folder, None)
    page = html_of(out)
    data = bundle_of(page)
    assert code == 0 and 'id="band-no-maps"' in page and data['maps'] is None and data['changes'] is None and data['cards'] == []
    assert 'id="tags-folded"' in section(page, 1) and 'id="locations"' in section(page, 1)
    assert 'ledger_maps_json_sha256' not in json.loads((out / 'report.json').read_text(encoding='utf-8'))


def test_maps_without_04_get_the_changes_band_and_fold_the_tag_axis(tmp_path):
    doc, folder, maps = setup(tmp_path)               # no `changes` key at all (an older ledger-maps run)
    code, out = report(tmp_path, folder, maps)
    page = html_of(out)
    assert code == 0 and 'id="band-no-maps"' not in page and 'id="band-changes"' in page and 'id="tags-folded"' in page
    data = bundle_of(page)
    assert data['changes'] == {'link_status': 'no_candidates'} and data['maps']['status'] == 'ok'
    report_json = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert report_json['ledger_maps_status'] == 'ok' and len(report_json['ledger_maps_json_sha256']) == 64


@pytest.mark.parametrize('status', ['stale', 'invalid', 'no_candidates'])
def test_each_04_status_has_its_band(tmp_path, status):
    changes = dict(ok_changes(), link_status=status, rois=[], pairs={})
    doc, folder, maps = setup(tmp_path, changes=changes)
    page = html_of(report(tmp_path, folder, maps)[1])
    assert 'id="band-changes"' in page and 'id="tags-folded"' in page and '변경 위치: 04 결과 없음' in page


def test_04_changes_feed_the_tag_axis_and_the_count_line_from_the_json_values(tmp_path):
    doc, folder, maps = setup(tmp_path, changes=ok_changes())
    page = html_of(report(tmp_path, folder, maps)[1])
    s1 = section(page, 1)
    assert 'id="band-changes"' not in page and 'id="tags"' in s1
    rows = re.findall(r'<tr data-href="#cat=tag:([^"]+)"><td class="l">([^<]*)</td><td>(\d+)</td><td>(\d+)</td><td>(\d+)</td></tr>', s1)
    assert rows == [('reshaped', '형상 변경', '1', '1', '2'), ('shifted', '위치 이동', '1', '0', '1')]
    assert 'Δ 를 더하면 중복' in s1
    assert re.search(r'변경 위치 <b>1곳</b>\(04\) · 위치에 연결된 쌍 <b>2</b> · 미연결 쌍 <b>2</b>', s1) and '분모 4 쌍' in s1
    data = bundle_of(page)
    assert data['changes']['summary']['roi_count'] == 1 and data['changes']['pairs']['P1']['roi_keys'] == ['roi:R1']
    assert data['changes']['rois'][0]['roi_key'] == 'roi:R1' and 'cases' not in data['changes']['rois'][0]


def test_partial_maps_get_a_band_and_the_status_is_in_report_json(tmp_path):
    doc, folder, maps = setup(tmp_path, status='partial')
    code, out = report(tmp_path, folder, maps)
    page = html_of(out)
    assert code == 0 and 'id="maps-partial"' in page and 'ledger_maps_value_check_failed' in page
    assert '부분 결과: <b>예</b>' in section(page, 1)
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['ledger_maps_status'] == 'partial'
    _doc, folder2, maps2 = setup(tmp_path / 'ok')
    assert 'maps-partial' not in html_of(report(tmp_path, folder2, maps2, name='ok_out')[1])


def test_cards_budget_reduced_is_a_band_and_a_trust_note(tmp_path):
    dropped = [{'kind': 'line', 'pair_id': 'P1', 'rank': 'L1', 'tabs': ['all'], 'stage': 'target', 'reason': 'card_reserve_failed'}]
    doc, folder, maps = setup(tmp_path, status='partial', dropped=dropped)
    page = html_of(report(tmp_path, folder, maps)[1])
    assert 'id="band-cards-budget"' in page and '카드 1 장이 빠졌습니다' in page and '카드 일부 없음' in section(page, 1)
    assert bundle_of(page)['maps']['cards_dropped'] == 1


def test_cards_in_the_bundle_keep_only_the_header(tmp_path):
    cards = [{'kind': 'line', 'pair_id': 'P1', 'rank': 'L1', 'tabs': ['same', 'all'], 'tab_ranks': {'same': 1, 'all': 1},
              'match': {'status': 'matched'}, 'rows': [{'id': 'A1'}] * 50}]
    doc, folder, maps = setup(tmp_path, cards=cards)
    data = bundle_of(html_of(report(tmp_path, folder, maps)[1]))
    assert data['cards'] == [{'pair_id': 'P1', 'kind': 'line', 'rank': 'L1', 'tabs': ['same', 'all'], 'tab_ranks': {'same': 1, 'all': 1},
                              'match': 'matched'}]


# ---- errors -----------------------------------------------------------------------------------------------------------------

def test_maps_made_from_another_ledger_exit_2_ledger_maps_stale(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    maps = write_maps(tmp_path, doc, 'f' * 64)
    code, out = report(tmp_path, folder, maps)
    assert code == 2
    error = json.loads((out / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'ledger_maps_stale' and error['exit_code'] == 2
    assert not (out / 'ledger_report.html').exists() and not (out / 'report.json').exists()


def test_stale_maps_remove_the_previous_report_of_the_folder(tmp_path):
    doc, folder, maps = setup(tmp_path)
    code, out = report(tmp_path, folder, maps)
    assert code == 0 and (out / 'ledger_report.html').is_file()
    stale = write_maps(tmp_path, doc, '0' * 64, name='stale')
    code2, _ = report(tmp_path, folder, stale)
    assert code2 == 2 and not (out / 'ledger_report.html').exists()


def test_ledger_maps_v1_exits_2_schema_mismatch_with_the_rerun_hint(tmp_path):
    doc, folder, maps = setup(tmp_path, schema='ledger_maps_v1')
    code, out = report(tmp_path, folder, maps)
    error = json.loads((out / 'error.json').read_text(encoding='utf-8'))
    assert code == 2 and error['code'] == 'ledger_maps_schema_mismatch' and '08 을 새 번들로 다시' in error['detail']
    assert not (out / 'ledger_report.html').exists()


@pytest.mark.parametrize('breaker,code_name', [('missing_json', 'ledger_maps_input_missing'), ('missing_png', 'ledger_maps_input_missing'),
                                               ('schema', 'ledger_maps_schema_mismatch'), ('bad_json', 'ledger_maps_input_missing'),
                                               ('path_name', 'ledger_maps_input_missing')])
def test_unusable_maps_folders_exit_2(tmp_path, breaker, code_name):
    doc, folder, maps = setup(tmp_path)
    if breaker == 'missing_json':
        (maps / 'ledger_maps.json').unlink()
    elif breaker == 'missing_png':
        next(maps.glob('map_reference_*_it.png')).unlink()
    elif breaker == 'schema':
        saved = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
        saved['schema_version'] = 'ledger_maps_v0'
        (maps / 'ledger_maps.json').write_text(json.dumps(saved), encoding='utf-8')
    elif breaker == 'path_name':
        saved = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
        saved['sides']['reference']['maps'][0]['file'] = '../ledger/ledger.json'
        (maps / 'ledger_maps.json').write_text(json.dumps(saved), encoding='utf-8')
    else:
        (maps / 'ledger_maps.json').write_text('{not json', encoding='utf-8')
    code, out = report(tmp_path, folder, maps)
    assert code == 2 and json.loads((out / 'error.json').read_text(encoding='utf-8'))['code'] == code_name


def test_cli_option_runs_the_report_with_maps_and_reports_stale_as_exit_2(tmp_path, capsys):
    doc, folder, maps = setup(tmp_path, changes=ok_changes())
    out = tmp_path / 'cli'
    assert main(['ledger-report', '--ledger-dir', str(folder), '--out', str(out), '--maps-dir', str(maps)]) == 0
    assert bundle_of(html_of(out))['changes']['link_status'] == 'ok'
    stale = write_maps(tmp_path, doc, 'e' * 64, name='stale')
    assert main(['ledger-report', '--ledger-dir', str(folder), '--out', str(tmp_path / 'cli2'), '--maps-dir', str(stale)]) == 2
    assert 'ledger_maps_stale' in (tmp_path / 'cli2' / 'error.json').read_text(encoding='utf-8')
    capsys.readouterr()


# ---- the 07 link ------------------------------------------------------------------------------------------------------------

def test_report07_link_is_relative_to_the_output_folder_and_missing_is_told(tmp_path):
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    seven = tmp_path / 'compare2_report'
    seven.mkdir()
    (seven / 'report.html').write_text('<html></html>', encoding='utf-8')
    out = tmp_path / 'data' / 'ledger_report'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, report07_dir=str(seven)) == 0
    page = html_of(out)
    assert 'id="link07-a" href="../../compare2_report/report.html"' in section(page, 5)
    data = bundle_of(page)
    assert data['report07'] == {'status': 'linked', 'href': '../../compare2_report/report.html'}
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['report07'] == 'linked'
    (seven / 'report.html').unlink()
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None, report07_dir=str(seven)) == 0
    page2 = html_of(out)
    assert 'id="link07-a"' not in page2 and '07 보고서 없음' in section(page2, 5) and bundle_of(page2)['report07']['status'] == 'missing'
    assert ledger_report.main_ledger_report(str(folder), str(out), lambda r: None) == 0
    assert '07 보고서 없음' in section(html_of(out), 5)


# ---- the real chain ---------------------------------------------------------------------------------------------------------

def test_real_chain_ledger_maps_feeds_the_report(tmp_path):
    from test_ledger_maps import chain, maps_args
    chain(tmp_path, dl1={'ref': {'A': {'L1': {0, 1}}, 'B': {'L1': {1, 2}}}, 'tgt': {'A': {'L1': {0, 1}}}})
    assert main(maps_args(tmp_path)) == 0
    out = tmp_path / 'rep'
    assert main(['ledger-report', '--ledger-dir', str(tmp_path / 'led'), '--out', str(out), '--maps-dir', str(tmp_path / 'maps')]) == 0
    page = html_of(out)
    data = bundle_of(page)
    saved = json.loads((tmp_path / 'maps' / 'ledger_maps.json').read_text(encoding='utf-8'))
    assert data['maps']['status'] == saved['status'] and data['changes']['link_status'] == saved['changes']['link_status']
    assert [c['pair_id'] for c in data['cards']] == [c['pair_id'] for c in saved['cards']]
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['ledger_maps_json_sha256'] == hashlib.sha256(
        (tmp_path / 'maps' / 'ledger_maps.json').read_bytes()).hexdigest()
    # a ledger rerun changes ledger.json's bytes only if its content changes; here the maps still match, so edit the ledger
    (tmp_path / 'led' / 'ledger.json').write_text((tmp_path / 'led' / 'ledger.json').read_text(encoding='utf-8') + '\n', encoding='utf-8')
    assert main(['ledger-report', '--ledger-dir', str(tmp_path / 'led'), '--out', str(tmp_path / 'rep2'), '--maps-dir',
                 str(tmp_path / 'maps')]) == 2
