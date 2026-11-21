"""Settings › device: start at sign-in, and backing up, restoring and resetting the home. The token on its
routes and the restores it refuses are floors in tests/floors/boundary/."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import zipfile

import pytest
from dotenv import dotenv_values

from harness.app import TOKEN
from mibo import browsed, chatlog, db, i18n, shortcut, taste
from mibo import setup_wizard as sw
from mibo import welcome
from mibo.library import Item, Library
from mibo.panels import desk, feed

HEADERS = {welcome.TOKEN_HEADER: TOKEN}
KEY = "sk-desk-secret-0123456789"
SAID = "said only in the chat record"
CONV = "private:browser:desk"
MCP_KEY = "ghp-desk-secret-0123456789"


@pytest.fixture(autouse=True)
def no_fit(monkeypatch):
    """A reaction seeded here refits no taste model in the background, so the home holds only what a test wrote."""
    monkeypatch.setattr(taste, "fit", lambda runtime, soon=False: None)


@pytest.fixture
def places(tmp_path, monkeypatch):
    """The person's Desktop and Documents, and the folders a link could go in, all under tmp."""
    desktop, documents = tmp_path / "Desktop", tmp_path / "Documents"
    desktop.mkdir()
    documents.mkdir()
    monkeypatch.setenv(desk.DIRS_ENV, os.pathsep.join([str(desktop), str(documents)]))
    links = [tmp_path / "links" / k for k in shortcut.SPECIAL]
    monkeypatch.setenv(shortcut.DIRS_ENV, os.pathsep.join(map(str, links)))
    return tmp_path


@pytest.fixture
def restarts(client):
    """What welcome._start gives the page: a new start, and this one ending, each noted."""
    asked: list[str] = []
    client.app.state.relaunch = lambda: asked.append("relaunch")
    client.app.state.shutdown = lambda: asked.append("shutdown")
    return asked


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(desk, "open_path", seen.append)
    return seen


