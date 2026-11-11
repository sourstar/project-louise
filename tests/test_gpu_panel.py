"""The System page's GPU panel (app/gpu.py, /admin/system/gpu): the web
asks the probe over HTTP and never reads /proc itself."""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, gpu, spaces, web

ARC = gpu.GpuDevice(
    card="card0", pdev="0000:03:00.0", driver="xe", vendor_id="8086", device_id="e223",
    vendor="Intel Corporation", name="Battlemage G31 [Arc Pro B70]",
    vram_total=34242297856, vram_used=25730179072,
    processes=(gpu.GpuProcess(9317, "Qwen3-Embedding-4B-Q4_K_M", "llama-server",
                              "87f3fbd3e86a", 9586248 * 1024, 4103064 * 1024),
               gpu.GpuProcess(371292, "MedCPT-Article-Encoder-f16", "llama-server",
                              "6bedda131c5e", 416 * 1024, 423928 * 1024)))


@pytest.fixture
def client(isolated_config, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("h", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    from app.api import app
    with TestClient(app) as c:
        c.headers["X-Admin-Token"] = "sekrit"
        yield c


def _probe(monkeypatch, snapshot: gpu.GpuSnapshot) -> list[str]:
    asked: list[str] = []
    monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")
    monkeypatch.setattr(gpu, "_get_json", lambda url: asked.append(url) or gpu.to_json(snapshot))
    return asked


def test_the_panel_shows_the_card_its_memory_and_each_model(client, monkeypatch):
    asked = _probe(monkeypatch, gpu.GpuSnapshot(devices=(ARC,)))
    html = client.get("/admin/system/gpu").text
    assert asked == ["http://gpu-probe:8080/gpu"]
    assert "Battlemage G31 [Arc Pro B70]" in html and "Intel Corporation" in html
    # the node's figures, 2026-09-18: the driver's own used, and free from it
    assert "VRAM 31.89 GiB · used 23.96 GiB" in html and "free 7.93 GiB" in html
    assert "sum of the models" not in html
    rows = re.findall(r"<tr><td title=\"llama-server\">([^<]+)</td>", html)
    assert rows == ["Qwen3-Embedding-4B-Q4_K_M", "MedCPT-Article-Encoder-f16"]
    assert "9.14 GiB" in html and "28.7%" in html          # Qwen's VRAM, its share
    assert "416 KiB" in html and "414 MiB" in html          # an encoder held in host RAM
    # what the driver counts and no process shown holds
    unattributed = 25730179072 - (9586248 + 416) * 1024
    assert "<td>held by no model shown</td>" in html and gpu.size(unattributed) in html


def test_without_the_drivers_count_used_is_the_processes_sum(client, monkeypatch):
    from dataclasses import replace
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(replace(ARC, vram_used=None),),
                                        notes=("card0: the driver reports used memory "
                                               "only to CAP_PERFMON",)))
    html = client.get("/admin/system/gpu").text
    assert "sum of the models" in html and "CAP_PERFMON" in html
    assert "<td>held by no model shown</td>" not in html
    assert f"used {gpu.size((9586248 + 416) * 1024)}" in html


def test_a_card_whose_memory_is_not_read_says_so(client, monkeypatch):
    from dataclasses import replace
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(replace(
        ARC, driver="i915", vram_total=None, vram_used=None, processes=()),)))
    assert "VRAM not read for the i915 driver" in client.get("/admin/system/gpu").text


def test_unset_says_there_is_no_probe(client, monkeypatch):
    monkeypatch.setattr(config, "GPU_PROBE_URL", "")
    monkeypatch.setattr(gpu, "_get_json", lambda url: pytest.fail("nothing to ask"))
    assert "GPU_PROBE_URL is unset" in client.get("/admin/system/gpu").text


def test_an_unreachable_probe_is_the_panels_state_not_the_pages(client, monkeypatch):
    monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")

    def refused(url):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(gpu, "_get_json", refused)
    r = client.get("/admin/system/gpu")
    assert r.status_code == 200
    assert "unreachable" in r.text and "ConnectError: connection refused" in r.text


def test_the_system_page_loads_the_panel_and_names_the_probe(client, monkeypatch):
    monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")
    page = client.get("/admin/system").text
    assert 'hx-get="/admin/system/gpu"' in page
    assert web._effective_config()["tier1"]["GPU_PROBE_URL"] == "http://gpu-probe:8080"


def test_the_panel_is_an_admin_route(client):
    del client.headers["X-Admin-Token"]
    assert client.get("/admin/system/gpu").status_code == 401


@pytest.mark.parametrize("n,text", [(None, "—"), (512, "512 B"), (416 * 1024, "416 KiB"),
                                    (272148 * 1024, "266 MiB"), (34242297856, "31.89 GiB")])
