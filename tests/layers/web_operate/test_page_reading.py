"""Reading a page for a question (web/pagetext.py, act's read step and the chat's page tools): what the model is
given, in how many calls, what is loaded and when, and what the reading says it read. The model is a scripted
stand-in that answers each reading call from a list and records what it was sent; the pages are fixed texts, and
the document's own reading (pageops) runs on fixed pages in the headless browser."""
from __future__ import annotations

import json
import re

import pytest

from mibo import context
from mibo.web import operate, pagetext

# A profile as a short-video site draws it: its numbers in the first screen, a feed of cards below.
PROFILE = "\n".join(["## 小乐的日常", "短视频号：xiaole_daily", "关注 129", "粉丝 4649", "获赞 87411", "作品 推荐 喜欢"]
                    + [f"周末在家做饭（第{n}期）\n11-0{n % 9 + 1} · ♡ {300 + n}\n分享" for n in range(40)])
# A report of 60 sections, the answer in the 50th.
REPORT = "\n".join(f"## 第{n}章\n" + "\n".join(f"西城分馆在第{n}季度延长了周末开放时间，涉及{100 + k}名读者。" for k in range(12))
                   + ("\n东城分馆早在2015年就实现了全天二十四小时自助借还。" if n == 50 else "") for n in range(1, 61))


class Model:
    """Answers each reading call with the next answer of its script, as JSON, and records what each was sent."""

    def __init__(self, *answers: dict):
        self.answers = list(answers)
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, system: str, messages: list[dict]) -> str:
        self.sent.append((system, messages[-1]["content"]))
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


class Feed:
    """A live page's `more`: each step shows the next screen of rows, "all" the whole list; it records each call."""

    def __init__(self, screens: list[str], everything: str = ""):
        self.screens, self.everything, self.calls = screens, everything, []

    async def __call__(self, how: str) -> tuple[str, bool]:
        self.calls.append(how)
        if how == "all":
            return self.everything, False
        shown = "\n".join(self.screens[:len([c for c in self.calls if c == "step"]) + 1])
        return shown, len(self.calls) < len(self.screens) - 1


async def test_a_page_that_fits_is_answered_in_one_call_with_nothing_loaded() -> None:
    model, feed = Model({"answer": "关注 129，粉丝 4649，获赞 87411", "found": "yes"}), Feed([PROFILE, "more cards"])
    got = await pagetext.answer(model, "账号数据：关注数、粉丝数、获赞数", PROFILE, 50_000, more=feed, goes_on=True)
    assert got["calls"] == 1 and feed.calls == []
    # The page went whole: its numbers and the last card of its feed, in the one call.
    assert model.sent[0][0] == pagetext.READ and "粉丝 4649" in model.sent[0][1] and "（第39期）" in model.sent[0][1]
    assert "87411" in got["answer"]
    assert "whole page" in got["read"]


async def test_more_is_loaded_one_step_at_a_time_and_stops_once_answered() -> None:
    screens = ["## 动态\n张三：早上好", "李四：中午吃什么", "林小雨：今天去看海了", "王五：晚安", "赵六：周末见"]
    model = Model({"answer": "", "found": "no"}, {"answer": "", "found": "no"},
                  {"answer": "林小雨：今天去看海了", "found": "yes"})
    feed = Feed(screens)
    got = await pagetext.answer(model, "林小雨发了什么", screens[0], 50_000, more=feed, goes_on=True)
    assert feed.calls == ["step", "step"] and got["calls"] == 3
    # Each later call carries only the rows that came new.
    assert "张三" not in model.sent[2][1] and "林小雨" in model.sent[2][1]
    assert "loaded 2 times" in got["read"]


async def test_a_question_over_the_whole_list_loads_the_rest_once() -> None:
    first = "## 我的回答（400）\n" + "\n".join(f"回答{n} · 阅读 {100 + n}" for n in range(10))
    everything = "## 我的回答（400）\n" + "\n".join(f"回答{n} · 阅读 {100 + n}" for n in range(400))
    model = Model({"answer": "回答9", "found": "partly"}, {"answer": "回答399 · 阅读 499", "found": "yes"})
    feed = Feed([first], everything)
    got = await pagetext.answer(model, "哪一篇阅读量最高", first, 50_000, more=feed, goes_on=True)
    assert feed.calls == ["all"] and got["calls"] == 2
    assert "回答399 · 阅读 499" in model.sent[1][1] and got["answer"] == "回答399 · 阅读 499"


async def test_a_list_already_whole_is_read_again_once_loading_shows_nothing_is_left() -> None:
    whole = "## 全部文章（300）\n" + "\n".join(f"文章{n} · 阅读 {100 + n}" for n in range(300))
    model = Model({"answer": "只列出了部分", "found": "partly"}, {"answer": "文章299 · 阅读 399", "found": "yes"})
    feed = Feed([whole], whole)
    got = await pagetext.answer(model, "哪一篇阅读量最高", whole, 50_000, more=feed, goes_on=True)
    assert feed.calls == ["all"] and got["calls"] == 2
    assert got["answer"] == "文章299 · 阅读 399" and got["found"] == "yes" and "loaded to its end" in got["read"]


