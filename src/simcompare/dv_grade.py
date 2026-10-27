"""dv-grade: 사용자가 채운 전압 시트와 제품 case 폴더 이름으로 short 쌍별 ΔV·최대 타이밍·등급을 계산한다.

PNG·CSV 내용은 열지 않는다(case 폴더 이름만). 모든 수는 Decimal 이고 float 는 쓰지 않는다.
코드에는 전압·임피던스·경계 기본값이 없다. 등급 경계는 사용자가 줄 때만 쓴다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from . import __version__
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'dv_grade_v1'
GRADE_RULE_VERSION = 'dv_grade_rule_v1'
IMPEDANCE_VALUES = ('low', 'high')
MAX_TIMINGS = 20
GRADES_NAME, BY_CASE_NAME, TIMINGS_NAME = 'dv_grades.csv', 'dv_grades_by_case.csv', 'dv_pair_timings.csv'
MANIFEST_NAME = 'dv_grade_manifest.json'
OUTPUT_FILES = (GRADES_NAME, BY_CASE_NAME, TIMINGS_NAME)
SHORT_DEFECTS = ('lateral_short', 'vertical_short')
OPEN_DEFECTS = ('lateral_open', 'vertical_open')
# 지수 표기·쉼표·단위는 받지 않는다. 이 범위에서 Decimal 기본 정밀도의 뺄셈은 정확하다.
NUMBER_RE = re.compile(r'[+-]?[0-9]{1,6}(\.[0-9]{1,6})?')
NUMBER_HINT = '부호(선택)와 정수부 최대 6자리, 소수부 최대 6자리의 일반 소수만 가능합니다. 지수 표기 없이 소수로 쓰세요(예 0.001)'

GRADES_COLUMNS = ['pair_key', 'electrode_a', 'electrode_b', 'impedance_a', 'impedance_b', 'impedance_class', 'dv_v', 'dv_timing',
                  'dv_timings_tied', 'dv_timing_tie_count', 'grade', 'threshold_v', 'case_count', 'cases', 'defects', 'layer_pairs']
BY_CASE_COLUMNS = ['case_name', 'defect', 'electrode_a', 'layer_a', 'electrode_b', 'layer_b', 'pair_key', 'dv_v', 'dv_timing', 'grade']
TIMINGS_COLUMNS = ['pair_key', 'timing', 'dv_ab', 'dv_ba', 'dv_t']


class DvGradeInputError(NumericContractError):
    """입력 오류 여러 건. errors 는 한 줄씩의 문자열이다."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('dv_input_invalid', f'{len(self.errors)}건\n' + '\n'.join(self.errors))


class DvGradeLoadError(ValueError):
    def __init__(self, code, detail=''):
        self.code = code
        super().__init__(f'{code}: {detail}' if detail else code)


def _line(code, description, row=None, electrode=None, column=None, value=None):
    parts = [code]
    for key, item in (('row', row), ('electrode', electrode), ('column', column), ('value', value)):
        if item is not None:
            parts.append(f'{key}={item}')
    return ' '.join(parts) + f' : {description}'


def _number(text):
    """일반 소수 문자열이면 Decimal, 아니면 None. 앞뒤 공백만 제거한다."""
    text = text.strip()
    return Decimal(text) if NUMBER_RE.fullmatch(text) else None


def _text(value):
    return format(value, 'f')


def _timing_label(index):
    return f'T{index}'


# ---- 시트 --------------------------------------------------------------------------------------