def test_sizes_are_in_the_drivers_binary_units(n, text):
    assert gpu.size(n) == text


def test_nothing_but_the_probe_imports_the_probe():
    """The reader of the host's processes stays in its own container: no
    module the web or the worker loads may import it."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    importers = [
        str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py")
        if p.name != "gpu_probe.py" and any(
            re.match(r"\s*(from|import)\s", line) and "gpu_probe" in line
            for line in p.read_text(encoding="utf-8").splitlines())]
    assert importers == []


def test_real_host_ram_is_shown_apart_from_mapped_files_and_a_pushed_out_model_is_flagged(
        client, monkeypatch):
    """Operator, 2026-09-20: "separate real RAM occupancy from memory mapped
    RAM". And what the separation showed the same day: another container took
    21 GiB of the card, the driver moved every resident model's buffers to
    host RAM without an error to anyone, and two models died. The panel says
    which host figure is real, and says when a model is no longer on the card."""
    from dataclasses import replace
    qwen, medcpt = ARC.processes
    arc = replace(ARC, processes=(replace(qwen, ram=657 << 20, mapped=2400 << 20), medcpt))
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(arc,)))
    html = client.get("/admin/system/gpu").text
    for heading in ("GPU buffers in host RAM", "model RAM", "mapped file (reclaimable)"):
        assert heading in html
    assert "657 MiB" in html and "2.34 GiB" in html
    assert "NOT occupancy" in html, "the column's own title says which figure is not real"
    # MedCPT: 416 KiB on the card, 414 MiB in host RAM — pushed out; Qwen is not
    assert medcpt.pushed_out and not qwen.pushed_out
    assert html.count(">pushed off the card</span>") == 1
    assert "a model pushed off the card</span></summary>" in html, "in sight with the drawer closed"
    # a row whose split was not read says so, never 0
    assert "<td>—</td>" in html


def test_a_few_mib_of_buffers_in_host_ram_is_not_a_model_pushed_out():
    small = gpu.GpuProcess(1, "probe", "python", "host", vram=0, host=64 << 20)
    assert not small.pushed_out, "every GPU client keeps some buffers in host memory"
    assert gpu.GpuProcess(2, "chat", "llama-server", "ix-localai-localai-1", vram=476 << 10,
                          host=7403 << 20).pushed_out, "the node, 2026-09-20"


def test_a_probe_one_deploy_ahead_of_the_web_is_still_read():
    snap = gpu.to_json(gpu.GpuSnapshot(devices=(ARC,)))
    snap["devices"][0]["processes"][0]["a_field_from_the_future"] = 1
    back = gpu.from_json(snap)
    assert back.devices[0].processes[0].pid == 9317
    old = gpu.to_json(gpu.GpuSnapshot(devices=(ARC,)))
    for p in old["devices"][0]["processes"]:
        del p["ram"], p["mapped"]                       # a probe one deploy BEHIND
    assert gpu.from_json(old).devices[0].processes[0].ram is None


# ---- the bar without LocalAI's count ----------------------------------------------------

def test_the_bar_says_what_the_processes_hold_and_what_no_process_shown_holds(client, monkeypatch):
    """Where LocalAI gives no count per model, the bar is the processes the probe
    read, what the driver counts beyond them, and what is free — no model
    container is singled out (the model pool, which was, retired on 2026-10-09)."""
    from dataclasses import replace
    qwen = gpu.GpuProcess(1, "qwen", "llama-server", "ix-localai-localai-1", vram=8 << 30, host=0)
    other = gpu.GpuProcess(2, "x", "comfy", "ix-comfyui-1", vram=4 << 30, host=0)
    arc = replace(ARC, processes=(qwen, other), vram_total=32 << 30, vram_used=16 << 30)
    shares = arc.shares()
    assert round(shares["processes"], 1) == 37.5 and round(shares["unattributed"], 1) == 12.5
    assert round(shares["free"], 1) == 50.0


def test_the_card_is_broken_down_by_model_louise_or_other(client, monkeypatch):
    """Operator, 2026-10-12: the panel lists every model on the card as
    Louise's or another's, from LocalAI's own count (app/gpu_models.py)."""
    from dataclasses import replace
    from app import gpu_models
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(replace(ARC, processes=()),)))
    monkeypatch.setattr(config, "CHAT_URL", "http://nas:30286/v1")
    monkeypatch.setattr(config, "CHAT_MODEL", "louise-chat-qwen3-8b")
    monkeypatch.setattr(gpu_models, "_get_json", lambda url: {"loaded_models": [
        {"id": "louise-chat-qwen3-8b", "backend": "intel-sycl-f16-llama-cpp", "size_vram": 7 << 30},
        {"id": "flux-dev", "backend": "stablediffusion-ggml", "size_vram": 6 << 30}]})
    html = client.get("/admin/system/gpu").text
    assert '<span class="swatch louise"></span><div class="use">chat</div>' in html
    assert "flux-dev" in html and "other models 6.00 GiB" in html
    assert "outside LocalAI" in html


