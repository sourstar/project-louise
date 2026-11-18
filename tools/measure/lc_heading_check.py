"""Check proposed LCSH headings against the Library of Congress authorities (id.loc.gov).

    python -m tools.measure.lc_heading_check HEADINGS.txt --out RESULT.csv

HEADINGS.txt holds one proposed heading per line ("Artists -- Biography",
"Rosen, Erwin, 1876-1923"). For each, the longest prefix LC established
as one heading is looked up by its exact label among LC subject headings, then
LC names (id.loc.gov's "label" service: 302 = an authorized heading, 404 = none);
an unknown name is also searched left-anchored ("suggest2") so the authorized
form can be proposed instead. Each SUBDIVISION is looked up as a subject
label, or a name label for a geographic one; free-floating ones ("Biography", "Personal narratives",
"Fiction", "Early works to 1800") resolve, an invented one does not.

The standard being used is LC's own Linked Data Service — not a scrape of a
catalogue. One request at a time, a pause between them, a cache per run.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://id.loc.gov"
PAUSE_S = 0.4
_cache: dict[str, str] = {}

#: "-- Personal narratives, British" (or American, French …) is formed under a war
#: by pattern; LC's label service has no record for each nationality.
NATIONAL_NARRATIVES = re.compile(r"^Personal narratives, [A-Z][a-z]+( [A-Z][a-z]+)?$")


def _label(scheme: str, label: str) -> str:
    """The authority URI for an exact label in a scheme, or ''."""
    key = f"{scheme}|{label}"
    if key in _cache:
        return _cache[key]
    url = f"{BASE}/authorities/{scheme}/label/{urllib.parse.quote(label)}"
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "lc-heading-check/1"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # type: ignore[no-untyped-def]
            return None

    opener = urllib.request.build_opener(NoRedirect)
    uri = ""
    for attempt in range(3):
        try:
            opener.open(req, timeout=30)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303):
                uri = e.headers.get("X-URI") or e.headers.get("Location", "")
                break
            if e.code == 404:
                break
            time.sleep(2 * (attempt + 1))
            continue
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 * (attempt + 1))
            continue
        break
    time.sleep(PAUSE_S)
    _cache[key] = uri
    return uri


def _suggest(label: str) -> str:
    """The first authorized name starting like `label`, or ''."""
    url = f"{BASE}/authorities/names/suggest2/?q={urllib.parse.quote(label)}&count=1"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lc-heading-check/1"}),
                                    timeout=30) as r:
            hits = json.load(r).get("hits", [])
    except (urllib.error.URLError, TimeoutError, ValueError):
        hits = []
    time.sleep(PAUSE_S)
    return hits[0]["aLabel"] if hits else ""


def check(heading: str) -> dict[str, str]:
    parts = [p.strip() for p in heading.split(" -- ")]
    # The longest prefix that LC established as one heading ("France -- History --
    # Louis XV, 1715-1774"): period and topical subdivisions exist only there.
    uri = scheme = ""
    k = len(parts)
    while k > 0:
        label = "--".join(parts[:k])  # LC writes compound labels without spaces
        for sch in ("subjects", "names"):
            uri = _label(sch, label)
            if uri:
                scheme = sch
                break
        if uri:
            break
        k -= 1
    main = " -- ".join(parts[:k]) if uri else parts[0]
    subs = parts[k:] if uri else parts[1:]
    suggestion = "" if uri else _suggest(parts[0])
    # The rest are free-floating: a subject label, or a name label for a geographic one.
    bad_subs = [s for s in subs if not (NATIONAL_NARRATIVES.match(s) or _label("subjects", s) or _label("names", s))]
    ok = bool(uri) and not bad_subs
    return {"heading": heading, "main": main, "main_found_in": scheme, "main_uri": uri,
            "name_suggestion": suggestion, "subdivisions_not_found": "; ".join(bad_subs),
            "status": "ok" if ok else "check"}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("headings", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    hs = [h.strip() for h in a.headings.read_text(encoding="utf-8").splitlines() if h.strip()]
    rows = [check(h) for h in dict.fromkeys(hs)]
    with a.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} headings: {sum(r['status'] == 'ok' for r in rows)} ok, "
          f"{sum(r['status'] != 'ok' for r in rows)} to check")


if __name__ == "__main__":
    main()
