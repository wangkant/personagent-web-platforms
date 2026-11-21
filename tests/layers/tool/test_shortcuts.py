"""open takes a shortcut the person keeps in their own folders (mibo/resolve.py, mibo/desk/names.py): by its name
without the extension in the folder they named, or by its full path, the shortcut found there is the one opened,
whether or not Start lists a program of that name. Their own folders are the known folders the file tools use,
which a bench points at stand-ins; a shortcut elsewhere still opens only by what Start and the Desktop list.
(2026-11-21: open('grok bot', near desktop) and the shortcut's full path both answered that nothing by that name
exists; the model reached it through the files tool after eleven calls.)"""
from __future__ import annotations

import sys

import pytest

from mibo import resolve


def unlisted(name: str) -> resolve.Program:
    return resolve.Program()


@pytest.fixture
def desktop(tmp_path):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    (folder / "grok bot.lnk").write_bytes(b"")
    (folder / "tiny site.url").write_text("[InternetShortcut]\nURL=http://127.0.0.1/\n")
    return folder


def test_a_shortcut_in_their_folder_opens_by_its_name_without_the_extension(desktop):
    folders = {"desktop": str(desktop)}
    found = resolve.pick("grok bot", "desktop", folders=folders, program=unlisted)
    assert (found.kind, found.target, found.title) == ("program", str(desktop / "grok bot.lnk"), "grok bot")
    assert resolve.pick("tiny site", "desktop", folders=folders, program=unlisted).target == str(desktop / "tiny site.url")


def test_a_shortcut_in_their_folder_opens_by_its_full_path(desktop):
    path = str(desktop / "grok bot.lnk")
    found = resolve.pick(path, folders={"desktop": str(desktop)}, program=unlisted)
    assert (found.kind, found.target) == ("program", path)


def test_a_shortcut_outside_their_folders_opens_only_by_what_start_lists(desktop, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "grok bot.lnk").write_bytes(b"")
    assert resolve.pick(str(elsewhere / "grok bot.lnk"), folders={"desktop": str(desktop)},
                        program=unlisted).kind == "none"


def test_the_launcher_takes_the_shortcut_resolve_found_by_its_path(desktop, tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("the app starts other programs on Windows only")
    from mibo.desk import names, win32
    monkeypatch.setattr(resolve, "profile_folders", lambda: {"desktop": str(desktop)})
    monkeypatch.setattr(win32, "link_reader", lambda: lambda path: r"C:\stand-in\wscript.exe")
    monkeypatch.setattr(names, "open_windows", lambda: [])
    found = names.find(str(desktop / "grok bot.lnk"))
    assert found.app is not None and found.app.file == str(desktop / "grok bot.lnk") and found.app.name == "grok bot"
    (tmp_path / "other.lnk").write_bytes(b"")
    assert names.own_link(str(tmp_path / "other.lnk")) is None
