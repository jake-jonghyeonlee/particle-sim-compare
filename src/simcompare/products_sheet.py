"""`products-sheet` (pasx/T7): the asset sheet of the registered products, rebuilt from the asset files every time.

  products-sheet --config products.yaml [--now 2026-11-27T09:00:00Z]

Writes exactly two files, nothing else (no asset, no generated settings, no folder below data/products/<P>):

  data/products/products_sheet.csv    one line per product (UTF-8 with BOM, opens in Excel); the HTML is the reference
  data/products/products_sheet.html   the same table with the long lists unfolded (one file, no external resource)

Lines: every registered product, then every folder `data/products/<P>/` of a product that left products.yaml
(state `등록 없음`). A product with 01 only is one line; its 02 columns are empty and its state is `없음`.

Sources (nothing is computed here; every value is shown as the asset files and products.yaml hold it):
  identity, input places, layers       products.yaml
  state (정상 / 낡음 / 없음 / 손상)       `products_assets.check_assets` (read only, no mask is read)
  case counts, sizes, indicators       manifest.json of the hotspots generation, summary.json (compare2-summary)
  assets: times, hashes                complete.json of the hotspots and side generations, side.json meta
  last run                             last line of asset_log.jsonl
  pairs                                data/compare/<A>__<B>/ledger_report/execution.json
  DL1 electrodes                       `scan_electrode_shapes` (names only)

Write guarantee (PLAN-v2 3.7, v2.1 J): each file is a temporary file + os.replace (a file is old or new, never half
written). Both files carry the same `sheet_id` (a hash of the table content; the update time is not part of it), so a
reader can see that the two files do not belong together after an interruption between the two replaces.
CSV rule: a text cell that starts with = + - @ TAB CR gets a leading `'`; a hash cell is `sha256:` + the first 12 digits.
"""
from __future__ import annotations

import copy
import csv
import datetime
import hashlib
import html
import io
import json
import math
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import compare2_side, products_assets as pa, products_config as pc
from .numeric_schema import NumericContractError

SHEET_SCHEMA = 'products_sheet_v1'
CSV_NAME = 'products_sheet.csv'
HTML_NAME = 'products_sheet.html'
UNREGISTERED = '등록 없음'
CHECK_FAILED = '판정 실패'
FORMULA_START = ('=', '+', '-', '@', '\t', '\r')

