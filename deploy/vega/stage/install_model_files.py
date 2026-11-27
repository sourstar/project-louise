#!/usr/bin/env python3
"""A PACKAGE AND ITS MODEL FILES, TOGETHER (compute precision, stated:
docs/notes/approved-encoders.md "Compute precision, stated" and its deploy
plan).

WHY. VeGa's wrapper states no run for a model whose file does not say what it
computes in (`vega_wrapper.stated_precision`). A wrapper built before its
model files say it is an OUTAGE of every such model, until someone edits the
files. So the order is not left to whoever deploys: build_vega_package.sh runs
this FIRST, before it removes anything of the package, and builds nothing when
it refuses.

WHAT IT DOES, for the package PKG in front of the real backend BACKEND:
  1. reads every model file of LocalAI's models path whose `backend` is PKG —
     the node's files are what runs, and not all of them are staged ones
     (seed_pinned.py's count, 2026-11-26);
  2. takes the precision each one states, or the one its STAGED counterpart
     states — the staged model file of the same `name`
     (deploy/vega/stage/models, or a peer's deploy/localai-*/models), or the
     gallery entry of that name, whose overrides are all the repo holds of a
     gallery-only model (the three `louise-sum-*`);
  3. holds each to the wrapper's OWN rule (`stated_precision`, the very
     function, against BACKEND's name), so what passes here is what the new
     wrapper states;
  4. and only if every file passes, writes the staged statement into the
     files that lack it: one line in their `env`, everything else of the
     node's file byte for byte (read back: the file is the same model but for
     that key), written beside it and renamed over it.
A file that states no precision, and that nothing staged states one for, is
refused unless the wrapper exempts it (PRECISION_EXEMPT: DAR-legal-it and
MedCPT, operator 2026-11-27 — those are never written). A node file that
states another precision than its staged one is refused: which is right is a
decision, not a merge. Nothing is written when anything is refused.

Files before package, never the reverse: a file that states its precision
under the OLD wrapper loads as before, its env one key longer (a new run for
an encoder, which the package's rebuild makes anyway).

    python3 deploy/vega/stage/install_model_files.py --package vega-louise-llama-cpp \\
        --backend louise-sycl-f16-llama-cpp-… --models /models \\
        --staged deploy/vega/stage/models --staged deploy/vega/stage/gallery [--check]

Exit 0: every file behind the package states its precision (after the
writes; with --check, nothing written and what would be said). 1: refused,
each file named, nothing written. 2: a file could not be read.

Standard alternative: none — LocalAI has no "a backend requires these model
keys" setting, and its gallery installs a model's file without the backend's
say. Reads LocalAI's JSON or YAML model files with the standard library alone
(the build runs in python:3.12-slim): the subset a model file's `name`,
`backend`, `env`, `options` and `parameters.model` are written in.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]          # deploy/vega/stage/ -> the repo (/src)
KEY = "LOUISE_COMPUTE_PRECISION"


class Unreadable(ValueError):
    """A model file this reader cannot read: nothing is decided for it."""


def load_wrapper() -> Any:
    """deploy/vega/vega_wrapper.py, with the repo's app/ beside it (as the package)."""
    name = "vega_wrapper_install"
    if name in sys.modules:
        return sys.modules[name]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / "vega" / "vega_wrapper.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                      # its dataclasses look themselves up
    spec.loader.exec_module(module)
    return module


# ---- reading a model file: JSON (with # comments) or LocalAI's YAML ------------------

