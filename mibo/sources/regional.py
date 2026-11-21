"""Local press, tech sites and forums for readers beyond Chinese and English:
Japanese, Korean, Traditional Chinese (Taiwan and Hong Kong), Russian,
Portuguese (Brazil), Spanish, French and German.

Each source says the language it is written in and the region whose press or
platform it is ("" when it serves the whole language), so a round can lean on
what is local to the person and readable by them. Every one is a public feed
or Wikipedia's public daily feed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .base import PER_SOURCE, Find, Source, find, read_feed

# Query parameters that only tell the publisher where a click came from (utm_ on Habr and
# Qiita, at_ on the BBC); dropped so the same story from two places is one item.
_TRACKING = ("utm_", "at_")


def _clean(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith(_TRACKING)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _folha(url: str) -> str:
    """Folha's feed wraps each story in a click counter: ".../rss091/*https://www1.folha...". """
    return _clean(url.split("*", 1)[1] if "*https://" in url else url)


def _feed(key: str, name: tuple[str, str], about: tuple[str, str], group: str, url: str, lang: str,
          region: str = "", *, site: str = "", default: str = "", link: Callable[[str], str] = _clean) -> Source:
    """One feed address. `site` names the outlet; without it each link keeps its own site,
    as for a bookmark or vote list that points elsewhere."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        found = (await read_feed(http, url, site=site))[:PER_SOURCE]
        for one in found:
            one.item.url = link(one.item.url)
        return found

    return Source(key, name, about, group, fetch, default, lang, region)


def _wiki_mostread(wiki: str):
    """What one Wikipedia's readers read most yesterday."""
    async def fetch(http: httpx.AsyncClient) -> list[Find]:
        day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        answer = await http.get(f"https://{wiki}.wikipedia.org/api/rest_v1/feed/featured/{day}",
                                headers={"Accept-Language": wiki})
        answer.raise_for_status()
        return [find(page["content_urls"]["desktop"]["page"], page.get("displaytitle") or page["normalizedtitle"],
                     site="Wikipedia", text=page.get("extract", ""),
                     picture=(page.get("thumbnail") or {}).get("source", ""))
                for page in sorted(answer.json()["mostread"]["articles"], key=lambda a: a["rank"])[:PER_SOURCE]]
    return fetch


def _wiki(key: str, name: tuple[str, str], about: tuple[str, str], wiki: str, region: str = "") -> Source:
    return Source(key, name, about, "culture", _wiki_mostread(wiki), "", wiki, region)


