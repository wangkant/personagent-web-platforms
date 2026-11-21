"""Doing something in a page of the person's browser for them, when they ask in chat.

Two halves, in two processes. `Hands` lives where the pane is (the app window's process): it looks at a tab's page
(pageops), sorts every step through the gate (gate.py) and, for anything with an effect outside, shows the person a
card built from the page itself and waits for their press. Nothing it is sent can get around that: the model's words
never count as consent. There is no loop of its own here: the reply model calls act (surface.py) with a few steps on
a place's tab, out of sight or in the middle, and decides itself from what comes back whether the task is done, as
the mature page tools leave it to the host's model (Playwright MCP, Chrome DevTools MCP, Claude in Chrome). One act
call is `Hands.steps`: each step's line, then what changed (the address, the title) and a Noticed line of what the
app saw meanwhile (what went out, the page's dialogs, text typed and not sent).

While it acts, Hands also hears the page's own requests that can change something (any that is not an image, a
script, a style or a font) and its navigations. What is judged is the act, as Claude in Chrome, ChatGPT agent and
Gemini's computer use judge it: the press and what it does for the person, by its name, its place (a 好的 in a box
that asks about deleting deletes: gate.py), the form it submits and what was typed. The page's own traffic counts
only where it carries the act: a request that carries text typed here (a form that saves by itself when a field
loses the focus) or a choice made here is held before it leaves and put to the person on the same card as a send;
so is what a press code could not sort sends to the page's own site (or its form's) that is not a GET (a save, a
submission), and a DELETE on any press. Every other request goes on untouched: a site's monitoring, beacons and
reads to its other hosts on every click and timer are not anyone's act, so a press that only moves about a site
never reaches a card or the reviewer. One act asks once: the requests one press holds share one card, and the answer
is kept (`kept`, shared across the turn's act calls). In automatic review the reviewer sees a held request (its
method, address and a summary of its body) and judges it. Once something was typed, images, scripts and styles are
heard too, so none carries it to another site. How much asks is the person's mode (acting.py), read at every step:
read only presses nothing that sends, automatic review puts sends and presses to the reviewer first, and paying,
deleting and accounts ask in every mode. Going to another site by an address the model wrote is free, unless the
address carries words read on a page (`_carried`): that is refused outright; the page going to another site by its
own script waits for the reviewer or a card (leave). Each step is shown before it happens, in the person's words.

The step verbs are the converged page tools' (Playwright MCP's browser_click, browser_type, browser_select_option,
browser_press_key, browser_hover, browser_drag, browser_wait_for, browser_file_upload, browser_navigate and
browser_navigate_back; Claude in Chrome's scroll_to), each with a short line of what it does (Playwright MCP's
`element`, Claude's action_summary) that a gate card shows: `action_of` maps them to what Hands.act takes. A press
that changed nothing says so. A double click on a control with an effect outside is refused (it would press it
twice); a right click, a drag and a keyboard shortcut are watched as a bare 确认 is, so what they send waits for a
card; a chord ending in Enter is Enter; the clipboard is never used. `read` answers a question from what the page's
document holds, nothing scrolled, loading more only when the answer needs it (`read_page`, web/pagetext.py).

What the page asks for itself goes to the person on a card too (gate's upload, download and dialog), as Playwright's
file chooser, download and dialog events are handled: a file it wants goes into its field (DOM.setFileInputFiles)
once the person names or chooses it and says yes; a file it saves goes to the download folder on their yes; its own
confirm or prompt is answered Cancel at once and, on their yes, the step is done again with it answered OK. An alert
is closed and its words put on the step's line.

What a step says of itself is checked by code, never left to the model's word (tools/optruth.py measures it). A
click or a typing names its control, and an id whose control is another one is refused. A step's line names the
control and says what it changed: the control's own state and the words that came up on the page. A press that went
out once on the person's yes is not made again, nor the same words to the same place.
"""
from __future__ import annotations

import asyncio
import base64
import html
import io
import json
import logging
import re
import secrets
import time
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qsl, unquote_plus, urlsplit

from . import gate, pagetext, signin
from .pageops import STATE_MAX, STATE_MOST, Page, Refused, chord, clip, fold, quote, view_of

logger = logging.getLogger("mibo.web")

# Presses of one control in one click (`times`): a stepper's +, a calendar's next-month arrow.
TIMES_MAX = 24
# Steps one act call takes (the reply model's act tool).
STEPS_MAX = 8
# How long after an action a request it set off is waited for; after a confirmed press, how long its own
# requests are let out.
HEAR_S = 0.4
PRESS_TAIL_S = 1.0
# How long a card waits for the person, and how long a press is watched for its result.
CARD_S = 170.0
PROOF_S = 12.0
# Requests an act's Noticed line names.
WENT_SHOWN = 6

# A sign-in form, a check, a payment page: the page goes back to the person (what pageops sees is not enough).
WALL_JS = r"""() => {
  const shown = (e) => e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const frames = [...document.querySelectorAll('iframe')].filter((f) => shown(f) && f.offsetWidth > 60 &&
    /captcha|challenge|turnstile|geetest|verify|hcaptcha|recaptcha\/api2\/anchor/i.test(f.src || ''));
  if (frames.length) return {kind: 'verification', evidence: (frames[0].src || '').slice(0, 80)};
  const body = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ');
  // A short page that only says to sign in first, whatever its title calls itself: a sign-in, with no check to pass.
  if (body.length < 1500 && /请先?登录|登录后(重试|继续|查看|访问)|(log|sign) ?in to (continue|view|see|access)|(must|need to) (be )?(log|sign)(ged|ed)? ?in/i.test(body))
    return {kind: 'sign_in', evidence: 'sign-in notice'};
  const top = document.title + ' ' + body.slice(0, 600);
  if (/just a moment|verify you are human|are you a robot|人机验证|安全验证|滑动验证|环境异常|ロボットではありません|자동입력 방지|проверка.{0,20}робот/i.test(top))
    return {kind: 'verification', evidence: top.slice(0, 80)};
  for (const form of document.querySelectorAll('form')) {
    if (!form.querySelector('input[type=password]') || !shown(form)) continue;
    const words = [...form.querySelectorAll('button,input[type=submit]')].map((b) => b.innerText || b.value || '').join(' ');
    if (/log\s?in|sign\s?in|登录|登入|ログイン|로그인|войти/i.test(words)) return {kind: 'sign_in', evidence: words.trim().slice(0, 80)};
  }
  // A canvas, map or game filling a fifth of the window or more: drawn, not written.
  const drawn = [...document.querySelectorAll('canvas,embed,object')].filter((e) => {
    const r = e.getBoundingClientRect();
    return shown(e) && r.width * r.height >= 0.2 * innerWidth * innerHeight;
  }).map((e) => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; });
  return {kind: '', evidence: '', drawn};
}"""

# A native list's options, for the select (or its label) clicked: a click opens a list no page can show.
OPTIONS_JS = """function(){const e = this.nodeType === 1 ? this : this.parentElement;
  const s = e.tagName === 'SELECT' ? e : e.control && e.control.tagName === 'SELECT' ? e.control : null;
  return s ? [...s.options].map((o) => o.text.trim()).filter(Boolean).slice(0, 40) : null;}"""

# A link that opens a new tab: its address, to open in this one (the run works in one page).
BLANK_JS = """function(){const a = (this.nodeType === 1 ? this : this.parentElement).closest('a[href]');
  return a && a.target === '_blank' && /^https?:/.test(a.href) ? a.href : null;}"""

# A field typed into, given back what it held before, unless someone changed it since: (typed, before).
RESTORE_JS = r"""function(pair) {
  const [typed, before] = pair;
  const e = this.nodeType === 1 ? this : this.parentElement;
  if (!e || !e.isConnected || ((e.isContentEditable ? e.innerText : e.value) || '').trim() !== typed) return false;
  if (e.isContentEditable) e.innerText = before;
  else Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), 'value').set.call(e, before);
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

# The words the page shows, one entry a text node, through same-site frames and shadow roots (pageops.VISIBLE_JS
# keeps them as one string): what an action brought up is what was not there before it.
LINES_JS = r"""() => {
  const out = [], shown = new Map(), roots = [document];
  for (let i = 0; i < roots.length; i++) {
    const walk = document.createTreeWalker(roots[i], NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n && out.length < 4000; n = walk.nextNode()) {
      if (n.nodeType === Node.ELEMENT_NODE) {
        if (n.shadowRoot) roots.push(n.shadowRoot);
        if (n.contentDocument) roots.push(n.contentDocument);
        continue;
      }
      const text = n.textContent.replace(/\s+/g, ' ').trim(), e = n.parentElement;
      if (!e || !text) continue;
      if (!shown.has(e)) shown.set(e, e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
      if (shown.get(e)) out.push(text.slice(0, 120));
    }
  }
  return out;
}"""

# A control before and after an action: what it says of itself (checked, pressed, expanded, selected; "on" where
# only its class says so, as pageops reads a filled star) and the words the page shows.
SEEN_JS = r"""function() {
  const e = this.nodeType === 1 ? this : this.parentElement;
  const said = (name) => e.getAttribute(name) === null ? null : e.getAttribute(name) === 'true';
  return {lines: (""" + LINES_JS + r""")(), state: {
    checked: e.matches('input[type=checkbox],input[type=radio]') ? e.checked : said('aria-checked'),
    pressed: said('aria-pressed'), expanded: said('aria-expanded'),
    selected: e.tagName === 'OPTION' ? e.selected : said('aria-selected'),
    on: /(^|\s)(on|active|is-active|is-on|liked|starred|selected|checked|pressed|faved)(\s|$)/.test(
      typeof e.className === 'string' ? e.className : '')}};
}"""
# How many of the words an action brought up its line holds: a click's, and a hover's (a menu's entries).
CAME_UP = 2
CAME_UP_HOVER = 8

# What a role is called, beside its own word: a control named only by its kind ("输入框", "the button") is not
# taken for another one.
KINDS = {"textbox": "input field box 输入框 文本框", "searchbox": "search box 搜索框", "button": "btn 按钮",
         "link": "链接", "checkbox": "check 复选框 勾选", "radio": "单选", "combobox": "dropdown select list 下拉 选择 列表",
         "select": "dropdown list 下拉 选择 列表", "listbox": "list 列表", "option": "选项", "tab": "标签 选项卡",
         "menuitem": "menu item 菜单", "switch": "toggle 开关", "slider": "滑块", "clickable": "", "text": ""}
CJK = "぀-ヿ㐀-鿿가-힯"

# Why a step did not work, as the column beside the page tells the person; the first pattern found in the line.
WHY = [("covered", r"covered by another element"), ("hidden", r"Target hidden|outside viewport"),
       ("disabled", r"Target disabled"), ("other", r" is not \".*: nothing was done"),
       ("picker", r"opens a picker|does not take"),
       ("changed", r"^\[changed\]"), ("gone", r"detached|not on the (latest )?page|drawn anew"), ("back", r"no page before")]
WHY_SAID = {
    "zh": {"covered": "被其他内容遮挡，未点击。", "hidden": "当前不可见，未点击。", "disabled": "当前不可用，未点击。",
           "other": "与网页上的名称不符，未执行。", "picker": "该字段未接受输入的内容。",
           "changed": "网页内容在确认后发生变化，未执行。", "gone": "已不在网页上，未执行。", "back": "没有可返回的上一页。"},
    "en": {"covered": "Something else covers this control; it was not clicked.",
           "hidden": "This control is not in view now; it was not clicked.", "disabled": "This control is turned off now.",
           "other": "The control named is not the one on the page; nothing was done.",
           "picker": "The field did not take what was typed.",
           "changed": "The page changed after you confirmed; nothing was done.", "gone": "This control is no longer on the page.",
           "back": "There is no page to go back to."},
}

# What a person reads in the step log for each action, before it happens.
SAID = {
    "zh": {"click": "点击「{name}」", "type": "在「{name}」输入「{text}」", "press": "按 {key}", "hover": "将指针移到「{name}」",
           "down": "向下滚动", "up": "向上滚动", "in": "（在「{name}」中）", "until": "，直到出现「{text}」",
           "navigate": "打开 {host}", "back": "返回上一页", "wait": "等待页面加载", "find": "在页面上查找「{text}」",
           "box": "输入框", "point": "画面上的某个位置", "times": "（{n} 次）", "held": "网页将发送刚才输入的内容",
           "chose": "网页将保存刚才的选择", "leave": "网页提示：离开此网页后，未保存的内容可能会丢失。",
           "double": "双击「{name}」", "right": "右键单击「{name}」", "drag": "将「{name}」拖到「{to}」",
           "shows": "等待「{text}」出现", "gone": "等待「{text}」消失", "read": "读取网页内容：{text}",
           "more": "加载更多网页内容：{text}"},
    "en": {"click": "Click “{name}”", "type": "Type “{text}” into “{name}”", "press": "Press {key}",
           "hover": "Point at “{name}”", "down": "Scroll down", "up": "Scroll up", "in": " in “{name}”",
           "until": " until “{text}” shows", "navigate": "Open {host}", "back": "Go back",
           "wait": "Wait for the page", "find": "Look for “{text}” on the page", "box": "the box",
           "point": "a point on the picture", "times": " ({n} times)",
           "held": "The page sending what was typed", "chose": "The page saving the choice just made",
           "leave": "The page warns that what is not saved may be lost if you leave it.",
           "double": "Double-click “{name}”", "right": "Right-click “{name}”", "drag": "Drag “{name}” onto “{to}”",
           "shows": "Wait for “{text}” to show", "gone": "Wait for “{text}” to go",
           "read": "Read the page: {text}", "more": "Load more of the page: {text}"},
}
# Requests the page makes while the character operates that are paused and looked at: navigations, and the kinds
# that can send or change something. Images, scripts, styles, fonts and media load untouched until something is
# typed (TYPED_WATCHED): from then on a picture's address could carry it to another site.
WATCHED = [{"urlPattern": "*", "resourceType": kind, "requestStage": "Request"}
           for kind in ("Document", "XHR", "Fetch", "Ping", "Other")]
TYPED_WATCHED = WATCHED + [{"urlPattern": "*", "resourceType": kind, "requestStage": "Request"}
                           for kind in ("Image", "Script", "Stylesheet", "Font", "Media")]
# A GraphQL body that only asks (no mutation): a request a plain press may set off without a card.
ASKS_ONLY = re.compile(r"\s*(?:query\b|\{)")
# After a link the run followed or an address it was let go to, how long the page may go on to other sites by
# itself (a redirect, a search result's hop) without a card.
FOLLOW_S = 10.0
# Words read in the run that an address to another site may not carry, characters running.
READ_RUN = 8
# Controls that make a choice; a post one sets off is held as a setting saved (Hands._choosing).
CHOICES = ("checkbox", "radio", "menuitemcheckbox", "menuitemradio", "option", "combobox", "listbox")
# A base64 run in a body long enough to be typed text, decoded and looked in too.
BASE64 = re.compile(r"[A-Za-z0-9+/_-]{8,}={0,2}")
# A typed text this short must be a whole value of the request to count as carried by it.
CARRIED_MIN = 3


def _shape(url: str) -> str:
    """An address's path with its ids made {id}, for matching a stored step to the page it was on."""
    path = urlsplit(url).path or "/"
    return re.sub(r"/(?:\d+|[0-9a-zA-Z_-]{16,})(?=/|$)", "/{id}", path)


def _squeeze(s: str) -> str:
    return re.sub(r"\s+", "", html.unescape(s))


def _texts(request: dict) -> list[str]:
    """What a paused request says, every way it may say it: its address and body as sent, form-decoded, its JSON
    leaves, and base64 runs decoded."""
    body, url, values = _leaves(request)
    texts = [body, unquote_plus(body), unquote_plus(url), " ".join(values)]
    for run in BASE64.findall(body + " " + unquote_plus(url)):
        try:
            texts.append(base64.b64decode(run.replace("-", "+").replace("_", "/") + "=" * (-len(run) % 4)).decode("utf-8"))
        except ValueError:
            pass
    return texts + values


def _leaves(request: dict) -> tuple[str, str, list[str]]:
    """A paused request's body, its address, and the values in both."""
    body = str(request.get("postData") or "")
    # The entries are the same body in parts, read when the body itself was not given.
    for entry in [] if body else request.get("postDataEntries") or []:
        try:
            body += base64.b64decode(entry.get("bytes") or "").decode("utf-8", "replace")
        except ValueError:
            pass
    url = str(request.get("url") or "")
    values = [v.strip() for v in _values(body)] if body else []
    values += [v.strip() for _, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)]
    return body, url, values


