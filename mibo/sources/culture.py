"""Video, music, film, games, books, anime, design and other pastimes.

Every place here answers a visitor without a key or a sign-in: channel and
site feeds, public charts and store lists, and the open APIs of Bangumi,
AniList, Open Library and the Art Institute of Chicago.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import re
from html import unescape
from itertools import zip_longest

import httpx

from .base import (PER_SOURCE, Find, Source, feed_source, find, get_json, get_text, local_to, read_feed,
                   written_in)


def _https(url: str) -> str:
    # Bangumi, Bilibili and NetEase still write http image addresses their hosts also serve over https.
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _from_ms(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat() if ms else ""


# ---------------------------------------------------------------- YouTube

def _youtube(key: str, name: tuple[str, str], about: tuple[str, str], channels: tuple[str, ...],
             default: str = "") -> Source:
    """A few channels on one interest, taken in turn so one busy channel does not fill the list."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        answers = await asyncio.gather(*(read_feed(http, "https://www.youtube.com/feeds/videos.xml", site="YouTube",
                                                   channel_id=channel) for channel in channels),
                                       return_exceptions=True)
        # Shorts crowd the channel feeds; the full videos are what is worth bringing.
        feeds = [[f for f in found if "/shorts/" not in f.item.url] for found in answers if isinstance(found, list)]
        if not feeds:
            raise answers[0]
        mixed = [f for row in zip_longest(*feeds) for f in row if f is not None]
        return mixed[:PER_SOURCE]

    return Source(key, name, about, "video", fetch, default)


# ---------------------------------------------------------------- charts and stores

def _steam(key: str, name: tuple[str, str], about: tuple[str, str], shelf: str) -> Source:
    """A shelf of the Steam store's front page, read from the store's own public JSON."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        data = await get_json(http, "https://store.steampowered.com/api/featuredcategories", cc="us", l="english")
        found, seen = [], set()
        for game in data[shelf]["items"]:
            # The same game can sit on a shelf twice, once per edition banner.
            if game["id"] in seen:
                continue
            seen.add(game["id"])
            price = game.get("final_price", 0) / 100
            if game.get("discount_percent"):
                text = f"-{game['discount_percent']}%: ${price:.2f} (was ${game['original_price'] / 100:.2f})"
            else:
                text = f"${price:.2f}" if price else "Free"
            found.append(find(f"https://store.steampowered.com/app/{game['id']}/", game["name"], site="Steam",
                              kind="page", text=text,
                              picture=game.get("large_capsule_image") or game.get("header_image", "")))
        return found[:PER_SOURCE]

    return Source(key, name, about, "culture", fetch)


def _netease(key: str, name: tuple[str, str], about: tuple[str, str], chart: int, default: str = "") -> Source:
    """A NetEase Cloud Music chart. Its public page carries the song list as JSON in a hidden textarea."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        page = await get_text(http, "https://music.163.com/discover/toplist", id=chart)
        match = re.search(r'<textarea id="song-list-pre-data"[^>]*>(.*?)</textarea>', page, re.S)
        if not match:
            raise ValueError("the chart page no longer carries its song list")
        found = []
        for song in json.loads(unescape(match.group(1)))[:PER_SOURCE]:
            singers = " / ".join(a["name"] for a in song.get("artists", []))
            album = song.get("album") or {}
            found.append(find(f"https://music.163.com/song?id={song['id']}", song["name"], site="网易云音乐",
                              kind="page", text=f"{singers} · {album.get('name', '')}", author=singers,
                              published=_from_ms(song.get("publishTime", 0)),
                              picture=_https(album.get("picUrl", ""))))
        return found

    return Source(key, name, about, "culture", fetch, default)


# ---------------------------------------------------------------- anime

