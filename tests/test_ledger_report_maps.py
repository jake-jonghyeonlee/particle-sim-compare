"""lmap/T5: ledger-report section 2 (layout risk maps) from a ledger-maps folder (`--maps-dir`).

The maps folder is built by hand here (a ledger from test_ledger_report.make_ledger + number PNGs written with Pillow in the
`ledger_maps_v1` layout) so the report side is tested without a hotspot chain; one test runs the real
compare2 -> ledger -> ledger-maps -> ledger-report chain. Browser behaviour (canvas drawing, `#size`, `#demo`, `#print`) is not
run here: `test_synthetic_maps_html_for_browser_check` writes the page a headless browser can open."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from simcompare import ledger_report
from simcompare.cli import main
from test_ledger_report import html_of, make_ledger, sample_doc, spec, write_ledger

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


def write_maps(tmp, doc, ledger_sha, name='maps', status='ok', top_pairs=None, tamper=None, cards=None, bands=None):
    """Item picture + electrode picture per layer and product, ledger_maps.json. tamper = (role, file): that PNG gets a
    changed pixel number (the JSON keeps the real counts), like a browser that alters colours."""
    out = tmp / name
    out.mkdir(parents=True, exist_ok=True)
    electrodes = sorted({e for p in doc['pairs'] for e in p['electrodes']})
    sides = {}
    for role, key, present in ROLES:
        items, layers = [], []
        for pair in doc['pairs']:
            for item in pair['items']:
                if item['presence'] in present:
                    items.append((pair, item))
                    if item['analysis_layer'] not in layers:
                        layers.append(item['analysis_layer'])
        layers.sort()
        maps, rows = [], []
        for index, layer in enumerate(layers, 1):
            num = np.zeros((30, 60), dtype=np.uint16)
            for number, (pair, item) in enumerate(items, 1):
                if item['analysis_layer'] == layer:
                    num[3:20, 2 + 6 * (number - 1):2 + 6 * (number - 1) + 5] = number
            el = np.zeros((30, 60), dtype=np.uint16)
            for k, name_ in enumerate(electrodes, 1):
                el[22:28, 2 + 6 * (k - 1):2 + 6 * (k - 1) + 5] = k
            for kind, array in (('it', num), ('el', el)):
                file = 'map_%s_%03d_%s.png' % (role, index, kind)
                shown = array.copy()
                if tamper == (role, file):
                    shown[shown == shown.max()] = shown.max() + 1
                (out / file).write_bytes(png_of(shown))
                maps.append({'file': file, 'layer': layer, 'kind': kind, 'px_um': 0.2, 'shape_yx': [30, 60], 'origin_px': [0, 0],
                             'numbers': counts_of(array)})
        for number, (pair, item) in enumerate(items, 1):
            rows.append({'item_id': item['item_id'], 'pair_id': pair['pair_id'], 'kind': item['class'],
                         'layers': [item['analysis_layer']], 'upper_layer': item['upper_layer'], 'lower_layer': item['lower_layer'],
                         'number': number, 'value': item[key], 'tile_sum': item[key], 'has_map_area': True, 'hidden_px': 0,
                         'status': 'pending' if item[key] is None else 'ok'})
        sides[role] = {'items': rows, 'maps': maps, 'frame': {'shape_yx': [30, 60], 'origin_um': [0.0, 0.0], 'pitch_um': 0.2},
                       'electrodes': [{'number': k, 'name': n, 'layers': {}} for k, n in enumerate(electrodes, 1)],
                       'hidden': {'item_px': 3, 'electrode_overlap_px': 0}}
    options = {'tile_um': 1.0}
    if top_pairs is not None:
        options['top_pairs'] = top_pairs
    document = {'schema_version': 'ledger_maps_v1', 'status': status,
                'reasons': ['ledger_maps_value_check_failed'] if status == 'partial' else [],
                'inputs': {'ledger_json_sha256': ledger_sha}, 'options': options, 'report': {'diameter_um': 5.0},
                'sides': sides, 'cards': cards or [], 'scale_steps': [], 'bands': bands or []}
    (out / 'ledger_maps.json').write_text(json.dumps(document, sort_keys=True), encoding='utf-8')
    return out


def crow(ident, side, rect, pair, total, others=None, edge='none', typ='linear', match=None, edge_reason=None):
    """A `cards[].rows[]` entry as ledger-maps writes it (README 22); an excluded row has no values."""
    excluded = edge == 'excluded'
    return {'id': ident, 'side': side, 'type': typ, 'rect': rect, 'rect_tiles': [0, 0, 1, 1], 'edge': edge, 'edge_reason': edge_reason,
            'shift_tiles': None, 'image_tiles': 0, 'image_shifts': [], 'edge_rounding_um': 0.0, 'tiles': 10, 'tiles_lost': 0,
            'pair': None if excluded else pair, 'range_total': None if excluded else total,
            'others': None if excluded else (others or {'top': [], 'others_n': 0, 'others_value': 0.0}),
            'match': match or {'status': 'only', 'reason': None}}


def sample_cards():
    """Three cards on the sample ledger: P1 (line: one matched row pair, an excluded A row, a B remainder), P3 (pixel, no 04: rows
    stay on their own product) and P5 (new pair: only B)."""
    top = {'top': [{'pair_id': 'P2', 'value': 0.0011}, {'pair_id': 'P4', 'value': 0.0002}], 'others_n': 2, 'others_value': 0.00005}
    p1 = {'kind': 'line', 'pair_id': 'P1', 'rank': 'L1', 'delta': 0.003, 'a': 0.0120, 'b': 0.0150, 'change_kind': 'same_pair_change',
          'rows': [crow('A1', 'reference', [0.4, 0.6, 1.6, 4.0], 0.0050, 0.0061, top,
                        match={'status': 'matched', 'reason': None, 'counterpart': 'B1', 'overlap': 1.0, 'offset_px': [0, 0]}),
                   crow('A2', 'reference', [3.0, 0.6, 4.0, 4.0], 0.0, 0.0, edge='excluded', edge_reason='edge_no_dl1',
                        match={'status': 'only', 'reason': 'edge_no_dl1'}),
                   crow('B1', 'target', [0.4, 0.6, 1.6, 4.0], 0.0070, 0.0082, top, match={'status': 'matched', 'reason': None, 'with_a': 1,
                                                                                         'counterpart': 'A1'}),
                   dict(crow('B2', 'target', [2.2, 0.6, 3.4, 4.0], 0.0030, 0.0030, edge='repeat_used',
                             match={'status': 'only', 'reason': None, 'partial_matched': True}),
                        image_tiles=4, image_shifts=[[-40, 0]])],
          'match': {'status': 'matched', 'reason': None, 'matched_rows': 1, 'absorbed_b_rows': [],
                    'not_listed': {'reference': {'n': 3, 'value': 0.0004}, 'target': {'n': 0, 'value': 0.0}}},
          'observed': {'reference': 0.0098, 'target': 0.0120}, 'section4_ref': {'reference': 0.0120, 'target': 0.0150},
          'rows_sum': {'reference': 0.0050, 'target': 0.0100}, 'rounding_error_um': 0.0}
    p3 = {'kind': 'pixel', 'pair_id': 'P3', 'rank': 'P1', 'delta': 0.0, 'a': 0.0200, 'b': 0.0200, 'change_kind': 'no_change',
          'rows': [crow('A1', 'reference', [0.4, 0.6, 1.6, 4.0], 0.0200, 0.0210, typ='point'),
                   crow('B1', 'target', [0.4, 0.6, 1.6, 4.0], 0.0200, 0.0210, typ='point')],
          'match': {'status': 'not_compared', 'reason': 'coords_no_candidates', 'matched_rows': 0, 'absorbed_b_rows': [],
                    'not_listed': {'reference': {'n': 0, 'value': 0.0}, 'target': {'n': 0, 'value': 0.0}}},
          'observed': {'reference': 0.0200, 'target': 0.0200}, 'section4_ref': {'reference': 0.0200, 'target': 0.0200},
          'rows_sum': {'reference': 0.0200, 'target': 0.0200}, 'rounding_error_um': 0.0}
    p5 = {'kind': 'line', 'pair_id': 'P5', 'rank': 'L3', 'delta': 0.0065, 'a': 0.0, 'b': 0.0065, 'change_kind': 'new_pair',
          'rows': [crow('B1', 'target', [0.4, 0.6, 1.6, 4.0], 0.0065, 0.0065)],
          'match': {'status': 'b_only', 'reason': None, 'matched_rows': 0, 'absorbed_b_rows': [],
                    'not_listed': {'reference': {'n': 0, 'value': 0.0}, 'target': {'n': 0, 'value': 0.0}}},
          'observed': {'reference': 0.0, 'target': 0.0065}, 'section4_ref': {'reference': 0.0, 'target': 0.0065},
          'rows_sum': {'reference': 0.0, 'target': 0.0065}, 'rounding_error_um': 0.0}
    return [p1, p3, p5]


def section5(page):
    return page[page.index('<section id="s5">'):page.index('<section id="s6">')]


def card_page(tmp_path, cards=None, bands=None, doc=None, edit=None):
    doc = doc or sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), cards=sample_cards() if cards is None else cards, bands=bands)
    if edit is not None:
        saved = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
        edit(saved)
        (maps / 'ledger_maps.json').write_text(json.dumps(saved, sort_keys=True), encoding='utf-8')
    code, out = report(tmp_path, folder, maps)
    assert code == 0
    return html_of(out)


def card_part(page, pair_id):
    start = page.index('<details class="card" id="card-%s"' % pair_id)
    return page[start:page.index('</details>', start)]


def text_of(fragment):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', fragment)).strip()


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


def payload(page):
    return json.loads(re.search(r'<script type="application/json" id="maps-data">(.*?)</script>', page, re.S).group(1))


def section2(page):
    return page[page.index('<section id="s2">'):page.index('<section id="s3">')]


# ---- without --maps-dir -----------------------------------------------------------------------------------------------

# sha256 of the output of 1ce685b (before --maps-dir existed) for sample_doc(); update only if that output changes on purpose
GOLDEN_HTML = '984cba94ce7c2a8fd99833cdb438e96e02694a6661d86c1185eb1ddc144e8fa2'
GOLDEN_REPORT = 'f6fa7ae466b54e5e0244dd1227551be7cf0ce4d934dca80d984363684098cfed'


def test_without_maps_dir_section_2_and_5_stay_placeholders(tmp_path):
    folder = write_ledger(tmp_path / 'ledger', sample_doc())
    code, out = report(tmp_path, folder, None)
    assert code == 0
    page = html_of(out)
    assert 'maps-data' not in page and '<canvas' not in page and 'data-map-pair' not in page
    assert section2(page).count('다음 단계에서 추가됩니다') == 1
    assert page.count('class="lead placeholder"') == 2
    saved = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert 'ledger_maps_json_sha256' not in saved and saved['sections'] == {'present': [1, 3, 4, 6, 7], 'placeholders': [2, 5]}


def test_without_maps_dir_output_is_byte_identical_to_the_one_before_the_option(tmp_path):
    folder = write_ledger(tmp_path / 'ledger', sample_doc())
    code, out = report(tmp_path, folder, None)
    assert code == 0
    html_sha = hashlib.sha256((out / 'ledger_report.html').read_bytes()).hexdigest()
    report_sha = hashlib.sha256((out / 'report.json').read_bytes()).hexdigest()
    assert html_sha == GOLDEN_HTML and report_sha == GOLDEN_REPORT


# ---- with --maps-dir --------------------------------------------------------------------------------------------------

def test_maps_section_has_both_canvases_two_views_layer_chips_and_no_external_url(tmp_path):
    doc, folder, maps = setup(tmp_path)
    code, out = report(tmp_path, folder, maps)
    assert code == 0
    page = html_of(out)
    s2 = section2(page)
    assert 'id="mapA"' in s2 and 'id="mapB"' in s2 and s2.count('<canvas') == 2
    assert 'data-mode="change"' in s2 and 'data-mode="size"' in s2 and s2.count('data-mode=') == 2
    layers = sorted({i['analysis_layer'] for p in doc['pairs'] for i in p['items']})
    assert all(('data-layer="%s"' % layer) in s2 for layer in layers) and 'data-layer=""' in s2
    assert 'id="esearch"' in s2 and 'id="noteA"' in s2 and 'id="noteB"' in s2 and 'id="legend-change"' in s2
    assert '이 제품에 없음' in s2                                          # P5 is new: the list says A has no map for it
    assert 'CNO 는 지도가 없어 이 목록에 없습니다' in s2
    assert not re.search(r'(?:src|href|action)\s*=\s*["\']?\s*(?:https?:)?//', page, re.I)
    assert 'http://' not in page.replace('http://www.w3.org', '') and 'https://' not in page
    assert '<img' not in page and '@import' not in page and 'url(' not in page
    assert page.count('class="lead placeholder"') == 0 and 'id="s5"' in page         # section 5 is the location cards (T6)
    # no selection at the start: the script only selects on #demo
    assert "location.hash==='#demo'" in page and "location.hash==='#size'" in page and "location.hash==='#print'" in page
    assert "addEventListener('beforeprint'" in page


def test_png_count_matches_maps_and_numbers_are_in_the_page_for_the_self_check(tmp_path):
    doc, folder, maps = setup(tmp_path)
    code, out = report(tmp_path, folder, maps)
    assert code == 0
    data = payload(html_of(out))
    saved = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
    total = 0
    for role, key in (('reference', 'A'), ('target', 'B')):
        shown = data['sides'][key]['maps']
        wanted = sorted(saved['sides'][role]['maps'], key=lambda m: m['file'])
        assert [m['file'] for m in shown] == [m['file'] for m in wanted]
        for m, want in zip(shown, wanted):
            assert m['numbers'] == want['numbers'] and m['w'] == want['shape_yx'][1] and m['h'] == want['shape_yx'][0]
            raw = base64.b64decode(m['png'].split(',', 1)[1])
            assert raw == (maps / m['file']).read_bytes()                  # the page carries the PNG bytes as they are
            array = np.asarray(Image.open(io.BytesIO(raw)))
            num = array[..., 0].astype(np.int64) | (array[..., 1].astype(np.int64) << 8)
            assert counts_of(num.astype(np.uint16)) == m['numbers']      # what the in-page self-check recomputes
            total += 1
    assert total > 0 and html_of(out).count('data:image/png;base64,') == total


def test_a_picture_whose_numbers_changed_disagrees_with_the_counts_the_page_carries(tmp_path):
    """What the in-page self-check detects: the PNG counts differ from `numbers` (here made by hand, like a browser that
    changes colours would see)."""
    doc = sample_doc()
    folder = write_ledger(tmp_path / 'ledger', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder), tamper=('reference', 'map_reference_001_it.png'))
    code, out = report(tmp_path, folder, maps)
    assert code == 0                         # the report does not look into the pixels; the browser does at load
    data = payload(html_of(out))
    entry = [m for m in data['sides']['A']['maps'] if m['file'] == 'map_reference_001_it.png'][0]
    array = np.asarray(Image.open(io.BytesIO(base64.b64decode(entry['png'].split(',', 1)[1]))))
    num = (array[..., 0].astype(np.int64) | (array[..., 1].astype(np.int64) << 8)).astype(np.uint16)
    assert counts_of(num) != entry['numbers']


def test_report_json_records_the_maps_sha_and_the_page_is_deterministic(tmp_path):
    doc, folder, maps = setup(tmp_path)
    code, out = report(tmp_path, folder, maps)
    assert code == 0
    saved = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert saved['ledger_maps_json_sha256'] == hashlib.sha256((maps / 'ledger_maps.json').read_bytes()).hexdigest()
    assert saved['ledger_json_sha256'] == ledger_sha(folder) and saved['ledger_maps_status'] == 'ok'
    assert saved['sections'] == {'present': [1, 2, 3, 4, 5, 6, 7], 'placeholders': []}
    code2, out2 = report(tmp_path, folder, maps, name='out2')
    assert code2 == 0 and html_of(out) == html_of(out2)
    execution = json.loads((out / 'execution.json').read_text(encoding='utf-8'))
    assert execution['maps_dir'] == str(maps)


def test_risk_list_numbers_are_the_ledger_numbers_and_cno_is_not_listed(tmp_path):
    doc, folder, maps = setup(tmp_path)
    code, out = report(tmp_path, folder, maps)
    page = html_of(out)
    s2 = section2(page)
    rows = re.findall(r'<tr data-sel-p="([^"]*)" tabindex="0"><td>([LP]\d+)</td><td class="l"><b>([^<]*)</b>', s2)
    by_id = {p['pair_id']: p for p in doc['pairs']}
    assert {r[0] for r in rows} == set(by_id)
    for pair_id, rank, name in rows:
        assert rank == by_id[pair_id]['rank'] and name == '–'.join(by_id[pair_id]['electrodes'])
        assert ('id="pair-%s"' % rank) in page                            # the same number is in section 4
    assert 'Anode' in s2 and s2.count('data-sel-p=') == len(by_id)
    data = payload(page)
    assert {p['id']: p['no'] for p in data['pairs']} == {k: v['rank'] for k, v in by_id.items()}
    assert all(p['top'] for p in data['pairs'])
    # the map payload carries the ledger values only as text and as the map shade (no new number is computed)
    p1 = [p for p in data['pairs'] if p['id'] == 'P1'][0]
    assert p1['a'] == 0.0120 and p1['b'] == 0.0150 and p1['ck'] == 'same_pair_change'


def test_top_pairs_of_ledger_maps_limits_the_list_and_the_labels(tmp_path):
    doc, folder, maps = setup(tmp_path, top_pairs=1)
    code, out = report(tmp_path, folder, maps)
    page = html_of(out)
    rows = re.findall(r'<tr data-sel-p="([^"]*)"', section2(page))
    assert len(rows) == 2                                                  # one line and one pixel pair
    data = payload(page)
    assert sum(1 for p in data['pairs'] if p['top']) == 2 and len(data['pairs']) == 5


def test_pair_rows_in_section_4_link_to_the_map_only_with_maps(tmp_path):
    doc, folder, maps = setup(tmp_path)
    with_maps = html_of(report(tmp_path, folder, maps)[1])
    without = html_of(report(tmp_path, folder, None, name='plain')[1])
    assert with_maps.count('data-map-pair=') == len(doc['pairs']) and 'data-map-pair' not in without
    assert 'href="#s2" data-map-pair="P1"' in with_maps and 'pair-L1' in with_maps


def test_item_values_have_a_shade_and_missing_sides_are_told(tmp_path):
    doc, folder, maps = setup(tmp_path)
    data = payload(html_of(report(tmp_path, folder, maps)[1]))
    items_a = {i['p']: i for i in data['sides']['A']['items']}
    items_b = {i['p']: i for i in data['sides']['B']['items']}
    assert 'P5' not in items_a and 'P5' in items_b                         # P5 is new: only B has it
    assert items_b['P5']['pct'] is not None and data['max_pct']['line'] > 0 and data['max_pct']['pixel'] > 0
    assert data['electrodes']['Scan2'] == [True, True]
    assert [p for p in data['pairs'] if p['id'] == 'P5'][0]['pa'] is False


def test_an_item_without_a_map_area_is_told_in_the_list(tmp_path):
    doc, folder, maps = setup(tmp_path)
    saved = json.loads((maps / 'ledger_maps.json').read_text(encoding='utf-8'))
    saved['sides']['reference']['items'][0]['status'] = 'no_map_area'
    saved['sides']['reference']['items'][0]['has_map_area'] = False
    (maps / 'ledger_maps.json').write_text(json.dumps(saved, sort_keys=True), encoding='utf-8')
    code, out = report(tmp_path, folder, maps)
    assert code == 0 and '지도 영역 없음' in section2(html_of(out))


def test_names_with_markup_are_escaped_in_the_map_section(tmp_path):
    pairs = [spec('P1', ['<b>X', 'Y&"'], 'line', 0.01, 0.02)]
    doc = make_ledger(pairs)
    folder = write_ledger(tmp_path / 'ledger', doc)
    maps = write_maps(tmp_path, doc, ledger_sha(folder))
    code, out = report(tmp_path, folder, maps)
    page = html_of(out)
    assert code == 0 and '<b>X' not in page and '&lt;b&gt;X' in section2(page)
    assert '</script><' not in page.replace('</script></body>', '').replace('</script><script', '')
    assert payload(page)['pairs'][0]['e'] == ['<b>X', 'Y&"']


def test_partial_maps_get_a_band_and_the_status_is_in_report_json(tmp_path):
    doc, folder, maps = setup(tmp_path, status='partial')
    code, out = report(tmp_path, folder, maps)
    page = html_of(out)
    assert code == 0 and 'id="maps-partial"' in page and 'ledger_maps_value_check_failed' in page
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['ledger_maps_status'] == 'partial'
    _doc, folder2, maps2 = setup(tmp_path / 'ok')
    assert 'maps-partial' not in html_of(report(tmp_path, folder2, maps2, name='ok_out')[1])


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


@pytest.mark.parametrize('breaker,code_name', [('missing_json', 'ledger_maps_input_missing'), ('missing_png', 'ledger_maps_input_missing'),
                                               ('schema', 'ledger_maps_schema_mismatch'), ('bad_json', 'ledger_maps_input_missing')])
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
    else:
        (maps / 'ledger_maps.json').write_text('{not json', encoding='utf-8')
    code, out = report(tmp_path, folder, maps)
    assert code == 2 and json.loads((out / 'error.json').read_text(encoding='utf-8'))['code'] == code_name


def test_cli_option_runs_the_report_with_maps_and_reports_stale_as_exit_2(tmp_path, capsys):
    doc, folder, maps = setup(tmp_path)
    out = tmp_path / 'cli'
    assert main(['ledger-report', '--ledger-dir', str(folder), '--out', str(out), '--maps-dir', str(maps)]) == 0
    assert '<canvas' in html_of(out)
    stale = write_maps(tmp_path, doc, 'e' * 64, name='stale')
    assert main(['ledger-report', '--ledger-dir', str(folder), '--out', str(tmp_path / 'cli2'), '--maps-dir', str(stale)]) == 2
    assert 'ledger_maps_stale' in (tmp_path / 'cli2' / 'error.json').read_text(encoding='utf-8')
    capsys.readouterr()


# ---- the script ---------------------------------------------------------------------------------------------------------

def test_script_has_the_self_check_and_the_selection_links(tmp_path):
    doc, folder, maps = setup(tmp_path)
    page = html_of(report(tmp_path, folder, maps)[1])
    assert 'function check(' in page and 'm.meta.numbers' in page and 'data-map-check' in page      # number counts vs JSON
    assert 'id="mapwarn"' in page and 'hidden' in section2(page)
    assert "'--surface-0'" in page and 'getImageData' in page and 'beforeprint' in page
    # ES2017 or lower: none of the later syntax
    script = page[page.rindex('<script>'):page.rindex('</script>')]
    assert not re.search(r'\?\.[^0-9]', script) and '??' not in script and '**' not in script and '...' not in script
    for later in ('BigInt', 'flatMap', 'Object.fromEntries', 'matchAll', 'finally(', 'async ', 'await '):
        assert later not in script, later


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_script_parses(tmp_path):
    out = tmp_path / 'check.js'
    out.write_text(ledger_report.MAPS_SCRIPT, encoding='utf-8')
    result = subprocess.run(['node', '--check', str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_synthetic_maps_html_for_browser_check(tmp_path):
    """Writes the page a headless browser can open: file://<path>#size, #demo, #print (section 5 cards included).
    Set LEDGER_MAPS_HTML_OUT to keep it."""
    doc, folder, maps = setup(tmp_path, cards=sample_cards(), bands=[{'code': 'coords_no_candidates', 'detail': 'no --candidates'}])
    code, out = report(tmp_path, folder, maps)
    assert code == 0
    page = html_of(out)
    target = os.environ.get('LEDGER_MAPS_HTML_OUT')
    if target:
        Path(target).write_text(page, encoding='utf-8')
    print('synthetic maps report: %d bytes' % len(page.encode('utf-8')))
    assert len(page.encode('utf-8')) > 20000 and page.count('<details class="card"') == 3 and page.count('class="cardmap"') >= 4


# ---- section 5: location cards ------------------------------------------------------------------------------------------

def money(doc, kind, value):
    """What the report shows for a value: the % of vi_A and the raw value."""
    vi_a = doc['kinds'][kind]['vi_a']
    return ledger_report.pct_txt(ledger_report.pct_of(value, vi_a)), ledger_report.raw_pct(value)


def test_cards_follow_the_json_order_with_header_values_like_section_4(tmp_path):
    doc = sample_doc()
    page = card_page(tmp_path, doc=doc)
    s5 = section5(page)
    assert [m for m in re.findall(r'<details class="card" id="card-(\w+)"', s5)] == ['P1', 'P3', 'P5']
    head = text_of(card_part(page, 'P1')[:card_part(page, 'P1').index('</summary>')])
    pct_a, raw_a = money(doc, 'line', 0.0120)
    pct_b, raw_b = money(doc, 'line', 0.0150)
    assert 'L1 Data–ELVDD' in head and '같은 쌍 증감' in head and '같은 좌표 비교' in head
    assert 'A %s %s → B %s %s' % (pct_a, raw_a, pct_b, raw_b) in head
    assert 'id="pair-L1"' in page and 'href="#pair-L1"' in card_part(page, 'P1')           # the link to section 4
    assert s5.count('<details class="card"') == 3 and ' open' in card_part(page, 'P1')[:90]


def test_card_numbers_are_the_json_values_and_a_wrong_sum_is_shown_as_it_is(tmp_path):
    doc = sample_doc()

    def break_sum(saved):
        saved['cards'][0]['rows_sum']['reference'] = 0.0777
        saved['cards'][0]['observed']['target'] = 0.0888
    page = card_page(tmp_path, doc=doc, edit=break_sum)
    totals = text_of(card_part(page, 'P1')[card_part(page, 'P1').index('<h3>합계</h3>'):])
    pct, raw = money(doc, 'line', 0.0777)
    assert '범위 안 합 (줄의 이 쌍 값을 더한 값) %s %s' % (pct, raw) in totals          # not 0.0050 + ...: shown as written
    pct, raw = money(doc, 'line', 0.0888)
    assert '%s %s 42.9%%' % (pct, raw) not in totals and ('%s %s' % (pct, raw)) in totals
    ref_pct, ref_raw = money(doc, 'line', 0.0120)
    assert '4절 쌍 값 (참고) %s %s' % (ref_pct, ref_raw) in totals and 'unit 한 장 기준이라 4절 값과 다릅니다' in totals
    # the untouched card shows the stored values
    good = card_page(tmp_path / 'good', doc=doc)
    part = text_of(card_part(good, 'P1')[card_part(good, 'P1').index('<h3>합계</h3>'):])
    pct, raw = money(doc, 'line', 0.0050)
    assert '범위 안 합 (줄의 이 쌍 값을 더한 값) %s %s' % (pct, raw) in part


def test_matched_rows_and_the_rest_are_in_different_tables_with_the_row_values(tmp_path):
    doc = sample_doc()
    page = card_page(tmp_path, doc=doc)
    part = card_part(page, 'P1')
    same = part[part.index('<h3>같은 좌표 비교'):part.index('<h3>있는 쪽 값만')]
    only = part[part.index('<h3>있는 쪽 값만'):part.index('<h3>합계</h3>')]
    same_text, only_text = text_of(same), text_of(only)
    assert 'A1 ↔ B1' in same_text and '구간' in same_text and 'A2' not in same_text and 'B2' not in same_text
    for value in (0.0050, 0.0070, 0.0061, 0.0082):
        pct, raw = money(doc, 'line', value)
        assert '%s %s' % (pct, raw) in same_text
    delta_pct = ledger_report.spct(ledger_report.pct_of(0.0070 - 0.0050, doc['kinds']['line']['vi_a']))
    assert delta_pct in same_text                                                   # the row change, displayed like section 4
    assert 'Scan1–EM' in same_text and '외 2 쌍' in same_text                       # other pairs: top rows + "외 k 쌍"
    assert 'A1' not in only_text and 'B1' not in only_text
    assert 'A2' in only_text and 'B2' in only_text and '대응 부분 제외' in only_text and '안쪽 반복 사용' in only_text
    assert '비교 제외' in only_text and 'DL1 자료가 없어' in only_text
    assert '그 밖 A 3 곳' in text_of(part)                                          # match.not_listed
    # a card without any match keeps every row on its own product with the band reason
    p3 = text_of(card_part(page, 'P3'))
    assert '같은 좌표로 대응된 줄이 없습니다' in p3 and '좌표를 맞추지 않았습니다' in p3
    only3 = card_part(page, 'P3')
    only3 = only3[only3.index('<h3>있는 쪽 값만'):only3.index('<h3>합계</h3>')]
    assert ('<td class="l rowid">A1</td><td class="l">A</td>' in only3 and '<td class="l rowid">B1</td><td class="l">B</td>' in only3)


def test_excluded_rows_say_so_and_have_no_values(tmp_path):
    page = card_page(tmp_path)
    only = card_part(page, 'P1')
    only = only[only.index('<h3>있는 쪽 값만'):only.index('<h3>합계</h3>')]
    row = re.search(r'<tr><td class="l rowid">A2</td>.*?</tr>', only, re.S).group(0)
    assert text_of(row).count('비교 제외') >= 3 and '%' not in text_of(row)           # value, other pairs and totals say so
    payload_cards = payload(page)['cards']
    rows = {r['id']: r for r in payload_cards[0]['sides']['A']['rows']}
    assert rows['A2']['e'] == 'excluded' and rows['A1']['e'] == 'none'


def test_repeat_used_row_says_how_many_inside_repeat_tiles_its_value_includes(tmp_path):
    page = card_page(tmp_path)
    part = card_part(page, 'P1')
    assert '안쪽 반복 타일 4개 포함' in text_of(part[part.index('<h3>있는 쪽 값만'):part.index('<h3>합계</h3>')])
    rows = {r['id']: r for r in payload(page)['cards'][0]['sides']['B']['rows']}
    assert rows['B2']['n'] == 4 and rows['B1']['n'] == 0
    assert '안쪽 반복 타일 \'+rw.n+\'개 포함' in page                                # the label drawn beside the row on the canvas


def test_bands_become_one_korean_line_each_and_unknown_codes_stay_as_codes(tmp_path):
    bands = [{'code': 'coords_not_applied', 'detail': 'apply_align=diagnostic'}, {'code': 'coords_stale', 'detail': 'frame'},
             {'code': 'coords_unavailable', 'detail': 'x'}, {'code': 'coords_no_candidates', 'detail': 'y'},
             {'code': 'coords_resolution_too_coarse', 'detail': 'z'}, {'code': 'coords_new_code', 'detail': 'future'}]
    s5 = section5(card_page(tmp_path, bands=bands))
    assert s5.count('data-band=') == 6
    for code in ('coords_not_applied', 'coords_stale', 'coords_unavailable', 'coords_no_candidates', 'coords_resolution_too_coarse'):
        assert code in ledger_report.BAND_TEXT and 'data-band="%s"' % code in s5
    assert '진단 모드' in s5 and '지문이 맞지 않음' in s5 and '분석 타일이 대응 허용치보다 커서' in s5
    assert re.search(r'data-band="coords_new_code">coords_new_code', s5)
    assert s5.index('data-band=') < s5.index('<details class="card"')                # the bands are at the top of section 5
    assert '07 위치 Δvi' in s5


def test_waiting_absent_and_zero_are_shown_differently(tmp_path):
    # P1 has no A value yet (null): 대기. P5 does not exist in A: 없음. A zero value stays 0%.
    pairs = [spec('P1', ['Data', 'ELVDD'], 'line', None, 0.0150), spec('P5', ['Scan2', 'Vint'], 'line', 0.0, 0.0065, present_a=False,
                                                                       change='new_pair'),
             spec('P6', ['Aa', 'Bb'], 'line', 0.0, 0.0, change='no_change')]
    doc = make_ledger(pairs)
    cards = sample_cards()[:1] + sample_cards()[2:]
    cards[0].update({'a': None, 'delta': None})
    zero = json.loads(json.dumps(cards[0]))
    zero.update({'pair_id': 'P6', 'rank': 'L3', 'a': 0.0, 'b': 0.0, 'delta': 0.0, 'change_kind': 'no_change'})
    for row in zero['rows']:
        if row['edge'] != 'excluded':
            row['pair'] = 0.0
    page = card_page(tmp_path, cards=cards, doc=doc)
    p1, p5 = (text_of(card_part(page, pid)) for pid in ('P1', 'P5'))
    assert 'A 대기 → B' in p1 and 'A 0%' not in p1 and '범위 안 합 (줄의 이 쌍 값을 더한 값) 대기' in p1
    assert '없음' in p5 and 'A 없음' in p5 and '대기' not in p5
    # a real zero (vi_A is not null here, so the percent is defined): 0%
    pairs2 = [spec('P6', ['Aa', 'Bb'], 'line', 0.0, 0.0, change='no_change'), spec('P7', ['Cc', 'Dd'], 'line', 0.01, 0.02)]
    zero['pair_id'] = 'P6'
    page2 = card_page(tmp_path / 'zero', cards=[zero], doc=make_ledger(pairs2))
    p6 = text_of(card_part(page2, 'P6'))
    assert 'A 0% → B 0%' in p6 and '대기' not in p6.split('같은 좌표 비교')[0]


def test_print_opens_every_card_and_draws_the_card_canvases_again(tmp_path):
    page = card_page(tmp_path)
    assert page.count('<details class="card"') == 3
    assert "if(location.hash==='#print'){openAll();}" in page                       # every <details> opens, cards included
    assert "document.querySelectorAll('details.card')" in page and 'drawCards();' in page
    before = page[page.index("window.addEventListener('beforeprint',function(){var ds"):]
    assert before.index('d.open=true') < before.index('drawAll();drawCards();') if 'd.open=true' in before else 'ds[i].open=true' in before[:200]
    assert "window.addEventListener('load',function(){drawAll();drawCards();})" in page
    assert 'data-card-drawn' in page and 'canvas.cardmap' in page and 'break-inside:auto' in page


def test_card_links_in_sections_2_and_4_only_for_pairs_with_a_card(tmp_path):
    page = card_page(tmp_path)
    s4 = page[page.index('<section id="s4">'):page.index('<section id="s5">')]
    s2 = section2(page)
    for pair_id in ('P1', 'P3', 'P5'):
        assert ('href="#card-%s"' % pair_id) in s4 and ('href="#card-%s"' % pair_id) in s2
        assert 'id="card-%s"' % pair_id in page
    for pair_id in ('P2', 'P4'):
        assert ('href="#card-%s"' % pair_id) not in page
    plain = card_page(tmp_path / 'nocards', cards=[])
    assert 'href="#card-' not in plain and '카드가 없습니다' in section5(plain)


def test_card_payload_has_crops_inside_the_map_and_no_external_request(tmp_path):
    page = card_page(tmp_path)
    cards = payload(page)['cards']
    assert [c['p'] for c in cards] == ['P1', 'P3', 'P5']
    for card in cards:
        for key, side in card['sides'].items():
            x0, y0, x1, y1 = side['vb']
            assert 0 <= x0 < x1 <= 60 and 0 <= y0 < y1 <= 30 and 1 <= side['z'] <= 8
    assert 'A' not in cards[2]['sides'] and 'B' in cards[2]['sides']                 # P5 is not in A
    assert 'id="cv-0-A"' in page and 'id="cv-0-B"' in page and 'id="cv-2-A"' not in page
    assert not re.search(r'(?:src|href|action)\s*=\s*["\']?\s*(?:https?:)?//', page, re.I)
    assert 'http://' not in page.replace('http://www.w3.org', '') and 'https://' not in page and 'url(' not in page and '<img' not in page


def test_card_script_stays_inside_es2017_and_parses(tmp_path):
    script = ledger_report.MAPS_SCRIPT
    for needle in ('function drawCards(', 'function openCard(', 'cv-', "closest('a[href^=\"#card-\"]')"):
        assert needle in script
    assert not re.search(r'\?\.[^0-9]', script) and '??' not in script and '**' not in script and '...' not in script
    if shutil.which('node'):
        out = tmp_path / 'cards.js'
        out.write_text(script, encoding='utf-8')
        assert subprocess.run(['node', '--check', str(out)], capture_output=True, text=True).returncode == 0


def test_html_size_of_500_pairs_and_10_cards(tmp_path):
    """Records the page size (no bound is asserted); the PNGs of a real run dominate it (--max-map-bytes 12 MB, base64 x 4/3)."""
    pairs = [spec('P%d' % i, ['E%da' % i, 'E%db' % i], 'line' if i % 2 else 'pixel', 0.0001 * (1 + i % 7), 0.0001 * (2 + i % 5),
                  layer='L%d' % (i % 3)) for i in range(1, 501)]
    doc = make_ledger(pairs)
    cards = []
    for i, pair in enumerate(doc['pairs'][:10]):
        base = json.loads(json.dumps(sample_cards()[0]))
        base.update({'pair_id': pair['pair_id'], 'rank': pair['rank'], 'kind': pair['kind'], 'a': pair['a'], 'b': pair['b'],
                     'delta': pair['delta'], 'change_kind': pair['change_kind']})
        cards.append(base)
    folder = write_ledger(tmp_path / 'ledger', doc)
    with_cards = write_maps(tmp_path, doc, ledger_sha(folder), cards=cards)
    without = write_maps(tmp_path, doc, ledger_sha(folder), name='nocards')
    page = html_of(report(tmp_path, folder, with_cards)[1])
    plain = html_of(report(tmp_path, folder, without, name='out2')[1])
    s5 = section5(page).encode('utf-8')
    print('500 pairs / 10 cards: page %d bytes, without cards %d bytes, section 5 %d bytes, maps payload %d bytes' % (
        len(page.encode('utf-8')), len(plain.encode('utf-8')), len(s5), len(json.dumps(payload(page)['cards']))))
    assert page.count('<details class="card"') == 10


# ---- the real chain ---------------------------------------------------------------------------------------------------------

def test_real_chain_ledger_maps_feeds_the_report(tmp_path):
    from test_ledger_maps import chain, maps_args
    chain(tmp_path, dl1={'ref': {'A': {'L1': {0, 1}}, 'B': {'L1': {1, 2}}}, 'tgt': {'A': {'L1': {0, 1}}}})
    assert main(maps_args(tmp_path)) == 0
    out = tmp_path / 'rep'
    assert main(['ledger-report', '--ledger-dir', str(tmp_path / 'led'), '--out', str(out), '--maps-dir', str(tmp_path / 'maps')]) == 0
    page = html_of(out)
    data = payload(page)
    saved = json.loads((tmp_path / 'maps' / 'ledger_maps.json').read_text(encoding='utf-8'))
    for role, key in (('reference', 'A'), ('target', 'B')):
        assert [m['numbers'] for m in data['sides'][key]['maps']] == [m['numbers'] for m in saved['sides'][role]['maps']]
        assert {e['name'] for e in data['sides'][key]['electrodes']} == {e['name'] for e in saved['sides'][role]['electrodes']}
    ledger = json.loads((tmp_path / 'led' / 'ledger.json').read_text(encoding='utf-8'))
    assert {p['id']: p['no'] for p in data['pairs']} == {p['pair_id']: p['rank'] for p in ledger['pairs'] if p['rank']}
    assert json.loads((out / 'report.json').read_text(encoding='utf-8'))['ledger_maps_json_sha256'] == hashlib.sha256(
        (tmp_path / 'maps' / 'ledger_maps.json').read_bytes()).hexdigest()
    # a ledger rerun changes ledger.json's bytes only if its content changes; here the maps still match, so edit the ledger
    (tmp_path / 'led' / 'ledger.json').write_text((tmp_path / 'led' / 'ledger.json').read_text(encoding='utf-8') + '\n', encoding='utf-8')
    assert main(['ledger-report', '--ledger-dir', str(tmp_path / 'led'), '--out', str(tmp_path / 'rep2'), '--maps-dir',
                 str(tmp_path / 'maps')]) == 2