def _bears(texts: list[str], text: str) -> bool:
    """`text` is in what a request says, as typed, JSON-escaped, or with its spaces and HTML entities aside; one
    shorter than CARRIED_MIN only as a whole value."""
    t = text.strip()
    if len(t) < CARRIED_MIN:
        return bool(t) and t in texts
    if any(t in x or json.dumps(t)[1:-1] in x for x in texts):
        return True
    squeezed = _squeeze(t)
    return len(squeezed) >= CARRIED_MIN and any(squeezed in _squeeze(x) for x in texts)


def _words(text: str) -> tuple[set[str], set[str]]:
    """A name's words, for telling one control from another: runs of letters and digits and pairs of CJK
    characters (a CJK character alone where it stands alone), and its CJK characters one by one."""
    low = fold(text)
    runs = re.findall(rf"[{CJK}]+", low)
    return (set(re.findall(rf"[^\W_{CJK}]+", low)) | {run[i:i + 2] for run in runs for i in range(max(len(run) - 1, 1))},
            {c for run in runs for c in run})


def _why(line: str, lang: str) -> str:
    """Why a step failed, in the person's words; "" where the line gives no reason they could use."""
    return next((WHY_SAID[lang][key] for key, pattern in WHY if re.search(pattern, line)), "")


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB"


def _item(facts: dict) -> str:
    """The item a control acts on as the card quotes it, with its counts and spaces aside."""
    return re.sub(r"[\d\s.,]+", "", str(facts.get("row") or ""))


def _values(body: str) -> list[str]:
    """The leaf values of a JSON or form body, as text."""
    try:
        found: list[str] = []

        def leaves(v) -> None:
            if isinstance(v, dict):
                for x in v.values():
                    leaves(x)
            elif isinstance(v, list):
                for x in v:
                    leaves(x)
            elif v is not None:
                found.append(str(v))
        leaves(json.loads(body))
        return found
    except ValueError:
        return [v for _, v in parse_qsl(body, keep_blank_values=True)]


def _asks_only(request: dict) -> bool:
    """A GraphQL request whose every operation is a query written out: it reads and changes nothing."""
    try:
        body = json.loads(str(request.get("postData") or ""))
    except ValueError:
        return False
    items = body if isinstance(body, list) else [body]
    return bool(items) and all(isinstance(i, dict) and isinstance(i.get("query"), str) and ASKS_ONLY.match(i["query"])
                               for i in items)


def _summary(request: dict) -> dict:
    """A request as the reviewer reads it: its method, its address without the query, and the short values of its
    query and body (ids and long runs aside)."""
    body, url, values = _leaves(request)
    parts = urlsplit(url)
    short = [v for v in dict.fromkeys(values) if v and len(v) <= 80][:16]
    return {"method": str(request.get("method") or "GET").upper(), "address": f"{parts.netloc}{parts.path}"[:200],
            "values": short, "body_chars": len(body)}


def _reading(kind: str, does: str) -> str:
    """The card's word for an act: the reviewer's reading of an outward act, else the gate's own ("press" for an
    outward act nothing read)."""
    if does in ("send", "comment", "like", "post", "submit"):
        return does
    if does == "follow":
        return "like"
    return "press" if kind == "outward" else kind


def _leaving(url: str) -> dict:
    """Going to another site as the reviewer reads it: the address, and its query and fragment as the text it
    carries (where an address or a number the person never gave would ride along)."""
    parts = urlsplit(url)
    return {"target": [url[:300]], "text": unquote_plus(" ".join(filter(None, [parts.query, parts.fragment])))}