# (key, header, group, kind). kind: text | num | hash | long (a list that the HTML unfolds)
FRONT_GROUP = '요약'      # the first nine columns; every other group can be folded in the HTML (default folded)
COLUMNS: List[Tuple[str, str, str, str]] = [
    ('product', '제품', FRONT_GROUP, 'text'), ('group', '그룹', FRONT_GROUP, 'text'),
    ('asset_state', '자산 상태', FRONT_GROUP, 'text'), ('reasons', '이유 코드', FRONT_GROUP, 'long'),
    ('vi_line', 'vi_line', FRONT_GROUP, 'num'), ('vi_pixel', 'vi_pixel', FRONT_GROUP, 'num'),
    ('last_run', '마지막 실행', FRONT_GROUP, 'text'), ('pairs', '쌍(마지막 08)', FRONT_GROUP, 'long'),
    ('note', 'note', FRONT_GROUP, 'text'),
    ('product_id', 'product_id', '식별 상세', 'text'), ('family', '제품군', '식별 상세', 'text'),
    ('resolution', '해상도', '식별 상세', 'text'), ('unit_size', 'unit 크기(um)', '식별 상세', 'text'),
    ('hotspot_root', 'hotspot 폴더', '입력 위치', 'text'), ('dl1_model', 'DL1 모델', '입력 위치', 'text'),
    ('pair_table', 'pairs.csv', '입력 위치', 'text'), ('distance_table', '거리표', '입력 위치', 'text'),
    ('layer_count', '층 수', '층', 'num'), ('layer_names', '층 이름(stack_order 순)', '층', 'long'),
    ('cases_short', 'short case 수(제외 전)', 'case', 'num'), ('cases_cno', 'CNO case 수', 'case', 'num'),
    ('cases_excluded', '제외 case 수', 'case', 'num'), ('excluded_expected_cases', '제외 기대 case 수', 'case', 'num'),
    ('conds_line', 'line 조건 수', 'case', 'num'),
    ('conds_pixel', 'pixel 조건 수', 'case', 'num'), ('expected_cases', '기대 case 수(제외 전)', 'case', 'num'),
    ('absent_cases', 'absent case 수', 'case', 'num'), ('union_sha', '그룹 합집합', 'case', 'hash'),
    ('diam_min', '관측 지름 최소(um)', '크기', 'num'), ('diam_max', '관측 지름 최대(um)', '크기', 'num'),
    ('diam_count', '관측 지름 수', '크기', 'num'),
    ('caa_line', 'caa_line', '지표 상세', 'num'), ('caa_pixel', 'caa_pixel', '지표 상세', 'num'),
    ('lam_line', 'λ line', '지표 상세', 'num'), ('lam_pixel', 'λ pixel', '지표 상세', 'num'),
    ('lam_cno', 'λ cno', '지표 상세', 'num'),
    ('density_line_sha', '밀도 line', '밀도', 'hash'), ('density_pixel_sha', '밀도 pixel', '밀도', 'hash'),
    ('diam_subset', '지름 부분집합', '밀도', 'text'),
    ('dl1_electrodes', 'DL1 전극 수', 'DL1', 'num'),
    ('created_01', '01 만든 시각', '자산', 'text'), ('elapsed_01', '01 소요(s)', '자산', 'num'),
    ('created_02', '02 만든 시각', '자산', 'text'), ('elapsed_02', '02 소요(s)', '자산', 'num'),
    ('manifest_sha', 'manifest', '자산', 'hash'), ('input_sha', 'input', '자산', 'hash'),
    ('code_sha_01', 'code 01', '자산', 'hash'), ('code_sha_02', 'code 02', '자산', 'hash'),
    ('side_basis', 'side_basis', '자산', 'text'),
    ('state_01', '01 상태', '세부 상태', 'text'), ('state_side', 'side 상태', '세부 상태', 'text'),
    ('state_summary', 'summary 상태', '세부 상태', 'text'),
    ('metric_status', '지표 상태', '세부 상태', 'text'),
    ('maps_src_status', '지도 원자료 상태', '세부 상태', 'text'), ('size_mb', '자산 폴더(MB)', '세부 상태', 'num'),
]
KEYS = [column[0] for column in COLUMNS]
KIND = {column[0]: column[3] for column in COLUMNS}
STATE_CLASS = {pa.OK: 'ok', pa.STALE: 'stale', pa.MISSING: 'missing', pa.CORRUPT: 'corrupt', UNREGISTERED: 'orphan',
               CHECK_FAILED: 'corrupt'}
READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, NumericContractError)


# ------------------------------------------------------------------------------------------------------------ reading

