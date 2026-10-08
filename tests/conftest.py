"""유지된 legacy export loader의 작은 수작업 계약 fixture.

numeric 시험은 자체 numeric_v2 fixture를 쓰며 학습 설정을 읽지 않는다.
"""
import csv
import json
import pytest


@pytest.fixture
def legacy_export_dir(tmp_path):
    root = tmp_path / 'legacy_export'
    root.mkdir()
    rows = [dict(sample_id=s, product_id='P', product_family='F', design_version='v1',
                 design_lineage_id='L', electrode_a_role='gate', electrode_b_role='data',
                 layer_a='M1', layer_b='M1', failure_type='lateral_short', fab_id='FAB',
                 assigned_process_layer='M1', unit_width=100, unit_height=100,
                 physical_length_unit='um') for s in ('S1', 'S2')]
    tables = {'bundles.csv': rows, 'ca_records.csv': [dict(sample_id=s,particle_size=d,
              particle_size_unit='um',ca_value=v,ca_unit='1')
              for s in ('S1','S2') for d,v in ((1,0),(2,.1),(4,.2))]}
    for name, records in tables.items():
        with (root/name).open('w',newline='') as f:
            writer=csv.DictWriter(f,records[0].keys());writer.writeheader();writer.writerows(records)
    (root/'fab.json').write_text(json.dumps({'distributions':[dict(
        fab_distribution_id='D',fab_id='FAB',product_family='F',process_layer='M1',
        bin_lower=[.5,1.5],bin_upper=[1.5,2.5],raw_values=[1,2],value_kind='count',
        value_unit='particles',size_unit='um',surveyed_area=1000,area_unit='um^2',is_reference=False)]}))
    (root/'provenance.json').write_text(json.dumps({'is_synthetic':True,'calc_version':'fixture'}))
    return root
