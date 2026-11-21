"""Key files in a folder they granted (.env, id_rsa, *.pem) are never named or read to the model: not by
list_files, not by search_files, not in what a plain command prints. A binary file is still found by its name."""
from __future__ import annotations

import json
import sys

import pytest

from harness.folders import Profile
from harness.model import call, say
from mibo.shell import grants
from mibo.shell.run import HIDDEN, hide_secret_names

# What a key file's name or its contents may look like in what reaches the model.
NAMES = (".env", "id_rsa", "server.pem")
CONTENT = "sk-not-a-real-key"


@pytest.fixture
def granted(tmp_path, monkeypatch, app):
    """A profile under tmp whose Documents and Downloads they granted to read; Documents/project holds an id_rsa
    beside its .env."""
    profile = Profile(tmp_path / "profile", monkeypatch)
    (profile.root / "Documents" / "project" / "id_rsa").write_text(f"API_KEY={CONTENT}\n", encoding="utf-8")
    folders = [str(profile.root / "Documents"), str(profile.root / "Downloads")]
    (app.home / "runtime" / "desktop" / grants.SETTINGS_FILE).write_text(
        json.dumps({"folders": folders, "read_only": folders}), encoding="utf-8")
    return profile.root


def leaked(done) -> list[str]:
    """The key files' names or contents that reached the model, in any request of the turn."""
    sent = [json.dumps(body, ensure_ascii=False) for body in done.asked()]
    return [w for w in (*NAMES, CONTENT) if any(w in s for s in sent)]


def test_a_key_file_is_neither_listed_nor_searched_and_a_binary_is_found_by_name(turn, granted):
    documents, downloads = str(granted / "Documents"), str(granted / "Downloads")
    done = turn("看看文档文件夹里都有什么，再找找哪些文件写着 API_KEY，还有下载里的 setup.bin",  # copy: voice
                [call("list_files", path=documents, recursive=True),
                 call("search_files", text="API_KEY", path=documents),
                 call("search_files", text="setup.bin", path=downloads)],
                say("都在这里了。"))  # copy: voice
    listed, searched, by_name = (done.result_of(c["id"]) for c in done.calls())
    assert "README.md" in listed and "简历.docx" in listed
    assert "0 files hold it" in searched
    assert "setup.bin: (the name holds it)" in by_name
    assert leaked(done) == []


@pytest.mark.skipif(sys.platform != "win32", reason="commands run in Windows PowerShell only")
def test_a_plain_command_never_prints_a_key_files_name(turn, granted):
    done = turn("用 Get-ChildItem 把文档文件夹里的文件全列出来",  # copy: voice
                call("use_computer", command="Get-ChildItem -Force -Recurse -Name", folder=str(granted / "Documents")),
                say("列好了。"))  # copy: voice
    (listed,) = done.calls()
    printed = done.result_of(listed["id"])
    assert "README.md" in printed and HIDDEN in printed
    assert leaked(done) == []


# What a command prints about key files, as PowerShell, cmd, ls and find print it.
PRINTED = [
    "-a---          2026/11/19     20:08             26 .env",
    "Mode  LastWriteTime  Length Name\n----  -------------  ------ ----\n-a---  2026/11/19  26 id_rsa",
    r"C:\Users\me\Documents\certs\server.pem",
    "project\\.env.local",
    "-rw-------  1 me me  411 Nov 19 20:08 id_ed25519",
    "./.ssh/id_rsa\n./notes.txt",
    "/home/me/backup/wallet.dat",
    "11/19/2026  08:08 PM     2,483 vault.kdbx",
    "C:\\Users\\me\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Login Data",
]


@pytest.mark.parametrize("printed", PRINTED)
def test_a_key_files_name_in_what_a_command_prints_is_hidden(printed):
    shown = hide_secret_names(printed)
    assert HIDDEN in shown
    assert not any(name in shown for name in (".env", "id_rsa", "id_ed25519", ".pem", "wallet.dat", ".kdbx",
                                              "Login Data"))


@pytest.mark.parametrize("printed", ["notes.txt  README.md  简历.docx", "I like cookies", "the envelope icon",
                                     "C:\\Users\\me\\Documents\\environment.md"])
def test_ordinary_names_and_words_in_what_a_command_prints_are_kept(printed):
    assert hide_secret_names(printed) == printed
