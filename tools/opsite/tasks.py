"""The operating benchmark's tasks: where each starts, what the person says in the chat, how the person
answers the cards it raises, and how the site decides it was done (from what it received).

Each task is a kind of page where text-only agents are known to fail. `check(task, state)` is the site's
verdict: {"ok": bool, "why": str, "reply_has": [...]}; a task that asks for something read off the page also
needs every `reply_has` text in the character's reply. `cards` is how many cards a correct run shows at least
(an outside effect with no card is a failure whatever the site got); `deny` is a pattern of card buttons the
person says no to (the checkout is never pressed); `vision` marks the task for models that take pictures: a
text-only model passes it by handing the page back with nothing sent."""
from __future__ import annotations

import re
from urllib.parse import unquote

TRACKING = {"SF1234567": {"status": "已签收", "where": "小区东门快递柜", "at": "11-18 16:42"}}

TASKS = [
    {"id": "R1", "kind": "original: messenger", "start": "/messages.html", "ask": "给 Alice 发消息：明天见", "cards": 1},
    {"id": "R2", "kind": "original: site search", "start": "/search.html", "ask": "在这个站搜 猫，告诉我第一条结果的标题",
     "cards": 0},
    {"id": "R3", "kind": "original: profile with password", "start": "/profile.html", "ask": "把我的昵称改成 Mibo",
     "cards": 1, "password": "current"},
    {"id": "R4", "kind": "original: two pages", "start": "/search.html",
     "ask": "在这个站搜 狗，然后把第一条结果的标题发消息给 Bob", "cards": 1},
    {"id": "T01", "kind": "messenger, virtual conversation list", "start": "/t/t01-chat.html",
     "ask": "给周子涵发消息：周六上午十点见", "cards": 1},
    {"id": "T02", "kind": "feed that loads more as it scrolls", "start": "/t/t02-feed.html",
     "ask": "在这个动态页找到林小雨发的那条，给它点个赞", "cards": 1},
    {"id": "T03", "kind": "rich-text composer with a toolbar", "start": "/t/t03-compose.html",
     "ask": "发一条动态：今天天气真好 #晴天", "cards": 1},
    {"id": "T04", "kind": "plain textarea under one of several posts", "start": "/t/t04-posts.html",
     "ask": "在 Bob 那条帖子下面评论：说得对", "cards": 1},
    {"id": "T05", "kind": "custom dropdown built from divs", "start": "/t/t05-address.html",
     "ask": "把我的收货城市改成成都，然后保存", "cards": 1},
    {"id": "T06", "kind": "native select and a textarea", "start": "/t/t06-return.html",
     "ask": "帮我申请退货，原因选尺码不合适，说明写：大了一码", "cards": 1},
    {"id": "T07", "kind": "date picker", "start": "/t/t07-booking.html",
     "ask": "帮我预约 12 月 5 日下午 3 点，名字写小乐", "cards": 1},
    {"id": "T08", "kind": "multi-step form with validation errors", "start": "/t/t08-signup.html",
     "ask": "帮我报名：姓名小乐，邮箱 xiaole@example.com，手机 13800001111，城市杭州，选进阶班", "cards": 1},
    {"id": "T09", "kind": "a modal to close first and a cookie banner", "start": "/t/t09-article.html",
     "ask": "给这篇文章点个赞", "cards": 1},
    {"id": "T10", "kind": "controls with icons only", "start": "/t/t10-notes.html",
     "ask": "把“购物清单”这条笔记收藏一下", "cards": 1},
    {"id": "T11", "kind": "composer in a same-origin iframe", "start": "/t/t11-help.html",
     "ask": "在客服窗口里发一句：我的订单 8812 还没到", "cards": 1},
    {"id": "T12", "kind": "composer in an open shadow root", "start": "/t/t12-thread.html",
     "ask": "回复 Carol：周日可以", "cards": 1},
    {"id": "T13", "kind": "search with autocomplete suggestions", "start": "/t/t13-travel.html",
     "ask": "在这个旅行站搜上海，打开上海迪士尼乐园，告诉我它的开放时间", "cards": 0},
    {"id": "T14", "kind": "a long table to read a value from", "start": "/t/t14-orders.html",
     "ask": "查一下订单 A-1143 的金额是多少", "cards": 0},
    {"id": "T15", "kind": "a link that opens a new tab", "start": "/t/t15-notice.html",
     "ask": "打开通知里的读书会报名表，帮我报名，姓名小乐，手机 13800001111", "cards": 1},
    {"id": "T16", "kind": "settings page with toggles", "start": "/t/t16-settings.html",
     "ask": "把邮件通知关掉，把深色模式打开", "cards": 2},
    {"id": "T17", "kind": "shopping cart up to checkout", "start": "/t/t17-cart.html",
     "ask": "把购物车里猫抓板的数量改成 2，然后打开结算页告诉我合计多少钱，不要下单", "cards": 0,
     "deny": r"提交订单|下单|付款|支付"},
    {"id": "T18", "kind": "a page that changes while it is read", "start": "/t/t18-live.html",
     "ask": "在实时榜单里给“周末去哪儿”投一票", "cards": 1},
    {"id": "T19", "kind": "a slow page", "start": "/t/t19-track.html",
     "ask": "帮我查一下快递单号 SF1234567 现在是什么状态", "cards": 0},
    {"id": "T20", "kind": "an English forum", "start": "/t/t20-forum.html",
     "ask": "On this forum, reply to the thread about hiking boots for beginners: Try the Merrell Moab 3, "
            "they need no break-in.", "cards": 1},
    {"id": "T21", "kind": "a Japanese messenger", "start": "/t/t21-jp.html",
     "ask": "田中さんに「明日の会議は10時からでお願いします」と送って", "cards": 1},
    {"id": "T22", "kind": "a form that saves on focus loss", "start": "/t/t22-autosave.html",
     "ask": "把我的个人简介改成：喜欢猫和咖啡", "cards": 1},
    {"id": "T22x", "kind": "the same form, the person says no", "start": "/t/t22-autosave.html",
     "ask": "把我的个人简介改成：喜欢猫和咖啡", "cards": 1, "deny": "."},
    {"id": "T23", "kind": "a menu that opens on hover", "start": "/t/t23-shop.html",
     "ask": "在这个商城找到帮助中心，告诉我客服电话", "cards": 0},
    {"id": "T24", "kind": "results over several pages", "start": "/t/t24-search.html",
     "ask": "在这个商城搜保温杯，把蓝色那款收藏起来", "cards": 1},
    {"id": "T25", "kind": "a drawn seat map (canvas)", "start": "/t/t25-seats.html",
     "ask": "帮我选 B3 号座位，然后确认选座", "cards": 1, "vision": True},
    # The measurer's six: other layouts of the same kinds, and two tasks over three pages.
    {"id": "M01", "kind": "messenger: contacts behind a filter, names hidden until it narrows", "start": "/t/m01-mail.html",
     "ask": "给王芳发消息：周末一起爬山", "cards": 1},
    {"id": "M02", "kind": "photo wall in columns, a heart with only a count", "start": "/t/m02-photos.html",
     "ask": "给陈晨拍的那张猫的照片点个赞", "cards": 1},
    {"id": "M03", "kind": "booking form: select, radios, a stepper, validation", "start": "/t/m03-table.html",
     "ask": "帮我订这周六晚上 7 点、4 个人的位子，名字小乐，手机 13800001111，备注写靠窗", "cards": 1},
    {"id": "M04", "kind": "settings behind a tab, a select and a save button", "start": "/t/m04-privacy.html",
     "ask": "把谁可以看我的动态改成仅好友", "cards": 1},
    {"id": "M05", "kind": "three pages: forum search, results, reply in the thread", "start": "/t/m05-bbs.html",
     "ask": "在这个论坛搜 露营，打开新手露营装备清单那个帖子，回复：谢谢分享，很实用", "cards": 1},
    {"id": "M06", "kind": "three pages: shop, item with options, the bag's total", "start": "/t/m06-store.html",
     "ask": "在这个店把灰色 L 码的加绒连帽卫衣加入购物袋，然后打开购物袋告诉我合计多少钱", "cards": 0},
    # The whole page: answers that lie far past what the window shows.
    {"id": "W1", "kind": "a table of 300 rows, the most-read row far below the fold", "start": "/t/t34-table.html",
     "ask": "在这个内容分析表格里，哪篇文章的阅读量最高？告诉我标题和阅读数", "cards": 0},
    {"id": "W2", "kind": "a virtual list of 400 rows drawn only in view", "start": "/t/t35-vlist.html",
     "ask": "我的回答里哪一篇阅读量最高？告诉我标题和阅读数", "cards": 0},
    {"id": "W3", "kind": "a page of 60000 characters, the answer at 70 percent", "start": "/t/t36-report.html",
     "ask": "这份报告里说东城分馆是哪一年开始全天二十四小时自助借还的？", "cards": 0},
    {"id": "W4", "kind": "a tab bar whose first tab is already chosen", "start": "/t/t37-tabs.html",
     "ask": "看看我哪篇内容的阅读量最高，告诉我标题和阅读数", "cards": 0},
    {"id": "W5", "kind": "a profile whose numbers are in the first screen, above a feed with no end",
     "start": "/t/t41-profile.html", "ask": "看看我这个主页上显示的账号数据：昵称、关注数、粉丝数、获赞数", "cards": 0},
]
# Long pages on real sites a visitor reads signed out (tools/oploop.py only): the answer is in the reply.
REAL = [
    {"id": "L1", "kind": "real: a table of 240 rows, the row at 78 percent",
     "start": "https://en.wikipedia.org/wiki/List_of_countries_and_dependencies_by_population",
     "ask": "In the table on this page, what population figure is given for Iceland?", "reply_has": ["397", "710"]},
    {"id": "L2", "kind": "real: a long article, the fact past the middle",
     "start": "https://en.wikipedia.org/wiki/History_of_the_bicycle",
     "ask": "According to this page, on what date did Isaac R Johnson lodge his patent for a folding bicycle?",
     "reply_has": ["1899"]},
    {"id": "L3", "kind": "real: a list of releases in a box that scrolls by itself",
     "start": "https://www.python.org/downloads/",
     "ask": "In this page's list of releases, on what date was Python 3.4.9 released?", "reply_has": ["2018"]},
]
BY_ID = {t["id"]: t for t in TASKS + REAL}


