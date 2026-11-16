"""Electrode pairs that are left out of the calculation (2026-11-16, run pexcl).

Two sources, decided per pair of electrode names (exact, case-sensitive string match; the order of a pair never matters):

  1. pairs.csv rows with `class = excluded` (reason `pairs_csv:excluded`);
  2. the built-in rule below: any pair with an electrode named GH or N_NODE, and the pair VBIAS-VINT1.

Order of the decision (the first line that applies wins):
  - the pair is registered in pairs.csv as line / pixel  -> it is computed (a built-in rule that overlaps is only recorded);
  - the pair is registered as `excluded`                 -> left out;
  - the built-in rule applies                            -> left out (reason: the first match, GH, N_NODE, VBIAS-VINT1);
  - otherwise                                            -> not decided here (the case stays `unmapped_pair`).

A left-out case never enters the dataset (see adapters.real_hotspot._split_pair_exclusions): no union, no 1/n share, no
coverage, no group count. CNO cases (one electrode, no pair) are never left out.

No heavy import here: the module is read by the loader, the hash function and the reports alike.
"""
from __future__ import annotations

import copy
from typing import Dict, Iterable, List, Optional

# The version string of the rule. It is part of `input_hash` whenever at least one case is left out, and compare2
# refuses a result made under another string. CHANGE THIS STRING whenever the built-in lists below, the order of the
# decision or the meaning of `class = excluded` changes; otherwise stale results are accepted.
PAIR_EXCLUSION_RULE = 'pair_excluded_gh_n_node_vbias_vint1_2026_11_16'

EXCLUDED_CLASS = 'excluded'                                   # the pairs.csv class value
BUILTIN_EXCLUDED_ELECTRODES = ('GH', 'N_NODE')                # exact names; tested in this order
BUILTIN_EXCLUDED_PAIRS = (('VBIAS', 'VINT1'),)                # compared as unordered pairs
REASON_TABLE = 'pairs_csv:excluded'


def builtin_reason(electrode_a: str, electrode_b: str) -> Optional[str]:
    """The reason of the first built-in rule that matches the pair (or the same-name case a/a), else None."""
    for name in BUILTIN_EXCLUDED_ELECTRODES:
        if name == electrode_a or name == electrode_b:
            return 'builtin:' + name
    key = frozenset((electrode_a, electrode_b))
    for first, second in BUILTIN_EXCLUDED_PAIRS:
        if key == frozenset((first, second)):
            return 'builtin:%s-%s' % (first, second)
    return None


def exclusion_reason(electrode_a: str, electrode_b: str, registered, table_excluded) -> Optional[str]:
    """Why the pair is left out, or None when it is computed or not decided here.

    registered: frozenset keys of the line/pixel pairs of pairs.csv; table_excluded: frozenset keys of its excluded rows."""
    key = frozenset((electrode_a, electrode_b))
    if key in registered:
        return None
    if key in table_excluded:
        return REASON_TABLE
    return builtin_reason(electrode_a, electrode_b)


def overridden_reason(electrode_a: str, electrode_b: str, registered) -> Optional[str]:
    """The built-in reason that a registered line/pixel pair overrides (None when no built-in rule applies)."""
    if frozenset((electrode_a, electrode_b)) in registered:
        return builtin_reason(electrode_a, electrode_b)
    return None


def build_block(case_records: Iterable[dict], overridden: Iterable[dict]) -> Optional[dict]:
    """The `pair_exclusions` block of the dataset provenance; None when no case was left out.

    case_records: one dict per left-out case: case, electrodes (sorted list), failure_type, analysis_layer (or None),
    presence ('discovered' | 'expected_only'), observed_diameters, reason, pair_id (None for a built-in reason).
    Every list is sorted, so equal input gives equal bytes."""
    records = sorted(case_records, key=lambda r: r['case'])
    if not records:
        return None
    by_reason: Dict[str, int] = {}
    pair_groups: Dict[tuple, dict] = {}
    for record in records:
        by_reason[record['reason']] = by_reason.get(record['reason'], 0) + 1
        key = (tuple(record['electrodes']), record['reason'], record['pair_id'])
        group = pair_groups.setdefault(key, {'electrodes': list(record['electrodes']), 'pair_id': record['pair_id'],
                                             'reason': record['reason'], 'cases': 0, 'layers': set()})
        group['cases'] += 1
        if record['analysis_layer'] is not None:
            group['layers'].add(record['analysis_layer'])
    pairs: List[dict] = []
    for key in sorted(pair_groups, key=lambda k: (k[0], k[1], k[2] or '')):
        group = pair_groups[key]
        pairs.append({'electrodes': group['electrodes'], 'pair_id': group['pair_id'], 'reason': group['reason'],
                      'cases': group['cases'], 'analysis_layers': sorted(group['layers'])})
    discovered = sum(1 for r in records if r['presence'] == 'discovered')
    return {'rule': PAIR_EXCLUSION_RULE,
            'builtin': {'electrodes': list(BUILTIN_EXCLUDED_ELECTRODES),
                        'pairs': [list(pair) for pair in BUILTIN_EXCLUDED_PAIRS]},
            'excluded_cases': discovered, 'excluded_expected_cases': len(records) - discovered,
            'by_reason': {reason: by_reason[reason] for reason in sorted(by_reason)},
            'pairs': pairs,
            'cases': [{'case': r['case'], 'electrodes': list(r['electrodes']), 'failure_type': r['failure_type'],
                       'analysis_layer': r['analysis_layer'], 'presence': r['presence'],
                       'observed_diameters': r['observed_diameters'], 'reason': r['reason']} for r in records],
            'builtin_overridden_by_table': sorted((dict(item) for item in overridden),
                                                  key=lambda i: (i['electrodes'], i['pair_id']))}


def record_block(provenance) -> Optional[dict]:
    """The block to write into compare2.json / candidates from a dataset provenance; None when nothing was left out
    (the caller then writes no key and the output bytes stay those of an input without exclusions)."""
    raw = (provenance or {}).get('pair_exclusions')
    return copy.deepcopy(raw) if raw else None