class Hands:
    """The pane's side: looks and acts on `tab`, every action through the gate. `ask(card)` shows the
    person a card and answers {"yes": bool, "never": bool}, {"waiting": the act in the person's words} when
    its time ran out with the card still up (the act ends waiting on it, and it stays answerable), or None
    when the person took the page or stopped; `kept` holds the answers to this turn's acts, by act, so one
    act asks once however often it comes (a card left waiting and answered later keeps its answer); `attended()`
    says the person is at the app (only then is a card's tick let to skip a card); `held()` says why the
    run may not go on ("" when it may: "stopped", "paused" when the person took the page); `tell(line)`
    shows the person the step about to happen, in `lang`.

    While it runs, a request the page makes that carries text typed in this run is held before it leaves (a
    form that saves itself when a field loses the focus, on a timer, by a beacon as the page is left; a GET
    only when it goes to another site, as a new tab or a fetch): the card is shown for it as for a send, and it
    goes on yes and is dropped on no. So is what a watched press sends to the page's own site that is not a GET,
    a DELETE on any press, and the page going to a site that is not the run's. A confirmed press lets out only its own site's requests,
    and only when what it sends carries the words the card showed. Requests that carry none of it pass
    untouched, so the site's own reads and telemetry go on as they would; images, scripts, styles and fonts
    are not paused at all. A file chooser, a download and the
    page's own dialogs are caught and put to the person on cards; `downloads()` gives the files the pane began
    to save since it was last asked, each held until its `answer(yes)`. `close()` ends that, and after a run the
    person said no to or stopped, puts back what was typed and never let out, so the page cannot send it later
    by itself.

    `mode()` is the person's mode for the browser (acting.py), read at every action; `review(action)` is the
    reviewer's verdict (review.decide) for what acting.decide sends it; `said()` is the person's own words of the
    turn under way (an address may carry what they said, never what a page showed); `page` is the tab's Page the
    looks read, so a look's refs are the steps' refs."""

    def __init__(self, tab, *, ask: Callable[[dict], Awaitable[Optional[dict]]], allowed: gate.Allowed,
                 attended: Callable[[], bool], held: Callable[[], str] = lambda: "",
                 tell: Callable[[str], None] = lambda line: None, lang: str = "zh",
                 downloads: Callable[[], list[dict]] = lambda: [], mode: Callable[[], str] = lambda: "auto",
                 review: Optional[Callable[[dict], Awaitable]] = None, kept: Optional[dict] = None,
                 said: Callable[[], str] = lambda: "", page: Optional[Page] = None):
        self.page = page or Page(tab)
        self.said = said
        self.kept = {} if kept is None else kept
        self.tab = tab
        # Every card carries the step's own line of what it does.
        self.ask = lambda card: ask({**card, "what": self._what})
        self.allowed = allowed
        self.attended = attended
        self.held = held
        self.tell = tell
        self.downloads = downloads
        self.mode = mode
        self.review = review
        self.lang = "zh" if str(lang).startswith("zh") else "en"
        self.confirmed = False
        # What was typed this run into fields that are not search boxes: {"text", "field", "site", "node",
        # "before", "out"}; "out" once the person let it out. Requests carrying one of these are held, all run long.
        self.typed: list[dict] = []
        self._paused: Optional[asyncio.Queue] = None
        # Held requests, each with the typed entries it carried when it was held.
        self._holding: list[tuple[dict, list[dict]]] = []
        # A confirmed press under way: {"site", "said", "matched", "aside"}.
        self._pressing: Optional[dict] = None
        # A choice being made (a box ticked, a radio, a list's option): {"text", "field", "site", "out", "choice"}.
        # A post it sets off is a setting saved the moment it changed, held as an account change.
        self._choice: Optional[dict] = None
        # The file chooser the page opened (Page.fileChooserOpened), for the step that opened it.
        self._file: Optional[dict] = None
        # The page's own dialogs: lines of what it said, a question a step opened and that waits for its card,
        # the question the person said yes to (answered OK when it comes again), whether a step is under way.
        self._told: list[str] = []
        self._asked: Optional[dict] = None
        self._again: Optional[tuple[str, str]] = None
        self._acting = False
        self._closed = False
        self._drain: Optional[asyncio.Task] = None
        # Outside actions pressed in this run, with what the page showed after: a like pressed twice is undone, and
        # anything pressed again on a page that has not changed since is done twice.
        self.pressed: dict[tuple, int] = {}
        # Words that went out in this run on the person's yes, by kind, page and recipient: the same again would
        # send them twice.
        self.sent: set[tuple] = set()
        # The run's sites: where it began, those the person named or said yes to, those a link led to. Until
        # `_follow_until` the page may go on to others by itself (a link's redirects).
        self.sites: set[str] = set()
        self._follow_until = 0.0
        # The main frame, whose navigations are the page's own, and the address it last went to.
        self._frame = ""
        self._address = ""
        # A bare submission under way (gate's "watch"): what the card says if a request leaves on it.
        self._watching: Optional[dict] = None
        # The page's own navigations held, each with its card under way; how the last one not let go ended
        # ({"status": "declined" or "waiting", "kind", "result"}).
        self._navs: list[tuple[asyncio.Task, dict]] = []
        self._turned: dict = {}
        # While the person signs in, the page is theirs: Fetch's settings as they were, to turn it on again after.
        self._released: Optional[dict] = None
        # The reviewer's verdicts in this run, by action: the same control and request is not asked about twice.
        self._verdicts: dict[str, object] = {}
        # Where each box that scrolls by itself stood after a read's last step on.
        self._read_at: dict = {}
        # Every page's words the model was shown from this tab: no address to another site carries them (_carried).
        self.read: list[str] = []
        # The step under way's own line of what it does, for its card; while an act call runs, the requests let out
        # and the page's dialogs, for its Noticed line.
        self._what = ""
        self._noting = False
        self._went: list[tuple[str, str]] = []
        self._dialogs: list[str] = []
        # The answers' status codes, by the browser's request id, for what went out.
        self._status: dict[str, int] = {}

    # --- requests that carry what was typed

    async def _watch(self) -> None:
        """Requests of the operated tab are paused and looked at, from the first look on; a file chooser the page
        opens is caught, never shown, and its dialogs are answered here."""
        if self._paused is not None or self._closed:
            return
        self._paused = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def heard(params: dict) -> None:
            # On the browser's own thread: handed to the loop, never answered here.
            if not self._closed:
                loop.call_soon_threadsafe(self._paused.put_nowait, params)

        def chooser(params: dict) -> None:
            if not self._closed:
                self._file = params

        def answer(params: dict) -> None:
            self._answering = loop.create_task(self._dialog(params))

        def dialog(params: dict) -> None:
            # Answered at once on the loop: the page, and the step that opened the dialog, wait for it.
            if not self._closed:
                loop.call_soon_threadsafe(answer, params)

        def answered(params: dict) -> None:
            if self._noting:
                self._status[str(params.get("requestId") or "")] = int((params.get("response") or {}).get("status") or 0)
        self._address = await self._url()
        self.sites.add(gate.site_of(self._address))
        self._frame =(await self.tab.cdp("Page.getFrameTree", {}))["frameTree"]["frame"]["id"]
        await self.tab.events("Fetch.requestPaused", heard)
        await self.tab.events("Page.fileChooserOpened", chooser)
        await self.tab.events("Page.javascriptDialogOpening", dialog)
        await self.tab.events("Network.responseReceived", answered)
        await self.tab.cdp("Network.enable", {})
        await self.tab.cdp("Fetch.enable", {"patterns": WATCHED})
        try:
            await self.tab.cdp("Page.enable", {})
            await self.tab.cdp("Page.setInterceptFileChooserDialog", {"enabled": True})
        except Exception:
            logger.info("[Agent] operate: the browser would not catch file choosers; a page may open one",
                        exc_info=True)
        self._drain = asyncio.create_task(self._let_through())

    async def _let_through(self) -> None:
        while not self._closed:
            params = await self._paused.get()
            try:
                request = params.get("request") or {}
                carried = self._carries(params)
                press = self._pressing
                site = gate.site_of(str(request.get("url") or ""))
                get = str(request.get("method") or "GET").upper() in ("GET", "HEAD", "OPTIONS")
                sends = not get and params.get("resourceType") != "Ping"
                top = params.get("resourceType") == "Document" and params.get("frameId") == self._frame
                reads = not top and self._only_reads(request, carried, params.get("resourceType") == "Ping")
                hold: list[dict] = []
                if press is not None and not (carried and site != press["site"]):
                    # A confirmed press: its own site's requests go, a post only once one is seen carrying the
                    # card's words; the others wait for the press to end beside it.
                    if not get and press["said"] and not press["matched"]:
                        if not _bears(_texts(request), press["said"]):
                            press["aside"].append(params)
                            continue
                        press["matched"] = True
                elif reads:
                    pass
                elif self._watching is not None and sends and self._submits(request):
                    hold = [self._watching, *carried]
                elif carried:
                    hold = carried
                elif self._choice is not None and sends:
                    hold = [self._choice]
                elif top and site not in self.sites and time.monotonic() > self._follow_until:
                    # The page going by itself to a site that is not the run's.
                    hold = [{"leave": True, "field": "", "text": ""}]
                if hold and top:
                    # The page cannot be read while its own navigation waits: that card comes at once.
                    self._navs.append((asyncio.create_task(self._held_nav(params, hold)), params))
                    continue
                if hold:
                    self._holding.append((params, hold))
                    continue
                if top:
                    self.sites.add(site)
                    self._address = str(request.get("url") or "")
                    # What was typed and now goes to its own site in the page's address has gone: a request of that
                    # site carrying it later carries nothing new.
                    for t in self.typed:
                        if t["site"] == site and _bears(_texts(request), t["text"]):
                            t["went"] = True
                if self._noting and not get:
                    # What went out while an act call ran, for its Noticed line.
                    parts = urlsplit(str(request.get("url") or ""))
                    self._went.append((f"{str(request.get('method')).upper()} {parts.hostname or ''}{parts.path}"[:120],
                                       str(params.get("networkId") or "")))
                await self.tab.cdp("Fetch.continueRequest", {"requestId": params["requestId"]})
            except Exception:
                logger.debug("[Agent] operate: a paused request could not be let through", exc_info=True)

    def _carries(self, params: dict) -> list[dict]:
        """The typed entries a paused request carries, in its body or address: a GET only when it goes to
        another site than the one the text was typed on (a new tab, a fetch)."""
        request = params.get("request") or {}
        if not self.typed:
            return []
        get = str(request.get("method") or "GET").upper() in ("GET", "HEAD", "OPTIONS")
        if not get and request.get("hasPostData") and not request.get("postData") and not request.get("postDataEntries"):
            # A body the browser would not show: whatever was typed may be in it.
            return list(self.typed)
        site = gate.site_of(str(request.get("url") or ""))
        texts = _texts(request)
        return [t for t in self.typed if not ((get or t.get("went")) and site == t["site"]) and _bears(texts, t["text"])]

    def _submits(self, request: dict) -> bool:
        """A request that leaves on a watched press and is its act: a DELETE, or one to the page's own site or its
        form's (a save, a submission). The page's monitoring and reads to its other hosts on the same click are not
        the press's act."""
        if str(request.get("method") or "").upper() == "DELETE":
            return True
        return gate.site_of(str(request.get("url") or "")) in self._watching["sites"]

    def _only_reads(self, request: dict, carried: list[dict], ping: bool) -> bool:
        """What code is certain only reads, let through with no card and no reviewer: a beacon or ping, or a
        GraphQL query, that carries nothing typed in this run. Every other request a press sends is the reviewer's."""
        return not carried and (ping or _asks_only(request))

    async def _held_cards(self) -> dict:
        """The requests held since the last look, each by its act's one card: {"status", "line", "kind"};
        "declined" when the person said no (that request is dropped), "waiting" when the card waits for an
        answer. The requests one act holds (a press's submission and its retries, what a form saves twice) go as
        its first card was answered (`kept`); a press confirmed meanwhile never lets out what was held before it."""
        lines, ended = [], {"status": "ok", "kind": ""}
        while self._holding:
            # It leaves the list only once answered: if this step breaks off, close() still drops it.
            held = await self._held_card(*self._holding[0])
            self._holding.pop(0)
            lines.append(held["line"])
            if held["status"] != "ok":
                ended = {k: held[k] for k in ("status", "kind", "result") if k in held}
        return {**ended, "line": "\n".join(lines)}

    async def _held_nav(self, params: dict, carried: list[dict]) -> None:
        """The page's own navigation, held: its card at once, its outcome among what the page did, for the next
        line."""
        held = await self._held_card(params, carried, top=True)
        self._told.append(held["line"])
        if held["status"] in ("declined", "waiting"):
            self._turned = {k: held[k] for k in ("status", "kind", "result") if k in held}

    async def _held_card(self, params: dict, carried: list[dict], top: bool = False) -> dict:
        """The card for one held request, and the request let go on yes, dropped on no: {"status", "line",
        "kind"}. A navigation of the page itself said no to is answered 204, so the page stays as it was."""
        request = params.get("request") or {}
        url = str(request.get("url") or "")
        site = gate.site_of(url)
        first = carried[0]
        chose = any(c.get("choice") for c in carried)
        label = f"{request.get('method', 'POST')} {urlsplit(url).path}"[:120]
        try:
            # A page opening a window to another site can stall its own scripts while that is held, and no page
            # can be read while its own navigation waits.
            shown_at = await asyncio.to_thread(lambda: self.tab.url) if top else await asyncio.wait_for(
                self._url(), HEAR_S * 3)
        except (asyncio.TimeoutError, RuntimeError):
            shown_at = ""
        card = {"id": secrets.token_urlsafe(8), "kind": "press" if chose else "submit", "site": site,
                "url": shown_at, "button": SAID[self.lang]["chose" if chose else "held"],
                # The fields typed into, then the request's other short values (a recipient the page put in it),
                # so the card reads back what goes, not only the words typed.
                "to": "、".join(dict.fromkeys([c["field"] for c in carried if c["field"]] + [
                    v for v in _leaves(request)[2] if v and len(v) <= 40
                    and v not in {c["text"].strip() for c in carried}][:4]))[:120],
                "text": "\n".join(dict.fromkeys(c["text"] for c in carried if c["text"]))[:2000],
                "label": label, "skippable": False,
                "why": "the page itself sends what was typed (it saves by itself, for example when a field loses "
                       "the focus)"}
        if first.get("leave"):
            card.update(kind="leave", to="", text=clip(url, 300), button="", label="",
                        why="the page goes to a site that is not one of this run's")
            what = (f"the page went on to {site}, another site", f"the page tried to go on to {site}, another site")
        elif first.get("watch"):
            # A press: the card says what pressing it sends, the form's fields as the person sees them; a press with
            # no fields is named by its button alone.
            card.update(kind="submit" if first["text"] else "press", button=first["button"], to="",
                        text=first["text"] or card["text"], why=f"pressing {first['button']!r} sends this ({label})")
            what = (f"pressing {first['button']!r} sent {label}", f"pressing {first['button']!r} would send {label}")
            if str(request.get("method") or "").upper() == "DELETE":
                # A DELETE deletes, whatever the button says: a delete's card, never the reviewer's.
                card.update(kind="delete", why=f"pressing {first['button']!r} sends a DELETE ({label})")
        else:
            what = (f"the page sent {card['text'][:60]!r} by itself ({label})",
                    f"the page tried to send {card['text'][:60]!r} by itself ({label})")
        # The act this request belongs to: one question for it, and the answer kept for the turn.
        act = ("held", card["kind"], site, card["button"] if first.get("watch") else card["text"])
        card["act"] = act
        decision, passed, does = "ask", "", ""
        kept = self.kept.get(act)
        if kept is not None:
            decision, passed, does = "kept", kept.get("line", ""), kept.get("does", "")
        elif all(c.get("site", site) == site for c in carried):
            # What was typed on one site going to another is never left to the reviewer; going on to another site
            # with nothing typed carried is.
            decision, passed, does = await self._decided(card["kind"], site, _leaving(url) if first.get("leave") else {
                "text": card["text"], "page_says": card["button"], "request": _summary(request),
                "effects": ["the page saves the choice the moment it is made"] if chose else []})
        card["kind"] = _reading(card["kind"], does)
        if decision == "read":
            await self._drop(params, top)
            return {"status": "failed", "kind": card["kind"], "line": f"[read-only] {what[1]}; the person set the "
                    f"browser to read only, so it was dropped"}
        if decision == "turned" or (kept or {}).get("turned"):
            self.kept[act] = {"turned": True, "line": passed, "does": does}
            await self._drop(params, top)
            return {"status": "declined", "kind": card["kind"], "line": f"[declined] {what[1]}; {passed}"}
        answer = kept if kept is not None else {"yes": True} if decision == "go" else None
        if answer is None:
            try:
                card["picture"] = await asyncio.wait_for(self.page.screenshot(), 3)
            except Exception:
                card["picture"] = ""
            answer = await self.ask(card)
            if answer is not None and not answer.get("waiting"):
                self.kept[act] = {**answer, "does": does}
        elif decision == "go":
            self.kept[act] = {"yes": True, "line": passed, "does": does}
        if answer and answer.get("waiting"):
            # Nobody answered in time: the card waits, and so does the act; nothing of it goes meanwhile.
            await self._drop(params, top)
            return {"status": "waiting", "kind": card["kind"], "result": answer["waiting"],
                    "line": f"[waiting] {what[1]}; the card waits for the person's answer, so it was held back"}
        if answer and answer.get("yes"):
            if first.get("leave"):
                self.sites.add(site)
                self._follow_until = time.monotonic() + FOLLOW_S
            else:
                self.confirmed = True
            # A bare submission the person let out took all that was typed for it.
            for t in self.typed if first.get("watch") else carried:
                t["out"] = True
            await self.tab.cdp("Fetch.continueRequest", {"requestId": params["requestId"]})
            how = (passed or "the person's answer to this act before") if decision == "kept" else (
                passed if decision == "go" else "the person said yes on a card")
            return {"status": "ok", "kind": card["kind"], "line": f"[{'went' if first.get('leave') else 'held'}] "
                    f"{what[0]}: {how}, and it went"}
        await self._drop(params, top)
        for t in self.typed if first.get("watch") else carried:
            t["no"] = True
        return {"status": "declined", "kind": card["kind"], "line": f"[declined] {what[1]}; the person "
                f"{'said no' if answer else 'did not answer'}, so it was dropped"}

    async def _decided(self, kind: str, site: str, action: dict) -> tuple[str, str, str]:
        """What the person's mode makes of an outside action before any card: "read" (read only: nothing goes),
        "go" (the reviewer passed it), "turned" (the reviewer turned it down) or "ask" (the card), with the
        reviewer's line and its reading of an outward act (review.DOES, or ""). The reviewer sees only kinds and
        sites acting.decide sends it."""
        from .. import acting
        decision = acting.decide(self.mode(), kind, site)
        if decision == "refuse":
            return "read", "", ""
        if decision != "review" or self.review is None:
            return "ask", "", ""
        key = json.dumps([kind, site, action], ensure_ascii=False, sort_keys=True, default=str)
        if key not in self._verdicts:
            self._verdicts[key] = await self.review({"kind": kind, "where": site, **action})
        verdict = self._verdicts[key]
        line = verdict.line(self.lang)
        if verdict.decision == "go":
            return "go", line, verdict.does
        if verdict.decision == "refuse":
            return "turned", verdict.declined() + ("; turned down three times in a row: stop and hand the task back "
                                                   "to the person" if verdict.stop else ""), verdict.does
        self.tell(line)
        return "ask", line, verdict.does

    async def _drop(self, params: dict, top: bool) -> None:
        """A held request that does not go: a navigation of the page answered 204 (nothing to show, the page stays
        as it was, what was typed in it too), any other failed."""
        if top:
            await self.tab.cdp("Fetch.fulfillRequest", {"requestId": params["requestId"], "responseCode": 204,
                                                       "responseHeaders": [], "body": ""})
        else:
            await self.tab.cdp("Fetch.failRequest", {"requestId": params["requestId"], "errorReason": "BlockedByClient"})

    # --- what the page asks for itself: a file, a download, an answer

    async def _dialog(self, params: dict) -> None:
        """A page's own alert, confirm, prompt or leave-page question, answered at once: an alert closed, its words
        kept for the next line; OK for one that belongs to the press the person confirmed, or that they said yes to
        on its card (`_again`); Cancel for any other, kept for its card when a step opened it (act)."""
        kind = str(params.get("type") or "")
        words = clip(" ".join(str(params.get("message") or "").split()), 200)
        asks = gate.asks(words, str(params.get("url") or "")) if kind != "alert" else ""
        press = self._pressing
        again = self._again == (kind, words)
        ok = kind == "alert" or again or (press is not None and asks in ("dialog", "submit", press["kind"]))
        await self.tab.cdp("Page.handleJavaScriptDialog", {"accept": ok, **(
            {"promptText": str(params.get("defaultPrompt") or "")} if kind == "prompt" and ok else {})})
        said = quote(words) if words else "nothing"
        if self._noting:
            self._dialogs.append(f"{kind} {said}")
        if kind == "alert":
            self._told.append(f"[alert] the page said {said}; it was closed")
        elif again:
            return
        elif ok:
            self._told.append(f"[dialog] the page asked {said}; answered OK, as part of the press the person confirmed")
        elif self._acting and press is None:
            self._asked = {"type": kind, "words": words, "kind": asks}
        else:
            self._told.append(f"[dialog] the page asked {said} " + ("as the press went out, which is not what the "
                              "person confirmed" if press else "by itself") + "; answered Cancel")

    async def _card(self, kind: str, button: str, to: str, text: str, why: str, review: Optional[dict] = None,
                    **more) -> Optional[dict]:
        """A card for something the page asks for itself, with the page as the person sees it: their answer. With
        `review` (the action as the reviewer reads it) the mode is asked first: {"yes": True, "passed": line} when
        the reviewer passed it, {"turned": line} when it turned it down, else the card. An act answered before in
        this turn gets that answer again."""
        url = await self._url()
        card = {"id": secrets.token_urlsafe(8), "kind": kind, "site": gate.site_of(url), "url": url, "button": button,
                "to": to, "text": text, "label": "", "why": why, "skippable": False, **more}
        card["act"] = act = ("card", kind, card["site"], button, text)
        if act in self.kept:
            return self.kept[act]
        if review is not None:
            decision, passed, _ = await self._decided(kind, card["site"], review)
            if decision in ("go", "turned"):
                self.kept[act] = {"yes": True, "passed": passed} if decision == "go" else {"turned": passed}
                return self.kept[act]
        try:
            card["picture"] = await asyncio.wait_for(self.page.screenshot(), 3)
        except Exception:
            card["picture"] = ""
        answer = await self.ask(card)
        if answer is not None and not answer.get("waiting"):
            self.kept[act] = answer
        return answer

    async def _answered(self, action: dict, result: dict) -> dict:
        """The step opened the page's own confirm, prompt or leave-page question, answered Cancel: on a card now,
        and on the person's yes the step is done again with it answered OK."""
        asked, self._asked = self._asked, None
        said = quote(asked["words"]) if asked["words"] else "whether to leave the page"
        text = asked["words"] or (SAID[self.lang]["leave"] if asked["type"] == "beforeunload" else "")
        answer = await self._card(asked["kind"], "", "", text, f"the page's own {asked['type']}",
                                  review={"text": text, "page_says": asked["words"]})
        if answer and answer.get("turned"):
            return {**result, "status": "declined", "kind": asked["kind"],
                    "line": f"{result.get('line', '')}\n[declined] the page asked {said}; it was answered Cancel. "
                            f"{answer['turned']}".strip()}
        if answer and answer.get("waiting"):
            return {**result, "status": "waiting", "kind": asked["kind"], "result": answer["waiting"],
                    "line": f"{result.get('line', '')}\n[waiting] the page asked {said}; it was answered Cancel "
                            f"for now, and the card waits for the person's answer".strip()}
        if not (answer and answer.get("yes")):
            return {**result, "status": "declined", "kind": asked["kind"],
                    "line": f"{result.get('line', '')}\n[declined] the page asked {said}; the person "
                            f"{'said no' if answer else 'did not answer'}, so it was answered Cancel".strip()}
        self._again = (asked["type"], asked["words"])
        try:
            again = await self._act(action)
        finally:
            self._again = None
        self.confirmed = True
        return {**again, "kind": asked["kind"], "outside": True,
                "line": f"[dialog] the page asked {said}; "
                        f"{answer.get('passed') or 'the person said yes on a card'}, so the step was done again "
                        f"and it was answered OK\n{again.get('line', '')}"}

    async def _upload(self, action: dict) -> dict:
        """The step opened the page's file chooser (caught, never shown): a card names the file the person named
        (`file`, which surface.act checks was named this turn) or lets them choose one, and on their yes it goes into
        the page's file field. Nothing is chosen without that yes."""
        chooser, self._file = self._file, None
        if self.mode() == "read":
            return {"status": "failed", "kind": "upload", "line": "[read-only] the page asked for a file; the person "
                                                                  "set the browser to read only, so none was given"}
        ident, many = chooser["backendNodeId"], chooser.get("mode") == "selectMultiple"
        named = str(action.get("file") or "")
        files = [named] if named and Path(named).is_file() else []
        field = clip(self.page.last.get(ident, ("", ""))[1] or str((await self.page.facts(ident)).get("label") or ""), 40)
        # Only a file the person named goes to the reviewer; choosing one is theirs.
        answer = await self._card("upload", field, "、".join(Path(f).name for f in files), "\n".join(files),
                                  "the page asks for a file", many=many,
                                  review={"target": files, "page_says": field} if files else None)
        named = f" for {quote(field)}" if field else ""
        if answer and answer.get("turned"):
            return {"status": "declined", "kind": "upload",
                    "line": f"[declined] the page asked for a file{named}; nothing was chosen. {answer['turned']}"}
        if answer and answer.get("waiting"):
            return {"status": "waiting", "kind": "upload", "result": answer["waiting"],
                    "line": f"[waiting] the page asked for a file{named}; the card waits for the person's answer"}
        files = list(answer.get("files") or files) if answer and answer.get("yes") else []
        if not files:
            return {"status": "declined", "kind": "upload",
                    "line": f"[declined] the page asked for a file{named}; the person "
                            f"{'did not answer' if answer is None else 'gave none'}, so nothing was chosen"}
        files = files if many else files[:1]
        before = await self._seen(None)
        await self.page.cdp("DOM.setFileInputFiles", files=files, backendNodeId=ident)
        await asyncio.sleep(HEAR_S)
        self.confirmed = True
        gave = ", ".join(f"{Path(f).name} ({_size(Path(f).stat().st_size)})" for f in files)
        return {"status": "ok", "kind": "upload", "outside": True,
                "line": f"[ok] gave {gave} to the page's file field{named}: "
                        f"{answer.get('passed') or 'the person chose it on a card'}"
                        + await self._changed(None, before, "[ok]")}

    async def _saved(self) -> str:
        """A card for each file the page began to save since the last look (the pane holds it meanwhile): saved
        to the download folder on the person's yes, dropped on no, and the run goes on either way."""
        lines = []
        for want in self.downloads():
            name = Path(want["path"]).name
            size = f" ({_size(want['bytes'])})" if want["bytes"] else ""
            answer = await self._card("download", "", name + size, want["path"], "the page saves a file to this computer")
            yes = bool(answer and answer.get("yes"))
            await asyncio.to_thread(want["answer"], yes)
            self.confirmed = self.confirmed or yes
            lines.append(f"[download] {name}{size}: the person said yes on a card; it is saved as {want['path']}"
                         if yes else f"[declined] the page wanted to save {name}{size}; the person "
                         f"{'said no' if answer else 'did not answer'}, so nothing was saved")
        return "\n".join(lines)

    async def _page_said(self) -> str:
        """What the page said and saved by itself since the last step: its alerts and other dialogs, how its own
        navigations' cards were answered, and a card for each download."""
        navs, self._navs = self._navs, []
        for task, _ in navs:
            await task
        told, self._told = self._told, []
        return "\n".join(x for x in [*told, await self._saved()] if x)

    async def let_go(self) -> None:
        """The person signs in: nothing of the run holds the page's requests until the next look. Those paused now
        go on, except one carrying what was typed, which is dropped (as at close)."""
        if self._paused is None or self._closed or self._released is not None:
            return
        self._released = self.tab._fetch
        while not self._paused.empty():
            params = self._paused.get_nowait()
            self._holding.append((params, self._carries(params)))
        for params, carried in self._holding:
            try:
                await self.tab.cdp("Fetch.failRequest" if carried else "Fetch.continueRequest",
                                   {"requestId": params["requestId"], **({"errorReason": "BlockedByClient"} if carried else {})})
            except Exception:
                # A request the page dropped meanwhile.
                pass
        self._holding = []
        await self.tab.cdp("Fetch.disable", {})

    async def close(self, restore: bool = False) -> None:
        """The run is over. With `restore` (the person said no, or stopped it) each field typed into and never
        let out gets back what it held, while its requests are still held: a timer or a beacon of the page
        cannot send it later; so does a field whose sending the person refused on a card, however the run
        ended. Then what is still held is dropped, and the page's requests are no longer looked at."""
        # A navigation of the page still waiting for its card stays where it was (no page is read while it waits).
        for task, params in self._navs:
            if not task.done():
                task.cancel()
                try:
                    await self._drop(params, True)
                except Exception:
                    pass
        self._navs = []
        back = [t for t in self.typed if not t["out"] and (restore or t.get("no"))]
        if back and self._paused is not None:
            for t in back:
                try:
                    await self.page.on_node(t["node"], RESTORE_JS, [t.get("shows", t["text"]), t["before"]])
                except Exception:
                    logger.debug("[Agent] operate: a typed field was gone before it could be put back")
            await asyncio.sleep(HEAR_S)
        self._closed = True
        if self._drain is not None:
            self._drain.cancel()
        # Paused and not yet looked at: what carries typed text is dropped too.
        while self._paused is not None and not self._paused.empty():
            params = self._paused.get_nowait()
            self._holding.append((params, self._carries(params)))
        for params, carried in self._holding:
            try:
                await self.tab.cdp("Fetch.failRequest" if carried else "Fetch.continueRequest",
                                   {"requestId": params["requestId"], **({"errorReason": "BlockedByClient"} if carried else {})})
            except Exception:
                pass
        self._holding = []
        if self._paused is not None:
            try:
                await self.tab.cdp("Page.setInterceptFileChooserDialog", {"enabled": False})
            except Exception:
                pass
            try:
                await self.tab.cdp("Fetch.disable", {})
            except Exception:
                pass

    # --- looking

    async def look(self, most: int = STATE_MAX) -> dict:
        """The page as the model reads it, at most `most` characters, and what the page did by itself since."""
        why = self.held()
        if why:
            return {"status": why}
        if self._released is not None:
            # The person is past the sign-in: the page's requests are looked at again.
            await self.tab.cdp("Fetch.enable", self._released)
            self._released = None
        await self._watch()
        state = await self.page.state(min(max(int(most), STATE_MAX), STATE_MOST))
        self.read.append(state)
        url = await self.page._evaluate("() => location.href")
        try:
            wall = await self.page._evaluate(WALL_JS) or {}
            # A sign-in box over a page a visitor may read is a prompt to close, not a wall (web/signin.py).
            layer = await self.page._evaluate(signin.CHECK_JS) or {}
            seen = getattr(self, "_prompt_seen", ("", None))
            wall = signin.judge(wall, layer, seen[1] if seen[0] == url else None,
                                getattr(self, "_last_do", "") in ("click", "press"))
            self._prompt_seen = (url, wall["prompt"])
            if wall["prompt"]:
                state += signin.prompt_line(wall["prompt"], signin.key(url))
        except Exception:
            wall = {}
        found = {"status": "ok", "state": state, "url": url, "wall": wall.get("kind", ""),
                 "evidence": wall.get("evidence", ""), "controls": self.page.interactive,
                 "drawn": bool(wall.get("drawn"))}
        found.update(await self.since())
        return found

    async def since(self) -> dict:
        """What the page did by itself since the last step: its dialogs, its own navigations' cards, its downloads'
        cards and the held requests' cards, as {"held": lines} (and how a card that did not let one go ended);
        {} when it did nothing."""
        found: dict = {}
        lines = [await self._page_said()]
        if self._turned:
            found.update(self._turned)
            self._turned = {}
        if self._holding:
            held = await self._held_cards()
            lines.append(held.pop("line"))
            if held["status"] != "ok":
                found.update(held)
        if any(lines):
            found["held"] = "\n".join(x for x in lines if x)
        return found

    async def _where(self) -> dict:
        try:
            return await self.page._evaluate("() => ({url: location.href, title: document.title})") or {}
        except Exception:
            # Between two pages.
            return {}

    async def steps(self, actions: list[dict]) -> dict:
        """One act call: each action in turn until one ends it (stopped, paused, declined, waiting, refused; one
        that did not work, or that took the page elsewhere with steps left, whose refs would be another page's),
        then what changed and what the app noticed meanwhile. {"status", "lines", "noticed", "url", "title",
        "texts"}: `texts` are the page's words each read step took ({"text", "goes_on"}), for the reader the
        service runs (read_page); they join what `_carried` checks."""
        why = self.held() or ("stopped" if self.mode() == "off" else "")
        if why:
            return {"status": why, "lines": [f"[{why}]"], "noticed": "", "url": "", "title": "", "texts": []}
        if self._released is not None:
            await self.tab.cdp("Fetch.enable", self._released)
            self._released = None
        await self._watch()
        before = await self._where()
        self._noting, self._went, self._dialogs, self._status = True, [], [], {}
        lines: list[str] = []
        texts: list[dict] = []
        status = "ok"
        try:
            for n, action in enumerate(actions):
                result = await self.act(action)
                line = str(result.get("line") or "")
                if action.get("do") == "read" and result.get("status") == "ok":
                    texts.append({"text": str(result.get("text") or ""), "goes_on": bool(result.get("goes_on"))})
                    self.read.append(texts[-1]["text"])
                lines.append(line)
                status = str(result.get("status") or "failed")
                left = len(actions) - n - 1
                if status in ("stopped", "paused", "declined", "waiting", "refused"):
                    break
                if left and status != "ok":
                    lines.append(f"[not done] the {left} step(s) after it: it did not work. Look again first")
                    break
                if left and "navigated to" in line:
                    lines.append(f"[not done] the {left} step(s) after it: the page changed, so its refs are gone. "
                                 f"Look again first")
                    break
            lines += [held for held in [(await self.since()).get("held", "")] if held]
        finally:
            self._noting = False
        after = await self._where()
        noticed = []
        if after.get("url") and after.get("url") != before.get("url"):
            noticed.append(f"the address is now {clip(after['url'], 200)}")
        if after.get("title") and after.get("title") != before.get("title"):
            noticed.append(f"the title is now {quote(clip(after['title'], 80))}")
        went = list(dict.fromkeys(f"{line} {self._status.get(ident) or 'no answer yet'}" for line, ident in self._went))
        if went:
            noticed.append("went out: " + "; ".join(went[:WENT_SHOWN]) + (f" and {len(went) - WENT_SHOWN} more"
                                                                           if len(went) > WENT_SHOWN else ""))
        if self._dialogs:
            noticed.append("the page's own dialogs: " + "; ".join(self._dialogs[:4]))
        unsent = [t["field"] or "a field" for t in self.typed if not t["out"]]
        if unsent:
            noticed.append(f"typed into {', '.join(dict.fromkeys(unsent))} and not sent or saved yet")
        self.read += lines
        return {"status": status, "lines": lines, "noticed": "; ".join(noticed), "url": after.get("url", ""),
                "title": after.get("title", ""), "texts": texts}

    async def picture(self) -> dict:
        return await picture_of(self.page)

    # --- acting

    def _said(self, action: dict, name: str = "") -> str:
        """The step as a person names it: 点「发送」, 在搜索框输入「猫」."""
        say, do = SAID[self.lang], action.get("do")
        name = clip(" ".join(str(name or action.get("element") or action.get("text") or "").split()), 30)
        if do == "click":
            how = "double" if action.get("double") is True else "right" if action.get("button") == "right" else "click"
            return say[how].format(name=name or say["point"])
        if do == "drag":
            return say["drag"].format(name=name, to=clip(" ".join(str(action.get("target") or action.get("to_text")
                                                                       or "").split()), 30))
        if do == "type":
            return say["type"].format(name=name or say["box"], text=clip(str(action.get("text") or ""), 40))
        if do == "press":
            return say["press"].format(key=action.get("key") or "")
        if do == "hover":
            return say["hover"].format(name=name)
        if do == "scroll":
            return (say["up" if action.get("direction") == "up" else "down"]
                    + (say["in"].format(name=name) if action.get("id") is not None and name else "")
                    + (say["until"].format(text=clip(str(action["until"]), 30)) if action.get("until") else ""))
        if do == "navigate":
            return say["navigate"].format(host=urlsplit(str(action.get("url") or "")).hostname or "")
        if do == "wait" and (action.get("until") or action.get("gone")):
            return say["shows" if action.get("until") else "gone"].format(
                text=clip(str(action.get("until") or action.get("gone")), 30))
        if do in ("back", "wait"):
            return say[do]
        if do == "find":
            return say["find"].format(text=clip(str(action.get("text") or ""), 30))
        if do == "read":
            return say["more" if action.get("more") else "read"].format(text=clip(str(action.get("question") or ""), 30))
        return ""

    async def act(self, action: dict) -> dict:
        """One action: {"status", "line", "said"}."""
        why = self.held() or ("stopped" if self.mode() == "off" else "")
        if why:
            return {"status": why, "line": f"[{why}]"}
        self._last_do = action.get("do")
        self._what = clip(" ".join(str(action.get("what") or "").split()), 80)
        self._acting = True
        try:
            result = await self._act(action)
        finally:
            self._acting = False
        if result.get("status") == "failed":
            # Why, for the person watching the steps.
            result["why"] = _why(str(result.get("line") or ""), self.lang)
        if self.typed and action.get("do") in ("type", "press", "click"):
            # A field left (a click elsewhere, Tab) may be saving what was typed: its request is heard first.
            await asyncio.sleep(HEAR_S)
        if self._asked:
            result = await self._answered(action, result)
        if self._file:
            upload = await self._upload(action)
            result = {**result, **upload, "line": f"{result.get('line', '')}\n{upload['line']}".strip()}
        said = await self._page_said()
        if said:
            result = {**result, "line": f"{result.get('line', '')}\n{said}".strip()}
            if "[download]" in said or "[held]" in said:
                result["outside"] = True
        if self._turned:
            result.update(self._turned)
            self._turned = {}
        if self._holding and result.get("status") in ("ok", "failed"):
            held = await self._held_cards()
            line = held.pop("line")
            result = {**result, "line": f"{result.get('line', '')}\n{line}".strip()}
            if "[held]" in line:
                result["outside"] = True
            if held["status"] != "ok":
                result.update(held)
        self._what = ""
        return result

    async def _act(self, action: dict) -> dict:
        do = action.get("do")
        if "id" in action and action["id"] in (None, ""):
            # {"id": null, "text": "保存"}: the words are the target.
            action = {k: v for k, v in action.items() if k != "id"}
        if do in ("click", "type", "hover") and "id" not in action:
            if do in ("click", "hover") and str(action.get("text") or "").strip():
                # Something shown with no id, by the words it shows.
                found = await self.page.locate(str(action["text"])) or await self.page.seek(str(action["text"]))
                if found is None:
                    return {"status": "missing", "line": f"[missing] nothing on the page shows "
                                                         f"{clip(str(action['text']), 60)!r}; look again"}
                action = {**action, "id": found, "element": action.get("element") or action["text"]}
            elif not (do == "click" and isinstance(action.get("x"), (int, float))
                      and isinstance(action.get("y"), (int, float))):
                return {"status": "failed", "line": f"[failed] {do} needs a ref from the latest look"}
        try:
            if do == "click" and "id" not in action:
                return await self._click_point(float(action["x"]), float(action["y"]), str(action.get("element") or ""))
            if do == "click":
                times = int(action["times"]) if str(action.get("times", "")).isdigit() else 1
                how = "double" if action.get("double") is True else "right" if action.get("button") == "right" else ""
                return await self._click(int(action["id"]), str(action.get("element") or ""),
                                         max(1, min(times, TIMES_MAX)), how)
            if do == "type":
                return await self._type(int(action["id"]), str(action.get("text") or ""),
                                        action.get("enter") is True, str(action.get("element") or ""),
                                        action.get("append") is True)
            if do == "drag":
                return await self._drag(action)
            if do == "hover":
                ident = await self._fresh(int(action["id"]))
                if ident is None:
                    return {"status": "failed", "line": f"[failed] [{action['id']}] is not on the page now; look again"}
                said = self._said(action, self.page.last.get(ident, ("", ""))[1])
                self.tell(said)
                before = await self._seen(ident)
                line = await self.page.hover(ident)
                # A menu opened by a style alone changes nothing the page counts: what came up says it opened.
                return {"status": "ok" if line.startswith("[ok]") else "failed", "said": said,
                        "line": line + await self._changed(ident, before, line, CAME_UP_HOVER)}
            if do == "press":
                try:
                    keys = chord(str(action.get("key") or ""))[0]
                except ValueError as error:
                    return {"status": "failed", "line": f"[failed] {error}"}
                return await self._press(keys)
            if do == "scroll":
                ident = action.get("id")
                ident = await self._fresh(int(ident)) if ident is not None else None
                said = self._said(action, self.page.last.get(ident, ("", ""))[1] if ident else "")
                self.tell(said)
                line = await self.page.scroll(action.get("direction") != "up", ident, str(action.get("until") or ""))
                return {"status": "ok", "line": line, "said": said}
            if do == "find":
                words = str(action.get("text") or "")
                found = self.page.grep(words)
                return {"status": "ok", "said": self._said(action), "found": found,
                        "line": f"[ok] find {clip(words, 40)!r}: " + (f"{found.count(chr(10)) + 1} lines" if found
                                                                      else "not on this page")}
            if do == "read":
                # The page's words for the loop's reader (read_page): what the document holds now, the page not
                # scrolled (a list that draws only the rows in view read through); with "more", one step on
                # ("step") or the rest of a list ("all").
                said = self._said(action)
                self.tell(said)
                how = action.get("more")
                if how == "all":
                    text, goes_on = await self.page.whole(), False
                elif how == "step":
                    text, goes_on = await self.page.step(self._read_at)
                else:
                    self._read_at = {}
                    text, goes_on = await self.page.document()
                return {"status": "ok", "said": said, "text": text, "goes_on": goes_on,
                        "line": f"[ok] read the page: {len(text)} characters" + (" after loading more" if how else "")}
            if do == "wait":
                self.tell(self._said(action))
                return {"status": "ok", "said": self._said(action), "line": await self.page.wait(
                    str(action.get("until") or ""), str(action.get("gone") or ""))}
            if do == "navigate":
                url = str(action.get("url") or "")
                if urlsplit(url).scheme not in ("http", "https"):
                    return {"status": "failed", "line": "[failed] only http and https addresses"}
                site = gate.site_of(url)
                if not signin.chose(self.allowed.path.parent, url, "task"):
                    # Settings › 连接: the person turned off the character opening this site by itself.
                    return {"status": "failed", "line": f"[refused] the person turned off opening {site} on your own "
                                                        f"(Settings › 连接); nothing was opened. Ask them"}
                carried = _carried(url, "\n".join(self.read), self.said()) if site not in self.sites else ""
                if carried:
                    # A page's words in an address to another site: what a page said would leave by the address.
                    return {"status": "refused", "line": f"[refused] the address to {site} carries words read on a "
                                                         f"page ({carried!r}) that the person did not say; "
                                                         f"nothing was opened"}
                self.sites.add(site)
                self._follow_until = time.monotonic() + FOLLOW_S
                self.tell(self._said(action))
                return {"status": "ok", "line": await self.page.goto(url), "said": self._said(action)}
            if do == "back":
                self.tell(self._said(action))
                return {"status": "ok", "line": await self.page.back(), "said": self._said(action)}
        except Refused as refused:
            return {"status": "refused", "line": str(refused)}
        except (KeyError, ValueError, TypeError):
            return {"status": "failed", "line": f"[failed] {do} needs an id from the latest page"}
        except RuntimeError as error:
            return {"status": "failed", "line": f"[failed] {do}: {clip(str(error), 160)}; look again"}
        return {"status": "failed", "line": f"[failed] no action {str(do)[:20]!r}"}

    async def _url(self) -> str:
        return str(await self.page._evaluate("() => location.href") or "")

    async def _fresh(self, ident: int) -> Optional[int]:
        """The id itself while it is on the page; a control drawn again since the look, found again (in one
        step in the page, else by a fresh look); or None."""
        if ident in self.page.last and await self.page.alive(ident):
            return ident
        return await self.page.again(ident) or await self.page.relocate(ident)

    async def _seen(self, ident: Optional[int]) -> dict:
        """What a control says of itself and the words the page shows, read before an action and after it."""
        try:
            if ident is not None:
                return await self.page.on_node(ident, SEEN_JS)
        except Exception:
            pass
        try:
            return {"state": {}, "lines": await self.page._evaluate(LINES_JS) or []}
        except Exception:
            return {"state": {}, "lines": []}

    async def _changed(self, ident: Optional[int], before: dict, line: str, most: int = CAME_UP) -> str:
        """What an action that went through changed, to end its line: the control's own state (now on, no longer
        checked), then the words that came up on the page, `most` of them. "" for an action that failed or left
        the page: the next look shows a new page whole."""
        if not line.startswith("[ok]") or "navigated to" in line:
            return ""
        after = await self._seen(ident)
        said = [f"{'now' if now else 'no longer'} {key}" for key, now in after["state"].items()
                if now is not None and now != before["state"].get(key) and (now or before["state"].get(key))]
        left, new = Counter(before["lines"]), []
        for text in after["lines"]:
            if left[text] > 0:
                left[text] -= 1
            elif text not in new:
                new.append(text)
        if new:
            said.append("came up on the page: " + " | ".join(quote(clip(text, 60)) for text in new[:most])
                        + (f" and {len(new) - most} more" if len(new) > most else ""))
        return "".join(f"; {s}" for s in said)

    def _other(self, ident: int, element: str, role: str, name: str, facts: dict) -> str:
        """The refusal for an id whose control is not the one the model names: `element` shares no word with this
        control (its name, tooltip, label, class, kind, the text before it; for one with no words of its own, the
        item it sits in), while those words do stand elsewhere on the page. "" when it may go on: a control
        named loosely, by its kind or in another language, is none of the page's other words."""
        nameless = bool(gate.NO_WORDS.match(name))
        # Its own words share by a single CJK character too (收藏 for 藏品); what stands round it, by whole words.
        own = " ".join(str(x or "") for x in (name, facts.get("text"), facts.get("label"), facts.get("tip"), role,
                                              KINDS.get(role, "")))
        round_it = " ".join(str(x or "") for x in (facts.get("hint"), facts.get("classes"), self.page.near.get(ident, ""),
                                                   facts.get("row") if nameless else ""))
        wanted, wanted_chars = _words(element)
        mine, mine_chars = _words(own)
        mine |= _words(round_it)[0]
        if not wanted or wanted & mine or wanted_chars & mine_chars or any(
                len(w) > 2 and (w in m or m in w) for w in wanted for m in mine if len(m) > 2):
            return ""
        here = self.page.describe(ident)
        elsewhere = [x for x in self.page.lines if not x.lstrip("*").startswith(f"[{ident}] ") and wanted & _words(x)[0]]
        if not elsewhere:
            return ""
        # The line that holds most of the words named, when it is a control's.
        best = max(elsewhere, key=lambda x: len(wanted & _words(x)[0]))
        there = re.match(r"\*?(\[\d+\] \S+(?: \"[^\"]*\")?)", best)
        return (f"[failed] {here} is not {quote(clip(element, 60))}: nothing was done. "
                + (f"{there.group(1)} holds those words" if there else
                   f"Those words are shown with no id ({quote(clip(best, 60))}): click or hover them by their text")
                + "; look again")

    async def _click(self, ident: int, element: str, times: int = 1, how: str = "") -> dict:
        """A click; `how` "double" or "right". A double click on a control with an effect outside would press it
        twice and is refused; a right click presses nothing but opens the page's menu, so what it sends is watched
        as a bare submission's press is."""
        fresh = await self._fresh(ident)
        if fresh is None:
            return {"status": "failed", "line": f"[failed] [{ident}] is not on the latest page; look again"}
        ident = fresh
        role, name = self.page.last.get(ident, ("", ""))
        facts = await self.page.facts(ident)
        other = self._other(ident, element, role, name, facts)
        if other:
            return {"status": "failed", "line": other}
        url = await self._url()
        kind, why = gate.classify("click", "" if role == "text" and facts.get("text") else name, role, facts, url)
        if how == "right":
            kind, why, times = "watch", "a right click opens the page's own menu", 1
        elif how == "double" and kind and kind != "watch":
            return {"status": "failed", "line": f"[failed] double click {self.page.describe(ident)}: it is {kind}, and a "
                                                f"double click would press it twice; nothing was done. Click it once"}
        elif how == "double":
            times = 1
        # An icon is named on the card by its tooltip, as the person sees it on hover.
        shown = name or facts.get("text") or facts.get("label") or ""
        if gate.NO_WORDS.match(shown) and facts.get("tip"):
            shown = f"{shown} {facts['tip']}".strip()
        said = self._said({"do": "click", "double": how == "double", "button": how}, shown or element)
        if facts.get("tag") in ("select", "label") and not how:
            options = await self.page.on_node(ident, OPTIONS_JS)
            if options is not None:
                return {"status": "ok", "said": said, "line": f"[ok] {self.page.describe(ident)} is a list to choose "
                        f"from, not to click: type the option's words into its id; its options: {' | '.join(options)}"}
        # On the line, a control with no name is named as the card names it, and one of several with the same
        # name by the item it sits in.
        twins = sum(1 for r, n in self.page.last.values() if r == role and fold(n) == fold(name)) > 1
        more = (f" {{{clip(shown, 40)}}}" if not name and shown else "") + (
            f" (in {quote(clip(str(facts['row']), 50))})" if twins and facts.get("row") else "")
        # The control pressed and what it and the page showed just before, to say after what the press changed.
        seen: dict = {}

        async def press(again: bool = False) -> str:
            target = await self._fresh(ident) or ident
            if not again:
                self.tell(said)
                seen.update(await self._seen(target), target=target)
            if not how and (target != ident or not await self.page.alive(target)):
                # The page keeps drawing this control anew: found and pressed in one step, never by a point
                # another row may have moved under.
                line = await self.page.click_again(target)
            else:
                line = await self.page.click(target, how)
            here = self.page.describe(target)
            return line.replace(here, here + more, 1)
        if kind and kind != "watch":
            if await self.page.alive(ident):
                # A press that cannot land (covered, hidden, turned off) is not put to the person on a card.
                try:
                    await self.page._point(ident)
                except RuntimeError as error:
                    self.tell(said)
                    return {"status": "failed", "said": said,
                            "line": f"[failed] click {self.page.describe(ident)}: {clip(str(error), 160)}; look again"}
            result = await self._outside(kind, why, ident, shown, facts, url, element, press)
            if result.get("outside"):
                result["line"] += await self._changed(seen["target"], seen, result["line"])
            return {**result, "said": said}
        if (facts.get("link") or facts.get("away")) and how != "right":
            # A link the person could see: where it leads (and its redirects) is one of the run's sites.
            self._follow_until = time.monotonic() + FOLLOW_S
        away = await self.page.on_node(ident, BLANK_JS) if facts.get("link") and not how else None
        if away:
            self.tell(said)
            line = f"{await self.page.goto(away)} (the link opens a new tab; opened here instead)"
        elif how == "right":
            line = await self._watched(press, shown or element, facts)
        elif role in CHOICES or facts.get("type") in ("checkbox", "radio"):
            line = await self._choosing(press, shown or element, url)
        elif kind == "watch":
            line = await self._watched(press, shown or element, facts)
        else:
            if times > 1:
                said += SAID[self.lang]["times"].format(n=times)

            async def presses() -> str:
                line = await press()
                # The same control pressed again (`times`, a stepper or a calendar's arrow), found again where the
                # page drew it anew; none once a press failed or the page went elsewhere.
                pressed = 1
                while pressed < times and line.startswith("[ok]") and "navigated to" not in line:
                    again = await press(again=True)
                    if not again.startswith("[ok]"):
                        line += f"; press {pressed + 1} did not work: {clip(again, 120)}"
                        break
                    pressed += 1
                return line + (f"; pressed {pressed} times" if times > 1 else "")
            # A link or a tab leads somewhere; any other control's press is watched for what it saves to the page's
            # own site and a DELETE, never for the page's traffic to its other hosts.
            line = await (presses() if facts.get("link") or facts.get("away") or role in ("link", "tab")
                          else self._watched(presses, shown or element, facts, HEAR_S))
        still = False
        if seen:
            line += await self._changed(seen["target"], seen, line)
            still = line.startswith("[ok]") and line.endswith("-> no change")
        if still:
            # The press landed and the page did not move: said so in a way that ends the pressing.
            chosen = any(seen.get("state", {}).get(k) for k in ("selected", "pressed", "checked", "on"))
            line += ("; it was already chosen before the press, so what it shows is on the page now: read it there "
                     "(or read the whole page), never press it again" if chosen else
                     "; the press landed and nothing on the page changed: pressing it again will not help")
        return {"status": "ok" if line.startswith("[ok]") else "failed", "line": line, "said": said, "still": still}

    async def _choosing(self, act: Callable[[], Awaitable[str]], field: str, url: str, value: str = "") -> str:
        """A box ticked, a radio or an option chosen: a post it sets off (a settings page that saves as it
        changes) is held as an account change, for the card."""
        self._choice = {"text": f"{field}：{value}" if value else field, "field": field, "site": gate.site_of(url),
                        "out": False, "choice": True}
        try:
            line = await act()
            await asyncio.sleep(HEAR_S)
        finally:
            self._choice = None
        return line

    def _fields(self, facts: dict) -> str:
        """The filled fields a press sends, one a line, as the card lists them: 就诊日期：2026-12-05."""
        colon = "：" if self.lang == "zh" else ": "
        return "\n".join(f"{c}{colon}{v}" if c else v for c, v in facts.get("fields") or [])

    async def _watched(self, act: Callable[[], Awaitable[str]], button: str, facts: dict,
                       tail: float = PRESS_TAIL_S) -> str:
        """A press code could not sort, or a bare submission (gate's "watch": a form sent by GET, a shortcut, a
        drag): what leaves on it within `tail` and is its act (`_submits`: a DELETE, or a request to the page's own
        site that is not a GET) is held for its card, which act shows next with the fields it sends; nothing of the
        kind leaves, no card. Once the page's own question about it had a
        yes on its card, the press is the person's."""
        if self._again is not None:
            return await act()
        sites = {gate.site_of(await self._url()), *([gate.site_of(facts["form"]["action"])]
                                                    if (facts.get("form") or {}).get("action") else [])}
        self._watching = {"watch": True, "button": clip(" ".join(button.split()), 40), "field": "", "out": False,
                          "text": self._fields(facts), "sites": sites}
        try:
            line = await act()
            await asyncio.sleep(tail)
        finally:
            self._watching = None
        return line

    async def _click_point(self, x: float, y: float, element: str) -> dict:
        """A click on a point of a drawn page (from a picture), through the gate like any other."""
        node = await self.page.cdp("DOM.getNodeForLocation", x=int(x), y=int(y), includeUserAgentShadowDOM=False)
        ident = node.get("backendNodeId")
        facts = await self.page.facts(ident) if ident else {}
        url = await self._url()
        kind, why = gate.classify("click", "", str(facts.get("role") or facts.get("tag") or ""), facts, url)
        said = self._said({"do": "click"}, element if ident is None or not facts.get("text") else facts["text"])

        async def press() -> str:
            self.tell(said)
            return await self.page.click_point(x, y)
        if kind and kind != "watch" and ident:
            return {**await self._outside(kind, why, ident, facts.get("text") or facts.get("label") or "", facts, url,
                                          "", press), "said": said}
        line = await self._watched(press, str(facts.get("text") or element), facts,
                                   PRESS_TAIL_S if kind == "watch" else HEAR_S)
        return {"status": "ok" if line.startswith("[ok]") else "failed", "line": line, "said": said}

    async def _drag(self, action: dict) -> dict:
        """One control dragged onto another, each by its id and name or by the words it shows (`text`, `to_text`):
        pressed, moved and let go (pageops.drag), watched as a bare submission's press is, so a page that saves the
        move asks on a card first."""
        ends = []
        for ident, words, element in ((action.get("id"), action.get("text"), action.get("element")),
                                      (action.get("to"), action.get("to_text"), action.get("target"))):
            if isinstance(ident, str) and not ident.strip().isdigit():
                ident, words = None, words or ident
            if ident in (None, ""):
                found = await self.page.locate(str(words)) if str(words or "").strip() else None
                if found is None:
                    return {"status": "missing", "line": f"[missing] nothing on the page shows "
                                                         f"{clip(str(words or ''), 60)!r}; look again"}
                ends.append((found, str(element or words)))
                continue
            fresh = await self._fresh(int(ident))
            if fresh is None:
                return {"status": "failed", "line": f"[failed] [{ident}] is not on the latest page; look again"}
            role, name = self.page.last.get(fresh, ("", ""))
            other = self._other(fresh, str(element or ""), role, name, await self.page.facts(fresh))
            if other:
                return {"status": "failed", "line": other}
            ends.append((fresh, name or str(element or "")))
        (start, start_name), (end, end_name) = ends
        said = self._said({"do": "drag", "target": end_name}, start_name)
        self.tell(said)
        before = await self._seen(None)
        line = await self._watched(lambda: self.page.drag(start, end), said, await self.page.facts(start))
        return {"status": "ok" if line.startswith("[ok]") else "failed", "said": said,
                "line": line + await self._changed(None, before, line)}

    async def _type(self, ident: int, text: str, enter: bool, element: str, append: bool = False) -> dict:
        fresh = await self._fresh(ident)
        if fresh is None:
            return {"status": "failed", "line": f"[failed] [{ident}] is not on the latest page; look again"}
        ident = fresh
        role, name = self.page.last.get(ident, ("", ""))
        url = await self._url()
        said = self._said({"do": "type", "text": text}, name or element)
        if await self.page.secret(ident):
            # Theirs to type: the field gets the focus, and the page goes to them as for a form that wants it.
            try:
                await self.page.focus(ident)
            except Exception:
                pass
            return {"status": "refused", "line": f"[handover] {self.page.describe(ident)} is a secret field, never "
                                                 f"typed for them; it has the focus now, and they say 继续 once it is in"}
        facts = await self.page.facts(ident)
        other = self._other(ident, element, role, name, facts)
        if other:
            return {"status": "failed", "line": other}
        self.tell(said)
        if text.strip() and not facts.get("search"):
            # What a request may not carry out of the page without the person's press; what the field held, to
            # put back if the run ends with it never let out.
            earlier = next((t for t in self.typed if t["node"] == ident and not t["out"]), None)
            before = earlier["before"] if earlier else await self.page.value(ident)
            if not self.typed:
                # From now on a picture or a script from another site could carry it out in its address.
                await self.tab.cdp("Fetch.enable", {"patterns": TYPED_WATCHED})
            self.typed.append({"text": text.strip(), "field": clip(name or str(facts.get("label") or ""), 40),
                               "site": gate.site_of(self.page.frame_url(ident) or url), "node": ident,
                               "before": before, "out": False})
        if facts.get("tag") == "select":
            line = await self._choosing(lambda: self.page.type(ident, text), name or str(facts.get("label") or ""), url,
                                        text)
        else:
            line = await self.page.type(ident, text, append=append)
            if append and self.typed and self.typed[-1]["node"] == ident:
                # What the field shows now, so the run's end can tell it is still as typed and put it back.
                self.typed[-1]["shows"] = (await self.page.value(ident)).strip()
        result = {"status": "ok" if line.startswith("[ok]") else "failed", "line": line, "said": said}
        if not enter or result["status"] != "ok":
            return result
        facts = await self.page.facts(ident)
        kind, why = gate.classify("enter", name, role, facts, url)
        if kind and kind != "watch":
            outside = await self._outside(kind, why, ident, f"Enter in {name or 'the field'}", facts, url, element,
                                          lambda: self.page.press("Enter"))
            outside["line"] = f"{line}\n{outside['line']}"
            return {**outside, "said": said}
        entered = await self._watched(lambda: self.page.press("Enter"), str((facts.get("form") or {}).get("button") or "")
                                      or f"Enter in {name or 'the field'}", facts, PRESS_TAIL_S if kind == "watch" else HEAR_S)
        return {**result, "line": f"{line}\n{entered}"}

    async def _press(self, key: str) -> dict:
        """A key or chord, as pageops.chord spells it, through the gate's chord rule: one ending in Enter or Space is
        that key on the control with the focus, a shortcut is watched as a bare submission's press is, and the
        clipboard is refused."""
        said = self._said({"do": "press", "key": key})
        ident = await self.page.active()
        facts = await self.page.facts(ident) if ident is not None else {}
        mode, why = gate.chord(key, bool(facts.get("textEntry")))
        if mode == "refuse":
            return {"status": "failed", "line": f"[failed] press {key}: the clipboard is the person's own and is never "
                                                f"used; nothing was pressed. Type the words instead"}
        # A character keyed into a secret field is typing it: theirs, as for `type`.
        if (ident is not None and len(key.rsplit("+", 1)[-1]) == 1 and not re.match(r"(Control|Alt|Meta)\+", key)
                and await self.page.secret(ident)):
            return {"status": "refused", "line": f"[handover] {self.page.describe(ident)} is a secret field, never "
                                                 f"typed for them; they say 继续 once it is in"}
        kind = "watch" if mode == "watch" else ""
        if mode in ("enter", "click") and ident is not None:
            url = await self._url()
            role, name = self.page.last.get(ident, (facts.get("role") or facts.get("tag") or "", ""))
            kind, why = gate.classify(mode, name, role, facts, url)
            if kind and kind != "watch":
                async def press() -> str:
                    self.tell(said)
                    return await self.page.press(key)
                return {**await self._outside(kind, why, ident, f"{key} in {name or role}", facts, url, "", press),
                        "said": said}
        if ident is not None:
            self.tell(said)
        else:
            self.tell(said)
        before = await self._seen(None)
        line = await (self._watched(lambda: self.page.press(key), str((facts.get("form") or {}).get("button")
                                                                      or facts.get("text") or facts.get("label") or key),
                                    facts, PRESS_TAIL_S if kind == "watch" else HEAR_S)
                      if kind == "watch" or mode in ("enter", "click") else self.page.press(key))
        return {"status": "ok", "line": line + await self._changed(None, before, line), "said": said}

    async def _outside(self, kind: str, why: str, ident: int, button: str, facts: dict, url: str, element: str,
                       press: Callable[[], Awaitable[str]]) -> dict:
        """An action with an effect outside: the person's press on a card first (unless they let this kind
        through on this site, at the app, or the reviewer passed it), then exactly one press, then a look
        for its result. In read only nothing is pressed."""
        from .. import acting
        site = gate.site_of(url)
        said = str(facts.get("said") or "")
        if acting.decide(self.mode(), kind, site) == "refuse":
            return {"status": "failed", "kind": kind,
                    "line": f"[read-only] {kind} ({button[:40]}): the person set the browser to read only, so nothing "
                            f"was pressed; read and tell them, or ask_person"}
        once = (kind, _shape(url), button, said, self.page.near.get(ident, ""))
        if once in self.pressed and (kind == "like" or self.pressed[once] == await self._shown()):
            return {"status": "ok", "kind": kind, "proven": True,
                    "line": f"[already] {kind} on {button[:40]!r} was pressed once in "
                    f"this run, on the person's word, and nothing on the page has changed since; pressing it again "
                    f"would {'undo it' if kind == 'like' else 'do it twice'}. It is done: say done, or go on with "
                    f"what is left of the task"}
        words = (kind, _shape(url), str(facts.get("to") or ""), _squeeze(said))
        if said and words in self.sent:
            # The same words to the same place a second time: they went once, on the person's yes. What was
            # typed again goes back out of the box, so nothing is left there to send by itself.
            for t in [t for t in self.typed if not t["out"] and _squeeze(t["text"]) == _squeeze(said)]:
                try:
                    await self.page.on_node(t["node"], RESTORE_JS, [t.get("shows", t["text"]), t["before"]])
                except Exception:
                    logger.debug("[Agent] operate: a field typed into twice was gone before it could be put back")
                self.typed.remove(t)
            return {"status": "ok", "kind": kind, "proven": True,
                    "line": f"[already] {clip(said, 60)!r} went out once in this run ({kind}), on the person's word; "
                    f"sending it again would put it there twice, so nothing was pressed and the box was put back. "
                    f"It is done: say done, or go on with what is left of the task"}
        # A form that still wants their password, card or code goes to them before any card.
        missing = await self.page.secret_unfilled(ident)
        if missing is not None:
            return {"status": "refused", "kind": kind,
                    "line": f"[handover] the form needs their own {missing or 'password'} first, a secret field "
                            f"never typed for them; it has the focus now, and they say 继续 once it is in"}
        card = {"id": secrets.token_urlsafe(8), "kind": kind, "site": site, "url": url, "button": button[:120],
                "to": str(facts.get("to") or "")[:80], "label": element[:120], "why": why[:200],
                # A like or a delete has no words of its own: the card quotes the item it acts on; a form, the filled
                # fields it sends.
                "text": (said or (str(facts.get("row") or "") if kind in ("like", "delete") else
                                  self._fields(facts) if kind in ("submit", "account", "order") else ""))[:2000],
                "skippable": kind in gate.SKIPPABLE and not acting.sensitive(site) and why != gate.FRAME_WHY}
        # The act, for its one question in this turn: the person's answer to it before stands.
        card["act"] = act = ("outside", kind, site, button[:120], said, card["to"])
        kept = self.kept.get(act)
        if kept is not None and not kept.get("yes"):
            return {"status": "declined", "kind": kind, "line": f"[declined] the person said no to {kind} "
                                                                f"({button[:40]}) before in this turn; nothing was pressed"}
        skipped = card["skippable"] and self.attended() and self.allowed.skips(site, kind)
        # Whatever was typed on this site in this run and not let out yet may leave with the press, not only the
        # box beside the button: the reviewer and code's floor (an address or number they never gave) see it all.
        typed = [f"{t['field']}: {t['text']}" for t in self.typed if not t["out"] and t["site"] == site
                 and _squeeze(t["text"]) not in _squeeze(card["text"])]
        # Enter in another site's frame is the floor: the card, never the reviewer.
        decision, passed, does = ("kept", "", "") if kept else ("ask", "", "") if (
            skipped or why == gate.FRAME_WHY) else await self._decided(
            kind, site, {"target": [card["to"]] if card["to"] else [],
                         "text": "\n".join([card["text"], *typed]).strip()[:2000], "page_says": button[:120],
                         "fields": ["password"] if (facts.get("form") or {}).get("password") else []})
        if not skipped:
            # The card names the act as the reviewer read it; 始终允许 is offered for that reading.
            kind = card["kind"] = _reading(kind, does)
            card["skippable"] = kind in gate.SKIPPABLE and not acting.sensitive(site) and why != gate.FRAME_WHY
        if decision == "turned":
            return {"status": "declined", "kind": kind, "line": f"[declined] {kind} ({button[:40]}): {passed}; "
                                                                "nothing was pressed"}
        if not skipped and decision not in ("go", "kept"):
            try:
                card["picture"] = await self.page.screenshot()
            except Exception:
                card["picture"] = ""
            # The control, for a ring that stays on it while the card waits.
            card["node"] = ident
            answer = await self.ask(card)
            if answer is None:
                return {"status": "declined", "kind": kind,
                        "line": f"[declined] nobody answered the card for {kind} ({button[:40]}); nothing was pressed"}
            if answer.get("waiting"):
                # Its time ran out with the card up: the act waits on it, and the card stays answerable.
                return {"status": "waiting", "kind": kind, "result": answer["waiting"],
                        "line": f"[waiting] the card for {kind} ({button[:40]}) waits for the person's answer; "
                                f"nothing was pressed"}
            self.kept[act] = {"yes": bool(answer.get("yes"))}
            if not answer.get("yes"):
                # What was typed for it stays unsent: the run puts it back when it ends.
                for t in self.typed:
                    if not t["out"]:
                        t["no"] = True
                return {"status": "declined", "kind": kind,
                        "line": f"[declined] the person said no to {kind} ({button[:40]}); nothing was pressed"}
            if answer.get("never") and card["skippable"]:
                self.allowed.set(site, kind, True)
            # What the card showed must still be what is pressed (a control drawn again is found again).
            again = None
            try:
                ident = await self._fresh(ident) or ident
                again = await self.page.facts(ident)
            except Exception:
                pass
            # The words, the recipient and the item it acts on (its numbers aside: a live count moves) all as shown.
            if again is None or str(again.get("said") or "") != said or str(again.get("to") or "") != str(
                    facts.get("to") or "") or (kind in ("like", "delete") and _item(again) != _item(facts)) or (
                    not said and kind in ("submit", "account", "order") and self._fields(again) != self._fields(facts)):
                return {"status": "failed", "kind": kind,
                        "line": "[changed] the page changed since the card; nothing was pressed, look again"}
        seen_before = await self._count(said)
        # What the person let out goes out: this press's own site's requests, a post once one carries the card's
        # words (a page that swaps them as the button goes down sends nothing). A press in a frame from another site
        # sends to that frame's site.
        self._pressing = {"site": gate.site_of(self.page.frame_url(ident) or url), "said": said, "matched": False,
                          "aside": [], "kind": kind}
        try:
            line = await press()
            await asyncio.sleep(PRESS_TAIL_S)
        finally:
            pressed, self._pressing = self._pressing, None
        swapped = bool(said and not pressed["matched"] and pressed["aside"])
        for params in pressed["aside"]:
            await self.tab.cdp("Fetch.failRequest" if swapped else "Fetch.continueRequest",
                               {"requestId": params["requestId"], **({"errorReason": "BlockedByClient"} if swapped else {})})
        if swapped:
            return {"status": "failed", "kind": kind, "line": "[changed] what the page sent on the press did not carry "
                    "the words the card showed; it was stopped and nothing went. Look again"}
        if not line.startswith("[ok]"):
            return {"status": "failed", "kind": kind, "line": line}
        self.confirmed = True
        # What was typed before this press went with it: the person confirmed the form it sits in.
        for t in self.typed:
            t["out"] = True
        proof = await self._proof(ident, said, seen_before) if said else ""
        if kind == "like":
            proof += "; a like is a toggle: pressing it again would undo it"
        self.pressed[once] = await self._shown()
        if said:
            self.sent.add(words)
        how = ("let through by the person's setting for this site" if skipped else passed if decision == "go"
               else "confirmed by the person" + (" before in this turn" if decision == "kept" else ""))
        return {"status": "ok", "kind": kind, "outside": True,
                "line": f"{line} ({kind}, {how}; pressed once){proof}"}

    async def _shown(self) -> int:
        try:
            return hash(await self.page.visible_text())
        except Exception:
            return 0

    async def _count(self, said: str) -> int:
        if not said:
            return 0
        try:
            return (await self.page.visible_text()).count(fold(said).strip()[:200])
        except Exception:
            return 0

    async def _proof(self, ident: int, said: str, seen_before: int) -> str:
        """Sent is not done: the box emptied, or the words showing once more on the page."""
        start = time.monotonic()
        while time.monotonic() - start < PROOF_S:
            if await self._count(said) > seen_before:
                return f"; the page now shows {said[:60]!r}"
            try:
                if not str((await self.page.facts(ident)).get("said") or ""):
                    return "; the box is empty again"
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return "; not seen on the page yet: look before saying it went through, and never press again"