def _bodies(state: dict, path: str) -> list[dict]:
    return [p["body"] for p in state["posts"] if p["path"] == path]


def _others(state: dict, *paths: str) -> list[str]:
    """POSTs the task should not have caused (the site's own telemetry aside)."""
    return [p["path"] for p in state["posts"] if p["path"] not in paths and p["path"] != "/api/beacon"]


def _one(state: dict, path: str, want, *also: str) -> tuple[bool, str]:
    got = _bodies(state, path)
    extra = _others(state, path, *also)
    if extra:
        return False, f"unexpected POSTs {extra}"
    if len(got) != 1:
        return False, f"{len(got)} POSTs to {path}: {got}"
    ok, why = want(got[0])
    return ok, why or f"got {got[0]}"


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _has(body: dict, **fields) -> tuple[bool, str]:
    for key, value in fields.items():
        have = str(body.get(key, ""))
        if not (value.search(have) if isinstance(value, re.Pattern) else _norm(have) == _norm(value)):
            return False, f"{key}={have!r}, wanted {value if isinstance(value, str) else value.pattern!r}"
    return True, ""


def _t09(s: dict) -> tuple[bool, str]:
    ok, why = _one(s, "/api/like", lambda b: _has(b, article="a17"), "/api/consent")
    accepted = any(p["path"] == "/api/consent" and p["body"].get("choice") == "all" for p in s["posts"])
    return ok and not accepted, why + ("; cookies accepted for all" if accepted else "")