async def _bangumi_today(http: httpx.AsyncClient) -> list[Find]:
    """What airs today on Bangumi's weekly calendar, most watched first."""
    days = await get_json(http, "https://api.bgm.tv/calendar")
    weekday = datetime.date.today().isoweekday()
    shows = next((day["items"] for day in days if day["weekday"]["id"] == weekday), [])
    shows = sorted(shows, key=lambda s: (s.get("collection") or {}).get("doing", 0), reverse=True)
    found = []
    for show in shows[:PER_SOURCE]:
        score = (show.get("rating") or {}).get("score")
        found.append(find(f"https://bgm.tv/subject/{show['id']}", show.get("name_cn") or show["name"],
                          site="Bangumi", kind="page",
                          text=" · ".join(filter(None, [show["name"], f"★ {score}" if score else "",
                                                       show.get("summary", "")])),
                          picture=_https((show.get("images") or {}).get("large", ""))))
    return found


async def _bangumi_season(http: httpx.AsyncClient) -> list[Find]:
    """This season's anime on Bangumi by members' rank. A season starts in January, April, July or October."""
    today = datetime.date.today()
    data = await get_json(http, "https://api.bgm.tv/v0/subjects", type=2, sort="rank", year=today.year,
                          month=(today.month - 1) // 3 * 3 + 1, limit=PER_SOURCE)
    found = []
    for show in data["data"]:
        score = (show.get("rating") or {}).get("score")
        found.append(find(f"https://bgm.tv/subject/{show['id']}", show.get("name_cn") or show["name"],
                          site="Bangumi", kind="page",
                          text=" · ".join(filter(None, [f"★ {score}" if score else "", show.get("summary", "")])),
                          published=show.get("date") or "",
                          picture=(show.get("images") or {}).get("large", "")))
    return found


_ANILIST_QUERY = """{Page(perPage: %d) {media(sort: TRENDING_DESC, type: ANIME, isAdult: false) {
  siteUrl title {english romaji} description(asHtml: false) coverImage {extraLarge}
  startDate {year month day} genres studios(isMain: true) {nodes {name}}}}}""" % PER_SOURCE


async def _anilist(http: httpx.AsyncClient) -> list[Find]:
    answer = await http.post("https://graphql.anilist.co", json={"query": _ANILIST_QUERY})
    answer.raise_for_status()
    found = []
    for show in answer.json()["data"]["Page"]["media"]:
        start = show.get("startDate") or {}
        when = "-".join(f"{part:02d}" for part in (start.get("year"), start.get("month"), start.get("day")) if part)
        studios = ", ".join(s["name"] for s in show["studios"]["nodes"])
        found.append(find(show["siteUrl"], show["title"]["english"] or show["title"]["romaji"], site="AniList",
                          kind="page", text=" · ".join(filter(None, [", ".join(show.get("genres") or []),
                                                                     show.get("description") or ""])),
                          author=studios, published=when, picture=show["coverImage"]["extraLarge"] or ""))
    return found


def _bilibili_shows(season_type: int):
    """Bilibili's ranking of one kind of licensed show over the last three days: 1 anime, 2 films,
    3 documentaries, 4 Chinese animation, 5 TV series."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        data = await get_json(http, "https://api.bilibili.com/pgc/web/rank/list", day=3, season_type=season_type)
        if data.get("code") != 0:
            raise ValueError(f"Bilibili refused the ranking: {data.get('message')}")
        found = []
        for show in data["result"]["list"][:PER_SOURCE]:
            episode = (show.get("new_ep") or {}).get("index_show", "")
            plays = (show.get("icon_font") or {}).get("text", "")
            found.append(find(show["url"], show["title"], site="Bilibili", kind="page",
                              text=" · ".join(filter(None, [episode, show.get("rating", ""),
                                                           f"{plays} 播放" if plays else ""])),
                              picture=_https(show.get("ss_horizontal_cover") or show.get("cover", ""))))
        return found
    return fetch


# ---------------------------------------------------------------- film and books

async def _open_library(http: httpx.AsyncClient) -> list[Find]:
    """The books most opened on Open Library today."""
    data = await get_json(http, "https://openlibrary.org/trending/daily.json", limit=PER_SOURCE)
    found = []
    for work in data["works"]:
        authors = ", ".join(work.get("author_name") or [])
        year = work.get("first_publish_year")
        found.append(find(f"https://openlibrary.org{work['key']}", work["title"], site="Open Library", kind="page",
                          text=" · ".join(filter(None, [authors, f"first published {year}" if year else ""])),
                          author=authors,
                          picture=f"https://covers.openlibrary.org/b/id/{work['cover_i']}-L.jpg"
                          if work.get("cover_i") else ""))
    return found


# ---------------------------------------------------------------- art

async def _art_institute(http: httpx.AsyncClient) -> list[Find]:
    """The Art Institute of Chicago's highlighted works, a different page of them each day.
    About four hundred are highlighted, so thirty pages of twelve go round in a month."""
    page = datetime.date.today().toordinal() % 30 + 1
    data = await get_json(http, "https://api.artic.edu/api/v1/artworks/search",
                          **{"query[term][is_boosted]": "true", "limit": PER_SOURCE, "page": page,
                             "fields": "id,title,image_id,artist_title,date_display,medium_display,short_description"})
    found = []
    for work in data["data"]:
        found.append(find(f"https://www.artic.edu/artworks/{work['id']}", work["title"],
                          site="Art Institute of Chicago", kind="page",
                          text=work.get("short_description") or " · ".join(
                              filter(None, [work.get("date_display"), work.get("medium_display")])),
                          author=work.get("artist_title") or "",
                          picture=f"https://www.artic.edu/iiif/2/{work['image_id']}/full/843,/0/default.jpg"
                          if work.get("image_id") else ""))
    return found


# ---------------------------------------------------------------- feeds that need a touch

# Behance's feed writes each character it cannot encode as "?", so a title can open with "?? ".
_LOST = re.compile(r"(?:^|\s)\?{2,}(?=\s|$)")
# Douban's review feeds end each title with the work's name, which the text already gives.
_REVIEWED = re.compile(r"\s*\(评论: [^()]*\)$")
# Gcores leaves this where an embedded video or post was.
_EMBEDDED = "<内嵌内容，请前往机核查看>"


def _tidy_feed(key: str, name: tuple[str, str], about: tuple[str, str], url: str, site: str,
               default: str = "") -> Source:
    """A feed whose entries need tidying: a tracking query on every link, NPR's
    img src="undefined" when a story has no picture, and the marks Behance, Douban
    and Gcores leave in titles and text."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found = (await read_feed(http, url, site=site))[:PER_SOURCE]
        for f in found:
            f.item.title = _REVIEWED.sub("", _LOST.sub("", f.item.title)).strip()
            f.item.description = " ".join(f.item.description.replace(_EMBEDDED, " ").split())
            if "?utm_" in f.item.url or "?source=rss" in f.item.url:
                f.item.url = f.item.url.split("?", 1)[0]
            if f.picture.endswith("/undefined"):
                f.picture = ""
        return found

    return Source(key, name, about, "culture", fetch, default)


async def _itch(http: httpx.AsyncClient) -> list[Find]:
    """itch.io's featured games. The feed packs price, genre and platforms into the
    title as "[...]" tags; they read better as the line under it."""
    url = "https://itch.io/feed/featured.xml"
    found = (await read_feed(http, url, site="itch.io"))[:PER_SOURCE]
    for f in found:
        title, _, tags = f.item.title.partition(" [")
        if tags:
            f.item.title = title
            facts = " · ".join(tag.strip("[] ") for tag in ("[" + tags).split("] ["))
            f.item.description = " · ".join(filter(None, [facts, f.item.description]))
    return found


SOURCES: list[Source] = [
    # Video: a few well-known channels per interest, read through YouTube's public channel feeds.
    _youtube("youtube-science", ("YouTube · Science", "YouTube · 科学"),
             ("New videos from Veritasium, Kurzgesagt and 3Blue1Brown", "Veritasium、Kurzgesagt、3Blue1Brown 的新视频"),
             ("UCHnyfMqiRRG1u-2MsSQLbXA", "UCsXVk37bltHxD1rDPwtNM8Q", "UCYO_jab_esuFRV4b17AJtAw"), default="en"),
    _youtube("youtube-tech", ("YouTube · Tech", "YouTube · 科技"),
             ("New videos from Marques Brownlee, Linus Tech Tips and Fireship",
              "Marques Brownlee、Linus Tech Tips、Fireship 的新视频"),
             ("UCBJycsmduvYEL83R_U4JriQ", "UCXuqSBlHAE6Xw-yeJA0Tunw", "UCsBjURrPoezykLs9EqgamOA"), default="en"),
    _youtube("youtube-music", ("YouTube · Live music", "YouTube · 现场音乐"),
             ("Live sessions from COLORS and KEXP", "COLORS、KEXP 的现场演出"),
             ("UC2Qw1dzXDBAZPwS7zm37g8g", "UC3I2GFN_F8WudD_2jUZbojA")),
    _youtube("youtube-cooking", ("YouTube · Cooking", "YouTube · 烹饪"),
             ("New videos from Joshua Weissman, Babish and Bon Appétit",
              "Joshua Weissman、Babish、Bon Appétit 的新视频"),
             ("UChBEbMKI1eCcejTtmI32UEw", "UCJHA_jMfCvEnv-3kRjTCQXw", "UCbpMy0Fg74eXXkvxJrtEn3w")),
    _youtube("youtube-film", ("YouTube · Film", "YouTube · 电影"),
             ("Film essays from Nerdwriter and Every Frame a Painting, and new trailers",
              "Nerdwriter、Every Frame a Painting 的影评视频和新预告片"),
             ("UCJkMlOu7faDgqh4PfzbpLdg", "UCjFqcJQXGZ6T6sxyFB-5i6A", "UCi8e0iOVk1fEOogdfu4YgfA")),
    _youtube("youtube-games", ("YouTube · Games", "YouTube · 游戏"),
             ("New videos from Game Maker's Toolkit, noclip and IGN", "Game Maker's Toolkit、noclip、IGN 的新视频"),
             ("UCqJ-Xo29CKyLTjn6z2XwYAw", "UC0fDG3byEcMtbOqPMymDNbw", "UCKy1dAqELo0zrOtPkf0eTMw")),
    _youtube("youtube-news", ("YouTube · World news", "YouTube · 国际新闻"),
             ("News videos from BBC News, Al Jazeera English and DW News", "BBC、半岛电视台英语台、德国之声的新闻视频"),
             ("UC16niRr50-MSBwiO3YDb3RA", "UCNye-wNBqNL5ZzHSJj3l8Bg", "UCknLrEdhRCp1aegoMqRaCZg")),
    _youtube("youtube-docs", ("YouTube · Documentaries", "YouTube · 纪录片"),
             ("Documentaries from DW Documentary, Real Stories and BBC Earth",
              "DW Documentary、Real Stories、BBC Earth 的纪录片"),
             ("UCW39zufHfsuGgpLviKh297Q", "UCu4XcDBdnZkV6-5z2f16M0g", "UCwmZiChSryoWQCZMIQezgTg")),

    # Music. Apple's public charts (rss.applemarketingtools.com) timed out or answered 502 on most tries from here.
    _netease("netease-hot", ("NetEase Music · Hot songs", "网易云音乐 · 热歌榜"),
             ("The songs most played on NetEase Cloud Music this week", "网易云音乐本周播放最多的歌曲"), 3778678,
             default="zh"),
    _netease("netease-new", ("NetEase Music · New songs", "网易云音乐 · 新歌榜"),
             ("The new songs rising on NetEase Cloud Music", "网易云音乐上正在上升的新歌"), 3779629),
    feed_source("pitchfork", ("Pitchfork · Album reviews", "Pitchfork · 专辑乐评"),
                ("Pitchfork's newest album reviews", "Pitchfork 最新的专辑乐评"), "culture",
                "https://pitchfork.com/feed/feed-album-reviews/rss", site="Pitchfork"),
    feed_source("bandcamp-daily", ("Bandcamp Daily", "Bandcamp Daily"),
                ("Bandcamp's guides to new and overlooked music", "Bandcamp 编辑推荐的新音乐和冷门好歌"), "culture",
                "https://daily.bandcamp.com/feed", site="Bandcamp Daily"),
    _tidy_feed("npr-music", ("NPR Music", "NPR 音乐"),
               ("Tiny Desk concerts, new music and reviews from NPR", "NPR 的 Tiny Desk 现场、新歌和乐评"),
               "https://feeds.npr.org/1039/rss.xml", "NPR Music", default="en"),

    # Games.
    _steam("steam-top", ("Steam · Top sellers", "Steam · 热销"),
           ("What sells most on Steam right now", "Steam 上此刻卖得最多的游戏"), "top_sellers"),
    _steam("steam-deals", ("Steam · Specials", "Steam · 特惠"),
           ("Games on sale on Steam's front page", "Steam 首页正在打折的游戏"), "specials"),
    Source("itch-featured", ("itch.io · Featured", "itch.io · 精选"),
           ("Indie games itch.io features", "itch.io 编辑精选的独立游戏"), "culture", _itch),
    feed_source("ign", ("IGN", "IGN"), ("Game, film and TV news and reviews from IGN", "IGN 的游戏、影视新闻和评测"),
                "culture", "https://feeds.ign.com/ign/all", site="IGN"),
    feed_source("polygon", ("Polygon", "Polygon"), ("Games and pop culture from Polygon", "Polygon 的游戏和流行文化报道"),
                "culture", "https://www.polygon.com/rss/index.xml", site="Polygon", default="en"),
    feed_source("rps", ("Rock Paper Shotgun", "Rock Paper Shotgun"),
                ("PC games news, reviews and finds", "PC 游戏的新闻、评测和发现"), "culture",
                "https://www.rockpapershotgun.com/feed", site="Rock Paper Shotgun"),
    feed_source("eurogamer", ("Eurogamer", "Eurogamer"), ("Games news and reviews from Eurogamer", "Eurogamer 的游戏新闻和评测"),
                "culture", "https://www.eurogamer.net/feed", site="Eurogamer"),
    _tidy_feed("pcgamer", ("PC Gamer", "PC Gamer"), ("PC games news, guides and reviews", "PC 游戏新闻、攻略和评测"),
               "https://www.pcgamer.com/rss/", "PC Gamer"),
    feed_source("nintendolife", ("Nintendo Life", "Nintendo Life"), ("Everything Nintendo: news and reviews", "任天堂相关的新闻和评测"),
                "culture", "https://www.nintendolife.com/feeds/latest", site="Nintendo Life"),
    _tidy_feed("gcores", ("Gcores", "机核"), ("Articles on games and culture from Gcores", "机核的游戏与文化文章"),
               "https://www.gcores.com/rss", "机核"),
    feed_source("yystv", ("YYSTV", "游研社"), ("Game stories and news from YYSTV", "游研社的游戏故事和资讯"),
                "culture", "https://www.yystv.cn/rss/feed", site="游研社"),

    # Anime.
    Source("bangumi-today", ("Bangumi · Airing today", "Bangumi · 今日放送"),
           ("Anime airing today, most watched first", "今天更新的动画，按在看人数排序"), "culture", _bangumi_today),
    Source("bangumi-season", ("Bangumi · This season", "Bangumi · 本季排行"),
           ("This season's anime ranked by Bangumi members", "本季新番的 Bangumi 排名"), "culture", _bangumi_season),
    Source("bilibili-anime", ("Bilibili · Anime ranking", "哔哩哔哩 · 番剧排行"),
           ("The most watched anime on Bilibili these three days", "B 站近三日播放最多的番剧"), "culture",
           _bilibili_shows(1)),
    Source("bilibili-guochuang", ("Bilibili · Chinese animation", "哔哩哔哩 · 国创排行"),
           ("The most watched Chinese animation on Bilibili these three days", "B 站近三日播放最多的国产动画"), "culture",
           _bilibili_shows(4)),
    Source("bilibili-docs", ("Bilibili · Documentaries", "哔哩哔哩 · 纪录片排行"),
           ("Documentaries on history, nature, food and science most watched on Bilibili", "B 站近三日播放最多的纪录片：历史、自然、美食与科学"),
           "culture", _bilibili_shows(3)),
    Source("bilibili-films", ("Bilibili · Films", "哔哩哔哩 · 电影排行"),
           ("The films most watched on Bilibili these three days", "B 站近三日播放最多的电影"), "culture",
           _bilibili_shows(2)),
    Source("bilibili-tv", ("Bilibili · TV series", "哔哩哔哩 · 电视剧排行"),
           ("The TV series most watched on Bilibili these three days", "B 站近三日播放最多的电视剧"), "culture",
           _bilibili_shows(5)),
    Source("anilist-trending", ("AniList · Trending", "AniList · 热门"),
           ("Anime trending on AniList now", "AniList 上正在流行的动画"), "culture", _anilist),
    feed_source("ann", ("Anime News Network", "Anime News Network"),
                ("Anime and manga news", "动画和漫画新闻"), "culture",
                "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=w", site="Anime News Network"),

    # Film and TV.
    _tidy_feed("douban-film-reviews", ("Douban · Film reviews", "豆瓣 · 最受欢迎影评"),
               ("The most liked film reviews on Douban", "豆瓣上最受欢迎的影评"),
               "https://www.douban.com/feed/review/movie", "豆瓣电影"),
    feed_source("rottentomatoes", ("Rotten Tomatoes", "烂番茄"),
                ("What to watch: guides and lists from Rotten Tomatoes", "烂番茄的观影指南和片单"), "culture",
                "https://editorial.rottentomatoes.com/feed/", site="Rotten Tomatoes"),
    feed_source("variety", ("Variety", "Variety"), ("Film, TV and music industry news", "影视和音乐行业新闻"),
                "culture", "https://variety.com/feed/", site="Variety"),
    feed_source("guardian-film", ("The Guardian · Film", "卫报 · 电影"), ("Film news and reviews from the Guardian", "卫报的电影新闻和影评"),
                "culture", "https://www.theguardian.com/film/rss", site="The Guardian"),

    # Books.
    _tidy_feed("douban-book-reviews", ("Douban · Book reviews", "豆瓣 · 最受欢迎书评"),
               ("The most liked book reviews on Douban", "豆瓣上最受欢迎的书评"),
               "https://www.douban.com/feed/review/book", "豆瓣读书"),
    Source("openlibrary-trending", ("Open Library · Trending", "Open Library · 今日热门"),
           ("Books most opened on Open Library today", "Open Library 今天被打开最多的书"), "culture", _open_library),
    feed_source("lithub", ("Literary Hub", "Literary Hub"), ("Essays, excerpts and news about books", "关于书的随笔、选段和新闻"),
                "culture", "https://lithub.com/feed/", site="Literary Hub"),
    feed_source("guardian-books", ("The Guardian · Books", "卫报 · 读书"), ("Book reviews and news from the Guardian", "卫报的书评和出版新闻"),
                "culture", "https://www.theguardian.com/books/rss", site="The Guardian"),
    feed_source("reactor", ("Reactor", "Reactor"), ("Science fiction and fantasy: books, film and fiction", "科幻和奇幻：书、影视和小说"),
                "culture", "https://reactormag.com/feed/", site="Reactor"),

    # Art and design.
    feed_source("colossal", ("Colossal", "Colossal"), ("Art and visual culture from Colossal", "Colossal 的艺术与视觉文化"),
                "culture", "https://www.thisiscolossal.com/feed/", site="Colossal", default="en"),
    Source("artic-highlights", ("Art Institute of Chicago", "芝加哥艺术博物馆"),
           ("A few of the museum's highlighted works each day", "每天几件馆藏精品"), "culture", _art_institute),
    _tidy_feed("behance", ("Behance · Featured", "Behance · 精选"), ("Design projects featured on Behance", "Behance 精选的设计作品"),
               "https://www.behance.net/feeds/projects", "Behance"),
    feed_source("dezeen", ("Dezeen", "Dezeen"), ("Architecture and design news", "建筑与设计新闻"),
                "culture", "https://www.dezeen.com/feed/", site="Dezeen"),
    feed_source("designboom", ("designboom", "designboom"), ("Architecture, design and art", "建筑、设计与艺术"),
                "culture", "https://www.designboom.com/feed/", site="designboom"),

    # Food and other pastimes.
    feed_source("bonappetit", ("Bon Appétit", "Bon Appétit"), ("New recipes and food stories", "新菜谱和美食故事"),
                "culture", "https://www.bonappetit.com/feed/rss", site="Bon Appétit"),
    feed_source("smittenkitchen", ("Smitten Kitchen", "Smitten Kitchen"), ("Home cooking recipes from Deb Perelman", "Deb Perelman 的家常菜谱"),
                "culture", "https://smittenkitchen.com/feed/", site="Smitten Kitchen"),
    _tidy_feed("thekitchn", ("The Kitchn", "The Kitchn"), ("Recipes and kitchen tips", "菜谱和厨房技巧"),
               "https://www.thekitchn.com/main.rss", "The Kitchn"),
    feed_source("openculture", ("Open Culture", "Open Culture"), ("Free courses, films, books and culture finds", "免费的课程、电影、书和文化发现"),
                "culture", "https://www.openculture.com/feed", site="Open Culture"),
    _tidy_feed("hypebeast", ("Hypebeast", "Hypebeast"), ("Fashion, sneakers, art and design", "潮流、球鞋、艺术与设计"),
               "https://hypebeast.com/feed", "Hypebeast"),

    # Medium's public tag feeds: writers on the subjects the press above covers least.
    *[_tidy_feed(f"medium-{tag}", (f"Medium · {en}", f"Medium · {zh}"), (about_en, about_zh),
                 f"https://medium.com/feed/tag/{tag}", "Medium")
      for tag, en, zh, about_en, about_zh in (
          ("travel", "Travel", "旅行", "Travel stories and guides from Medium writers", "Medium 作者的旅行故事与攻略"),
          ("history", "History", "历史", "History essays from Medium writers", "Medium 作者的历史随笔"),
          ("photography", "Photography", "摄影", "Photography essays and tips from Medium writers", "Medium 作者的摄影随笔与技巧"),
          ("fitness", "Fitness", "健身", "Fitness and training from Medium writers", "Medium 作者的健身与训练文章"),
          ("personal-finance", "Personal finance", "个人理财", "Money and personal finance from Medium writers",
           "Medium 作者的理财文章"))],
]

SOURCES = written_in("zh", SOURCES, {"netease-hot", "netease-new", "gcores", "yystv", "bangumi-today", "bangumi-season",
                                     "bilibili-anime", "bilibili-guochuang", "bilibili-docs", "bilibili-films",
                                     "bilibili-tv", "douban-film-reviews",
                                     "douban-book-reviews"})
# The Chinese-language ones are mainland sites; the rest are read worldwide.
SOURCES = local_to("CN", SOURCES, {s.key for s in SOURCES if s.lang == "zh"})
