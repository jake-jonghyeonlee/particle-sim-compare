"""CA units must agree across comparison consumers and adapter boundaries."""
from copy import deepcopy
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from simcompare.adapters.example import load_dataset
from simcompare.analysis import risk_table
from simcompare.caa import FabWeights, compute_caa
from simcompare.cards import _order_value
from simcompare.dataset import encode_bundles
from simcompare.schema import CACurve, DataContractError, prepare_ca_bundles


@pytest.mark.parametrize('canonical', ['um', 'mm'])
def test_area_ca_consumers_agree(demo_loaded, demo_cfg, canonical):
    bundles = deepcopy(demo_loaded.bundles[:2])
    demo_cfg.data.canonical_length_unit = canonical
    demo_cfg.data.ca_grid = [1, 2, 4] if canonical == 'um' else [.001, .002, .004]
    for bundle, scale, unit in zip(bundles, [1, 1e6], ['um^2', 'nm^2']):
        bundle.ca = CACurve(np.array([1., 2., 4.]), np.array([10., 50., 200.]) * scale,
                            np.ones(3, bool), unit)
        bundle.unit_width = bundle.unit_height = 100
        bundle.physical_length_unit = 'um'
    table = risk_table(bundles, demo_loaded.distributions, demo_cfg)
    assert table.ca_max.iloc[0] == pytest.approx(table.ca_max.iloc[1])
    assert table.ca_unit.tolist() == [canonical + '^2'] * 2
    enc = encode_bundles(bundles, demo_loaded.distributions, demo_cfg)
    np.testing.assert_allclose(enc.ca_log[0], enc.ca_log[1])
    np.testing.assert_allclose(enc.ca_area_log[0], enc.ca_area_log[1])
    np.testing.assert_allclose(bundles[0].ca_per_area(canonical)[0], [.001, .005, .02])
    weights = FabWeights(np.array([1., 2., 4.]), np.array([.2, .2, .2]), 'um',
                         'test', 'test', '1', False)
    results = [compute_caa(b.ca, weights, canonical_length_unit=canonical) for b in bundles]
    assert results[0].value == pytest.approx(results[1].value)
    assert results[0].unit == 'relative_index*' + canonical + '^2'
    # Standalone CAA also canonicalizes an unprepared area curve.
    raw = CACurve(np.array([1., 2., 4.]), np.array([10., 50., 200.]) * 1e6,
                  np.ones(3, bool), 'nm^2')
    assert compute_caa(raw, weights).value == pytest.approx(52.)
    raw.values = np.array([.1, .2, .3])
    raw.unit = '1'
    assert compute_caa(raw, weights).unit == 'relative_index'


def test_mixed_family_adapter_policy(tmp_path, demo_dir):
    out = tmp_path / 'data'
    shutil.copytree(demo_dir, out)
    (out / 'layouts.csv').unlink()
    (out / 'hotspots.csv').unlink()
    ca = pd.read_csv(out / 'ca_records.csv', dtype={'ca_unit': str})
    ca.loc[ca.sample_id == ca.sample_id.iloc[0], 'ca_unit'] = 'nm^2'
    ca.to_csv(out / 'ca_records.csv', index=False)
    with pytest.raises(DataContractError, match=r'Mixed CA unit families.*dimensionless count=.*area count=1'):
        load_dataset(out, mode='real')
    loaded = load_dataset(out, mode='demo')
    assert any('Mixed CA unit families' in w and 'samples=' in w for w in loaded.warnings)


def test_prefix_export_adapter_policy(tmp_path, demo_dir):
    out = tmp_path / 'data'
    shutil.copytree(demo_dir, out)
    (out / 'layouts.csv').unlink()
    (out / 'hotspots.csv').unlink()
    provenance = json.loads((out / 'provenance.json').read_text())
    provenance['calc_version'] = 'particle-sim-old'
    (out / 'provenance.json').write_text(json.dumps(provenance))
    rows = pd.read_csv(out / 'bundles.csv')
    if 'calc_version' in rows:
        rows['calc_version'] = 'particle-sim-old'
        rows.to_csv(out / 'bundles.csv', index=False)
    with pytest.raises(DataContractError, match='pre-fix particle-sim.*re-export'):
        load_dataset(out, mode='real')
    assert any('re-export' in w for w in load_dataset(out, mode='demo').warnings)


def test_different_area_units_are_one_family(demo_loaded):
    bundles = deepcopy(demo_loaded.bundles[:2])
    for bundle, unit in zip(bundles, ['nm^2', 'um^2']):
        bundle.ca.unit = unit
    assert prepare_ca_bundles(bundles, 'real') == []
    assert {b.ca.unit for b in bundles} == {'um^2'}


def test_cards_choose_complete_metric_and_most_observed():
    group = pd.DataFrame({'caa_actual': [100, np.nan, np.nan],
                          'ca_area_max': [.001, .1, .01], 'ca_max': [3, 2, 1]})
    assert _order_value(group).tolist() == [.001, .1, .01]
    group.loc[0, 'ca_area_max'] = np.nan
    group.loc[0, 'ca_max'] = np.nan
    values = _order_value(group)
    assert values.name == 'ca_area_max'
    assert values.isna().tolist() == [True, False, False]