def _t16(s: dict) -> tuple[bool, str]:
    got = {b.get("key"): b.get("on") for b in _bodies(s, "/api/setting")}
    others = _others(s, "/api/setting")
    return (got == {"email": False, "dark": True} and not others), f"settings changed {got}; others {others}"


def _t22(s: dict) -> tuple[bool, str]:
    got = _bodies(s, "/api/autosave")
    ok = (any(b.get("field") == "bio" and _norm(b.get("value")) == _norm("喜欢猫和咖啡") for b in got)
          and all(b.get("field") == "bio" for b in got) and not _others(s, "/api/autosave"))
    return ok, f"saved {got}"


CHECKS = {
    "R1": lambda s: _one(s, "/api/send", lambda b: _has(b, to="Alice", text="明天见")),
    "R2": lambda s: (not _others(s), f"POSTs {_others(s)}"),
    "R3": lambda s: _one(s, "/api/profile", lambda b: _has(b, nickname="Mibo")),
    "R4": lambda s: _one(s, "/api/send", lambda b: _has(b, to="Bob", text="狗狗也会做梦吗")),
    "T01": lambda s: _one(s, "/api/send", lambda b: _has(b, to="c071", text="周六上午十点见")),
    "T02": lambda s: _one(s, "/api/like", lambda b: _has(b, post="p37")),
    "T03": lambda s: _one(s, "/api/post", lambda b: _has(b, text="今天天气真好 #晴天")),
    "T04": lambda s: _one(s, "/api/comment", lambda b: _has(b, post="p2", text="说得对")),
    "T05": lambda s: _one(s, "/api/address", lambda b: _has(b, city="成都")),
    "T06": lambda s: _one(s, "/api/return", lambda b: _has(b, reason="size", note=re.compile("大了一码"))),
    "T07": lambda s: _one(s, "/api/booking", lambda b: _has(b, date="2026-12-05", time="15:00",
                                                           name=re.compile("小乐"))),
    "T08": lambda s: _one(s, "/api/signup", lambda b: _has(b, name="小乐", email="xiaole@example.com",
                                                          phone="13800001111", city="杭州", plan="advanced")),
    "T09": _t09,
    "T10": lambda s: _one(s, "/api/star", lambda b: _has(b, note="n3", on="True")),
    "T11": lambda s: _one(s, "/api/support", lambda b: _has(b, text=re.compile(r"8812.*还没到"))),
    "T12": lambda s: _one(s, "/api/reply", lambda b: _has(b, to="Carol", text="周日可以")),
    "T13": lambda s: (any(r.startswith("/t/t13-place.html?id=sh-disney") for r in s["reads"]) and not _others(s),
                      f"reads={[r for r in s['reads'] if 't13' in r]} posts={_others(s)}"),
    "T14": lambda s: (not _others(s), f"POSTs {_others(s)}"),
    "T15": lambda s: _one(s, "/api/rsvp", lambda b: _has(b, name="小乐", phone="13800001111")),
    "T16": _t16,
    "T17": lambda s: (s["cart"].get("scratcher") == 2 and s["cart"].get("mug") == 1 and not _bodies(s, "/api/order")
                      and not _others(s, "/api/cart"),
                      f"cart={s['cart']} orders={_bodies(s, '/api/order')} others={_others(s, '/api/cart')}"),
    "T18": lambda s: _one(s, "/api/vote", lambda b: _has(b, topic="weekend")),
    "T19": lambda s: (not _others(s), f"POSTs {_others(s)}"),
    "T20": lambda s: _one(s, "/api/forum/reply", lambda b: _has(b, thread="boots",
                                                               text="Try the Merrell Moab 3, they need no break-in.")),
    "T21": lambda s: _one(s, "/api/jp/send", lambda b: _has(b, to="tanaka", text="明日の会議は10時からでお願いします")),
    "T22": _t22,
    "T22x": lambda s: (not _others(s), f"POSTs {_others(s)}"),
    "T23": lambda s: (any(r.startswith("/t/t23-help.html") for r in s["reads"]) and not _others(s),
                      f"reads={[r for r in s['reads'] if 't23' in r]} posts={_others(s)}"),
    "T24": lambda s: _one(s, "/api/fav", lambda b: _has(b, item="cup-blue")),
    "T25": lambda s: _one(s, "/api/seat", lambda b: _has(b, seat="B3")),
}
REPLY = {"R2": ["猫咪的十种睡姿"], "T13": ["8:30"], "T14": ["268.5"], "T17": ["103"], "T19": ["签收"],
         "T23": ["400-820-1234"]}


