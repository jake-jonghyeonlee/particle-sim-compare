"""change-roi-v2/T3b: ``--electrode-png-root`` and the layer bundles of the ROI detail in ``alignment.html``.

World: the two-layer world of test_change_roi_v2_merge (cases on L1 and L2 at the same window, ``--merge-scope all`` gives one ROI
with both layers). A copy of its DL1 root gets electrode PNGs of the right size (frame + 500 px per side): electrode A has a bar on
L1 and L2, electrode B a second bar on L1. The target bars sit at the reference bars + OFFSET, so after alignment they coincide.
The other electrode folders keep the tiny wrong-size PNGs of the shared world (unreadable → skipped).
"""
import base64
import io
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import test_change_candidates as tcc
from simcompare import alignment_report, change_map
from simcompare.adapters import electrode_png
from simcompare.cli import main
from test_alignment_report import synthetic_source
from test_change_roi_v2_merge import build_world, two_layer_world  # noqa: F401  (module-scoped fixture of the other module)

IMG = re.compile(r'<img src="data:image/png;base64,([A-Za-z0-9+/=]+)" alt="([^"]*)"')
BASE = tuple(alignment_report.BASE_COLOUR)
PAD = 500
BAR_A = (300, 250, 20, 110)       # frame rows 300..319, cols 250..359 — covers the planted change (rows 300..319, cols 300..329)
BAR_B = (340, 250, 10, 110)       # a second electrode bar below it (L1 only)
BAR_A2 = (330, 250, 8, 110)       # electrode A on L2: a different place than on L1


def bar_image(bar, side):
    y, x, h, w = bar
    dx, dy = tcc.OFFSET
    if side == 'target':
        y, x = y + dy, x + dx
    mask = np.zeros(tcc.FULL, dtype=bool)
    mask[PAD + y:PAD + y + h, PAD + x:PAD + x + w] = True
    return mask


def write_electrodes(dl1, layers_by_electrode):
    for side, product in tcc.PRODUCTS.items():
        for electrode, layers in layers_by_electrode.items():
            for layer, bar in layers.items():
                tcc.save_black(Path(dl1) / product / electrode / f'1-{layer}.png', bar_image(bar, side))


def copy_root(world, tmp, name, layers_by_electrode, rm=()):
    root = tmp / name
    shutil.copytree(world.dl1, root)
    for electrode in rm:
        shutil.rmtree(root / tcc.PRODUCTS['reference'] / electrode, ignore_errors=True)
        shutil.rmtree(root / tcc.PRODUCTS['target'] / electrode, ignore_errors=True)
    write_electrodes(root, layers_by_electrode)
    return root


@pytest.fixture(scope='module')
def roots(two_layer_world, tmp_path_factory):
    tmp = tmp_path_factory.mktemp('layer_roots')
    default = copy_root(two_layer_world, tmp, 'dl1_default', {'A': {'L1': BAR_A, 'L2': BAR_A2}, 'B': {'L1': BAR_B}})
    # a second root: other shapes (the bars moved), and an electrode folder that the DL1 root does not have
    other = copy_root(two_layer_world, tmp, 'png_other', {'A': {'L1': BAR_B, 'L2': BAR_B}, 'Z': {'L1': BAR_B}})
    only_l1 = copy_root(two_layer_world, tmp, 'png_l1_only', {'A': {'L1': BAR_A}})
    return SimpleNamespace(default=default, other=other, only_l1=only_l1)


def run(world, out, dl1, *extra):
    args = tcc.cli_args(world.config, out, dl1)
    return main(args[:-2] + list(extra) + args[-2:])


def read(out):
    return (Path(out) / 'alignment.html').read_text(encoding='utf-8')


def pictures(html):
    return [(alt, np.asarray(Image.open(io.BytesIO(base64.b64decode(data))).convert('RGB'))) for data, alt in IMG.findall(html)]


