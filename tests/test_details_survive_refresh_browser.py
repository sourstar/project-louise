"""A drawer the reader opened stays open while its panel refreshes — in a
real browser.

The verbose text waits behind one closed line (operator, 2026-11-02: "A
crowded interface is a bad interface"). On the System tab the GPU panel is
swapped in by htmx every 5 s, so a drawer there would shut under the reader
at the next swap; each carries `data-keep-open` and an id, and system.html
opens again what was open (2026-11-11, replacing `hx-preserve`). Whether
that holds is a question for a browser:
tests/browser/details_survive_refresh.js opens one and watches two
refreshes. Skipped, saying why, where no browser and no driver for it are
installed.

THE DRAWER IS ONE THE PANEL HAS (2026-11-24). Until then this test waited
for `#gpu-why-unset`, the drawer of a panel with no probe, which the
11-11 panel no longer draws ("STATE, NOT EXPLANATION"): red wherever a
browser is installed, skipped everywhere else, so nobody saw it. It now
serves a card and a model on the CPU and opens the CPU card's "models".
"""
import os
import subprocess
from pathlib import Path

from app import config, gpu, gpu_models
from tests.test_gpu_panel import ARC, _probe
from tests.test_mobile_width_browser import CHROMIUM, NODE, pytestmark, served  # noqa: F401

DRAWER = "cpu-models"

SCRIPT = Path(__file__).parent / "browser" / "details_survive_refresh.js"


def test_an_open_drawer_survives_the_gpu_panels_refresh(served, monkeypatch):
    # a card the probe reads and a model LocalAI runs on no card: the CPU card's drawer
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(ARC,)))
    monkeypatch.setattr(config, "CHAT_URL", "http://nas:30286/v1")
    monkeypatch.setattr(gpu_models, "_get_json", lambda url: {"loaded_models": [
        {"id": "stt-cpu", "backend": "intel-sycl-f16-parakeet-cpp", "size_vram": 0, "process": {"pid": 66}}]})
    env = dict(os.environ, ADMIN_TOKEN="sekrit", CHROMIUM=CHROMIUM or "")
    if NODE:
        env["NODE_PATH"] = os.pathsep.join(
            [env.get("NODE_PATH", ""), str(Path(NODE).resolve().parents[1] / "lib" / "node_modules")])
    run = subprocess.run([NODE, str(SCRIPT), served, DRAWER], capture_output=True, text=True, timeout=120, env=env)
    assert run.returncode == 0 and "PASS" in run.stdout, run.stdout + run.stderr
