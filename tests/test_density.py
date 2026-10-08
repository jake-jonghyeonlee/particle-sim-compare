from dataclasses import replace
import math

import pytest

from simcompare.density import DensityContract,adapt_density,density_per_um2
from simcompare.numeric_schema import DIAMETERS_UM,NumericContractError


def density(values=None, *, unit='um^-2',synthetic=True,semantics='per_bin',missing=(),
            distribution='D',process='density-M2',period='period',basis_version='bins_v1'):
    values={} if values is None else values
    rows=[{'distribution_id':distribution,'process_layer':process,'period':period,'bin_semantics':semantics,
           'raw_value_unit':unit,'diameter_um':d,'observed':d not in missing,
           'raw_value':None if d in missing else values.get(d,0)} for d in DIAMETERS_UM]
    contract=DensityContract(distribution,process,period,basis_version,synthetic,semantics,unit)
    return adapt_density(rows,contract=contract)


@pytest.mark.parametrize('value,unit,expected',[(0.5,'um^-2',0.5),(0.0000005,'nm^-2',0.5),(500000,'mm^-2',0.5)])
def test_density_unit_conversion(value,unit,expected):
    assert density_per_um2(value,unit)==expected
    assert density({5:value},unit=unit).bins[5].value_per_um2==expected


def test_density_32_small_and_large_bins_no_width_invention_or_total_normalisation():
    dist=density({1:1,30:2,40:3,50:4})
    assert set(dist.bins)==set(DIAMETERS_UM)
    assert sum(b.value_per_um2 for b in dist.bins.values())==10
    assert dist.bins[40].value_per_um2==3 and dist.bins[50].value_per_um2==4
    assert all('bin_lower_um' not in row for row in dist.raw_records)
    assert dist.coverage['canonical_available']==32


def test_missing_density_bin_and_observed_zero_are_distinct():
    dist=density(missing=(40,))
    assert dist.bins[5].observed and dist.bins[5].value_per_um2==0
    assert not dist.bins[40].observed and dist.bins[40].value_per_um2 is None
    assert dist.bins[40].reasons==('missing_density_input',)
    rows=[r for r in dist.raw_records if r['diameter_um']!=50]
    missing=adapt_density(rows,contract=dist.contract)
    assert missing.bins[50].value_per_um2 is None
    assert missing.coverage['missing']==[40,50]


@pytest.mark.parametrize('unit,synthetic,semantics',[('count/glass',True,'per_bin'),('um^-2',False,'per_bin'),
                                                   ('um^-2',True,'per_um'),('probability',True,'per_bin')])
def test_real_count_or_continuous_density_contracts_stay_pending(unit,synthetic,semantics):
    dist=density({5:10},unit=unit,synthetic=synthetic,semantics=semantics)
    assert dist.bins[5].value_per_um2 is None
    assert dist.reasons==('density_contract_unconfirmed',)


def test_raw_count_glass_with_area_still_needs_user_bin_contract():
    rows=[{'distribution_id':'D','process_layer':'M2','period':'p','bin_semantics':'',
           'raw_value_unit':'count/glass','diameter_um':5,'observed':'true','raw_value':'10','surveyed_area_um2':100}]
    dist=adapt_density(rows)
    assert dist.bins[5].raw_value==10 and dist.bins[5].value_per_um2 is None


@pytest.mark.parametrize('value',[-1,math.nan,math.inf,True])
def test_density_invalid_numbers_rejected(value):
    with pytest.raises(NumericContractError):density({5:value})


def test_density_duplicate_converted_diameter_and_contract_mismatch_rejected():
    dist=density()
    rows=list(dist.raw_records)
    rows.append({**rows[4],'diameter':5000,'diameter_unit':'nm'})
    del rows[-1]['diameter_um']
    with pytest.raises(NumericContractError,match='duplicate_density_diameter'):
        adapt_density(rows,contract=dist.contract)
    with pytest.raises(NumericContractError,match='contract_identity_mismatch'):
        adapt_density(dist.raw_records,contract=replace(dist.contract,distribution_id='another'))
    with pytest.raises(NumericContractError,match='contract_metadata_mismatch'):
        adapt_density(dist.raw_records,contract=replace(dist.contract,value_unit='mm^-2'))


@pytest.mark.parametrize('update',[{'surveyed_area_um2':0},{'surveyed_area_um2':float('nan')},
                                   {'bin_lower_um':5,'bin_upper_um':4},{'bin_lower_um':1,'bin_upper_um':None}])
def test_optional_density_metadata_validated_without_inventing_bins(update):
    dist=density()
    rows=[{**row,**update} for row in dist.raw_records]
    with pytest.raises(NumericContractError):adapt_density(rows,contract=dist.contract)


def test_missing_density_fields_are_typed_error():
    with pytest.raises(NumericContractError,match='missing_density_fields'):
        adapt_density([{'diameter_um':5}])


def test_real_count_per_glass_keeps_per_diameter_meaning_without_area_conversion():
    rows=[{'distribution_id':'REAL','process_layer':'L1','period':'period','bin_semantics':'per_bin',
           'diameter_um':5,'raw_value':10,'raw_value_unit':'count/glass','observed':True}]
    result=adapt_density(rows)
    assert result.bins[5].raw_value==10
    assert result.bins[5].value_per_um2 is None
    assert 'density_contract_unconfirmed' in result.bins[5].reasons
    assert result.to_dict()['diameter_bin_policy']=={
        'meaning':'one_value_per_diameter','decided_on':'2026-10-08',
        'bin_width_required':False,'continuous_density':False}
