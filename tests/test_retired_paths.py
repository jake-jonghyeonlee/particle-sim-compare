"""T7 공개 명령·runtime import·의존성 폐기 검산."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
from simcompare.cli import _parser, main

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {'model','train','losses','dataset','retrieval','baseline','split','variants',
           'cards','plots','caa','analysis','neighborhood','pair_geometry','checks','config'}
APPROVED = {'demo-data','validate-data','compare','electrode-map','export-review-rois','merge-exports','import-hotspots','compare-windows','report'}


def test_help_lists_exactly_approved_commands():
    parser = _parser()
    sub = next(a for a in parser._actions if hasattr(a,'choices') and isinstance(a.choices,dict))
    assert set(sub.choices) == APPROVED
    result=subprocess.run([sys.executable,'-m','simcompare','--help'],capture_output=True,text=True)
    assert result.returncode == 0
    for name in APPROVED:
        assert name in result.stdout
    for name in ('train','embed','analyze','evaluate','all'):
        assert name not in sub.choices


@pytest.mark.parametrize('command',['train','embed','analyze','evaluate','all'])
def test_retired_commands_have_explicit_nonzero_error(command,tmp_path):
    result=subprocess.run([sys.executable,'-m','simcompare',command,'--config','missing.yaml'],
                          cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode == 2
    assert command in result.stderr and '폐기' in result.stderr
    assert '오류:' in result.stderr and 'Traceback' not in result.stderr
    assert not list(tmp_path.iterdir())


def test_no_kept_source_imports_retired_modules():
    for path in (ROOT/'src/simcompare').rglob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                for alias in node.names:
                    assert alias.name not in {'simcompare.'+n for n in RETIRED},path
            elif isinstance(node,ast.ImportFrom):
                assert node.module not in {'simcompare.'+n for n in RETIRED},path
                if node.module == 'simcompare':
                    assert not RETIRED.intersection(a.name for a in node.names),path
    assert all(not (ROOT/f'src/simcompare/{n}.py').exists() for n in RETIRED)


def test_numeric_entrypoints_import_without_dl_or_legacy_loader():
    script='''
import sys,importlib.abc
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'torch','sklearn','scipy','matplotlib','pandas'} or fullname in {'simcompare.schema','simcompare.preprocess','simcompare.adapters.example'}:
            raise AssertionError('forbidden runtime dependency: '+fullname)
sys.meta_path.insert(0,Guard())
import simcompare.cli,simcompare.pipeline,simcompare.synthetic
from simcompare.cli import _parser
_parser()
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_declared_dependencies_exclude_retired_packages_and_keep_export_reader():
    deps=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['dependencies']
    assert not any(d.startswith(('torch','scikit-learn','scipy','matplotlib')) for d in deps)
    assert any(d.startswith('pandas') for d in deps)  # kept parquet/DataFrame source checker
    assert all(any(d.lower().startswith(n) for d in deps) for n in ('numpy','pillow','pyyaml'))


def test_legacy_config_is_not_silently_accepted_by_numeric_validate(tmp_path,capsys):
    cfg=tmp_path/'legacy.yaml';cfg.write_text('mode: demo\ndata: {}\n')
    assert main(['validate-data','--config',str(cfg)])==2
    assert 'numeric_config_v1' in capsys.readouterr().err
