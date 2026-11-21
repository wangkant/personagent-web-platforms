"""Technology, programming, AI and science: the places people who build things read."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .base import PER_SOURCE, Find, Source, find, get_json, local_to, plain, read_feed, written_in

# A post that opens with its own <style> block reads as CSS once the tags are gone.
_CSS = re.compile(r"^(?:/\*|[.#]?[A-Za-z][\w-]*(?:[.#:\[][^{}\s]*)?(?:\s+[.#]?[A-Za-z][^{}\s]*)*\s*\{)")
# Upgraded to https: these sites serve it, their feeds still say http.
_PLAIN_HTTP = ("http://bair.berkeley.edu/", "http://www.ruanyifeng.com/")


def _tidy(found: list[Find], *, bare: bool, cut: str, covers: bool) -> list[Find]:
    """Tracking parameters off links (the whole query when `bare`), `cut` out of
    each summary."""
    for each in found:
        item = each.item
        if item.url.startswith(_PLAIN_HTTP):
            item.url = "https://" + item.url[len("http://"):]
        parts = urlsplit(item.url)
        if bare or "utm_" in parts.query:
            kept = [] if bare else [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
            item.url = urlunsplit(parts._replace(query=urlencode(kept)))
        if _CSS.match(item.description):
            item.description = ""
        if cut:
            item.description = re.sub(cut, "", item.description).strip()
        if not covers:
            each.picture = ""
    return found


def _feed(key: str, name: tuple[str, str], about: tuple[str, str], url: str, *, group: str = "tech",
          default: str = "", bare: bool = False, cut: str = "", covers: bool = True) -> Source:
    """A source that is one feed. `bare` drops a query the feed appends to every link;
    `cut` is boilerplate the feed puts in every summary; `covers` is False where the
    first image is a site icon."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found = _newest(await read_feed(http, url, site=name[0]))[:PER_SOURCE]
        return _tidy(found, bare=bare, cut=cut, covers=covers)

    return Source(key, name, about, group, fetch, default)


def _newest(found: list[Find]) -> list[Find]:
    """Newest first, for the few feeds (Product Hunt) that mix old entries in;
    left as the feed has them when a date will not read."""
    try:
        when = [datetime.fromisoformat(f.item.published) for f in found]
    except ValueError:
        return found
    return [f for _, f in sorted(zip(when, found), key=lambda pair: pair[0], reverse=True)]


