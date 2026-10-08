"""CA units must agree across comparison consumers and adapter boundaries."""
from copy import deepcopy
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from simcompare.adapters.example import load_dataset
from simcompare.schema import CACurve, DataContractError, prepare_ca_bundles




def test_mixed_family_adapter_policy(tmp_path, legacy_export_dir):
    out = tmp_path / 'data'
    shutil.copytree(legacy_export_dir, out)
    ca = pd.read_csv(out / 'ca_records.csv', dtype={'ca_unit': str})
    ca.loc[ca.sample_id == ca.sample_id.iloc[0], 'ca_unit'] = 'nm^2'
    ca.to_csv(out / 'ca_records.csv', index=False)
    with pytest.raises(DataContractError, match=r'Mixed CA unit families.*dimensionless count=.*area count=1'):
        load_dataset(out, mode='real')
    loaded = load_dataset(out, mode='demo')
    assert any('Mixed CA unit families' in w and 'samples=' in w for w in loaded.warnings)


def test_prefix_export_adapter_policy(tmp_path, legacy_export_dir):
    out = tmp_path / 'data'
    shutil.copytree(legacy_export_dir, out)
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


def test_different_area_units_are_one_family(legacy_export_dir):
    bundles = load_dataset(legacy_export_dir, mode='demo').bundles
    for bundle, unit in zip(bundles, ['nm^2', 'um^2']):
        bundle.ca.unit = unit
    assert prepare_ca_bundles(bundles, 'real') == []
    assert {b.ca.unit for b in bundles} == {'um^2'}


@pytest.mark.parametrize('canonical', ['um', 'mm'])
def test_area_ca_unit_conversion_and_area_normalization(legacy_export_dir, canonical):
    bundles = load_dataset(legacy_export_dir, mode='demo').bundles
    for bundle, scale, unit in zip(bundles, [1, 1e6], ['um^2', 'nm^2']):
        bundle.ca = CACurve(np.array([1., 2., 4.]), np.array([10., 50., 200.]) * scale,
                            np.ones(3, bool), unit)
    assert prepare_ca_bundles(bundles, 'real', canonical) == []
    np.testing.assert_allclose(bundles[0].ca.values, bundles[1].ca.values)
    assert {b.ca.unit for b in bundles} == {canonical + '^2'}
    np.testing.assert_allclose(bundles[0].ca_per_area(canonical)[0], [.001, .005, .02])