def picture(html, prefix):
    found = [rgb for alt, rgb in pictures(html) if alt.startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


def base_mask(rgb):
    return np.all(rgb == np.array(BASE, dtype=np.uint8), axis=2)


@pytest.fixture(scope='module')
def default_run(two_layer_world, roots, tmp_path_factory):
    out = tmp_path_factory.mktemp('layers_default') / 'out'
    code = run(two_layer_world, out, roots.default, '--merge-scope', 'all')
    return SimpleNamespace(out=out, code=code, payload=tcc.load(out), html=read(out))


def test_default_root_is_the_cnt_root_and_draws_its_electrode_shapes(default_run):
    """Option omitted: no key in change_args / execution args; the bundle background is the electrode PNGs of the CNT root."""
    assert 'electrode_png_root' not in default_run.payload['change_args']
    assert 'electrode_png_root' not in tcc.load(default_run.out, 'execution.json')['args']
    ref = picture(default_run.html, 'ROI 층 L1 · reference:')
    mask = base_mask(ref)
    assert mask.any()
    rows = np.flatnonzero(mask.any(axis=1))
    # bars A (rows 300..319) and B (rows 340..349) are both in the crop box; the picture is scaled by an integer factor or reduced
    assert rows.max() > rows.min() + 5 and (~mask).any()
    assert not any('전극 형상 없음' in alt for alt, _ in pictures(default_run.html) if alt.startswith('ROI 층 L1'))


def test_explicit_root_changes_the_background_and_is_recorded(two_layer_world, roots, default_run, tmp_path):
    out = tmp_path / 'out'
    assert run(two_layer_world, out, roots.default, '--merge-scope', 'all', '--electrode-png-root', str(roots.other)) == default_run.code
    payload = tcc.load(out)
    assert payload['change_args']['electrode_png_root'] == str(roots.other)
    assert tcc.load(out, 'execution.json')['args']['electrode_png_root'] == str(roots.other)
    html = read(out)
    default_mask, other_mask = (base_mask(picture(h, 'ROI 층 L1 · reference:')) for h in (default_run.html, html))
    assert default_mask.shape == other_mask.shape and default_mask.any() and other_mask.any()
    # the other root has one bar (rows 340..349); the default root has bar A (rows 300..319) on top of it: the rows of the other
    # picture are a subset of the default rows, and the default picture has rows the other one lacks
    default_rows, other_rows = np.flatnonzero(default_mask.any(axis=1)), np.flatnonzero(other_mask.any(axis=1))
    assert not np.array_equal(default_mask, other_mask)
    assert other_rows.min() > default_rows.min() and other_rows.max() <= default_rows.max()
    # the calculation and the other outputs stay the same: only the recorded argument differs
    a, b = dict(default_run.payload), dict(payload)
    for item in (a, b):
        item.pop('alignment_report')
    b['change_args'] = {k: v for k, v in b['change_args'].items() if k != 'electrode_png_root'}
    assert a == b


def test_electrode_list_comparison_ignores_the_electrode_png_root(two_layer_world, roots, default_run, tmp_path):
    """Root `other` has an electrode folder Z that the CNT root lacks: no electrode_set_differs note appears."""
    out = tmp_path / 'out'
    run(two_layer_world, out, roots.default, '--merge-scope', 'all', '--electrode-png-root', str(roots.other))
    notes = tcc.load(out)['notes']
    assert [n for n in notes if n['code'] == 'electrode_set_differs'] == [n for n in default_run.payload['notes'] if n['code'] == 'electrode_set_differs']
    assert not any('Z' in json.dumps(n) for n in notes if n['code'] == 'electrode_set_differs')


@pytest.mark.parametrize('kind', ['missing', 'file'])
def test_bad_electrode_png_root_exits_2_before_writing(two_layer_world, roots, tmp_path, capsys, kind):
    bad = tmp_path / 'nothing'
    if kind == 'file':
        bad.write_text('x')
    out = tmp_path / 'out'
    assert run(two_layer_world, out, roots.default, '--electrode-png-root', str(bad)) == 2
    assert not out.exists()
    err = capsys.readouterr().err
    assert 'electrode_png_root_missing' in err and str(bad.resolve()) in err


def expected_counts(payload):
    """layer → (n, shape-change, appeared, disappeared) from the first query's contributing cases (independent of the report code)."""
    out = {}
    for case in tcc.queries(payload, 'reference_to_target')[0]['contributing_cases']:
        item = out.setdefault(case['layer'], [0, 0, 0, 0])
        item[0] += 1
        item[{'appeared': 2, 'disappeared': 3}.get(case['change_kind'], 1)] += 1
    return out


def test_two_layers_give_two_bundles_with_titles_and_counts(default_run):
    counts = expected_counts(default_run.payload)
    assert sorted(counts) == ['L1', 'L2']
    html = default_run.html
    titles = re.findall(r'<div class="bundle"><h4>([^<]*)</h4>', html)
    assert len(titles) == 2 and not any('L3' in t for t in titles)
    for layer, (n, shape, new, gone) in counts.items():
        parts = [f'{label} {value}' for label, value in (('형상 변화', shape), ('창 안 새로 생김', new), ('창 안 사라짐', gone)) if value]
        assert f'층 {layer} — 불량 조합 {n}개 (' + ' · '.join(parts) + ')' in titles
    alts = [alt for alt, _ in pictures(html) if alt.startswith('ROI 층 ')]
    assert len(alts) == 6 and {a.split(' · ')[0] for a in alts} == {'ROI 층 L1', 'ROI 층 L2'}
    # a layer that has electrode PNGs but no case gets no bundle
    assert not [a for a in alts if 'L3' in a]
    assert '개 층 (창 안 변경 면적 합이 작은 층)' not in html                    # two layers: nothing is left out
    assert html.count('변경 종류는 창 단위다') == 1 and '창 안 새로 생김' in html


def test_layer_without_electrode_png_is_drawn_without_background_and_exit_code_is_unchanged(two_layer_world, roots, default_run, tmp_path):
    out = tmp_path / 'out'
    code = run(two_layer_world, out, roots.only_l1, '--merge-scope', 'all')
    assert code == default_run.code
    html = read(out)
    alts = {alt: rgb for alt, rgb in pictures(html) if alt.startswith('ROI 층 ')}
    l2 = [a for a in alts if a.startswith('ROI 층 L2')]
    l1 = [a for a in alts if a.startswith('ROI 층 L1')]
    assert len(l2) == 3 and all('전극 형상 없음' in a for a in l2)
    assert not any('전극 형상 없음' in a for a in l1)
    assert all(not base_mask(alts[a]).any() for a in l2) and all(base_mask(alts[a]).any() for a in l1)
    assert tcc.load(out)['status'] == default_run.payload['status']


def test_unreadable_files_of_the_shared_world_do_not_stop_the_run(two_layer_world, tmp_path):
    """The shared world's electrode PNGs are tiny (wrong size): no background, no error, exit code as before."""
    out = tmp_path / 'out'
    assert run(two_layer_world, out, two_layer_world.dl1, '--merge-scope', 'all') in (0, 3)
    html = read(out)
    assert all('전극 형상 없음' in alt for alt, _ in pictures(html) if alt.startswith('ROI 층 '))


def test_admission_limit_drops_only_the_backgrounds_and_warns(two_layer_world, roots, default_run, tmp_path, monkeypatch):
    monkeypatch.setattr(alignment_report, 'electrode_read_bytes', lambda *args: 10 ** 15)
    out = tmp_path / 'out'
    assert run(two_layer_world, out, roots.default, '--merge-scope', 'all') == 3
    payload = tcc.load(out)
    warning = next(w for w in payload['warnings'] if w['code'] == 'alignment_report_layer_base_skipped')
    assert warning['estimated_bytes'] == 10 ** 15 and warning['max_operation_bytes'] > 0 and warning['reserved_bytes'] > 0
    assert payload['status'] == 'partial' and 'alignment_report_images_skipped' not in {w['code'] for w in payload['warnings']}
    html = read(out)
    alts = {alt: rgb for alt, rgb in pictures(html) if alt.startswith('ROI 층 ')}
    assert len(alts) == 6 and all('전극 형상 없음' in a and not base_mask(rgb).any() for a, rgb in alts.items())
    assert 'data:image' in html                                       # the other pictures are still there
    assert payload['directions'] == default_run.payload['directions']      # the calculation is untouched
    assert tcc.load(out, 'funnel.json') == tcc.load(default_run.out, 'funnel.json')


def layer_query(layers_areas, kinds=None):
    cases = []
    for k, (layer, area) in enumerate(layers_areas):
        cases.append({'case_key': 'lateral_short|A%d|%s|B|%s|%s' % (k, layer, layer, layer), 'layer': layer, 'residual_um2': area,
                      'change_kind': (kinds or {}).get(k, 'shifted'), 'area_reference_um2': 1.0, 'area_target_um2': 1.0,
                      'present_in': 'both', 'layer_changed': False})
    return cases


def test_more_than_four_layers_show_four_bundles_by_area_and_count_the_rest():
    alignment, qs, windows, source = synthetic_source(1)
    # five layers: areas L1 1+1, L2 9, L3 2, L4 5+1, L5 0.5 (cases on the same layer add up)
    cases = layer_query([('L1', 1.0), ('L1', 1.0), ('L2', 9.0), ('L3', 2.0), ('L4', 5.0), ('L4', 1.0), ('L5', 0.5)],
                        {2: 'appeared', 5: 'disappeared'})
    qs = [{**qs[0], 'contributing_cases': cases}]
    shown, omitted = alignment_report.layer_bundles(qs[0], windows[0])
    assert [b['layer'] for b in shown] == ['L2', 'L4', 'L1', 'L3'] and omitted == ['L5']        # L1 and L3 tie at 2.0: natural name order
    assert [b['area'] for b in shown] == [9.0, 6.0, 2.0, 2.0]
    html = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows[:1], source=source)[0].decode('utf-8')
    titles = re.findall(r'<div class="bundle"><h4>([^<]*)</h4>', html)
    assert titles == ['층 L2 — 불량 조합 1개 (창 안 새로 생김 1)', '층 L4 — 불량 조합 2개 (형상 변화 1 · 창 안 사라짐 1)',
                      '층 L1 — 불량 조합 2개 (형상 변화 2)', '층 L3 — 불량 조합 1개 (형상 변화 1)']
    assert '생략 1개 층' in html and 'L5' in html[html.index('생략 1개 층'):html.index('생략 1개 층') + 120]
    assert sum(alt.startswith('ROI 층 ') for alt, _ in pictures(html)) == 12