async def picture_of(page: Page) -> dict:
    """The page as it stands in its view, in its own pixels, with each control and clickable thing of the last
    look boxed under its id; nothing else drawn on it."""
    from PIL import Image, ImageDraw
    metrics = await page.cdp("Page.getLayoutMetrics")
    view = metrics.get("cssVisualViewport") or {}
    wide, high = float(view.get("clientWidth") or 1280), float(view.get("clientHeight") or 800)
    shot = await page.cdp("Page.captureScreenshot", format="png", clip={
        "x": float(view.get("pageX") or 0), "y": float(view.get("pageY") or 0), "width": wide, "height": high,
        "scale": 1})
    image = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")
    if image.size != (round(wide), round(high)):
        # A scaled display gives device pixels: the picture is brought to the page's own pixels, so a point
        # read off it and a click mean the same place.
        image = image.resize((round(wide), round(high)), Image.LANCZOS)
    draw = ImageDraw.Draw(image)
    boxed = 0
    for ident, (role, _name) in list(page.last.items()):
        if role in ("scrollable", "text"):
            continue
        try:
            b = await page.box(ident)
        except Exception:
            continue
        if b["w"] < 4 or b["h"] < 4 or b["x"] + b["w"] < 0 or b["y"] + b["h"] < 0 or b["x"] > wide or b["y"] > high:
            continue
        boxed += 1
        draw.rectangle([b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]], outline=(255, 90, 0), width=2)
        draw.rectangle([b["x"], b["y"] - 13, b["x"] + 8 + 7 * len(str(ident)), b["y"]], fill=(255, 90, 0))
        draw.text((b["x"] + 3, b["y"] - 13), str(ident), fill=(255, 255, 255))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return {"status": "ok", "picture": base64.b64encode(out.getvalue()).decode(), "boxed": boxed,
            "size": [round(wide), round(high)]}


