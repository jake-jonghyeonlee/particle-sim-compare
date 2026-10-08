import json
import numpy as np


def test_numeric_demo_is_deterministic_and_has_explicit_synthetic_contracts(tmp_path):
    from simcompare.synthetic import generate_numeric_demo
    from simcompare.adapters.numeric import load_dataset
    import yaml
    generate_numeric_demo(tmp_path/'a')
    generate_numeric_demo(tmp_path/'b')
    for product in ('reference','target'):
        for name in ('manifest.json','pairs.csv','density.csv','review_assets.json'):
            assert (tmp_path/'a'/product/name).read_bytes()==(tmp_path/'b'/product/name).read_bytes()
        dataset=load_dataset(tmp_path/'a'/product)
        assert len(dataset.cases)==5
        assert all(len(c.records)==32 for c in dataset.cases)
        assert sum(r.input_status=='not_applicable' for c in dataset.cases for r in c.records.values())==2
        assert all(g['status']=='pending' and g['answer'] is None for g in dataset.gates.values())
    cfg=yaml.safe_load((tmp_path/'a/config.yaml').read_text())
    assert cfg['reference']['density_contracts'][0]['is_synthetic'] is True
    assert 'min_count' not in cfg['map']
    assert 'map_region_ids' not in cfg['review_rois']
    assert cfg['review_rois']['manual']==[]
    assert cfg['reference']['unit_bases'][0]['basis']=='one_unit'
    pending=yaml.safe_load((tmp_path/'a/pending.yaml').read_text())
    assert 'unit_bases' not in pending['reference'] and 'density_contracts' not in pending['target']
