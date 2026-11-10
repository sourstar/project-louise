"""A sister's source is her SISTER, never her mother (docs/01, Vocabulary;
docs/02 §2.7, amended 2026-11-08: "A daughter's predecessor is her
**mother**; the collection a sister or a corrective sister is derived from is
her **sister**").

The code kept calling a sister's source "mother" after the spec changed.
This guard is scoped to the functions that only ever make or check
a SISTER (or a corrective sister), so daughter code — `continue_`,
`continues`, the lineage walks — and the wire field `mother_platform_image`
(09 §9.3, a daughter's) can never trip it. A function that handles every
relation uses a neutral word ("predecessor", "the Codex she comes from").
"""
from __future__ import annotations

import argparse
import inspect
import re

import pytest

from app import louise
from app.hold import codex as cx, collection as col, records

#: "mother" in any form but the stored daughter field 09 §9.3 fixes.
MOTHER = re.compile(r"mother(?!_platform_image)", re.IGNORECASE)

#: Functions whose every line is about a sister or a corrective sister.
SISTER_ONLY = (
    col.derive, col.replace, col._slicing_at, col.recalibrate,
    cx._sister_changes, cx.corrective_epoch, cx.written_version,
    louise._sister_new, louise.cmd_derive, louise._acceptance, louise._inherited_encoder,
)

#: Functions that serve every relation: their parameters name no relative.
GENERIC = (col.write, cx.validate, cx._validate_against_predecessor, records.parser_carried)


@pytest.mark.parametrize("fn", SISTER_ONLY, ids=lambda f: f"{f.__module__}.{f.__qualname__}")
def test_a_sister_function_says_sister_not_mother(fn):
    src = inspect.getsource(fn)
    hits = [line.strip() for line in src.splitlines() if MOTHER.search(line)]
    assert not hits, f"{fn.__qualname__} calls a sister's source her mother: {hits}"


@pytest.mark.parametrize("fn", GENERIC, ids=lambda f: f"{f.__module__}.{f.__qualname__}")
def test_a_generic_function_names_no_relative_in_its_parameters(fn):
    params = list(inspect.signature(fn).parameters)
    assert not [p for p in params if MOTHER.search(p)], params


def test_derive_and_replace_take_her_sister_s_id():
    assert list(inspect.signature(col.derive).parameters)[:2] == ["root", "sister_id"]
    assert list(inspect.signature(col.replace).parameters)[:2] == ["root", "sister_id"]


def _actions(parser: argparse.ArgumentParser):
    for action in parser._actions:
        yield action
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from _actions(sub)


def test_sister_of_names_a_sister_in_the_cli():
    found = [a for a in _actions(louise.build_parser()) if "--sister-of" in a.option_strings]
    assert found, "`hold collection new --sister-of` exists"
    for a in found:
        assert not MOTHER.search(a.metavar or ""), a.metavar
        assert not MOTHER.search(a.help or ""), a.help
    assert "--sister-of MOTHER" not in inspect.getsource(louise)
