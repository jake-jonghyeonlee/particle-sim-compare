"""Opt-in two-repository exporter/adapter gate; synthetic Fab is test-only."""
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from simcompare.adapters.example import load_dataset
from simcompare.adapters import particle_sim_run as checker
from simcompare.schema import DataContractError

ROOT = os.environ.get('PARTICLE_SIM_ROOT')
PYTHON = os.environ.get('PARTICLE_SIM_PYTHON')
pytestmark = pytest.mark.skipif(not ROOT or not PYTHON,
                               reason='set PARTICLE_SIM_ROOT and PARTICLE_SIM_PYTHON for exporter integration')

# This code runs only in the explicitly supplied particle-sim interpreter.
GENERATE = r'''
import sys
from pathlib import Path
import numpy as np
from PIL import Image
from particle_sim.validation.synthetic_cases import build_case, write_case
from particle_sim.pipeline import run_nominal
from particle_sim.export.simcompare import export_simcompare
base = Path(sys.argv[1])
for name in ('pilot', 'open_straight', 'auto_open'):
    case = build_case('open_straight' if name == 'auto_open' else name)
    case.manifest['particle_sizes_um'] = [2.0, 4.0]
    if name != 'pilot':
        case.manifest['schema_version'] = '2.0'
        case.manifest['open_paths'][0]['path_id'] = 'W:path'
        case.manifest['open_paths'][0]['applicable_particle_source_steps'] = [s['step_id'] for s in case.manifest['particle_source_steps']]
        if name == 'auto_open':
            case.manifest['open_paths'] = []
        wire = case.electrode_masks['W']
        yy, xx = np.nonzero(wire)
        holes = np.zeros_like(wire)
        pads = []
        for eid, x in [('PA', int(xx.min()) + 3), ('PB', int(xx.max()) - 3)]:
            y = int(np.median(yy))
            pad = np.zeros_like(wire)
            pad[y-6:y+7, x-4:x+5] = True
            hole = np.zeros_like(wire)
            hole[y-1:y+2, x-1:x+2] = True
            holes |= hole & wire
            case.electrode_masks[eid] = pad
            pads.append(pad)
            case.manifest['electrodes'].append(dict(electrode_id=eid, layer_name='M1', net_id='N1',
                                                     electrode_role='DATA', path=f'masks/electrode_{eid}.png'))
        case.layer_masks['M1'] = pads[0] | pads[1]
        case.manifest['contacts'] = [dict(contact_id='CNT', path='masks/contact.png', hole_layer='GI',
                                         applicable_particle_source_steps=['S_M1'])]
        case.manifest['open_applicable_steps_by_layer'] = {'M1': [], 'M2': ['S_M2']}
    manifest = write_case(case, base / name / 'case')
    if name != 'pilot':
        Image.fromarray(holes.astype(np.uint8)*255).save(manifest.parent / 'masks/contact.png')
    run_nominal(manifest, base / name / 'run')
    export_simcompare(base / name / 'run', manifest, base / name / 'export')
'''


def test_export_load_contract(tmp_path):
    pytest.importorskip('pyarrow')
    pytest.importorskip('zarr')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(Path(ROOT) / 'src'))
    result = subprocess.run([PYTHON, '-B', '-c', GENERATE, str(tmp_path)], cwd=ROOT,
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = []
    for name in ('pilot', 'open_straight', 'auto_open'):
        out = tmp_path / name / 'export'
        with pytest.raises(DataContractError, match='fab.json|fab_measurements'):
            load_dataset(out, mode='real')
        # Only adapter plumbing evidence: these are not real Fab measurements.
        (out / 'fab.json').write_text(json.dumps({'distributions': [dict(
            fab_distribution_id='SYNTHETIC_ONLY_' + layer, fab_id='FAB_SYN', process_layer=layer,
            bin_lower=[1.0], bin_upper=[5.0], raw_values=[1.0], value_kind='density',
            value_unit='1/cm2', size_unit='um') for layer in ('M1', 'M2', 'M3')]}))
        loaded = load_dataset(out, mode='real')
        observed.extend(loaded.bundles)
        run_path = tmp_path / name / 'run'
        args = checker.parse_args([str(run_path)])
        report = checker.RunReport(str(run_path))
        run = checker.load_run(run_path)
        checked = checker.build_bundles(
            run, report, checker.map_units(run, report, checker.FAILURE_MAPPING),
            checker.open_store(run.store_path),
            checker.load_product_config(tmp_path / name / 'case/manifest.yaml'), args, {})
        assert report.count('error') == 0
        assert {b.sample_id: b.assigned_process_layer for b in checked if b.failure_type == 'vertical_short'} == {
            b.sample_id: b.assigned_process_layer for b in loaded.bundles if b.failure_type == 'vertical_short'}
        raw = pd.read_parquet(tmp_path / name / 'run/ca.parquet')
        id_col = 'failure_unit_id' if 'failure_unit_id' in raw else 'pair_id'
        trace = json.loads((out / 'provenance.json').read_text())['sample_id_mapping']
        assert {b.sample_id for b in loaded.bundles} == {v['sample_id'] for v in trace}
        by_id = {v['sample_id']: v['raw_tuple'] for v in trace}
        for bundle in loaded.bundles:
            p, v, step, unit = by_id[bundle.sample_id]
            assert bundle.sample_id == ''.join(c if c.isascii() and (c.isalnum() or c in '_.-') else '_'
                                               for c in '-'.join((p, v, step, unit)))
            rows = raw[(raw.source_step == step) & (raw[id_col] == unit)].sort_values('particle_size_um')
            np.testing.assert_allclose(bundle.ca.values, rows.ca_nominal_normalized)
            np.testing.assert_allclose(bundle.ca.particle_sizes, rows.particle_size_um)
            assert len(bundle.layouts) == (1 if bundle.is_open else 2)
            if bundle.is_open:
                assert bundle.electrode_b_role == bundle.layer_b == 'N/A'
            if bundle.failure_type == 'vertical_short':
                assert bundle.assigned_process_layer == max((bundle.layer_a, bundle.layer_b),
                                                            key={'M1': 0, 'M2': 2, 'M3': 4}.get)
        if name != 'pilot':
            warnings = json.loads((out / 'export_warnings.json').read_text())
            cno = raw[raw.failure_mode == 'contact_not_open'][id_col].unique()
            assert len(cno) == 2
            assert all(any(uid in w and 'kind=contact' in w and 'mode=contact_not_open' in w for w in warnings) for uid in cno)
            assert not any(t['raw_tuple'][3] in cno for t in trace)
            if name == 'open_straight':
                assert any(t['raw_tuple'][3] == 'W:path' for t in trace)
            else:
                auto = [t for t in trace if t['raw_tuple'][3].startswith('auto_open:')]
                assert len(auto) == 1 and auto[0]['raw_tuple'][2] == 'S_M2'
    assert {'lateral_short', 'vertical_short', 'lateral_open'} <= {b.failure_type for b in observed}