def test_bundle_background_moves_with_the_hotspot_direction_for_a_nonzero_offset():
    """The target electrode crop uses the same _shift_crop as the hotspot: out[y, x] = frame[y + dy, x + dx]."""
    shape = (60, 80)
    valid = np.ones(shape, bool)
    source = alignment_report.ImageSource({}, {}, valid, valid, (7, -4))
    source.hot = [{'box': (10, 50, 20, 70)}]
    frame = np.zeros(shape, bool)
    frame[30 - 4, 40 + 7] = True                  # the reference pixel (30, 40) as the target model draws it
    source.collect_bases({'L1': [0]}, lambda layer: frame.copy(), lambda layer: frame.copy())
    tgt, ref = source.base[(0, 'L1', 'tgt')], source.base[(0, 'L1', 'ref')]
    assert tgt.shape == ref.shape == (40, 50) and tgt.sum() == 1 and tgt[30 - 10, 40 - 20]
    assert np.array_equal(tgt, change_map._shift_crop(frame, -4, 7, 10, 50, 20, 70))
    assert ref.sum() == 1 and ref[26 - 10, 47 - 20]            # the reference model has no offset
    source.collect_bases({'L2': [0]}, lambda layer: None, lambda layer: frame)
    assert source.base[(0, 'L2', 'ref')] is None and source.base[(0, 'L2', 'tgt')] is not None