def test_a_model_is_shown_on_its_own_card_and_the_cpu_has_a_card(client, monkeypatch):
    """Operator, 2026-11-11: each GPU card's models under "models", and a CPU
    card with the models loaded there (app/gpu_models.py `by_card`)."""
    from dataclasses import replace
    from app import gpu_models
    igpu = gpu.GpuDevice(card="card1", pdev="0000:c5:00.0", driver="amdgpu", vendor_id="1002",
                         device_id="13c0", vendor="AMD", name="Radeon 610M",
                         vram_total=516 << 20, vram_used=400 << 20,
                         processes=(gpu.GpuProcess(5005, "parakeet", "parakeet", "localai",
                                                   300 << 20, 0, ns_pid=77),))
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(replace(ARC, processes=()), igpu)))
    monkeypatch.setattr(config, "CHAT_URL", "http://nas:30286/v1")
    monkeypatch.setattr(config, "CHAT_MODEL", "louise-chat-qwen3-8b")
    monkeypatch.setattr(gpu_models, "_get_json", lambda url: {"loaded_models": [
        {"id": "louise-chat-qwen3-8b", "backend": "intel-sycl-f16-llama-cpp", "size_vram": 7 << 30},
        {"id": "stt-igpu", "backend": "vulkan-parakeet-cpp", "size_vram": 300 << 20, "process": {"pid": 77}},
        {"id": "stt-cpu", "backend": "intel-sycl-f16-parakeet-cpp", "size_vram": 0, "process": {"pid": 66}}]})
    html = client.get("/admin/system/gpu").text
    b70, radeon, cpu = html.index("Arc Pro B70"), html.index("Radeon 610M"), html.index("<b>CPU</b>")
    unknown = html.index("<b>GPU, card not known</b>")
    assert b70 < radeon < html.index("stt-igpu") < cpu < html.index("stt-cpu") < unknown \
        < html.index("louise-chat-qwen3-8b"), "the chat's backend is on no card the probe read: no guess"
    assert 'id="gpu-models-0000:c5:00.0" data-keep-open' in html and 'id="cpu-models" data-keep-open' in html
    assert 'id="unplaced-models" data-keep-open' in html
    assert html.count("models · 1 loaded") == 3


def test_an_integrated_card_s_models_in_shared_ram_are_never_pushed_off(client, monkeypatch):
    """Operator, 2026-11-11: "Radeon 610M igpu, the only model loaded is
    marked as pushed off the card, because it is in RAM". An integrated card's
    memory IS the system's RAM: its buffers there are where they belong."""
    from dataclasses import replace
    stt = gpu.GpuProcess(41, "parakeet-cpp-multilingual-diarization-speakers-igpu", "parakeet",
                         "ix-localai-localai-1", vram=12 << 20, host=1900 << 20)
    assert stt.pushed_out, "by the dedicated card's rule it would be"
    igpu = gpu.GpuDevice(card="card1", pdev="0000:0e:00.0", driver="amdgpu", vendor_id="1002",
                         device_id="164e", vendor="AMD", name="Radeon 610M", vram_total=512 << 20,
                         vram_used=40 << 20, processes=(stt,), integrated=True)
    assert not igpu.pushed_off(stt) and igpu.pushed_off_processes == ()
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(igpu,)))
    html = client.get("/admin/system/gpu").text
    assert "pushed off the card" not in html
    assert "GPU buffers in shared RAM" in html and "part of the processor" in html
    # a dedicated card, or one that could not say, keeps the rule
    for integrated in (False, None):
        card = replace(igpu, integrated=integrated)
        assert card.pushed_off(stt)
    back = gpu.from_json(gpu.to_json(gpu.GpuSnapshot(devices=(igpu,))))
    assert back.devices[0].integrated is True


def test_a_web_one_deploy_behind_reads_a_device_field_it_does_not_know():
    snap = gpu.to_json(gpu.GpuSnapshot(devices=(ARC,)))
    snap["devices"][0]["a_device_field_from_the_future"] = 1
    assert gpu.from_json(snap).devices[0].card == ARC.card


def test_the_memory_legend_is_one_entry_a_line(client, monkeypatch):
    """Operator, 2026-11-11: "System tab: the memory occupation legend, one
    entry per line"."""
    _probe(monkeypatch, gpu.GpuSnapshot(devices=(ARC,)))
    html = client.get("/admin/system/gpu").text
    legend = re.search(r'<div class="legend note">(.*?)</div></div>', html, re.S).group(1)
    entries = re.findall(r'<div><span class="swatch [a-z]+"></span>[^<]+</div>', legend + "</div>")
    assert len(entries) >= 2 and entries[0].startswith("<div>")
