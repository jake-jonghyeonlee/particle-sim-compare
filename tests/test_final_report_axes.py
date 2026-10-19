"""report T4: ROI 두 축 보기(절대 CA / CA 변화), 매칭 근거, 두 깔때기. 새 입력이 없으면 기존 보고서와 바이트 동일."""
import hashlib
import json
import os
from pathlib import Path
import re

from test_final_report import demo, make_windows, report_args, run_cli  # noqa: F401  (demo는 pytest fixture)

GOLDEN = Path(__file__).parent / 'golden'


def normalise(text, *roots):
    """실행마다 달라지는 값(경로, 시각, 해시, commit, 이미지 바이트)만 고정 문자열로 바꾼다."""
    for root in roots:
        for form in {str(root), str(Path(root).resolve())}:
            text = text.replace(form, '<ROOT>').replace(form.replace('\\', '\\\\'), '<ROOT>')
    text = re.sub(r'data:image/png;base64,[A-Za-z0-9+/=]+', 'data:image/png;base64,<PNG>', text)
    text = re.sub(r'\b[0-9a-f]{64}\b', '<SHA>', text)
    text = re.sub(r'\d{4}-\d\d-\d\dT[\d:]+\+00:00', '<TS>', text)
    text = re.sub(r'("producer_commit[^"]*": )(?:"[^"]*"|null)', r'\1"<COMMIT>"', text)
    text = re.sub(r'(producer_commit[^<]*</td><td class="l">)[^<]*', r'\1<COMMIT>', text)
    text = re.sub(r'(producer_commit )[^\s<·]+', r'\1<COMMIT>', text)
    return re.sub(r'\b[0-9a-f]{40}\b', '<COMMIT>', text)


def test_without_new_options_report_is_byte_identical_to_golden(demo, tmp_path):
    out = tmp_path / 'out'
    assert run_cli(*report_args(demo, out, '--rank-window-um', 1)).returncode == 0
    root = demo['data'].parent
    got = {name: normalise((out / name).read_text(encoding='utf-8'), root) for name in ('report.html', 'report.json')}
    for name, text in got.items():
        golden = GOLDEN / ('baseline_' + name)
        if os.environ.get('WRITE_GOLDEN'):
            golden.write_text(text, encoding='utf-8', newline='\n')
        # 골든에 남은 옛 producer commit 도 같은 규칙으로 고정한다(커밋마다 골든을 다시 만들지 않는다)
        assert normalise(golden.read_text(encoding='utf-8')) == text, name


# ---- 합성 입력: 6쌍 + 값 없는 쌍 -------------------------------------------------------------------------------

SIZE = 20.0
# 쌍 → (a, b). Δ = b − a. b가 None이면 값 없음(partial).
AXES_VALUES = {'P1': (100.0, 100.0), 'P2': (10.0, 40.0), 'P3': (90.0, 20.0), 'P4': (50.0, 51.0), 'P5': (60.0, 80.0),
               'P6': (100.0, 60.0), 'P7': (10.0, None)}
ROW_HEADER = ['pair_id', 'window_size_um', 'diameter_um', 'kind', 'side_um', 'metric', 'a_value_um2', 'a_partial_value_um2', 'a_status',
              'a_fraction_of_window', 'b_value_um2', 'b_partial_value_um2', 'b_status', 'b_fraction_of_window', 'delta_um2',
              'delta_fraction', 'delta_status', 'delta_reasons']


