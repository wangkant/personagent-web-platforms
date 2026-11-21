"""act on a page place (surface.py, panels/browser.py Operating, web/operate.Hands): steps in a tab out of sight
leave the tab in front as it was and answer with what changed and what went out; a send is put to the person
before it goes; an upload gives a page only a file they named or a tool found; going to another site is free
unless the address carries words read on a page; a click waits for a control as Playwright's actionability does;
a link to a part of the same page leaves later steps their refs; the answer carries the page as the steps left it;
and work asked for in the browser comes with the page in front. The pages are a stand-in site in headless Chromium
behind the stand-in pane (harness/pane.py); the model and the reviewer are scripted, and refs come from a look of
the same page before the turn."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from harness.model import call, say, text
from harness.pane import Pane

from mibo import catalog, installed_browser
from mibo.web import pageops
from mibo.web.pageops import Page
from mibo.web.tabs import PaneTab

FRONT = """<!doctype html><meta charset="utf-8"><title>Tide tables</title><h1>Tides</h1><p>High water 06:12.</p>"""
# A signed-in board: a tab whose press posts device data to another host, a reply box and its button, a file field,
# and a code shown on the page that no address may carry away.
BOARD = """<!doctype html><meta charset="utf-8"><title>Board</title>
<nav><button id="mine">Mine</button></nav>
<main id="out">Signed in</main>
<p>Your invite code: ZETA7741QUARTZ</p>
<textarea id="r1" aria-label="Reply to Jay"></textarea> <button id="send1">Reply</button>
<input type="file" id="f1" aria-label="Attachment">
<script>
mine.onclick = () => { fetch("http://localhost:PORT/api/monitor", {method: "POST", mode: "no-cors",
  headers: {"Content-Type": "text/plain"}, body: '{"gpu": "ANGLE"}'}); out.textContent = "Followers 128"; };
send1.onclick = () => fetch("/api/reply", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({to: "Jay", text: r1.value})});
</script>"""
# A link to a part of the same page and a button under it: the address changes, the document stays.
PART = """<!doctype html><meta charset="utf-8"><title>Part</title>
<a id="m" href="#more">More</a> <button id="b">Go</button><p id="said"></p>
<script>b.onclick = () => { said.textContent = "pressed"; };</script>"""
# A button that slides in and is enabled only after a moment, as a page that is still drawing.
LATE = """<!doctype html><meta charset="utf-8"><title>Late</title>
<button id="go" disabled style="position:relative;left:300px;transition:left .4s linear">Go</button><p id="said"></p>
<script>
setTimeout(() => { go.style.left = "0px"; }, 50);
setTimeout(() => { go.disabled = false; }, 600);
go.onclick = () => { said.textContent = "pressed"; };
</script>"""
REPLY = "See you Sunday"
BROWSER = installed_browser.find_browser()


class Site(BaseHTTPRequestHandler):
    posts: list = []

    def do_GET(self):
        page = (BOARD if self.path.startswith("/board") else LATE if self.path.startswith("/late") else
                PART if self.path.startswith("/part") else FRONT)
        self._send(200, "text/html; charset=utf-8", page.replace("PORT", str(self.server.server_address[1])))

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        Site.posts.append((self.path, body))
        self._send(204, "text/plain", "")

    def do_OPTIONS(self):
        self._send(204, "text/plain", "")

    def _send(self, status: int, kind: str, words: str) -> None:
        raw = words.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def posted(path: str) -> list[str]:
    return [body for where, body in Site.posts if where == path]


@pytest.fixture
def pages():
    try:
        import playwright  # noqa: F401
    except ImportError as missing:
        pytest.skip(f"no Playwright: {missing}")
    Site.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pane = Pane.start(BROWSER)
    if pane is None:
        server.shutdown()
        pytest.skip("no headless browser")
    port = server.server_address[1]
    pane.open_tab(f"http://127.0.0.1:{port}/front")
    yield SimpleNamespace(pane=pane, url=f"http://127.0.0.1:{port}", port=port)
    pane.stop()
    server.shutdown()


@pytest.fixture
def board(app, pages):
    """The board opened out of sight in tab 2 behind the front page, the app window a stand-in whose page answers
    cards, and the refs a look of the board gives."""
    shown = []
    app.app.state.window = SimpleNamespace(pane=pages.pane, show=lambda: shown.append("show"), attended=lambda: True,
                                           frame_key="k", is_own_page=lambda key: key == "k")
    tab = pages.pane.open_tab(f"{pages.url}/board", front=False)
    page = Page(PaneTab(pages.pane, tab))
    asyncio.run(page.state(6000))
    refs = {name: ident for ident, (role, name) in page.last.items() if name}
    return SimpleNamespace(**vars(pages), tab=tab, refs=refs, shown=shown)


def opened(board) -> list:
    """The turn's first round: the board, opened by its address, out of sight."""
    return [call("open", name=f"{board.url}/board")]