def seed(home):
    sw.write_env(home / ".env", {"LLM_API_KEY": KEY, "LLM_MODEL": "m", "PERSONA_NAME": "Ann",
                                 "MIBO_MCP_GITHUB_TOKEN": MCP_KEY})
    (home / "persona.txt").write_text("You are Ann.\n", encoding="utf-8")
    (home / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (home / "skills" / "notes").mkdir(parents=True)
    (home / "skills" / "notes" / "SKILL.md").write_text("notes", encoding="utf-8")
    runtime = home / "runtime"
    (runtime / "desktop" / "webview").mkdir(parents=True, exist_ok=True)
    (runtime / "desktop" / "webview" / "Cookies").write_text("site sign-in", encoding="utf-8")
    (runtime / "desktop" / "app.log").write_text("log line\n", encoding="utf-8")
    (runtime / "desktop" / "welcome.json").write_text('{"url": "x"}', encoding="utf-8")
    # The page's copy of a chat and the chat record, where the app keeps them (runtime/mibo.db).
    _page_chat(runtime, "tea at four")
    chatlog.ChatLog(runtime).take(CONV, [{"seq": 1, "t": time.time(), "user_id": "browser:desk", "text": SAID}])
    _find(runtime, "tide tables")
    (runtime / "desktop" / "app.json").write_text('{"toured": true}', encoding="utf-8")
    (runtime / "memory.json").write_text('{"a": "likes tea"}', encoding="utf-8")
    (runtime / "dashboard.token").write_text("dash-token", encoding="utf-8")
    (home / "data").mkdir()
    (home / "data" / "examples.en.jsonl").write_text("", encoding="utf-8")


def _page_chat(runtime, text: str) -> None:
    chat = welcome.PageChat(db.at(runtime))
    chat.append([{"role": "user", "text": text}])
    chat.save()


def _find(runtime, title: str) -> None:
    """A find with a reaction to it, and a page read in the app's browser (runtime/mibo.db)."""
    item = Library(runtime).add(Item(url=f"https://shore.example/{title.replace(' ', '-')}", title=title,
                                     seen_at=int(time.time())))
    feed.remember(runtime, item, "open", time.time())
    browsed.record(runtime / "desktop", {"url": item.url, "title": title, "s": 5})


def _finds(path) -> list[tuple]:
    """The finds a database file holds: each one's title, its reactions and its visits."""
    conn = sqlite3.connect(path)
    try:
        return [(json.loads(item)["title"], conn.execute("SELECT count(*) FROM feed_events WHERE id = ?", (i,)).fetchone()[0],
                 conn.execute("SELECT count(*) FROM browsed WHERE url = ?", (json.loads(item)["url"],)).fetchone()[0])
                for i, item in conn.execute("SELECT id, item FROM library ORDER BY seen_at, id")]
    finally:
        conn.close()


def _page_texts(path) -> list[str]:
    """The page's messages a database file holds."""
    conn = sqlite3.connect(path)
    try:
        return [json.loads(body)["text"] for (body,) in conn.execute("SELECT body FROM page_messages ORDER BY seq")]
    finally:
        conn.close()


def post(client, path, payload=None):
    return client.post(f"/api/desk{path}", headers=HEADERS, json=payload or {})


def test_data_names_the_places_and_no_secret(client, workspace, places):
    seed(workspace.home)
    answer = client.get("/api/desk/data", headers=HEADERS)
    assert answer.status_code == 200
    value = answer.json()
    assert {t["id"]: t["path"] for t in value["targets"]} == {
        "desktop": str(places / "Desktop"), "documents": str(places / "Documents"),
        "backups": str(workspace.home / "backups")}
    assert value["backups"] == [] and value["restored"] is None
    assert KEY not in answer.text


def test_open_shows_only_the_places_a_backup_goes(client, opened, places, workspace):
    assert post(client, "/open", {"place": "backups"}).json() == {"ok": True}
    assert opened == [workspace.home / "backups"] and (workspace.home / "backups").is_dir()
    assert post(client, "/open", {"place": "D:/anywhere"}).json() == {"error": "desk_invalid_place"}
    assert opened == [workspace.home / "backups"]


# -- start at sign-in --------------------------------------------------------

def stand_in_windows(monkeypatch, found):
    """Windows' answer about the links, and every script it was asked to run."""
    ran = []

    def powershell(source):
        ran.append(source)
        return (json.dumps(found) if "ConvertTo-Json" in source else ""), ""
    monkeypatch.setattr(shortcut, "WINDOWS", True)
    monkeypatch.setattr(shortcut, "_powershell", powershell)
    return ran


def test_start_at_sign_in_tells_this_copy_from_another(client, workspace, places, monkeypatch):
    program, _arguments = shortcut.target(workspace.home)
    stand_in_windows(monkeypatch, [{"kind": "startup", "target": str(program), "arguments": "-m other"}])
    assert client.get("/api/desk/shortcuts", headers=HEADERS).json() == {"login_supported": True, "login": "other"}
    stand_in_windows(monkeypatch, [])
    assert client.get("/api/desk/shortcuts", headers=HEADERS).json()["login"] == "off"


def test_status_asks_only_the_redirected_folders(workspace, places, monkeypatch):
    ran = stand_in_windows(monkeypatch, [])
    shortcut.status(workspace.home)
    assert "GetFolderPath" not in ran[-1] and str(places / "links" / "startup") in ran[-1]
    monkeypatch.setenv(shortcut.DIRS_ENV, "only-one")
    with pytest.raises(ValueError):
        shortcut.status(workspace.home)


def test_turning_it_on_writes_only_the_startup_link_for_this_home(client, workspace, places, monkeypatch):
    ran = stand_in_windows(monkeypatch, [])
    answer = post(client, "/shortcut", {"which": "login", "on": True})
    assert answer.status_code == 200 and answer.json()["login_supported"] is True
    made = [s for s in ran if "ConvertTo-Json" not in s]
    assert made and all("Desktop" not in s for s in made)
    assert str(places / "links" / "startup") in made[-1] and f'--home "{workspace.home}"' in made[-1]


def test_start_at_sign_in_refusals(client, places, monkeypatch):
    stand_in_windows(monkeypatch, [])
    monkeypatch.setattr(shortcut, "_powershell", lambda source: ("", "access denied"))
    assert post(client, "/shortcut", {"on": True}).json() == {"error": "desk_shortcut_failed"}
    assert client.get("/api/desk/shortcuts", headers=HEADERS).json() == {"error": "desk_shortcut_failed"}
    monkeypatch.setattr(shortcut, "WINDOWS", False)
    monkeypatch.setattr(desk.autostart, "MACOS", False)
    assert client.get("/api/desk/shortcuts", headers=HEADERS).json()["login_supported"] is False
    assert post(client, "/shortcut", {"on": True}).json() == {"error": "desk_unsupported"}


def test_a_startup_link_made_here_reads_as_on(workspace, places, monkeypatch):
    program, arguments = shortcut.target(workspace.home)
    assert f"{arguments} --background" in shortcut.script(workspace.home, kinds=shortcut.LOGIN)
    stand_in_windows(monkeypatch, [{"kind": "startup", "target": str(program), "arguments": arguments + " --background"}])
    assert shortcut.status(workspace.home)["startup"] == "on"


# -- backing up ---------------------------------------------------------------

def test_a_backup_holds_the_home_but_no_keys_logs_sign_ins_or_chat_record(client, workspace, places):
    seed(workspace.home)
    answer = post(client, "/backup", {"to": "documents"})
    assert answer.status_code == 200
    value = answer.json()
    path = places / "Documents" / value["name"]
    assert value["path"] == str(path) and value["size"] == path.stat().st_size > 0
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    assert {desk.MANIFEST, ".env", "persona.txt", "mcp.json", "skills/notes/SKILL.md", "runtime/memory.json",
            "runtime/mibo.db", "runtime/desktop/app.json", "data/examples.en.jsonl"} <= names
    for left in ("runtime/desktop/app.log", "runtime/desktop/welcome.json",
                 "runtime/desktop/webview/Cookies", "runtime/mibo.db-wal", "runtime/mibo.db-shm"):
        assert left not in names, left
    # The database whole, through SQLite's backup API: the page's chats, and not one byte of the chat record.
    copy = places / "copy.db"
    copy.write_bytes(archive.read("runtime/mibo.db"))
    assert _page_texts(copy) == ["tea at four"] and SAID.encode() not in copy.read_bytes()
    assert _finds(copy) == [("tide tables", 1, 1)], "the finds, what was said of them and the pages read"
    env = archive.read(".env").decode("utf-8")
    assert KEY not in env and MCP_KEY not in env and "LLM_MODEL=m" in env and "LLM_API_KEY=" in env
    assert json.loads(archive.read(desk.MANIFEST))["secrets"] is False
    assert [b["name"] for b in value["backups"]] == [value["name"]]
    assert value["backups"][0]["place"] == "documents"
    assert post(client, "/backup", {"to": "D:/anywhere"}).json() == {"error": "desk_invalid_place"}


def test_keys_go_into_a_backup_only_when_asked(client, workspace, places):
    seed(workspace.home)
    name = post(client, "/backup", {"to": "desktop", "secrets": True}).json()["name"]
    archive = zipfile.ZipFile(places / "Desktop" / name)
    assert KEY in archive.read(".env").decode("utf-8") and MCP_KEY in archive.read(".env").decode("utf-8")
    assert json.loads(archive.read(desk.MANIFEST))["secrets"] is True
    copy = places / "copy.db"
    copy.write_bytes(archive.read("runtime/mibo.db"))
    assert _finds(copy) == [("tide tables", 1, 1)]


def test_backup_reports_a_folder_it_cannot_write(client, workspace, places, monkeypatch):
    blocker = places / "file-not-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(desk.DIRS_ENV, os.pathsep.join([str(blocker / "inside"), str(places)]))
    assert post(client, "/backup", {"to": "desktop"}).json() == {"error": "desk_backup_failed"}
    monkeypatch.setenv(desk.DIRS_ENV, "")
    monkeypatch.setattr(desk, "_known_folder", lambda kind: None)
    assert post(client, "/backup", {"to": "desktop"}).json() == {"error": "desk_missing"}


# -- restoring ------------------------------------------------------------------

def test_a_restore_is_done_by_the_next_start_and_keeps_the_keys_here(client, workspace, places, restarts):
    seed(workspace.home)
    home = workspace.home
    name = post(client, "/backup", {"to": "desktop"}).json()["name"]
    (home / "runtime" / "memory.json").write_text('{"b": "later"}', encoding="utf-8")
    _page_chat(home / "runtime", "said after the backup")
    _find(home / "runtime", "found after the backup")
    (home / "skills" / "later").mkdir()
    (home / "skills" / "later" / "SKILL.md").write_text("later", encoding="utf-8")
    answer = post(client, "/restore", {"place": "desktop", "name": name})
    assert answer.json() == {"restarting": True}
    assert restarts == ["relaunch", "shutdown"]
    assert json.loads((home / "runtime" / "desktop" / "pending.json").read_text(encoding="utf-8")) == {
        "restore": str(places / "Desktop" / name)}
    desk.apply_pending(home)
    assert (home / "runtime" / "memory.json").read_text(encoding="utf-8") == '{"a": "likes tea"}'
    assert not (home / "skills" / "later").exists()
    values = dotenv_values(home / ".env")
    assert values["LLM_API_KEY"] == KEY and values["MIBO_MCP_GITHUB_TOKEN"] == MCP_KEY
    assert (home / "runtime" / "desktop" / "webview" / "Cookies").exists()
    # The page's chats as the backup has them; the chat record, never in a backup, as it was.
    assert _page_texts(home / "runtime" / "mibo.db") == ["tea at four"]
    assert _finds(home / "runtime" / "mibo.db") == [("tide tables", 1, 1)]
    assert [r["text"] for r in chatlog.ChatLog(home / "runtime").read(CONV)] == [SAID]
    assert not (home / "runtime" / "desktop" / "pending.json").exists()
    # The home as it was just before is kept in a backup of its own, and it is the present, not the backup.
    safety = list((home / "backups").glob("mibo-backup-*-before-restore.zip"))
    assert len(safety) == 1
    assert json.loads(zipfile.ZipFile(safety[0]).read("runtime/memory.json")) == {"b": "later"}
    told = client.get("/api/desk/data", headers=HEADERS).json()
    assert told["restored"] == {"error": ""}
    assert client.get("/api/desk/data", headers=HEADERS).json()["restored"] is None


def test_a_restore_that_fails_midway_puts_the_present_back(client, workspace, places, restarts, monkeypatch):
    seed(workspace.home)
    home = workspace.home
    name = post(client, "/backup", {"to": "backups"}).json()["name"]
    (home / "runtime" / "memory.json").write_text('{"b": "now"}', encoding="utf-8")
    real, failed = desk.shutil.copyfileobj, []

    def flaky(source, out, *args):
        # Only the copy out of a backup: zipfile uses the same function to write one.
        if not failed and isinstance(out, io.BufferedWriter):
            failed.append(True)
            raise OSError("disk full")
        return real(source, out, *args)
    monkeypatch.setattr(desk.shutil, "copyfileobj", flaky)
    post(client, "/restore", {"place": "backups", "name": name})
    desk.apply_pending(home)
    assert failed, "the restore met the failure"
    assert (home / "runtime" / "memory.json").read_text(encoding="utf-8") == '{"b": "now"}'
    assert (home / "persona.txt").exists() and (home / "skills" / "notes" / "SKILL.md").exists()
    assert client.get("/api/desk/data", headers=HEADERS).json()["restored"] == {"error": "desk_restore_undone"}


# -- resetting --------------------------------------------------------------------

def test_a_reset_backs_up_first_when_asked_then_leaves_only_the_backups(client, workspace, places, restarts):
    seed(workspace.home)
    home = workspace.home
    (home / "backups").mkdir()
    (home / "backups" / "mibo-backup-kept.zip").write_bytes(b"kept")
    answer = post(client, "/reset", {"backupTo": "desktop", "secrets": True})
    value = answer.json()
    assert value["restarting"] is True and value["path"].startswith(str(places / "Desktop"))
    assert KEY in zipfile.ZipFile(value["path"]).read(".env").decode("utf-8")
    assert restarts == ["relaunch", "shutdown"]
    assert (home / ".env").exists(), "nothing is deleted before the next start"
    desk.apply_pending(home)
    assert (home / "backups" / "mibo-backup-kept.zip").exists() and (home / "runtime" / "desktop" / "app.log").exists()
    for gone in (".env", "persona.txt", "mcp.json", "skills", "data", "runtime/memory.json", "runtime/mibo.db",
                 "runtime/desktop/webview", "runtime/desktop/app.json", "runtime/desktop/pending.json"):
        assert not (home / gone).exists(), gone


def test_a_reset_without_a_backup_writes_none(client, workspace, places, restarts):
    seed(workspace.home)
    assert post(client, "/reset", {}).json() == {"restarting": True, "path": ""}
    assert list((places / "Desktop").iterdir()) == [] and list((places / "Documents").iterdir()) == []


def test_a_start_with_nothing_asked_changes_nothing(workspace):
    seed(workspace.home)
    before = sorted(p.relative_to(workspace.home).as_posix() for p in workspace.home.rglob("*"))
    desk.apply_pending(workspace.home)
    assert sorted(p.relative_to(workspace.home).as_posix() for p in workspace.home.rglob("*")) == before


@pytest.mark.parametrize("lang", sorted(path.stem for path in i18n.LOCALES.glob("*.json")))
def test_every_error_the_panel_gives_is_one_the_page_can_say(lang):
    assert all(code.startswith("desk_") for code in desk.ERRORS)
    said = i18n.catalog(lang)["setup_device"]["errors"]
    assert [code for code in (*desk.ERRORS, "desk_restore_undone", "desk_restore_failed") if code not in said] == []