def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _yaml(text: str) -> dict[str, Any]:
    """LocalAI's YAML model file, as much of it as names the model: top-level
    scalars, and the `env` and `parameters` maps and the `options` list one
    level down. Anything deeper (a template, a block scalar) is skipped."""
    doc: dict[str, Any] = {}
    section: str | None = None
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip() or line.strip() == "---":
            continue
        if section == "options" and line.startswith("- "):     # a list at the key's column
            doc["options"].append(_scalar(line[2:]))
            continue
        if not line[0].isspace():
            key, sep, value = line.partition(":")
            if not sep:
                raise Unreadable(f"line {raw!r} is no key")
            key, value = key.strip(), value.strip()
            section = key if not value and key in ("env", "parameters", "options") else None
            if value in ("{}", "[]") and key in ("env", "parameters", "options"):
                doc[key] = [] if value == "[]" else {}
            elif value:
                doc[key] = _scalar(value)
            elif section == "options":
                doc[key] = []
            elif section:
                doc[key] = {}
            continue
        if section == "options":
            item = line.strip()
            if item.startswith("- "):
                doc["options"].append(_scalar(item[2:]))
            continue
        if section in ("env", "parameters"):
            indent = len(line) - len(line.lstrip())
            if section == "env" or indent <= 4:
                key, sep, value = line.strip().partition(":")
                if sep and value.strip():
                    doc[section][key.strip()] = _scalar(value)
    return doc


def read_model(text: str) -> dict[str, Any]:
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    if body.lstrip().startswith("{"):
        try:
            doc = json.loads(body)
        except ValueError as e:
            raise Unreadable(f"not JSON: {e}") from None
        if "overrides" in doc and isinstance(doc["overrides"], dict):  # a gallery entry
            doc = {**doc["overrides"], "name": doc.get("name")}
        return doc
    return _yaml(text)


def _canon(w: Any, doc: dict[str, Any], env: dict[str, str]) -> Any:
    model = str((doc.get("parameters") or {}).get("model") or doc.get("name") or "m")
    return w.canon_from_protojson(json.dumps({
        "Model": model, "ModelFile": f"/models/{model}",
        "Options": [str(o) for o in doc.get("options") or []], "EnvVars": env}))


# ---- writing the one key -------------------------------------------------------------

