"""Synthetic fixture runner; launched by the marked cross-repository test only.

Native search/index JSON is written directly from producer functions, unchanged.
No optimizer step, pretrained weights or network request is made.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(root, repo):
    import numpy as np
    from simcompare.adapters.numeric import load_dataset, source_payload_hash
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.roi_export import export_review_rois
    from simcompare.dl_results import create_context
    shutil.copytree(repo/'tests/fixtures/review_roi_v1', root/'numeric', dirs_exist_ok=True,ignore=shutil.ignore_patterns('expected.json','README.md'))
    source = root/'numeric'
    manifest = json.loads((source/'manifest.json').read_text())
    assets = json.loads((source/'review_assets.json').read_text())
    np.save(source/'assets/valid.npy', np.ones((8,10),np.uint8))
    h = digest(source/'assets/valid.npy')
    manifest['frames'][0]['valid']['sha256'] = h
    for item in assets['cases']:
        item['assets']['valid']['sha256'] = h
    manifest['cases'] += [deepcopy(manifest['cases'][0])]
    manifest['cases'][-1]['product_id'] = 'OTHER'
    assets['cases'] += [deepcopy(assets['cases'][0])]
    assets['cases'][-1]['identity']['product_id'] = 'OTHER'
    manifest['input_hash'] = manifest['source_manifest_hash'] = source_payload_hash(manifest)
    assets['source_manifest_hash'] = manifest['input_hash']
    write(source/'manifest.json',manifest); write(source/'review_assets.json',assets)
    data = load_dataset(source)
    selection = {'map_region_ids':'all','manual':[
        {'product_id':'PRODUCT','design_version':'DESIGN','case_id':'A','frame_id':'FRAME','center_xy_um':[102.5,201.5]},
        {'product_id':'OTHER','design_version':'DESIGN','case_id':'A','frame_id':'FRAME','center_xy_um':[102.5,201.5]},
        {'product_id':'PRODUCT','design_version':'DESIGN','frame_id':'FRAME','center_xy_um':[102.5,201.5]}]}
    export_review_rois(data,source,root,maps=compute_electrode_maps(data,min_count=3),selections=selection,
        asset_manifest={'path':'review_assets.json','sha256':digest(source/'review_assets.json')},
        producer_commit='f9d7a31d85eadc14d4b8467f452e3610ea6378cb')
    create_context(source,root/'review_rois.json',root/'numeric_context.json',
                   asset_manifest={'path':'review_assets.json','sha256':digest(source/'review_assets.json')})


def dl1(root):
    import numpy as np
    import torch
    from psdl1.schema import Product, Element, Frame
    from psdl1.data import build_vocab, input_identity
    from psdl1.model import ProductEncoder
    from psdl1.search import search
    from psdl1.train import model_config
    torch.manual_seed(7)
    # Exercise the producer's own synthetic fixture as well as the C round trip.
    import sys
    sys.path.insert(0,str(Path(__import__('psdl1').__file__).resolve().parents[2]/'tests'))
    from conftest import make_product
    q=make_product('bus',32,product_id='SYNTHETIC_QUERY')
    candidate=make_product('bus',32,product_id='SYNTHETIC_CANDIDATE')
    r,l=build_vocab([q,candidate]); cfg=model_config(32,r,l)
    write(root/'dl1_standalone_search.json',search(ProductEncoder(len(r),len(l)).eval(),cfg,q,[candidate],topk=1))
    torch.manual_seed(7)
    context = json.loads((root/'numeric_context.json').read_text())
    products = []
    for product in ('PRODUCT','OTHER'):
        rows = [r for r in context['records'] if r['source_identity']['product_id']==product]
        by_electrode = {}
        for row in rows[:1]:
            for layout in row['source_assets']['layouts']:
                ep = layout['endpoint']; by_electrode[ep['electrode_id']] = layout
        elements = tuple(Element(l['endpoint']['role'],l['endpoint']['actual_layer'],
                         np.load(root/'numeric'/l['asset']['path']),electrode_id=e) for e,l in sorted(by_electrode.items()))
        f = rows[0]['frame']
        products.append(Product(product,(5,4),elements,np.load(root/'numeric/assets/cnt.npy'),
                        design_version='DESIGN',frame=Frame('FRAME',(8,10),0.5,(100,200)),
                        valid=np.ones((8,10)),grid_check={'manifest_candidate_grid':10}))
    from dataclasses import replace
    query = replace(products[0],product_id='QUERY')
    roles,layers = build_vocab(products)
    config = model_config(10,roles,layers)
    model = ProductEncoder(len(roles),len(layers)).eval()
    result = search(model,config,query,products,topk=2,batch_size=1)
    write(root/'dl1_search.json',result)
    bindings=[]
    for p in products:
        for row in context['records']:
            if row['source_identity']['product_id']==p.product_id and row['source_identity']['case_id']=='A':
                bindings.append({k:deepcopy(row[k]) for k in ('roi_id','source_identity','frame','source_assets','source_version')}
                                | {'product_id':p.product_id,'input_identity':input_identity(p)})
    write(root/'dl1_bindings.json',bindings)


def dl2(root, repo):
    import torch
    from psdl2.data import load_export
    from psdl2.index import build_index
    from psdl2.model import Encoder, ModelConfig
    from psdl2.views import ViewConfig
    from psdl2.checkpoint import fingerprint
    from psdl2.search import search
    torch.manual_seed(7)
    import sys
    sys.path.insert(0,str(repo/'tests'))
    from test_end_to_end import synthetic_exchange
    standalone=root/'dl2_standalone';standalone.mkdir(exist_ok=True)
    _,own,own_config,_,_=synthetic_exchange(standalone)
    own_model=Encoder(ModelConfig(**own_config['model']))
    own_index=build_index(own.ready,own_model,ViewConfig.from_dict(own_config['views']),
                          fingerprint({'synthetic_fixture_config':own_config,'seed':7}))
    write(standalone/'index.json',own_index)
    write(standalone/'search.json',search(own_index['records'][0],own_index,{'SYNTHETIC_0'},topk=4,expand=True))
    torch.manual_seed(7)
    config = json.loads((repo/'configs/synthetic.example.json').read_text())['config']
    config['model']['layers']=['M2']
    imported = load_export(root/'review_rois.json',assets_root=root/'numeric',
                           expected_producer_commit='f9d7a31d85eadc14d4b8467f452e3610ea6378cb')
    model = Encoder(ModelConfig(**config['model']))
    fp = fingerprint({'synthetic_untrained_fixture_config':config,
                      'weights':{k:v.tolist() for k,v in model.state_dict().items()}})
    index = build_index(imported.ready,model,ViewConfig.from_dict(config['views']),fp)
    index['blocked_export_records']=[{'roi_id':r['roi_id'],'reasons':r['reasons']} for r in imported.blocked]
    write(root/'dl2_index.json',index)
    query = next(r for r in index['records'] if r['product_id']=='PRODUCT' and r['case_id']=='A')
    result = search(query,index,{'PRODUCT'},topk=10,expand=True)
    write(root/'dl2_search.json',result)
    write(root/'dl2_expected.json',{k:index[k] for k in ('schema_version','checkpoint_fingerprint','view_fingerprint')})
    write(root/'dl2_fixture_config.json',config)


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('mode',choices=['prepare','dl1','dl2']);p.add_argument('out',type=Path);p.add_argument('--repo',type=Path,required=True)
    a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    if a.mode=='prepare':prepare(a.out,a.repo)
    elif a.mode=='dl1':dl1(a.out)
    else:dl2(a.out,a.repo)
