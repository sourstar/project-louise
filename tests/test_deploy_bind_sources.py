"""tests/test_deploy_bind_sources.py — no deploy file binds a card by its
DRM NUMBER, and no bind source carries a colon (2026-09-22 f1b9dfc, RAGE's
clock; 2026-11-23 10:17Z, the GPU governor would not start after a reboot:
the B70 went card0 -> card1, card0 was the Radeon). A card number is handed
out in load order and swaps across boots; a PCI address is stable but has
colons, which TrueNAS splits a bind on. So a bind names a colon-free link
the deploy makes to the address."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
CARD_NUMBER = re.compile(r"/sys/class/drm/card\d+")


def _binds():
    for f in sorted(DEPLOY.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text()) or {}
        for name, svc in (doc.get("services") or {}).items():
            for v in svc.get("volumes") or ():
                src = v.get("source") if isinstance(v, dict) else str(v).split(":", 1)[0]
                yield f.name, name, str(src or ""), v


def test_no_bind_names_a_card_by_number_or_carries_a_colon():
    seen = list(_binds())
    assert any(f == "truenas-gpu-governor.yaml" for f, *_ in seen), "the governor's file is read"
    for f, svc, src, v in seen:
        assert not CARD_NUMBER.search(src), f"{f} {svc}: {v} binds a DRM card NUMBER, which swaps across boots"
        if isinstance(v, str) and src.startswith("/sys/"):
            assert v.count(":") <= 2, f"{f} {svc}: {v}: a colon in the source breaks the bind"


def test_the_governors_card_binds_are_the_links_the_deploy_makes_to_the_address():
    gov = yaml.safe_load((DEPLOY / "truenas-gpu-governor.yaml").read_text())["services"]["gpu-governor"]
    binds = {str(v).split(":")[1]: str(v).split(":")[0] for v in gov["volumes"]}
    script = (DEPLOY / "16f" / "deploy-louise-madlad-t5.sh").read_text()
    links = re.search(r"^GOVERNOR_LINKS=(\S+)$", script, re.M).group(1)
    assert binds["/gpufreq"] == f"{links}/gpufreq" and binds["/gpuhwmon"] == f"{links}/gpuhwmon"
    assert '"tile0/gt0/freq0 gpufreq" "hwmon gpuhwmon"' in script
    assert 'src="/sys/bus/pci/devices/$GOVERNOR_GPU_PCI/$1"' in script and 'ln -sfn "$src"' in script
    assert script.index("GOVERNOR_LINKS=") < script.index('c.call("app.update", "louise-gpu-governor"'), \
        "the links are made before the file is applied"
