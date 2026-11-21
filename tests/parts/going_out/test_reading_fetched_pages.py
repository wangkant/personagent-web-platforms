"""A page read over a plain fetch, as read_page reads it with no browser, as a link in the person's message is
named to the model and as a link card is drawn: its words come in the charset a browser would read them in, a
story is kept and the page's menus, side lists and footer are left, a table keeps its rows under its header, and
reading the page reaches nothing past the page itself. The pages are saved ones (tests/data/pages)."""
from __future__ import annotations

from pathlib import Path

from harness.model import call, say
from mibo.panels.links import Links

PAGES = Path(__file__).resolve().parents[2] / "data" / "pages"


def put(web, path: str, name: str, kind: str = "text/html") -> str:
    """The saved page `name` served at `path` on shore.example, with `kind` as its type; its address."""
    base = web.name("shore.example", "93.184.216.34")
    web.site.put(path, (PAGES / name).read_bytes(), kind)
    return f"{base}{path}"


def read(turn, url: str):
    """One turn that reads `url` with read_page: (what the tool gave the model, what the person's message
    reached it as)."""
    done = turn(f"What does {url} say?", call("read_page", url=url), say("Read it."))
    return done.result_of(done.calls()[0]["id"]), str(done.asked()[0]["messages"][-1]["content"])


def test_a_page_that_names_gb2312_only_in_its_meta_reads_in_its_own_words(turn, web) -> None:
    # The server names no charset, and the page holds 堃, which GB2312 lacks: browsers read it as GB18030.
    url = put(web, "/tides", "gbk-meta-only.html")
    given, told = read(turn, url)
    assert "当地志愿者王堃带领十二名中学生沿着礁石记录物种" in given and "�" not in given
    assert "潮汐池的清晨·海岸日报" in told and "�" not in told


def test_a_short_story_is_read_without_the_pages_menus_side_lists_and_footer(turn, web) -> None:
    url = put(web, "/lighthouse", "short-story.html", "text/html; charset=utf-8")
    given, _ = read(turn, url)
    for said in ("北角灯塔昨晚七点重新点亮", "守塔人老周今年六十一岁", "灯塔改造历时十个月"):
        assert said in given
    for chrome in ("相关阅读", "社区食堂推出老年套餐", "广告服务", "京ICP备", "房产"):
        assert chrome not in given


def test_a_table_keeps_its_rows_under_its_header(turn, web) -> None:
    url = put(web, "/tides/week", "table.html", "text/html; charset=utf-8")
    given, _ = read(turn, url)
    lines = given.split("\n")
    header = lines.index("| 城市 | 日期 | 最高潮位 |")
    assert lines[header + 1] == "| --- | --- | --- |"
    assert lines[header + 3] == "| 厦门 | 11月20日 | 5.6 |"
    assert "## 各城市最高潮位" in lines


def test_reading_a_page_reaches_nothing_the_page_points_to(turn, web) -> None:
    """The guarded fetch is the only thing that touches the network: the reader is handed the page and never
    follows its scripts, styles, pictures, refresh or links."""
    elsewhere = web.name("elsewhere.example", "93.184.216.34")
    body = (PAGES / "short-story.html").read_text(encoding="utf-8").replace("<head>", (
        f'<head><meta http-equiv="refresh" content="30;url={elsewhere}/moved">'
        f'<link rel="canonical" href="{elsewhere}/canon"><link rel="stylesheet" href="{elsewhere}/s.css">'
        f'<script src="{elsewhere}/s.js"></script>')).replace("<h1>", f'<img src="{elsewhere}/p.jpg"><h1>')
    base = web.name("shore.example", "93.184.216.34")
    web.site.put("/lighthouse", body)
    given, _ = read(turn, f"{base}/lighthouse")
    assert "守塔人老周今年六十一岁" in given
    assert set(web.lookups) == {"shore.example"} and set(web.site.log) == {"GET /lighthouse"}


def test_a_title_with_an_apostrophe_reaches_the_model_whole(turn, web) -> None:
    base = web.name("shore.example", "93.184.216.34")
    web.site.put("/quote", "<html><head><meta property=\"og:title\" content=\"It's low tide at four\">"
                           "<meta property=\"og:description\" content=\"The pools open at dawn.\"></head>"
                           "<body><p>Tide pools.</p></body></html>")
    told = str(turn(f"look {base}/quote", say("Seen.")).asked()[0]["messages"][-1]["content"])
    assert "It's low tide at four" in told and "The pools open at dawn." in told


async def test_a_link_card_takes_its_title_and_summary_from_the_page_in_its_charset(web, tmp_path) -> None:
    url = put(web, "/tides", "gbk-meta-only.html")
    card = await Links(tmp_path).preview(url)
    assert card["title"] == "潮汐池的清晨·海岸日报"
    assert card["description"] == "志愿者带领中学生记录潮汐池里的十九种生物。"