async def look_at(page: Page, most: int, *, ref: Optional[int] = None, interactive: bool = False, find: str = "",
                  start: int = 0, picture: bool = False) -> dict:
    """A look at a page (surface.py's look): its print or a part of it, what was left out
    and why, what the app noticed on it (hints from WALL_JS and signin.CHECK_JS, never a verdict), and with
    `picture` the page as it stands. Nothing on the page is pressed and no request of it is held."""
    # What the page's own script draws once it has loaded (a list, a table) is waited for.
    await page.settle()
    state = await page.state(min(max(int(most), STATE_MAX), STATE_MOST))
    probe = await page._evaluate("() => ({url: location.href, title: document.title})") or {}
    left = [f"lines {a + 1}-{b} are not shown (report_limit): look from {a + 1} goes on, or find" for a, b in page.cut]
    if ref is not None or interactive or find or start:
        state, part = view_of(page.lines, page.spans, ref=ref, interactive=interactive, find=find, start=start,
                              most=most)
        left = [part] if part else []
    if page.secrets:
        left.append(f"what {len(page.secrets)} secret fields hold is masked (private)")
    if any(line.startswith("--- frame") and "for the person" in line for line in page.lines):
        left.append("payment and sign-in frames are not read (private)")
    noticed = []
    try:
        wall = await page._evaluate(WALL_JS) or {}
        judged = signin.judge(wall, await page._evaluate(signin.CHECK_JS) or {}, None, False)
    except Exception:
        judged = {}
    if judged.get("kind") == "verification":
        noticed.append(f"a check for people is on the page ({clip(judged.get('evidence', ''), 80)})")
    elif judged.get("kind"):
        noticed.append(f"the page asks them to sign in ({clip(judged.get('evidence', ''), 80)})")
    if judged.get("prompt"):
        noticed.append(f"a sign-in box that can be closed is over the page (“{clip(judged['prompt'], 60)}”)")
    if page.secrets:
        noticed.append("a password field")
    if judged.get("drawn"):
        noticed.append("a drawn part (canvas, map or game) the lines cannot show: picture shows it")
    found = {"status": "ok", "state": state, "url": probe.get("url", ""), "title": probe.get("title", ""),
             "omitted": "; ".join(left), "noticed": "; ".join(noticed)}
    if picture:
        found["picture"] = (await picture_of(page))["picture"]
    return found


