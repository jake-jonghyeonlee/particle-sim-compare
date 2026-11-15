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


def write_maps(tmp, doc, ledger_sha, name='maps', status='ok', top_pairs=None, tamper=None):
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
                maps.append({'file': file, 'layer': layer, 'kind': kind, 'px_um': 0.2, 'shape_yx': [30, 60],
                             'numbers': counts_of(array)})
        for number, (pair, item) in enumerate(items, 1):
            rows.append({'item_id': item['item_id'], 'pair_id': pair['pair_id'], 'kind': item['class'],
                         'layers': [item['analysis_layer']], 'upper_layer': item['upper_layer'], 'lower_layer': item['lower_layer'],
                         'number': number, 'value': item[key], 'tile_sum': item[key], 'has_map_area': True, 'hidden_px': 0,
                         'status': 'pending' if item[key] is None else 'ok'})
        sides[role] = {'items': rows, 'maps': maps,
                       'electrodes': [{'number': k, 'name': n, 'layers': {}} for k, n in enumerate(electrodes, 1)],
                       'hidden': {'item_px': 3, 'electrode_overlap_px': 0}}
    options = {'tile_um': 1.0}
    if top_pairs is not None:
        options['top_pairs'] = top_pairs
    document = {'schema_version': 'ledger_maps_v1', 'status': status,
                'reasons': ['ledger_maps_value_check_failed'] if status == 'partial' else [],
                'inputs': {'ledger_json_sha256': ledger_sha}, 'options': options, 'report': {'diameter_um': 5.0},
                'sides': sides, 'cards': [], 'scale_steps': []}
    (out / 'ledger_maps.json').write_text(json.dumps(document, sort_keys=True), encoding='utf-8')
    return out


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
    assert page.count('class="lead placeholder"') == 1 and 'id="s5"' in page         # section 5 waits for T6
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
    assert saved['sections'] == {'present': [1, 2, 3, 4, 6, 7], 'placeholders': [5]}
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
    """Writes the page a headless browser can open: file://<path>#size, #demo, #print. Set LEDGER_MAPS_HTML_OUT to keep it."""
    doc, folder, maps = setup(tmp_path)
    code, out = report(tmp_path, folder, maps)
    assert code == 0
    page = html_of(out)
    target = os.environ.get('LEDGER_MAPS_HTML_OUT')
    if target:
        Path(target).write_text(page, encoding='utf-8')
    print('synthetic maps report: %d bytes' % len(page.encode('utf-8')))
    assert len(page.encode('utf-8')) > 20000


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
