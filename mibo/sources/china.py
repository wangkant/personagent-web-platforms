"""Public places on the Chinese-language internet: hot lists, rankings, feeds.

Every address here answers a visitor without a key, a sign-in or a cookie.
Places that refused a plain visitor when this was written (Weibo's hot search
sends one to a visitor check, Douyin answers empty, Qidian and 什么值得买 answer
with a script challenge, Bilibili's 每周必看 and its search with a risk-control
code) are not here, nor are those whose robots.txt turns programs away
(Hupu's boards).
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from .base import PER_SOURCE, Find, Source, find, get_json, get_text, local_to, plain, read_feed, written_in

# The places below date things in Beijing time.
_CST = timezone(timedelta(hours=8))


def _stamp(seconds: object) -> str:
    return datetime.fromtimestamp(int(seconds), _CST).isoformat() if seconds else ""


def _https(url: str) -> str:
    """Covers and pages that are written http:// or //, asked for over https."""
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return "https://" + url[7:] if url.startswith("http://") else url


def _bare(url: str) -> str:
    """A link without its tracking query (utm_*, f=rss) or reply anchor, so a page keeps one address."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _feed(key: str, name: tuple[str, str], about: tuple[str, str], group: str, url: str, *,
          default: str = "", bare: bool = True, cut: str = "") -> Source:
    """A feed whose links are tidied: https, and no tracking query unless the query is the page (`bare=False`).
    `cut` is boilerplate the feed opens every summary with."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found = (await read_feed(http, url, site=name[1]))[:PER_SOURCE]
        for one in found:
            link = _https(one.item.url)
            one.item.url = _bare(link) if bare else link
            if cut:
                one.item.description = re.sub(cut, "", one.item.description).strip()
        return found

    return Source(key, name, about, group, fetch, default)


# ---------------------------------------------------------------- Bilibili

def _bili(video: dict) -> Find:
    desc = video.get("desc") or ""
    return find(f"https://www.bilibili.com/video/{video['bvid']}", video["title"], site="哔哩哔哩",
                text="" if desc.strip() == "-" else desc, author=(video.get("owner") or {}).get("name", ""),
                # Bilibili's image host cuts a cover to size when asked; the full one can pass half a megabyte.
                published=_stamp(video.get("pubdate")), picture=_https(video.get("pic", "")) + "@640w_360h_1c.jpg",
                length=video.get("duration") or None)


