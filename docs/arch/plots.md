# plots.py contract (analysis figures as SVG)

No extra dependency: SVG strings are written by hand so Korean text is rendered by the viewer's
fonts. Figures only restate result tables; they add no judgement, importance or causal claims.

- `rank_shift_svg(ranked: pd.DataFrame) -> str`: per `comparison_group_id`, two slope panels
  (`rank_ca_max` -> `rank_ca_area_max`, `rank_caa_reference` -> `rank_caa_actual`). Rank 1 on top.
  The two rows with the largest absolute rank change are coloured; others are grey. Rows with one
  side missing are drawn on the available side only and marked `*`; a panel whose two columns are
  all NaN shows "두 순위 모두 값이 없어 비교할 수 없습니다".
- `ca_curves_svg(ranked) -> str`: per group, `ca@<size>` columns on log-log axes. NaN and
  non-positive values are never filled; lines break there. The row with the largest `ca_max` is
  emphasised. Without `ca@` columns, a notice is drawn instead.
- `loo_stability_svg(loo) -> str`: horizontal bars of `top_items_jaccard` and `neighbor_jaccard`
  (0..1) per `held_out_product`; NaN shows "값 없음".
- `write_analysis_figures(out_dir, ranked, loo) -> list[Path]`: writes the three SVGs to
  `out_dir/figures/` in the order rank_shift, ca_curves, loo_stability.
- Long sample IDs are shortened with "…" (full IDs stay in the CSV files); all text is XML-escaped.
