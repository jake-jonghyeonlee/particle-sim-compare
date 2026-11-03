"""diam-fig/T2: ``candidates-change --diameter-um``. 두 지름(3·5 µm) 월드를 합성해 해석값이 쌍 만들기·export·표시까지 가는지 본다."""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import test_change_candidates as tcc
from test_change_candidates import (CASE_AB, CASE_AC, CHANGE, OFFSET, PITCH, PRODUCTS, build_product, cli_args, digests, load,
                                    write_config)
from simcompare.cli import main

SIDES = ('reference', 'target')
ORIGINAL_IMPORT = tcc.import_hotspots


def _unchanged(mask, side):
    """5 µm 월드의 변경 사각형을 지운 mask (reference·target 이 layout 이동만 다르다)."""
    out = mask.copy()
    dx, dy = OFFSET
    if side == 'reference':
        out[CHANGE] = False
    else:
        out[CHANGE[0].start + dy:CHANGE[0].stop + dy, CHANGE[1].start + dx:CHANGE[1].stop + dx] = False
    return out


def _build_world(base, plan, monkeypatch):
    """plan[side](case, full_mask, side) -> {지름 µm: mask}. import-hotspots 직전에 PNG·CSV 를 plan 대로 다시 쓴다."""
    original = ORIGINAL_IMPORT      # 세계마다 감싸면 앞 세계의 plan 이 다시 덮어쓴다

    def patched(root, out, **kw):
        side = 'reference' if root.name == 'hotspot_reference' else 'target'
        for folder in sorted(Path(root, 'images').iterdir()):
            defect = folder.name.split('-')[0]
            full = np.asarray(Image.open(folder/f'hotspot_{defect}05.png')) > 0
            masks = plan[side](folder.name, full, side)
            for stale in folder.glob(f'hotspot_{defect}*.png'):
                stale.unlink()
            rows = []
            for diameter, mask in sorted(masks.items()):
                Image.fromarray(mask.astype(np.uint8)*255).save(folder/f'hotspot_{defect}{diameter:02d}.png')
                rows.append([str(diameter), f'{int(mask.sum())*PITCH*PITCH:.2f}'])
            with (folder/f'result/{defect}.csv').open('w', newline='', encoding='utf-8-sig') as handle:
                writer = csv.writer(handle)
                writer.writerow(['defect_size(um)', 'Area(um2)'])
                writer.writerows(rows)
        return original(root, out, **kw)
    monkeypatch.setattr(tcc, 'import_hotspots', patched)
    datas, assignments = {}, {}
    for side in SIDES:
        datas[side], assignments[side] = build_product(base, side)
    return SimpleNamespace(base=base, dl1=base/'dl1', datas=datas, assignments=assignments)


def _plan(three):
    """5 µm 은 ``five`` 방식, 3 µm 은 ``three`` 방식(None 이면 3 µm PNG 없음)."""
    def make(five):
        def side_plan(case, full, side):
            masks = {5: full if five == 'full' else _unchanged(full, side)}
            if three is not None:
                kind = three(case, side)
                if kind is not None:
                    base = _unchanged(full, side)
                    masks[3] = np.zeros_like(full) if kind == 'zero' else (full if kind == 'full' else base)
            return masks
        return side_plan
    return make


