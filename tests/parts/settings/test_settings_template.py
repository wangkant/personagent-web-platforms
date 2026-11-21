"""The template a person copies their .env from (.env.example) lists every setting the code reads, in every
folder of the package and the tools, and every setting a log line tells them to change exists."""
from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
READERS = {"env_int", "env_float", "env_bool", "env_str", "env_csv"}

# Variables the system or another program owns, read but never a Mibo setting.
NOT_SETTINGS = {
    "PATH", "TEMP", "USERPROFILE", "COMPUTERNAME",
    # The OpenAI SDK's own variable, read to know whose key OPENAI_API_KEY is (panels/detect.py).
    "OPENAI_BASE_URL",
    # A hosted runner's own scratch folder, where the sandbox suites keep their homes (tools/sandbox/_suite.py).
    "RUNNER_TEMP",
    # The freedesktop base folders, where Electron keeps its own folders by default (tools/xplat/electron_check.py).
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
}
# Switches the measuring scripts and benches set, kept out of the person's template until the author
# decides otherwise.
SWITCHES = {
    "SHELL_OPEN_DRY",  # tools/computer_tasks.py: report what would open, open nothing
    "USER_DESKTOP_DIR",  # the measuring scripts' stand-in Desktop
    "TOOL_CALLS",  # =written: written tool calls for an endpoint whose own are poor
    "RECSIM_TIRE", "RECSIM_CURIOUS", "RECSIM_BAIT",  # tools/recsim's simulated people
    "SANDBOX_MODEL_KEY", "SANDBOX_BUDGET",  # the sandbox workflow's real-model run and its spending cap
}


def _sources() -> list[Path]:
    return sorted(path for folder in ("mibo", "tools") for path in (ROOT / folder).rglob("*.py")
                  if "__pycache__" not in path.parts)


def _tree(path: Path) -> ast.Module:
    # A source's own escape warnings are its business, not this scan's.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _reads_the_environment(call: ast.Call) -> bool:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id in READERS
    if not isinstance(fn, ast.Attribute):
        return False
    if fn.attr in READERS:
        return True
    if fn.attr == "getenv":
        return isinstance(fn.value, ast.Name) and fn.value.id == "os"
    return (fn.attr == "get" and isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ"
            and isinstance(fn.value.value, ast.Name) and fn.value.value.id == "os")


def _settings_read() -> dict[str, str]:
    """Each upper-case name the code reads from the environment, with the first place it does."""
    read: dict[str, str] = {}
    for path in _sources():
        for node in ast.walk(_tree(path)):
            if (isinstance(node, ast.Call) and _reads_the_environment(node) and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    and node.args[0].value.isupper()):
                read.setdefault(node.args[0].value, f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    return read


def test_every_setting_the_code_reads_is_in_the_template() -> None:
    read = _settings_read()
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {line.split("=", 1)[0].strip() for line in template.splitlines()
                  if "=" in line and not line.lstrip().startswith("#")}
    assert len(read) > 30, "the scan found the settings at all"
    missing = sorted(f"{key} ({where})" for key, where in read.items()
                     if key not in documented | NOT_SETTINGS | SWITCHES)
    assert missing == [], "add these to .env.example"
    # The two lists only name what the code still reads.
    assert sorted((NOT_SETTINGS | SWITCHES) - set(read)) == []


def test_settings_named_in_log_messages_exist() -> None:
    """A log line that tells the person to change a setting names one the template knows."""
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    known = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", template, re.MULTILINE))
    name = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]\b")
    unknown = []
    for path in _sources():
        if path.parts[len(ROOT.parts)] != "mibo":
            continue
        for node in ast.walk(_tree(path)):
            if not (isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
                continue
            for part in ast.walk(node.args[0]):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    unknown += [f"{path.relative_to(ROOT).as_posix()}:{node.lineno} {setting}"
                                for setting in name.findall(part.value) if setting not in known]
    assert unknown == []