def result(done, n: int) -> str:
    return done.result_of(done.calls()[n]["id"])


ASK = {"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True, "decision": "ask",
       "does": "comment", "reason": "stand-in"}


@pytest.mark.browser
@pytest.mark.slow
def test_steps_out_of_sight_leave_the_front_tab_and_answer_with_what_changed_and_what_went_out(app, board, turn):
    done = turn("Open my board and press Mine", opened(board),
                call("act", place="p1", steps=[{"do": "click", "ref": board.refs["Mine"], "what": "open Mine"}]),
                say("It shows 128 followers."))
    acted = result(done, 1)
    assert board.pane.front_of == 1 and board.shown == [] and done.cards == []
    assert "out of sight" in acted and "Noticed:" in acted
    # The page as the press left it comes with the answer, so the next act needs no look first: the reply box the
    # press did not touch is there with its ref.
    assert "Followers 128" in acted and f'[{board.refs["Reply to Jay"]}]' in acted
    # The page's own post to another host went, unjudged, and the act says so with its answer.
    assert "POST localhost/api/monitor 204" in acted and len(posted("/api/monitor")) == 1
    assert [step["tool"] for step in done.trace][-1] == "operate"


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.parametrize("answer", ["yes", "no"])
def test_a_send_is_put_to_the_person_before_it_goes(app, board, turn, answer):
    app.model.script(text(json.dumps(ASK)), purpose="reviewer")
    done = turn(f"Reply to Jay on my board: {REPLY}", opened(board),
                call("act", place="p1", steps=[
                    {"do": "type", "ref": board.refs["Reply to Jay"], "text": REPLY, "what": "write the reply"},
                    {"do": "click", "ref": board.refs["Reply"], "what": "send the reply to Jay"}]),
                say("Done." if answer == "yes" else "Not sent."), cards=answer)
    acted = result(done, 1)
    assert len(done.cards) == 1 and done.cards[0]["card"]["what"] == "send the reply to Jay"
    if answer == "yes":
        assert [json.loads(body)["text"] for body in posted("/api/reply")] == [REPLY]
        assert "POST 127.0.0.1/api/reply 204" in acted
    else:
        assert posted("/api/reply") == [] and acted.startswith("Error: not done (declined)")


@pytest.mark.browser
@pytest.mark.slow
def test_an_upload_gives_the_page_only_a_file_they_named(app, board, turn, tmp_path):
    app.model.script(text(json.dumps({**ASK, "does": "upload"})), purpose="reviewer")
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 stand-in")
    step = {"do": "upload_file", "ref": board.refs["Attachment"], "file": str(paper), "what": "attach the paper"}
    unnamed = turn("Attach my paper to the board", opened(board), call("act", place="p1", steps=[step]),
                   say("Which file?"))
    assert "neither named by them nor found by a tool" in result(unnamed, 1) and unnamed.cards == []
    # A file carried on any other step is held the same.
    clicked = turn("Attach my paper to the board", call("act", place="p1", steps=[{**step, "do": "click"}]),
                   say("Which file?"))
    assert "neither named by them nor found by a tool" in result(clicked, 0) and clicked.cards == []
    named = turn(f"Attach {paper} to the board", call("act", place="p1", steps=[step]), say("Attached."))
    assert [c["card"]["kind"] for c in named.cards] == ["upload"]
    assert "[ok] gave paper.pdf" in result(named, 0)