async def _bili_hot(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    data = await get_json(http, "https://api.bilibili.com/x/web-interface/popular", ps=limit, pn=1)
    return [_bili(v) for v in data["data"]["list"]][:limit]


async def _bili_articles(http: httpx.AsyncClient) -> list[Find]:
    # The 专栏 ranking: Bilibili's written posts, one list whatever category is asked for.
    data = await get_json(http, "https://api.bilibili.com/x/article/rank/list", cid=1)
    if data.get("code") != 0:
        raise ValueError(f"Bilibili refused the ranking: {data.get('message')}")
    return [find(f"https://www.bilibili.com/read/cv{post['id']}", post["title"], site="哔哩哔哩专栏", kind="post",
                 text=post.get("summary", ""), author=(post.get("author") or {}).get("name", ""),
                 published=_stamp(post.get("publish_time")),
                 picture=_https(next(iter(post.get("image_urls") or []), "") or post.get("banner_url", "")))
            for post in data["data"]][:PER_SOURCE]


async def _acfun_rank(http: httpx.AsyncClient) -> list[Find]:
    data = await get_json(http, "https://www.acfun.cn/rest/pc-direct/rank/channel", channelId="", subChannelId="",
                          rankLimit=PER_SOURCE, rankPeriod="DAY")
    if data.get("result") != 0:
        raise ValueError(f"AcFun refused the ranking: {data.get('error_msg')}")
    return [find(f"https://www.acfun.cn/v/ac{video['contentId']}", video["contentTitle"], site="AcFun", kind="video",
                 text=video.get("contentDesc", ""), author=video.get("userName", ""),
                 published=_stamp(int(video.get("contributeTime") or 0) // 1000), picture=_https(video.get("coverUrl", "")),
                 length=int(video.get("durationMillis") or 0) // 1000 or None)
            for video in data["rankList"]][:PER_SOURCE]


async def _bili_precious(http: httpx.AsyncClient) -> list[Find]:
    data = await get_json(http, "https://api.bilibili.com/x/web-interface/popular/precious")
    return [_bili(v) for v in data["data"]["list"]][:PER_SOURCE]


def _bili_rank(rid: int):
    async def fetch(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
        data = await get_json(http, "https://api.bilibili.com/x/web-interface/ranking/v2", rid=rid, type="all")
        return [_bili(v) for v in data["data"]["list"]][:limit]
    return fetch


# Bilibili's current partition ids. The old ones (36, 188, ...) still answer, with lists frozen in early 2025.
_BILI_PARTITIONS = (
    ("tech", 1012, ("Tech", "科技数码"), ("tech and gadget videos", "科技数码视频")),
    ("knowledge", 1010, ("Knowledge", "知识"), ("science, history, finance and explainers", "科普、人文、财经与讲解视频")),
    ("game", 1008, ("Games", "游戏"), ("game videos and esports", "游戏视频与电竞")),
    ("anime", 1005, ("Anime", "二次元"), ("animation, fan works and ACG culture", "动画、同人与二次元视频")),
    ("music", 1003, ("Music", "音乐"), ("covers, originals and performances", "翻唱、原创与演奏")),
    ("film", 1001, ("Film & TV", "影视"), ("film and TV commentary and edits", "影视解说与剪辑")),
    ("food", 1020, ("Food", "美食"), ("cooking, tasting and food trips", "美食制作、测评与探店")),
    ("sports", 1018, ("Sports", "体育运动"), ("sports, fitness and match highlights", "体育、健身与赛事集锦")),
    # Checked signed out on 2026-11-19 (1009, news, answers -400): interests the other zh sources barely bring.
    ("ai", 1011, ("AI", "人工智能"), ("AI explainers and tools", "人工智能讲解与工具")),
    ("cars", 1013, ("Cars", "汽车"), ("cars, EVs and driving", "汽车、新能源车与驾驶")),
    ("fitness", 1017, ("Fitness", "健身"), ("workouts and training", "健身与训练")),
    ("travel", 1022, ("Travel", "旅游出行"), ("travel vlogs and trips", "旅行视频与出行")),
    ("animals", 1024, ("Animals", "动物"), ("pets, wildlife and animal videos", "宠物、野生动物与动物视频")),
    ("parenting", 1025, ("Parenting", "亲子"), ("parenting, pregnancy and kids", "育儿、孕产与亲子")),
    ("health", 1026, ("Health", "健康"), ("health, sleep and mind", "健康、睡眠与心理")),
)


# ---------------------------------------------------------------- communities

async def _v2ex_hot(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    topics = await get_json(http, "https://www.v2ex.com/api/topics/hot.json")
    return [find(t["url"], t["title"], site="V2EX", kind="post", text=t.get("content", ""),
                 author=t["member"]["username"], published=_stamp(t.get("created")))
            for t in topics][:limit]


async def _zhihu_hot(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    # api.zhihu.com answers a visitor; www.zhihu.com's hot list asks for a sign-in.
    data = await get_json(http, "https://api.zhihu.com/topstory/hot-list", limit=limit)
    found = []
    for card in data["data"]:
        question = card["target"]
        thumbs = [c.get("thumbnail", "") for c in card.get("children") or [] if c.get("thumbnail")]
        # A bare thumbnail is the original upload; the _720w name is the same picture made smaller.
        picture = re.sub(r"\.(png|jpe?g)$", "_720w.jpg", thumbs[0]) if thumbs else ""
        found.append(find(f"https://www.zhihu.com/question/{question['id']}", question["title"], site="知乎",
                          kind="post", text=question.get("excerpt", ""), published=_stamp(question.get("created")),
                          picture=picture))
    return found[:limit]


_IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"')
_TIEBA = re.compile(r'<li class="topic-top-item"><img src="([^"]*)".*?<a href="([^"]+)"[^>]*class="topic-text">([^<]+)</a>'
                    r'.*?<p class="topic-top-item-desc">([^<]*)</p>', re.S)


async def _tieba_hot(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    # Tieba's hot topics page: each topic with its line and picture, readable signed out.
    page = await get_text(http, "https://tieba.baidu.com/hottopic/browse/topicList", res_type=1)
    return [find(html.unescape(url), title, site="百度贴吧", kind="page", text=about, picture=html.unescape(cover))
            for cover, url, title, about in _TIEBA.findall(page)][:limit]


# ---------------------------------------------------------------- hot lists

async def _baidu_hot(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    data = await get_json(http, "https://top.baidu.com/api/board", platform="pc", tab="realtime")
    card = data["data"]["cards"][0]
    return [find(entry["url"], entry["word"], site="百度热搜", kind="page", text=entry.get("desc", ""),
                 picture=entry.get("img", ""))
            for entry in card.get("topContent", []) + card["content"]][:limit]


async def _toutiao_hot(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    data = await get_json(http, "https://www.toutiao.com/hot-event/hot-board/", origin="toutiao_pc")
    return [find(_bare(entry["Url"]), entry["Title"], site="今日头条", picture=(entry.get("Image") or {}).get("url", ""))
            for entry in data["data"]][:limit]


# ---------------------------------------------------------------- tech and science sites

async def _sspai(http: httpx.AsyncClient) -> list[Find]:
    # The site's own front-page list: the feed has no covers.
    data = await get_json(http, "https://sspai.com/api/v1/article/index/page/get", limit=PER_SOURCE, offset=0)
    # Banners are full-size photographs; the image host shrinks one when asked.
    return [find(f"https://sspai.com/post/{post['id']}", post["title"], site="少数派", text=post.get("summary", ""),
                 author=post["author"]["nickname"], published=_stamp(post.get("released_time")),
                 picture=f"https://cdnfile.sspai.com/{post['banner']}?imageMogr2/thumbnail/640x" if post.get("banner") else "")
            for post in data["data"]][:PER_SOURCE]


async def _huxiu(http: httpx.AsyncClient) -> list[Find]:
    # Huxiu's feed address no longer answers; this is the list its article page loads.
    answer = await http.post("https://api-article.huxiu.com/web/article/articleList",
                             data={"platform": "www", "pagesize": str(PER_SOURCE)})
    answer.raise_for_status()
    return [find(f"https://www.huxiu.com/article/{post['aid']}.html", post["title"], site="虎嗅",
                 text=post.get("summary", ""), author=(post.get("user_info") or {}).get("username", ""),
                 # pic_path is the cut-down cover; the original can be megabytes.
                 published=_stamp(post.get("dateline")), picture=post.get("pic_path", ""))
            for post in answer.json()["data"]["dataList"]][:PER_SOURCE]


async def _guokr(http: httpx.AsyncClient) -> list[Find]:
    posts = await get_json(http, "https://www.guokr.com/beta/proxy/science_api/articles",
                           retrieve_type="by_category", page=1)
    # No covers: Guokr's image host turns away requests from outside its own pages.
    return [find(f"https://www.guokr.com/article/{post['id']}/", post["title"], site="果壳",
                 text=post.get("summary", ""), author=(post.get("author") or {}).get("nickname", ""),
                 published=post.get("date_published", ""))
            for post in posts][:PER_SOURCE]


async def _jiqizhixin(http: httpx.AsyncClient) -> list[Find]:
    # The feed address now redirects to a page about data services; the article list is open.
    data = await get_json(http, "https://www.jiqizhixin.com/api/article_library/articles.json",
                          sort="time", page=1, per=PER_SOURCE)
    found = []
    for post in data["articles"]:
        when = post.get("publishedAt", "")
        try:
            when = datetime.strptime(when, "%Y/%m/%d %H:%M").replace(tzinfo=_CST).isoformat()
        except ValueError:
            pass
        found.append(find(f"https://www.jiqizhixin.com/articles/{post['slug']}", post["title"], site="机器之心",
                          text=post.get("content", ""), author=post.get("author", ""), published=when,
                          picture=post.get("coverImageUrl", "")))
    return found[:PER_SOURCE]


# ---------------------------------------------------------------- news

async def _thepaper(http: httpx.AsyncClient) -> list[Find]:
    data = await get_json(http, "https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar")
    found = []
    for news in data["data"]["hotNews"]:
        picture = news.get("pic", "")
        found.append(find(f"https://www.thepaper.cn/newsDetail_forward_{news['contId']}", news["name"],
                          site="澎湃新闻", author=(news.get("nodeInfo") or {}).get("name", ""),
                          published=_stamp((news.get("pubTimeLong") or 0) // 1000),
                          # Lead stories carry a generic banner instead of their own picture.
                          picture="" if "/client/" in picture else picture))
    return found[:PER_SOURCE]


_XINHUA = re.compile(r"""<a href=['"](https://www\.news\.cn/[\w/]+/(\d{4})(\d{2})(\d{2})/[0-9a-f]{32}/c\.html)['"]"""
                     r"""[^>]*>([^<]{6,})</a>""")


async def _xinhua(http: httpx.AsyncClient) -> list[Find]:
    # The old feed addresses stopped in 2022; the front page lists today's stories in order.
    page = await get_text(http, "https://www.news.cn/")
    found, seen = [], set()
    for url, year, month, day, title in _XINHUA.findall(page):
        if url not in seen:
            seen.add(url)
            found.append(find(url, title, site="新华网", published=f"{year}-{month}-{day}"))
    return found[:PER_SOURCE]


# ---------------------------------------------------------------- books

_BOOK = re.compile(r'<h2 class="clearfix">\s*<a class="fleft" href="([^"]+)">([^<]+)</a>'
                   r'.*?<p class="subject-abstract color-gray">(.*?)</p>(?:.*?<span class="font-small fleft">([\d.]+)</span>)?',
                   re.S)


async def _douban_new_books(http: httpx.AsyncClient) -> list[Find]:
    page = await get_text(http, "https://book.douban.com/latest")
    found = []
    for block in page.split('<li class="media clearfix">')[1:]:
        book = _BOOK.search(block)
        if not book:
            continue
        url, title, facts, score = book.groups()
        cover = _IMG_SRC.search(block)
        found.append(find(url, title, site="豆瓣读书", text=plain(facts) + (f" ｜ 豆瓣 {score}" if score else ""),
                          # The list shows the small cover; the same picture comes larger under /m/.
                          picture=cover.group(1).replace("/subject/s/", "/subject/m/") if cover else ""))
    return found[:PER_SOURCE]


_JJWXC_AUTHOR = re.compile(r'<a title="([^"]*)"\s+href="oneauthor\.php\?authorid=\d+"')
_JJWXC_BOOK = re.compile(r'<a title="([^"]*)"[^>]*?href="onebook\.php\?novelid=(\d+)"\s+rel="([^"]*)"', re.S)


async def _jjwxc(http: httpx.AsyncClient) -> list[Find]:
    # orderstr 5 is the monthly list; the all-time one (7) barely moves.
    answer = await http.get("https://www.jjwxc.net/topten.php", params={"orderstr": 5})
    answer.raise_for_status()
    page = answer.content.decode("gb18030", "replace")
    found = []
    for row in page.split('<tr bgcolor="#eefaee" onMouseOver')[1:]:
        book, author = _JJWXC_BOOK.search(row), _JJWXC_AUTHOR.search(row)
        if book:
            found.append(find(f"https://www.jjwxc.net/onebook.php?novelid={book.group(2)}", book.group(1),
                              site="晋江文学城", text=book.group(3).replace("<br>", " "),
                              author=author.group(1) if author else ""))
    return found[:PER_SOURCE]


# Chinese places that tech.py, world.py and culture.py already list (IT之家, 爱范儿, 量子位,
# 开源中国, InfoQ, cnBeta, Solidot, 掘金, 阮一峰, BBC/纽约时报/法广 中文, 豆瓣热映 and 影评书评, 机核, 游研社)
# are left to them so a person does not see one place twice.
SOURCES: list[Source] = [
    # video
    Source("bili-hot", ("Bilibili Popular", "B站热门"), ("what is popular on Bilibili right now", "B站当下的热门视频"),
           "video", _bili_hot, default="zh"),
    Source("bili-rank", ("Bilibili Ranking", "B站排行榜"), ("Bilibili's all-site top videos of the last days", "B站全站排行榜"),
           "video", _bili_rank(0)),
    *[Source(f"bili-rank-{slug}", (f"Bilibili Ranking: {en}", f"B站排行榜·{zh}"), (f"top {about_en} on Bilibili", f"B站{about_zh}排行"),
             "video", _bili_rank(rid), default="zh" if slug == "knowledge" else "")
      for slug, rid, (en, zh), (about_en, about_zh) in _BILI_PARTITIONS],
    Source("bili-precious", ("Bilibili Must-Watch", "B站入站必刷"), ("the classics Bilibili says every newcomer should see", "B站官方选出的入站必刷视频"),
           "video", _bili_precious),
    Source("acfun-rank", ("AcFun Daily Ranking", "AcFun 日榜"), ("AcFun's most watched videos today: anime, games and fun", "AcFun 今日最热视频：动画、游戏与趣味"),
           "video", _acfun_rank),
    # communities
    Source("bili-articles", ("Bilibili Articles", "B站专栏热门"), ("Bilibili's most read written posts", "B站专栏阅读最多的文章"),
           "community", _bili_articles),
    Source("tieba-hot", ("Tieba Hot Topics", "贴吧热议"), ("the topics Baidu Tieba is talking about: games, shows and life", "百度贴吧正在热议的话题：游戏、影视与生活"),
           "community", _tieba_hot),
    Source("zhihu-hot", ("Zhihu Hot", "知乎热榜"), ("the questions everyone on Zhihu is answering", "知乎上大家正在讨论的问题"),
           "community", _zhihu_hot, default="zh"),
    Source("v2ex-hot", ("V2EX Hot", "V2EX 最热"), ("today's most-discussed V2EX topics", "V2EX 今日最热主题"),
           "community", _v2ex_hot),
    _feed("v2ex-tech", ("V2EX Tech", "V2EX 技术"), ("new topics in V2EX's tech tab", "V2EX 技术版块的新主题"),
          "community", "https://www.v2ex.com/feed/tab/tech.xml"),
    _feed("v2ex-creative", ("V2EX Creative", "V2EX 创意"), ("things people made and share on V2EX", "V2EX 上大家做的东西"),
          "community", "https://www.v2ex.com/feed/tab/creative.xml"),
    _feed("v2ex-play", ("V2EX Fun", "V2EX 好玩"), ("games, gadgets and fun on V2EX", "V2EX 上的游戏、玩物与趣事"),
          "community", "https://www.v2ex.com/feed/tab/play.xml"),
    _feed("v2ex-jobs", ("V2EX Jobs", "V2EX 酷工作"), ("job posts on V2EX", "V2EX 上的招聘帖"),
          "community", "https://www.v2ex.com/feed/tab/jobs.xml"),
    # hot lists
    Source("baidu-hot", ("Baidu Hot Searches", "百度热搜"), ("what China is searching for now", "全网正在搜的热点"),
           "news", _baidu_hot, default="zh"),
    Source("toutiao-hot", ("Toutiao Hot", "今日头条热榜"), ("Toutiao's trending news events", "今日头条的热门事件"),
           "news", _toutiao_hot),
    # news
    Source("thepaper-hot", ("The Paper", "澎湃新闻"), ("The Paper's most-read stories", "澎湃新闻热门报道"),
           "news", _thepaper, default="zh"),
    Source("xinhua-home", ("Xinhua", "新华网"), ("the stories on Xinhua's front page", "新华网首页的报道"),
           "news", _xinhua),
    # China News Service's channel feeds: written news on the subjects the hot lists leave thin.
    _feed("chinanews-culture", ("China News: Culture", "中新网·文化"), ("culture, history, heritage, books and the arts", "文化、历史、非遗、读书与艺术"),
          "culture", "https://www.chinanews.com.cn/rss/culture.xml"),
    _feed("chinanews-life", ("China News: Living", "中新网·生活"), ("everyday life: food, travel, health and shopping", "日常生活：美食、旅游、健康与消费"),
          "news", "https://www.chinanews.com.cn/rss/life.xml"),
    _feed("chinanews-sports", ("China News: Sports", "中新网·体育"), ("football, basketball, the Olympics and fitness", "足球、篮球、奥运与健身"),
          "news", "https://www.chinanews.com.cn/rss/sports.xml"),
    _feed("chinanews-finance", ("China News: Finance", "中新网·财经"), ("the economy, markets, companies and money", "经济、市场、公司与理财"),
          "news", "https://www.chinanews.com.cn/rss/finance.xml"),
    _feed("chinanews-edu", ("China News: Education", "中新网·教育"), ("schools, universities, exams and parenting", "中小学、高校、考试与家庭教育"),
          "news", "https://www.chinanews.com.cn/rss/edu.xml"),
    _feed("chinanews-photo", ("China News: Photos", "中新网·图片"), ("the day in pictures: places, scenery and events", "图片新闻：各地风光与现场"),
          "news", "https://www.chinanews.com.cn/rss/photo.xml"),
    _feed("initium", ("Initium Media", "端传媒"), ("long-form reporting from Initium (some paywalled)", "端传媒的深度报道（部分需付费）"),
          "news", "https://theinitium.com/rss/"),
    _feed("ft-chinese", ("FT Chinese", "FT中文网"), ("Financial Times stories in Chinese", "FT中文网的财经报道"),
          "news", "https://www.ftchinese.com/rss/news"),
    # tech
    Source("sspai", ("SSPAI", "少数派"), ("apps, tools and better digital living", "高效工具与数字生活"),
           "tech", _sspai, default="zh"),
    _feed("36kr", ("36Kr", "36氪"), ("startups, funding and the business of tech", "创投、公司与科技商业"),
          "tech", "https://www.36kr.com/feed"),
    Source("huxiu", ("Huxiu", "虎嗅"), ("commentary on business and technology", "商业与科技评论"),
           "tech", _huxiu),
    Source("jiqizhixin", ("Synced", "机器之心"), ("AI research and industry news", "人工智能研究与产业报道"),
           "tech", _jiqizhixin),
    _feed("leiphone", ("Leiphone", "雷峰网"), ("AI, chips and the industry around them", "人工智能、芯片与产业"),
          "tech", "https://www.leiphone.com/feed"),
    _feed("tmtpost", ("TMTPost", "钛媒体"), ("business and markets in tech", "科技商业与资本"),
          "tech", "https://www.tmtpost.com/rss.xml"),
    _feed("appinn", ("Appinn", "小众软件"), ("small, useful software and how to use it", "好用的小众软件与技巧"),
          "tech", "https://www.appinn.com/feed/"),
    _feed("cnblogs", ("Cnblogs", "博客园"), ("posts from the cnblogs front page", "博客园首页的技术文章"),
          "tech", "https://feed.cnblogs.com/blog/sitehome/rss"),
    _feed("codingnow", ("Cloud Wind's Blog", "云风的 BLOG"), ("a game engine programmer's notes", "游戏引擎程序员的笔记"),
          "tech", "https://blog.codingnow.com/atom.xml"),
    _feed("zhangxinxu", ("Zhang Xinxu's Blog", "张鑫旭的博客"), ("front-end CSS and JS in depth", "前端 CSS 与 JS 深度文章"),
          "tech", "https://www.zhangxinxu.com/wordpress/feed/", cut=r"^by zhangxinxu from \S+ 本文可全文转载.*?商用请联系授权。"),
    # science
    Source("guokr", ("Guokr", "果壳"), ("popular science, explained with care", "认真讲清楚的科普"),
           "science", _guokr, default="zh"),
    # culture
    Source("douban-new-books", ("Douban: New Books", "豆瓣·新书速递"), ("new books worth a look", "值得一看的新书"),
           "culture", _douban_new_books),
    Source("jjwxc-rank", ("Jinjiang Monthly Ranking", "晋江月榜"), ("Jinjiang's top novels this month", "晋江文学城月度排行榜"),
           "culture", _jjwxc),
]

SOURCES = written_in("zh", SOURCES)
# Mainland platforms and press; Zaobao is Singapore's paper, Initium began in Hong Kong, FT Chinese is global.
SOURCES = local_to("CN", SOURCES, {s.key for s in SOURCES} - {"initium", "ft-chinese"})
SOURCES = local_to("HK", SOURCES, {"initium"})
