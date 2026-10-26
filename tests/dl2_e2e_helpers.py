"""change-roi/T12: helpers for the compare <-> DL2 end-to-end test (``test_dl2_e2e.py``).

Nothing here imports DL2. DL2 is only ever run as a subprocess (``SIMCOMPARE_DL2_PY -m psdl2.<module>`` with
``PYTHONPATH=SIMCOMPARE_DL2_SRC``) and everything it writes goes to the caller's tmp folder.

The synthetic world is the one of ``test_change_candidates`` (known layout offset, planted hotspot change) with two additions:
the electrode layouts (1.png / 2.png) are real bars through the change area instead of the corner marks of the original
world, and the target copy of every layout is shifted by the same offset as the CNT layers. DL2 needs something to look at.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest
import yaml

import test_change_candidates as tcc
from simcompare.cli import main
from test_change_candidates import CASE_AB, CASE_AC, FULL, OFFSET, ROLES, build_product, cnt_world, save_black, shifted

B = 500                                                   # crop border of the DL1 full-frame images, in pixels
ORIGIN_UM = 50.0                                          # frame origin of the cropped area (B * 0.1 um)
BARS = {1: (291, 299), 2: (302, 310), 3: (306, 314)}     # seed -> crop columns: electrode A, B (case AB), C (case AC)
RULE = 'between_stack_order_v1'
DB_PRODUCT = 'PROD_X'                                     # a product that is only in the DL2 database
DL2_ENV = ('SIMCOMPARE_DL2_SRC', 'SIMCOMPARE_DL2_PY')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dl2_settings():
    """(src, python) or None. SIMCOMPARE_REQUIRE_DL2=1 turns a missing setting into a failure (the final gate)."""
    values = [os.environ.get(name) for name in DL2_ENV]
    if all(values):
        return tuple(os.path.expanduser(v) for v in values)
    if os.environ.get('SIMCOMPARE_REQUIRE_DL2') == '1':
        pytest.fail('SIMCOMPARE_REQUIRE_DL2=1 but ' + ' and '.join(DL2_ENV) + ' are not both set')
    pytest.skip('SIMCOMPARE_DL2_SRC and SIMCOMPARE_DL2_PY are not both set (DL2 worktree src and its interpreter)')


# ---- layouts: vertical bars through the change area, shifted with the whole target layout --------------------------------

def bar(seed, shift=(0, 0)):
    mask = np.zeros(FULL, bool)
    lo, hi = BARS[seed]
    mask[B+200:B+400, B+lo:B+hi] = True
    return shifted(mask, *shift) if shift != (0, 0) else mask


class layouts:
    """Context manager: `build_product` calls the module-level ``pattern(seed)``; seeds 1, 2, 3 are the electrode layouts."""

    def __init__(self, side):
        self.side, self.patch = side, pytest.MonkeyPatch()

    def __enter__(self):
        shift = OFFSET if self.side == 'target' else (0, 0)
        self.patch.setattr(tcc, 'pattern', lambda seed: bar(seed if seed in BARS else 1, shift))
        return self

    def __exit__(self, *exc):
        self.patch.undo()


def build_world_product(base, side):
    with layouts(side):
        return build_product(base, side)


# ---- per-product template export (the DL2 database) -----------------------------------------------------------------------

def write_template_tree(root, side, cases=(CASE_AB, CASE_AC)):
    """Hotspot-free tree: images/<case>/1.png, 2.png only."""
    shift = OFFSET if side == 'target' else (0, 0)
    for case in cases:
        folder = root / 'images' / case
        save_black(folder / '1.png', bar(1, shift))
        save_black(folder / '2.png', bar(2 if case == CASE_AB else 3, shift))


def export_template(base, product, side, centres, *, name=None):
    """`simcompare export-template-rois` for one product. Returns the output folder (roi/review_rois.json, roi/mask_derivation.json)."""
    name = name or f'template_{product}'
    tree = base / f'{name}_tree'
    write_template_tree(tree, side)
    dl1 = base / 'dl1'
    if not (dl1 / product).exists():                                      # the database-only product has its own CNT layers
        for number, seed in enumerate((31, 32, 33), start=1):
            save_black(dl1 / product / 'cno' / f'{number}_CNT{number}.png', cnt_world(seed))
    (base / 'template_pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
                                             'AB,A,B,line,pair-v1\nAC,A,C,line,pair-v1\n')
    config = {'schema_version': 'template_export_config_v1', 'data_dir': str(tree), 'cnt_from_dl1_root': str(dl1),
              'product_id': product, 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
              'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT',
              'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1}],
              'electrode_roles': dict(ROLES), 'pair_table_path': str(base / 'template_pairs.csv'), 'source_version': 'layout_v1',
              'max_asset_memory_mb': 512, 'mask_derivation_rule': RULE,
              'rois': [{'case_id': case, 'center_xy_um': list(centre)} for case, centre in centres.items()]}
    path = base / f'{name}.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    out = base / f'{name}_out'
    code = main(['export-template-rois', '--config', str(path), '--out', str(out)])
    return code, out


# ---- DL2 as a subprocess ---------------------------------------------------------------------------------------------------

def dl2_run(settings, module, *argv, cwd=None):
    """Runs ``python -m psdl2.<module> ...`` against the DL2 worktree sources. Returns the CompletedProcess (text)."""
    src, python = settings
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env.update(PYTHONPATH=src, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    return subprocess.run([python, '-B', '-m', f'psdl2.{module}', *map(str, argv)], capture_output=True, text=True, env=env,
                          cwd=cwd, timeout=1200)


def dl2_probe(settings):
    """Which psdl2 does the DL2 interpreter import? (it must be the worktree, not an editable install of another checkout)."""
    src, python = settings
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env.update(PYTHONPATH=src, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    done = subprocess.run([python, '-B', '-c', 'import psdl2, sys; print(psdl2.__file__)'], capture_output=True, text=True, env=env)
    return done.stdout.strip(), done


def json_of(done):
    return json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else None
