"""World news and general reading: newspapers, magazines, Wikipedia's daily pages,
space agencies, sport and the open social networks' trending lists.

Most of these are feeds the publishers offer; the rest are public JSON a
visitor's browser reads, or a section page read the same way.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .base import PER_SOURCE, Find, Source, find, get_json, local_to, plain, read_feed, written_in

# Query parameters that only tell the publisher where a click came from; dropped
# so the same story from two places is one item in the library.
_TRACKING = ("utm_", "at_", "maca")


def _clean(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith(_TRACKING)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _feed(key: str, name: tuple[str, str], about: tuple[str, str], group: str, url: str, *, site: str = "",
          default: str = "", link: Callable[[str], str] = _clean) -> Source:
    """A source that is one feed address, its links cleaned of tracking."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found = (await read_feed(http, url, site=site or name[0]))[:PER_SOURCE]
        for one in found:
            one.item.url = link(one.item.url)
            # NPR's feed sometimes names an image its page script never filled in.
            if one.picture.endswith("/undefined"):
                one.picture = ""
        return found

    return Source(key, name, about, group, fetch, default)


def _headline(text: str, limit: int = 100) -> str:
    """A post's first line, as its title."""
    line = next((part for part in text.splitlines() if part.strip()), "").strip()
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + "…"


# --- Wikipedia's daily feed: one request gives the featured article, the most
# read, on this day and in the news; each is its own source.

async def _wiki_day(http: httpx.AsyncClient, lang: str) -> dict:
    day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    # zh-cn asks the site to convert extracts and display titles to simplified characters.
    answer = await http.get(f"https://{lang}.wikipedia.org/api/rest_v1/feed/featured/{day}",
                            headers={"Accept-Language": "zh-cn" if lang == "zh" else "en"})
    answer.raise_for_status()
    return answer.json()


def _wiki_page(page: dict, lang: str, *, title: str = "", text: str = "") -> Find:
    url = page["content_urls"]["desktop"]["page"]
    if lang == "zh":
        url = url.replace("/wiki/", "/zh-cn/", 1)
    picture = (page.get("thumbnail") or {}).get("source", "")
    return find(url, title or page.get("displaytitle") or page["normalizedtitle"], site="Wikipedia",
                text=text or page.get("extract", ""), picture=picture)


def _wiki_featured(lang: str):
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        day = await _wiki_day(http, lang)
        return [_wiki_page(day["tfa"], lang)] if "tfa" in day else []
    return fetch


def _wiki_mostread(lang: str):
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        articles = (await _wiki_day(http, lang))["mostread"]["articles"]
        return [_wiki_page(page, lang) for page in sorted(articles, key=lambda a: a["rank"])[:PER_SOURCE]]
    return fetch


# A year's own article ("1991", "1991年") says nothing about the event.
_YEAR_PAGE = re.compile(r"(AD )?\d{1,4}( BC)?年?")


def _wiki_onthisday(lang: str):
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found: list[Find] = []
        seen: set[str] = set()
        for event in (await _wiki_day(http, lang))["onthisday"]:
            pages = [p for p in event["pages"] if not _YEAR_PAGE.fullmatch(p["normalizedtitle"])] or event["pages"]
            if not pages or pages[0]["normalizedtitle"] in seen:
                continue
            seen.add(pages[0]["normalizedtitle"])
            year = f"{event['year']}年：" if lang == "zh" else f"{event['year']}: "
            found.append(_wiki_page(pages[0], lang, title=year + event["text"]))
        return found[:PER_SOURCE]
    return fetch


_BOLD_LINK = re.compile(r"""<b[^>]*>\s*<a[^>]+href=["']\./([^"'#]+)""")


async def _wiki_news(http: httpx.AsyncClient) -> list[Find]:
    found: list[Find] = []
    for story in (await _wiki_day(http, "en"))["news"]:
        # The bold link in a story is the article it is about.
        bold = _BOLD_LINK.search(story["story"])
        pages = [p for p in story["links"] if bold and p["title"] == bold.group(1)] or story["links"]
        found.append(_wiki_page(pages[0], "en", title=plain(story["story"]), text=pages[0].get("extract", "")))
    return found[:PER_SOURCE]


# --- The open social networks.

