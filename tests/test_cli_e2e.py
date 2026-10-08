"""공개 CLI는 numeric demo와 검사만 실행하며 학습 산출물을 만들지 않는다."""
import json
from simcompare.cli import main


def test_cli_demo_validation_and_comparison(tmp_path, capsys):
    data=tmp_path/'data'
    assert main(['demo-data','--out',str(data)]) == 0
    cfg=data/'config.yaml'
    assert main(['validate-data','--config',str(cfg)]) == 3
    assert main(['compare','--config',str(cfg)]) == 0
    out=data/'numeric_outputs'
    assert json.loads((out/'comparison.json').read_text())['status']=='available'
    assert not (out/'model.pt').exists()
    assert not (out/'embeddings.npz').exists()
    assert not (data/'bundles.csv').exists()
    assert 'DEMO 모드' in capsys.readouterr().err


def test_cli_reports_numeric_config_errors(tmp_path, capsys):
    config=tmp_path/'bad.yaml';config.write_text('mode: real\noutput_dir: x\n')
    assert main(['validate-data','--config',str(config)]) == 2
    assert '오류:' in capsys.readouterr().err