def rewrite_rows(folder, values):
    import csv
    with open(folder / 'local_delta_rows.csv', 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(ROW_HEADER)
        for pid, (a, b) in values.items():
            delta = None if b is None else b - a
            writer.writerow([pid, SIZE, 5.0, 'window', SIZE, 'union', a, 'null', 'available', 0.1, '' if b is None else b, 'null', 'available', 0.1,
                             '' if delta is None else delta, '' if delta is None else delta / 400, 'available' if b is not None else 'partial',
                             '[]' if b is not None else '["window_clipped_b"]'])


def axes_windows(demo, tmp_path, values=AXES_VALUES, name='win'):
    pairs = {pid: (10.0 + 30 * i, 10.0, 10.0 + 30 * i, 10.0, 'ready') for i, pid in enumerate(values)}
    folder = make_windows(tmp_path / name, pairs, {pid: 0.0 for pid in values})
    rewrite_rows(folder, values)
    return {**demo, 'win': folder}


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fake_candidates(root, name='cand', *, status='available', reason='<b id="pwn">'):
    stages = [{'stage': 'regions', 'count': 7, 'dropped': 0, 'reasons': {}},
              {'stage': 'merged_queries', 'count': 5, 'dropped': 2, 'reasons': {'merged_overlap': 2}},
              {'stage': 'after_max_candidates', 'count': 4, 'dropped': 1, 'reasons': {'cut_by_max_candidates': 1}},
              {'stage': 'exported_ready', 'count': 3, 'dropped': 1, 'reasons': {reason: 1}}]
    funnel = {'schema_version': 'candidate_funnel_v1', 'directions': [{'direction': 'reference_to_target', 'stages': stages},
                                                                       {'direction': 'target_to_reference', 'stages': stages}],
              'cut_by_max_candidates': 2, 'max_candidates': 4, 'max_candidates_scope': 'per_direction',
              'index_coverage': {'reference': {'index_rois_ready': 6, 'index_rois_blocked': 1, 'electrodes_covered': 9, 'electrodes_total': 10,
                                               'complete': False, 'blocked_reasons': {'index_frame_has_no_valid_cell': 1}}}}
    queries = lambda prefix, count: [{'query_id': prefix, 'roi_ids': [prefix + '_roi'], 'max_count': count, 'merged_from': ['r1', 'r2', 'r3'],
                                      'status': 'ready'}]
    payload = {'schema_version': 'candidate_queries_v1', 'status': status, 'funnel': funnel,
               'inputs': {'reference': {'product_ids': ['reference']}, 'target': {'product_ids': ['target']}},
               'directions': [{'direction': 'reference_to_target', 'queries': queries('qr', 4)},
                              {'direction': 'target_to_reference', 'queries': queries('qt', 7)}]}
    write(root / name / 'candidate_queries_v1.json', payload)
    write(root / name / 'funnel.json', funnel)
    return root / name, funnel


def fake_confirm(root, windows, cand, scores, name='confirm', pair_ids=None, hostile=''):
    """pair 이름 → 사이드카 기록. 홀수 번째 쌍은 reference_to_target, 짝수 번째는 target_to_reference + 상호 병합."""
    pair_ids = pair_ids if pair_ids is not None else [p['pair_id'] for p in json.loads((windows / 'local_delta.json').read_text(encoding='utf-8'))['pairs']]
    folder = root / name
    corr = write(folder / 'roi_correspondence.json', {'schema_version': 'roi_correspondence_v1', 'pairs': pair_ids})
    delta = json.loads((windows / 'local_delta.json').read_text(encoding='utf-8'))
    delta['provenance']['correspondence_sha256'] = sha(corr)
    write(windows / 'local_delta.json', delta)
    records = {}
    for i, pid in enumerate(pair_ids):
        direction = 'reference_to_target' if i % 2 == 0 else 'target_to_reference'
        source = {'direction': direction, 'query_roi_id': ('qr' if i % 2 == 0 else 'qt') + '_roi', 'rank': 1, 'score': scores[i % len(scores)],
                  'flags': {'near_equal_peaks': True, 'low_structure': hostile or False}}
        records[pid] = {'pair_id': pid, 'status': 'ready', 'direction': direction, 'candidates': [source], 'merged_with': [],
                        'mutual_status': 'mutual' if i % 2 else 'one_way'}
    sidecar = {'schema_version': 'confirmed_pairs_sidecar_v1', 'pairs': records,
               'inputs': {'candidates_payload_sha256': sha(cand / 'candidate_queries_v1.json')}}
    write(folder / 'confirmed_pairs_sidecar.json', sidecar)
    confirm_stage = lambda name, count, reasons=None: {'stage': name, 'count': count, 'reasons': reasons or {}}
    stages = [confirm_stage('candidates_in_match_file', 9), confirm_stage('decided_accept', 4), confirm_stage('decided_reject', 1),
              confirm_stage('undecided', 4), confirm_stage('blocked', 1, {'pixel_pitch_mismatch': 1}), confirm_stage('emitted_ready_pairs', 3)]
    funnel = {'schema_version': 'confirm_funnel_v1', 'directions': [{'direction': 'reference_to_target', 'n_queries_in_match_file': 3, 'stages': stages}],
              'totals': {'counts': {}, 'stages': stages}}
    write(folder / 'confirm_funnel.json', funnel)
    return folder, funnel


def axes_args(dirs, out, cand=None, confirm=None, *extra, windows=True):
    args = report_args(dirs, out, '--rank-window-um', SIZE, '--top-n', 3, *extra, windows=windows)
    if cand is not None:
        args += ['--candidates-dir', cand]
    if confirm is not None:
        args += ['--confirm-dir', confirm]
    return args


def a2b(out):
    return json.loads((out / 'report.json').read_text(encoding='utf-8'))['sections']['A2b']


def test_two_axis_lists_and_badge_classes_are_hand_computed(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.91, 0.83])
    out = tmp_path / 'out'
    result = run_cli(*axes_args(dirs, out, cand, confirm))
    assert result.returncode == 3, result.stderr   # P7은 값이 없다
    section = a2b(out)
    # max(a,b): P1 100, P6 100 (같으면 쌍 이름 순), P3 90, P5 80, P4 51, P2 40. 값 없는 P7은 순위에 없다
    assert section['absolute_total'] == 6
    assert [e['pair_id'] for e in section['absolute']] == ['P1', 'P6', 'P3']
    assert [e['larger'] for e in section['absolute']] == ['같음', 'a', 'a']
    # Δ: P2 +30, P5 +20, P4 +1 (악화) / P3 −70, P6 −40 (개선). P1은 Δ = 0
    assert [e['pair_id'] for e in section['worse']] == ['P2', 'P5', 'P4']
    assert [e['pair_id'] for e in section['better']] == ['P3', 'P6']
    assert section['intersection'] == {'both': ['P3', 'P6'], 'absolute_only': ['P1'], 'change_only': ['P2', 'P4', 'P5']}
    assert section['badges'] == {'P1': '절대 CA 큼 (변화 작음)', 'P2': '변화 큼 (절대 CA 작음)', 'P3': '절대·변화 모두 큼',
                                 'P4': '변화 큼 (절대 CA 작음)', 'P5': '변화 큼 (절대 CA 작음)', 'P6': '절대·변화 모두 큼'}
    assert 'P7' not in section['badges']
    # 변화 목록은 A2와 같은 계산을 재사용한다
    full = json.loads((out / 'report.json').read_text(encoding='utf-8'))['sections']['A2']
    assert [e['pair_id'] for e in full['worse']] == [e['pair_id'] for e in section['worse']]
    html = (out / 'report.html').read_text(encoding='utf-8')
    for text in ('ROI 두 축 보기', 'CA 변화 큰 곳', '절대·변화 모두 큼', '절대 CA 큼 (변화 작음)', '변화 큼 (절대 CA 작음)',
                 '형상이 바뀌었다는 뜻은 아닙니다'):
        assert text in html, text
    assert '변경부' not in html and 'href="#a2b"' in html
    assert html.index('id="a2"') < html.index('id="a2b"') < html.index('id="a3"')


