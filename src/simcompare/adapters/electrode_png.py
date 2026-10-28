"""실제 전극 PNG → review_assets_v1 어댑터 (PLAN_v4 §6 T1a).

출처 종류 두 가지를 읽는다: DL1 PNG 폴더(<root>/<model>/<electrode>/<n>-<layer>.png,
<root>/<model>/cno/<n>_<layer>.png)와 hotspot case의 전극 영상(<case>/1.png, 2.png).
case 문맥(identity/endpoint/failure_type/frame/valid/input_hash)은 numeric dataset에서만 가져오고,
이 어댑터는 endpoint를 정확히 일치하는 PNG에 묶어 asset으로 내보낼 뿐이다.
hotspot/CA/density 값은 읽지도 내보내지도 않는다. 극성은 출처별 계약 표(SOURCE_KIND_CONTRACT)로만 정한다.
layout PNG는 전체 frame(0.1 µm/pixel)이고 dataset frame은 hotspot crop(원점 (50,50) µm, 축마다 1000 px 작음)이므로
중앙 500 px 테두리를 정확히 잘라낸 부분 배열만 내보낸다(보간·threshold 없음).
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zlib

import numpy as np
from PIL import Image

from ..numeric_images import resolve_path
from ..numeric_schema import NumericContractError
from ..png_strict import _PNGError, _validate_png_structure, decoder_session
from ..roi_export import _frame_payload
from .real_hotspot import CONTRACT_VERSION as REAL_CONTRACT_VERSION, _case_name, configured_dataset, load_real_manifest

ADAPTER_VERSION = 'electrode_png_v1'
RECORD_NAME = 'electrode_asset_import_record.json'
MANIFEST_NAME = 'review_assets.json'
BORDER_PX = 500
LAYOUT_PITCH_UM = 0.1
HOTSPOT_ORIGIN_UM = (50.0, 50.0)
PNG_MODES = {'1', 'L', 'RGB', 'LA', 'RGBA', 'P'}

# 출처 종류별 극성 계약. 묵시적 기본값은 없고 표에 없는 종류는 blocked(electrode_polarity_unspecified)다.
# 검정 픽셀 = 형상이며 DL2 read_asset이 받는 value_semantics는 binary / binary_zero_is_marked 뿐이다.
SOURCE_KIND_CONTRACT = {
    'dl1_png_folder': {'value_semantics': 'binary_zero_is_marked', 'polarity_basis': 'user_answer_2026_10_19'},
    'hotspot_case_electrode': {'value_semantics': 'binary_zero_is_marked', 'polarity_basis': 'user_answer_2026_10_19'},
}
SEMANTICS_TAG = {'binary': 'b', 'binary_zero_is_marked': 'z'}
ELECTRODE_NAME = re.compile(r'^(\d+)-(.+)\.png$', re.IGNORECASE)
# cno 는 실자료(ETX)에서 전극과 같이 `<n>-<layer>.png` 이고 예전 자료는 `<n>_<layer>.png` 다. DL1 png_source 와 같게 둘 다 받는다.
CNO_NAME = re.compile(r'^(\d+)[-_](.+)\.png$', re.IGNORECASE)


def _error(code, detail=''):
    return NumericContractError(code, detail)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def _mask_hash(marked: np.ndarray) -> str:
    """Shape + packed bits; independent of the PNG encoding."""
    head = f'{marked.shape[0]},{marked.shape[1]}:'.encode('ascii')
    return hashlib.sha256(head + np.packbits(marked).tobytes()).hexdigest()


def _header(path: Path, rel: str):
    """(width, height, file_bytes, mode) without decoding pixels."""
    try:
        size = path.stat().st_size
        with Image.open(path) as image:
            return image.width, image.height, size, image.mode
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _error('png_decode_failed', rel) from exc


def _decode(data: bytes, rel: str):
    """Strict decode of one buffer: returns (black-is-shape bool array, decoded mode).

    Modes 1/L/RGB/LA/RGBA/P only when the decoded RGB is exactly black or white and alpha is opaque.
    No threshold, gamma, rotation or resampling; mode L accepts only 0 and 255.
    """
    try:
        with decoder_session() as pixel_limit:
            _validate_png_structure(data, pixel_limit=pixel_limit)
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format != 'PNG' or image.mode not in PNG_MODES:
                        raise _PNGError('unsupported_png_mode', str(image.mode))
                    if image.getexif().get(274, 1) != 1:
                        raise _PNGError('png_nonidentity_orientation', 'nonidentity orientation metadata')
                    mode = image.mode
                    image.load()
                    if mode in ('1', 'L'):
                        pixels = np.asarray(image)
                        if mode == 'L' and not np.all((pixels == 0) | (pixels == 255)):
                            raise _PNGError('nonbinary_nominal_mask', 'pixels must be exactly black or white')
                        black = ~(pixels.astype(bool))
                    else:
                        rgba = np.asarray(image.convert('RGBA'))
                        if not np.all(rgba[..., 3] == 255):
                            raise _PNGError('png_nonopaque_pixels', 'nonopaque pixels')
                        rgb = rgba[..., :3]
                        black = np.all(rgb == 0, axis=2)
                        if not np.all(black | np.all(rgb == 255, axis=2)):
                            raise _PNGError('nonbinary_nominal_mask', 'pixels must be exactly black or white')
                    return black, mode
            except (_PNGError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise
            except Exception as exc:
                raise _PNGError('png_decode_failed', str(exc)) from exc
    except _PNGError as exc:
        raise _error(exc.code, rel + ': ' + str(exc)) from exc
    except Image.DecompressionBombError as exc:
        raise _error('png_pixel_limit_error', rel) from exc
    except Image.DecompressionBombWarning as exc:
        raise _error('png_pixel_limit_warning', rel) from exc
    except zlib.error as exc:
        raise _error('png_invalid_idat_stream', rel) from exc
    except (OSError, ValueError, TypeError, SyntaxError) as exc:
        raise _error('png_decode_failed', rel) from exc


def _load_stack_order(path):
    """dl1_png_stack_order_v1: strict two-key JSON. 순서는 여기서만 오며 추정하지 않는다."""
    if path is None:
        return None
    data = Path(path).read_bytes()

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _error('stack_order_invalid', f'duplicate key {key}')
            result[key] = value
        return result

    def constant(value):
        raise _error('stack_order_invalid', f'nonfinite number {value}')
    try:
        document = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, NumericContractError):
            raise
        raise _error('stack_order_invalid', 'not strict UTF-8 JSON') from exc
    order = document.get('order') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or set(document) != {'stack_order_schema_version', 'order'}
            or document['stack_order_schema_version'] != 'dl1_png_stack_order_v1'
            or not isinstance(order, list) or not order
            or any(not isinstance(n, str) or not n for n in order)):
        raise _error('stack_order_invalid', 'expected dl1_png_stack_order_v1 with a non-empty order array')
    seen = {}
    for name in order:
        key = name.casefold()
        if key in seen:
            raise _error('stack_order_casefold_collision', f'{seen[key]!r} vs {name!r}')
        seen[key] = name
    return {'order': order, 'sha256': _sha(data)}


def _scan_dl1_model(root: Path, model: str, prefix: str = ''):
    """Names only (no decoding). Malformed names and duplicate electrode x layer are errors."""
    model_dir = root / model
    electrodes, cno, warnings = {}, [], []
    for entry in sorted(os.scandir(model_dir), key=lambda e: e.name):
        if not entry.is_dir():
            warnings.append({'status': 'unrelated_file', 'path': f'{prefix}{model}/{entry.name}'})
            continue
        is_cno = entry.name == 'cno'
        pattern = CNO_NAME if is_cno else ELECTRODE_NAME
        for item in sorted(os.scandir(entry.path), key=lambda e: e.name):
            rel = f'{model}/{entry.name}/{item.name}'
            if not item.name.lower().endswith('.png'):
                warnings.append({'status': 'unrelated_file', 'path': prefix + rel})
                continue
            match = pattern.fullmatch(item.name)
            if not match or int(match.group(1)) <= 0:
                raise _error('invalid_electrode_png_name', prefix + rel)
            record = {'relative_path': prefix + rel, 'fs_rel': rel, 'original_filename': item.name,
                      'filename_number': int(match.group(1)), 'layer': match.group(2)}
            if is_cno:
                cno.append(record)
            else:
                slot = electrodes.setdefault(entry.name, {})
                if record['layer'] in slot:
                    raise _error('duplicate_electrode_layer', f"{slot[record['layer']]['relative_path']} and {prefix + rel}")
                slot[record['layer']] = record
    return {'electrodes': electrodes, 'cno': cno, 'warnings': warnings}


def list_dl1_electrodes(root, model):
    """DL1 root 의 모델 폴더(``<root>/<model>/<electrode>/``)에서 전극 폴더 이름을 정렬해 돌려준다(``cno`` 제외).

    전극 PNG 가 하나도 없는 폴더는 세지 않는다. 모델 폴더가 없으면 빈 목록이다. 이름 규칙 위반은 ``_scan_dl1_model`` 과 같은 오류다.
    """
    root = Path(root)
    if not (root / model).is_dir():
        return []
    return sorted(_scan_dl1_model(root, model)['electrodes'])


def scan_electrode_layers(root, model):
    """change-roi-v2/T3b: ``{layer: [fs_rel, ...]}`` — 모델 폴더의 전극 PNG 를 층별로 모은다(이름만, 읽지 않는다).

    모델 폴더가 없으면 빈 dict 다. 이름 규칙 위반·중복은 ``_scan_dl1_model`` 과 같은 오류다."""
    root = Path(root)
    if not (root / model).is_dir():
        return {}
    layers = {}
    scan = _scan_dl1_model(root, model)
    for name in sorted(scan['electrodes']):
        for layer, record in sorted(scan['electrodes'][name].items()):
            layers.setdefault(layer, []).append(record['fs_rel'])
    return layers


def _caused_by_memory_error(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, MemoryError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def load_electrode_layer(root, model, frame, fs_rels, errors=None):
    """change-roi-v2/T3b: 한 층의 전극 PNG 여러 장을 frame 크기 bool(검정 = 형상)로 합집합해 돌려준다. 읽지 못한 파일은 건너뛴다.

    ``fs_rels`` 는 ``scan_electrode_layers`` 의 값. 디코드·frame 크기 오류(``NumericContractError``)와 파일 읽기 오류(``OSError``)는
    이 함수 안에서 그 파일만 건너뛰며(보고서 그림 전용이라 실행을 멈추지 않는다), 읽은 파일이 하나도 없으면 ``None`` 이다.
    건너뛴 파일의 오류 코드(``NumericContractError.code`` 또는 ``os_error``)는 ``errors`` 목록에 더한다(주면). ``_decode`` 가
    ``MemoryError`` 를 ``png_decode_failed`` 로 바꾼 경우는 건너뛰지 않고 ``MemoryError`` 를 다시 올린다.
    sha256·경고를 기록하지 않는다: 이 값은 alignment.html 그림에만 쓰이고 계산·JSON 에 들어가지 않는다."""
    root = Path(root)
    _check_frame(frame)
    union = None
    for fs_rel in fs_rels:
        try:
            marked, _ = _decode(resolve_path(root, fs_rel).read_bytes(), fs_rel)
            cropped = _crop(marked, frame.shape_yx, fs_rel)
        except (NumericContractError, OSError) as exc:
            if _caused_by_memory_error(exc):
                raise MemoryError(str(exc)) from exc
            if errors is not None:
                errors.append(exc.code if isinstance(exc, NumericContractError) else 'os_error')
            continue
        union = cropped.copy() if union is None else (union | cropped)
        del marked, cropped
    return union


def _check_stack_order(model, scan, stack_order):
    """With an order file every present name must match (casefold); collisions reject. Without one they warn."""
    names = sorted({r['layer'] for slot in scan['electrodes'].values() for r in slot.values()} |
                   {r['layer'] for r in scan['cno']})
    folded = {}
    for name in names:
        folded.setdefault(name.casefold(), []).append(name)
    collisions = [v for v in folded.values() if len(v) > 1]
    if stack_order is None:
        return {'status': 'not_used', 'resolved': None,
                'warnings': [{'status': 'layer_name_case_variants', 'model': model, 'names': v} for v in collisions]}
    if collisions:
        raise _error('layer_name_casefold_collision', f'{model}: {collisions[0]}')
    by_fold = {n.casefold(): n for n in stack_order['order']}
    missing = [n for n in names if n.casefold() not in by_fold]
    if missing:
        raise _error('stack_order_entry_missing', f'{model}: {missing[0]!r} is not in the stack-order file')
    present = {n.casefold() for n in names}
    resolved = [{'order_name': n, 'source_name': folded[n.casefold()][0]}
                for n in stack_order['order'] if n.casefold() in present]
    ignored = [n for n in stack_order['order'] if n.casefold() not in present]
    return {'status': 'resolved', 'resolved': resolved, 'ignored_names': ignored, 'warnings': []}


class _Writer:
    """Content-addressed asset files: identical (semantics, mask) pairs are encoded and written once."""

    def __init__(self, folder: Path):
        self.folder, self.written, self.references = folder, {}, 0

    def put(self, marked: np.ndarray, semantics: str):
        key = (semantics, _mask_hash(marked))
        if key not in self.written:
            if semantics == 'binary_zero_is_marked':
                pixels = np.where(marked, np.uint8(0), np.uint8(255))
            else:
                pixels = np.where(marked, np.uint8(255), np.uint8(0))
            relative = f'assets/{SEMANTICS_TAG[semantics]}_{key[1]}.png'
            Image.fromarray(pixels).save(self.folder / relative, format='PNG', compress_level=9)
            data = (self.folder / relative).read_bytes()
            self.written[key] = {'path': relative, 'sha256': _sha(data), 'value_semantics': semantics}
        return key[1], {**self.written[key], 'observed': True}


def _crop(marked: np.ndarray, frame_shape, rel: str):
    if marked.shape != (frame_shape[0] + 2 * BORDER_PX, frame_shape[1] + 2 * BORDER_PX):
        raise _error('electrode_frame_mismatch',
                     f'{rel}: layout shape {marked.shape} != hotspot frame {tuple(frame_shape)} + {2 * BORDER_PX} px per axis')
    return marked[BORDER_PX:-BORDER_PX, BORDER_PX:-BORDER_PX]


def _check_frame(frame):
    if (frame.pixel_pitch_um != LAYOUT_PITCH_UM or tuple(float(v) for v in frame.origin_xy_um) != HOTSPOT_ORIGIN_UM):
        raise _error('layout_hotspot_frame_mismatch',
                     f'frame {frame.frame_id}: origin {tuple(frame.origin_xy_um)} pitch {frame.pixel_pitch_um} '
                     f'!= hotspot frame origin {HOTSPOT_ORIGIN_UM} pitch {LAYOUT_PITCH_UM}')


def load_cnt_layers(root, model, frame):
    """층별 CNT 형상(검정=형상)을 frame 크기로 crop 해 읽는다. layer 사이 합집합은 만들지 않는다.

    모델 폴더 = product_id. 폴더나 ``cno`` 가 없거나 비면 ``layers == {}`` 와 경고를 돌려준다(오류 아님).
    같은 layer 이름 파일이 둘 이상이면 경고하고 그 layer 안에서만 합집합한다.
    """
    root = Path(root)
    _check_frame(frame)
    result = {'layers': {}, 'files': [], 'warnings': []}
    if not (root / model).is_dir():
        result['warnings'].append({'status': 'product_folder_missing', 'model': model})
        return result
    scan = _scan_dl1_model(root, model)
    result['warnings'].extend(scan['warnings'])
    if not scan['cno']:
        result['warnings'].append({'status': 'cnt_not_provided', 'model': model})
        return result
    paths = {}
    for item in scan['cno']:
        rel = item['relative_path']
        data = resolve_path(root, item['fs_rel']).read_bytes()
        marked, _ = _decode(data, rel)
        cropped = _crop(marked, frame.shape_yx, rel)
        layer = item['layer']
        if layer in result['layers']:
            result['layers'][layer] |= cropped
        else:
            result['layers'][layer] = cropped.copy()
        paths.setdefault(layer, []).append(rel)
        result['files'].append({'layer': layer, 'relative_path': rel, 'sha256': _sha(data)})
    for layer in sorted(paths):
        if len(paths[layer]) > 1:
            result['warnings'].append({'status': 'duplicate_cnt_layer_name', 'model': model,
                                       'layer': layer, 'files': paths[layer]})
    return result


def _load_dataset(numeric_data_dir, manifest_path, condition_assignment):
    root = Path(numeric_data_dir)
    try:
        header = json.loads(resolve_path(root, manifest_path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise _error('numeric_manifest_unreadable', manifest_path) from exc
    if header.get('schema_version') == REAL_CONTRACT_VERSION:
        if condition_assignment is None:
            raise _error('condition_assignment_required', 'real_hotspot_v1 needs explicit --condition-assignment')
        assignment = json.loads(Path(condition_assignment).read_text(encoding='utf-8'))
        return configured_dataset(root, load_real_manifest(root, manifest_path), assignment)
    if condition_assignment is not None:
        raise _error('condition_assignment_not_applicable', 'only real_hotspot_v1 takes an assignment')
    from .numeric import load_dataset
    return load_dataset(root, manifest_path=manifest_path, mode='synthetic')


# 픽셀당 바이트 모델(측정으로 검증: tests/test_electrode_png.py의 RSS 테스트).
# Pillow는 RGB/RGBA/LA를 픽셀당 4바이트, 1/L/P를 1바이트로 디코드한다.
_PILLOW_BPP = {'1': 1, 'L': 1, 'P': 1, 'LA': 4, 'RGB': 4, 'RGBA': 4}
# 회색조: np.asarray 1 + 비교 임시 bool 3 + astype(bool) 1 + 반전 1.
_GRAY_EXTRA_BPP = 6
# 컬러: convert('RGBA') 4 + np.asarray 4 + (rgb==0) 3 + black 1 + (rgb==255) 3 + all/or 임시 2.
_COLOR_EXTRA_BPP = 17
# 쓰기: np.where uint8 임시 1 + PNG 인코더 버퍼 최대 1(+ 인코딩 결과 읽기는 파일 크기로 계상).
_WRITE_CROP_BPP = 2


def _dataset_bytes(dataset):
    """가드 이전에 이미 올라와 있는 숫자 데이터셋이 붙들고 있는 모든 ndarray 저장소의 바이트 합.

    frame.valid뿐 아니라 관측된 case.records[d].mask(diameter마다 전체 원본 마스크)를 포함한다. 객체 그래프를 걸으며
    같은 버퍼(view는 base 체인의 최상위 배열)는 한 번만 센다. 배열이 아닌 메타데이터(문자열·dict 등)는 계상하지 못한다.
    """
    seen, buffers, total = set(), set(), 0
    stack = [dataset]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, np.ndarray):
            root = obj
            while isinstance(root.base, np.ndarray):
                root = root.base
            if id(root) not in buffers:
                buffers.add(id(root))
                total += int(root.nbytes)
        elif isinstance(obj, dict):
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        elif is_dataclass(obj) and not isinstance(obj, type):
            stack.extend(getattr(obj, f.name, None) for f in fields(obj))
        elif hasattr(obj, '__dict__') and not isinstance(obj, (str, bytes, type)):
            stack.extend(vars(obj).values())
    return total


def _estimate_mb(headers, frame_px, dataset_bytes=0):
    """보수적 추정(측정 아님): 파일 하나씩 처리하므로 최대 한 파일 + 상시 배열 3개 + 이미 올라온 데이터셋.

    headers는 (width, height, file_bytes[, mode]) 목록. 모드를 모르면 RGBA로 본다.
    디코드 단계는 파일 바이트 + 픽셀 × (Pillow 디코드 버퍼 + 변환본 + NumPy 임시들),
    쓰기 단계는 파일 바이트 + 전체 프레임 bool 1 + 잘라낸 프레임 × (np.where 임시 + 인코더 버퍼)다.
    둘 중 큰 쪽에 상시 배열 3개(frame_px 바이트씩)와 데이터셋을 더한다.
    """
    worst = 0
    for header in headers:
        w, h, size = header[:3]
        mode = header[3] if len(header) > 3 else 'RGBA'
        gray = mode in ('1', 'L')
        decode = w * h * (_PILLOW_BPP.get(mode, 4) + (_GRAY_EXTRA_BPP if gray else _COLOR_EXTRA_BPP))
        write = w * h + frame_px * _WRITE_CROP_BPP
        worst = max(worst, size + max(decode, write))
    return (worst + 3 * frame_px + dataset_bytes) / (1024 * 1024)


def import_electrode_assets(source_kind, root, out, *, numeric_data_dir, source_version, max_asset_memory_mb,
                            manifest_path='manifest.json', condition_assignment=None, stack_order=None,
                            cnt_from_dl1_root=None):
    """전극 PNG를 review_assets_v1 manifest + asset 파일로 변환한다. 입력은 수정하지 않는다."""
    if source_kind not in SOURCE_KIND_CONTRACT:
        return {'status': 'blocked', 'reasons': ['electrode_polarity_unspecified'], 'outputs': [], 'exit_code': 3,
                'detail': f'source kind {source_kind!r} is not in the polarity contract table'}
    contract = SOURCE_KIND_CONTRACT[source_kind]
    semantics = contract['value_semantics']
    root, out = Path(root).resolve(strict=True), Path(out).resolve()
    if not root.is_dir():
        raise _error('electrode_root_required', str(root))
    if out.exists():
        raise _error('electrode_output_exists', str(out))
    if not isinstance(source_version, str) or not source_version.strip():
        raise _error('source_version_required')
    if isinstance(max_asset_memory_mb, bool) or not isinstance(max_asset_memory_mb, (int, float)) or max_asset_memory_mb <= 0:
        raise _error('invalid_resource_limit', '--max-asset-memory-mb must be positive')
    if cnt_from_dl1_root is not None and source_kind != 'hotspot_case_electrode':
        raise _error('cnt_from_not_applicable', 'only hotspot_case_electrode takes --cnt-from-dl1-root')
    cnt_root = Path(cnt_from_dl1_root).resolve(strict=True) if cnt_from_dl1_root is not None else None
    if stack_order is not None and source_kind == 'hotspot_case_electrode' and cnt_root is None:
        raise _error('stack_order_not_applicable', 'no DL1 folder is read')
    order = _load_stack_order(stack_order)
    dataset = _load_dataset(numeric_data_dir, manifest_path, condition_assignment)
    cases = sorted(dataset.cases, key=lambda c: tuple(asdict(c.identity).values()))
    if not cases:
        raise _error('numeric_dataset_has_no_cases')
    for frame_id in sorted({c.identity.frame_id for c in cases}):
        _check_frame(dataset.frames[frame_id])

    warnings, blocked, used_files = [], [], {}
    plan = _plan(source_kind, root, cnt_root, cases, order, warnings, blocked)
    headers = {item['relative_path']: _header(resolve_path(item['root'], item['fs_rel']), item['relative_path'])
               for item in plan['files']}
    for binding in plan['bindings'].values():
        if source_kind == 'hotspot_case_electrode' and not binding['reasons']:
            first, second = binding['items']
            if headers[first['relative_path']][:2] != headers[second['relative_path']][:2]:
                raise _error('electrode_shape_mismatch',
                             f"{first['relative_path']} {headers[first['relative_path']][:2]} vs "
                             f"{second['relative_path']} {headers[second['relative_path']][:2]} (width, height)")
    frame_px = max(f.shape_yx[0] * f.shape_yx[1] for f in dataset.frames.values())
    estimate = _estimate_mb(list(headers.values()), frame_px, _dataset_bytes(dataset))
    if estimate > max_asset_memory_mb:
        raise _error('asset_memory_limit_exceeded',
                     f'estimated {estimate:.1f} MB > --max-asset-memory-mb {max_asset_memory_mb} '
                     f'({len(headers)} files; nothing truncated, nothing written)')

    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=out.name + '.tmp-', dir=out.parent))
    try:
        (staging / 'assets').mkdir()
        writer = _Writer(staging)
        decoded = {}   # (file sha256, frame shape) -> decode summary + asset ref; arrays are not retained
        meta_cache = {}

        def read(item, frame, *, keep=False, need_asset=False):
            """Decode one file once per distinct bytes. Returns (crop or None, summary, meta)."""
            rel = item['relative_path']
            data = resolve_path(item['root'], item['fs_rel']).read_bytes()
            sha = _sha(data)
            used_files[rel] = sha
            key = (sha, frame.shape_yx)
            cropped = None
            if key not in decoded or keep or (need_asset and 'asset' not in decoded[key]):
                marked, mode = _decode(data, rel)
                cropped = _crop(marked, frame.shape_yx, rel)
                if key not in decoded:
                    decoded[key] = {'mode': mode, 'full_shape_yx': list(marked.shape), 'full_mask_sha256': _mask_hash(marked)}
            summary = decoded[key]
            meta = {'relative_path': rel, 'original_filename': item['original_filename'],
                    'filename_number': item['filename_number'], 'original_sha256': sha,
                    'decoded_mode': summary['mode'], 'full_shape_yx': summary['full_shape_yx'],
                    'crop_offset_px': [BORDER_PX, BORDER_PX], 'full_mask_sha256': summary['full_mask_sha256']}
            return cropped, summary, meta

        def layout(item, frame):
            cropped, summary, meta = read(item, frame, need_asset=True)
            if 'asset' not in summary:
                summary['asset'] = writer.put(cropped, semantics)
            digest, ref = summary['asset']
            writer.references += 1
            return ref, {**meta, 'asset_path': ref['path'], 'asset_mask_sha256': digest}

        cnt_cache = {}

        def cnt_for(model, frame):
            if model in cnt_cache:
                return cnt_cache[model]
            union, components = np.zeros(frame.shape_yx, dtype=bool), {}
            for item in plan['cno'][model]:
                cropped, _, meta = read(item, frame, keep=True)
                union |= cropped
                comp = components.setdefault((item['layer'], meta['full_mask_sha256']),
                                             {'layer': item['layer'], 'full_mask_sha256': meta['full_mask_sha256'],
                                              'files': []})
                comp['files'].append({**meta, 'layer': item['layer']})
            for (layer, _), comp in components.items():
                if len(comp['files']) > 1:
                    warnings.append({'status': 'duplicate_cnt_mask_same_name', 'model': model, 'layer': layer,
                                     'files': [f['relative_path'] for f in comp['files']]})
            digest, ref = writer.put(union, semantics)
            result = (ref, {'union_mask_sha256': digest, 'union_path': ref['path'],
                            'components': [components[k] for k in sorted(components)]})
            cnt_cache[model] = result
            return result

        valid_cache = {}
        entries = []
        for case in cases:
            ident = case.identity
            frame = dataset.frames[ident.frame_id]
            binding = plan['bindings'][tuple(asdict(ident).values())]
            if binding['reasons']:
                blocked.append({'case_id': ident.case_id, 'product_id': ident.product_id,
                                'reasons': sorted(set(binding['reasons']))})
                continue
            asset_layouts, layout_prov = [], []
            for item, endpoint in zip(binding['items'], case.endpoints):
                ref, meta = layout(item, frame)
                asset_layouts.append({'endpoint': asdict(endpoint), 'asset': ref})
                layout_prov.append({**meta, 'electrode_slot': item.get('slot'), 'source_kind': source_kind})
            cnt_ref, cnt_prov = cnt_for(binding['cnt_model'], frame)
            if frame.frame_id not in valid_cache:
                valid_cache[frame.frame_id] = writer.put(frame.valid.astype(bool), 'binary')[1]
            entries.append({
                'identity': asdict(ident), 'endpoints': [asdict(e) for e in case.endpoints],
                'failure_type': case.failure_type, 'source_version': source_version,
                'frame': _frame_payload(frame),
                'assets': {'layouts': asset_layouts, 'valid': valid_cache[frame.frame_id], 'cnt': cnt_ref, 'via': []},
                'electrode_asset_provenance': {
                    'source_kind': source_kind, 'polarity_basis': contract['polarity_basis'],
                    'value_semantics': semantics, 'black_pixel_is_shape': True,
                    'layouts': layout_prov, 'cnt': {**cnt_prov, 'source_kind': 'dl1_png_folder'},
                    'valid': {'from': 'numeric_dataset_frame_valid', 'path': valid_cache[frame.frame_id]['path']},
                    'frame_correspondence': _correspondence(frame, layout_prov)}})
        for item in plan['unbound']:
            warnings.append({'status': 'png_not_bound_to_dataset_case', 'path': item})
        if not entries:
            raise _error('no_case_could_be_converted', f'{len(blocked)} blocked case(s): ' +
                         '; '.join(f"{b['case_id']}={','.join(b['reasons'])}" for b in blocked[:5]))
        manifest = {'schema_version': 'review_assets_v1', 'source_manifest_hash': dataset.provenance['input_hash'],
                    'cases': entries,
                    'electrode_asset_import': {'adapter_version': ADAPTER_VERSION, 'source_kind': source_kind,
                                               'polarity_basis': contract['polarity_basis'],
                                               'value_semantics': semantics, 'record': RECORD_NAME}}
        manifest_bytes = _json_bytes(manifest)
        (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
        input_hash = _sha(_json_bytes({'adapter_version': ADAPTER_VERSION, 'source_kind': source_kind,
                                       'dataset_input_hash': dataset.provenance['input_hash'],
                                       'source_version': source_version, 'files': used_files,
                                       'stack_order_sha256': None if order is None else order['sha256'],
                                       'cnt_from_dl1_root': cnt_root is not None}))
        record = {
            'schema_version': 'electrode_asset_import_record_v1', 'adapter_version': ADAPTER_VERSION,
            'source_kind': source_kind, 'polarity_basis': contract['polarity_basis'], 'value_semantics': semantics,
            'polarity_contract_table': SOURCE_KIND_CONTRACT, 'source_version': source_version,
            'input_hash': input_hash, 'dataset_input_hash': dataset.provenance['input_hash'],
            'manifest_sha256': _sha(manifest_bytes), 'source_root_name': root.name,
            'stack_order': None if order is None else {'sha256': order['sha256'], 'models': plan['order_models']},
            'frame_policy': {'layout_origin_xy_um': [0.0, 0.0], 'hotspot_frame_origin_xy_um': list(HOTSPOT_ORIGIN_UM),
                             'pixel_pitch_um': LAYOUT_PITCH_UM, 'crop_offset_px': [BORDER_PX, BORDER_PX],
                             'border_context_um': BORDER_PX * LAYOUT_PITCH_UM,
                             'limitation': 'assets are the exact central crop; the 50 um border context is not exported'},
            'memory_estimate_mb': round(estimate, 3), 'max_asset_memory_mb': max_asset_memory_mb,
            'files': {k: used_files[k] for k in sorted(used_files)},
            'asset_files': {v['path']: v['sha256'] for v in sorted(writer.written.values(), key=lambda v: v['path'])},
            'blocked_cases': sorted(blocked, key=lambda b: (b['case_id'], b['product_id'])),
            'warnings': sorted(warnings, key=lambda w: json.dumps(w, sort_keys=True, ensure_ascii=False)),
            'counts': {'dataset_cases': len(cases), 'cases_converted': len(entries), 'cases_blocked': len(blocked),
                       'png_files_read': len(used_files), 'png_files_decoded': len(decoded),
                       'distinct_asset_masks_written': len(writer.written), 'layout_asset_references': writer.references}}
        (staging / RECORD_NAME).write_bytes(_json_bytes(record))
        os.replace(staging, out)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {'status': 'partial' if blocked else 'available', 'outputs': [str(out / MANIFEST_NAME), str(out / RECORD_NAME)],
            'cases_converted': len(entries), 'cases_blocked': len(blocked), 'asset_files': len(writer.written),
            'input_hash': input_hash, 'memory_estimate_mb': round(estimate, 3),
            'exit_code': 3 if blocked else 0}


def _correspondence(frame, layout_prov):
    shape = layout_prov[0]['full_shape_yx']
    return {'layout_origin_xy_um': [0.0, 0.0], 'layout_shape_yx': shape, 'layout_pixel_pitch_um': LAYOUT_PITCH_UM,
            'hotspot_frame_origin_xy_um': list(frame.origin_xy_um), 'hotspot_frame_shape_yx': list(frame.shape_yx),
            'crop_offset_px': [BORDER_PX, BORDER_PX],
            'check': 'layout shape == frame shape + 1000 px per axis; origin (50,50) um = 500 px * 0.1 um'}


def _plan(source_kind, root, cnt_root, cases, order, warnings, blocked):
    """Bind each dataset endpoint to exactly one PNG by exact names. No decoding here."""
    plan = {'files': [], 'bindings': {}, 'cno': {}, 'unbound': [], 'order_models': {}}
    seen = set()

    def add(item, kind):
        key = (str(item['root']), item['relative_path'])
        if key not in seen:
            seen.add(key)
            plan['files'].append({**item, 'key': key, 'kind': kind})

    def scan_models(dl1_root, products, prefix=''):
        scans = {}
        for product in products:
            if not (dl1_root / product).is_dir():
                scans[product] = None
                continue
            scans[product] = _scan_dl1_model(dl1_root, product, prefix)
            checked = _check_stack_order(product, scans[product], order)
            warnings.extend(scans[product]['warnings'] + checked['warnings'])
            if checked['status'] == 'resolved':
                plan['order_models'][product] = {'resolved_order': checked['resolved'], 'ignored_names': checked['ignored_names']}
        return scans

    products = sorted({c.identity.product_id for c in cases})
    if source_kind == 'dl1_png_folder':
        scans = scan_models(root, products)
        for model_dir in sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in products):
            warnings.append({'status': 'model_not_in_dataset', 'model': model_dir})
        used = set()
        for case in cases:
            ident = case.identity
            scan = scans[ident.product_id]
            binding = {'items': [], 'reasons': [], 'cnt_model': ident.product_id}
            plan['bindings'][tuple(asdict(ident).values())] = binding
            if scan is None:
                binding['reasons'].append('product_folder_missing')
                continue
            if not scan['cno']:
                binding['reasons'].append('cnt_not_provided')
            for endpoint in case.endpoints:
                found = scan['electrodes'].get(endpoint.electrode_id, {}).get(endpoint.actual_layer)
                if found is None:
                    # 대소문자만 다른 layer는 같은 것으로 보지 않는다(모호함 → blocked).
                    variants = [r for r in scan['electrodes'].get(endpoint.electrode_id, {}).values()
                                if r['layer'].casefold() == endpoint.actual_layer.casefold()]
                    binding['reasons'].append('electrode_layer_case_mismatch' if variants else 'electrode_png_missing')
                else:
                    binding['items'].append({**found, 'root': root})
                    used.add(found['relative_path'])
            if binding['reasons']:
                continue
            for item in binding['items']:
                add(item, 'electrode')
        for product, scan in scans.items():
            if scan is None:
                continue
            for slot in scan['electrodes'].values():
                for rec in slot.values():
                    if rec['relative_path'] not in used:
                        plan['unbound'].append(rec['relative_path'])
        cno_products = {c.identity.product_id for c in cases if not plan['bindings'][tuple(asdict(c.identity).values())]['reasons']}
        for product in sorted(cno_products):
            plan['cno'][product] = [{**r, 'root': root} for r in scans[product]['cno']]
            for item in plan['cno'][product]:
                add(item, 'cno')
        plan['unbound'].sort()
        return plan
    # hotspot_case_electrode
    folders = {}
    for directory, _, files in os.walk(root):
        if '1.png' in files or '2.png' in files:
            folder = Path(directory)
            if folder.name.lower() == 'images' and folder != root:
                folder = folder.parent  # <case>/images/ 배치: 이름은 case 폴더 것이고 PNG는 images/ 안에 있다
            folders.setdefault(folder.name, []).append(Path(directory))
    for name, paths in folders.items():
        if len(paths) > 1:
            raise _error('duplicate_real_case_name', name)
    cnt_scans = scan_models(cnt_root, products, 'cnt_from_dl1_root:') if cnt_root is not None else {}
    for case in cases:
        ident = case.identity
        binding = {'items': [], 'reasons': [], 'cnt_model': ident.product_id}
        plan['bindings'][tuple(asdict(ident).values())] = binding
        try:
            defect, ea, la, eb, lb = _case_name(ident.case_id)
            if (defect != case.failure_type
                    or [(e.electrode_id, e.actual_layer) for e in case.endpoints] != [(ea, la), (eb, lb)]):
                binding['reasons'].append('case_name_endpoint_mismatch')
        except NumericContractError:
            binding['reasons'].append('case_name_unparseable')
        cnt_scan = cnt_scans.get(ident.product_id)
        if cnt_root is None:
            binding['reasons'].append('cnt_not_provided')
        elif cnt_scan is None:
            binding['reasons'].append('cnt_product_folder_missing')
        elif not cnt_scan['cno']:
            binding['reasons'].append('cnt_not_provided')
        paths = folders.get(ident.case_id)
        if not paths:
            binding['reasons'].append('electrode_case_folder_missing')
        else:
            folder = paths[0]
            for slot in (1, 2):
                if not (folder / f'{slot}.png').exists():
                    binding['reasons'].append('electrode_png_missing')
                    break
        if binding['reasons']:
            continue
        for slot in (1, 2):
            rel = (folder / f'{slot}.png').relative_to(root).as_posix()
            item = {'relative_path': rel, 'fs_rel': rel, 'original_filename': f'{slot}.png', 'filename_number': None,
                    'slot': slot, 'root': root}
            binding['items'].append(item)
            add(item, 'electrode')
    for product in sorted({p for p, s in cnt_scans.items() if s and s['cno']}):
        if any(not b['reasons'] and b['cnt_model'] == product for b in plan['bindings'].values()):
            plan['cno'][product] = [{**r, 'root': cnt_root} for r in cnt_scans[product]['cno']]
            for item in plan['cno'][product]:
                add(item, 'cno')
    return plan
