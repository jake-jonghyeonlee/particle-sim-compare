"""pyarrow 없는 compare 환경에서도 실제 exporter의 양성 보존 gate를 실행한다.

생산자 환경만 명시적으로 호출하며 실제 Fab/정확도 검증은 하지 않는다. 사례 생성은
기존 GENERATE, 원천·단위·보존 assertions는 아래 테스트를 수정한다.
"""
import json
import os
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from simcompare.adapters.example import load_dataset
from test_particle_sim_export_integration import GENERATE

ROOT = os.environ.get("PARTICLE_SIM_ROOT")
PYTHON = os.environ.get("PARTICLE_SIM_PYTHON")
pytestmark = pytest.mark.skipif(
    not ROOT or not PYTHON, reason="set PARTICLE_SIM_ROOT and PARTICLE_SIM_PYTHON for exporter integration")


def test_particle_sim_export_positive_conservation_without_pyarrow(tmp_path):
    """생산자 원본 nominal export의 full valid sum=CA를 확인한다. compare에는 parquet IO가 없다."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(Path(ROOT) / "src"))
    completed = subprocess.run([PYTHON, "-B", "-c", GENERATE, str(tmp_path)], cwd=ROOT,
                               env=env, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    count = 0
    for name in ("pilot", "open_straight", "auto_open"):
        out = tmp_path / name / "export"
        layers = pd.read_csv(out / "bundles.csv").assigned_process_layer.unique()
        distributions = [dict(fab_distribution_id="SYNTHETIC_ONLY_" + layer,
                              fab_id="FAB_SYN", process_layer=layer, bin_lower=[1.], bin_upper=[5.],
                              raw_values=[1.], value_kind="density", value_unit="1/cm2", size_unit="um")
                         for layer in layers]
        (out / "fab.json").write_text(json.dumps(dict(distributions=distributions)))
        loaded = load_dataset(out, mode="real")
        for bundle in loaded.bundles:
            assert len(bundle.ca_spatial_sources) == len(bundle.ca.values)
            for source in bundle.ca_spatial_sources:
                gate = source.validation
                assert gate.status == "available" and gate.reason is None
                assert gate.integrated_total_um2 == pytest.approx(source.total_ca)
                assert gate.residual_um2 == pytest.approx(0.)
                assert source.provenance["source_step"] and source.provenance["unit_id"]
                count += 1
    assert count > 0