def test_without_confirm_dir_section_says_so_and_lists_no_pairs_from_candidates(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    out = tmp_path / 'out'
    assert run_cli(*axes_args(dirs, out, cand)).returncode == 3
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert '--confirm-dir 없음' in html and '확정되지 않은 후보는 쌍으로 나열하지 않습니다' in html
    assert a2b(out)['provenance'] is None and a2b(out)['confirm_funnel'] is None
    assert '확정 쌍의 출처' not in html
    only_confirm_missing = tmp_path / 'out_nothing'
    assert run_cli(*axes_args(dirs, only_confirm_missing)).returncode == 3
    assert 'id="a2b"' not in (only_confirm_missing / 'report.html').read_text(encoding='utf-8')


def provenance_part(html):
    start = html.index('<h3>확정 쌍의 출처</h3>')
    return html[start:html.index('<h3>깔때기 1', start)]


def test_provenance_columns_and_scores_only_in_the_provenance_column(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.123456, 0.654321])
    out = tmp_path / 'out'
    run_cli(*axes_args(dirs, out, cand, confirm))
    html = (out / 'report.html').read_text(encoding='utf-8')
    part = provenance_part(html)
    assert '매칭 근거(표시용, 수치에 쓰지 않음)' in part and '알 수 없음' in part   # target 쪽은 후보가 없는 쌍이 있다
    assert '0.123456' in part and '0.654321' in part
    assert '0.123456' not in html.replace(part, '') and '0.654321' not in html.replace(part, '')
    prov = a2b(out)['provenance']
    # P1(0번째)은 reference_to_target 출처: reference 쪽 취약 max_count 4, merged_from 3. target 쪽은 알 수 없음
    assert prov['P1']['origin'] == {'reference': {'max_count': 4, 'merged_from': 3}, 'target': None}
    assert prov['P2']['origin'] == {'reference': None, 'target': {'max_count': 7, 'merged_from': 3}}
    assert prov['P1']['mutual_status'] == 'one_way' and prov['P2']['mutual_status'] == 'mutual'
    # 표시되지 않은 쌍(P7)은 표에 없다
    assert set(prov) == {'P1', 'P2', 'P3', 'P4', 'P5', 'P6'}
    # 점수를 바꿔도 순위·표시·색은 그대로다
    other, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.654321, 0.123456, 0.5], name='confirm2')
    out2 = tmp_path / 'out2'
    run_cli(*axes_args(dirs, out2, cand, other))
    first, second = a2b(out), a2b(out2)
    for key in ('absolute', 'worse', 'better', 'badges', 'intersection', 'absolute_total'):
        assert first[key] == second[key], key
    assert first['provenance'] != second['provenance']
    # 출처 표 앞의 본문(순위·색·교집합 표시)은 점수 순열과 무관하게 같다
    # 생성 시각만 고정한다(초 경계를 넘는 두 실행이 달라지지 않게). 나머지는 그대로 비교한다
    cut = lambda text: re.sub(r'\d{4}-\d\d-\d\dT[\d:.]+\+00:00', '<TS>', text[:text.index('<h3>확정 쌍의 출처</h3>')])
    assert cut(html) == cut((out2 / 'report.html').read_text(encoding='utf-8'))


