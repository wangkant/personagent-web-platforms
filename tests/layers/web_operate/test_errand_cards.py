"""What an errand in the app's browser puts to the person, and when (web/operate.py, panels/browser.py,
review.py): the act is judged, never the page's own traffic; one act is asked about once and its answer kept for
the errand; a card nobody answered in time leaves the errand waiting with the card still answerable; a press on
the chat laid over the page is no press on the page; and the reviewer's answer is read as a weak model gives it.

The site is a stand-in on 127.0.0.1 in the headless browser: a signed-in creator page whose 我的 tab draws the
numbers, and which posts device data to another host (localhost) on load and on every click, as short-video sites
do. No model runs; the card's answers and the reviewer are scripted."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from mibo import review
from mibo.browser import on_page
from mibo.panels.browser import Operating, act_of
from mibo.web import gate, operate

PAGE = """<!doctype html><meta charset="utf-8"><title>创作者中心</title>
<nav><span class="tab" id="mine" style="cursor:pointer">我的</span> <span class="tab" style="cursor:pointer">推荐</span></nav>
<main id="out">小乐 · 已登录</main>
<section id="fan" hidden><p>阿杰：一直在看你的视频，加油！</p>
<textarea id="r1" aria-label="回复阿杰"></textarea> <button id="send1">回复</button>
<span id="pin" style="cursor:pointer">置顶</span></section>
<script>
const beat = () => fetch("http://localhost:PORT/api/monitor", {method: "POST", mode: "no-cors",
  headers: {"Content-Type": "text/plain"}, body: JSON.stringify({gpu: "ANGLE", t: Math.round(performance.now())})});
document.addEventListener("click", beat, true);
beat();
mine.onclick = () => { out.textContent = "粉丝 12.8万 · 获赞 356.2万 · 近7日播放 48.3万"; fan.hidden = false; };
send1.onclick = () => fetch("/api/reply", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({to: "阿杰", text: r1.value})}).then(() => { r1.value = ""; });
pin.onclick = () => fetch("/api/pin", {method: "POST", body: '{"post": 7}'}).then(() => { pin.textContent = "已置顶"; });
</script>"""
REPLY = "谢谢支持，下周更新"  # copy: voice


class Site(BaseHTTPRequestHandler):
    """The creator page, and every POST it receives by path."""
    posts: list = []

    def do_GET(self):
        self._send(200, "text/html; charset=utf-8", PAGE.replace("PORT", str(self.server.server_address[1])))

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        Site.posts.append((self.path, body))
        self._send(204, "text/plain", "")

    def do_OPTIONS(self):
        self._send(204, "text/plain", "")

    def _send(self, status: int, kind: str, text: str) -> None:
        raw = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def posted(path: str) -> list[str]:
    return [body for where, body in Site.posts if where == path]


class Errand:
    """Hands on the creator page with scripted answers: each card the person is shown and each review asked."""

    def __init__(self, page, answers=(), kept=None, tmp=None):
        self.cards, self.reviews, self.answers = [], [], list(answers)

        async def ask(card):
            self.cards.append(card)
            return self.answers.pop(0)

        async def judge(action):
            self.reviews.append(action)
            return review.Verdict("ask", "stand-in", "review")
        from mibo.web.tabs import PlaywrightTab
        self.hands = operate.Hands(PlaywrightTab(page), ask=ask, allowed=gate.Allowed(tmp), attended=lambda: True,
                                   review=judge, kept=kept)


@pytest.fixture
def creator_page(tmp_path):
    """A page of the stand-in site in the headless browser, and the event loop it runs on."""
    from mibo import installed_browser
    try:
        from playwright.async_api import async_playwright
    except ImportError as missing:
        pytest.skip(f"no Playwright: {missing}")
    Site.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    loop = asyncio.new_event_loop()
    pw = loop.run_until_complete(async_playwright().start())
    found = installed_browser.find_browser()
    try:
        browser = loop.run_until_complete(pw.chromium.launch(executable_path=str(found) if found else None,
                                                             headless=True))
    except Exception as missing:
        loop.run_until_complete(pw.stop())
        server.shutdown()
        pytest.skip(f"no headless browser: {missing}")
    page = loop.run_until_complete(browser.new_page(viewport={"width": 900, "height": 700}))
    loop.run_until_complete(page.goto(f"http://127.0.0.1:{server.server_address[1]}/"))
    yield SimpleNamespace(page=page, run=loop.run_until_complete, tmp=tmp_path)
    loop.run_until_complete(browser.close())
    loop.run_until_complete(pw.stop())
    loop.close()
    server.shutdown()
    server.server_close()


async def reply(hands) -> dict:
    """The reply typed under the fan's message, and its button pressed."""
    await hands.look()
    await hands.act({"do": "type", "role": "textbox", "name": "回复阿杰", "text": REPLY})
    return await hands.act({"do": "click", "role": "button", "name": "回复"})


