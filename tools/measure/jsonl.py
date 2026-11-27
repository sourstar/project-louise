"""The lines of a JSON Lines file: "\\n" is the only separator (jsonlines.org: "the line separator is '\\n'"; a "\\r"
before it is dropped, as str.splitlines() drops it). str.splitlines() is NOT that: it also breaks at U+2028 and
U+2029 (and \\x0b, \\x0c, \\x1c-\\x1e, \\x85), which JSON keeps unescaped inside a string - written with
ensure_ascii=False, a PubMed abstract cut a capture's line in two (München, 2026-11-27)."""
from __future__ import annotations


def lines(text: str) -> list[str]:
    """`text` cut at "\\n" alone; no empty element for a final newline, a trailing "\\r" dropped from each line."""
    if not text:
        return []
    parts = text.split("\n")
    if parts[-1] == "":
        parts.pop()
    return [p[:-1] if p.endswith("\r") else p for p in parts]