def table_after(html, heading):
    """heading 뒤 첫 표의 행별 셀 텍스트(태그 제거, HTML 엔티티 복원)."""
    from html import unescape
    start = html.index(heading)
    table = html[html.index('<table>', start):]
    table = table[:table.index('</table>')]
    return [[unescape(re.sub(r'<[^>]+>', '', cell)) for cell in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, flags=re.S)]
            for row in re.findall(r'<tr>(.*?)</tr>', table, flags=re.S)][1:]


def test_funnel_tables_equal_the_funnel_files(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, cand_funnel = fake_candidates(tmp_path, reason='cut_x')
    confirm, confirm_funnel = fake_confirm(tmp_path, dirs['win'], cand, [0.5])
    out = tmp_path / 'out'
    run_cli(*axes_args(dirs, out, cand, confirm))
    html = (out / 'report.html').read_text(encoding='utf-8')
    reasons = lambda r: '; '.join(f'{k}:{v}' for k, v in sorted(r.items()))
    expected = [[d['direction'], s['stage'], str(s['count']), str(s['dropped']), reasons(s['reasons'])]
                for d in cand_funnel['directions'] for s in d['stages']]
    assert table_after(html, '<h3>깔때기 1') == expected
    cov = cand_funnel['index_coverage']['reference']
    assert table_after(html, '<h3>index 전극 덮임') == [['reference', '6', '1', '9 / 10', '아니오', 'index_frame_has_no_valid_cell:1']]
    expected = [[d['direction'], s['stage'], str(s['count']), reasons(s['reasons'])] for d in confirm_funnel['directions'] for s in d['stages']]
    expected += [['합계', s['stage'], str(s['count']), reasons(s['reasons'])] for s in confirm_funnel['totals']['stages']]
    assert table_after(html, '<h3>깔때기 2') == expected
    windows = table_after(html, '<h3>깔때기 3')
    assert [row[1] for row in windows] == ['7', '7', '6', '1', '0'] and windows[3][2] == 'window_clipped_b:1'
    section = a2b(out)
    assert section['candidates_funnel'] == cand_funnel and section['confirm_funnel'] == confirm_funnel
    assert section['windows_funnel']['pairs_partial'] == 1 and section['windows_funnel']['pairs_blocked'] == 0
    assert cov['index_rois_ready'] == 6


def test_correspondence_sha_mismatch_is_exit2_and_no_report(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.5])
    (confirm / 'roi_correspondence.json').write_text('{"schema_version": "roi_correspondence_v1", "pairs": []}', encoding='utf-8')
    out = tmp_path / 'out'
    assert run_cli(*axes_args(dirs, out, cand, confirm)).returncode == 2
    assert not (out / 'report.html').exists() and not (out / 'report.json').exists()
    assert json.loads((out / 'error.json').read_text(encoding='utf-8'))['reasons'] == ['confirm_windows_correspondence_mismatch']


