"""Entry paths stay testable without optional parquet/zarr readers."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from simcompare.adapters import particle_sim_run as checker

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('entry', [['-m', 'simcompare.adapters.particle_sim_run'],
                                    [str(ROOT / 'scripts/check_particle_sim_run.py')]])
def test_help_and_usage(entry):
    assert subprocess.run([sys.executable, *entry, '--help'], capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, *entry], capture_output=True).returncode == 2


@pytest.mark.parametrize('status', [0, 1, 2])
def test_wrapper_forwarding(tmp_path, status):
    bin_dir = tmp_path / 'fake bin'
    bin_dir.mkdir()
    output = tmp_path / 'args.json'
    uv = bin_dir / 'uv'
    uv.write_text(f'#!{sys.executable}\nimport os,sys,json\njson.dump(dict(args=sys.argv[1:],cwd=os.getcwd()),open(os.environ["CAPTURE"],"w"))\nsys.exit({status})\n')
    uv.chmod(0o755)
    args = ['relative run with spaces', '--output', 'relative output.json', '--ca', 'mean', '--strict']
    env = dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'], CAPTURE=str(output))
    result = subprocess.run(['bash', str(ROOT / 'scripts/5_check_particle_sim_run.sh'), *args],
                            cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == status
    payload = json.loads(output.read_text())
    assert payload['cwd'] == str(tmp_path)
    assert payload['args'] == ['run', '--project', str(ROOT), '--with', 'pyarrow', '--with', 'zarr',
                               'python', str(ROOT / 'scripts/check_particle_sim_run.py'), *args]


def test_wrapper_no_arguments():
    assert subprocess.run(['bash', str(ROOT / 'scripts/5_check_particle_sim_run.sh')],
                          capture_output=True).returncode == 2


def test_unreadable_and_malformed_inputs(tmp_path):
    assert checker.main([str(tmp_path / 'missing')]) == 2
    (tmp_path / 'ca.parquet').touch()
    (tmp_path / 'run_manifest.json').write_text('{')
    assert checker.main([str(tmp_path)]) == 2
    mapping = tmp_path / 'mapping.yaml'
    for text in ['[', '42', '- {failure_type: lateral_short}']:
        mapping.write_text(text)
        assert checker.main([str(tmp_path), '--mapping', str(mapping)]) == 2
    config = tmp_path / 'config.yaml'
    config.write_text('[')
    assert checker.main([str(tmp_path), '--product-config', str(config)]) == 2


def test_optional_imports_are_lazy_and_absent_reader_is_classified(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name.split('.')[0] in {'pyarrow', 'fastparquet', 'zarr'}:
            raise ImportError('test missing optional reader')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', blocked)
    assert checker.open_store(tmp_path) is None
    (tmp_path / 'run_manifest.json').write_text('{}')
    (tmp_path / 'ca.parquet').touch()
    assert checker.main([str(tmp_path)]) == 2