async def test_the_rows_of_a_table_given_whole_are_counted_for_the_model_and_in_what_was_read() -> None:
    table = "| 标题 | 已读 |\n| --- | --- |\n" + "\n".join("| 同题 | 是 |" for _ in range(37))
    model = Model({"answer": "", "found": "no"})
    got = await pagetext.answer(model, "哪篇读过", table, 50_000)
    assert "37" in model.sent[0][1] and "37" in got["read"]


async def test_a_page_too_long_gives_its_outline_and_the_lines_that_match() -> None:
    model = Model({"answer": "2015年", "found": "yes", "section": 0, "look_for": ""})
    got = await pagetext.answer(model, "东城分馆是哪一年开始全天二十四小时自助借还的", REPORT, 3000)
    assert got["calls"] == 1 and got["answer"] == "2015年"
    sent = model.sent[0][1]
    # The line that answers, under the number of its section, which can be asked for whole.
    assert "东城分馆早在2015年" in sent and f"§{got['sections'].index('第50章')} 第50章" in sent
    # Far less than the page, and the reading says it read a part.
    assert len(sent) < len(REPORT) / 3
    assert "not read whole" in got["read"] and "第50章" in got["sections"]


async def test_lines_that_match_say_how_many_matched_and_how_many_were_given() -> None:
    # A long list where every row matches: the model is told it sees only some of them, counted.
    rows = "\n".join(f"回答{n}\n发布于 11-0{n % 9 + 1} · 阅读 {100 + n}" for n in range(400))
    model = Model({"answer": "", "found": "partly", "section": 0, "look_for": ""},
                  *[{"found": "回答399 · 阅读 499"}] * 12, {"answer": "回答399，阅读 499", "found": "yes"})
    await pagetext.answer(model, "哪一篇阅读最高", rows, 1500)
    counts = re.search(r"\((\d+)\D+(\d+)\D*\):", model.sent[0][1])
    assert counts and int(counts.group(1)) == 400 and 0 < int(counts.group(2)) < 400


async def test_a_section_the_model_names_is_read_whole() -> None:
    number = int(re.search(r"§(\d+) 第50章", pagetext.give(REPORT, "东城分馆", 3000)[0]).group(1))
    model = Model({"answer": "", "found": "no", "section": number, "look_for": ""}, {"answer": "2015年", "found": "yes"})
    got = await pagetext.answer(model, "图书馆在哪一年做了改变", REPORT, 3000)
    assert got["calls"] == 2 and f"§{number}" in got["read"]
    assert model.sent[1][1].count("第50季度") == 12 and "第51季度" not in model.sent[1][1]


async def test_pieces_side_by_side_only_when_too_long_and_the_question_needs_all_of_it() -> None:
    table = "| 标题 | 阅读 |\n| --- | --- |\n" + "\n".join(f"| 文章{n} | {1000 + n} |" for n in range(1500))
    pieces = len(pagetext.pieces(pagetext.clean(table), pagetext._chars(table, 3000)))
    model = Model({"answer": "", "found": "partly", "section": 0, "look_for": ""},
                  *[{"found": f"文章{n}"} for n in range(pieces)], {"answer": "文章1499 · 2499"})
    got = await pagetext.answer(model, "哪篇阅读最高", table, 3000)
    assert got["calls"] == 1 + pieces + 1 and got["answer"] == "文章1499 · 2499"
    # Every piece keeps the table's header row.
    assert all("| 标题 | 阅读 |" in sent for system, sent in model.sent if system == pagetext.PART)


def test_the_room_follows_the_window_the_app_measured() -> None:
    small, large = pagetext.room(context.budget(32_000)), pagetext.room(context.budget(128_000))
    assert small < large <= pagetext.PAGE_MOST
    assert pagetext.room(context.budget(2_000_000)) == pagetext.PAGE_MOST


def test_repeated_chrome_goes_and_numbers_and_rows_stay() -> None:
    kept = pagetext.clean(PROFILE + "\n| 标题 | 已读 |\n| --- | --- |\n" + "\n".join("| 同名 | 是 |" for _ in range(6)))
    assert "分享" not in kept.split("\n")
    assert "关注 129" in kept and kept.count("| 同名 | 是 |") == 6


def test_a_tool_gets_the_page_whole_when_it_fits_else_its_outline_and_how_to_read_on() -> None:
    given, note = pagetext.give(PROFILE, "粉丝数", 50_000)
    assert note == "the whole page" and "粉丝 4649" in given
    given, note = pagetext.give(REPORT, "东城分馆 自助借还", 3000)
    number = int(re.search(r"§(\d+) 第50章", given).group(1))
    assert "东城分馆早在2015年" in given and len(given) < len(REPORT) / 3
    assert "section" in note and "not given whole" in note
    given, note = pagetext.give(REPORT, "", 3000, part=number)
    assert given.startswith("## 第50章") and "第51季度" not in given and f"§{number}" in note


class Hands:
    """A page place as a read step reaches it: a read gives its document, and says whether the page goes on."""

    def __init__(self, text: str):
        self.text, self.acts = text, []

    async def act(self, action: dict) -> dict:
        self.acts.append(action)
        return {"status": "ok", "text": self.text, "goes_on": True, "line": "[ok] read the page"}


