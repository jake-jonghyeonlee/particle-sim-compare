"""docs/product_assets.md names only things the code has, and its reason-code tables cover the code's lists (pasx/T9).

The checks read source text and a few module constants. No mask, no product data and no file outside the repository is read.
"""
import re
from pathlib import Path

import pytest

from simcompare import pair_assets, products_assets as pa, products_config as pc

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/product_assets.md'
USAGE = ROOT / 'docs/usage.md'
SRC = ROOT / 'src/simcompare'
RUNNER = ROOT / 'scripts/build_etx_stage_runners.py'

SNAKE = re.compile(r'^[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?::<[a-z_]+>)?$')
UPPER = re.compile(r'^[A-Z][A-Z0-9_]+$')
OPTION = re.compile(r'^--[a-z][a-z0-9-]*$')
COMMAND = re.compile(r'^(?:products|pair)-[a-z]+(?: .*)?$')
FENCE = re.compile(r'```.*?```', re.S)
INLINE = re.compile(r'`([^`\n]+)`')
# reason words that only the log writes (asset_log.jsonl, pair_log.jsonl); they are not stale reasons and the doc need not list them
LOG_ONLY_REASONS = ('forced', 'rebuild', 'force', 'maps_src_replay_failed_retry')


def _in_source(name, source):
    """True when `name` appears as a whole word (a longer name that contains it does not count)."""
    return re.search(r'(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])' % re.escape(name), source) is not None


def _doc_text():
    return DOC.read_text(encoding='utf-8')


def _prose_tokens(text):
    return INLINE.findall(FENCE.sub('', text))


def _source_text():
    parts = [path.read_text(encoding='utf-8') for path in sorted(SRC.rglob('*.py'))]
    parts.append(RUNNER.read_text(encoding='utf-8'))
    parts.append((ROOT / 'configs/etx/templates/products.yaml').read_text(encoding='utf-8'))
    return '\n'.join(parts)


def _classified(tokens):
    snake, upper, options, commands = set(), set(), set(), set()
    for token in tokens:
        if COMMAND.match(token):
            commands.add(token.split(' ')[0])
        elif OPTION.match(token):
            options.add(token)
        elif UPPER.match(token):
            upper.add(token)
        elif SNAKE.match(token):
            snake.add(token)
    return snake, upper, options, commands


def _fenced_tokens(text):
    """Names inside code blocks: option flags, EDIT names at the start of a line and the commands after `-m simcompare`."""
    options, upper, commands = set(), set(), set()
    for block in re.findall(r'```(?:python|text)\n(.*?)```', text, re.S):
        options.update(re.findall(r'(?<![\w-])(--[a-z][a-z0-9-]*)', block))
        upper.update(re.findall(r'^([A-Z][A-Z0-9_]+) = ', block, re.M))
        commands.update(re.findall(r'-m simcompare ([a-z0-9-]+)', block))
    return options, upper, commands


def _collected_names():
    text = _doc_text()
    snake, upper, options, commands = _classified(_prose_tokens(text))
    f_options, f_upper, f_commands = _fenced_tokens(text)
    return snake, upper | f_upper, options | f_options, commands | f_commands


def test_doc_exists_and_has_the_eleven_sections():
    text = _doc_text()
    for number in range(1, 12):
        assert re.search(r'^## %d\. ' % number, text, re.M), 'missing section %d' % number
    assert 'product_assets.md' in USAGE.read_text(encoding='utf-8')


def test_every_code_name_in_the_doc_is_in_the_source():
    source = _source_text()
    snake, upper, options, commands = _collected_names()
    missing = sorted(name for name in snake if not _in_source(name.split(':')[0], source))
    assert not missing, 'in the doc but not in the code: %s' % missing
    runner = RUNNER.read_text(encoding='utf-8')
    edit = runner.split("FLOW_EDIT = '''", 1)[1].split("'''", 1)[0]
    missing_edit = sorted(name for name in upper if not _in_source(name, edit) and not _in_source(name, source))
    assert not missing_edit, 'EDIT names not in the code: %s' % missing_edit
    cli = (SRC / 'cli.py').read_text(encoding='utf-8')
    missing_options = sorted(name for name in options if not _in_source(name, cli))
    assert not missing_options, 'options not in cli.py: %s' % missing_options
    missing_commands = sorted(name for name in commands if '"%s"' % name not in cli)
    assert not missing_commands, 'commands not in cli.py: %s' % missing_commands
    assert len(snake) + len(upper) + len(options) + len(commands) >= 100


