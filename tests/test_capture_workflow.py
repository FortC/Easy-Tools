"""Regression: exercise real drag painting in an isolated Qt process."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_drag_paint_cancellation_and_privacy_workflow(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "capture-workflow.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(root / "main.py"), "--selftest-capture",
                             "--skip-real-ocr", "--report", str(report)],
                            cwd=root, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=40)
    assert result.returncode == 0, (result.stderr, report.read_text(encoding="utf-8") if report.exists() else "")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] and data["qt_errors"] == []
    assert len(data["scenarios"]) == 12
