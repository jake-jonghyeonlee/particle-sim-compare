"""T7: DL2 search JSON → roi_correspondence_v1 → compare-windows, on the real producer fixture."""
import json
from pathlib import Path

from simcompare.adapters.numeric import load_dataset
from simcompare.correspondence import bind_pairs, from_dl2_search, load_correspondence, write_correspondence
from simcompare.local_delta import compute_local_delta
from simcompare.numeric_schema import NumericDataset

HANDOFF = Path(__file__).parent / 'fixtures/handoff'


def test_dl2_search_to_local_delta_round_trip_and_scores_never_enter_numbers(tmp_path):
    search = json.loads((HANDOFF / 'dl2_search.json').read_text())
    index = json.loads((HANDOFF / 'dl2_index.json').read_text())
    export = json.loads((HANDOFF / 'review_rois.json').read_text())
    payload = from_dl2_search([search], index, export, export)
    assert payload['producer']['kind'] == 'dl2_search' and len(payload['pairs']) == len(search['primary'])
    pair = payload['pairs'][0]
    assert pair['source']['checkpoint_fingerprint'] == index['checkpoint_fingerprint']
    assert pair['source']['index_fingerprint'] == index['view_fingerprint'] and pair['source']['rank'] == 1
    assert pair['a']['roi_id'] == search['query']['roi_id'] and pair['b']['roi_id'] == search['primary'][0]['roi_id']
    full = load_dataset(HANDOFF / 'numeric')
    # the exchange fixture holds several products; bind only the DL2 fixture product on both sides
    cases = [c for c in full.cases if c.identity.product_id == 'PRODUCT']
    dataset = NumericDataset(cases, full.pairs, {c.identity.frame_id: full.frames[c.identity.frame_id] for c in cases},
                             [], {}, {}, {}, {'_case_list': True})
    bound = bind_pairs(payload, dataset, dataset)
    assert all(p.status == 'ready' for p in bound)
    result = compute_local_delta(bound, dataset, dataset, window_sizes_um=[1.0], sort_diameter_um=5)
    ranked = result.ranking['by_window_size']['1.0']
    assert result.rows and {e['pair_id'] for e in ranked} == {p['pair_id'] for p in payload['pairs']}
    assert all(e['status'] == 'available' for e in ranked)
    d5 = next(r for r in result.rows if r['diameter_um'] == 5)
    assert d5['window']['a']['union'].status == 'available' and d5['window']['b']['union'].status == 'available'
    # same positions with a manual source: identical numbers, so the DL2 score is provenance only
    manual = json.loads(json.dumps(payload))
    manual['producer'] = {'kind': 'manual', 'version': '1'}
    for row in manual['pairs']:
        row['source'] = {'kind': 'manual', 'labelled_by': 't', 'labelled_at': '2026-10-18'}
    manual_result = compute_local_delta(bind_pairs(manual, dataset, dataset), dataset, dataset, window_sizes_um=[1.0], sort_diameter_um=5)
    assert result.to_dict()['rows'] == manual_result.to_dict()['rows']
    written = write_correspondence(tmp_path / 'dl2_pairs.json', payload)
    assert load_correspondence(written)['pairs'][0]['source']['kind'] == 'dl2_search'