@pytest.mark.browser
@pytest.mark.slow
def test_a_tab_pressed_on_a_signed_in_page_asks_nothing_while_the_page_posts_device_data(creator_page):
    errand = Errand(creator_page.page, tmp=creator_page.tmp)

    async def go():
        await errand.hands.look()
        done = await errand.hands.act({"do": "click", "text": "我的"})
        await asyncio.sleep(0.3)
        seen = await errand.hands.look()
        await errand.hands.close()
        return done, seen
    done, seen = creator_page.run(go())
    assert done["status"] == "ok" and "12.8万" in seen["state"]
    assert (errand.cards, errand.reviews) == ([], [])
    assert len(posted("/api/monitor")) >= 2


@pytest.mark.browser
@pytest.mark.slow
def test_a_reply_typed_and_pressed_is_one_question_and_goes_on_yes(creator_page):
    errand = Errand(creator_page.page, answers=[{"yes": True, "never": False}], tmp=creator_page.tmp)

    async def go():
        await errand.hands.look()
        await errand.hands.act({"do": "click", "text": "我的"})
        pressed = await reply(errand.hands)
        await asyncio.sleep(0.3)
        await errand.hands.close()
        return pressed
    pressed = creator_page.run(go())
    assert pressed["status"] == "ok"
    assert len(errand.cards) == 1 and errand.cards[0]["text"] == REPLY
    assert [json.loads(body)["text"] for body in posted("/api/reply")] == [REPLY]


@pytest.mark.browser
@pytest.mark.slow
def test_an_unanswered_card_leaves_the_errand_waiting_and_its_later_answer_is_kept_for_the_act(creator_page):
    kept: dict = {}
    first = Errand(creator_page.page, answers=[{"waiting": "回复阿杰"}], kept=kept, tmp=creator_page.tmp)

    async def waiting():
        await first.hands.look()
        await first.hands.act({"do": "click", "text": "我的"})
        pressed = await reply(first.hands)
        await first.hands.close()
        return pressed
    pressed = creator_page.run(waiting())
    assert (pressed["status"], pressed["result"]) == ("waiting", "回复阿杰")
    assert posted("/api/reply") == []
    # The person answers the card afterwards (Operating.keep), and the errand goes on with the same answers.
    kept[first.cards[0]["act"]] = {"yes": True}
    again = Errand(creator_page.page, kept=kept, tmp=creator_page.tmp)

    async def going_on():
        await again.hands.look()
        pressed = await again.hands.act({"do": "click", "role": "button", "name": "回复"})
        await asyncio.sleep(0.3)
        await again.hands.close()
        return pressed
    assert creator_page.run(going_on())["status"] == "ok"
    assert again.cards == [] and [json.loads(body)["text"] for body in posted("/api/reply")] == [REPLY]


