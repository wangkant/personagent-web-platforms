"""The page's file routes serve only what they hold: a shipped portrait, a cover the finds library stored,
a picture a chat message shows. No spelling of a path, no link and no folder that leads elsewhere reaches
another file of the home (its settings least of all), and a picture is never written through a planted link."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from mibo import settingsfile
from mibo import setup_wizard as sw
from mibo import welcome
from mibo.library import Item, Library

TOKEN = "test-local-capability"
HEADERS = {welcome.TOKEN_HEADER: TOKEN}
SECRET = "sk-served-files-secret-0123456789"
# The smallest PNG: one transparent pixel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
# Ways of naming a file outside the route's folder, as a URL path segment.
TRAVERSALS = ("..%2F..%2Fsettings.json", "..%5C..%5Csettings.json", "%2Fetc%2Fpasswd", "C%3A%5CWindows%5Cwin.ini",
              "settings.json", "%2E%2E%2Fsettings.json", "..", "%2E%2E")


@pytest.fixture
def workspace(tmp_path):
    value = welcome.Workspace(tmp_path / "home")
    # Words only the home's settings file holds.
    settingsfile.write(value.home, {"LLM_API_KEY": "sk-served-files-key", "ADMIN_NAME": SECRET})
    yield value
    value.close()


@pytest.fixture
def client(workspace):
    # No lifespan: the routes read files, and no character needs to start.
    return TestClient(welcome.create_app(workspace.home, TOKEN, workspace=workspace), base_url="http://127.0.0.1")


def _refused(answer) -> bool:
    # A bare ".." is folded by the client into another route, which refuses a GET.
    return answer.status_code in (404, 405) and SECRET.encode() not in answer.content


def _picture_in_chat(client, workspace, monkeypatch, data: bytes = PNG) -> Path | None:
    """The character sends `data` as a picture in the chat; where the page kept it, None when no message shows it."""
    async def reply(conversation, parts, *, choices=(), run=None):
        return [{"type": "image", "b64": base64.b64encode(data).decode()}]

    monkeypatch.setattr(workspace.character, "reply", reply)
    shown = client.post("/api/chat", headers=HEADERS, json={"text": "a picture, please"}).json()["messages"]
    name = next((message["picture"] for message in shown if message.get("picture")), None)
    return next(workspace.home.rglob(name)) if name else None


def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let the test make a symbolic link (Windows without developer mode)")


def test_portraits_are_served_for_characters_and_nothing_else(client, tmp_path, monkeypatch):
    portraits = tmp_path / "portraits"
    portraits.mkdir()
    for key in ("chatgpt", "claude"):
        (portraits / f"{key}.webp").write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
    (portraits / "stray.webp").write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
    monkeypatch.setattr(sw, "PORTRAITS", portraits)
    assert client.get("/assets/characters/claude.webp").status_code == 200
    for name in ("deepseek.webp", "template.webp", "stray.webp", "claude", "claude.png", "claude.webp.bak",
                 "..%2Fwelcome%2Fapp.js", "..%2F..%2Fwelcome.py", "%2E%2E.webp", *TRAVERSALS):
        assert _refused(client.get(f"/assets/characters/{name}")), name


def test_the_cover_comes_from_the_library_only(client, workspace):
    library = Library(workspace.home / "runtime")
    told = library.add(Item(url="https://example.test/watch", site="Example", kind="video", title="Slow train",
                            fits=True, seen_at=1_800_000_000))
    plain = library.add(Item(url="https://example.test/post", site="Example", kind="article", title="Kettles",
                             fits=True, seen_at=1_800_000_000))
    told.picture = library.save_picture(told.id, b"\xff\xd8\xff" + b"x" * 50)
    library.add(told)
    cover = client.get(f"/api/library/picture/{told.id}", headers=HEADERS)
    assert cover.status_code == 200 and cover.content.startswith(b"\xff\xd8\xff")
    for name in (plain.id, "0" * 16, *TRAVERSALS):
        assert _refused(client.get(f"/api/library/picture/{name}", headers=HEADERS)), name
    # A stored item whose picture names a file outside the library's pictures: the home's settings, or a
    # picture of the person's elsewhere on the disk.
    (workspace.home / "elsewhere.jpg").write_bytes(b"\xff\xd8\xff" + SECRET.encode())
    for outside in ("../../../settings.json", "../../../elsewhere.jpg"):
        plain.picture = outside
        library.add(plain)
        assert _refused(client.get(f"/api/library/picture/{plain.id}", headers=HEADERS)), outside


def test_the_picture_route_reads_nothing_but_stored_pictures(client, workspace, monkeypatch):
    kept = _picture_in_chat(client, workspace, monkeypatch)
    assert client.get(f"/api/chat/picture/{kept.name}", headers=HEADERS).content == PNG
    for name in (*TRAVERSALS, kept.name.upper(), kept.name + ".png", kept.stem + ".txt"):
        assert _refused(client.get(f"/api/chat/picture/{name}", headers=HEADERS)), name


def test_a_picture_no_message_shows_is_not_served(client, workspace, monkeypatch):
    kept = _picture_in_chat(client, workspace, monkeypatch)
    # A file of the right shape that no message shows, placed beside the kept one.
    planted = kept.with_name("c" * 40 + ".png")
    planted.write_bytes(PNG)
    assert _refused(client.get(f"/api/chat/picture/{planted.name}", headers=HEADERS))


def test_a_linked_picture_is_refused(client, workspace, tmp_path, monkeypatch):
    kept = _picture_in_chat(client, workspace, monkeypatch)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    kept.unlink()
    _link(outside, kept)
    assert _refused(client.get(f"/api/chat/picture/{kept.name}", headers=HEADERS))


def test_a_pictures_folder_that_leads_elsewhere_is_refused(client, workspace, tmp_path, monkeypatch):
    kept = _picture_in_chat(client, workspace, monkeypatch)
    folder = kept.parent
    elsewhere = tmp_path / "elsewhere"
    folder.rename(elsewhere)
    if os.name == "nt":
        # A junction needs no privilege on Windows, so this case runs there too.
        import _winapi
        _winapi.CreateJunction(str(elsewhere), str(folder))
    else:
        _link(elsewhere, folder)
    assert _refused(client.get(f"/api/chat/picture/{kept.name}", headers=HEADERS))
    # Nothing new is stored there, and clearing the chat removes nothing from there.
    assert _picture_in_chat(client, workspace, monkeypatch, data=PNG + b"\0") is None
    assert client.post("/api/chat/reset", headers=HEADERS, json={}).status_code == 200
    assert sorted(path.name for path in elsewhere.iterdir()) == [kept.name]


def test_a_link_in_the_pictures_folder_is_never_written_through(client, workspace, tmp_path, monkeypatch):
    folder = _picture_in_chat(client, workspace, monkeypatch).parent
    # The name the next picture will take, planted as a link to a file outside.
    outside = tmp_path / "outside.png"
    other = PNG + b"\0"
    _link(outside, folder / f"{hashlib.sha256(other).hexdigest()[:40]}.png")
    assert _picture_in_chat(client, workspace, monkeypatch, data=other) is None
    assert not outside.exists()