async def _mastodon_links(http: httpx.AsyncClient) -> list[Find]:
    links = await get_json(http, "https://mastodon.social/api/v1/trends/links", limit=PER_SOURCE)
    return [find(_clean(link["url"]), link["title"], site=link.get("provider_name", ""),
                 text=link.get("description", ""), author=link.get("author_name", ""),
                 published=link.get("published_at") or "", picture=link.get("image") or "")
            for link in links if link.get("title")][:PER_SOURCE]


async def _mastodon_posts(http: httpx.AsyncClient) -> list[Find]:
    found: list[Find] = []
    for status in await get_json(http, "https://mastodon.social/api/v1/trends/statuses", limit=PER_SOURCE * 2):
        # The first paragraph is the title; a post made only of pictures has none and is passed over.
        paragraphs = [plain(part) for part in re.split(r"</p>|<br\s*/?>", status["content"])]
        first = next((part for part in paragraphs if part), "")
        if not first:
            continue
        account = status["account"]
        images = [m for m in status.get("media_attachments", []) if m.get("type") == "image"]
        picture = images[0]["preview_url"] if images else ((status.get("card") or {}).get("image") or "")
        found.append(find(status["url"] or status["uri"], _headline(first), site="Mastodon", kind="post",
                          text=plain(status["content"]), author=account.get("display_name") or account["acct"],
                          published=status["created_at"], picture=picture))
    return found[:PER_SOURCE]


def _bluesky_picture(embed: dict) -> str:
    embed = embed.get("media", embed)          # a quote with media keeps the media one level down
    if embed.get("images"):
        return embed["images"][0].get("thumb", "")
    if embed.get("external"):
        return embed["external"].get("thumb", "")
    return embed.get("thumbnail", "")


async def _bluesky_hot(http: httpx.AsyncClient) -> list[Find]:
    feed = await get_json(http, "https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed",
                          feed="at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot",
                          limit=PER_SOURCE * 2)
    found: list[Find] = []
    for entry in feed["feed"]:
        post = entry["post"]
        text = post["record"].get("text", "")
        if not text.strip():
            continue
        author = post["author"]
        url = f"https://bsky.app/profile/{author['handle']}/post/{post['uri'].rsplit('/', 1)[-1]}"
        found.append(find(url, _headline(text), site="Bluesky", kind="post", text=text,
                          author=author.get("displayName") or author["handle"],
                          published=post["record"].get("createdAt", ""),
                          picture=_bluesky_picture(post.get("embed") or {})))
    return found[:PER_SOURCE]


# --- Pages and APIs that are not feeds.

def _alt(entry: dict) -> str:
    # The feed's summary is the comic itself; its title attribute is the joke's second half.
    match = re.search(r'title="([^"]*)"', entry.get("summary", ""))
    return match.group(1) if match else ""


async def _xkcd(http: httpx.AsyncClient) -> list[Find]:
    return (await read_feed(http, "https://xkcd.com/atom.xml", site="xkcd", text=_alt))[:PER_SOURCE]


_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def _story(entry: dict) -> str:
    # NASA's feed carries the whole page, menus and blurbs first; the longest paragraph is the story's own.
    page = next((part.value for part in entry.get("content", [])), "")
    return max((plain(p, 2000) for p in _PARAGRAPH.findall(page)), key=len, default="")


async def _nasa(http: httpx.AsyncClient) -> list[Find]:
    return (await read_feed(http, "https://www.nasa.gov/feed/", site="NASA", text=_story))[:PER_SOURCE]


async def _espn(http: httpx.AsyncClient) -> list[Find]:
    news = await get_json(http, "https://now.core.api.espn.com/v1/sports/news", limit=PER_SOURCE * 2)
    found: list[Find] = []
    for story in news["headlines"]:
        web = (story.get("links") or {}).get("web", {}).get("href", "")
        if not web.startswith("https://") or story["type"] not in ("Story", "HeadlineNews"):
            continue
        images = story.get("images") or []
        found.append(find(web, story["headline"], site="ESPN", text=story.get("description", ""),
                          author=story.get("byline") or "", published=story.get("published", ""),
                          picture=images[0]["url"] if images else ""))
    return found[:PER_SOURCE]


def _bbc_simplified(url: str) -> str:
    # The Chinese feed links each story's traditional page; the same story has a simplified one.
    return re.sub(r"/trad$", "/simp", _clean(url))