def test_other_chain_and_schema_errors_are_exit2(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.5])
    cases = {}
    # 사이드카가 기록한 candidates sha256이 다르다
    sidecar_path = confirm / 'confirmed_pairs_sidecar.json'
    sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
    original = json.dumps(sidecar)
    sidecar['inputs']['candidates_payload_sha256'] = '0' * 64
    sidecar_path.write_text(json.dumps(sidecar), encoding='utf-8')
    cases['confirm_candidates_mismatch'] = axes_args(dirs, tmp_path / 'o1', cand, confirm)
    # 쌍 목록이 다르다
    other, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.5], name='confirm_b')
    side = json.loads((other / 'confirmed_pairs_sidecar.json').read_text(encoding='utf-8'))
    side['pairs'].pop('P1')
    (other / 'confirmed_pairs_sidecar.json').write_text(json.dumps(side), encoding='utf-8')
    cases['confirm_pair_set_mismatch'] = axes_args(dirs, tmp_path / 'o2', cand, other)
    # --confirm-dir만 있고 --windows-dir가 없다
    cases['confirm_requires_windows_dir'] = axes_args(dirs, tmp_path / 'o3', cand, confirm, windows=False)
    # 예상 밖 schema_version
    bad, _ = fake_candidates(tmp_path, 'cand_bad')
    payload = json.loads((bad / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    payload['schema_version'] = 'candidate_queries_v0'
    (bad / 'candidate_queries_v1.json').write_text(json.dumps(payload), encoding='utf-8')
    cases['unexpected_schema_version'] = axes_args(dirs, tmp_path / 'o4', bad)
    # 제품이 다르다
    wrong, _ = fake_candidates(tmp_path, 'cand_prod')
    payload = json.loads((wrong / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    payload['inputs']['target']['product_ids'] = ['someone_else']
    (wrong / 'candidate_queries_v1.json').write_text(json.dumps(payload), encoding='utf-8')
    cases['product_id_mismatch'] = axes_args(dirs, tmp_path / 'o5', wrong)
    # funnel.json이 candidates 안의 funnel과 다르다
    skew, funnel = fake_candidates(tmp_path, 'cand_skew')
    funnel['cut_by_max_candidates'] = 99
    (skew / 'funnel.json').write_text(json.dumps(funnel), encoding='utf-8')
    cases['candidates_funnel_mismatch'] = axes_args(dirs, tmp_path / 'o6', skew)
    # 개수가 아닌 값
    garbage, funnel = fake_candidates(tmp_path, 'cand_count')
    funnel['directions'][0]['stages'][0]['count'] = '<b>x</b>'
    payload = json.loads((garbage / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    payload['funnel'] = funnel
    (garbage / 'candidate_queries_v1.json').write_text(json.dumps(payload), encoding='utf-8')
    (garbage / 'funnel.json').write_text(json.dumps(funnel), encoding='utf-8')
    cases['invalid_count_value'] = axes_args(dirs, tmp_path / 'o7', garbage)
    for code, args in cases.items():
        result = run_cli(*args)
        out = Path(args[args.index('--out') + 1])
        assert result.returncode == 2, (code, result.stderr)
        assert not (out / 'report.html').exists() and json.loads((out / 'error.json').read_text(encoding='utf-8'))['reasons'] == [code], code
    sidecar_path.write_text(original, encoding='utf-8')


def test_out_equal_to_an_input_dir_is_exit2_and_inputs_survive(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.5])
    before = {p: p.read_bytes() for folder in (cand, confirm) for p in folder.iterdir()}
    for target in (cand, confirm):
        result = run_cli(*axes_args(dirs, target, cand, confirm))
        assert result.returncode == 2 and '입력 폴더와 달라야' in result.stderr
    assert {p: p.read_bytes() for folder in (cand, confirm) for p in folder.iterdir()} == before
    assert not (cand / 'report.html').exists() and not (confirm / 'error.json').exists()


def test_partial_candidates_status_gives_exit3_in_that_section(demo, tmp_path):
    values = {k: v for k, v in AXES_VALUES.items() if k != 'P7'}
    dirs = axes_windows(demo, tmp_path, values)
    cand, _ = fake_candidates(tmp_path, status='partial')
    out = tmp_path / 'out'
    assert run_cli(*axes_args(dirs, out, cand)).returncode == 3
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert payload['section_status']['A2b'] == 'partial' and payload['section_status']['A2'] == 'available'
    assert any(reason.startswith('A2b:') for reason in payload['reasons'])
    complete, _ = fake_candidates(tmp_path, 'cand_ok')
    out2 = tmp_path / 'out2'
    assert run_cli(*axes_args(dirs, out2, complete)).returncode == 3   # 합성 창 출력에는 crop이 없어 B3가 partial
    done = json.loads((out2 / 'report.json').read_text(encoding='utf-8'))
    assert done['section_status']['A2b'] == 'available' and not any(r.startswith('A2b:') for r in done['reasons'])


def test_no_windows_dir_with_candidates_only_is_no_input_section(demo, tmp_path):
    cand, _ = fake_candidates(tmp_path)
    out = tmp_path / 'out'
    assert run_cli(*axes_args(demo, out, cand, windows=False)).returncode in (0, 3)
    payload = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert payload['section_status']['A2b'] == 'no_input'
    assert '--windows-dir를 주지 않아' in (out / 'report.html').read_text(encoding='utf-8')


def test_hostile_strings_are_escaped_and_html_stays_self_contained(demo, tmp_path):
    evil = '<b id="pwn">'
    values = {evil + 'P1': (100.0, 20.0), 'P2': (10.0, 40.0)}
    dirs = axes_windows(demo, tmp_path, values)
    cand, _ = fake_candidates(tmp_path, reason=evil)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [evil], hostile=evil)
    sidecar = json.loads((confirm / 'confirmed_pairs_sidecar.json').read_text(encoding='utf-8'))
    sidecar['pairs'][evil + 'P1']['mutual_status'] = evil
    sidecar['pairs'][evil + 'P1']['direction'] = evil
    (confirm / 'confirmed_pairs_sidecar.json').write_text(json.dumps(sidecar), encoding='utf-8')
    out = tmp_path / 'out'
    assert run_cli(*axes_args(dirs, out, cand, confirm)).returncode == 3   # 합성 창 출력에는 crop이 없어 B3가 partial
    html = (out / 'report.html').read_text(encoding='utf-8')
    assert evil not in html and '&lt;b id=&quot;pwn&quot;&gt;' in html
    assert '<script' not in html and not re.search(r'https?:|src="//|href="//', html)


# ---- 실제 candidates → confirm-pairs → compare-windows 출력으로 끝까지 -----------------------------------------

from test_confirm_pairs import both, cid, decisions, env, run as run_confirm  # noqa: E402,F401  (env는 pytest fixture)


def test_real_pipeline_outputs_end_to_end(env):
    tmp = env['tmp']
    assert run_cli('compare', '--config', env['config']).returncode == 0
    compare = env['config'].parent / 'numeric_outputs'
    matches = both(env)
    rows = [[cid(env, 'reference_to_target', 1, 0), 'accept', '같은 형상'], [cid(env, 'reference_to_target', 2, 1), 'accept', ''],
            [cid(env, 'target_to_reference', 1, 0), 'accept', '<i>x</i>'], [cid(env, 'reference_to_target', 1, 2), 'reject', '']]
    assert run_confirm(env, matches, decisions(env, rows)) in (0, 3)
    confirm = tmp / 'out'
    windows = tmp / 'windows'
    assert run_cli('compare-windows', '--config', env['config'], '--correspondence', confirm / 'roi_correspondence.json',
                   '--window-sizes', 1, '--sort-diameter-um', 5, '--out', windows).returncode in (0, 3)
    report = tmp / 'report'
    result = run_cli('report', '--compare-dir', compare, '--windows-dir', windows, '--candidates-dir', env['cand'],
                     '--confirm-dir', confirm, '--rank-window-um', 1, '--out', report)
    assert result.returncode in (0, 3), result.stderr
    html = (report / 'report.html').read_text(encoding='utf-8')
    section = json.loads((report / 'report.json').read_text(encoding='utf-8'))['sections']['A2b']
    cand_funnel = json.loads((env['cand'] / 'funnel.json').read_text(encoding='utf-8'))
    confirm_funnel = json.loads((confirm / 'confirm_funnel.json').read_text(encoding='utf-8'))
    assert section['candidates_funnel'] == cand_funnel and section['confirm_funnel'] == confirm_funnel
    reasons = lambda r: '; '.join(f'{k}:{v}' for k, v in sorted(r.items()))
    assert table_after(html, '<h3>깔때기 1') == [[d['direction'], s['stage'], str(s['count']), str(s['dropped']), reasons(s['reasons'])]
                                                 for d in cand_funnel['directions'] for s in d['stages']]
    totals = confirm_funnel['totals']['counts']
    assert table_after(html, '<h3>깔때기 2')[-1][2] == str(totals['emitted_ready'])
    sidecar = json.loads((confirm / 'confirmed_pairs_sidecar.json').read_text(encoding='utf-8'))
    assert set(section['provenance']) <= set(sidecar['pairs']) and section['provenance']
    queries = {roi: q for d in json.loads((env['cand'] / 'candidate_queries_v1.json').read_text(encoding='utf-8'))['directions']
               for q in d['queries'] for roi in q['roi_ids']}
    for pid, info in section['provenance'].items():
        source = sidecar['pairs'][pid]['candidates'][0]
        side = 'reference' if source['direction'] == 'reference_to_target' else 'target'
        assert info['origin'][side]['max_count'] == queries[source['query_roi_id']]['max_count']
    assert 'id="a2b"' in html and not re.search(r'https?:', html) and '<script' not in html



def test_mutual_context_differs_is_shown_in_table_and_json_only_when_true(demo, tmp_path):
    dirs = axes_windows(demo, tmp_path)
    cand, _ = fake_candidates(tmp_path)
    confirm, _ = fake_confirm(tmp_path, dirs['win'], cand, [0.5, 0.4])
    side_path = confirm / 'confirmed_pairs_sidecar.json'
    sidecar = json.loads(side_path.read_text(encoding='utf-8'))
    sidecar['pairs']['P2']['mutual_context_differs'] = True
    sidecar['pairs']['P3']['mutual_context_differs'] = False
    side_path.write_text(json.dumps(sidecar), encoding='utf-8')
    out = tmp_path / 'out'
    run_cli(*axes_args(dirs, out, cand, confirm))
    note = '상호 위치만 같고 측정 맥락(분석 layer/failure type)이 다름'
    prov = a2b(out)['provenance']
    assert prov['P2']['mutual_context_differs'] is True and prov['P2']['mutual_context_note'] == note
    assert all('mutual_context_differs' not in prov[p] for p in prov if p != 'P2')
    assert provenance_part((out / 'report.html').read_text(encoding='utf-8')).count(note) == 1
