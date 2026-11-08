"""The `eli` namespace's one owner (docs/notes/legal-fr-de-eu.md §5.2; 06 §6.5).

A canonical ELI is jurisdiction-qualified — `eli:it:id/2003/11/15/003G0333`,
`eli:fr:…`, `eli:eu:…`, `eli:de:…` (eli.Profile.key) — and 06 §6.5 gives a
namespace ONE owner. Until 2026-11-08 the owner was `normattiva-refs`, so a
French or EU key would have been looked up among the Italian acts. This
source owns `eli` and holds nothing: it ROUTES a key by its jurisdiction to
the reference source that declares it (`eli_jurisdiction = "it"`), which
finds the row (`key_lookup`). Each jurisdiction's tables stay with its own
source, beside its native namespaces (`urn`/`akn`, `legi`/`jorf`/`nor`,
`celex`, `jurabk`).

THE STANDARD, NOT ADOPTED: the ELI ontology's own resolution is an HTTP
redirect per member state's register (eli-legal-plugins.md §1). Here every
row is already in refs.db, synced from each origin; a resolver over the
network would make a lookup depend on four registers being up.
"""
from __future__ import annotations

from typing import Any

from ..plugins.base import LicenseClass, RefSchema

SOURCE_ID = "eli-refs"
NAMESPACES = ("eli",)


def jurisdiction_of(canonical: str) -> str | None:
    """`eli:it:id/2003/…` → `it`; None for anything not in that form."""
    ns, _, rest = str(canonical or "").partition(":")
    jur, sep, path = rest.partition(":")
    if ns.strip().lower() != "eli" or not sep or not jur or not path:
        return None
    return jur.strip().lower()


def path_of(canonical: str) -> str:
    """`eli:it:id/2003/11/15/003G0333` → `id/2003/11/15/003G0333`."""
    return str(canonical).split(":", 2)[2] if str(canonical).count(":") >= 2 else ""


class EliRefSource:
    source_id = SOURCE_ID
    ref_version = 1
    protocol_version = 1
    namespaces = NAMESPACES
    license_class = LicenseClass.GREEN     # holds no data of its own

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=())

    def sync(self, state) -> Any:
        return iter(())

    def route(self, canonical: str, registry: dict) -> Any | None:
        """The reference source that declares the key's jurisdiction, or None."""
        jur = jurisdiction_of(canonical)
        if jur is None:
            return None
        for source in registry.values():
            if str(getattr(source, "eli_jurisdiction", "") or "").lower() == jur:
                return source
        return None
