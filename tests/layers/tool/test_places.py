"""open and look (surface.py, resolve.py, places.py): a name the person said comes to the place it means, a page
opened out of sight leaves the tab in front as it was, a look reads a page or a window in one snapshot grammar
(refs, one part by ref, the controls only, words found, what was left out and why, what the app noticed), and the
places stay listed in the chat's turns while they are open. Pages are a stand-in site in headless Chromium behind a
stand-in pane (harness/pane.py); a program's window is a stand-in of the desk's answers, so this runs on any
system. The model is scripted."""
from __future__ import annotations

import asyncio
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from harness.model import call, say
from harness.pane import Pane

from mibo import catalog, installed_browser, resolve, turnon
from mibo.web import operate
from mibo.web.pageops import FIND_MAX, Page, view_of
from mibo.web.tabs import PaneTab

FRONT = """<!doctype html><meta charset="utf-8"><title>Tide tables</title><h1>Tides</h1><p>High water 06:12.</p>"""
# A list the page's script draws after it loads, a sign-in form and a drawn chart: what a look must read and say.
STOCK = """<!doctype html><meta charset="utf-8"><title>Stock list</title>
<h1>Stock</h1>
<ul id="rows" aria-label="items"></ul>
<form><input aria-label="Card name"><input type="password" aria-label="Card PIN"><button>Save</button></form>
<canvas width="900" height="500"></canvas>
<script>
setTimeout(() => {
  for (let n = 1; n <= 30; n++) {
    const li = document.createElement("li");
    li.innerHTML = `<button>Item ${n}</button> ${n % 3 ? "in stock" : "sold out"}`;
    rows.appendChild(li);
  }
}, 150);
</script>"""


# Found before an app of a test points the browser setting elsewhere.
BROWSER = installed_browser.find_browser()


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = (STOCK if self.path.startswith("/stock") else FRONT).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def pages():
    """The stand-in site, and the stand-in pane with its front page open."""
    try:
        import playwright  # noqa: F401
    except ImportError as missing:
        pytest.skip(f"no Playwright: {missing}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    pane = Pane.start(BROWSER)
    if pane is None:
        server.shutdown()
        pytest.skip("no headless browser")
    pane.open_tab(f"{url}/front")
    yield SimpleNamespace(pane=pane, url=url)
    pane.stop()
    server.shutdown()


@pytest.fixture
def in_app(app, pages):
    app.app.state.window = SimpleNamespace(pane=pages.pane, show=lambda: None, attended=lambda: True)
    return pages


def _look(pane, tab: int, **part) -> dict:
    page = Page(PaneTab(pane, tab))
    return asyncio.run(operate.look_at(page, 6000, **part)), page


# ---------------------------------------------------------------- open

def test_a_page_opened_out_of_sight_leaves_the_front_tab_and_comes_back_with_its_drawn_list(app, in_app, turn):
    stock = f"{in_app.url}/stock"
    done = turn("Check the stock list", call("open", name=stock), say("Items 3, 6 and 9 are sold out."))
    result = done.result_of(done.calls()[0]["id"])
    assert in_app.pane.placed == [(stock, False)] and in_app.pane.front_of == 1
    assert re.search(r"out of sight as place p\d+", result)
    # The script-drawn list, its buttons with refs; nothing was asked of the person to open a page.
    assert re.search(r"\[\d+\] button \"Item 30\"", result) and done.cards == []


def test_open_with_show_brings_a_named_tab_to_the_front_without_a_card(app, in_app, turn):
    in_app.pane.open_tab(f"{in_app.url}/stock", front=False)
    done = turn("Show me the stock list", call("open", name="Stock list", show=True), say("It is in front."))
    assert in_app.pane.front_of == 2 and in_app.pane.placed == [] and done.cards == []
    assert "they see it now" in done.result_of(done.calls()[0]["id"])


def test_a_name_that_ties_comes_back_as_candidates_to_pick_from(tmp_path):
    (tmp_path / "report 2025.docx").write_text("a")
    (tmp_path / "report 2026.docx").write_text("b")
    found = resolve.pick("report", str(tmp_path))
    assert found.kind == "several" and sorted(found.candidates) == ["report 2025.docx", "report 2026.docx"]


def test_a_name_comes_to_a_tab_a_file_a_shortcut_a_known_site_or_nothing(tmp_path):
    tabs = [{"id": 4, "title": "Inbox (3) - Mail", "url": "https://mail.example.com/"}]
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "grok bot.lnk").write_bytes(b"")
    started = resolve.Program(app="grok bot")
    assert resolve.pick("Inbox (3) - Mail", tabs=tabs).target == 4
    assert resolve.pick("notes", str(tmp_path)).target == str(tmp_path / "notes.txt")
    # A shortcut on the Desktop opens as Start and the Desktop list it, by its name.
    assert resolve.pick("grok bot", "desktop", folders={"desktop": str(tmp_path)},
                        program=lambda name: started if name == "grok bot" else resolve.Program()).kind == "program"
    assert resolve.pick("b站").target == resolve.pick("B站首页").target == "https://www.bilibili.com/"
    assert resolve.pick("https://example.org/a", tabs=tabs).target == "https://example.org/a"
    assert resolve.pick("zzqx unheard of").kind == "none"


