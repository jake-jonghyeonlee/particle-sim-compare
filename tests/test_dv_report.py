"""vulnerability-report (T3b): candidates-dv 출력(T3a fixture)으로 근거 HTML 을 만든다. 표시 값은 vulnerability_v1.json 과 대조한다."""
import json
import re
import shutil
from pathlib import Path

import pytest

from simcompare.cli import main
from test_dv_candidates import World, standard_cases, TWIST, load, queries


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    return World(tmp_path_factory.mktemp('dvr'), standard_cases(), standard_cases(), tgt_types=TWIST)


@pytest.fixture(scope='module')
def basic(world):
    assert world.run('basic') == 0
    return world.base / 'basic'


@pytest.fixture(scope='module')
def report(basic, world):
    out = world.base / 'report'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(basic), '--out', str(out)]) == 0
    return out


def test_report_files_self_contained_and_footnote(report):
    assert sorted(p.name for p in report.iterdir()) == ['execution.json', 'report.html', 'report.json']
    html = (report / 'report.html').read_text(encoding='utf-8')
    assert not re.search(r'https?://|<script|<link |@import', html)
    assert '병합 전 등급 영역(선정 성분) 기준 CA' in html and 'ROI 내 CA µm² *' in html
    assert 'prefers-color-scheme: dark' in html
    assert '(보고서 exit 0)' in html
    assert json.loads((report / 'execution.json').read_text())['command'] == 'vulnerability-report'


def test_table_values_match_vulnerability_json(basic, report):
    html = (report / 'report.html').read_text(encoding='utf-8')
    vulnerability = load(basic / 'vulnerability_v1.json')
    for side in ('reference', 'target'):
        product = vulnerability['products'][side]
        pairs = {p['pair_key']: p for p in product['pairs']}
        for region in product['regions']:
            if not region['is_representative']:
                continue
            assert f'>{region["rank_graded"]}</td>' in html and f'{region["area_um2"]:.2f}' in html
            assert f'title="{region["region_id"]}"' in html
            for pair in region['pairs']:
                record = pairs[pair['pair_key']]
                assert f'>{record["dv_v"]}</td>' in html and record['electrode_a'] in html
                for case in pair['cases']:
                    assert f'{case["original_ca_um2"]:.3f}' in html and f'{case["ca_in_region_um2"]:.3f}' in html
        for item in product['unweighted_regions']:
            if 'below_graded_threshold' in item['flags']:
                assert f'title="{item["region_id"]}"' in html
    payload = json.loads((report / 'report.json').read_text(encoding='utf-8'))
    shown = payload['products']['reference']['rois'][0]['pairs']
    ref = vulnerability['products']['reference']
    assert {r['pair_key'] for r in shown} == {p['pair_key'] for r in ref['regions'] for p in r['pairs']}     # 병합된 두 영역의 쌍이 모두 나온다
    assert [r['grade'] for r in shown] == sorted((r['grade'] for r in shown), key={'High': 0, 'Low': 1, 'None': 2}.get)
    assert payload['inputs']['sha256']['vulnerability_v1.json'] and payload['input_status'] == 'available'


def test_layout_layer_colors_in_legend_and_map(report):
    html = (report / 'report.html').read_text(encoding='utf-8')
    assert 'class="lf l1"' in html and '<i class="lf l1"' in html and 'L1</span>' in html
    assert '<title>P1 (L1)</title>' in html or '<title>N1 (L1)</title>' in html
    # 견본·막대에 실제 색을 주는 CSS 선언이 있어야 한다(클래스 이름만으로는 색이 나오지 않는다)
    style = html.split('<style>')[1].split('</style>')[0]
    for slot in (1, 2, 3, 4):
        assert re.search(r'\.legend i\.l%d\{background:color-mix\(in srgb,var\(--l-%d\) \d+%%,transparent\);border-color:var\(--l-%d\)\}' % (slot, slot, slot), style)
        assert f'--l-{slot}:#' in style and re.search(r'\.l%d\{fill:var\(--l-%d\);stroke:var\(--l-%d\)\}' % (slot, slot, slot), style)
    for grade in ('High', 'Low', 'None'):
        assert f'rect.g-{grade}{{fill:var(--g-{grade.lower()})}}' in style and f'.g-{grade}{{background:var(--g-{grade.lower()})}}' in style
    assert 'style="border:1px solid"' not in html and '<rect x=' in html and 'class="bt"' in html and 'fill="var(' not in html
    assert 'class="gc gc3"' in html and 'class="win"' in html and 'class="sel"' in html


def test_asset_sha_mismatch_omits_layout_warns_exit_3(world, basic):
    copy = world.base / 'basic_sha'
    shutil.copytree(basic, copy)
    path = copy / 'candidate_queries_v1.json'
    payload = load(path)
    payload['inputs']['reference']['asset_manifest']['sha256'] = '0' * 64
    path.write_text(json.dumps(payload))
    out = world.base / 'report_sha'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(copy), '--out', str(out)]) == 3
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert 'layout_asset_sha256_mismatch' in html and '<path class="lf l1"' in html     # target 의 layout 은 남는다
    ref_side = html.split('id="side-reference"')[1].split('id="side-target"')[0]
    assert '<path class="lf' not in ref_side and 'layout 없이 그렸습니다' in ref_side
    assert 'layout_asset_sha256_mismatch' in json.loads((out / 'execution.json').read_text())['reasons']