SOURCES = [
    # Japanese, Japan.
    _feed("itmedia-news", ("ITmedia NEWS", "ITmedia 新闻"),
          ("Japanese tech and IT industry news", "日本的科技与 IT 行业新闻"), "tech",
          "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml", "ja", "JP", site="ITmedia NEWS", default="ja"),
    _feed("gigazine", ("GIGAZINE", "GIGAZINE"),
          ("Odd finds, science and tech from a Japanese web daily", "日本网络日报的趣闻、科学与科技"), "tech",
          "https://gigazine.net/news/rss_2.0/", "ja", "JP", site="GIGAZINE"),
    _feed("hatena-hot", ("Hatena Bookmark: Hot", "Hatena 书签 热门"),
          ("The pages Japanese readers bookmark most right now", "日本读者此刻收藏最多的网页"), "community",
          "https://b.hatena.ne.jp/hotentry.rss", "ja", "JP", default="ja"),
    _feed("hatena-it", ("Hatena Bookmark: Tech", "Hatena 书签 科技"),
          ("The tech pages Japanese readers bookmark most", "日本读者收藏最多的科技网页"), "tech",
          "https://b.hatena.ne.jp/hotentry/it.rss", "ja", "JP"),
    _feed("zenn", ("Zenn: Trending", "Zenn 热门"),
          ("Trending articles by Japanese developers", "日本开发者的热门技术文章"), "tech",
          "https://zenn.dev/feed", "ja", "JP", site="Zenn"),
    _feed("qiita-popular", ("Qiita: Popular", "Qiita 热门"),
          ("Popular posts on Japan's programming knowledge site", "日本编程知识社区 Qiita 的热门文章"), "tech",
          "https://qiita.com/popular-items/feed", "ja", "JP", site="Qiita"),
    _feed("yahoo-jp-topics", ("Yahoo! News Japan: Top topics", "雅虎日本新闻 要闻"),
          ("The main stories Yahoo! Japan's editors pick", "雅虎日本编辑挑选的要闻"), "news",
          "https://news.yahoo.co.jp/rss/topics/top-picks.xml", "ja", "JP", site="Yahoo!ニュース", default="ja"),
    _feed("4gamer", ("4Gamer.net", "4Gamer.net"),
          ("Japanese video game news", "日本的游戏新闻"), "culture",
          "https://www.4gamer.net/rss/index.xml", "ja", "JP", site="4Gamer.net"),
    _wiki("wiki-ja-mostread", ("Japanese Wikipedia: Most read", "日文维基 最多阅读"),
          ("What people read most on Japanese Wikipedia yesterday", "昨天日文维基百科上读得最多的条目"), "ja", "JP"),

    # Korean, South Korea.
    _feed("yonhap", ("Yonhap News", "韩联社"),
          ("Korea's national news agency", "韩国国家通讯社的新闻"), "news",
          "https://www.yna.co.kr/rss/news.xml", "ko", "KR", site="연합뉴스", default="ko"),
    _feed("hani", ("The Hankyoreh", "韩民族日报"),
          ("News from a progressive Korean daily", "韩国进步派日报的新闻"), "news",
          "https://www.hani.co.kr/rss/", "ko", "KR", site="한겨레"),
    _feed("donga", ("Dong-A Ilbo", "东亚日报"),
          ("News from a conservative Korean daily", "韩国保守派日报的新闻"), "news",
          "https://rss.donga.com/total.xml", "ko", "KR", site="동아일보"),
    _feed("zdnet-kr", ("ZDNet Korea", "ZDNet 韩国"),
          ("Korean IT and industry news", "韩国的 IT 与产业新闻"), "tech",
          "https://feeds.feedburner.com/zdkorea", "ko", "KR", site="지디넷코리아", default="ko"),
    _feed("bloter", ("Bloter", "Bloter"),
          ("Korean tech and startup news", "韩国的科技与创业新闻"), "tech",
          "https://www.bloter.net/rss/allArticle.xml", "ko", "KR", site="블로터"),
    _feed("geeknews", ("GeekNews", "GeekNews"),
          ("Tech links Korean developers share and discuss", "韩国开发者分享和讨论的技术链接"), "community",
          "https://news.hada.io/rss/news", "ko", "KR", site="GeekNews", default="ko"),
    _feed("yozm-it", ("Yozm IT", "Yozm IT"),
          ("Long reads on software work, from Korea", "韩国关于软件工作的长文"), "tech",
          "https://yozm.wishket.com/magazine/feed/", "ko", "KR", site="요즘IT"),
    _feed("ruliweb-news", ("Ruliweb: Game news", "Ruliweb 游戏新闻"),
          ("Video game news from Korea's big gaming community", "韩国大型游戏社区的游戏新闻"), "culture",
          "https://bbs.ruliweb.com/news/rss", "ko", "KR", site="루리웹"),
    _feed("bbc-korean", ("BBC News Korean", "BBC 韩语"),
          ("The BBC's Korean service", "BBC 韩语部的新闻"), "news",
          "https://feeds.bbci.co.uk/korean/rss.xml", "ko", site="BBC 코리아"),
    _wiki("wiki-ko-mostread", ("Korean Wikipedia: Most read", "韩文维基 最多阅读"),
          ("What people read most on Korean Wikipedia yesterday", "昨天韩文维基百科上读得最多的条目"), "ko", "KR"),

    # Traditional Chinese, Taiwan.
    _feed("pts-news", ("PTS News", "公视新闻"),
          ("Taiwan's public broadcaster", "台湾公共电视的新闻"), "news",
          "https://news.pts.org.tw/xml/newsfeed.xml", "zh-Hant", "TW", site="公視新聞網", default="zh-Hant"),
    _feed("cna-world", ("CNA: World", "中央社 国际"),
          ("International news from Taiwan's national news agency", "台湾中央社的国际新闻"), "news",
          "https://feeds.feedburner.com/rsscna/intworld", "zh-Hant", "TW", site="中央社"),
    _feed("ltn-news", ("Liberty Times", "自由时报"),
          ("Latest news from a major Taiwanese daily", "台湾大报的即时新闻"), "news",
          "https://news.ltn.com.tw/rss/all.xml", "zh-Hant", "TW", site="自由時報"),
    _feed("twreporter", ("The Reporter", "报导者"),
          ("Nonprofit in-depth reporting from Taiwan", "台湾非营利媒体的深度报道"), "news",
          "https://www.twreporter.org/a/rss2.xml", "zh-Hant", "TW", site="報導者"),
    _feed("newslens", ("The News Lens", "关键评论网"),
          ("News and commentary from Taiwan", "台湾的新闻与评论"), "news",
          "https://feeds.feedburner.com/TheNewsLens", "zh-Hant", "TW", site="關鍵評論網"),
    _feed("ithome-tw", ("iThome", "iThome"),
          ("Taiwan's IT news for engineers and IT staff", "台湾面向工程师与 IT 人员的资讯"), "tech",
          "https://www.ithome.com.tw/rss", "zh-Hant", "TW", site="iThome", default="zh-Hant"),
    _feed("inside-tw", ("INSIDE", "INSIDE 硬塞"),
          ("Taiwanese tech, startups and internet trends", "台湾的科技、创业与网络趋势"), "tech",
          "https://www.inside.com.tw/feed/rss", "zh-Hant", "TW", site="INSIDE"),
    _feed("technews-tw", ("TechNews", "科技新报"),
          ("Tech and science news from Taiwan", "台湾的科技与科学新闻"), "tech",
          "https://technews.tw/feed/", "zh-Hant", "TW", site="科技新報"),
    _feed("gnn-gamer", ("Bahamut GNN", "巴哈姆特 GNN"),
          ("Game and anime news from Taiwan's biggest ACG community", "台湾最大动漫游戏社区的新闻"), "culture",
          "https://gnn.gamer.com.tw/rss.xml", "zh-Hant", "TW", site="巴哈姆特"),
    _feed("pansci", ("PanSci", "泛科学"),
          ("Popular science from Taiwan", "台湾的科普文章"), "science",
          "https://pansci.asia/feed", "zh-Hant", "TW", site="泛科學"),

    # Hong Kong.
    _feed("rthk-local", ("RTHK: Local news", "香港电台 本地新闻"),
          ("Hong Kong's public broadcaster, local news", "香港公共广播的本地新闻"), "news",
          "https://rthk.hk/rthk/news/rss/c_expressnews_clocal.xml", "zh-Hant", "HK", site="香港電台",
          default="zh-Hant"),
    _feed("stheadline", ("Sing Tao Headline", "星岛头条"),
          ("Latest news from a major Hong Kong daily", "香港大报的即时新闻"), "news",
          "https://www.stheadline.com/rss", "zh-Hant", "HK", site="星島頭條"),
    _feed("inmediahk", ("InMedia HK", "香港独立媒体"),
          ("Independent, citizen-written news from Hong Kong", "香港的独立公民媒体"), "news",
          "https://www.inmediahk.net/rss.xml", "zh-Hant", "HK", site="獨立媒體"),
    _feed("bastille-post", ("Bastille Post", "巴士的报"),
          ("Hong Kong news and current affairs", "香港的新闻与时事"), "news",
          "https://www.bastillepost.com/hongkong/feed", "zh-Hant", "HK", site="巴士的報"),
    _feed("unwire-hk", ("unwire.hk", "unwire.hk"),
          ("Hong Kong gadget and tech news", "香港的数码与科技新闻"), "tech",
          "https://unwire.hk/feed/", "zh-Hant", "HK", site="unwire.hk"),
    _feed("hkfp", ("Hong Kong Free Press", "香港自由新闻"),
          ("Independent English-language news from Hong Kong", "香港独立的英文新闻"), "news",
          "https://hongkongfp.com/feed/", "en", "HK", site="HKFP"),
    _feed("scmp-hk", ("SCMP: Hong Kong", "南华早报 香港"),
          ("Hong Kong news from the South China Morning Post", "《南华早报》的香港新闻"), "news",
          "https://www.scmp.com/rss/2/feed", "en", "HK", site="SCMP"),

    # Russian.
    _feed("habr", ("Habr: Best of the day", "Habr 今日最佳"),
          ("The day's best posts on the big Russian-language IT community", "俄语大型 IT 社区当天的最佳文章"), "tech",
          "https://habr.com/ru/rss/best/daily/", "ru", "RU", site="Хабр", default="ru"),
    _feed("meduza", ("Meduza", "Meduza"),
          ("Independent Russian-language news", "独立的俄语新闻"), "news",
          "https://meduza.io/rss/all", "ru", "RU", site="Медуза"),
    _feed("lenta", ("Lenta.ru", "Lenta.ru"),
          ("Russian news site", "俄罗斯新闻网站"), "news",
          "https://lenta.ru/rss", "ru", "RU", site="Лента.ру"),
    _feed("kommersant", ("Kommersant", "生意人报"),
          ("News from a Russian business daily", "俄罗斯商业日报的新闻"), "news",
          "https://www.kommersant.ru/RSS/news.xml", "ru", "RU", site="Коммерсантъ", default="ru"),
    _feed("tass", ("TASS", "塔斯社"),
          ("Russia's state news agency", "俄罗斯国家通讯社的新闻"), "news",
          "https://tass.ru/rss/v2.xml", "ru", "RU", site="ТАСС"),
    _feed("nplus1", ("N + 1", "N + 1"),
          ("Science news in Russian", "俄语的科学新闻"), "science",
          "https://nplus1.ru/rss", "ru", "RU", site="N + 1", default="ru"),
    _feed("3dnews", ("3DNews", "3DNews"),
          ("Hardware, software and gadget news in Russian", "俄语的硬件、软件与数码新闻"), "tech",
          "https://3dnews.ru/news/rss/", "ru", "RU", site="3DNews", default="ru"),
    _feed("postnauka", ("PostNauka", "PostNauka"),
          ("Scientists explain their fields, in Russian", "科学家用俄语讲解自己的领域"), "science",
          "https://postnauka.org/feed", "ru", "RU", site="ПостНаука"),
    _feed("bbc-russian", ("BBC News Russian", "BBC 俄语"),
          ("The BBC's Russian service", "BBC 俄语部的新闻"), "news",
          "https://feeds.bbci.co.uk/russian/rss.xml", "ru", site="Русская служба BBC"),
    _wiki("wiki-ru-mostread", ("Russian Wikipedia: Most read", "俄文维基 最多阅读"),
          ("What people read most on Russian Wikipedia yesterday", "昨天俄文维基百科上读得最多的条目"), "ru"),

    # Portuguese, Brazil.
    _feed("g1", ("g1", "g1"),
          ("Brazil's biggest news site, from Globo", "巴西最大的新闻网站，来自环球集团"), "news",
          "https://g1.globo.com/rss/g1/", "pt", "BR", site="g1", default="pt"),
    _feed("folha", ("Folha de S.Paulo", "圣保罗页报"),
          ("Latest news from a major Brazilian daily", "巴西大报的即时新闻"), "news",
          "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml", "pt", "BR", site="Folha", default="pt",
          link=_folha),
    _feed("agencia-brasil", ("Agência Brasil", "巴西通讯社"),
          ("Brazil's public news agency", "巴西公共通讯社的新闻"), "news",
          "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml", "pt", "BR", site="Agência Brasil"),
    _feed("nexo", ("Nexo Jornal", "Nexo 报"),
          ("Explainers and context on the news in Brazil", "巴西新闻的解读与背景"), "news",
          "https://www.nexojornal.com.br/rss.xml", "pt", "BR", site="Nexo"),
    _feed("bbc-brasil", ("BBC News Brasil", "BBC 巴西"),
          ("The BBC's Brazilian Portuguese service", "BBC 的巴西葡语新闻"), "news",
          "https://feeds.bbci.co.uk/portuguese/rss.xml", "pt", "BR", site="BBC News Brasil"),
    _feed("tecnoblog", ("Tecnoblog", "Tecnoblog"),
          ("Brazilian tech news", "巴西的科技新闻"), "tech",
          "https://tecnoblog.net/feed/", "pt", "BR", site="Tecnoblog", default="pt"),
    _feed("canaltech", ("Canaltech", "Canaltech"),
          ("Tech, games and entertainment from Brazil", "巴西的科技、游戏与娱乐"), "tech",
          "https://canaltech.com.br/rss/", "pt", "BR", site="Canaltech"),
    _feed("olhar-digital", ("Olhar Digital", "Olhar Digital"),
          ("Brazilian tech and science news", "巴西的科技与科学新闻"), "tech",
          "https://olhardigital.com.br/feed/", "pt", "BR", site="Olhar Digital"),
    _feed("pesquisa-fapesp", ("Pesquisa FAPESP", "FAPESP 研究杂志"),
          ("Science journalism from São Paulo's research foundation", "圣保罗研究基金会的科学报道"), "science",
          "https://revistapesquisa.fapesp.br/feed/", "pt", "BR", site="Pesquisa FAPESP"),
    _wiki("wiki-pt-mostread", ("Portuguese Wikipedia: Most read", "葡文维基 最多阅读"),
          ("What people read most on Portuguese Wikipedia yesterday", "昨天葡文维基百科上读得最多的条目"), "pt"),

    # Spanish.
    _feed("el-pais", ("El País", "国家报"),
          ("Spain's largest daily", "西班牙最大的日报"), "news",
          "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "es", "ES", site="El País",
          default="es"),
    _feed("el-mundo", ("El Mundo", "世界报"),
          ("News from a major Spanish daily", "西班牙大报的新闻"), "news",
          "https://e00-elmundo.uecdn.es/elmundo/rss/portada.xml", "es", "ES", site="El Mundo"),
    _feed("eldiario", ("elDiario.es", "elDiario.es"),
          ("An independent Spanish online daily", "西班牙的独立网络日报"), "news",
          "https://www.eldiario.es/rss/", "es", "ES", site="elDiario.es"),
    _feed("20minutos", ("20minutos", "20minutos"),
          ("Spanish news and everyday stories", "西班牙的新闻与生活故事"), "news",
          "https://www.20minutos.es/rss/", "es", "ES", site="20minutos"),
    _feed("xataka", ("Xataka", "Xataka"),
          ("Tech news in Spanish", "西班牙语的科技新闻"), "tech",
          "https://www.xataka.com/feedburner.xml", "es", "ES", site="Xataka", default="es"),
    _feed("genbeta", ("Genbeta", "Genbeta"),
          ("Software, apps and the internet, in Spanish", "西班牙语的软件、应用与互联网"), "tech",
          "https://www.genbeta.com/feedburner.xml", "es", "ES", site="Genbeta"),
    _feed("meneame", ("Menéame", "Menéame"),
          ("The links Spanish readers vote to the front page", "西班牙读者投票选上首页的链接"), "community",
          "https://www.meneame.net/rss", "es", "ES", default="es"),
    _feed("bbc-mundo", ("BBC News Mundo", "BBC 西语"),
          ("The BBC's Spanish service, for Latin America and beyond", "BBC 面向拉丁美洲等地的西语新闻"), "news",
          "https://feeds.bbci.co.uk/mundo/rss.xml", "es", site="BBC News Mundo", default="es"),
    _wiki("wiki-es-mostread", ("Spanish Wikipedia: Most read", "西文维基 最多阅读"),
          ("What people read most on Spanish Wikipedia yesterday", "昨天西文维基百科上读得最多的条目"), "es"),

    # French.
    _feed("le-monde", ("Le Monde", "世界报（法国）"),
          ("The front page of France's daily of record", "法国《世界报》的头版"), "news",
          "https://www.lemonde.fr/rss/une.xml", "fr", "FR", site="Le Monde", default="fr"),
    _feed("franceinfo", ("franceinfo", "franceinfo"),
          ("France's public news service", "法国公共新闻服务"), "news",
          "https://www.francetvinfo.fr/titres.rss", "fr", "FR", site="franceinfo", default="fr"),
    _feed("france24-fr", ("France 24 (French)", "法兰西 24（法语）"),
          ("International news in French", "法语的国际新闻"), "news",
          "https://www.france24.com/fr/rss", "fr", site="France 24"),
    _feed("rfi-fr", ("RFI (French)", "法广（法语）"),
          ("Radio France Internationale in French", "法国国际广播电台的法语新闻"), "news",
          "https://www.rfi.fr/fr/rss", "fr", site="RFI"),
    _feed("numerama", ("Numerama", "Numerama"),
          ("French tech, science and pop culture", "法国的科技、科学与流行文化"), "tech",
          "https://www.numerama.com/feed/", "fr", "FR", site="Numerama", default="fr"),
    _feed("next-ink", ("Next", "Next"),
          ("French tech and digital-rights news", "法国的科技与数字权利新闻"), "tech",
          "https://next.ink/feed/", "fr", "FR", site="Next"),
    _feed("korben", ("Korben", "Korben"),
          ("A French blog of tech tips and finds", "法国的科技技巧与发现博客"), "tech",
          "https://korben.info/feed", "fr", "FR", site="Korben"),
    _feed("linuxfr", ("LinuxFr.org", "LinuxFr.org"),
          ("News from the French-speaking free-software community", "法语自由软件社区的新闻"), "community",
          "https://linuxfr.org/news.atom", "fr", "FR", site="LinuxFr.org"),
    _feed("conversation-fr", ("The Conversation France", "The Conversation 法国"),
          ("Academics write about their research, in French", "学者用法语写自己的研究"), "science",
          "https://theconversation.com/fr/articles.atom", "fr", "FR", site="The Conversation"),
    _wiki("wiki-fr-mostread", ("French Wikipedia: Most read", "法文维基 最多阅读"),
          ("What people read most on French Wikipedia yesterday", "昨天法文维基百科上读得最多的条目"), "fr"),

    # German.
    _feed("tagesschau", ("tagesschau", "每日新闻（德国）"),
          ("News from Germany's public broadcaster ARD", "德国公共广播 ARD 的新闻"), "news",
          "https://www.tagesschau.de/index~rss2.xml", "de", "DE", site="tagesschau", default="de"),
    _feed("spiegel", ("Der Spiegel", "明镜"),
          ("Headlines from Germany's news weekly", "德国新闻周刊《明镜》的头条"), "news",
          "https://www.spiegel.de/schlagzeilen/index.rss", "de", "DE", site="Der Spiegel", default="de"),
    _feed("zeit", ("Die Zeit", "时代周报"),
          ("News and long reads from a German weekly", "德国周报的新闻与长文"), "news",
          "https://newsfeed.zeit.de/index", "de", "DE", site="Zeit Online"),
    _feed("dw-de", ("DW (German)", "德国之声（德语）"),
          ("Deutsche Welle's news in German", "德国之声的德语新闻"), "news",
          "https://rss.dw.com/rdf/rss-de-all", "de", site="DW"),
    _feed("heise", ("heise online", "heise online"),
          ("Germany's main IT news site", "德国主要的 IT 新闻网站"), "tech",
          "https://www.heise.de/rss/heise-atom.xml", "de", "DE", site="heise online", default="de"),
    _feed("golem", ("Golem.de", "Golem.de"),
          ("IT news for professionals, in German", "面向专业人士的德语 IT 新闻"), "tech",
          "https://rss.golem.de/rss.php?feed=RSS2.0", "de", "DE", site="Golem.de"),
    _feed("t3n", ("t3n", "t3n"),
          ("German digital business and tech", "德国的数字商业与科技"), "tech",
          "https://t3n.de/rss.xml", "de", "DE", site="t3n"),
    _feed("netzpolitik", ("netzpolitik.org", "netzpolitik.org"),
          ("Digital rights and internet policy, in German", "德语的数字权利与网络政策"), "tech",
          "https://netzpolitik.org/feed/", "de", "DE", site="netzpolitik.org"),
    _feed("spektrum", ("Spektrum der Wissenschaft", "科学光谱"),
          ("Science news from Germany's popular science magazine", "德国科普杂志的科学新闻"), "science",
          "https://www.spektrum.de/alias/rss/spektrum-de-rss-feed/996406", "de", "DE", site="Spektrum"),
    _wiki("wiki-de-mostread", ("German Wikipedia: Most read", "德文维基 最多阅读"),
          ("What people read most on German Wikipedia yesterday", "昨天德文维基百科上读得最多的条目"), "de"),
]
