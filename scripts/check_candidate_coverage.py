#!/usr/bin/env python3
"""Do electrode-count regions cover the labelled positions of a product? (PLAN_v3 T6a)

Read-only analysis. For each requested ``--min-counts`` threshold the electrode-count
regions of the config's ``map.input`` product are computed at the representative
diameter, and every labelled centre of that product taken from a
``roi_correspondence_v1`` file is tested: inside a region's cells (exact), inside a
region bbox, and the distance to the nearest region centre of the same layer.
The report says whether map regions can serve as the DL2 candidate-ROI source
before real data; it changes no schema and writes only ``coverage.json/csv``.

Usage:
  python scripts/check_candidate_coverage.py --config cfg.yaml --correspondence pairs.json \
      --min-counts 2 3 4 [--diameter-um 5] --out report_dir
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))


def _cell(frame, center):
    x0, y0, x1, y1 = frame.bbox_um
    if not (x0 <= center[0] < x1 and y0 <= center[1] < y1):
        return None
    return int((center[1] - y0) / frame.pixel_pitch_um), int((center[0] - x0) / frame.pixel_pitch_um)


def coverage_report(dataset, payload, *, min_counts, diameter_um):
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.numeric_schema import NumericContractError, finite
    products = {case.identity.product_id for case in dataset.cases}
    if len(products) != 1:
        raise NumericContractError('single_declared_product_required', 'map.input')
    product = next(iter(products))
    centres = []
    for pair in payload['pairs']:
        if pair['status'] != 'ready':
            continue
        for label in ('a', 'b'):
            side = pair[label]
            if side['product_id'] == product:
                centres.append({'pair_id': pair['pair_id'], 'side': label, 'center_xy_um': list(side['center_xy_um']),
                                'analysis_layer': side['analysis_layer'], 'frame_id': side['frame_id']})
    thresholds = sorted({int(k) for k in min_counts})
    if not thresholds or any(k < 1 for k in thresholds):
        raise NumericContractError('invalid_min_counts')
    rows, by_threshold = [], {}
    for k in thresholds:
        maps = compute_electrode_maps(dataset, diameter_um=diameter_um, min_count=k)
        layers = {}
        for layer in maps.layers:
            layers.setdefault((layer.key.layer, layer.frame.frame_id), []).append(layer)
        covered_mask = covered_bbox = 0
        for centre in centres:
            matches = layers.get((centre['analysis_layer'], centre['frame_id']), [])
            regions = [r for layer in matches for r in layer.regions]
            inside_mask, inside_bbox, nearest, nearest_id, cell = False, False, None, None, None
            for region in regions:
                cell = _cell(region.frame, centre['center_xy_um'])
                if cell is not None and bool(region.mask[cell]):
                    inside_mask = True
                bx0, by0, bx1, by1 = region.region_bbox_um
                if bx0 <= centre['center_xy_um'][0] < bx1 and by0 <= centre['center_xy_um'][1] < by1:
                    inside_bbox = True
                d = math.dist(centre['center_xy_um'], region.center_xy_um)
                if nearest is None or d < nearest:
                    nearest, nearest_id = d, region.region_id
            covered_mask += inside_mask
            covered_bbox += inside_bbox
            rows.append({'min_count': k, **centre, 'layer_maps': len(matches), 'regions_in_layer': len(regions),
                         'inside_region_cells': inside_mask, 'inside_region_bbox': inside_bbox,
                         'nearest_region_center_distance_um': nearest, 'nearest_region_id': nearest_id,
                         'position_in_frame': cell is not None or not regions})
        by_threshold[str(k)] = {'n_centres': len(centres), 'covered_by_region_cells': covered_mask,
                                'covered_by_region_bbox': covered_bbox,
                                'fraction_covered_by_region_cells': covered_mask / len(centres) if centres else None,
                                'n_regions': sum(len(layer.regions) for layer in maps.layers),
                                'regions_per_layer': {layer.key.layer: len(layer.regions) for layer in maps.layers}}
    report = {'schema_version': 'candidate_coverage_v1', 'product_id': product, 'representative_diameter_um': finite(diameter_um, 'diameter', positive=True),
              'min_counts': thresholds, 'n_labelled_centres': len(centres), 'by_min_count': by_threshold,
              'status': 'available' if centres else 'unavailable',
              'reasons': [] if centres else ['no_labelled_centres_for_product'],
              'note': '지도 영역이 DL2 후보 ROI 출처로 충분한지 보는 읽기 전용 점검이다. 격자 후보(grid_candidate)는 별도 schema v2 계획이다.'}
    return report, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Labelled-position coverage by electrode-count regions (read-only).')
    parser.add_argument('--config', required=True, help='numeric_config_v1 YAML; map.input is the product checked')
    parser.add_argument('--correspondence', required=True, help='roi_correspondence_v1 JSON')
    parser.add_argument('--min-counts', required=True, nargs='+', type=int, help='electrode-count thresholds to test')
    parser.add_argument('--diameter-um', type=float, default=None, help='representative diameter; default = map.diameter_um or 5')
    parser.add_argument('--out', required=True, help='report directory')
    args = parser.parse_args(argv)
    try:
        from simcompare.correspondence import load_correspondence
        from simcompare.pipeline import _dataset_for, load_numeric_config
        cfg = load_numeric_config(args.config)
        spec = cfg.get('map') or {}
        dataset = _dataset_for(cfg, spec.get('input'), 'map.input')
        diameter = args.diameter_um if args.diameter_um is not None else spec.get('diameter_um', 5)
        report, rows = coverage_report(dataset, load_correspondence(args.correspondence),
                                       min_counts=args.min_counts, diameter_um=diameter)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'coverage.json').write_text(json.dumps({**report, 'rows': rows}, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        columns = list(dict.fromkeys(k for row in rows for k in row)) or ['status']
        with (out / 'coverage.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, columns, lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict, bool)) or v is None else v for k, v in row.items()})
        print(json.dumps({'status': report['status'], 'reasons': report['reasons'], 'by_min_count': report['by_min_count'],
                          'outputs': [str(out / 'coverage.json'), str(out / 'coverage.csv')]}, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'available' else 3
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