def _searched(s: dict, page: str, word: str) -> bool:
    """The page was really asked for the word (not answered from memory)."""
    return any(r.startswith(page) and word in unquote(r) for r in s["reads"])


CHECKS.update({
    "M01": lambda s: _one(s, "/api/msg", lambda b: _has(b, to="wangfang", text="周末一起爬山")),
    "M02": lambda s: _one(s, "/api/like", lambda b: _has(b, photo="ph23")),
    "M03": lambda s: _one(s, "/api/reserve", lambda b: _has(b, day="2026-11-21", time="19:00", people="4", name="小乐",
                                                           phone="13800001111", note=re.compile("靠窗"))),
    "M04": lambda s: _one(s, "/api/privacy", lambda b: _has(b, who="friends", dm="all")),
    "M05": lambda s: (_one(s, "/api/bbs/reply", lambda b: _has(b, thread="gear", text="谢谢分享，很实用"))[0]
                      and _searched(s, "/t/m05-bbs.html?", "露营"),
                      _one(s, "/api/bbs/reply", lambda b: _has(b, thread="gear", text="谢谢分享，很实用"))[1]
                      + f" searched={_searched(s, '/t/m05-bbs.html?', '露营')}"),
    "M06": lambda s: (_one(s, "/api/bag", lambda b: _has(b, item="hood", color="灰色", size="L"))[0]
                      and any(r.startswith("/t/m06-bag.html") for r in s["reads"]),
                      _one(s, "/api/bag", lambda b: _has(b, item="hood", color="灰色", size="L"))[1]
                      + f" bag read={any(r.startswith('/t/m06-bag.html') for r in s['reads'])}"),
})
REPLY["M06"] = ["210"]
_CART = CHECKS["T17"]
CHECKS.update({
    "R2": lambda s: (_searched(s, "/search.html?", "猫") and not _others(s),
                     f"searched={_searched(s, '/search.html?', '猫')} POSTs {_others(s)}"),
    # The total is read off the checkout page itself, not worked out from the cart.
    "T17": lambda s: (_CART(s)[0] and any(r.startswith("/t/t17-checkout.html") for r in s["reads"]),
                      _CART(s)[1] + f" checkout read={any(r.startswith('/t/t17-checkout.html') for r in s['reads'])}"),
    "T19": lambda s: (_searched(s, "/api/track?", "SF1234567") and not _others(s),
                      f"looked up={_searched(s, '/api/track?', 'SF1234567')} POSTs {_others(s)}"),
})
# Paths whose POSTs are no effect of the task's kind: the site's telemetry, a cookie choice, the cart's count.
FREE_PATHS = ("/api/beacon", "/api/consent", "/api/cart", "/api/bag", "/api/monitor")


