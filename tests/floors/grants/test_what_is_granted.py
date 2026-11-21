"""Nothing reaches beyond what the person granted: a file tool outside the granted folders asks for the folder
first, a folder granted to read is never changed, the one acting mode is honoured (off does nothing until they
turn it on, read only changes nothing), the app's home and key files are never touched, a run that starts on a
site nobody named asks first, and the programs on the never list are never operated."""
from __future__ import annotations

import json
import os
import sys

import pytest

from harness.model import call, say
from mibo import acting
from mibo.shell import files, read as reading
from mibo.shell.grants import Grants
from mibo.web import operate

ASKED = "Write a short note called plan.txt there"  # copy: voice


@pytest.fixture
def places(app, tmp_path):
    """A folder granted to change in, one granted to read only, and one nobody granted."""
    made = {name: tmp_path / name for name in ("granted", "read_only", "elsewhere")}
    for place in made.values():
        place.mkdir()
    grants = Grants(app.home / "runtime" / "desktop", app.home)
    assert grants.add(str(made["granted"])) == "" and grants.add(str(made["read_only"]), read_only=True) == ""
    return made


@pytest.mark.parametrize("where", ["elsewhere", "read_only"])
def test_a_write_outside_what_was_granted_asks_for_the_folder_and_on_no_nothing_is_written(app, turn, places, where):
    target = places[where] / "plan.txt"
    done = turn(ASKED, call("write_file", path=str(target), content="buy milk"), say("I left it."), cards="no")
    assert [(c["kind"], c["card"]["type"], c["card"]["choices"]) for c in done.cards] == \
        [("shell", "folder", ["write"])]
    assert not target.exists()


@pytest.mark.parametrize("mode", ["off", "read"])
def test_the_acting_mode_holds_off_waits_for_the_person_and_read_only_changes_nothing(app, turn, places, mode):
    modes = acting.Modes(app.home / "runtime" / "desktop")
    modes.set(mode)
    target = places["granted"] / "plan.txt"
    done = turn(ASKED, call("write_file", path=str(target), content="buy milk"), say("I left it."), cards="no")
    # Off: only the card that would turn acting on, answered no; read only: no card at all.
    assert [c["card"].get("type") for c in done.cards] == (["turn_on"] if mode == "off" else [])
    assert not target.exists()
    assert json.loads(modes.path.read_text(encoding="utf-8")) == {"mode": mode}


# File tools on the app's home, a key file and a folder nobody granted: {f} the granted folder, {h} the home, {e}
# a folder not granted. (tool, its job as panel.py makes it, what the gate must do)
REACHES = [
    ("read", {"path": "{h}/.env"}, "refuse"),
    ("read", {"path": "{f}/../home/.env"}, "refuse"),
    ("read", {"path": "{f}/id_rsa"}, "refuse"),
    ("list", {"path": "{e}"}, "folder"),
    ("search", {"path": "{h}", "text": "SECRET"}, "refuse"),
    ("write", {"path": "{h}/x.txt"}, "refuse"),
    ("write", {"path": "{f}/../home/.env"}, "refuse"),
    ("write", {"path": "{e}/x.txt"}, "folder"),
    ("move", {"moves": [{"from": "{f}/b.txt", "to": "{e}/b.txt", "folder": ""}]}, "folder"),
    ("move", {"moves": [{"from": "{f}/b.txt", "to": "{h}/b.txt", "folder": ""}]}, "refuse"),
    ("delete", {"paths": ["{h}/.env"]}, "refuse"),
    ("delete", {"paths": ["{f}/server.pem"]}, "refuse"),
]


def filled(value, places: dict):
    if isinstance(value, str):
        return os.path.normpath(value.format(**places)) if "{" in value else value
    if isinstance(value, list):
        return [filled(v, places) for v in value]
    if isinstance(value, dict):
        return {k: filled(v, places) for k, v in value.items()}
    return value


@pytest.mark.parametrize("op, job, want", REACHES, ids=[f"{op}-{n}" for n, (op, _, _) in enumerate(REACHES)])
def test_no_file_tool_reaches_the_home_a_key_file_or_an_ungranted_folder(tmp_path, op, job, want):
    folder, home, elsewhere = tmp_path / "files", tmp_path / "home", tmp_path / "elsewhere"
    for place in (folder, home / "runtime" / "desktop", elsewhere):
        place.mkdir(parents=True)
    for name in ("b.txt", "id_rsa", "server.pem"):
        (folder / name).write_text("x", encoding="utf-8")
    (home / ".env").write_text("SECRET=1\n", encoding="utf-8")
    grants = Grants(home / "runtime" / "desktop", home)
    assert grants.add(str(folder)) == ""
    job = {"op": op, "recursive": False, "copy": False, "kind": "write",
           **filled(job, {"f": str(folder), "h": str(home), "e": str(elsewhere)})}
    job["folder"] = os.path.dirname(job.get("path", ""))
    verdict = reading.judge(reading.Reading(files.call(job), str(folder), files.acts(job, str(folder)), []), grants)
    assert verdict.action == want, [a.why for a in verdict.reasons]


def test_a_run_that_starts_on_a_site_nobody_named_asks_first():
    here = "https://news.example.org/a"
    started = {"another site, unnamed": operate.plan("https://collect.evil.test/?d=x", here, "看看这篇新闻"),  # copy: voice
               "another site, named": operate.plan("https://www.bilibili.com/", here, "打开 bilibili 搜猫"),  # copy: voice
               "the same site": operate.plan("https://news.example.org/b", here, "看看下一篇"),  # copy: voice
               "no start": operate.plan("", here, "看看这篇新闻")}  # copy: voice
    assert {name: plan["card"] for name, plan in started.items()} == {
        "another site, unnamed": True, "another site, named": False, "the same site": False, "no start": False}


@pytest.mark.skipif(sys.platform != "win32", reason="the app operates other programs on Windows only")
def test_the_programs_on_the_never_list_are_never_operated(monkeypatch):
    from mibo.desk import grants, win32
    # Stand-ins for what Windows says of a running program: not elevated, not this app.
    monkeypatch.setattr(win32, "elevated", lambda pid: False)
    monkeypatch.setattr(win32, "own_tree", lambda: set())

    def program(exe: str, name: str = "") -> dict:
        return {"exe": exe, "name": name or os.path.splitext(os.path.basename(exe))[0], "product": "", "pid": 4242}

    never = [program(r"C:\Windows\System32\cmd.exe"), program(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
             program(r"C:\Windows\regedit.exe"), program(r"C:\Program Files\KeePass\KeePass.exe", "KeePass Password Safe"),
             program(r"D:\apps\icbc\client.exe", "工商银行网上银行"), program(r"D:\Downloads\setup.exe"),
             program(r"C:\Program Files\WindowsApps\Claude\claude.exe"), program(r"D:\tools\wallet.exe", "Crypto Wallet")]
    assert [p["exe"] for p in never if not grants.never(p)] == []
    assert grants.never(program(r"C:\Windows\System32\notepad.exe", "Notepad")) == ""
