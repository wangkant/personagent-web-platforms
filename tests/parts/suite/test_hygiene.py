"""The suite keeps its own shape: files in the folder of what they guard, named after behaviour, no
dead definitions, no reaching into the Agent's private methods, and every listed floor test present."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]
FOLDERS = ("layers", "floors", "parts", "interface", "window", "harness")

# Files from before the layout, each moved by its own job. A job that moves one strikes it here;
# the list only shrinks, and nothing new joins it.
WAITING = (
    "helpers.py",
    "mcp_standin.py",
    "roam_standin.py",
    "test_engine.py",
    "test_hybrid_retrieval.py",
    "test_life_endpoints.py",
    "test_llm_calls.py",
    "test_mcp_tools.py",
    "test_memory_store.py",
    "test_page_chat_media.py",
    "test_panel_tools.py",
    "test_textproc.py",
    "test_updates.py",
    "test_welcome.py",
)

# A name is about behaviour, not about when or why it was written: no round, fix, stage or date
# (20261120, 1120) among its words.
FORBIDDEN = re.compile(r"round\d*|fix|fixes|fixed|retired|legacy|old|s\d{1,2}|\d{8}|(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
PRIVATE = {"_call_llm", "_chat_dm", "_reply_with_tools", "_http"}
SLOW_CAP = 30


def _files() -> list[Path]:
    return sorted(path for path in SUITE.rglob("*.py") if "__pycache__" not in path.parts)


def _laid_out() -> list[Path]:
    """The suite's files under the layout: everything but the files still waiting to move."""
    return [path for path in _files() if path.relative_to(SUITE).as_posix() not in WAITING]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_by_pytest(node: ast.AST) -> bool:
    """A test, a hook or an autouse fixture: pytest calls it without anything naming it."""
    return node.name.startswith(("test_", "Test", "pytest_")) or any(
        "autouse=True" in ast.unparse(decorator) for decorator in node.decorator_list)


def test_every_file_sits_in_the_folder_of_what_it_guards() -> None:
    stray = [path.relative_to(SUITE).as_posix() for path in _laid_out()
             if path.name != "conftest.py" and path.relative_to(SUITE).parts[0] not in FOLDERS]
    assert stray == [], f"put these under {', '.join(FOLDERS)}/"


def test_the_files_waiting_to_move_only_shrink() -> None:
    gone = [name for name in WAITING if not (SUITE / name).is_file()]
    assert gone == [], "strike these from WAITING: they have moved or gone"


def test_names_say_the_behaviour_and_not_when_it_was_written() -> None:
    named = []
    for path in _laid_out():
        names = [path.stem] + [node.name for node in ast.walk(_tree(path))
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        named += [f"{path.relative_to(SUITE).as_posix()}: {name}" for name in names
                  if any(FORBIDDEN.fullmatch(word.lower()) for word in name.strip("_").split("_"))]
    assert named == [], "name these after the behaviour they guard"


def test_every_function_and_class_the_suite_defines_is_used() -> None:
    used: dict[str, int] = {}
    for path in _files():
        for node in ast.walk(_tree(path)):
            name = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                    else node.arg if isinstance(node, ast.arg) else node.value
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
            for word in [name] if name else ():
                used[word] = used.get(word, 0) + 1
    unused = []
    for path in _laid_out():
        for node in _tree(path).body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and not _called_by_pytest(node) and not used.get(node.name)):
                unused.append(f"{path.relative_to(SUITE).as_posix()}: {node.name}")
    assert unused == [], "delete these or use them"


def test_no_test_reaches_into_the_agents_private_methods_or_the_group_bot_helpers() -> None:
    reaching = []
    for path in _laid_out():
        where = path.relative_to(SUITE).as_posix()
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                reaching += [f"{where}: imports {module}" for module in modules
                             if module in ("helpers", "tests.helpers")]
            elif isinstance(node, ast.Call) and ast.unparse(node.func).endswith("setattr"):
                strings = [arg.value for arg in node.args
                           if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                reaching += [f"{where}: replaces {text}" for text in strings
                             if text.rsplit(".", 1)[-1] in PRIVATE]
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                reaching += [f"{where}: replaces {target.attr}" for target in targets
                             if isinstance(target, ast.Attribute) and target.attr in PRIVATE]
    assert reaching == [], "enter through the page's API or the model's wire (harness), not the Agent's insides"


def test_every_floor_test_the_manifest_names_exists() -> None:
    manifest = json.loads((SUITE / "floors" / "manifest.json").read_text(encoding="utf-8"))
    missing = []
    for floor, nodes in manifest["floors"].items():
        for node in nodes:
            file, *names = node.split("[")[0].split("::")
            path = SUITE.parent / file
            scope = _tree(path).body if path.is_file() else []
            for name in names:
                found = [part for part in scope if getattr(part, "name", None) == name]
                scope = found[0].body if found and isinstance(found[0], ast.ClassDef) else found
            if not scope:
                missing.append(f"{floor}: {node}")
    assert missing == [], "a floor test moved or went: change floors/manifest.json in the same change"


def test_few_tests_are_marked_slow() -> None:
    slow = 0
    for path in _files():
        tree = _tree(path)
        whole = any(isinstance(node, ast.Assign) and "pytestmark" in ast.unparse(node.targets[0])
                    and "slow" in ast.unparse(node.value) for node in tree.body)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                slow += whole or any("mark.slow" in ast.unparse(d) for d in node.decorator_list)
    assert slow <= SLOW_CAP, f"{slow} tests are marked slow; make some quicker before marking more"