def test_partial_input_exit_3_and_bad_out_exit_2(world, basic, report):
    copy = world.base / 'basic_partial'
    shutil.copytree(basic, copy)
    path = copy / 'candidate_queries_v1.json'
    payload = load(path)
    payload['status'] = 'partial'
    path.write_text(json.dumps(payload))
    assert main(['vulnerability-report', '--candidates-dv-dir', str(copy), '--out', str(world.base / 'report_partial')]) == 3
    assert main(['vulnerability-report', '--candidates-dv-dir', str(basic), '--out', str(basic / 'inside')]) == 2
    assert not (basic / 'inside').exists()
    assert main(['vulnerability-report', '--candidates-dv-dir', str(basic), '--out', str(report)]) == 2      # 이미 있음
    assert main(['vulnerability-report', '--candidates-dv-dir', str(world.base / 'nope'), '--out', str(world.base / 'x')]) == 2
    assert not (world.base / 'x').exists()


def test_top_n_limits_and_same_input_same_bytes(world, basic, report):
    other = world.base / 'report_again'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(basic), '--out', str(other)]) == 0
    assert (other / 'report.html').read_bytes() == (report / 'report.html').read_bytes()
    assert (other / 'report.json').read_bytes() == (report / 'report.json').read_bytes()
    one = world.base / 'report_n1'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(basic), '--out', str(one), '--top-n', '1']) == 0
    payload = json.loads((one / 'report.json').read_text(encoding='utf-8'))
    assert payload['top_n'] == 1 and len(payload['products']['reference']['dropped_rows']) == 1
    assert payload['products']['reference']['n_dropped'] == 2
    assert main(['vulnerability-report', '--candidates-dv-dir', str(basic), '--out', str(world.base / 'n0'), '--top-n', '0']) == 2


def test_malformed_json_shape_exit_2_without_output(world, basic):
    copy = world.base / 'basic_bad'
    shutil.copytree(basic, copy)
    path = copy / 'vulnerability_v1.json'
    payload = load(path)
    payload['products']['reference']['pairs'] = [None]            # TypeError 가 될 모양
    payload['products']['target']['regions'] = [[]]               # AttributeError/TypeError 가 될 모양
    path.write_text(json.dumps(payload))
    out = world.base / 'report_bad'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(copy), '--out', str(out)]) == 2
    assert not out.exists()


def test_status_row_shows_final_exit_code_on_layout_warning(world, basic):
    copy = world.base / 'basic_status'
    shutil.copytree(basic, copy)
    path = copy / 'candidate_queries_v1.json'
    payload = load(path)
    payload['inputs']['target']['asset_manifest']['sha256'] = '1' * 64
    path.write_text(json.dumps(payload))
    out = world.base / 'report_status'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(copy), '--out', str(out)]) == 3
    assert '(보고서 exit 3)' in (out / 'report.html').read_text(encoding='utf-8')


# ---- layer 색 슬롯: 보고서 전체에서 고정 ---------------------------------------------------------------------------
def _two_layer_cases():
    layer2 = [(re.sub(r'-([A-Z]\d+)-L2', r'-\1b-L2', name), cells) for name, cells in standard_cases('L2')]
    return standard_cases('L1') + layer2


def test_layer_slots_fixed_across_rois(tmp_path):
    world = World(tmp_path, _two_layer_cases(), _two_layer_cases(), tgt_types=TWIST)
    assert world.run('cdv') == 0
    out = tmp_path / 'rep'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(tmp_path / 'cdv'), '--out', str(out)]) == 0
    html = (out / 'report.html').read_text(encoding='utf-8')
    side = html.split('id="side-reference"')[1].split('id="side-target"')[0]
    maps = side.split('<svg class="map"')[1:]
    assert len(maps) >= 2
    seen = {}
    per_map = []
    for svg in maps:
        svg = svg.split('</svg>')[0]
        found = set(re.findall(r'class="lf (l\d|ln)" d="[^"]*"><title>[^<(]*\((L\d+)\)</title>', svg))
        per_map.append({layer for _, layer in found})
        for cls, layer in found:
            seen.setdefault(layer, set()).add(cls)
    assert seen == {'L1': {'l1'}, 'L2': {'l2'}}             # 같은 layer 는 ROI 가 달라도 같은 슬롯
    assert {'L1'} in per_map and {'L2'} in per_map           # layer 가 서로 다른 ROI 에 따로 나온다
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert payload['layer_slots']['slots'] == {'L1': 1, 'L2': 2} and '자연 정렬' in payload['layer_slots']['order']
    assert '자연 정렬' in html and '<i class="lf l1"></i>L1' in html and '<i class="lf l2"></i>L2' in html


def test_layer_slots_natural_order_and_overflow_has_no_new_colour():
    import numpy as np
    from types import SimpleNamespace
    from simcompare import dv_report
    assert dv_report._layer_slots(['L10', 'L2', 'L1']) == {'L1': 1, 'L2': 2, 'L10': 3}
    layers = ['L1', 'L2', 'L3', 'L4', 'L5', 'L6']
    slots = dv_report._layer_slots(layers)
    assert [slots[l] for l in layers] == [1, 2, 3, 4, None, None]
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True
    svg = dv_report._map_svg(1, SimpleNamespace(pitch=0.1, origin=(0.0, 0.0)), (0, 0, 4, 4), np.zeros((4, 4), dtype=int), np.zeros((4, 4), dtype=bool),
                             {('E%d' % i, l): mask for i, l in enumerate(layers)}, slots, (0, 0, 0.2, 0.2), layers)
    classes = set(re.findall(r'class="(?:lf|ls) (l\d|ln)"', svg))
    assert classes == {'l1', 'l2', 'l3', 'l4', 'ln'} and '<title>E5 (L6)</title>' in svg