@pytest.fixture(scope='module')
def worlds(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    made = {}
    try:
        specs = {
            # A: 5 µm 은 layout 이동 + 변경, 3 µm 는 이동만(변경 없음)
            'A': (_plan(lambda c, s: 'same'), 'full'),
            # B: 5 µm 는 이동만, 3 µm 에 변경이 있다
            'B': (_plan(lambda c, s: 'full'), 'same'),
            # N: 3 µm PNG 가 아예 없다
            'N': (_plan(None), 'full'),
            # P: reference 는 CASE_AB 만 3 µm 가 있고, target 은 3 µm 가 모두 관측 면적 0
            'P': (None, 'full'),
        }
        for key, (maker, five) in specs.items():
            base = tmp_path_factory.mktemp(f'diam_{key}')
            if key == 'P':
                plan = {'reference': lambda case, full, side: {5: full, **({3: _unchanged(full, side)} if case == CASE_AB else {})},
                        'target': lambda case, full, side: {5: full, 3: np.zeros_like(full)}}
            else:
                plan = {side: maker(five) for side in SIDES}
            made[key] = _build_world(base, plan, patch)
    finally:
        patch.undo()
    return made


def _config(worlds, ref, tgt, name, map_spec=None):
    base = worlds[ref].base
    datas = {'reference': worlds[ref].datas['reference'], 'target': worlds[tgt].datas['target']}
    assignments = {'reference': worlds[ref].assignments['reference'], 'target': worlds[tgt].assignments['target']}
    return write_config(base, datas, assignments, name=name, map_spec=map_spec)


def _run(world, out, config=None, diameter=None, **kw):
    argv = cli_args(config or world.config, out, world.dl1, **kw)
    if diameter is not None:
        argv += ['--diameter-um', str(diameter)]
    return main(argv)


@pytest.fixture(scope='module')
def configs(worlds):
    for key, world in worlds.items():
        world.config = _config(worlds, key, key, f'{key}.yaml', map_spec={'diameter_um': 5})
    return worlds


def _queries(payload):
    return [q for d in payload['directions'] for q in d['queries']]


def _scored(payload):
    return [q for q in _queries(payload) if q.get('score_um2', 0) > 0 or q.get('n_rois')]


# --- 생략 = 명시 5 --------------------------------------------------------------------------------------------------

def test_explicit_five_equals_omitted(configs, tmp_path):
    world = configs['A']
    code = _run(world, tmp_path/'omit')
    assert code in (0, 3)
    for value in (5, 5.0):
        assert _run(world, tmp_path/f'five{value}', diameter=value) == code
        assert digests(tmp_path/f'five{value}') == digests(tmp_path/'omit')
    payload = load(tmp_path/'omit')
    assert payload['diameter_um'] == 5.0 and 'diameter_um' not in payload['change_args']
    assert 'diameter_um' not in load(tmp_path/'omit', 'execution.json')['args']
    html = (tmp_path/'omit'/'alignment.html').read_text(encoding='utf-8')
    assert 'hotspot 지름' not in html


# --- 두 지름 월드 ---------------------------------------------------------------------------------------------------

def test_three_um_sees_no_change_where_only_five_um_differs(configs, tmp_path):
    world = configs['A']
    assert _run(world, tmp_path/'d5') in (0, 3)
    assert _run(world, tmp_path/'d3', diameter=3) in (0, 3)
    five, three = load(tmp_path/'d5'), load(tmp_path/'d3')
    assert _queries(five), 'the 5 µm world must have a change'
    assert not _queries(three), _queries(three)
    assert three['diameter_um'] == 3.0 and three['change_map']['pairing']['diameter_um'] == 3.0
    rep = three['args']['representative_diameter_um']
    assert rep == 3 and type(rep) is int
    for doc in (three['change_args'], load(tmp_path/'d3', 'execution.json')['args']):
        assert doc['diameter_um'] == 3 and type(doc['diameter_um']) is int
    html = (tmp_path/'d3'/'alignment.html').read_text(encoding='utf-8')
    assert 'hotspot 지름 3 µm' in html and 'hotspot 지름 (µm)' in html
    assert 'hotspot 지름' not in (tmp_path/'d5'/'alignment.html').read_text(encoding='utf-8')
    # 같은 3 µm 지름의 export 도 3 µm record 에서 나온다(5 µm 실행과 다르다)
    assert digests(tmp_path/'d3') != digests(tmp_path/'d5')


def test_three_um_sees_the_change_where_only_three_um_differs(configs, tmp_path):
    world = configs['B']
    assert _run(world, tmp_path/'d5') in (0, 3)
    assert _run(world, tmp_path/'d3', diameter=3) in (0, 3)
    assert not _queries(load(tmp_path/'d5'))
    three = load(tmp_path/'d3')
    assert _queries(three) and three['change_args']['diameter_um'] == 3
    top = _queries(three)[0]
    assert top['cnt_change_um2_by_layer'] is not None
    assert (tmp_path/'d3'/'alignment.html').read_text(encoding='utf-8').count('hotspot 지름 3 µm (기본 5 µm 아님)') == 1


# --- L3: 그 지름이 관측되지 않았다 ----------------------------------------------------------------------------------

@pytest.mark.parametrize('ref,tgt,side', [('N', 'A', 'reference'), ('A', 'N', 'target')])
def test_one_side_without_observed_diameter_is_refused_before_writing(configs, tmp_path, capsys, ref, tgt, side):
    config = _config(configs, ref, tgt, f'{ref}{tgt}.yaml', map_spec={'diameter_um': 5})
    out = tmp_path/'out'
    assert _run(configs['A'], out, config=config, diameter=3) == 2
    err = capsys.readouterr().err
    assert 'candidates_change_diameter_not_observed' in err and side in err and not out.exists()
    assert _run(configs['A'], tmp_path/'five', config=config) in (0, 3)      # 5 µm 의 동작은 그대로


def test_partly_missing_cases_go_on_and_a_zero_area_png_counts_as_observed(configs, tmp_path):
    partial = _config(configs, 'P', 'A', 'PA.yaml', map_spec={'diameter_um': 5})
    assert _run(configs['A'], tmp_path/'partial', config=partial, diameter=3) in (0, 3)
    pairing = load(tmp_path/'partial')['change_map']['pairing']
    assert pairing['diameter_um'] == 3.0
    zero = _config(configs, 'A', 'P', 'AP.yaml', map_spec={'diameter_um': 5})
    assert _run(configs['A'], tmp_path/'zero', config=zero, diameter=3) in (0, 3)
    assert (tmp_path/'zero'/'candidate_queries_v1.json').is_file()


def test_diameter_without_png_in_either_model_is_refused(configs, tmp_path, capsys):
    out = tmp_path/'out'
    assert _run(configs['A'], out, diameter=7) == 2
    assert 'candidates_change_diameter_not_observed' in capsys.readouterr().err and not out.exists()


# --- 값 오류 --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value', [2.5, 0, 35, -1])
def test_bad_diameter_value_exits_two_and_writes_nothing(configs, tmp_path, capsys, value):
    out = tmp_path/'out'
    assert _run(configs['A'], out, diameter=value) == 2
    assert 'invalid_candidates_change_argument' in capsys.readouterr().err and not out.exists()


def test_non_numeric_diameter_is_an_argparse_error(configs, tmp_path):
    with pytest.raises(SystemExit) as caught:
        _run(configs['A'], tmp_path/'out', diameter='abc')
    assert caught.value.code == 2 and not (tmp_path/'out').exists()


# --- config map.diameter_um 와의 관계 -------------------------------------------------------------------------------

def _notes(out):
    return [n for n in load(out)['notes'] if n.get('code') == 'diameter_overrides_config']


def test_argument_wins_over_config_and_says_so_only_when_they_differ(configs, tmp_path, capsys):
    world = configs['A']
    five = _config(configs, 'A', 'A', 'cfg5.yaml', map_spec={'diameter_um': 5})
    three = _config(configs, 'A', 'A', 'cfg3.yaml', map_spec={'diameter_um': 3})
    nomap = _config(configs, 'A', 'A', 'cfgnone.yaml')
    outside = _config(configs, 'A', 'A', 'cfg35.yaml', map_spec={'diameter_um': 35})

    assert _run(world, tmp_path/'a', config=five, diameter=3) in (0, 3)
    assert _notes(tmp_path/'a') == [{'code': 'diameter_overrides_config', 'severity': 'info',
                                     'config_diameter_um': 5, 'diameter_um': 3}]
    assert load(tmp_path/'a')['notes'] == load(tmp_path/'a', 'execution.json')['notes']

    capsys.readouterr()
    assert _run(world, tmp_path/'b', config=three) == 2          # 인수 생략 + config 3: 기존 오류
    assert 'candidates_change_diameter_fixed_5um' in capsys.readouterr().err and not (tmp_path/'b').exists()

    assert _run(world, tmp_path/'c', config=nomap, diameter=3) in (0, 3)
    assert not _notes(tmp_path/'c')

    assert _run(world, tmp_path/'d', config=three, diameter=5) in (0, 3)
    assert _notes(tmp_path/'d')[0]['config_diameter_um'] == 3 and _notes(tmp_path/'d')[0]['diameter_um'] == 5
    assert 'diameter_um' not in load(tmp_path/'d')['change_args']      # 5 µm 이면 표시 키 없음

    assert _run(world, tmp_path/'e', config=three, diameter=3) in (0, 3)
    assert not _notes(tmp_path/'e')

    capsys.readouterr()
    assert _run(world, tmp_path/'f', config=outside, diameter=3) == 2
    assert 'invalid_representative_diameter' in capsys.readouterr().err and not (tmp_path/'f').exists()
