# T2 — comparison.json payload measurement (2026-10-22)

Fixture: `scripts/make_compare_fixture.py --out D --cases N --size 200 --seed 1 --shared 3` (generator committed, data not).
Reference == target model, one layer, 32 diameters, 70 % line / 30 % pixel pairs, clusters shared by 3 cases so shared
buckets occur. Measured with `scripts/measure_compare_payload.py D/config.yaml` (compare runs in its own child process;
RSS = that child's `ru_maxrss`). Python 3.11, numpy from `.venv`, macOS arm64, baseline = HEAD 01989de + uncommitted T1.

## Step 1 — v1 breakdown (compact-JSON bytes of the same payload; the v1 file itself is `indent=2`)

| N | v1 file | compact total | raw (`source_cases[*].raw`) | `pair_lineage_case_keys` | `contributing_source_case_keys` | rest |
|---|---|---|---|---|---|---|
| 50 | 176,232,765 | 96,023,900 | 73,824,896 (76.9 %) | 3,547,380 (3.7 %) | 3,549,364 (3.7 %) | 15,102,260 (15.7 %) |
| 100 | 371,259,263 | 204,330,136 | 147,650,944 (72.3 %) | 10,983,808 (5.4 %) | 10,985,792 (5.4 %) | 34,709,592 (17.0 %) |

Lineage + contributors are 7.4 % (N=50) and 10.8 % (N=100) of v1 bytes — below the 30 % threshold, so **no integer-index
encoding**: v2 keeps every `pair_ids`/lineage/contributors list as the same strings. raw is written once per case
instead of once per (case × diameter). Growth is super-linear (N×2 → compact ×2.13, lineage ×3.1) because shared buckets
repeat lineage.

## Step 2 — v2 result (`real_hotspot_comparison_v2`, compact, raw once per case)

| N | v2 file | v2 / v1 file | v1 peak RSS | v2 peak RSS | wall v1 → v2 |
|---|---|---|---|---|---|
| 50 | 24,627,960 | 14.0 % | 1073.2 MiB | 162.9 MiB | 6.08 s → 1.90 s |
| 100 | 61,537,112 | 16.6 % | 2276.3 MiB | 399.9 MiB | 12.80 s → 4.04 s |

`comparison.csv` is byte-identical to v1 on both fixtures.

Target "≤ 10 % of v1 at the larger size" is **not met** (16.6 %). v2 composition at N=100 (per side 28.1 MB): groups 25.8 MB,
cases_raw 2.3 MB; `records` 5.3 MB. Inside groups the lineage/contributors strings are 22.5 MB of 61.5 MB (36 % of v2 bytes).
An offline estimate that index-encodes them per group (`source_index`) gives ≈ 39.9 MB = 10.7 % of v1 — still marginally
above 10 % — so it was not adopted under the stated rule (decide from v1 numbers, >30 %). The remaining mass is
`source_cases[*].measurement` (~0.6 KB/source), identity/endpoints (~0.35 KB/source) and bucket/pair metadata. If a
further cut is wanted: index encoding (≈ −21.6 MB) and a shared `identity`/`endpoints` table; both are lossless but are
decisions for the coordinator.