async def test_a_read_step_answers_a_profile_in_one_call_and_says_what_it_read() -> None:
    read = []

    async def ask_model(system: str, messages: list[dict]) -> str:
        read.append(messages[-1]["content"])
        return json.dumps({"answer": "关注 129，粉丝 4649，获赞 87411", "found": "yes"}, ensure_ascii=False)
    hands = Hands(PROFILE)
    got = await operate.read_page(ask_model, hands, {"do": "read"}, "账号数据", 50_000)
    # One reading call, one read of the page and no more loaded, though the page goes on.
    assert got["status"] == "ok" and got["calls"] == 1 and len(read) == 1 and len(hands.acts) == 1
    assert "粉丝 4649" in got["answer"] and "whole page" in got["read"]


# The document read in the browser: off-screen words, a table under its header, no site navigation, nothing scrolled.
PAGE = """<!doctype html><html><body style="margin:0">
<nav><a href="#">首页</a><a href="#">推荐</a></nav>
<header><h1>小乐的日常</h1><div style="display:flex"><span>关注 129</span><span>粉丝 4649</span></div></header>
<div style="height:3000px"></div>
<table><thead><tr><th>标题</th><th>阅读</th></tr></thead><tbody><tr><td>远处的文章</td><td>9174</td></tr></tbody></table>
<p>页面最底部的一句话</p>
<footer>用户服务协议 隐私政策</footer>
</body></html>"""


# A box that draws only the rows in view, as long lists do, beside a box whose rows are all in the document.
VLIST = """<!doctype html><html><body style="margin:0"><h2>我的回答（60）</h2>
<div id="box" style="height:200px;overflow:auto"><div id="in" style="position:relative;height:1800px"></div></div>
<div id="all" style="height:200px;overflow:auto">""" + "".join(f"<p>通知{n}</p>" for n in range(40)) + """</div>
<script>
const box = document.getElementById('box'), inner = document.getElementById('in');
function draw() { const first = Math.floor(box.scrollTop / 30); inner.innerHTML = '';
  for (let n = first; n < Math.min(60, first + 8); n++) { const row = document.createElement('div');
    row.style.cssText = `position:absolute;top:${n * 30}px;height:30px`; row.textContent = `回答${n} · 阅读 ${100 + n}`;
    inner.appendChild(row); } }
box.addEventListener('scroll', draw); draw();
</script></body></html>"""


@pytest.mark.browser
async def test_a_list_that_draws_only_the_rows_in_view_is_read_through_by_a_plain_read(tmp_path) -> None:
    from mibo import installed_browser
    from mibo.web import gate
    from mibo.web.tabs import PlaywrightTab
    try:
        from playwright.async_api import async_playwright
    except ImportError as missing:
        pytest.skip(f"no Playwright: {missing}")
    found = installed_browser.find_browser()
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(executable_path=str(found) if found else None, headless=True)
        except Exception as missing:
            pytest.skip(f"no headless browser: {missing}")
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.set_content(VLIST)
        hands = operate.Hands(PlaywrightTab(page), ask=None, allowed=gate.Allowed(tmp_path / "allow"),
                              attended=lambda: False, lang="zh")
        await hands.look(6000)
        read = await hands.act({"do": "read", "question": "哪篇阅读最高"})
        tops = await page.evaluate("['box', 'all'].map((id) => document.getElementById(id).scrollTop)"
                                   ".concat(document.scrollingElement.scrollTop)")
        await browser.close()
    # Every row of the list that draws only those in view, and of the box whose rows the document holds.
    assert "回答0 · 阅读 100" in read["text"] and "回答59 · 阅读 159" in read["text"]
    assert "通知0" in read["text"] and "通知39" in read["text"]
    # The box of plain rows still goes on below what it shows; nothing is left scrolled.
    assert read["goes_on"] and tops == [0, 0, 0]


@pytest.mark.browser
async def test_the_document_is_read_at_once_with_nothing_scrolled() -> None:
    from mibo import installed_browser
    from mibo.web.pageops import Page
    from mibo.web.tabs import PlaywrightTab
    try:
        from playwright.async_api import async_playwright
    except ImportError as missing:
        pytest.skip(f"no Playwright: {missing}")
    found = installed_browser.find_browser()
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(executable_path=str(found) if found else None, headless=True)
        except Exception as missing:
            pytest.skip(f"no headless browser: {missing}")
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.set_content(PAGE)
        text, goes_on = await Page(PlaywrightTab(page)).document()
        scrolled = await page.evaluate("document.scrollingElement.scrollTop")
        await browser.close()
    lines = text.split("\n")
    assert "## 小乐的日常" in lines and "关注 129" in lines and "粉丝 4649" in lines
    assert "| 标题 | 阅读 |" in lines and lines[lines.index("| 标题 | 阅读 |") + 1].startswith("| ---")
    assert "页面最底部的一句话" in lines and "首页" not in text and "隐私政策" not in text
    assert goes_on and scrolled == 0
