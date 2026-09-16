# synthetic.py contract (demo data generator)

Writes data in the docs/arch/adapter_example.md format. Synthetic CA/hotspot/Fab are
NOT physical simulation results; used only to exercise code paths.

`generate_demo_data(out_dir, *, seed: int = 0) -> dict` returns a summary dict and
writes `summary.json` and `README_SYNTHETIC.txt` (disclaimer text) into out_dir.
Uses numpy default_rng(seed), Pillow for PNG, np.save for NPY. Deterministic.

Content:
- products P1..P6; families: P1,P2 -> F1; P3,P4 -> F2; P5,P6 -> F3.
  fab_id: F1 -> FAB_A, F2 -> FAB_B, F3 -> P5 FAB_A and P6 FAB_B.
- unit sizes (um): P1 60x90, P2 45x70, P3 80x120, P4 50x75, P5 70x100, P6 no
  physical size (unit_width/height/physical_length_unit blank).
- design_lineage_id L<n> per product; P1 also has design_version v2 with
  parent_design_id "P1-v1" and change_description "wider data-pixel gap" (same lineage L1).
- per product-version three bundles:
  lateral_open: electrode a "data", layer M2, b "N/A";
  lateral_short: "data" (M2) + "pixel_electrode" (ITO), assigned_process_layer ITO;
  vertical_short: "gate" (M1) + "data" (M2), assigned_process_layer M2 (explicit).
  lateral_open assigned_process_layer M2. sample_id "<product>-<version>-<code>" with
  code OPEN/LSHORT/VSHORT.
- layout per electrode: 2-D binary array with image size depending on product
  aspect (height 48..96 px, width = round(height*unit_w/unit_h), P6 uses 64x64);
  parallel stripes with a product "gap" parameter; saved as NPY under arrays/.
- hotspot PNG (grayscale) for particle sizes [2, 4, 8]: blurred band where
  electrodes are close, intensity scaled by risk; skip size 8 for P4 samples.
- CA grid [1, 2, 4, 8, 16] um with ca_unit "um^2" and CA = risk * area_scale *
  size**1.5 (+ small noise); P3 uses grid [1, 3, 4, 8, 16]; blank ca_value for size 16
  in P2 samples. P5 and P2 share almost the same gap (similar shape) but P5 risk is 3x
  ("similar_shape_different_risk" in summary).
- fab.json: for each (fab_id in FAB_A/FAB_B, family, layer in M1/M2/ITO) matching the
  bundles, a "probability" distribution with bins lower [0.5,1.5,3,6,12], upper
  [1.5,2.5,5,10,20] (midpoints exactly 1,2,4,8,16), size_unit "um", value_unit "1",
  period "demo". FAB_B skews to larger sizes. id "<fab>-<family>-<layer>".
  Also "REF_FLAT" (is_reference true, fab_id "REFERENCE", family null, layer "ANY",
  flat 0.2) and "FAB_A-COUNT-DEMO" (value_kind "count", surveyed_area 1.0e6,
  area_unit "um^2", family F1, layer "M9") to show the demo rule rejecting counts.
- geometry.json: thickness {"M1": .., "M2": .., "ITO": ..} and shape_metrics
  {"gap": gap, "line_width": ..} for every sample except P4 (no geometry).
- provenance.json: {"calc_settings": {"note": "synthetic"}, "calc_version": "synthetic-0",
  "is_synthetic": true}.
summary keys: n_bundles, products, notes (list of strings incl. disclaimer),
similar_shape_different_risk: ["P2", "P5"].
