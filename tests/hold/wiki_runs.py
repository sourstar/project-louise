"""A wikipedia collection that follows its dump runs, for the tests of the
update by cadence (app/hold/update.py, app/hold/origin_run.py): a real
wikipedia Codex whose `origin.spec.files` names its FIRST run, dumps of any
run written as dumps.wikimedia.org writes them (multistream, index,
page_props), and the node's collection file holding one language."""
from __future__ import annotations

import bz2
import hashlib
from pathlib import Path
from xml.sax.saxutils import escape

FIRST = "20261001"


def page(pid: int, title: str, text: str, rev: int | None = None) -> str:
    return (f"<page><title>{escape(title)}</title><ns>0</ns><id>{pid}</id><revision>"
            f"<id>{rev or pid * 100}</id><timestamp>2026-09-30T12:00:00Z</timestamp>"
            f"<text>{escape(text)}</text></revision></page>\n")


def dump(corpus: Path, run: str, pages, *, wiki: str = "trwiki", props=None) -> Path:
    """`<corpus>/<wiki>/<run>/`: one multistream stream per page, its index
    and the run's page_props (`props`: (page, name, value) rows)."""
    from tests import wikipedia_props
    d = Path(corpus) / wiki / run
    d.mkdir(parents=True, exist_ok=True)
    base = f"{wiki}-{run}-pages-articles-multistream"
    body, index, at = b"", [], 0
    for p in pages:
        stream = bz2.compress(page(*p).encode("utf-8"))
        index.append(f"{at}:{p[0]}:{p[1]}\n")
        body += stream
        at += len(stream)
    (d / f"{base}.xml.bz2").write_bytes(body)
    (d / f"{base}-index.txt.bz2").write_bytes(bz2.compress("".join(index).encode("utf-8")))
    wikipedia_props.write(d / f"{base}.xml.bz2", props or ())
    return d


def codex(root: Path, name: str = "wikipedia", *, first: str = FIRST):
    """The Codex, created as `hold collection create` writes one (its
    calibration member and SHARD), pinning every wiki's files of `first`."""
    from app.acquire import specs
    from app.acquire.specs import ACQUIRE_REGISTRY
    from app.hold import collection as col
    from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
    from tests.hold.test_template import ORIGIN, SPACE, VALIDATION
    spec = ACQUIRE_REGISTRY["wikipedia"]
    files = [{"rel_path": f.rel_path, "size": None, "checksum": None}
             for w in specs.WIKIS for f in specs._wiki_files(w, first)]
    origin = {**ORIGIN, "cutoff_rule": "dated-dumps", "unit": "index-page",
              "form": "the dated multistream dumps", "clock": "UTC",
              "spec": {"source_id": "wikipedia", "transport": "https",
                       "url": "https://dumps.wikimedia.org", "layout": "container-files",
                       "listing": "snapshot-index", "files": files, "filters": list(spec.filters),
                       "checksums": {"kind": "none", "suffix": "", "algo": ""},
                       "delta": {"mode": "dated-dumps", "subdir": None}}}
    body = spec.terms_text_path.read_bytes()
    recipe = {"descriptive": {"title": "Wikipedia", "summary": "Articles."}, "space": dict(SPACE),
              "work": {"tokens": 40_000_000}, "origin": origin, "validation": dict(VALIDATION),
              "license": {"class": "yellow",
                          "terms_sha256": "sha256:" + hashlib.sha256(body).hexdigest()}}
    Path(root).mkdir(parents=True, exist_ok=True)
    return col.create_from_recipe(Path(root), "wikipedia", recipe, platform_image=PLATFORM,
                                  embed=fake_embed, embed_query=fake_query_embed, name=name,
                                  key=KEY, now=NOW)


def node_collection(data_dir: Path, config_dir: Path, name: str = "wikipedia", *,
                    first: str = FIRST, slices: str = '["lang=tr"]'):
    """The Codex under the node's root, acknowledged, and its collection
    file: one language held, its corpus `<root>/<name>/origin`."""
    from app import louise
    root = Path(data_dir) / "collections"
    cx = codex(root, name, first=first)
    assert louise.main(["hold", "collection", "ack", name, "--root", str(root), "--yes"]) == 0
    (Path(config_dir) / "collections").mkdir(parents=True, exist_ok=True)
    (Path(config_dir) / "collections" / f"{name}.toml").write_text(
        f'codex = "{cx.collection_id}"\nslices = {slices}\nkinds = ["records", "vectors"]\n'
        f'share = false\n', encoding="utf-8")
    return root, cx
