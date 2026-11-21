"""In automatic review a command that code cannot pass by itself goes to the reviewer before any card: a listing
that walks through links leading out of the granted folder runs on its pass with no card, the log row keeps the
reviewer's decision and reason, a card that comes says why and offers 本次对话内允许 and 始终允许 for the folders
outside (granted as Settings grants them), and a request that only fetches is the reviewer's while one that sends
is the person's each time; moves that would land on one name, one replacing another, do nothing. (2026-11-21: a
Desktop listing asked although the person had named what to find.)"""
from __future__ import annotations

import json
import sys

import pytest

from harness.model import call, say, text
from mibo.shell.grants import LOG_FILE, Grants

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="read.py reads commands with Windows PowerShell's "
                                                                "parser, and the links are junctions")
ASKED = "帮我打开桌面上的grok bot并给主agent发一个hello"  # copy: voice


def verdict(decision: str, reason: str) -> dict:
    checks = decision == "go"
    return text(json.dumps({"kind_asked": checks, "target_named": checks, "text_given": checks,
                            "nothing_added": checks, "decision": decision, "reason": reason}, ensure_ascii=False))


@pytest.fixture
def desktop(app, tmp_path):
    """A granted Desktop holding a 'grok bot' folder and a junction to a folder outside every grant."""
    import _winapi
    place, outside = tmp_path / "Desktop", tmp_path / "elsewhere" / "Game"
    (place / "grok bot").mkdir(parents=True)
    outside.mkdir(parents=True)
    (outside / "save.dat").write_text("x", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(place / "Game"))
    assert Grants(app.home / "runtime" / "desktop", app.home).add(str(place)) == ""
    return place


def logged(app) -> list[dict]:
    return [json.loads(line) for line in (app.home / "runtime" / "desktop" / LOG_FILE).read_text(
        encoding="utf-8").splitlines() if "command" in line]


def listing(place) -> dict:
    return call("use_computer", command=f"Get-ChildItem -Path {place} -Recurse -Depth 2 -Include *grok* "
                                        "| Select-Object FullName", folder=str(place))


def test_a_listing_through_links_out_of_the_granted_folder_runs_on_the_reviewers_pass_with_no_card(app, turn, desktop):
    app.model.script(verdict("go", "只读查找桌面上的 grok bot。"), purpose="reviewer")
    done = turn(ASKED, listing(desktop), say("找到了。"), cards="no")
    assert done.cards == []
    (asked,) = done.asked("reviewer")
    pending = json.loads(asked["messages"][-1]["content"])
    # Code tells the reviewer the command only reads, and the person's own words come with it.
    assert pending["only_reads"] is True
    assert any(ASKED in m["content"] for m in asked["messages"][1:-1])
    (row,) = logged(app)
    assert (row["outcome"], row["how"], row["review"]["decision"]) == ("ran", "review", "go")
    assert row["review"]["reason"] == "只读查找桌面上的 grok bot。"


def test_when_the_reviewer_asks_the_card_says_why_and_always_grants_the_folder_outside(app, turn, desktop):
    app.model.script(verdict("ask", "需要你确认是否读取桌面外的文件夹。"), purpose="reviewer")
    done = turn(ASKED, listing(desktop), say("找到了。"), cards="always")
    ((card,),) = [[c["card"] for c in done.cards]]
    assert card["choices"] == ["once", "chat", "always"]
    assert "需要你确认是否读取桌面外的文件夹。" in card["zh"]["review"]
    assert "Game" in card["zh"]["always"]
    shown = app.get("/api/shell").json()["folders"]
    assert [(f["zh"], f["read_only"]) for f in shown if f["zh"] == "Game"] == [("Game", True)]
    assert logged(app)[-1]["review"]["decision"] == "ask"
    # Granted, the same listing runs at once: no reviewer, no card.
    again = turn(ASKED, listing(desktop), say("找到了。"), cards="no")
    assert (again.cards, again.asked("reviewer")) == ([], [])
    assert logged(app)[-1]["outcome"] == "ran"


def test_a_fetch_that_only_reads_is_the_reviewers_and_one_that_sends_asks_every_time(app, turn, desktop):
    app.model.script(verdict("ask", "需要你确认是否访问这个网址。"), purpose="reviewer")
    read = turn("看看 example.com 能不能打开", call(  # copy: voice
        "use_computer", command="Invoke-WebRequest https://example.com -UseBasicParsing", folder=str(desktop)),
        say("没有打开。"), cards="no")
    assert len(read.asked("reviewer")) == 1
    assert [c["card"]["choices"] for c in read.cards] == [["once"]]
    sent = turn("把 notes 发到 example.com", call(  # copy: voice
        "use_computer", command="Invoke-RestMethod -Method Post -Body notes https://example.com/api",
        folder=str(desktop)), say("没有发送。"), cards="no")
    assert sent.asked("reviewer") == []
    assert [c["card"]["choices"] for c in sent.cards] == [["once"]]
    assert [row["outcome"] for row in logged(app)] == ["declined", "declined"]


def test_moves_that_land_on_one_name_replace_nothing(app, turn, desktop):
    # 2026-11-21, deepseek flash: three screenshots moved to "Pictures" in the Desktop, with no card, left one file
    # named Pictures; the other two were copied over it.
    shots = [desktop / f"屏幕截图 {n}.png" for n in (1, 2, 3)]
    for shot in shots:
        shot.write_bytes(shot.name.encode())
    moves = [{"from": str(shot), "to": str(desktop / "Pictures")} for shot in shots]
    done = turn("把桌面上的截图都移到图片文件夹", call("move_files", moves=moves), say("没有移动。"),  # copy: voice
                cards="yes")
    assert done.cards == [] and "land on" in done.result_of(done.calls()[0]["id"])
    assert all(shot.read_bytes() == shot.name.encode() for shot in shots)
    assert not (desktop / "Pictures").exists()