def test_open_file_is_gone_and_open_and_look_are_offered_without_a_switch():
    names = [t["function"]["name"] for t in catalog.definitions()]
    assert "open_file" not in names and {"open", "look"} <= set(names)
    assert catalog.module_of("open").switch == "" and catalog.module_of("look").switch == ""


# ---------------------------------------------------------------- look

def test_look_with_no_place_reads_the_page_in_front_and_the_places_stay_listed_next_turn(app, in_app, turn):
    first = turn("What does this page say?", call("look"), say("High water at 06:12."))
    result = first.result_of(first.calls()[0]["id"])
    ident = re.match(r"Place (p\d+) · page", result).group(1)
    assert "High water 06:12." in result
    second = turn("And now?", say("Still the tide table."))
    sent = " ".join(str(m.get("content")) for m in second.asked()[0]["messages"])
    assert "<open_places>" in sent and f"{ident} · page · Tide tables" in sent


def test_look_reads_one_part_by_ref_only_the_controls_or_the_lines_with_words(pages):
    tab = pages.pane.open_tab(f"{pages.url}/stock")
    _, page = _look(pages.pane, tab)
    ref = next(i for i, (role, name) in page.last.items() if role == "button" and name == "Item 2")
    one = asyncio.run(operate.look_at(page, 6000, ref=ref))
    assert one["state"].strip().startswith(f"[{ref}] button \"Item 2\"") and "Item 3" not in one["state"]
    controls = asyncio.run(operate.look_at(page, 6000, interactive=True))
    assert all(re.match(r"\*?\[\d+\]", line) for line in controls["state"].splitlines())
    found = asyncio.run(operate.look_at(page, 6000, find="Item 3"))
    # Items 3, 13, 23 and 30, each with its ref.
    assert [re.sub(r"\[\d+\] button ", "", line) for line in found["state"].splitlines()] == [
        '"Item 3"', '"Item 13"', '"Item 23"', '"Item 30"']


def test_a_look_says_what_it_left_out_and_what_the_app_noticed(pages):
    tab = pages.pane.open_tab(f"{pages.url}/stock")
    found, _ = _look(pages.pane, tab)
    assert "masked (private)" in found["omitted"]
    assert "a password field" in found["noticed"] and "drawn part" in found["noticed"]


def test_a_long_print_goes_on_from_the_line_the_omitted_line_names():
    lines = [f"[{n}] link \"row {n}\"" for n in range(1, 101)]
    first, left = view_of(lines, {}, start=1, most=300)
    assert "report_limit" in left
    after = int(re.search(r"look from (\d+)", left).group(1))
    rest, _ = view_of(lines, {}, start=after, most=300)
    assert rest.splitlines()[0] == lines[after - 1] and first.splitlines()[-1] == lines[after - 2]
    many, more = view_of(lines, {}, find="row")
    assert len(many.splitlines()) == FIND_MAX and "80 more lines" in more


@pytest.mark.skipif(sys.platform != "win32", reason="the app operates other programs on Windows only")
def test_a_program_window_is_read_in_the_same_grammar_after_the_programs_switch(app, monkeypatch, turn):
    from mibo.desk import panel
    asked = []

    async def desk(self, op, payload):
        asked.append((op, payload.get("show")))
        if op == "place_open":
            return {"status": "ok", "hwnd": 77, "program": "Notepad", "title": "todo.txt - Notepad"}
        return {"status": "ok", "state": "[state] todo.txt - Notepad\n[3] document \"Text editor\" = \"milk\"\n[/state]",
                "snapshot": 1, "omitted": "", "noticed": ""}
    monkeypatch.setattr(panel.Desk, "place", desk)
    monkeypatch.setattr(resolve, "program", lambda name: resolve.Program(app="Notepad"))
    monkeypatch.setattr(turnon, "off", lambda home: [])
    done = turn("What is in my todo in Notepad?", call("open", name="Notepad"), say("Milk."))
    result = done.result_of(done.calls()[0]["id"])
    assert asked == [("place_open", False), ("place_look", None)]
    assert "not brought to the front" in result and '[3] document "Text editor" = "milk"' in result
    assert [step["tool"] for step in done.trace] == ["desk", "desk"]
