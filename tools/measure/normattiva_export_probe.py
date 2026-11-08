"""Does any of Normattiva's export formats carry the Codice civile's
partitions? A PROBE (operator, 2026-11-08, option A: "Take the partition from
Normattiva's own data, if it is there"; docs/notes/legal-chunking-retrieval.md
§3).

The AKN member holds none: its 3,280 annexes are flat (München's ccprobe), and
the API has no tree route (nvapi). The open-data export also offers XML, JSON,
HTML and URI (`tipologiche/estensioni`). A first probe with München's own
client was refused at check-status by the Poligrafico's protection (409), and
was not retried with other headers: getting past an origin's protection is not
ours to do.

So this probe runs Louise's OWN export client, the node's acquisition path:
`app/acquire/export_job.run`, the endpoint's own headers
(`transport_https.headers_for`, Louise's User-Agent) and the bandwidth budget
(invariant 5). It runs:
  1. AKN FIRST, the format the node already exports: if that is refused too,
     the refusal is general and nothing more is sent (the operator's stop);
  2. then each other format, ONE JOB AT A TIME, stopping at the first
     refusal.
A job exports the acts EMANATED ON ONE DAY (`--day 1942-03-16`: the R.D. 262
that is the Codice civile, and the few acts of that day), in force on
`--vigenza`.

For each zip it reports:
  - the members' names and sizes;
  - the root element and DOCTYPE of an XML member;
  - the elements whose local name is a partition's (`libro`, `titolo`, `capo`,
    `sezione`, `chapter`, `book` …), counted;
  - the heading-like lines beginning LIBRO / TITOLO / CAPO / SEZIONE (short
    lines, not the notes of repealed articles that mention a partition), each
    example with the next article line after it.
The zips and `report.json` / `report.txt` are written to `--out`. Nothing is
written to a collection.

Reproducibility (a verifier must get the same bytes) is the NEXT question, and
only for a format that carries the partitions: run this again with
`--formats <that one>` and compare the zips' sha256 in the two reports.

    python tools/measure/normattiva_export_probe.py --out /root/measure/nvexport2 \\
        --day 1942-03-16 --match 19420316_262
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: a partition element's local name, AKN's and NIR's (Normattiva's older XML)
PARTITION_TAGS = frozenset({"libro", "titolo", "capo", "sezione", "parte", "partizione",
                            "book", "part", "title", "chapter", "section", "subsection",
                            "hcontainer", "tome", "division"})
#: a heading line: the level word, then its number, at the line's start
HEADING = re.compile(r"^\s*(LIBRO|TITOLO|CAPO|SEZIONE|PARTE)\s+([IVXLCDM]+|PRIM\w*|SECOND\w*|TERZ\w*|"
                     r"QUART\w*|QUINT\w*|SEST\w*|\d+)\b", re.I)
#: a note ABOUT a partition (a repealed article's), not the partition
NOTE = re.compile(r"ABROGAT|SOSTITUZIONE|NON PIU'|PREVISTO|DISPOST", re.I)
ARTICLE = re.compile(r"^\s*Art(?:icolo)?\.?\s*\d+", re.I)
HEADING_MAX = 160


def _text_lines(data: bytes, kind: str) -> list[str]:
    """The member's text, one line per block: tags become line breaks (XML,
    HTML), a JSON document's strings are its lines."""
    text = data.decode("utf-8", errors="replace")
    if kind == "json":
        try:
            out: list[str] = []

            def walk(v: Any) -> None:
                if isinstance(v, str):
                    out.extend(v.splitlines())
                elif isinstance(v, dict):
                    for x in v.values():
                        walk(x)
                elif isinstance(v, list):
                    for x in v:
                        walk(x)
            walk(json.loads(text))
            text = "\n".join(out)
        except ValueError:
            pass
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _xml_facts(data: bytes) -> dict[str, Any]:
    head = data[:2000].decode("utf-8", errors="replace")
    doctype = re.search(r"<!DOCTYPE\s+([^\s>\[]+)[^>]*>", head)
    out: dict[str, Any] = {"doctype": doctype.group(0)[:200] if doctype else None}
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        out["parse_error"] = str(e)[:200]
        return out
    local = lambda t: t.rsplit("}", 1)[-1].lower()  # noqa: E731
    out["root"] = root.tag
    tags = Counter(local(el.tag) for el in root.iter())
    out["partition_elements"] = {t: n for t, n in sorted(tags.items()) if t in PARTITION_TAGS}
    return out