def test_end_to_end_target_bars_coincide_with_reference_bars_after_alignment(default_run):
    """The target bars are drawn at the reference bars + OFFSET, so the shifted target background covers the same rows and columns.

    The hotspot colours cover part of the bars, so the masks are compared by their extent (an unshifted target would be 7 px right
    and 4 px up, times the picture scale)."""
    ref = base_mask(picture(default_run.html, 'ROI 층 L1 · reference:'))
    tgt = base_mask(picture(default_run.html, 'ROI 층 L1 · target:'))
    assert ref.any() and tgt.any() and ref.shape == tgt.shape
    assert default_run.payload['alignment']['offset_xy_um'] == [0.7, -0.4]
    for axis in (0, 1):
        assert np.array_equal(np.flatnonzero(ref.any(axis=axis)), np.flatnonzero(tgt.any(axis=axis)))


def test_compare_picture_uses_both_models_backgrounds(default_run):
    compare = picture(default_run.html, 'ROI 층 L1 · 비교')
    assert base_mask(compare).any()
    # hotspot colours sit on top of the background in the compare picture
    blue = np.all(compare == np.array(alignment_report.REF_COLOUR, dtype=np.uint8), axis=2)
    orange = np.all(compare == np.array(alignment_report.TGT_COLOUR, dtype=np.uint8), axis=2)
    assert blue.any() or orange.any() or np.all(compare == np.array(alignment_report.BOTH_COLOUR, dtype=np.uint8), axis=2).any()


# --- adapter ---------------------------------------------------------------------------------------------------------

def tiny_frame(shape):
    return SimpleNamespace(frame_id='F', pixel_pitch_um=electrode_png.LAYOUT_PITCH_UM, origin_xy_um=electrode_png.HOTSPOT_ORIGIN_UM,
                           shape_yx=shape)


