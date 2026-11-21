"""What an installed copy needs and how a release is made: one version, the shipped files, the seed
resolver, the command, and a release that only a tested commit on main can make."""
from __future__ import annotations

import importlib
import re
import runpy
from pathlib import Path

import pytest
import yaml

import mibo
from mibo import cli, home, paths

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"


def _pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _list(name: str) -> list[str]:
    """The quoted entries of a one-line `name = [...]` list in pyproject.toml."""
    return re.findall(r'"([^"]+)"', re.search(rf"^{name} = \[(.*)\]$", _pyproject(), re.MULTILINE).group(1))


def _dependencies() -> list[str]:
    block = re.search(r"^dependencies = \[(.*?)^\]", _pyproject(), re.MULTILINE | re.DOTALL).group(1)
    return re.findall(r'^\s*"([^"]+)",', block, re.MULTILINE)


def test_one_version_for_the_package_and_the_metadata() -> None:
    declared = re.search(r'^version = "([^"]+)"', _pyproject(), re.MULTILINE).group(1)
    assert declared == mibo.__version__


def test_the_classifiers_match_the_release() -> None:
    text = _pyproject()
    # 0.x: not yet released, and said so.
    assert "Development Status :: 3 - Alpha" in text
    for minor in range(10, 15):
        assert f"Programming Language :: Python :: 3.{minor}" in text
    assert 'requires-python = ">=3.10"' in text


def test_the_dependencies_have_floors_and_stay_on_pydantic_2() -> None:
    requirements = _dependencies()
    assert any(re.fullmatch(r"pydantic>=2[.\d]*,<3", line) for line in requirements), requirements
    assert [line for line in requirements if ">=" not in line] == []


def test_the_window_toolkit_is_required_only_where_the_system_has_one() -> None:
    """Windows ships WebView2; macOS and Linux opt in with the `app` extra, so a server install never needs
    a GUI toolkit."""
    webview = [line for line in _dependencies() if line.startswith("pywebview")]
    assert len(webview) == 1 and webview[0].endswith("; sys_platform == 'win32'"), webview
    extra = _list("app")
    assert [spec.split(";")[1].strip() for spec in extra] == ["sys_platform != 'linux'", "sys_platform == 'linux'"]
    assert extra[1].startswith("pywebview[qt]>="), "pywebview alone brings Linux no toolkit"


