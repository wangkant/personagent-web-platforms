"""What the person read in the app's own browser (browser.py), kept on this computer.

Every page they view there leaves a row in BROWSED_FILE under the home's desktop
folder: its address, title, host, when, and the seconds it was really in front.
A page read longer than ITEM_S also becomes a library Item marked as theirs
(via=BROWSED), so the taste model and the character's day hear of it as of any
other reading. Never kept: anything while the pane's private switch is on,
sign-in, account and payment pages, and anything but http(s).

Also the guard of reading with the person's session (browser.Reader): one host's
reads a day, and the hosts where a check or a sign-in wall stopped it.

The setting SHARE_KEY in the app's settings ("角色可以参考你在应用内浏览器里看过的
内容", on unless turned off) lets the rows reach the library and the character's
tools; off, they stay in the file alone and the tools get nothing. The person's
own address bar suggests from them either way (suggest), and Settings › 隐私
lists them as history (history): an entry or a time range deleted there takes
the library item its reading made with it (delete, clear).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .library import BROWSED, Item, Library, item_id
from .storage import append_jsonl, atomic_write_text

BROWSED_FILE = "browsed.jsonl"
WALLS_FILE = "browser-walls.json"
SHARE_KEY = "browsing_shared"
ROWS_MAX = 2000
ROW_TITLE_MAX = 300
# A list the person loaded (a feed of theirs): how many of its rows are noted with its row.
ROWS_NOTED = 10
ITEM_S = 30.0
ITEM_TEXT_MAX = 20_000
# Reading with the person's session: a pause between reads, and how many a host gets in a day.
READ_GAP_S = 20.0
HOST_DAY_MAX = 20
# A wall stays remembered this long; the person may sign in again meanwhile.
WALL_S = 7 * 86400
# The address bar's suggestions from the pages read: how many, and what a visit counts for by its age in days
# (Firefox's frecency buckets).
SUGGEST_MAX = 6
VISIT_WEIGHTS = ((4, 1.0), (14, 0.7), (31, 0.5), (90, 0.3), (float("inf"), 0.1))
# History in Settings: entries a page of it holds, and the ranges it is cleared by (seconds back; 0 for all).
HISTORY_PAGE = 50
RANGES = {"hour": 3600, "day": 86400, "all": 0}

# Sign-in, account and payment pages: never kept, whatever is on them.
_SECRET_HOSTS = ("accounts.google.com", "login.microsoftonline.com", "login.live.com", "account.microsoft.com",
                 "appleid.apple.com", "idmsa.apple.com", "passport.bilibili.com", "passport.baidu.com",
                 "passport.weibo.com", "login.taobao.com", "login.tmall.com", "passport.jd.com", "auth0.com",
                 "okta.com", "paypal.com", "checkout.stripe.com", "pay.google.com", "alipay.com", "pay.weixin.qq.com",
                 "wx.tenpay.com", "tenpay.com", "unionpay.com", "github.com/login", "github.com/session")
_SECRET_LABELS = frozenset({"login", "signin", "sign-in", "auth", "sso", "passport", "account", "accounts", "id",
                            "checkout", "pay", "payment", "payments", "billing", "wallet", "secure"})
_SECRET_PATH = re.compile(r"/(log-?in|sign-?in|sign-?up|register|oauth\d?|authorize|auth|sso|session|password|"
                          r"account|checkout|payment|pay|billing|wallet|cart/checkout)(/|$|\?)", re.I)

_lock = threading.Lock()


def web(address: str) -> bool:
    return urlsplit(str(address)).scheme in ("http", "https")


def secret(address: str) -> bool:
    """A sign-in, account or payment address, by its host or its path."""
    parts = urlsplit(str(address))
    host = (parts.hostname or "").lower()
    where = host + parts.path.lower()
    if any(where == known or where.startswith(known + "/") or host == known or host.endswith("." + known)
           for known in _SECRET_HOSTS):
        return True
    if host.split(".")[0] in _SECRET_LABELS:
        return True
    return bool(_SECRET_PATH.search(parts.path or "/"))


def site_of(host: str) -> str:
    """The site a host belongs to, near enough: its last two labels, three under a two-letter country's
    second level (bbc.co.uk, sina.com.cn)."""
    labels = [label for label in (host or "").lower().strip(".").split(".") if label]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "net", "org", "gov", "edu", "ac"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def shared(desktop: Path) -> bool:
    """The privacy switch, in the app's settings (welcome.app_settings): on unless turned off."""
    try:
        settings = json.loads((Path(desktop) / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not isinstance(settings, dict) or settings.get(SHARE_KEY) is not False


# ---------------------------------------------------------------- the rows

def record(desktop: Path, row: dict) -> bool:
    """Keep one page viewed: {"url", "title", "s"} plus "at", and for a list it loaded (a feed of theirs) its
    first rows as the page showed them; False when it is not to be kept."""
    url = str(row.get("url") or "")
    if not web(url) or secret(url) or row.get("secret"):
        return False
    kept = {"url": url[:2000], "title": " ".join(str(row.get("title") or "").split())[:ROW_TITLE_MAX],
            "host": (urlsplit(url).hostname or "").lower(), "at": round(float(row.get("at") or time.time()), 1),
            "s": round(max(0.0, float(row.get("s") or 0)), 1)}
    listed = [{k: str(r[k])[:200] for k in ("title", "link") if r.get(k)}
              for r in ((row.get("facts") or {}).get("rows") or [])[:ROWS_NOTED] if isinstance(r, dict)]
    if listed:
        kept["rows"] = listed
    path = Path(desktop) / BROWSED_FILE
    with _lock:
        append_jsonl(path, kept)
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > ROWS_MAX + ROWS_MAX // 10:
            atomic_write_text(path, "\n".join(lines[-ROWS_MAX:]) + "\n")
    return True


def _rows(desktop: Path) -> list[dict]:
    """Every row kept, oldest first."""
    try:
        lines = (Path(desktop) / BROWSED_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and web(row.get("url", "")):
            rows.append(row)
    return rows


def recent(desktop: Path, n: int = 20) -> list[dict]:
    """The last `n` rows, newest first; none while sharing is off."""
    return _rows(desktop)[::-1][:n] if shared(desktop) else []


def suggest(desktop: Path, text: str, n: int = SUGGEST_MAX) -> list[dict]:
    """The pages read that match what the person types in the address bar, best first: each word in the address
    or the title; an address that begins with the text first, then by frecency (every visit, worth less as it
    ages), then the latest. {"url", "title"}."""
    words, typed, now = text.lower().split(), text.strip().lower(), time.time()
    if not words:
        return []
    pages: dict[str, dict] = {}
    for row in _rows(desktop):
        page = pages.setdefault(row["url"], {"url": row["url"], "title": "", "score": 0.0, "at": 0.0})
        at = float(row.get("at") or 0)
        page["title"] = row.get("title") or page["title"]
        page["score"] += next(weight for days, weight in VISIT_WEIGHTS if (now - at) / 86400 < days)
        page["at"] = max(page["at"], at)

    def bare(url: str) -> str:
        return url.split("://", 1)[-1].removeprefix("www.")
    found = [page for page in pages.values() if all(word in f"{bare(page['url'])} {page['title']}".lower() for word in words)]
    found.sort(key=lambda page: (not bare(page["url"]).lower().startswith(typed), -page["score"], -page["at"]))
    return [{"url": page["url"], "title": page["title"]} for page in found[:n]]


def keep_item(runtime: Path, row: dict, facts: dict) -> str:
    """A page read long enough, made a library Item of the person's reading; its id, or "" when not."""
    url = str(row.get("url") or "")
    if not web(url) or secret(url) or float(row.get("s") or 0) < ITEM_S:
        return ""
    from .roam import site_name
    library = Library(runtime)
    ident = item_id(url)
    if library.get(ident) is None:
        facts = facts if isinstance(facts, dict) else {}
        kind = facts.get("kind") if facts.get("kind") in ("article", "video", "post", "page") else "page"
        library.add(Item(url=url, site=site_name(url), kind=kind,
                         title=str(facts.get("title") or row.get("title") or "")[:300] or site_name(url),
                         author=str(facts.get("author") or "")[:200],
                         description=str(facts.get("description") or "")[:1000],
                         text=str(facts.get("text") or "")[:ITEM_TEXT_MAX],
                         seen_at=int(row.get("at") or time.time()), via=BROWSED))
    return ident


def _day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at))


def history(desktop: Path, text: str = "", before: float | None = None, n: int = HISTORY_PAGE) -> dict:
    """The history as a browser's history page lists it: one entry per page and day (at its latest visit), newest
    first, those with every word of `text` in their title or address, from `before` (an entry's "at") on.
    {"pages": [{"url", "title", "host", "at", "day", "visits"}], "next": the "at" to ask from next, or None}"""
    words = text.lower().split()
    entries: dict[tuple, dict] = {}
    for row in _rows(desktop):
        at = float(row.get("at") or 0)
        entry = entries.setdefault((row["url"], _day(at)), {"url": row["url"], "title": "", "host": row.get("host") or "",
                                                             "at": 0.0, "day": _day(at), "visits": 0})
        entry["visits"] += 1
        if at >= entry["at"]:
            entry["at"], entry["title"] = at, row.get("title") or entry["title"]
    found = sorted((e for e in entries.values() if (before is None or e["at"] < before)
                    and all(word in f"{e['url']} {e['title']}".lower() for word in words)), key=lambda e: -e["at"])
    return {"pages": found[:n], "next": found[n - 1]["at"] if len(found) > n else None}


def delete(desktop: Path, url: str, day: str) -> None:
    """One history entry: every visit of `url` on `day` (as history lists it)."""
    _drop(desktop, lambda row: row["url"] == url and _day(float(row.get("at") or 0)) == day)


def clear(desktop: Path, since: float = 0.0) -> None:
    """The history from `since` on; with 0 all of it, and the walls the Reader met."""
    _drop(desktop, lambda row: float(row.get("at") or 0) >= since)
    if not since:
        (Path(desktop) / WALLS_FILE).unlink(missing_ok=True)


def _drop(desktop: Path, gone) -> None:
    """The rows `gone(row)` is true of go, and with a page's last visit the library item its reading made and what
    the recommender heard of it (feed.forget_items)."""
    with _lock:
        rows = _rows(desktop)
        kept = [row for row in rows if not gone(row)]
        if len(kept) == len(rows):
            return
        atomic_write_text(Path(desktop) / BROWSED_FILE, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept))
    ids = {item_id(url) for url in {row["url"] for row in rows} - {row["url"] for row in kept}}
    runtime = Path(desktop).parent
    dropped = Library(runtime).drop(lambda item: item.id in ids and item.via == BROWSED)
    if dropped:
        from .panels import feed
        feed.forget_items(runtime, set(dropped))