def test_scan_and_load_electrode_layer_unions_files_and_skips_unreadable_ones(tmp_path):
    shape = (12, 16)
    full = (shape[0] + 2 * PAD, shape[1] + 2 * PAD)

    def save(path, y, x):
        mask = np.zeros(full, bool)
        mask[PAD + y:PAD + y + 2, PAD + x:PAD + x + 3] = True
        tcc.save_black(path, mask)
    save(tmp_path / 'M' / 'A' / '1-L1.png', 1, 2)
    save(tmp_path / 'M' / 'B' / '1-L1.png', 8, 10)
    save(tmp_path / 'M' / 'C' / '1-L2.png', 4, 4)
    tcc.save_black(tmp_path / 'M' / 'D' / '1-L1.png', np.zeros((5, 5), bool))         # wrong size: skipped
    (tmp_path / 'M' / 'cno').mkdir()
    files = electrode_png.scan_electrode_layers(tmp_path, 'M')
    assert sorted(files) == ['L1', 'L2'] and len(files['L1']) == 3 and len(files['L2']) == 1
    union = electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), files['L1'])
    assert union.shape == shape and union.sum() == 12 and union[1, 2] and union[8, 10]       # A and B; D is unreadable
    assert electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), files['L2'])[4, 4]
    assert electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), []) is None
    assert electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), ['M/D/1-L1.png']) is None      # nothing readable
    assert electrode_png.scan_electrode_layers(tmp_path, 'missing') == {}


# --- T3a review advisories (T3b 4b) ----------------------------------------------------------------------------------

def test_after_alignment_block_with_reference_only_and_target_only_pixels_is_pure_target_orange():
    ref_idx = np.array([[2, 2]], dtype=np.int8)
    tgt_idx = np.array([[2, -1]], dtype=np.int8)
    same_idx = np.array([[-1, -1]], dtype=np.int8)
    gray = np.zeros((1, 2), bool)
    after = alignment_report._shade(ref_idx, tgt_idx, same_idx, gray)
    assert tuple(after[0, 0]) == alignment_report.TGT_SHADES[2]                 # both kinds in one block: target orange, not a grey mean
    assert tuple(after[0, 1]) == alignment_report.REF_SHADES[2]
    assert tuple(after[0, 0]) not in {tuple(c) for c in alignment_report.SAME_SHADES}
    before = alignment_report._shade(ref_idx, tgt_idx, None, gray)              # before alignment the pre-mixed colour stays
    assert tuple(before[0, 0]) not in {tuple(c) for c in alignment_report.TGT_SHADES + alignment_report.REF_SHADES}


def test_window_scoped_wording_for_appeared_and_disappeared():
    alignment, qs, windows, source = synthetic_source(1)
    cases = layer_query([('L1', 3.0), ('L1', 2.0), ('L1', 1.0)], {0: 'appeared', 1: 'disappeared'})
    html = alignment_report.build_alignment_html(alignment, queries=[{**qs[0], 'contributing_cases': cases}], windows=windows[:1],
                                                 source=source)[0].decode('utf-8')
    assert '<th>창 안 새로 생김</th><th>창 안 사라짐</th><th>형상 변화</th>' in html            # ROI list header
    assert '<td>창 안 새로 생김</td>' in html and '<td>창 안 사라짐</td>' in html                 # contribution table
    assert '층 L1 — 불량 조합 3개 (형상 변화 1 · 창 안 새로 생김 1 · 창 안 사라짐 1)' in html
    assert '<td>새로 생김</td>' not in html and '<th>새로 생김</th>' not in html
    assert ('변경 종류는 창 단위다: 그 창 안에서 한 모델의 hotspot 이 없으면 새로 생김/사라짐이다. '
            'case 자체는 두 모델에 다 있을 수 있다(있는 모델 열).') in html


# --- T6 follow-ups (T3b review e30ed7c2) -------------------------------------------------------------------------------

def test_empty_electrode_png_root_exits_2_before_writing(two_layer_world, roots, tmp_path, capsys):
    out = tmp_path / 'out'
    assert run(two_layer_world, out, roots.default, '--electrode-png-root', '') == 2
    assert not out.exists()
    assert 'electrode_png_root_missing' in capsys.readouterr().err