@pytest.mark.browser
@pytest.mark.slow
def test_going_to_another_site_is_free_unless_the_address_carries_words_read_on_a_page(app, board, turn):
    other = f"http://localhost:{board.port}"
    done = turn("Look at my board, then the front page of the other site", opened(board),
                call("act", place="p1", steps=[{"do": "go", "url": f"{other}/front?ref=ZETA7741QUARTZ",
                                                "what": "go on"}]),
                call("act", place="p1", steps=[{"do": "go", "url": f"{other}/front", "what": "open the other site"}]),
                say("It shows the tides."))
    assert "[refused]" in result(done, 1) and "carries words read on a page" in result(done, 1)
    assert "Tides" not in result(done, 1) and done.cards == []
    assert result(done, 2).startswith("Place p1") and "the address is now http://localhost" in result(done, 2)


def test_the_browser_errand_is_gone_and_act_is_offered_for_pages():
    names = [t["function"]["name"] for t in catalog.definitions()]
    assert "act" in names and "use_browser" not in names
    assert catalog.module_of("act").switch == "" and "act" not in catalog.READS


@pytest.mark.browser
@pytest.mark.slow
def test_a_click_waits_for_a_control_still_sliding_in_and_not_yet_enabled(pages):
    tab = pages.pane.open_tab(f"{pages.url}/late")
    page = Page(PaneTab(pages.pane, tab))

    async def press() -> tuple[str, str]:
        await page.state(6000)
        ident = next(i for i, (role, name) in page.last.items() if name == "Go")
        line = await page.click(ident)
        return line, await page.tab.evaluate("() => said.textContent")
    line, said = asyncio.run(press())
    assert line.startswith("[ok]") and said == "pressed"
    assert pageops.ACTION_S >= 1


@pytest.mark.browser
@pytest.mark.slow
def test_a_link_to_a_part_of_the_same_page_leaves_the_steps_after_it_their_refs(pages, tmp_path):
    from mibo.web import gate, operate
    tab = pages.pane.open_tab(f"{pages.url}/part")

    async def ask(card: dict) -> dict:
        return {"yes": False, "never": False}

    async def steps() -> tuple[dict, str]:
        hands = operate.Hands(PaneTab(pages.pane, tab), ask=ask, allowed=gate.Allowed(tmp_path / "allow"),
                              attended=lambda: True)
        await hands.look()
        refs = {name: ident for ident, (role, name) in hands.page.last.items() if name}
        got = await hands.steps([{"do": "click", "id": refs["More"]}, {"do": "click", "id": refs["Go"]}])
        said = await hands.tab.evaluate("() => said.textContent")
        await hands.close()
        return got, said
    got, said = asyncio.run(steps())
    assert got["status"] == "ok" and said == "pressed" and got["url"].endswith("#more")


@pytest.mark.browser
@pytest.mark.slow
def test_work_asked_for_in_the_browser_comes_with_the_page_in_front(app, pages):
    app.app.state.window = SimpleNamespace(pane=pages.pane, show=lambda: None, attended=lambda: True,
                                           frame_key="k", is_own_page=lambda key: key == "k")
    app.model.script(say("High water is at 06:12."))
    asked = len(app.model.asked())
    app.post("/api/chat", {"text": "When is high water?", "chat": "main", "choices": ["browser"]}, timeout=120)
    app.model.check()
    first = app.model.asked()[asked]["body"]["messages"]
    # The page's words reach the first round as a look the app made, so the first act needs no look of its own.
    assert any(m.get("role") == "tool" and "High water 06:12" in str(m.get("content")) for m in first)


def test_a_left_card_is_answered_by_a_plain_no_typed_beside_the_page(app):
    from mibo.panels.browser import Operating
    pane = SimpleNamespace(op=None, op_kept=lambda yes: None)
    run = Operating(SimpleNamespace(pane=pane, attended=lambda: True), app.home / "runtime" / "desktop", None)
    run.left = {"id": "c1", "kind": "comment", "act": ("card", "comment"), "said": "回复阿杰"}  # copy: voice
    app.app.state.window, app.app.state.operating = SimpleNamespace(pane=pane), run
    app.model.script(text(json.dumps({"card": "no"})))
    sent = app.post("/api/chat", {"text": "不要发", "chat": "main", "choices": ["page"]}, timeout=120)  # copy: voice
    app.model.check()
    assert "回复阿杰" in sent.json()["reply"] and run.kept == {("card", "comment"): {"yes": False}}  # copy: voice