VERBS = ("click", "type", "select_option", "key", "hover", "drag", "scroll_to", "wait_for", "upload_file", "go",
         "back", "read")


def action_of(step: dict) -> dict:
    """One step of the act tool (its converged verbs, Playwright's and the browser agents' names) as the Hands
    action it is: click {ref | text | x,y, double, right}, type {ref, text, submit, add}, select_option {ref,
    values}, key {keys}, hover {ref}, drag {ref, to}, scroll_to {ref?, text?, up}, wait_for {text | gone},
    upload_file {ref, file: the file chooser it opens is given that path}, go {url}, back, read {question,
    look_for?, section?}. Every step keeps its "what" line. ValueError for a verb it does not know."""
    verb = step.get("do")
    ref = step.get("ref")
    took = {"what": str(step.get("what") or "")}
    if ref is not None:
        took["id"] = ref
    if verb in ("click", "upload_file"):
        took.update(do="click", element=str(step.get("element") or ""))
        if verb == "upload_file":
            took["file"] = str(step.get("file") or "")
        for key in ("text", "x", "y"):
            if step.get(key) is not None:
                took[key] = step[key]
        if step.get("double") is True:
            took["double"] = True
        if step.get("right") is True:
            took["button"] = "right"
        return took
    if verb == "type":
        return {**took, "do": "type", "text": str(step.get("text") or ""), "enter": step.get("submit") is True,
                "append": step.get("add") is True}
    if verb == "select_option":
        values = step.get("values")
        values = values if isinstance(values, list) else [values]
        return {**took, "do": "type", "text": ", ".join(str(v) for v in values if v is not None)}
    if verb == "key":
        return {**took, "do": "press", "key": str(step.get("keys") or "")}
    if verb == "hover":
        return {**took, "do": "hover"}
    if verb == "drag":
        return {**took, "do": "drag", "to": step.get("to")}
    if verb == "scroll_to":
        return {**took, "do": "scroll", "direction": "up" if step.get("up") is True else "down",
                "until": str(step.get("text") or "")}
    if verb == "wait_for":
        return {**took, "do": "wait", "until": str(step.get("text") or ""), "gone": str(step.get("gone") or "")}
    if verb == "go":
        return {**took, "do": "navigate", "url": str(step.get("url") or "")}
    if verb == "back":
        return {**took, "do": "back"}
    if verb == "read":
        return {**took, "do": "read", "question": str(step.get("question") or ""), **(
            {"more": step["more"]} if step.get("more") in ("step", "all") else {}),
                "look_for": str(step.get("look_for") or ""), **({"section": step["section"]}
                                                                if isinstance(step.get("section"), int) else {})}
    raise ValueError(str(verb))


