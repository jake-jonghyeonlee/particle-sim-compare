"""Golden scenarios for compare2 (pasx/T2): five fixtures built from the existing compare2 test helpers.

Each scenario writes a pair (configs, density, hotspots) into `tmp` and runs the classic `compare2` command.
The golden files in tests/data/compare2_golden/<scenario>/ are the outputs of that command at the baseline
commit (before compare2-side / compose existed); a path-and-hash normaliser makes them tmp-path independent.

Regenerate (only when the output format is meant to change):
    PYTHONPATH=src:tests python -c "import compare2_golden_cases as g; g.regenerate()"
"""
import re
import tempfile
from pathlib import Path

import pytest

import test_compare2 as tc
import test_compare2_expected_zero as ez

GOLDEN_DIR = Path(__file__).resolve().parent / 'data' / 'compare2_golden'
GOLDEN_FILES = ('compare2.json', 'compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv',
                'compare2_cno.csv')
VERTICAL = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4}, 3: {3, 4, 5}})
VERTICAL_PAIRS = tc.PAIRS + [('XY', 'X', 'Y', 'line')]
CNO_B = 'defect_size(um),Area(um2)\n1,0.5\n2,1.5\n3,5.0\n'


def normalize(text, tmp):
    """Output text with the tmp folder and every 64-hex digest masked (paths and hashes depend on the tmp path).

    The masked digests are verified by the classic == compose byte comparison in test_compare2_side.py."""
    text = text.replace(str(Path(tmp).resolve()), 'TMP').replace(str(tmp), 'TMP')  # macOS: /var is /private/var
    return re.sub(r'[0-9a-f]{64}', 'H', text)


def scenario_base(tmp, mp):
    ref, tgt = tc.two_sides()
    return tc.run(tmp, ref, tgt)


def scenario_absent(tmp, mp):
    ref, tgt = ez.two_one_sided()
    code, out, _doc = ez.run_inventory(tmp, mp, ref, tgt)
    return code, out


def scenario_subset(tmp, mp):
    ref, tgt = tc.two_sides()
    return tc.run(tmp, ref, tgt, diameters=[1, 3])


def scenario_distance(tmp, mp):
    # the vertical case needs a distance; 1.5 um drops the 1 um record (size <= distance is dropped)
    thk = 'layers\tdistance\nL1L2\t1.5\n'
    ref = (tc.base_cases() + [VERTICAL], VERTICAL_PAIRS)
    tgt = (tc.base_cases() + [VERTICAL], VERTICAL_PAIRS)
    return tc.run(tmp, ref, tgt, thk=thk)


def scenario_cno(tmp, mp):
    ref, tgt = tc.two_sides()
    return tc.run(tmp, ref, tgt, ref_cno={tc.CNO_NAME: tc.CNO_CSV}, tgt_cno={tc.CNO_NAME: CNO_B})


def scenario_excluded(tmp, mp):
    # a pairs.csv row with class=excluded: its case is left out and a pair_exclusions meta block appears
    extra_case = ('lateral_short', 'Y', 'L1', 'Z', 'L1', {1: {0, 1}, 2: {0}})
    pairs = tc.PAIRS + [('YZ', 'Y', 'Z', 'excluded')]
    ref, tgt = tc.two_sides()
    return tc.run(tmp, (ref[0] + [extra_case], pairs), (tgt[0] + [extra_case], pairs))


SCENARIOS = {'base': scenario_base, 'absent': scenario_absent, 'subset': scenario_subset,
             'distance': scenario_distance, 'cno': scenario_cno}
# no golden folder (a baseline run cannot make one for it): checked by classic == compose byte equality only
EXTRA_SCENARIOS = {'excluded': scenario_excluded}


def build(name, tmp, mp):
    """Run scenario `name` in `tmp`; returns (exit code, output folder). The config is tmp/compare2.yaml."""
    code, out = (SCENARIOS.get(name) or EXTRA_SCENARIOS[name])(tmp, mp)
    return code, out


def regenerate():
    for name in SCENARIOS:
        with tempfile.TemporaryDirectory() as raw, pytest.MonkeyPatch.context() as mp:
            tmp = Path(raw)
            code, out = build(name, tmp, mp)
            assert code == 0, (name, code)
            target = GOLDEN_DIR / name
            target.mkdir(parents=True, exist_ok=True)
            for file_name in GOLDEN_FILES:
                text = normalize((out / file_name).read_bytes().decode('utf-8'), tmp)
                with open(str(target / file_name), 'w', encoding='utf-8', newline='\n') as handle:
                    handle.write(text)
            print('wrote', name)