# ---------------------------------------------------------------- reading with the session

class Pace:
    """One read at a time, READ_GAP_S between reads, HOST_DAY_MAX a day for a site, and none for a site
    whose check or sign-in wall stopped a read (remembered WALL_S)."""

    def __init__(self, desktop: Path, clock=time.time):
        self.path = Path(desktop) / WALLS_FILE
        self.clock = clock
        self.lock = threading.Lock()
        self.last = 0.0
        self.counts: dict[tuple[str, str], int] = {}

    def walls(self) -> dict:
        try:
            walls = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = self.clock()
        return {site: row for site, row in walls.items()
                if isinstance(row, dict) and now - float(row.get("at") or 0) < WALL_S}

    def refuse(self, url: str) -> str:
        """Why `url` may not be read now ("" when it may)."""
        site = site_of(urlsplit(url).hostname or "")
        if not web(url) or not site:
            return "not_web"
        if secret(url):
            return "sign_in_page"
        if site in self.walls():
            return "wall"
        if self.counts.get((time.strftime("%Y-%m-%d"), site), 0) >= HOST_DAY_MAX:
            return "host_day_cap"
        return ""

    def wait(self) -> None:
        gap = self.last + READ_GAP_S - self.clock()
        if gap > 0:
            time.sleep(gap)

    def done(self, url: str, wall: str = "") -> None:
        self.last = self.clock()
        site = site_of(urlsplit(url).hostname or "")
        day = (time.strftime("%Y-%m-%d"), site)
        self.counts[day] = self.counts.get(day, 0) + 1
        if wall:
            walls = self.walls()
            walls[site] = {"at": round(self.clock()), "why": wall}
            atomic_write_text(self.path, json.dumps(walls))


