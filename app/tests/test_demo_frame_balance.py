import os
import runpy
import pytest
from pathlib import Path

pytestmark = pytest.mark.timeout(5)


def test_demo_script_skips_startup_reconcile(monkeypatch):
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "demo_frame_balance.py"

    monkeypatch.delenv("INSTAPI_SKIP_RECONCILE", raising=False)
    monkeypatch.delenv("INSTAPI_DB_PATH", raising=False)
    monkeypatch.delenv("INSTAPI_PHOTOS_DIR", raising=False)

    runpy.run_path(str(script_path), run_name="demo_frame_balance_test")

    assert os.environ["INSTAPI_SKIP_RECONCILE"] == "1"
    assert os.environ["INSTAPI_DB_PATH"].endswith("app/.demo_frame_balance/instapi-demo.db")
