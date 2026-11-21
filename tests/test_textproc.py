"""The text module stands on its own: nothing in it reads an agent's state.

Run from the repo root:

    python -m pytest tests/test_textproc.py
"""
from __future__ import annotations

from mibo.textproc import TextProcessing as TP

from helpers import check


def test_nothing_in_this_module_can_read_agent_state() -> None:
    """The reason this module is importable without an `Agent` at all.

    `TextProcessing` used to be mixed into `Agent`, where `self` reached
    every attribute `Agent.__init__` builds — the same coupling that still
    ties `learning.py` (36 attributes) and `transport.py` (31) to the
    constructor. It reads none, so it was taken off the MRO and its callers
    name it instead. Inheritance is what made the old coupling invisible;
    without this check, re-adding the base class and one `self.reply_style`
    would pass every other test in this file while quietly undoing the
    split."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(TP))
    classdef = tree.body[0]
    assert isinstance(classdef, ast.ClassDef)

    plain = [f.name for f in classdef.body
             if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
             and not any(isinstance(d, ast.Name) and d.id == "staticmethod"
                         for d in f.decorator_list)]
    check("every helper is a staticmethod — none takes an instance",
          not plain, ", ".join(plain))

    reads = sorted({
        f"{node.value.id}.{node.attr}"
        for node in ast.walk(classdef)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ("self", "cls")
    })
    check("no helper reaches for agent state through self/cls",
          not reads, ", ".join(reads))