# ---------------------------------------------------------------- the character's tools

def _ask(home: Path, method: str, path: str, timeout: float = 60, **sent) -> dict:
    """The app's page (it holds the app window and its browser), over its local API, from a thread
    of its own: the page's address and token are in welcome.json. {} when no page answers."""
    import httpx

    from .welcome import TOKEN_HEADER
    try:
        url = json.loads((Path(home) / "runtime" / "desktop" / "welcome.json").read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    base, _, token = str(url).partition("/#token=")
    if not token:
        return {}
    try:
        answer = httpx.request(method, base + path, timeout=timeout, trust_env=False,
                               headers={TOKEN_HEADER: token}, **sent)
        answer.raise_for_status()
        return answer.json()
    except (httpx.HTTPError, ValueError):
        return {}


def read_signed_in(home: Path, url: str) -> dict:
    """`url` read with the person's session in the app's browser, out of sight: the same fields with its whole
    text, or {"error": why} (browser.Reader.read)."""
    if not shared(Path(home) / "runtime" / "desktop"):
        return {"error": "off"}
    return _ask(home, "POST", "/api/browser/read", json={"url": url}) or {"error": "no_app"}


def recent_pages(home: Path, n: int = 20) -> list[dict]:
    """The pages read lately in the app's browser, newest first: {"url", "title", "host", "at", "s"}."""
    return recent(Path(home) / "runtime" / "desktop", n)