def with_key(text: str, value: str) -> str:
    """The file's text with `KEY: value` in its env, every other byte kept."""
    if re.search(r'^\s*\{', "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))):
        empty = re.search(r'"env": \{\s*\}', text)
        if empty is not None:
            return text[:empty.start()] + f'"env": {{\n    "{KEY}": "{value}"\n  }}' + text[empty.end():]
        m = re.search(r'^(\s*)"env": \{\s*\n', text, re.M)
        if m is None:                                # no env: after the last top-level member
            end = text.rstrip().rfind("}")
            head = text[:end].rstrip()
            return f'{head},\n  "env": {{\n    "{KEY}": "{value}"\n  }}\n' + text[end:]
        start = m.end()
        indent = re.match(r'(\s*)', text[start:]).group(1)
        return text[:start] + f'{indent}"{KEY}": "{value}",\n' + text[start:]
    empty = re.search(r'^env:[ \t]*\{\}[ \t]*$', text, re.M)
    if empty is not None:
        return text[:empty.start()] + f'env:\n    {KEY}: "{value}"' + text[empty.end():]
    m = re.search(r'^env:[ \t]*\n', text, re.M)
    if m is None:
        return text + ("" if text.endswith("\n") else "\n") + f'env:\n    {KEY}: "{value}"\n'
    rest = text[m.end():]
    indent = re.match(r'([ \t]+)', rest)
    pad = indent.group(1) if indent else "    "
    return text[:m.end()] + f'{pad}{KEY}: "{value}"\n' + rest


# ---- the plan ------------------------------------------------------------------------

@dataclass
class Plan:
    writes: dict[Path, str] = field(default_factory=dict)     # file -> its new text
    stated: dict[str, str] = field(default_factory=dict)      # file name -> precision
    exempt: dict[str, str] = field(default_factory=dict)      # file name -> why
    refused: list[str] = field(default_factory=list)


def _behind(doc: dict[str, Any], package: str) -> bool:
    return str(doc.get("backend") or "") == package


def staged_statements(staged: list[Path], package: str) -> dict[str, str | None]:
    """name -> the precision the repo's file of that name states (None: none)."""
    out: dict[str, str | None] = {}
    for directory in staged:
        for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.json")]):
            doc = read_model(path.read_text(encoding="utf-8"))
            if _behind(doc, package):
                out[str(doc.get("name") or path.stem)] = (doc.get("env") or {}).get(KEY)
    return out


def plan(w: Any, *, package: str, backend: str, models: Path, staged: list[Path]) -> Plan:
    statements = staged_statements(staged, package)
    p = Plan()
    for path in sorted(f for f in models.iterdir() if f.suffix in (".yaml", ".yml") and f.is_file()):
        text = path.read_text(encoding="utf-8")
        try:
            doc = read_model(text)
        except Unreadable as e:
            if package in text:                       # it may be behind the package: undecided
                raise Unreadable(f"{path.name}: {e}") from None
            continue                                  # another backend's, whatever it says
        if not _behind(doc, package):
            continue
        name = str(doc.get("name") or path.stem)
        env = {str(k): str(v) for k, v in (doc.get("env") or {}).items()}
        said, want = env.get(KEY), statements.get(name)
        if said is None and want is None:
            why = w.precision_exempt(_canon(w, doc, env))
            if why:
                p.exempt[path.name] = why                 # held: never written
                continue
            p.refused.append(f"{path.name}: states no compute precision, and no staged file of "
                             f"`{name}` states one — stage it, or set env {KEY} in it")
            continue
        if said is not None and want is not None and said != want:
            p.refused.append(f"{path.name}: states {KEY}={said}, its staged file {want}: which "
                             f"is right is decided, not merged")
            continue
        value = said or want
        try:
            w.stated_precision(backend, _canon(w, doc, {**env, KEY: value}))
        except w.loadopts.Unstatable as e:
            p.refused.append(f"{path.name}: {e}")
            continue
        p.stated[path.name] = value
        if said is None:
            new = with_key(text, value)
            again = read_model(new)
            if {**doc, "env": {**(doc.get("env") or {}), KEY: value}} != again:
                p.refused.append(f"{path.name}: the key cannot be written without changing "
                                 f"anything else; add env {KEY}: \"{value}\" by hand")
                continue
            p.writes[path] = new
    return p


def write(p: Plan) -> None:
    """Each file beside itself, then renamed over it: its owner and mode kept."""
    for path, text in p.writes.items():
        st = path.stat()
        tmp = path.with_name(f".{path.name}.precision.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--package", required=True)
    ap.add_argument("--backend", required=True, help="the real backend's name (vega.json `backend`)")
    ap.add_argument("--models", required=True, type=Path, help="LocalAI's models directory")
    ap.add_argument("--staged", action="append", type=Path, default=[],
                    help="a directory of the repo's model files or gallery entries (repeatable)")
    ap.add_argument("--check", action="store_true", help="say it, write nothing")
    a = ap.parse_args(argv)
    if not a.models.is_dir():
        print(f"{a.models}: not LocalAI's models directory — the package is installed with its "
              f"model files", file=sys.stderr)
        return 2
    try:
        p = plan(load_wrapper(), package=a.package, backend=a.backend, models=a.models,
                 staged=a.staged)
    except Unreadable as e:
        print(f"cannot be read, nothing decided: {e}", file=sys.stderr)
        return 2
    for name, why in sorted(p.exempt.items()):
        print(f"exempt  {name}: {why}")
    for name, value in sorted(p.stated.items()):
        written = any(path.name == name for path in p.writes)
        print(f"{'write ' if written else 'stated'}  {name}: {KEY}={value}")
    if p.refused:
        for line in p.refused:
            print(f"REFUSED {line}", file=sys.stderr)
        print(f"{a.package}: not built — its model files first (nothing written)", file=sys.stderr)
        return 1
    if not a.check:
        write(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
