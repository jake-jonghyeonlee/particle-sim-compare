"""N=300 1262^2 stage 1-6 pipeline with the rebuilt ETX runners under /tmp/py38 (python3.8). Driven by the 3.11 venv; the runners run
in py38. Writes /tmp/py38run/result.json."""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path
REPO = Path('/Users/jonghyeonlee/git/particle-sim-compare')
sys.path.insert(0, str(REPO / 'scripts'))
import benchmark_scale_compare as b
import yaml

def go():
    BASE = Path('/tmp/py38run')
    PY38 = '/tmp/py38/bin/python'
    N, SIZE = 300, 1262
    shutil.rmtree(BASE, ignore_errors=True)
    shapes = {'reference': (SIZE, SIZE), 'target': (SIZE, SIZE)}
    gen_seconds, pairs = b.generate(BASE / 'gen', N, shapes, 'components', 'distinct', 4)
    (BASE / 'data' / 'input' / 'hotspot').mkdir(parents=True)
    shutil.move(str(BASE / 'gen' / 'input' / 'reference'), str(BASE / 'data' / 'input' / 'hotspot' / 'QA4_R00'))
    shutil.move(str(BASE / 'gen' / 'input' / 'target'), str(BASE / 'data' / 'input' / 'hotspot' / 'QA4_T00'))
    for side in ('reference', 'target'):
        (BASE / 'data' / 'input' / 'dl1' / b.PRODUCTS[side] / 'cno').mkdir(parents=True)
        b._layout_png(BASE / 'data' / 'input' / 'dl1' / b.PRODUCTS[side] / 'cno' / '1_CNT.png', 'CNT', shapes[side])
    LIMITS = {'max_operation_bytes': 34359738368}
    log = []


    def runner(stage, name, edit=None):
        src = REPO / 'bundles' / 'etx' / name
        text = src.read_text(encoding='utf-8')
        text = re.sub(r'^BASE_DIR = .*$', f'BASE_DIR = {str(BASE)!r}', text, count=1, flags=re.M)
        if edit:
            text = edit(text)
        path = BASE / name
        path.write_text(text, encoding='utf-8')
        t = time.time()
        proc = subprocess.run([PY38, str(path)], cwd=str(BASE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log.append({'stage': stage, 'runner': name, 'exit': proc.returncode, 'wall_s': round(time.time() - t, 1),
                    'tail': proc.stdout[-500:]})
        print(stage, proc.returncode, log[-1]['wall_s'], flush=True)
        assert proc.returncode == 0, proc.stdout[-2000:]


    def cfg_for(with_assets):
        cfg = yaml.safe_load(b.write_config(BASE, LIMITS, False, '_tmp.yaml', BASE / 'outputs' / 'compare', with_assets=with_assets,
                                            map_diameter=5 if with_assets else None).read_text())
        for side in ('reference', 'target'):
            cfg[side]['data_dir'] = f'data/imported/{side}'
        (BASE / 'real.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')


    # BASE_DIR in the runners is where relative paths resolve: write the config with relative data_dir after stage 1
    (BASE / 'data' / 'imported').mkdir(parents=True, exist_ok=True)
    runner('1_import_hotspots', 'simcompare_job_1_import_hotspots.py',
           lambda t: t.replace("'--out', 'data/imported/reference',", "'--out', 'data/imported/reference', '--expected-cases', 'gen/expected_cases_reference.txt',")
                      .replace("'--out', 'data/imported/target',", "'--out', 'data/imported/target', '--expected-cases', 'gen/expected_cases_target.txt',"))
    shutil.rmtree(BASE / 'gen' / 'input', ignore_errors=True)
    # pair tables go into data/imported/<side>/pairs.csv; write_pair_tables uses work/'imported'
    (BASE / 'imported').symlink_to(BASE / 'data' / 'imported')
    b.write_pair_tables(BASE, pairs)
    cfg_for(False)
    runner('2_validate_compare', 'simcompare_job_2_validate_compare.py')
    for side in ('reference', 'target'):
        (BASE / f'assignment_{side}.json').write_text(json.dumps(b.assignment_for(b.PRODUCTS[side], LIMITS)), encoding='utf-8')
    runner('3_import_electrode_assets', 'simcompare_job_3_import_electrode_assets.py',
           None)
    for side in ('reference', 'target'):
        out = BASE / 'data' / 'assets' / side
        for name in ('review_assets.json', 'electrode_asset_import_record.json'):
            shutil.copyfile(out / name, BASE / 'data' / 'imported' / side / name)
        shutil.copytree(out / 'assets', BASE / 'data' / 'imported' / side / 'assets', dirs_exist_ok=True)
    cfg_for(True)
    runner('4_candidates', 'simcompare_job_4_candidates.py', lambda t: t.replace("'--window-um', '20'", "'--window-um', '6'"))
    (BASE / 'data' / 'dl2').mkdir(parents=True)
    matches, queries = b.fake_matches(BASE / 'data' / 'candidates_k3', BASE / 'data' / 'dl2', shapes, 11, 15.55)
    for direction, short in (('reference_to_target', 'ncc_r2t.json'), ('target_to_reference', 'ncc_t2r.json')):
        shutil.move(str(matches[direction]), str(BASE / 'data' / 'dl2' / short))
    accepted = b.decisions_file(BASE / 'data' / 'dl2' / 'decisions.csv', {d: BASE / 'data' / 'dl2' / s for d, s in
                                (('reference_to_target', 'ncc_r2t.json'), ('target_to_reference', 'ncc_t2r.json'))}, 50)
    runner('5_confirm_pairs', 'simcompare_job_5_confirm_pairs.py', lambda t: t.replace('CHANGE_ME', 'bench'))
    corr = json.loads((BASE / 'data' / 'confirmed' / 'roi_correspondence.json').read_text())
    runner('6_compare_windows', 'simcompare_job_6_compare_windows.py')
    doc = json.loads((BASE / 'data' / 'local' / 'local_delta.json').read_text())
    ready = sum(1 for p in corr['pairs'] if p['status'] == 'ready')
    result = {'python': PY38, 'N': N, 'size': SIZE, 'profile': 'components', 'generate_s': gen_seconds, 'accepted': accepted, 'queries': queries,
              'pairs': len(corr['pairs']), 'ready': ready, 'rows': doc['n_rows'], 'rows_expected': ready * 3 * 32, 'status': doc['status'],
              'artefact': (BASE / 'data' / 'imported').exists() and (BASE / 'outputs' / 'compare' / 'comparison_masks.zip').exists(),
              'steps': log}
    (BASE.parent / 'py38run_result.json').write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != 'steps'}))


if __name__ == '__main__':
    go()