def parse_sheet(data):
    """시트 바이트를 읽어 {'timings': N, 'rows': {id: row}, 'errors': [...]} 로 돌려준다.

    구조 오류(인코딩·헤더)는 바로 NumericContractError. 행 단위 오류는 errors 에 모은다.
    row = {'row': 줄번호, 'impedance': 원문, 'values': [(min 원문, max 원문)], 'ranges': 모두 유효하면 [(Decimal, Decimal)], 'broken': bool}
    """
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise NumericContractError('dv_sheet_encoding_invalid',
                                   'UTF-8 이 아닙니다. 엑셀에서 "CSV UTF-8(쉼표로 분리)" 형식으로 저장하세요') from exc
    reader = csv.reader(io.StringIO(text, newline=''))
    try:
        header = next(reader)
    except StopIteration:
        raise NumericContractError('dv_sheet_header_invalid', '시트가 비어 있습니다')
    timings = (len(header) - 3) // 2
    expected = ['electrode_id', 'impedance', 'layers']
    for index in range(1, timings + 1):
        expected += [f'T{index}_min', f'T{index}_max']
    if not 1 <= timings <= MAX_TIMINGS or header != expected:
        raise NumericContractError(
            'dv_sheet_header_invalid',
            f'헤더는 electrode_id,impedance,layers,T1_min,T1_max,…,TN_min,TN_max (N 은 1–{MAX_TIMINGS} 연속, 추가 열 금지) 여야 합니다: {header!r}')
    rows, errors = {}, []
    while True:
        number = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        if all(not cell.strip() for cell in cells):
            continue
        electrode = cells[0].strip() if cells else ''
        if len(cells) != len(header):
            errors.append(_line('dv_sheet_row_width_invalid', f'칸이 {len(cells)}개입니다(헤더는 {len(header)}개)',
                                row=number, electrode=electrode or None))
            if electrode and electrode not in rows:
                rows[electrode] = {'row': number, 'broken': True}
            continue
        if not electrode:
            errors.append(_line('dv_sheet_electrode_id_empty', 'electrode_id 가 비어 있습니다', row=number))
            continue
        if electrode in rows:
            errors.append(_line('dv_sheet_duplicate_electrode', f'같은 electrode_id 가 앞에 이미 있습니다(row={rows[electrode]["row"]})',
                                row=number, electrode=electrode))
            continue
        impedance = cells[1].strip()
        if impedance and impedance not in IMPEDANCE_VALUES:
            errors.append(_line('dv_impedance_invalid', 'low 또는 high(소문자)만 가능합니다', row=number, electrode=electrode,
                                column='impedance', value=cells[1]))
        values, ranges, valid = [], [], True
        for index in range(timings):
            low_cell, high_cell = cells[3 + 2 * index], cells[4 + 2 * index]
            parsed = []
            for suffix, cell in (('min', low_cell), ('max', high_cell)):
                if cell.strip() and _number(cell) is None:
                    errors.append(_line('dv_voltage_invalid', NUMBER_HINT, row=number, electrode=electrode,
                                        column=f'T{index + 1}_{suffix}', value=cell))
                    valid = False
                parsed.append(_number(cell) if cell.strip() else None)
            if parsed[0] is not None and parsed[1] is not None and parsed[0] > parsed[1]:
                errors.append(_line('dv_voltage_range_inverted', 'min 이 max 보다 큽니다', row=number, electrode=electrode,
                                    column=f'T{index + 1}_min', value=f'{low_cell}>{high_cell}'))
                valid = False
            values.append((low_cell, high_cell))
            ranges.append((parsed[0], parsed[1]))
        rows[electrode] = {'row': number, 'broken': False, 'impedance': impedance, 'values': values, 'ranges': ranges,
                           'valid': valid and all(item is not None for pair in ranges for item in pair)}
    return {'timings': timings, 'rows': rows, 'errors': errors}


def _check_required(sheet, required):
    """쌍에 쓰이는 전극은 시트에 있고 임피던스·전압이 모두 채워져야 한다. 오류 문자열 목록."""
    errors = []
    for electrode in sorted(required):
        row = sheet['rows'].get(electrode)
        if row is None:
            errors.append(_line('dv_sheet_electrode_missing', '시트에 이 전극 행이 없습니다(short case 에 쓰입니다)', electrode=electrode))
            continue
        if row['broken']:
            continue
        if not row['impedance']:
            errors.append(_line('dv_impedance_invalid', '임피던스(low/high)를 채워야 합니다', row=row['row'], electrode=electrode,
                                column='impedance', value=''))
        for index, (low_cell, high_cell) in enumerate(row['values'], start=1):
            for suffix, cell in (('min', low_cell), ('max', high_cell)):
                if not cell.strip():
                    errors.append(_line('dv_voltage_invalid', '전압 칸이 비어 있습니다', row=row['row'], electrode=electrode,
                                        column=f'T{index}_{suffix}', value=''))
    return errors


# ---- 쌍 선정 -----------------------------------------------------------------------------------