SOURCES: list[Source] = [
    # News in English.
    _feed("bbc-world", ("BBC News: World", "BBC 国际新闻"),
          ("World news from the BBC", "BBC 的国际新闻"), "news",
          "https://feeds.bbci.co.uk/news/world/rss.xml", site="BBC", default="en"),
    _feed("guardian-world", ("The Guardian: World", "卫报 国际"),
          ("World news from The Guardian", "《卫报》的国际新闻"), "news",
          "https://www.theguardian.com/world/rss", site="The Guardian"),
    _feed("npr-news", ("NPR News", "NPR 新闻"),
          ("Top stories from America's public radio", "美国公共广播电台的头条"), "news",
          "https://feeds.npr.org/1001/rss.xml", site="NPR", default="en"),
    _feed("nyt-home", ("The New York Times", "纽约时报"),
          ("The front page of The New York Times", "《纽约时报》首页头条"), "news",
          "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    _feed("aljazeera-news", ("Al Jazeera", "半岛电视台"),
          ("News from Al Jazeera English, strong on the Middle East and Global South",
           "半岛电视台英文新闻，中东与南方国家报道多"), "news",
          "https://www.aljazeera.com/xml/rss/all.xml"),
    _feed("economist-latest", ("The Economist", "经济学人"),
          ("The latest from The Economist", "《经济学人》最新文章"), "news",
          "https://www.economist.com/latest/rss.xml"),
    _feed("dw-en", ("DW News", "德国之声 英文"),
          ("Deutsche Welle in English: Germany, Europe and the world", "德国之声英文：德国、欧洲与世界"), "news",
          "https://rss.dw.com/xml/rss-en-all", site="DW"),
    _feed("france24-en", ("France 24", "法国24台"),
          ("France 24 in English: news from a French view", "法国24台英文：法国视角的国际新闻"), "news",
          "https://www.france24.com/en/rss"),
    _feed("skynews-world", ("Sky News: World", "天空新闻 国际"),
          ("World stories from Sky News", "英国天空新闻的国际报道"), "news",
          "https://feeds.skynews.com/feeds/rss/world.xml", site="Sky News"),
    _feed("abc-au-news", ("ABC News Australia", "澳大利亚广播公司新闻"),
          ("Just in from the Australian Broadcasting Corporation", "澳大利亚广播公司的最新消息"), "news",
          "https://www.abc.net.au/news/feed/51120/rss.xml", site="ABC News"),
    _feed("scmp-news", ("South China Morning Post", "南华早报"),
          ("Hong Kong, China and Asia from the SCMP, in English", "《南华早报》英文版：香港、中国与亚洲"), "news",
          "https://www.scmp.com/rss/91/feed", site="SCMP"),
    _feed("un-news-en", ("UN News", "联合国新闻 英文"),
          ("Global news from the United Nations", "联合国新闻网的全球动态"), "news",
          "https://news.un.org/feed/subscribe/en/news/all/rss.xml", site="UN News"),
    _feed("propublica", ("ProPublica", "ProPublica"),
          ("Investigative journalism in the public interest", "公共利益调查报道"), "news",
          "https://www.propublica.org/feeds/propublica/main"),
    _feed("conversation-global", ("The Conversation", "The Conversation"),
          ("News explained by academics and researchers", "学者和研究者写的新闻解读"), "news",
          "https://theconversation.com/global/articles.atom"),
    _feed("pew-research", ("Pew Research Center", "皮尤研究中心"),
          ("Survey findings on politics, religion, technology and society", "政治、宗教、科技与社会的民调发现"),
          "news", "https://www.pewresearch.org/feed/", site="Pew Research"),
    Source("wiki-en-news", ("Wikipedia: In the news", "维基百科 新闻动态"),
           ("The stories on English Wikipedia's front page", "英文维基百科首页的新闻条目"), "news", _wiki_news),

    # News in Chinese and Japanese.
    _feed("bbc-zh", ("BBC News Chinese", "BBC 中文"),
          ("BBC's Chinese service", "BBC 中文网的新闻"), "news",
          "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", site="BBC 中文", link=_bbc_simplified),
    _feed("nyt-cn", ("The New York Times Chinese", "纽约时报中文网"),
          ("The New York Times in Chinese", "《纽约时报》的中文报道"), "news",
          "https://cn.nytimes.com/rss/", site="纽约时报中文网"),
    _feed("dw-zh", ("DW Chinese", "德国之声 中文"),
          ("Deutsche Welle's Chinese service", "德国之声中文网"), "news",
          "https://rss.dw.com/xml/rss-chi-all", site="德国之声"),
    _feed("rfi-zh", ("RFI Chinese", "法广 中文"),
          ("Radio France Internationale in Chinese", "法国国际广播电台中文部"), "news",
          "https://www.rfi.fr/cn/rss", site="法广"),
    _feed("un-news-zh", ("UN News Chinese", "联合国新闻 中文"),
          ("The United Nations' news in Chinese", "联合国新闻网中文版"), "news",
          "https://news.un.org/feed/subscribe/zh/news/all/rss.xml", site="联合国新闻"),
    _feed("nhk-news", ("NHK News (Japanese)", "NHK 新闻（日文）"),
          ("Japan's public broadcaster, in Japanese", "日本公共广播的日文新闻"), "news",
          "https://www.nhk.or.jp/rss/news/cat0.xml", site="NHK"),

    # Sport.
    Source("espn-top", ("ESPN", "ESPN"), ("Top sports headlines from ESPN", "ESPN 体育头条"), "news", _espn),
    _feed("bbc-sport", ("BBC Sport", "BBC 体育"),
          ("Football, cricket, tennis and more from the BBC", "BBC 的足球、板球、网球等体育新闻"), "news",
          "https://feeds.bbci.co.uk/sport/rss.xml", site="BBC Sport"),

    # Magazines and long reads.
    _feed("atlantic", ("The Atlantic", "大西洋月刊"),
          ("Essays and reporting from The Atlantic", "《大西洋月刊》的评论与报道"), "culture",
          "https://www.theatlantic.com/feed/all/"),
    _feed("newyorker", ("The New Yorker", "纽约客"),
          ("Reporting, criticism and fiction from The New Yorker", "《纽约客》的报道、评论与小说"), "culture",
          "https://www.newyorker.com/feed/everything"),
    _feed("vox", ("Vox", "Vox"),
          ("Explainers on politics, policy and culture", "政治、政策与文化的解释性文章"), "news",
          "https://www.vox.com/rss/index.xml"),
    _feed("guardian-longread", ("The Guardian: The Long Read", "卫报 长读"),
          ("The Guardian's weekly in-depth stories", "《卫报》的深度长文"), "culture",
          "https://www.theguardian.com/news/series/the-long-read/rss", site="The Guardian"),
    _feed("longreads", ("Longreads", "Longreads"),
          ("Long-form stories, picked and published", "精选与原创的长篇非虚构"), "culture",
          "https://longreads.com/feed/"),
    _feed("aeon", ("Aeon", "Aeon"),
          ("Essays and short films on ideas, science and culture", "关于思想、科学与文化的随笔和短片"), "culture",
          "https://aeon.co/feed.rss"),
    _feed("marginalian", ("The Marginalian", "The Marginalian"),
          ("Maria Popova on books, art and how to live", "Maria Popova 谈书、艺术与生活"), "culture",
          "https://www.themarginalian.org/feed/"),
    _feed("kottke", ("Kottke.org", "Kottke.org"),
          ("Jason Kottke's links to the best of the web", "Jason Kottke 收集的网上好东西"), "culture",
          "https://feeds.kottke.org/main", site="kottke.org"),
    _feed("atlas-obscura", ("Atlas Obscura", "Atlas Obscura"),
          ("The world's hidden wonders and curious places", "世界各地的奇特地方与冷知识"), "culture",
          "https://www.atlasobscura.com/feeds/latest", default="en"),
    _feed("smithsonian", ("Smithsonian Magazine", "史密森尼杂志"),
          ("History, science, art and travel", "历史、科学、艺术与旅行"), "culture",
          "https://www.smithsonianmag.com/rss/latest_articles/", site="Smithsonian"),
    _feed("publicdomainreview", ("The Public Domain Review", "公共领域评论"),
          ("Essays on curious art and books from the public domain", "公共领域中奇特图文的赏析"), "culture",
          "https://publicdomainreview.org/rss.xml"),
    _feed("pudding", ("The Pudding", "The Pudding"),
          ("Visual essays that explain culture with data", "用数据讲文化的可视化长文"), "culture",
          "https://pudding.cool/feed/index.xml"),
    Source("xkcd", ("xkcd", "xkcd"),
           ("Randall Munroe's comic about romance, sarcasm, math and language", "关于浪漫、讽刺、数学和语言的网络漫画"),
           "culture", _xkcd, "en"),

    # Science, data and space.
    _feed("nautilus", ("Nautilus", "鹦鹉螺"),
          ("Science told through stories", "用故事讲科学"), "science",
          "https://nautil.us/feed/"),
    _feed("owid", ("Our World in Data", "用数据看世界"),
          ("Research and charts on the world's largest problems", "关于世界重大问题的研究与图表"), "science",
          "https://ourworldindata.org/atom.xml"),
    _feed("flowingdata", ("FlowingData", "FlowingData"),
          ("Data visualisation and statistics, by Nathan Yau", "Nathan Yau 的数据可视化与统计"), "science",
          "https://flowingdata.com/feed/"),
    Source("nasa", ("NASA", "美国宇航局"),
           ("NASA news and the Astronomy Picture of the Day", "NASA 新闻与每日天文图"), "science", _nasa, "en"),
    _feed("esa-top", ("ESA", "欧洲空间局"),
          ("Top news from the European Space Agency", "欧洲空间局的头条新闻"), "science",
          "https://www.esa.int/rssfeed/TopNews"),

    # Wikipedia's daily pages.
    Source("wiki-en-featured", ("Wikipedia: Today's featured article", "英文维基 今日特色条目"),
           ("The article English Wikipedia features today", "英文维基百科今天推荐的条目"), "culture",
           _wiki_featured("en")),
    Source("wiki-en-mostread", ("Wikipedia: Most read", "英文维基 最多阅读"),
           ("What people read most on English Wikipedia yesterday", "昨天英文维基百科上读得最多的条目"), "culture",
           _wiki_mostread("en")),
    Source("wiki-en-onthisday", ("Wikipedia: On this day", "英文维基 历史上的今天"),
           ("What happened on this date in history", "历史上的今天发生了什么"), "culture",
           _wiki_onthisday("en")),
    Source("wiki-zh-featured", ("Chinese Wikipedia: Featured article", "维基百科 今日典范条目"),
           ("The article Chinese Wikipedia features today", "中文维基百科今天推荐的条目"), "culture",
           _wiki_featured("zh")),
    Source("wiki-zh-mostread", ("Chinese Wikipedia: Most read", "维基百科 最多阅读"),
           ("What people read most on Chinese Wikipedia yesterday", "昨天中文维基百科上读得最多的条目"), "culture",
           _wiki_mostread("zh")),
    Source("wiki-zh-onthisday", ("Chinese Wikipedia: On this day", "维基百科 历史上的今天"),
           ("This date in history, from Chinese Wikipedia", "中文维基百科的历史上的今天"), "culture",
           _wiki_onthisday("zh")),

    # The open social networks.
    Source("mastodon-links", ("Mastodon: Trending links", "Mastodon 热门链接"),
           ("The news stories most shared on mastodon.social", "mastodon.social 上分享最多的新闻"), "community",
           _mastodon_links),
    Source("mastodon-posts", ("Mastodon: Trending posts", "Mastodon 热门嘟文"),
           ("The posts most boosted on mastodon.social", "mastodon.social 上转发最多的嘟文"), "community",
           _mastodon_posts),
    Source("bluesky-hot", ("Bluesky: What's Hot", "Bluesky 热门"),
           ("The posts rising fastest across Bluesky", "Bluesky 上正在升温的帖子"), "community", _bluesky_hot, "en"),
]

SOURCES = written_in("ja", written_in("zh", SOURCES, {"bbc-zh", "nyt-cn", "dw-zh", "rfi-zh", "un-news-zh",
                                                    "wiki-zh-featured", "wiki-zh-mostread", "wiki-zh-onthisday"}),
                   {"nhk-news"})
# National press local to one place; world services in any language stay global.
SOURCES = local_to("US", SOURCES, {"npr-news", "propublica", "espn-top"})
SOURCES = local_to("JP", local_to("HK", local_to("AU", SOURCES, {"abc-au-news"}),
                                           {"scmp-news"}), {"nhk-news"})
