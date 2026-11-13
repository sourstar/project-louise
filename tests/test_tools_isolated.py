"""The measurement tools run on the node as `python3 -I tools/<tool>.py`:
isolated mode reads neither the script's directory nor PYTHONPATH, so each
tool puts the repository root on its own path before its lazy `from app
import …` (München, 2026-11-13: `encoder_bakeoff fetch` failed with "No
module named 'app'" under -I)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ["tools/encoder_bakeoff.py", "tools/chunker_mode.py", "tools/chunker_languages.py",
         "tools/legal_pilot.py"]


@pytest.mark.parametrize("tool", TOOLS)
def test_a_tool_finds_the_app_under_isolated_mode_from_anywhere(tool, tmp_path):
    probe = (f"import runpy; runpy.run_path({str(ROOT / tool)!r}, run_name='probe'); "
             f"import app.collections; print('ok')")
    got = subprocess.run([sys.executable, "-I", "-c", probe], cwd=tmp_path, capture_output=True,
                         text=True, timeout=120)
    assert got.returncode == 0 and got.stdout.strip().endswith("ok"), got.stderr[-2000:]