def test_missing_electrode_captions_name_the_reason(two_layer_world, roots, tmp_path):
    # 파일 없음: root only_l1 has no L2 electrode files
    out = tmp_path / 'no_file'
    run(two_layer_world, out, roots.only_l1, '--merge-scope', 'all')
    l2 = [alt for alt, _ in pictures(read(out)) if alt.startswith('ROI 층 L2')]
    assert len(l2) == 3 and all('전극 형상 없음' in a and '파일 없음' in a for a in l2)
    assert not any('전극 형상 없음' in alt for alt, _ in pictures(read(out)) if alt.startswith('ROI 층 L1'))
    # 읽기 실패: the shared world's tiny PNGs are listed but cannot be used
    out = tmp_path / 'unreadable'
    run(two_layer_world, out, two_layer_world.dl1, '--merge-scope', 'all')
    alts = [alt for alt, _ in pictures(read(out)) if alt.startswith('ROI 층 L1')]
    assert len(alts) == 3 and all('전극 형상 없음 (' in a and '읽기 실패: electrode_frame_mismatch' in a for a in alts)
    # 제품 폴더 없음: an electrode root without the target product folder
    root = tmp_path / 'no_target_folder'
    shutil.copytree(roots.default, root)
    shutil.rmtree(root / tcc.PRODUCTS['target'])
    out = tmp_path / 'no_folder'
    run(two_layer_world, out, roots.default, '--merge-scope', 'all', '--electrode-png-root', str(root))
    alts = [alt for alt, _ in pictures(read(out)) if alt.startswith('ROI 층 L1 · target')]
    assert len(alts) == 1 and '전극 형상 없음 (제품 폴더 없음)' in alts[0]
    assert not any('전극 형상 없음' in alt for alt, _ in pictures(read(out)) if alt.startswith('ROI 층 L1 · reference'))


def test_load_electrode_layer_reports_codes_and_reraises_memory_error(tmp_path, monkeypatch):
    shape = (12, 16)
    tcc.save_black(tmp_path / 'M' / 'D' / '1-L1.png', np.zeros((5, 5), bool))         # wrong size
    errors = []
    assert electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), ['M/D/1-L1.png'], errors) is None
    assert len(errors) == 1 and errors[0]
    assert electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), ['M/missing/1-L1.png'], errors) is None
    assert len(errors) == 2

    def boom(data, rel):
        try:
            raise MemoryError('oom')
        except MemoryError as exc:
            raise electrode_png.NumericContractError('png_decode_failed', rel) from exc
    monkeypatch.setattr(electrode_png, '_decode', boom)
    with pytest.raises(MemoryError):
        electrode_png.load_electrode_layer(tmp_path, 'M', tiny_frame(shape), ['M/D/1-L1.png'], [])


def test_electrode_read_estimate_counts_16_bytes_per_png_pixel_and_the_held_bytes():
    shape = (100, 200)
    assert alignment_report.electrode_read_bytes(shape, shape) == 16 * 1100 * 1200 + 2 * 100 * 200
    need = alignment_report.electrode_read_bytes(shape, shape)
    assert alignment_report.electrode_fits(shape, shape, need, 0)
    assert not alignment_report.electrode_fits(shape, shape, need, 0, 1)
    alignment, qs, windows, source = synthetic_source(2)
    held = source.held_bytes()
    assert held >= source.observed.nbytes + sum(a.nbytes for item in source.heat.values() for a in item.values())
    assert alignment_report.electrode_fits(shape, shape, need + held, 0, held)
    assert not alignment_report.electrode_fits(shape, shape, need + held - 1, 0, held)


def test_report_admission_uses_the_real_hotspot_layer_count(two_layer_world, roots, tmp_path, monkeypatch):
    seen = []
    real = alignment_report.fits

    def spy(*args, **kwargs):
        seen.append(args[5] if len(args) > 5 else kwargs.get('n_layers'))
        return real(*args, **kwargs)
    monkeypatch.setattr(alignment_report, 'fits', spy)
    run(two_layer_world, tmp_path / 'out', roots.default, '--merge-scope', 'all')
    assert seen == [2]                                                         # layers L1 and L2, not HEAT_LAYERS_ASSUMED


def test_roi_without_contributing_cases_has_no_bundles_and_says_so():
    alignment, qs, windows, source = synthetic_source(1)
    query = {**qs[0], 'contributing_cases': []}
    assert alignment_report.layer_bundles(query, windows[0]) == ([], [])
    assert alignment_report.section_images(source, 0, query, windows[0]) == 0
    html = alignment_report.build_alignment_html(alignment, queries=[query], windows=windows[:1], source=source)[0].decode('utf-8')
    assert '이 ROI 에 기여 조합이 없다.' in html and '<div class="bundle">' not in html
    assert not any(alt.startswith('ROI 층 ') for alt, _ in pictures(html))