def test_the_workflows_take_the_test_tools_from_the_dev_extra() -> None:
    """Their versions are written once, in pyproject.toml; the extra may gain tools."""
    tools = [re.match(r"[A-Za-z0-9_.-]+", spec).group(0) for spec in _list("dev")]
    assert {"pytest", "ruff", "playwright"} <= set(tools), tools
    # The `web` extra's playwright is the one the tests run with.
    assert [spec for spec in _list("dev") if spec.startswith("playwright")] == _list("web")
    for name in ("ci.yml", "release.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert 'python -m pip install -e ".[dev]"' in text, name
        for tool in tools:
            assert not re.search(rf'["\s]{re.escape(tool)}\s*[<>=~!]', text), f"{name} writes a version for {tool}"


def test_the_wheel_bundles_the_shared_files_under_bundled() -> None:
    """hatchling maps data/ under mibo/_bundled, where home.resource() looks in an installed copy; CI installs
    the built wheel and reads it back."""
    text = _pyproject()
    assert '"data" = "mibo/_bundled/data"' in text
    wheel = re.search(r"^\[tool\.hatch\.build\.targets\.wheel\]\n(.*?)\n\n", text, re.MULTILINE | re.DOTALL).group(1)
    for included in ("mibo", "data"):
        assert f'"{included}"' in wheel, included


def test_the_wheel_leaves_bytecode_caches_out() -> None:
    # hatchling skips what .gitignore ignores.
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "__pycache__/" in ignored and "*.pyc" in ignored


def test_resource_reads_the_checkout_here_and_the_bundle_when_installed(monkeypatch) -> None:
    assert home.is_checkout()
    assert home.resource("data", "x") == ROOT / "data" / "x"
    monkeypatch.setattr(home, "is_checkout", lambda path=home.CHECKOUT: False)
    assert home.resource("data", "x") == home.PACKAGE_DIR / "_bundled" / "data" / "x"


def test_a_folder_is_a_home_only_with_its_own_settings(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    assert not home.looks_like_home(tmp_path), "a stray data/ folder is not a home"
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "persona.txt").write_text("", encoding="utf-8")
    assert home.looks_like_home(tmp_path)
    assert home.looks_like_home(ROOT)


def test_seeds_prefer_the_home_then_fall_back_to_the_shipped_copy(monkeypatch, tmp_path: Path) -> None:
    """The files data/ ships today: the output filter by language, and the characters' folder."""
    shipped = tmp_path / "shipped"
    (shipped / "data" / "personas" / "en").mkdir(parents=True)
    (shipped / "data" / "output_filter.en.json").write_text("shipped", encoding="utf-8")
    (shipped / "data" / "output_filter.json").write_text("shipped", encoding="utf-8")
    (shipped / "data" / "personas" / "en" / "mibo.txt").write_text("", encoding="utf-8")
    own = tmp_path / "home"
    (own / "data").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", own)
    monkeypatch.setattr(paths, "resource", lambda *parts: shipped.joinpath(*parts))

    assert paths.seed_file("output_filter.en.json") == shipped / "data" / "output_filter.en.json"
    assert paths.seed_file("personas") == shipped / "data" / "personas"
    assert paths.resolve_seed_lang_file("output_filter", "json", "en") == shipped / "data" / "output_filter.en.json"
    # A language with no file of its own falls back to the unmarked one.
    assert paths.resolve_seed_lang_file("output_filter", "json", "zh") == shipped / "data" / "output_filter.json"

    (own / "data" / "output_filter.json").write_text("mine", encoding="utf-8")
    assert paths.resolve_seed_lang_file("output_filter", "json", "en") == own / "data" / "output_filter.json"
    (own / "data" / "output_filter.en.json").write_text("mine", encoding="utf-8")
    assert paths.seed_file("output_filter.en.json") == own / "data" / "output_filter.en.json"
    (own / "data" / "personas").mkdir()
    assert paths.seed_file("personas") == own / "data" / "personas"
    assert paths.resolve_seed_lang_file("missing", "json", "en") == own / "data" / "missing.json"


def test_the_cli_prints_its_version(capsys) -> None:
    with pytest.raises(SystemExit) as stop:
        cli.main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"mibo {mibo.__version__}"


def test_the_subcommands_this_package_serves_resolve() -> None:
    for name, (module, func, _help) in cli.COMMANDS.items():
        assert callable(getattr(importlib.import_module(module), func)), name


def test_release_notes_come_from_the_changelog_section_of_the_tag() -> None:
    notes = runpy.run_path(str(ROOT / "tools" / "release_notes.py"), run_name="notes_under_test")
    changelog = "\n".join([
        "# Changelog", "", "## [Unreleased]", "", "- next", "",
        "## [1.0.0] - 2026-10-10", "", "### Added", "", "- `mibo` command", "",
        "## [0.4.0] - 2026-09-24", "", "- earlier", "",
        "[1.0.0]: https://github.com/wangkant/mibo/releases/tag/v1.0.0", ""])
    assert notes["changelog_section"](changelog, "1.0.0") == "### Added\n\n- `mibo` command\n"
    with pytest.raises(ValueError, match="no '## .2.0.0.' section"):
        notes["changelog_section"](changelog, "2.0.0")
    assert notes["project_version"](_pyproject()) == mibo.__version__
    assert notes["main"](["v0.0.0-not-this"]) == 1


def test_a_release_is_made_only_from_a_tested_commit_on_main() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(text)["jobs"]
    test = "\n".join(step.get("run", "") for step in jobs["test"]["steps"])
    assert "origin main" in test
    assert test.index("merge-base --is-ancestor") < test.index("python -m pytest")

    def waits_for(name: str) -> set[str]:
        needs = jobs[name].get("needs") or []
        direct = {needs} if isinstance(needs, str) else set(needs)
        return direct.union(*(waits_for(job) for job in direct))

    # Every other job waits for the tests, and the release waits for every app.
    assert [name for name in jobs if name != "test" and "test" not in waits_for(name)] == []
    assert set(jobs) - {"github-release"} <= waits_for("github-release")
    # Nothing goes to PyPI.
    assert not re.search(r"pypi-publish|twine upload", text)
