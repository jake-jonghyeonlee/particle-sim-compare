"""dv-template: 제품 case 폴더 이름만 읽어 전극×타이밍 전압 시트의 빈 양식을 만든다.

PNG·CSV 내용은 열지 않는다. 임피던스 값은 사용자가 준 공유 DB(CSV)에서만 오고, 전압 칸은 항상 비운다.
코드에는 임피던스·전압 기본값이 없다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import __version__
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'dv_template_v1'
IMPEDANCE_VALUES = ('low', 'high')
MAX_TIMINGS = 20
SHEET_NAME, NEW_NAME, MANIFEST_NAME = 'dv_sheet.csv', 'new_electrodes.csv', 'dv_template_manifest.json'


def _read_impedance_db(path):
    """electrode_id, impedance 열만 읽는다(다른 열 무시). 빈 impedance 는 'DB 에 없음'. 반환: ({id: 'low'|'high'}, sha256)."""
    path = Path(path)
    if not path.is_file():
        raise NumericContractError('impedance_db_missing', str(path))
    data = path.read_bytes()
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise NumericContractError('impedance_db_invalid', f'{path}: UTF-8 이 아닙니다') from exc
    reader = csv.DictReader(text.splitlines())
    names = [(name or '').strip() for name in reader.fieldnames or ()]
    if 'electrode_id' not in names or 'impedance' not in names:
        raise NumericContractError('impedance_db_invalid', f'{path}: electrode_id, impedance 열이 필요합니다')
    values, seen = {}, set()
    for number, row in enumerate(reader, start=2):
        row = {(key or '').strip(): (value or '') for key, value in row.items() if key is not None}
        electrode, value = row['electrode_id'].strip(), row['impedance'].strip()
        if not electrode:
            if value:
                raise NumericContractError('impedance_db_invalid', f'{path}:{number}: electrode_id 가 비어 있습니다')
            continue
        if electrode in seen:
            raise NumericContractError('impedance_db_duplicate_electrode', f'{path}:{number}: {electrode}')
        seen.add(electrode)
        if value and value not in IMPEDANCE_VALUES:
            raise NumericContractError('impedance_db_invalid_value',
                                       f'{path}:{number}: {electrode}={value!r} (low/high/빈칸만 허용, 소문자)')
        if value:
            values[electrode] = value
    return values, hashlib.sha256(data).hexdigest()


def _write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle, lineterminator='\n').writerows(rows)


def run_dv_template(root, out, timings, impedance_db=None):
    """case 폴더 이름에서 전극을 모아 dv_sheet.csv, new_electrodes.csv, dv_template_manifest.json 을 쓴다. 요약 dict 를 돌려준다."""
    from .adapters.real_hotspot import _case_name, _discover_cases, _require_existing
    if isinstance(timings, bool) or not isinstance(timings, int) or not 1 <= timings <= MAX_TIMINGS:
        raise NumericContractError('invalid_timings', f'--timings 는 1–{MAX_TIMINGS} 정수입니다: {timings!r}')
    _require_existing(root, '--root', 'dv_template_root_missing')
    root_text, root, out = str(root), Path(root).resolve(strict=True), Path(out).resolve()
    if not root.is_dir():
        raise NumericContractError('dv_template_root_required', str(root))
    if out.exists():
        raise NumericContractError('dv_template_output_exists', str(out))
    db, db_record = {}, None
    if impedance_db is not None:
        db, sha = _read_impedance_db(impedance_db)
        db_record = {'path': str(impedance_db), 'sha256': sha}
    cases, _ = _discover_cases(root)
    layers, counts, ignored = {}, {}, []
    for case in cases:
        _, electrode_a, layer_a, electrode_b, layer_b = _case_name(case.name)
        if electrode_a == electrode_b:
            ignored.append(case.name)
            continue
        for electrode, layer in ((electrode_a, layer_a), (electrode_b, layer_b)):
            layers.setdefault(electrode, set()).add(layer)
            counts[electrode] = counts.get(electrode, 0) + 1
    electrodes = sorted(layers)
    joined = {electrode: ';'.join(sorted(layers[electrode])) for electrode in electrodes}
    header = ['electrode_id', 'impedance', 'layers']
    for index in range(1, timings + 1):
        header += [f'T{index}_min', f'T{index}_max']
    sheet = [header] + [[e, db.get(e, ''), joined[e]] + [''] * (2 * timings) for e in electrodes]
    new = [e for e in electrodes if e not in db]
    manifest = {'schema_version': SCHEMA_VERSION, 'root': root_text, 'case_count': len(cases),
                'ignored_same_name_cases': ignored, 'timings': timings, 'impedance_db': db_record,
                'electrode_count': len(electrodes), 'filled_impedance_count': len(electrodes) - len(new),
                'new_electrode_count': len(new), 'db_only_electrodes': sorted(set(db) - set(electrodes)),
                'tool_version': __version__}
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{out.name}.staging-', dir=str(out.parent)))
    try:
        _write_csv(stage / SHEET_NAME, sheet)
        _write_csv(stage / NEW_NAME, [['electrode_id', 'layers', 'case_count']] + [[e, joined[e], counts[e]] for e in new])
        with open(stage / MANIFEST_NAME, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        os.rename(str(stage), str(out))
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    return {'out': str(out), 'manifest': manifest}


def summary_lines(result):
    manifest = result['manifest']
    return [f"전극 {manifest['electrode_count']}개 (case {manifest['case_count']}개, 타이밍 {manifest['timings']}개)",
            f"DB 에서 임피던스를 채운 전극 {manifest['filled_impedance_count']}개",
            f"새 전극(DB 에 값 없음) {manifest['new_electrode_count']}개: {NEW_NAME}",
            f"무시한 같은 이름 전극 case {len(manifest['ignored_same_name_cases'])}개",
            f"출력: {result['out']}"]