@pytest.mark.browser
@pytest.mark.slow
def test_a_press_that_saves_to_the_site_itself_is_asked_about_before_it_goes(creator_page):
    errand = Errand(creator_page.page, answers=[{"yes": False, "never": False}], tmp=creator_page.tmp)

    async def go():
        await errand.hands.look()
        await errand.hands.act({"do": "click", "text": "我的"})
        await errand.hands.look()
        pressed = await errand.hands.act({"do": "click", "text": "置顶"})
        await errand.hands.close()
        return pressed
    pressed = creator_page.run(go())
    assert pressed["status"] == "declined" and len(errand.cards) == 1
    assert posted("/api/pin") == []


class Pane:
    """The pane's column as Operating tells it: the card shown, the answer told, the card left answered."""

    def __init__(self):
        self.op, self.shown, self.told, self.kept = {"held": ""}, [], [], []

    def op_card(self, card, text):
        self.shown.append(card)

    def op_answered(self, state, text):
        self.told.append(state)

    def op_kept(self, yes):
        self.kept.append(yes)


def test_a_card_whose_time_runs_out_stays_answerable_and_its_answer_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(operate, "CARD_S", 0.5)
    pane = Pane()
    run = Operating(SimpleNamespace(pane=pane, attended=lambda: True), tmp_path, None)
    card = {"id": "c1", "kind": "comment", "site": "127.0.0.1", "to": "阿杰", "text": REPLY, "button": "回复",
            "skippable": True, "act": ("card", "comment", "127.0.0.1", "回复", REPLY, "阿杰")}
    assert asyncio.run(run.ask(card)) == {"waiting": act_of(card, "zh")}
    assert len(pane.shown) == 1 and pane.told == []
    assert run.answer_card("c1", True, False, [])
    assert run.kept == {card["act"]: {"yes": True}} and pane.kept == [True]
    assert not run.answer_card("c1", True, False, [])


def test_a_press_on_the_chat_laid_over_the_page_is_not_a_press_on_the_page():
    # The chat column over the right of a pane 1000 CSS px wide, on a display at 200%.
    cuts = [[600, 0, 400, 800]]
    assert on_page(300, 300, 2000, 1600, cuts, 2.0)
    assert not on_page(1500, 300, 2000, 1600, cuts, 2.0)
    assert not on_page(2100, 300, 2000, 1600, cuts, 2.0)
    assert on_page(1500, 300, 2000, 1600, [], 2.0)


ACTION = {"area": "browser", "kind": "comment", "where": "127.0.0.1", "target": ["阿杰"], "text": REPLY,
          "page_says": "回复"}
PASS = {"kind_asked": True, "target_named": True, "text_given": True, "nothing_added": True, "decision": "go",
        "does": "comment", "reason": "stand-in"}


def reviewed(*answers: str) -> tuple[review.Verdict, int]:
    asked = []

    async def ask(system, messages):
        asked.append(messages)
        return answers[len(asked) - 1]
    said = [{"text": f"打开我的，回复阿杰的留言：{REPLY}", "at": time.time()}]  # copy: voice
    return asyncio.run(review.review(said, ACTION, ask=ask)), len(asked)


def test_the_reviewer_is_asked_once_more_when_its_answer_cannot_be_read():
    verdict, calls = reviewed("", json.dumps(PASS))
    assert (verdict.decision, verdict.by, calls) == ("go", "review", 2)
    verdict, calls = reviewed("无法判断", "仍然无法判断")  # copy: voice
    assert (verdict.decision, verdict.by, calls) == ("ask", "failed", 2)


def test_a_verdict_wrapped_in_words_is_read_and_a_go_without_every_check_is_an_ask():
    verdict, calls = reviewed(f"结论如下：{json.dumps(PASS)} 以上。")  # copy: voice
    assert (verdict.decision, calls) == ("go", 1)
    verdict, _ = reviewed(json.dumps({"decision": "Go", "reason": "stand-in"}))
    assert verdict.decision == "ask"