def _iso(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


# Hacker News

async def _hn_top(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    # The official list gives the front page's order; one search gives every story on it.
    ranks = await get_json(http, "https://hacker-news.firebaseio.com/v0/topstories.json")
    found = await get_json(http, "https://hn.algolia.com/api/v1/search", tags="front_page", hitsPerPage=60)
    place = {story_id: n for n, story_id in enumerate(ranks)}
    hits = sorted(found["hits"], key=lambda hit: place.get(int(hit["objectID"]), len(place)))
    return [_hn(hit) for hit in hits[:limit]]


async def _hn_show(http: httpx.AsyncClient) -> list[Find]:
    found = await get_json(http, "https://hn.algolia.com/api/v1/search_by_date", tags="show_hn",
                           numericFilters="points>20", hitsPerPage=PER_SOURCE)
    return [_hn(hit) for hit in found["hits"]]


def _hn(hit: dict) -> Find:
    thread = f"https://news.ycombinator.com/item?id={hit['objectID']}"
    talk = f"{hit.get('points') or 0} points · {hit.get('num_comments') or 0} comments"
    text = plain(hit.get("story_text"))
    return find(hit.get("url") or thread, hit["title"], site="Hacker News", kind="article" if hit.get("url") else "post",
                text=f"{text} ({talk})" if text else talk, author=hit.get("author") or "",
                published=hit.get("created_at") or "")


# Programming communities

async def _lobsters(http: httpx.AsyncClient) -> list[Find]:
    stories = await get_json(http, "https://lobste.rs/hottest.json")
    found = []
    for story in stories[:PER_SOURCE]:
        tags = " ".join(f"#{tag}" for tag in story.get("tags") or [])
        found.append(find(story.get("url") or story["comments_url"], story["title"], site="Lobsters",
                          kind="article" if story.get("url") else "post",
                          text=story.get("description_plain") or tags, author=str(story.get("submitter_user") or ""),
                          published=story.get("created_at") or ""))
    return found


async def _devto(http: httpx.AsyncClient) -> list[Find]:
    articles = await get_json(http, "https://dev.to/api/articles", top=1, per_page=PER_SOURCE)
    return [find(a["url"], a["title"], site="DEV", text=a.get("description") or "",
                 author=(a.get("user") or {}).get("name") or "", published=a.get("published_at") or "",
                 picture=a.get("cover_image") or a.get("social_image") or "")
            for a in articles]


async def _stackoverflow(http: httpx.AsyncClient) -> list[Find]:
    # "week" ranks by this week's votes and answers; "hot" is mostly fresh, unanswered posts.
    found = await get_json(http, "https://api.stackexchange.com/2.3/questions", order="desc", sort="week",
                           site="stackoverflow", pagesize=PER_SOURCE, filter="withbody")
    return [find(q["link"], q["title"], site="Stack Overflow", kind="post", text=q.get("body") or "",
                 author=(q.get("owner") or {}).get("display_name") or "", published=_iso(q["creation_date"]))
            for q in found["items"]]


async def _juejin(http: httpx.AsyncClient, limit: int = PER_SOURCE) -> list[Find]:
    found = await get_json(http, "https://api.juejin.cn/content_api/v1/content/article_rank",
                           category_id=1, type="hot")
    picks = []
    for entry in found["data"][:limit]:
        content, counter = entry["content"], entry.get("content_counter") or {}
        picks.append(find(f"https://juejin.cn/post/{content['content_id']}", content["title"], site="掘金",
                          text=content.get("brief") or f"{counter.get('view', 0)} 阅读 · {counter.get('like', 0)} 赞",
                          author=(entry.get("author") or {}).get("name") or ""))
    return picks


# Hugging Face

async def _hf_papers(http: httpx.AsyncClient) -> list[Find]:
    papers = await get_json(http, "https://huggingface.co/api/daily_papers", limit=50)
    papers.sort(key=lambda entry: -(entry.get("paper") or {}).get("upvotes", 0))
    found = []
    for entry in papers[:PER_SOURCE]:
        paper = entry["paper"]
        names = [a.get("name", "") for a in paper.get("authors") or []]
        author = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
        found.append(find(f"https://huggingface.co/papers/{paper['id']}", paper["title"], site="Hugging Face",
                          text=paper.get("summary") or "", author=author, published=paper.get("publishedAt") or "",
                          picture=entry.get("thumbnail") or ""))
    return found


async def _hf_models(http: httpx.AsyncClient) -> list[Find]:
    models = await get_json(http, "https://huggingface.co/api/models", sort="trendingScore", limit=PER_SOURCE)
    found = []
    for model in models:
        ident = model["id"]
        line = " · ".join(bit for bit in (model.get("pipeline_tag") or "", f"{model.get('likes', 0):,} likes",
                                          f"{model.get('downloads', 0):,} downloads") if bit)
        found.append(find(f"https://huggingface.co/{ident}", ident, site="Hugging Face", kind="page", text=line,
                          author=ident.split("/")[0], published=model.get("createdAt") or "",
                          picture=f"https://cdn-thumbnails.huggingface.co/social-thumbnails/models/{ident}.png"))
    return found


async def _hf_spaces(http: httpx.AsyncClient) -> list[Find]:
    # The card holds a Space's own title and one-line description.
    spaces = await get_json(http, "https://huggingface.co/api/spaces", sort="trendingScore", limit=PER_SOURCE,
                            **{"expand[]": ["cardData", "likes", "createdAt", "author"]})
    found = []
    for space in spaces:
        ident, card = space["id"], space.get("cardData") or {}
        found.append(find(f"https://huggingface.co/spaces/{ident}", card.get("title") or ident, site="Hugging Face",
                          kind="page", text=card.get("short_description") or f"{space.get('likes', 0):,} likes",
                          author=space.get("author") or ident.split("/")[0], published=space.get("createdAt") or "",
                          picture=f"https://cdn-thumbnails.huggingface.co/social-thumbnails/spaces/{ident}.png"))
    return found


# Preprints

_ANNOUNCE = re.compile(r"^arXiv:\S+\s+Announce Type:\s*(\S+)\s+Abstract:\s*")


def _arxiv(subject: str, name: tuple[str, str], about: tuple[str, str]) -> Source:
    """Today's new papers in one arXiv subject, from its daily listing. The listing
    is empty on days arXiv does not announce (weekends), so a round then brings nothing."""
    url = f"https://rss.arxiv.org/rss/{subject}"

    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found = await read_feed(http, url, site="arXiv")
        fresh = []
        for each in found:
            announce = _ANNOUNCE.match(each.item.description)
            # Cross-lists and new versions repeat papers already seen in other subjects or days.
            if announce and announce.group(1) != "new":
                continue
            each.item.description = each.item.description[announce.end():] if announce else each.item.description
            fresh.append(each)
        return fresh[:PER_SOURCE]

    return Source(f"arxiv-{subject.lower().replace('.', '-')}", name, about, "science", fetch)


def _biorxiv(subject: str, name: tuple[str, str], about: tuple[str, str]) -> Source:
    """The newest bioRxiv preprints in one subject, from its open API."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        today = datetime.now(timezone.utc).date()
        span = f"https://api.biorxiv.org/details/biorxiv/{today - timedelta(days=3)}/{today}"
        first = await get_json(http, f"{span}/0", category=subject)
        total = int(first["messages"][0].get("total") or 0)
        # The API lists oldest first, a page at a time; the newest are on the last page.
        papers = first["collection"]
        if total > len(papers):
            papers = (await get_json(http, f"{span}/{max(0, total - 30)}", category=subject))["collection"]
        found = []
        for paper in reversed(papers):
            if str(paper.get("version")) != "1":
                continue
            names = [n.strip() for n in (paper.get("authors") or "").split(";") if n.strip()]
            author = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
            found.append(find(f"https://www.biorxiv.org/content/{paper['doi']}v1", paper["title"], site="bioRxiv",
                              text=paper.get("abstract") or "", author=author, published=paper.get("date") or ""))
        return found[:PER_SOURCE]

    return Source(f"biorxiv-{subject}", name, about, "science", fetch)


SOURCES: list[Source] = [
    # Where builders talk
    Source("hn-top", ("Hacker News", "Hacker News"),
           ("The Hacker News front page, in its order", "Hacker News 首页，按站内排序"), "tech", _hn_top, "en"),
    Source("hn-show", ("Show HN", "Show HN"),
           ("Projects people just showed on Hacker News", "大家刚在 Hacker News 上展示的作品"), "tech", _hn_show),
    Source("lobsters-hot", ("Lobsters", "Lobsters"),
           ("The hottest links on Lobsters, a programming community", "编程社区 Lobsters 的热门链接"), "tech", _lobsters),
    Source("devto-top", ("DEV Community", "DEV 社区"),
           ("Today's most liked posts on dev.to", "dev.to 今天最受欢迎的文章"), "tech", _devto),
    Source("so-week", ("Stack Overflow", "Stack Overflow"),
           ("This week's hottest programming questions", "本周最热的编程问题"), "tech", _stackoverflow),
    _feed("ph-today", ("Product Hunt", "Product Hunt"),
          ("New products launched on Product Hunt", "Product Hunt 上新发布的产品"), "https://www.producthunt.com/feed",
          cut=r"\s*Discussion \| Link$"),
    Source("juejin-hot", ("Juejin", "掘金热榜"),
           ("The hot list of Juejin, a Chinese developer community", "掘金开发者社区的热门文章"), "tech", _juejin),
    _feed("oschina-news", ("OSCHINA", "开源中国"),
          ("Open-source and developer news from OSCHINA", "开源中国的开源与开发者资讯"), "https://www.oschina.net/news/rss"),
    _feed("infoqcn-news", ("InfoQ China", "InfoQ 中文"),
          ("Software engineering articles from InfoQ China", "InfoQ 中文站的软件工程文章"), "https://www.infoq.cn/feed",
          cut=r"^点击查看原文>$"),
    _feed("ruanyf-weekly", ("Ruan Yifeng's weekly", "阮一峰科技爱好者周刊"),
          ("Ruan Yifeng's blog and his weekly tech digest", "阮一峰的博客与每周科技周刊"),
          "https://www.ruanyifeng.com/blog/atom.xml"),

    # AI
    Source("hf-papers", ("Hugging Face Daily Papers", "Hugging Face 每日论文"),
           ("AI papers the community upvoted today", "社区今天点赞最多的 AI 论文"), "science", _hf_papers),
    Source("hf-models", ("Hugging Face trending models", "Hugging Face 热门模型"),
           ("Open models trending on Hugging Face", "Hugging Face 上正热的开源模型"), "tech", _hf_models),
    Source("hf-spaces", ("Hugging Face trending Spaces", "Hugging Face 热门 Spaces"),
           ("AI demos and apps trending on Hugging Face", "Hugging Face 上正热的 AI 演示与应用"), "tech", _hf_spaces),
    _feed("openai-news", ("OpenAI News", "OpenAI 新闻"),
          ("Announcements and research from OpenAI", "OpenAI 的发布与研究"), "https://openai.com/news/rss.xml"),
    _feed("deepmind-blog", ("Google DeepMind Blog", "Google DeepMind 博客"),
          ("Research and models from Google DeepMind", "Google DeepMind 的研究与模型"),
          "https://deepmind.google/blog/rss.xml"),
    _feed("google-research", ("Google Research Blog", "Google Research 博客"),
          ("Research news from Google Research", "Google Research 的研究进展"), "https://research.google/blog/rss/"),
    _feed("msr-blog", ("Microsoft Research Blog", "微软研究院博客"),
          ("Research stories and podcasts from Microsoft Research", "微软研究院的研究文章与播客"),
          "https://www.microsoft.com/en-us/research/feed/"),
    _feed("hf-blog", ("Hugging Face Blog", "Hugging Face 博客"),
          ("Guides and releases from Hugging Face", "Hugging Face 的教程与发布"), "https://huggingface.co/blog/feed.xml"),
    _feed("bair-blog", ("Berkeley AI Research Blog", "伯克利 AI 研究博客"),
          ("Research written up by Berkeley AI Research", "伯克利 BAIR 实验室的研究文章"),
          "https://bair.berkeley.edu/blog/feed.xml"),
    _feed("simonw-blog", ("Simon Willison's Weblog", "Simon Willison 博客"),
          ("Daily notes on LLMs and tools from Simon Willison", "Simon Willison 关于大模型与工具的每日笔记"),
          "https://simonwillison.net/atom/everything/"),
    _feed("qbitai-news", ("QbitAI", "量子位"),
          ("AI news from QbitAI", "量子位的人工智能资讯"), "https://www.qbitai.com/feed"),
    _feed("kexue-blog", ("Scientific Spaces", "科学空间"),
          ("Su Jianlin's deep dives into machine learning math", "苏剑林的机器学习与数学长文"), "https://kexue.fm/feed"),

    # arXiv
    _arxiv("cs.AI", ("arXiv: Artificial Intelligence", "arXiv：人工智能"),
           ("Today's new cs.AI papers", "今天新发的 cs.AI 论文")),
    _arxiv("cs.CL", ("arXiv: Computation and Language", "arXiv：计算语言学"),
           ("Today's new NLP and language-model papers", "今天新发的自然语言处理与语言模型论文")),
    _arxiv("cs.CV", ("arXiv: Computer Vision", "arXiv：计算机视觉"),
           ("Today's new computer-vision papers", "今天新发的计算机视觉论文")),
    _arxiv("cs.LG", ("arXiv: Machine Learning", "arXiv：机器学习"),
           ("Today's new cs.LG papers", "今天新发的 cs.LG 论文")),
    _arxiv("stat.ML", ("arXiv: Statistical ML", "arXiv：统计机器学习"),
           ("Today's new stat.ML papers", "今天新发的 stat.ML 论文")),
    _arxiv("q-bio", ("arXiv: Quantitative Biology", "arXiv：定量生物学"),
           ("Today's new q-bio papers", "今天新发的定量生物学论文")),

    # Tech press
    _feed("verge-all", ("The Verge", "The Verge"),
          ("Tech news and reviews from The Verge", "The Verge 的科技新闻与评测"),
          "https://www.theverge.com/rss/index.xml"),
    _feed("ars-all", ("Ars Technica", "Ars Technica"),
          ("In-depth tech and science news from Ars Technica", "Ars Technica 的深度科技与科学报道"),
          "https://feeds.arstechnica.com/arstechnica/index", default="en"),
    _feed("techcrunch-all", ("TechCrunch", "TechCrunch"),
          ("Startup and tech industry news", "创业与科技行业新闻"), "https://techcrunch.com/feed/"),
    _feed("wired-all", ("Wired", "连线 Wired"),
          ("Stories on technology and culture from Wired", "Wired 的科技与文化报道"), "https://www.wired.com/feed/rss"),
    _feed("mittr-all", ("MIT Technology Review", "麻省理工科技评论"),
          ("Emerging technology, explained by MIT Technology Review", "MIT Technology Review 解读新兴技术"),
          "https://www.technologyreview.com/feed/"),
    _feed("ieee-spectrum", ("IEEE Spectrum", "IEEE Spectrum"),
          ("Engineering and technology news from IEEE Spectrum", "IEEE Spectrum 的工程与技术新闻"),
          "https://spectrum.ieee.org/feeds/feed.rss"),
    _feed("techmeme-top", ("Techmeme", "Techmeme"),
          ("The tech stories everyone is linking to", "大家都在转的科技头条"), "https://www.techmeme.com/feed.xml"),
    _feed("register-all", ("The Register", "The Register"),
          ("IT, security and industry news from The Register", "The Register 的 IT、安全与行业新闻"),
          "https://www.theregister.com/headlines.atom"),
    _feed("slashdot-main", ("Slashdot", "Slashdot"),
          ("News for nerds, from Slashdot", "Slashdot 的极客新闻"),
          "https://rss.slashdot.org/Slashdot/slashdotMain", covers=False),
    _feed("hackaday-blog", ("Hackaday", "Hackaday"),
          ("Hardware hacks and builds from Hackaday", "Hackaday 的硬件改造与制作"), "https://hackaday.com/blog/feed/"),
    _feed("ithome-news", ("IT Home", "IT之家"),
          ("Tech and gadget news from IT Home", "IT之家的科技与数码资讯"), "https://www.ithome.com/rss/", default="zh"),
    _feed("solidot-news", ("Solidot", "Solidot"),
          ("Short tech and science news from Solidot", "Solidot 的科技与科学短讯"), "https://www.solidot.org/index.rss"),
    _feed("cnbeta-news", ("cnBeta", "cnBeta"),
          ("Tech news from cnBeta", "cnBeta 的科技资讯"), "https://www.cnbeta.com.tw/backend.php"),
    _feed("ifanr-news", ("ifanr", "爱范儿"),
          ("Products and tech life from ifanr", "爱范儿的产品与科技生活"), "https://www.ifanr.com/feed"),

    # Engineering
    _feed("infoq-all", ("InfoQ", "InfoQ"),
          ("Software architecture and engineering talks from InfoQ", "InfoQ 的软件架构与工程分享"),
          "https://feed.infoq.com/"),
    _feed("smashing-mag", ("Smashing Magazine", "Smashing Magazine"),
          ("Web design and front-end articles", "网页设计与前端文章"), "https://www.smashingmagazine.com/feed/"),
    _feed("lwn-headlines", ("LWN.net", "LWN.net"),
          ("Linux and free-software news from LWN", "LWN 的 Linux 与自由软件新闻"), "https://lwn.net/headlines/rss"),
    _feed("phoronix-news", ("Phoronix", "Phoronix"),
          ("Linux hardware, drivers and benchmarks", "Linux 硬件、驱动与性能测试"), "https://www.phoronix.com/rss.php"),
    _feed("pragmatic-engineer", ("The Pragmatic Engineer", "The Pragmatic Engineer"),
          ("Gergely Orosz on how software teams work", "Gergely Orosz 谈软件团队如何运作"),
          "https://newsletter.pragmaticengineer.com/feed"),
    _feed("fowler-blog", ("Martin Fowler", "Martin Fowler"),
          ("Articles on software design from martinfowler.com", "martinfowler.com 的软件设计文章"),
          "https://martinfowler.com/feed.atom"),
    _feed("cloudflare-blog", ("Cloudflare Blog", "Cloudflare 博客"),
          ("How the internet works, from Cloudflare's engineers", "Cloudflare 工程师讲互联网如何运作"),
          "https://blog.cloudflare.com/rss/"),
    _feed("github-blog", ("The GitHub Blog", "GitHub 博客"),
          ("Product news and engineering from GitHub", "GitHub 的产品动态与工程文章"), "https://github.blog/feed/"),
    _feed("python-insider", ("Python Insider", "Python Insider"),
          ("Releases and news from the Python core team", "Python 核心团队的发布与消息"),
          "https://blog.python.org/feeds/posts/default"),
    _feed("rust-blog", ("Rust Blog", "Rust 博客"),
          ("Releases and news from the Rust project", "Rust 项目的发布与消息"), "https://blog.rust-lang.org/feed.xml"),
    _feed("go-blog", ("The Go Blog", "Go 博客"),
          ("Posts from the Go team", "Go 团队的文章"), "https://go.dev/blog/feed.atom"),
    _feed("k8s-blog", ("Kubernetes Blog", "Kubernetes 博客"),
          ("Releases and how-tos from the Kubernetes project", "Kubernetes 项目的发布与实践"),
          "https://kubernetes.io/feed.xml"),

    # Science
    _feed("quanta-all", ("Quanta Magazine", "Quanta 杂志"),
          ("Math, physics, biology and computer science, explained", "数学、物理、生物与计算机科学的深度解读"),
          "https://www.quantamagazine.org/feed/", group="science"),
    _feed("nature-journal", ("Nature", "Nature 自然"),
          ("Research and news from the journal Nature", "《自然》期刊的论文与新闻"),
          "https://www.nature.com/nature.rss", group="science",
          cut=r"^Nature, Published online: [^;]*; doi:\S+\s*"),
    _feed("science-news", ("Science News", "Science 新闻"),
          ("News from the journal Science", "《科学》期刊的新闻"),
          "https://www.science.org/rss/news_current.xml", group="science", bare=True),
    _feed("newscientist-all", ("New Scientist", "新科学家"),
          ("Science and technology news from New Scientist", "New Scientist 的科学与技术新闻"),
          "https://www.newscientist.com/feed/home/", group="science"),
    _feed("sciam-all", ("Scientific American", "科学美国人"),
          ("Science stories from Scientific American", "《科学美国人》的科学报道"),
          "https://www.scientificamerican.com/platform/syndication/rss/", group="science"),
    _feed("physorg-all", ("Phys.org", "Phys.org"),
          ("Research news across physics, space, biology and tech", "物理、太空、生物与技术的研究新闻"),
          "https://phys.org/rss-feed/", group="science"),
    _feed("sciencedaily-top", ("ScienceDaily", "ScienceDaily 每日科学"),
          ("Top research news from ScienceDaily", "ScienceDaily 的头条研究新闻"),
          "https://www.sciencedaily.com/rss/top/science.xml", group="science"),
    _biorxiv("bioinformatics", ("bioRxiv: Bioinformatics", "bioRxiv：生物信息学"),
             ("The newest bioinformatics preprints", "最新的生物信息学预印本")),
    _biorxiv("genomics", ("bioRxiv: Genomics", "bioRxiv：基因组学"),
             ("The newest genomics preprints", "最新的基因组学预印本")),
    _biorxiv("neuroscience", ("bioRxiv: Neuroscience", "bioRxiv：神经科学"),
             ("The newest neuroscience preprints", "最新的神经科学预印本")),
]

SOURCES = written_in("zh", SOURCES, {"juejin-hot", "oschina-news", "infoqcn-news", "ruanyf-weekly", "qbitai-news",
                                     "kexue-blog", "ithome-news", "solidot-news", "cnbeta-news", "ifanr-news"})
# The Chinese-language ones are mainland sites; the rest are read worldwide.
SOURCES = local_to("CN", SOURCES, {s.key for s in SOURCES if s.lang == "zh"})