def analyse(name: str, data: bytes) -> dict[str, Any]:
    """One member: its size, sha256, partition elements (XML) and heading
    lines with the article each precedes."""
    from app.plugins.normattiva import akn
    kind = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    out: dict[str, Any] = {"member": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if kind == "xml":
        try:
            data, stored = akn.decode_member(data)      # two of the AKN members are base64
            out["stored"] = stored
        except Exception:   # noqa: BLE001 — not an AKN member: read as it is
            pass
        out.update(_xml_facts(data))
    lines = _text_lines(data, kind if kind in ("json", "html", "htm") else "xml")
    headings, examples = 0, []
    for i, ln in enumerate(lines):
        if len(ln) > HEADING_MAX or not HEADING.match(ln) or NOTE.search(ln):
            continue
        headings += 1
        if len(examples) < 10:
            nxt = next((x for x in lines[i + 1:i + 40] if ARTICLE.match(x)), None)
            examples.append({"heading": ln, "before": nxt})
    out["heading_lines"] = headings
    out["examples"] = examples
    return out


def probe(formats: list[str], partition: dict, *, vigenza: str, out: Path, match: str | None,
          run: Callable | None = None) -> dict[str, Any]:
    """One job per format, in order, stopping at the first refusal."""
    from app.acquire import export_job, specs, transport_https
    from app.acquire.spec import TransportError
    if formats and formats[0] != "AKN" and "AKN" in formats:
        raise SystemExit("AKN goes first: it is the format the node already exports, and its refusal "
                         "stops the probe before any other format is asked for")
    endpoint = specs.NORMATTIVA_SPEC.endpoints[0]
    api, headers = export_job.api_root(endpoint.url), transport_https.headers_for(endpoint)
    run = run or export_job.run
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"partition": partition, "vigenza": vigenza, "api": api,
                              "user_agent": headers.get("User-Agent"), "jobs": []}
    for fmt in formats:
        body = export_job.body_for(partition, vigenza_day=vigenza, formato=fmt)
        dest = out / "zips" / f"{fmt}.zip"
        t0 = time.monotonic()
        try:
            size = run(api, body, dest, headers=headers)
        except TransportError as e:
            report["jobs"].append({"format": fmt, "refused": str(e), "seconds": round(time.monotonic() - t0, 1)})
            report["stopped"] = f"{fmt} refused: nothing more is sent to the origin"
            break
        job: dict[str, Any] = {"format": fmt, "bytes": size, "seconds": round(time.monotonic() - t0, 1),
                               "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "members": []}
        with zipfile.ZipFile(io.BytesIO(dest.read_bytes())) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                entry: dict[str, Any] = {"member": info.filename, "bytes": info.file_size}
                if match is None or match in info.filename:
                    entry = analyse(info.filename, z.read(info))
                job["members"].append(entry)
        report["jobs"].append(job)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "report.txt").write_text(summary(report), encoding="utf-8")
    return report


def summary(report: dict[str, Any]) -> str:
    lines = [f"partition {report['partition']}, vigenza {report['vigenza']}, as {report['user_agent']}"]
    for job in report["jobs"]:
        if "refused" in job:
            lines.append(f"{job['format']}: REFUSED after {job['seconds']} s: {job['refused']}")
            continue
        lines.append(f"{job['format']}: {job['bytes']} bytes in {job['seconds']} s, zip sha256 {job['sha256']}")
        for m in job["members"]:
            if "heading_lines" not in m:
                lines.append(f"  {m['member']} {m['bytes']} bytes (not analysed)")
                continue
            lines.append(f"  {m['member']} {m['bytes']} bytes: root {m.get('root')}, doctype {m.get('doctype')}, "
                         f"partition elements {m.get('partition_elements')}, heading lines {m['heading_lines']}")
            for ex in m["examples"]:
                lines.append(f"    {ex['heading']!r} before {ex['before']!r}")
    if report.get("stopped"):
        lines.append(f"STOPPED: {report['stopped']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--day", required=True, help="the acts emanated that day, YYYY-MM-DD")
    ap.add_argument("--type", default=None, help="denominazioneAtto, to narrow the day's acts")
    ap.add_argument("--vigenza", default=time.strftime("%Y-%m-%d", time.gmtime()))
    ap.add_argument("--formats", default="AKN,XML,JSON,HTML,URI")
    ap.add_argument("--match", default=None, help="analyse only the members whose name holds this")
    a = ap.parse_args(argv)
    partition = {"day": a.day, "form": "V", **({"type": a.type} if a.type else {})}
    report = probe([f.strip().upper() for f in a.formats.split(",") if f.strip()], partition,
                   vigenza=a.vigenza, out=a.out, match=a.match)
    sys.stdout.write(summary(report))
    return 3 if report.get("stopped") else 0


if __name__ == "__main__":
    sys.exit(main())
