"""Every tokenizer a chunker cuts by is in the platform image, pinned.

The `wikidata-text` chunker loads `<TOKENIZERS_DIR>/<hex>.json` and its
error said "the platform image fetches it at build" — no Dockerfile step
did, and 506 wikidata documents were errored on the node (2026-10-20). A
bind-mounted test run never sees the image, so this reads the Dockerfile:
each tokenizer digest the repository pins (the recipe's layer, the fetch
script) is ADDed under /opt/louise/tokenizers/<hex>.json and checked by
sha256sum in the same image."""
import re
from pathlib import Path

from app import config

ROOT = Path(__file__).resolve().parent.parent
# every recipe the repository keeps, not one named file: the legal recipes'
# `legal_article` v2 pinned Nemotron's tokenizer and no ADD fetched it, which
# only the node found (München, 2026-11-08)
PINS = (*sorted((ROOT / "docs" / "notes").glob("*-recipe.toml")),
        ROOT / "deploy" / "jina-embeddings-v3" / "fetch.py")


def _pinned_tokenizers() -> set[str]:
    out = set()
    for path in PINS:
        text = path.read_text(encoding="utf-8")
        out |= set(re.findall(r'tokenizer\s*=\s*"sha256:([0-9a-f]{64})"', text))
        out |= set(re.findall(r'TOKENIZER_SHA256\s*=\s*"([0-9a-f]{64})"', text))
    return out


def test_every_pinned_tokenizer_is_added_and_checked_in_the_image():
    pinned = _pinned_tokenizers()
    assert "f59925fcb90c92b894cb93e51bb9b4a6105c5c249fe54ce1c704420ac39b81af" in pinned, "the wikidata recipe pins its tokenizer"
    assert "797410dfb649a5b9ba92bc4fef7dbf4022d00e73de6867c4ac199a8846439421" in pinned, "the legal recipes pin Nemotron's"
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    base = docker.split("FROM node:", 1)[0]          # the runtime stage, not the test-only ones
    assert str(config.TOKENIZERS_DIR) == "/opt/louise/tokenizers"
    for hex_ in pinned:
        target = f"/opt/louise/tokenizers/{hex_}.json"
        assert re.search(rf"^ADD https://\S+ {re.escape(target)}$", base, re.M), f"no ADD of {target}"
        assert re.search(rf'"{hex_}  {re.escape(target)}"\s*\\?\s*\n?\s*\| sha256sum -c -', base), \
            f"{target} is not checked against its digest"