def _json_file(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _open(base, product: str, kind: str, verdict_generation=None):
    if verdict_generation is not None:
        return verdict_generation
    try:
        return pa.open_generation(base, product, kind)
    except READ_ERRORS:
        return None


def _stamp(value) -> Optional[str]:
    """A time as `YYYY-MM-DDTHH:MM:SSZ` (the asset files write UTC)."""
    if not isinstance(value, str):
        return None
    text = value[:-6] if value.endswith('+00:00') else value
    text = text.rstrip('Z')
    return text.split('.')[0] + 'Z' if 'T' in text else value


def _folder_size_mb(root: Path) -> Optional[float]:
    total = 0
    stack = [root]
    try:
        while stack:
            for entry in os.scandir(str(stack.pop())):
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    stack.append(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
    except OSError:
        return None
    return round(total / 1048576.0, 3)


def _last_run(product_dir: Path) -> Optional[str]:
    path = product_dir / pa.ASSET_LOG
    last = None
    try:
        for raw in path.read_bytes().decode('utf-8', 'replace').splitlines():          # a bad byte must not hide the line
            try:
                line = json.loads(raw)
            except ValueError:
                continue                          # a half-written last line is skipped, not an error
            if isinstance(line, dict):
                last = line
    except OSError:
        return None
    if last is None:
        return None
    failed = last.get('status') == 'fail' or last.get('action') == 'fail'
    text = '%s %s %s' % ('실패' if failed else '성공', last.get('step', ''), last.get('at', ''))
    if failed and last.get('error_code'):
        text += ' (%s)' % last['error_code']
    return text


def _pair_index(base: Path, names: Sequence[str]) -> Dict[str, List[str]]:
    """{product: ['<A>-><B> @ <time of the last 08>', ...]} from the folders data/compare/<A>__<B>/."""
    known = set(names)
    found: Dict[str, List[str]] = {}
    root = base / 'data' / 'compare'
    try:
        folders = sorted(entry.name for entry in os.scandir(str(root)) if entry.is_dir() and not entry.is_symlink())
    except OSError:
        return found
    for folder in folders:
        parts = folder.split('__')
        if len(parts) != 2 or parts[0] not in known or parts[1] not in known:
            continue
        at = None
        bad = False
        try:
            execution = _json_file(root / folder / 'ledger_report' / 'execution.json')
            at = _stamp(execution.get('finished_at'))
            status, exit_code = execution.get('status'), execution.get('exit_code')
            bad = (status is not None and status != 'ok') or (exit_code is not None and exit_code != 0)
        except READ_ERRORS:
            pass
        label = '%s->%s @ %s%s' % (parts[0], parts[1], at or '08 없음', ' (error)' if bad else '')
        for name in parts:
            found.setdefault(name, []).append(label)
    return found


def _dl1_count(config: Optional[pc.ProductsConfig], product: str) -> Optional[int]:
    if config is None or product not in config.products:
        return None
    model = config.products[product].get('dl1_model')
    root = (config.common.get('report') or {}).get('dl1_root')
    if not model or not root:
        return None
    try:
        from .adapters.electrode_png import scan_electrode_shapes
        return len(scan_electrode_shapes(config.base / root, model))
    except READ_ERRORS:
        return None


def _layer_names(section: dict) -> List[str]:
    stack = (section.get('condition_assignment') or {}).get('layer_stack') or []

    def order(item):
        value = item.get('stack_order')
        return (0, value) if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, 0)
    return [str(item.get('actual_layer')) for item in sorted((i for i in stack if isinstance(i, dict)), key=order)]


def _read_summary(product_dir: Path) -> Tuple[Optional[dict], str]:
    """(summary document, metric status). Status: summary `status` (available / partial), `없음`, or `손상`."""
    path = product_dir / pa.SUMMARY
    if not path.exists():
        return None, pa.MISSING
    try:
        document = _json_file(path)
        if not isinstance(document, dict):
            raise ValueError('not a mapping')
    except (OSError, ValueError):
        return None, pa.CORRUPT
    return document, str(document.get('status'))


def _fill_summary(row: dict, summary: dict) -> None:
    for key in ('vi_line', 'vi_pixel', 'caa_line', 'caa_pixel'):
        row[key] = summary.get(key)
    lam = summary.get('lambda') or {}
    row['lam_line'], row['lam_pixel'], row['lam_cno'] = lam.get('line'), lam.get('pixel'), lam.get('cno')
    observed = summary.get('observed_diameters_um') or []
    if observed:
        row['diam_min'], row['diam_max'] = min(observed), max(observed)
    row['diam_count'] = len(observed)
    conditions = summary.get('conditions') or {}
    row['conds_line'], row['conds_pixel'] = conditions.get('line'), conditions.get('pixel')
    row['absent_cases'] = summary.get('absent_expected_cases')
    exclusions = summary.get('case_exclusions') or {}
    row['cases_excluded'] = exclusions.get('excluded_cases')
    row['excluded_expected_cases'] = exclusions.get('excluded_expected_cases')
    deps = summary.get('deps') or {}
    density = deps.get('density_sha256') or {}
    row['density_line_sha'], row['density_pixel_sha'] = density.get('line'), density.get('pixel')
    subset = deps.get('lambda_diameters_um')
    row['diam_subset'] = '전체' if subset is None else ' '.join(str(item) for item in subset)


def _fill_hotspots(row: dict, generation) -> None:
    complete = generation.complete
    row['created_01'], row['elapsed_01'] = complete.get('created_at'), complete.get('elapsed_s')
    row['code_sha_01'] = complete.get('code_sha256')
    row['manifest_sha'] = (complete.get('verification') or {}).get('manifest_sha256')
    row['union_sha'] = (complete.get('fingerprint') or {}).get('group_union_sha256')
    manifest = _json_file(generation.path / 'manifest.json')
    row['cases_short'] = len(manifest.get('cases') or [])
    row['cases_cno'] = len(manifest.get('cno_cases') or [])
    expected = manifest.get('expected_cases')
    row['expected_cases'] = len(expected) if isinstance(expected, (list, tuple)) else None      # no list = blank, not 0


def _fill_side(row: dict, generation) -> None:
    complete = generation.complete
    row['created_02'], row['elapsed_02'] = complete.get('created_at'), complete.get('elapsed_s')
    row['code_sha_02'] = complete.get('code_sha256')
    row['side_basis'] = (complete.get('fingerprint') or {}).get('side_basis')
    row['maps_src_status'] = (complete.get('maps_src') or {}).get('status')
    document, _sha = compare2_side.read_side(generation.path)
    row['input_sha'] = (document.get('meta') or {}).get('input_hash')


def _guard(action: Callable[[], None]) -> None:
    try:
        action()
    except READ_ERRORS:
        pass


def product_row(base: Path, product: str, config: Optional[pc.ProductsConfig], results: Optional[dict],
                pairs: Dict[str, List[str]], record_maps: bool = True,
                group_error: Optional[str] = None) -> Dict[str, object]:
    """One sheet line. `config` None / product not registered: the line of a left-over folder (state `등록 없음`).
    record_maps False: `maps_src_missing` does not make the state 낡음 (a run without recording). group_error: the
    group of this product does not hold together (the codes); the line says `판정 실패` without a mask or asset check."""
    row: Dict[str, object] = {key: None for key in KEYS}
    row['product'] = product
    registered = config is not None and product in config.products
    product_dir = pa.products_dir(base, product)
    hotspots = side = None
    if registered:
        section = config.products[product]
        assignment = section.get('condition_assignment') or {}
        row.update({'group': config.group_of(product), 'note': section.get('note'),
                    'product_id': assignment.get('product_id'), 'family': section.get('model_group'),
                    'resolution': 'x'.join(str(v) for v in section.get('resolution') or []),
                    'unit_size': 'x'.join(str(v) for v in section.get('unit_size_um') or []) or None,
                    'hotspot_root': section.get('hotspot_root'), 'dl1_model': section.get('dl1_model'),
                    'pair_table': assignment.get('pair_table_path'), 'distance_table': assignment.get('interlayer_distance_path')})
        names = _layer_names(section)
        row['layer_count'], row['layer_names'] = len(names), ' | '.join(names)
        row['dl1_electrodes'] = _dl1_count(config, product)
        try:
            if group_error is not None:
                raise NumericContractError('group_incompatible', group_error)
            verdicts = pa.check_assets(base, product, config, results, record_maps=record_maps)
            order = (pa.CORRUPT, pa.MISSING, pa.STALE, pa.OK)
            row['asset_state'] = min((v.state for v in verdicts.values()), key=order.index)
            row['reasons'] = ' '.join(pa._unique([r for key in ('hotspots', 'side', 'summary') for r in verdicts[key].reasons]))
            row['state_01'], row['state_side'] = verdicts['hotspots'].state, verdicts['side'].state
            row['state_summary'] = verdicts['summary'].state
            hotspots, side = verdicts['hotspots'].generation, verdicts['side'].generation
        except Exception as exc:                  # one product that cannot be judged must not stop the sheet
            row['asset_state'] = CHECK_FAILED
            row['reasons'] = getattr(exc, 'code', type(exc).__name__)
    else:
        row['asset_state'] = UNREGISTERED
    hotspots = _open(base, product, 'hotspots', hotspots)
    side = _open(base, product, 'side', side)
    if hotspots is not None:
        _guard(lambda: _fill_hotspots(row, hotspots))
    if side is not None:
        _guard(lambda: _fill_side(row, side))
    summary, row['metric_status'] = _read_summary(product_dir)
    if summary is not None:
        _guard(lambda: _fill_summary(row, summary))
        if not registered:                        # a left-over folder: what the summary recorded is all there is
            deps_product = (summary.get('deps') or {}).get('product') or {}
            row['family'] = ((summary.get('deps') or {}).get('model_group') or {}).get('name')
            row['resolution'] = 'x'.join(str(v) for v in deps_product.get('resolution') or []) or None
            row['unit_size'] = 'x'.join(str(v) for v in deps_product.get('unit_size_um') or []) or None
    row['last_run'] = _last_run(product_dir)
    row['size_mb'] = _folder_size_mb(product_dir)
    row['pairs'] = ' | '.join(pairs.get(product, [])) or None
    for key, value in row.items():                # NaN / inf cannot go into the sheet_id hash; shown as text
        if isinstance(value, float) and not math.isfinite(value):
            row[key] = 'NaN' if value != value else ('Infinity' if value > 0 else '-Infinity')
    return row


def _failed_row(product: str, config: Optional[pc.ProductsConfig], exc: BaseException) -> Dict[str, object]:
    row: Dict[str, object] = {key: None for key in KEYS}
    row['product'] = product
    try:
        row['group'] = config.group_of(product) if config is not None else None
    except KeyError:
        pass
    row['asset_state'] = CHECK_FAILED
    row['reasons'] = getattr(exc, 'code', None) or type(exc).__name__
    return row


def _check_each_group(config: pc.ProductsConfig):
    """({group: GroupResult} of the groups that hold together, {group: error text}, [violation dicts]).
    One group that does not hold together does not make the other groups' products fail (R7:4)."""
    results: dict = {}
    errors: Dict[str, str] = {}
    violations: List[dict] = []
    for group, members in config.groups.items():
        single = copy.copy(config)
        single.groups = {group: members}
        try:
            results.update(pc.check_groups(single))
        except pc.GroupCompatibilityError as exc:
            violations.extend(exc.violations)
            errors[group] = str(exc)
        except NumericContractError as exc:
            errors[group] = '%s: %s' % (exc.code, exc)
            violations.append({'code': exc.code, 'group': group, 'product': None, 'case': None})
    return results, errors, violations


def violation_summary(violations: Sequence[dict]) -> str:
    """`그룹 G: code ×n, code ×m; 그룹 H: ...` (one line for the HTML head), empty when there is none."""
    counts: Dict[str, Dict[str, int]] = {}
    for item in violations:
        per_group = counts.setdefault(str(item.get('group')), {})
        per_group[str(item.get('code'))] = per_group.get(str(item.get('code')), 0) + 1
    return '; '.join('그룹 %s: %s' % (group, ', '.join('%s ×%d' % (code, number) for code, number in sorted(codes.items())))
                     for group, codes in sorted(counts.items()))


def build_rows(config: pc.ProductsConfig, record_maps: bool = True) -> List[Dict[str, object]]:
    return build_sheet(config, record_maps)[0]


def build_sheet(config: pc.ProductsConfig, record_maps: bool = True) -> Tuple[List[Dict[str, object]], List[dict]]:
    """(rows, group violations). A product that cannot be judged gets its own `판정 실패` line; the sheet goes on."""
    base = config.base
    results, group_errors, violations = _check_each_group(config)
    names = list(config.products)
    root = base / 'data' / 'products'
    left = []
    try:
        left = sorted(entry.name for entry in os.scandir(str(root))
                      if entry.is_dir() and not entry.is_symlink() and pc.NAME_RE.fullmatch(entry.name)
                      and '__' not in entry.name and entry.name not in config.products)     # `_groups` etc. are not products
    except OSError:
        pass
    pairs = _pair_index(base, names + left)
    rows = []
    for name in names + left:
        try:
            error = group_errors.get(config.group_of(name)) if name in config.products else None
            rows.append(product_row(base, name, config, results, pairs, record_maps, error))
        except Exception as exc:                  # one line that cannot be built must not stop the sheet
            rows.append(_failed_row(name, config, exc))
    return rows, violations


# ----------------------------------------------------------------------------------------------------------- rendering

def sheet_id(rows: Sequence[dict]) -> str:
    """A hash of the table content (full values, no update time): the two files carry the same one."""
    payload = json.dumps({'schema': SHEET_SCHEMA, 'columns': KEYS, 'rows': list(rows)}, ensure_ascii=False,
                         sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _csv_cell(key: str, value) -> str:
    if value is None:
        return ''
    if KIND[key] == 'hash':
        return 'sha256:' + str(value)[:12]
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def render_csv(rows: Sequence[dict], sid: str) -> bytes:
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow([column[1] for column in COLUMNS] + ['sheet_id'])
    for row in rows:
        writer.writerow([_csv_cell(key, row.get(key)) for key in KEYS] + [sid])
    return b'\xef\xbb\xbf' + buffer.getvalue().encode('utf-8')


CSS = """
:root{color-scheme:light}
body{margin:16px;background:#fff;color:#1b1f23;font:13px/1.45 -apple-system,"Segoe UI","Malgun Gothic",sans-serif}
h1{font-size:18px;margin:0 0 4px}
.meta{color:#444;margin:0 0 10px}.warn{background:#fff4ce;border:1px solid #c9a227;padding:6px 10px;margin:8px 0}
input{padding:4px 8px;margin:6px 0;width:260px}
.wrap{overflow:auto;max-height:80vh;border:1px solid #bbb}
table{border-collapse:collapse}
th,td{border:1px solid #ccc;padding:3px 7px;white-space:nowrap;text-align:left;vertical-align:top;background:#fff;color:#1b1f23}
th{background:#e8ecf0;position:sticky;top:0;z-index:2}tr.g th{top:0;background:#d5dce4}
td.n{text-align:right;font-variant-numeric:tabular-nums}
td.c1,th.c1{position:sticky;left:0;z-index:1;font-weight:600}th.c1{z-index:3}
.st-ok{background:#c8ecc9;color:#12321a}.st-stale{background:#ffe08a;color:#3d2e00}
.st-missing{background:#d9dde3;color:#222}.st-corrupt{background:#f6b4b4;color:#4a0b0b}.st-orphan{background:#cfe0f7;color:#0f2a52}
.fail{background:#f6b4b4;color:#4a0b0b}
details summary{cursor:pointer}details div{white-space:normal;min-width:260px;max-width:520px}
th.gh{cursor:pointer;user-select:none}.stub{display:none;min-width:28px;text-align:center;color:#888}
.viol{background:#fde2e2;border:1px solid #c44;padding:6px 10px;margin:8px 0}
code{font-family:ui-monospace,Menlo,Consolas,monospace}
"""

SCRIPT = """
var box=document.getElementById('q');
box.addEventListener('input',function(){var q=box.value.toLowerCase();
document.querySelectorAll('tbody tr').forEach(function(r){r.style.display=r.textContent.toLowerCase().indexOf(q)<0?'none':''});});
function fold(th,open){var i=th.getAttribute('data-g'),name=th.getAttribute('data-name'),span=th.getAttribute('data-span');
document.querySelectorAll('.g'+i).forEach(function(c){c.style.display=open?'':'none'});
document.querySelectorAll('.s'+i).forEach(function(c){c.style.display=open?'none':'table-cell'});
th.colSpan=open?+span:1;th.setAttribute('data-open',open?'1':'0');th.textContent=(open?'\\u25BE ':'\\u25B8 ')+name;}
document.querySelectorAll('th.gh').forEach(function(th){fold(th,false);
th.addEventListener('click',function(){fold(th,th.getAttribute('data-open')!=='1');});});
"""


def _number(value) -> str:
    return ('%.6g' % value) if isinstance(value, float) else str(value)


def _html_cell(key: str, value) -> str:
    kind = KIND[key]
    if value is None or value == '':
        return '<td></td>'
    if kind == 'hash':
        return '<td title="%s"><code>sha256:%s</code></td>' % (html.escape(str(value)), html.escape(str(value)[:12]))
    if kind == 'num':
        full = repr(value) if isinstance(value, float) else str(value)
        return '<td class="n" title="%s">%s</td>' % (html.escape(full), html.escape(_number(value)))
    text = str(value)
    if kind == 'long':
        items = [item for item in text.split(' | ' if key != 'reasons' else ' ') if item]
        body = '<br>'.join(html.escape(item) for item in items)
        return '<td><details><summary>%d</summary><div>%s</div></details></td>' % (len(items), body)
    if key == 'asset_state':
        return '<td class="st-%s">%s</td>' % (STATE_CLASS.get(text, 'missing'), html.escape(text))
    if key in ('state_01', 'state_side', 'state_summary'):
        return '<td class="st-%s">%s</td>' % (STATE_CLASS.get(text, 'missing'), html.escape(text))
    if key == 'last_run' and text.startswith('실패'):
        return '<td class="fail">%s</td>' % html.escape(text)
    return '<td>%s</td>' % html.escape(text)


def _with_class(cell: str, name: str) -> str:
    """Add a class to the first tag of a cell (`<td>` or `<td class="x" ...>`)."""
    if cell.startswith('<td class="'):
        return '<td class="%s ' % name + cell[len('<td class="'):]
    return '<td class="%s"' % name + cell[len('<td'):]


def render_html(rows: Sequence[dict], sid: str, updated: str, violations: Sequence[dict] = ()) -> bytes:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[str(row['asset_state'])] = counts.get(str(row['asset_state']), 0) + 1
    summary = ', '.join('%s %d' % (state, number) for state, number in sorted(counts.items()))
    # runs of one group; every group after the front one gets a number i: cells `g<i>` (shown when open), stub `s<i>` (shown folded)
    runs: List[List[object]] = []
    for _key, _header, group, _kind in COLUMNS:
        if runs and runs[-1][0] == group:
            runs[-1][1] = int(runs[-1][1]) + 1
        else:
            runs.append([group, 1])
    number = {}
    for run in runs:
        if run[0] != FRONT_GROUP:
            number[str(run[0])] = len(number) + 1
    top = []
    for name, span in runs:
        if name == FRONT_GROUP:
            top.append('<th colspan="%d">%s</th>' % (span, html.escape(name)))
        else:
            i = number[str(name)]
            top.append('<th class="gh" data-g="%d" data-name="%s" data-span="%d" colspan="%d">▾ %s</th>' % (
                i, html.escape(str(name)), span, span, html.escape(str(name))))
    head = ['<tr class="g">' + ''.join(top) + '</tr>']
    cells_head = []
    stub_done = set()
    for index, column in enumerate(COLUMNS):
        i = number.get(column[2])
        if i is not None and i not in stub_done:
            stub_done.add(i)
            cells_head.append('<th class="stub s%d">…</th>' % i)
        cells_head.append('<th%s>%s</th>' % (' class="c1"' if index == 0 else (' class="g%d"' % i if i else ''),
                                             html.escape(column[1])))
    head.append('<tr>' + ''.join(cells_head) + '</tr>')
    body = []
    for row in rows:
        out_cells = []
        stub_done = set()
        for index, column in enumerate(COLUMNS):
            cell = _html_cell(column[0], row.get(column[0]))
            i = number.get(column[2])
            if index == 0:
                cell = _with_class(cell, 'c1') if cell != '<td></td>' else cell
            if i is not None:
                if i not in stub_done:
                    stub_done.add(i)
                    out_cells.append('<td class="stub s%d"></td>' % i)
                cell = _with_class(cell, 'g%d' % i)
            out_cells.append(cell)
        body.append('<tr>' + ''.join(out_cells) + '</tr>')
    violation = ''
    if violations:
        violation = '<p class="viol">그룹 호환 위반: %s (이 그룹의 제품은 판정 실패, 다른 그룹은 그대로)</p>\n' % html.escape(violation_summary(violations))
    page = ('<!DOCTYPE html>\n<html lang="ko"><head><meta charset="utf-8"><title>제품 자산 관리 시트</title>'
            '<style>%s</style></head><body>\n<h1>제품 자산 관리 시트</h1>\n'
            '<p class="meta">갱신 시각 %s · sheet_id <code>%s</code> · 제품 %d개 (%s)</p>\n%s'
            '<p class="warn">자동 생성 — 손으로 고친 내용은 다음 갱신 때 덮입니다. 기준 표시는 이 HTML 이고, CSV 는 같은 sheet_id 의 표입니다. '
            '두 파일의 sheet_id 가 다르면 갱신이 중간에 멈춘 것이니 <code>products-sheet</code> 를 다시 실행하세요. '
            '뒤쪽 묶음 머리글(▸)을 누르면 펼칩니다.</p>\n'
            '<input id="q" type="search" placeholder="제품·상태 찾기" aria-label="filter">\n'
            '<div class="wrap"><table><thead>%s</thead><tbody>\n%s\n</tbody></table></div>\n'
            '<script>%s</script></body></html>\n') % (
        CSS, html.escape(updated), sid, len(rows), html.escape(summary), violation, '\n'.join(head), '\n'.join(body), SCRIPT)
    return page.encode('utf-8')


# --------------------------------------------------------------------------------------------------------------- write

def _publish(path: Path, data: bytes) -> None:
    """Temporary file + os.replace for one file (a test replaces this to interrupt between the two files)."""
    pa._write_atomic(path, data)


def run_products_sheet(config_path, now: Optional[str] = None, out: Callable[[dict], None] = None,
                       err: Optional[Callable[[str], None]] = None, record_maps: bool = True) -> int:
    """0 = both files written, 2 = error (a file that was not replaced keeps its old content).
    record_maps False (`--no-record-maps`): a side without a maps_src recording is not 낡음 (the run did not record)."""
    out = out or (lambda result: print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)))
    err = err or (lambda text: print(text, file=sys.stderr))
    try:
        config = pc.load_products_config(config_path)
        pc._reject_symlink_components(config.base, 'data/products')          # the two files go into a real folder
        updated = now or pa._utc_now()
        rows, violations = build_sheet(config, record_maps)
        sid = sheet_id(rows)
        folder = config.base / 'data' / 'products'
        _publish(folder / CSV_NAME, render_csv(rows, sid))
        _publish(folder / HTML_NAME, render_html(rows, sid, updated, violations))
        out({'status': 'written', 'sheet_id': sid, 'csv': str(folder / CSV_NAME), 'html': str(folder / HTML_NAME),
             'products': [{'product': row['product'], 'state': row['asset_state']} for row in rows]})
        return 0
    except NumericContractError as exc:
        err('오류: %s' % exc)
        return 2
    except OSError as exc:
        err('오류: products_sheet_io: %s' % exc)
        return 2