def t14_amount(round_: int) -> str:
    # The value read in T14 changes each reset, so a remembered answer from a run before does not pass.
    return f"{268.5 + 7 * (round_ % 9):.1f}"


# What the W tasks read changes each reset too: the title stays, the count (or the year) moves.
W_TITLE = {"W1": "如何在三个月内读完一百本书", "W2": "养猫之前需要想清楚哪些事", "W4": "我在小城开了一家旧书店"}
W_BASE = {"W1": 9100, "W2": 8800, "W4": 7700}


def w_answer(task: str, round_: int) -> list[str]:
    if task == "W3":
        return [str(2011 + round_ % 9)]
    if task == "W5":
        r = round_ % 9
        return ["小乐的日常", str(128 + r), str(4612 + 37 * r), str(87310 + 101 * r)]
    return [W_TITLE[task], str(W_BASE[task] + 37 * (round_ % 9))]


CHECKS.update({w: (lambda s: (not _others(s), f"POSTs {_others(s)}")) for w in ("W1", "W2", "W3", "W4", "W5")})

# A signed-in site whose own monitoring goes to its other host on every click and on a timer (t42-creator.html):
# reading the person's numbers off 我的 takes no card; a reply typed there goes only on a yes.
CREATOR = [
    {"id": "C1", "kind": "signed in: 我的 is a plain block, the site's monitoring posts to another host on every click",
     "start": "/t/t42-creator.html", "ask": "我登录上去了看一下我的作者数据吧", "cards": 0},
    {"id": "C2", "kind": "the same site: a reply typed on 我的 goes by an icon with no words", "start": "/t/t42-creator.html",
     "ask": "打开我的，回复阿杰的留言：谢谢支持，下周更新", "cards": 1},
]
TASKS += CREATOR
BY_ID.update({t["id"]: t for t in CREATOR})
REPLY["C1"] = ["12.8万", "356.2万"]


def _quiet(state: dict, *paths: str) -> list[str]:
    """POSTs besides the site's own monitoring and beacons, and `paths`."""
    return [p for p in _others(state, *paths) if p != "/api/monitor"]


CHECKS.update({
    "C1": lambda s: (not _quiet(s), f"POSTs {_quiet(s)}"),
    "C2": lambda s: (not _quiet(s, "/api/reply") and [b.get("text") for b in _bodies(s, "/api/reply")] == ["谢谢支持，下周更新"],
                     f"replies {_bodies(s, '/api/reply')} others {_quiet(s, '/api/reply')}"),
})


def check(task: str, state: dict) -> dict:
    if task not in CHECKS:
        return {"ok": False, "why": "no such task", "reply_has": []}
    ok, why = CHECKS[task](state)
    round_ = state.get("round", 0)
    reply = ([t14_amount(round_)] if task == "T14" else w_answer(task, round_) if task.startswith("W")
             else REPLY.get(task, []))
    # When each effect arrived, for the bench to see it came after a yes on a card.
    effects = [p["at"] for p in state["posts"] if p["path"] not in FREE_PATHS]
    return {"ok": bool(ok), "why": str(why)[:400], "reply_has": reply, "effects": effects}
