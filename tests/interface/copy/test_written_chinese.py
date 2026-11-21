"""The interface's Chinese is written, not spoken (tools/copy_check.py): no string of the Chinese catalog and no
Chinese label in the modules that hold interface text carries a spoken form. Whether every catalog has every key
is guarded in tests/parts/catalogs/test_locales.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[3] / "tools" / "copy_check.py"


@pytest.fixture(scope="module")
def copy_check():
    spec = importlib.util.spec_from_file_location("copy_check", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spoken(check, text: str) -> list[str]:
    return [m.group(0) for rule in check.RULES for m in [rule.search(text)] if m]


def test_no_interface_string_in_the_chinese_catalog_is_spoken(copy_check):
    tree = json.loads(copy_check.CATALOG.read_text(encoding="utf-8"))
    spoken = {key: _spoken(copy_check, text) for key, text in copy_check.catalog(tree)
              if key.split(".")[0] not in copy_check.NOT_PAGES and not key.startswith(copy_check.VOICE_KEYS)}
    assert {key: found for key, found in spoken.items() if found} == {}


def test_no_chinese_label_in_the_interface_modules_is_spoken(copy_check):
    spoken = {f"{rel}:{number}": _spoken(copy_check, literal) for f, rel in copy_check.files()
              for number, literal in copy_check.strings(f.read_text(encoding="utf-8", errors="replace"))}
    assert {where: found for where, found in spoken.items() if found} == {}


@pytest.mark.parametrize("label", ["操作前先问你", "查看做了什么", "请稍等一下"])
def test_the_spoken_forms_the_author_named_are_caught(copy_check, label):
    assert _spoken(copy_check, label)