def collect_short_pairs(root):
    """case 폴더 이름만 읽어 short 쌍을 모은다. 같은 이름 전극 case 가 먼저 빠지고, 그다음 open 이 빠진다."""
    from .adapters.real_hotspot import _case_name, _discover_cases
    cases, _ = _discover_cases(Path(root))
    pairs, ignored, excluded, short_count = {}, [], [], 0
    for case in cases:
        defect, electrode_a, layer_a, electrode_b, layer_b = _case_name(case.name)
        if electrode_a == electrode_b:
            ignored.append(case.name)
            continue
        if defect in OPEN_DEFECTS:
            excluded.append(case.name)
            continue
        key = (min(electrode_a, electrode_b), max(electrode_a, electrode_b))
        pairs.setdefault(key, []).append({'case_name': case.name, 'defect': defect, 'electrode_a': electrode_a, 'layer_a': layer_a,
                                          'electrode_b': electrode_b, 'layer_b': layer_b})
        short_count += 1
    if not pairs:
        raise NumericContractError('dv_no_short_pairs', 'same-name 과 open 을 뺀 short case 가 없습니다')
    return {'pairs': pairs, 'case_count_found': len(cases), 'short_case_count': short_count,
            'ignored_same_name_cases': ignored, 'excluded_open_cases': excluded}


# ---- 계산 --------------------------------------------------------------------------------------

def grade_pair(ranges_a, ranges_b, impedance_a, impedance_b, threshold):
    """타이밍별 (min, max) Decimal 쌍 두 목록으로 ΔV·최대 타이밍·등급을 계산한다.

    dv_t = max(vmax_a - vmin_b, vmax_b - vmin_a). 두 값의 합은 폭_a + 폭_b >= 0 이라 큰 쪽은 항상 >= 0 이다.
    그래서 0 아래로 내리는 클램프는 쓰지 않는다. dv_t = 0 은 두 전극이 그 타이밍에 같은 한 점 값일 때뿐이다.
    """
    per_timing = []
    for (min_a, max_a), (min_b, max_b) in zip(ranges_a, ranges_b):
        dv_ab, dv_ba = max_a - min_b, max_b - min_a
        per_timing.append((dv_ab, dv_ba, max(dv_ab, dv_ba)))
    dv = max(item[2] for item in per_timing)
    if dv == 0:
        dv = Decimal('0')
        tied, grade = [], 'None'
    else:
        tied = [index for index, item in enumerate(per_timing, start=1) if item[2] == dv]   # 번호 오름차순
        if impedance_a == 'high' or impedance_b == 'high' or threshold is None:
            grade = 'High'
        else:
            grade = 'High' if dv >= threshold else 'Low'
    impedance_class = 'high_any' if 'high' in (impedance_a, impedance_b) else 'low_low'
    return {'dv_v': dv, 'timings_tied': tied, 'grade': grade, 'impedance_class': impedance_class, 'per_timing': per_timing}


# ---- 실행 --------------------------------------------------------------------------------------

