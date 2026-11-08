"""What `records-vs-origin` re-fetches for a collection whose spec declares a
`UnitFetcher` (spec.py): a record's `origin` names a unit, fetched again by
the fetcher under a temporary base at its corpus path, so the plugin reads it
as it reads the corpus. The third generic origin beside member_origin.py (zip
members read by Range) and file_origin.py (mirror trees): each is chosen by
what the spec declares, never by the plugin's name (2026-11-08, operator:
"The rag engine must be common")."""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from .spec import AcquisitionSpec


class UnitOrigin:
    def __init__(self, spec: AcquisitionSpec) -> None:
        self.spec = spec
        self.fetcher = spec.unit_fetcher
        self.base = Path(tempfile.mkdtemp(prefix=f"louise-{spec.source_id}-origin-"))
        atexit.register(self.close)
        self.bytes_read = 0

    @staticmethod
    def _rel(locator: str) -> str:
        return str(locator).partition("#")[0].strip("/")

    def unit(self, locator: str) -> tuple[str, int | None]:
        return self._rel(locator), None

    def materialise(self, locator: str) -> Path:
        path, moved = self.fetcher.refetch(self.spec, self._rel(locator), self.base)
        self.bytes_read += int(moved or 0)
        return path

    def weigh(self, unit: str) -> int | None:
        """A unit's size is known once it is fetched: weighing fetches it (the
        check's bound takes units one by one, so nothing is fetched that the
        bound does not reach)."""
        try:
            out = self.materialise(unit)
        except FileNotFoundError:
            return None
        files = [out] if out.is_file() else [p for p in out.iterdir() if p.is_file()]
        return sum(p.stat().st_size for p in files)

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def for_codex(codex) -> UnitOrigin | None:
    """The unit origin of a Codex whose plugin's spec declares a UnitFetcher
    with `refetch`; None for any other."""
    from .specs import ACQUIRE_REGISTRY
    spec = ACQUIRE_REGISTRY.get(getattr(codex, "plugin", None))
    if spec is None or spec.unit_fetcher is None or not hasattr(spec.unit_fetcher, "refetch"):
        return None
    return UnitOrigin(spec)