def test_the_doc_collects_a_known_number_of_names():
    """A guard against a doc that lost its names (the exact count is reported by the T9 hand-off)."""
    snake, upper, options, commands = _collected_names()
    assert len(snake) >= 90 and len(upper) >= 12 and len(options) >= 10 and len(commands) >= 4


def test_edit_names_of_the_product_block_are_all_documented():
    runner = RUNNER.read_text(encoding='utf-8')
    block = runner.split('# ---- 제품 모드', 1)[1].split("'''", 1)[0]
    names = re.findall(r'^([A-Z][A-Z0-9_]+) = ', block, re.M)
    assert len(names) == 8
    doc_names = _collected_names()[1]
    assert not [name for name in names if name not in doc_names]
    for name in ('MODE', 'RUN_TAG', 'DRY_RUN', 'LEDGER_MAPS_CARDS'):
        assert name in doc_names


def _file_literals(path, pattern):
    return set(re.findall(pattern, (SRC / path).read_text(encoding='utf-8')))


def code_reason_codes():
    """The reason codes the verdicts of 01, side, summary and compose can name (read from the source)."""
    assets = (SRC / 'products_assets.py').read_text(encoding='utf-8')
    pairs = (SRC / 'pair_assets.py').read_text(encoding='utf-8')
    found = set()
    for text in (assets, pairs):
        found.update(re.findall(r"reasons(?:\.append\(|\s*=\s*\[)\s*'([a-z0-9_]+)'", text))
    found.update(re.findall(r"Verdict\((?:MISSING|STALE), \['([a-z0-9_]+)'\]", assets))
    found.update(re.findall(r"if moved else '([a-z0-9_]+)'", assets))
    found.update(re.findall(r"return \['([a-z0-9_]+)'\]", pairs))
    found.update(re.findall(r"\.get\(\w+, '([a-z0-9_]+)'\)", assets + pairs))
    found.update(pa._BINDING_REASON.values())
    found.update(pa._SUMMARY_REASON.values())
    found.update(pair_assets._COMMON_REASON.values())
    found.update(pair_assets._DEPS_REASON.values())
    found.update(re.findall(r"'([a-z0-9_]+_(?:changed))'", assets + pairs))
    found.add('maps_src_missing')
    found.difference_update(LOG_ONLY_REASONS)
    return found


def code_corrupt_reasons():
    assets = (SRC / 'products_assets.py').read_text(encoding='utf-8')
    found = set(re.findall(r"_corrupt\('([a-z0-9_]+)'", assets))
    found.update(re.findall(r"'asset_corrupt:([a-z0-9_]+)'", assets))
    return found


def test_reason_code_tables_cover_the_code_lists():
    snake = _collected_names()[0]
    names = {token.split(':')[-1] for token in snake} | snake
    reasons = code_reason_codes()
    assert len(reasons) >= 30, sorted(reasons)
    missing = sorted(code for code in reasons if code not in names)
    assert not missing, 'reason codes not in the doc: %s' % missing
    corrupt = code_corrupt_reasons()
    assert len(corrupt) >= 20, sorted(corrupt)
    missing_corrupt = sorted(code for code in corrupt if code not in names)
    assert not missing_corrupt, 'asset_corrupt reasons not in the doc: %s' % missing_corrupt
    for warning in (pa.SOURCE_STAT_WARNING, 'mask_stat_changed'):
        assert warning in names


def test_group_error_codes_and_states_are_all_in_the_doc():
    text = _doc_text()
    snake = _collected_names()[0]
    assert not [code for code in pc.GROUP_ERROR_CODES if code not in snake]
    for state in (pa.OK, pa.STALE, pa.MISSING, pa.CORRUPT, '등록 없음', '판정 실패'):
        assert '`%s`' % state in text, state
    for reason in ('cap_exceeded', 'disk_full', 'write_error', 'finalize_failed'):
        assert reason in snake