def _write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle, lineterminator='\n').writerows(rows)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_dv_grade(sheet_path, root, out, product_id, threshold_text=None):
    """dv_grades.csv, dv_grades_by_case.csv, dv_pair_timings.csv, dv_grade_manifest.json 을 새 폴더에 쓴다. 요약 dict 를 돌려준다."""
    from .adapters.real_hotspot import _require_existing
    if product_id is None or not str(product_id).strip():
        raise NumericContractError('dv_product_id_empty', '--product-id 는 비어 있을 수 없습니다(기본값 없음)')
    product_id = str(product_id).strip()
    threshold = None
    if threshold_text is not None:
        threshold = _number(threshold_text)
        if threshold is None or threshold <= 0:
            raise NumericContractError('dv_threshold_invalid',
                                       f'--low-low-threshold-v {threshold_text!r}: 0 보다 큰 수여야 합니다. ' + NUMBER_HINT)
    _require_existing(root, '--root', 'dv_grade_root_missing')
    root_text, root, out = str(root), Path(root).resolve(strict=True), Path(out).resolve()
    if not root.is_dir():
        raise NumericContractError('dv_grade_root_required', str(root))
    if out.exists():
        raise NumericContractError('dv_grade_output_exists', str(out))
    sheet_path = Path(sheet_path)
    if not sheet_path.is_file():
        raise NumericContractError('dv_sheet_missing', str(sheet_path))
    data = sheet_path.read_bytes()
    sheet = parse_sheet(data)
    found = collect_short_pairs(root)
    required = {electrode for key in found['pairs'] for electrode in key}
    errors = sheet['errors'] + _check_required(sheet, required)
    if errors:
        raise DvGradeInputError(errors)
    threshold_out = _text(threshold) if threshold is not None else None

    grade_rows, case_rows, timing_rows, counts = [], [], [], {'None': 0, 'Low': 0, 'High': 0}
    for key in sorted(found['pairs']):
        electrode_a, electrode_b = key
        pair_key = electrode_a + '-' + electrode_b
        row_a, row_b = sheet['rows'][electrode_a], sheet['rows'][electrode_b]
        result = grade_pair(row_a['ranges'], row_b['ranges'], row_a['impedance'], row_b['impedance'], threshold)
        counts[result['grade']] += 1
        tied = [_timing_label(index) for index in result['timings_tied']]
        cases = found['pairs'][key]
        layers = []
        for case in cases:
            by_name = {case['electrode_a']: case['layer_a'], case['electrode_b']: case['layer_b']}
            item = f'{by_name[electrode_a]}:{by_name[electrode_b]}'
            if item not in layers:
                layers.append(item)
        grade_rows.append([pair_key, electrode_a, electrode_b, row_a['impedance'], row_b['impedance'], result['impedance_class'],
                           _text(result['dv_v']), tied[0] if tied else '', ';'.join(tied), len(tied) if tied else '',
                           result['grade'], threshold_out or '', len(cases), ';'.join(case['case_name'] for case in cases),
                           ';'.join(sorted({case['defect'] for case in cases})), ';'.join(layers)])
        for case in cases:
            case_rows.append([case['case_name'], case['defect'], case['electrode_a'], case['layer_a'], case['electrode_b'],
                              case['layer_b'], pair_key, _text(result['dv_v']), tied[0] if tied else '', result['grade']])
        for index, (dv_ab, dv_ba, dv_t) in enumerate(result['per_timing'], start=1):
            timing_rows.append([pair_key, _timing_label(index), _text(dv_ab), _text(dv_ba), _text(dv_t)])
    case_rows.sort(key=lambda row: row[0])

    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{out.name}.staging-', dir=str(out.parent)))
    try:
        _write_csv(stage / GRADES_NAME, [GRADES_COLUMNS] + grade_rows)
        _write_csv(stage / BY_CASE_NAME, [BY_CASE_COLUMNS] + case_rows)
        _write_csv(stage / TIMINGS_NAME, [TIMINGS_COLUMNS] + timing_rows)
        manifest = {'schema_version': SCHEMA_VERSION, 'grade_rule_version': GRADE_RULE_VERSION, 'product_id': product_id,
                    'sheet': {'path': str(sheet_path), 'sha256': hashlib.sha256(data).hexdigest()}, 'root': root_text,
                    'timings': sheet['timings'], 'threshold_v': threshold_out, 'case_count_found': found['case_count_found'],
                    'short_case_count': found['short_case_count'], 'pair_count': len(grade_rows), 'grade_counts': counts,
                    'ignored_same_name_cases': found['ignored_same_name_cases'], 'excluded_open_cases': found['excluded_open_cases'],
                    'unused_sheet_electrodes': sorted(e for e, row in sheet['rows'].items() if e not in required and not row['broken']),
                    'outputs': {name: _sha256(stage / name) for name in OUTPUT_FILES}, 'tool_version': __version__}
        with open(stage / MANIFEST_NAME, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        # mkdtemp 는 0700 으로 만든다. 다른 출력 폴더처럼 umask 기준 권한으로 맞춘 뒤 옮긴다.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(str(stage), 0o777 & ~umask)
        os.rename(str(stage), str(out))
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    return {'out': str(out), 'manifest': manifest, 'top': _top_pairs(grade_rows)}


def _top_pairs(grade_rows, limit=5):
    """ΔV 가 큰 순서 앞 몇 쌍(요약 출력용). 값 비교는 Decimal 로 한다."""
    ranked = sorted(grade_rows, key=lambda row: (-Decimal(row[6]), row[0]))
    return [(row[0], row[6], row[7], row[10]) for row in ranked[:limit]]


def summary_lines(result):
    manifest = result['manifest']
    counts = manifest['grade_counts']
    threshold = manifest['threshold_v']
    lines = [f"제품 {manifest['product_id']}: short 쌍 {manifest['pair_count']}개 (short case {manifest['short_case_count']}개, 타이밍 {manifest['timings']}개)",
             f"등급: High {counts['High']}개, Low {counts['Low']}개, None {counts['None']}개",
             f"low–low 경계: {threshold + ' V' if threshold else '없음(ΔV>0 이면 High)'}",
             f"제외: open case {len(manifest['excluded_open_cases'])}개, 같은 이름 전극 case {len(manifest['ignored_same_name_cases'])}개",
             f"쓰지 않은 시트 전극 {len(manifest['unused_sheet_electrodes'])}개"]
    if result['top']:
        lines.append('ΔV 큰 쌍:')
        lines += [f'  {key}  ΔV={dv} V  {timing or "-"}  {grade}' for key, dv, timing, grade in result['top']]
    lines.append(f"출력: {result['out']}")
    return lines


# ---- 로더 --------------------------------------------------------------------------------------

def _read_csv_dicts(path, columns):
    try:
        with open(path, encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if list(reader.fieldnames or []) != columns:
                raise DvGradeLoadError('dv_grade_columns_invalid', f'{Path(path).name}: 열이 {columns} 와 다릅니다')
            return list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise DvGradeLoadError('dv_grade_file_invalid', f'{path}: {exc}') from exc


def load_dv_grades(out_dir):
    """dv-grade 출력 폴더를 검증해 읽는다(소비자가 쓰는 유일한 진입점).

    {'manifest': dict, 'pairs': [dict], 'by_case': [dict]} 를 돌려준다. CSV 값은 모두 문자열이다.
    같은 case 이름이 다른 제품에 있을 수 있다. 제품 일치 확인은 소비자의 몫이며 manifest 의 product_id·root·sheet.sha256 이 근거다.
    """
    out_dir = Path(out_dir)
    manifest_path = out_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise DvGradeLoadError('dv_grade_manifest_missing', str(manifest_path))
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise DvGradeLoadError('dv_grade_manifest_invalid', f'{manifest_path}: {exc}') from exc
    if not isinstance(manifest, dict):
        raise DvGradeLoadError('dv_grade_manifest_invalid', str(manifest_path))
    if manifest.get('schema_version') != SCHEMA_VERSION:
        raise DvGradeLoadError('dv_grade_schema_unsupported', repr(manifest.get('schema_version')))
    if manifest.get('grade_rule_version') != GRADE_RULE_VERSION:
        raise DvGradeLoadError('dv_grade_rule_unsupported', repr(manifest.get('grade_rule_version')))
    outputs = manifest.get('outputs')
    if not isinstance(outputs, dict) or set(outputs) != set(OUTPUT_FILES):
        raise DvGradeLoadError('dv_grade_manifest_invalid', 'outputs 가 예상 파일 목록과 다릅니다')
    for name in OUTPUT_FILES:
        path = out_dir / name
        if not path.is_file():
            raise DvGradeLoadError('dv_grade_file_missing', str(path))
        if _sha256(path) != outputs[name]:
            raise DvGradeLoadError('dv_grade_hash_mismatch', name)
    pairs = _read_csv_dicts(out_dir / GRADES_NAME, GRADES_COLUMNS)
    by_case = _read_csv_dicts(out_dir / BY_CASE_NAME, BY_CASE_COLUMNS)
    by_key = {}
    for row in pairs:
        if row['pair_key'] in by_key:
            raise DvGradeLoadError('dv_grade_duplicate_pair', row['pair_key'])
        by_key[row['pair_key']] = row
    seen = set()
    for row in by_case:
        if row['case_name'] in seen:
            raise DvGradeLoadError('dv_grade_duplicate_case', row['case_name'])
        seen.add(row['case_name'])
        pair = by_key.get(row['pair_key'])
        if pair is None:
            raise DvGradeLoadError('dv_grade_pair_missing', f"{row['case_name']} -> {row['pair_key']}")
        for field, pair_field in (('dv_v', 'dv_v'), ('grade', 'grade'), ('dv_timing', 'dv_timing')):
            if row[field] != pair[pair_field]:
                raise DvGradeLoadError('dv_grade_case_value_mismatch', f"{row['case_name']}: {field} {row[field]!r} != {pair[pair_field]!r}")
        if {row['electrode_a'], row['electrode_b']} != {pair['electrode_a'], pair['electrode_b']}:
            raise DvGradeLoadError('dv_grade_case_electrode_mismatch', row['case_name'])
    return {'manifest': manifest, 'pairs': pairs, 'by_case': by_case}