def _carried(url: str, read: str, own: str) -> str:
    """Words read in this run that an address carries past its host (READ_RUN characters running, spaces aside, as
    written, decoded or base64), and that the person's own words do not hold: the first such run, or "". As much of
    the address as the page itself shows from its host on (a link written out, or the start of one a page tells the
    model to fill in) is the page's own, not carried."""
    shown, mine = _squeeze(read), _squeeze(own)
    address = _squeeze(unquote_plus(url.split("://", 1)[-1]))
    host = len(_squeeze(urlsplit(url).netloc))
    # The longest start of it the page shows: a start shown means every shorter one is too.
    low, high = 0, len(address)
    while low < high:
        mid = (low + high + 1) // 2
        low, high = (mid, high) if address[:mid] in shown else (low, mid - 1)
    rest = address[max(low, host):]
    texts = [rest] + [_squeeze(x) for x in _texts({"url": rest})[4:]]
    for t in texts:
        for i in range(len(t) - READ_RUN + 1):
            if t[i:i + READ_RUN] in shown and t[i:i + READ_RUN] not in mine:
                return t[i:i + READ_RUN]
    return ""


async def read_page(ask_model, hands, action: dict, question: str, room: int) -> dict:
    """The `read` step made: the page's words from `hands` (what its document holds now, the page not scrolled; a
    list that draws only the rows in view read through), read for `question` by web/pagetext.py in `room` tokens
    (the reply model's page room), more of the page loaded through `hands` only when the answer needs it. The
    step's result with "answer", "found" (yes, no or partly), "calls" (the model calls the reading took), "read"
    (what was read and what was left), "sections" and "text" (the words the model saw)."""
    result = await hands.act(action)
    if result.get("status") != "ok":
        return {**result, "answer": "", "found": "", "calls": 0, "read": "", "sections": [], "text": ""}

    async def more(how: str) -> tuple[str, bool]:
        got = await hands.act({"do": "read", "more": how, "question": question})
        return str(got.get("text") or ""), bool(got.get("goes_on"))
    part = action.get("section")
    part = part if isinstance(part, int) and not isinstance(part, bool) else -1
    text = str(result.pop("text", "") or "")
    reading = await pagetext.answer(ask_model, question, text, room, more=more, goes_on=bool(result.get("goes_on")),
                                    look_for=str(action.get("look_for") or ""), part=part)
    return {**result, **reading}
