"""How the character sees a page and acts in it: Chrome's accessibility tree, one
line per control with an id the model acts by, over any Tab's `cdp`.

From browser-use's browser_use/ax.py (MIT, see NOTICE), itself a port of Browser
Use Pi's ax.ts: the state print, the settle wait, the covered-target check, the
trusted press and release, insertText plus a real last key, and the value read
back. Changed for Mibo: CDP goes through `Tab.cdp(method, params)` (no daemon,
no event thread; the settle wait watches the page alone), secret fields print as
`[secret]` with their value hidden and are never typed into, and FACTS_JS reads
what the consent gate (gate.py) needs of a target: its form, the text box it
belongs to, the heading over it and any money amount by it.

Ours on top, for what a text-only model could not see or do: one DOM snapshot
a look finds what is shown as clickable though the tree names no control there
(a row, a menu entry, a date in a calendar: a pointer cursor, the outermost of
its kind), boxes that scroll by themselves (a virtual conversation list) and
the tooltip, icon or class of a control with no name; Playwright's ways for
ordinary controls (date, time, colour and range fields set by value, several options of a list chosen at once,
the click point on the first box of an element) and the rest of Playwright MCP's actions (keys with modifiers,
typing that adds to a field, double and right clicks, a drag from one element onto another with the browser's own
drag and drop caught as Playwright's crDragDrop does, waiting until words show or go); an element can be found by the
words it shows; scrolling goes inside a box and on until some words show; the
settle wait holds while the page says it is busy; a control drawn again since
the look is found again by its role, name and the text before it; and a ring
stays round the control a card asks about.

The whole page, not the window: the state holds the whole tree when it fits;
when it does not, its top, its end and what stands on the screen now, with
how many lines are cut above and below, so scrolling shows the rest (as
browser-use shows what is in view). A table's cells stay apart by `|`.
`whole` gives every word of the page for a question (browser-use's and
Stagehand's extract), the page taken to its end, a list that draws only the
rows in view read step by step; `seek` scrolls to a row not drawn yet.

A frame from another site runs in a process of its own, which the page's
session does not reach: as Playwright and Stagehand do, each is read in its own
session (Tab.frames, from Target.setAutoAttach with flatten) and printed where
it sits, its ids numbered apart (FRAME_IDS) so an action goes to its session.
Payment, sign-in and bot-check frames are named and left to the person.
"""
from __future__ import annotations

import asyncio
import bisect
import difflib
import json
import math
import re
import time
from typing import Any
from urllib.parse import urlsplit

from ..browsed import secret as secret_address

CONTROLS = set(
    "button link textbox searchbox combobox listbox option checkbox radio switch slider spinbutton tab menuitem "
    "menuitemcheckbox menuitemradio treeitem".split(" "))
# Text inside these is one line; text across them is not joined.
BLOCKS = {"paragraph", "listitem", "row", "LabelText"}
CELLS = {"cell", "gridcell", "columnheader", "rowheader"}
NATIVE_INPUTS = {"Date", "DateTime", "InputTime", "ColorWell"}
# Input types set by value, as Playwright fills them (injectedScript.ts fill), and the form each takes.
SET_BY_VALUE = {"date": "YYYY-MM-DD", "time": "HH:MM, 24-hour", "datetime-local": "YYYY-MM-DDTHH:MM",
                "month": "YYYY-MM", "week": "YYYY-Www", "color": "#rrggbb", "range": "a number"}
# How much of a field's value and how many of a list's options a line shows.
VALUE_MAX = 400
OPTIONS_MAX = 40
CONTEXT_LOSS = re.compile(r"Execution context was destroyed|Cannot find context|Cannot find default execution "
                          r"context|Inspected target navigated|Target closed|No frame")
CDP_TIMEOUT_MS = 15_000
SURROGATE = re.compile("[\ud800-\udfff]")
# What a state the model sees may hold, characters: for the least able model, and the most for one whose context
# window has room, as browser-use gives tens of thousands.
STATE_MAX = 6000
STATE_MOST = 20_000
# A line with an id on it, and how many lines a look's find returns (read_page's find: up to 20).
ID_LINE = re.compile(r"\*?\[\d+\]")
FIND_MAX = 20
SECRET_MASK = "•••"
# A field's own words that make it secret, whatever its type says: card and one-time-code words as Chromium's
# autofill heuristics name them (card #, ccnum, csc, a 6-digit code, 2FA, PIN), and their Chinese.
SECRET_LABEL = re.compile(r"password|passcode|passwd|\b(?:card|cc|acct)[\s._-]?(?:number|num|no\b|#)|cvv|cvc|cvn|csc|"
                          r"security\s*code|one[-\s]?time|\botp\b|\botc\b|totp|\b2fa\b|two[-\s]?factor|\bpin\b|"
                          r"verification\s*code|sms\s*code|\benter\s+(?:the\s+)?code\b|\d[-\s]?digit\s+code|"
                          r"密码|口令|验证码|校验码|动态码|卡号|银行卡|信用卡|安全码|支付密码",
                          re.I)
SECRET_AUTOCOMPLETE = re.compile(r"^(?:cc-.*|one-time-code|current-password|new-password)$", re.I)
SECRET_SELECTOR = ("input[type=password],input[autocomplete^=cc-],input[autocomplete=one-time-code],"
                   "input[autocomplete=current-password],input[autocomplete=new-password]")
# A pointer cursor on these says nothing new: the tree already names them, or they hold the page.
POINTER_SKIP = {"A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "LABEL", "OPTION", "SUMMARY", "HTML", "BODY", "IFRAME",
                "VIDEO", "AUDIO", "CANVAS"}
# Where a control with no name keeps its words, in this order; then its class.
HINT_ATTRS = ("data-tooltip", "data-tip", "data-title", "data-original-title", "aria-description", "title", "alt")
# A name that says nothing by itself: empty, a count, a sign.
NAMELESS = re.compile(r"^[\W\d_]{0,6}$")
# How long a blank page is given to draw.
BLANK_S = 8.0
# How far `scroll ... until` goes, in wheels, and how long it waits for more to load at the end of a list.
UNTIL_WHEELS = 30
MORE_S = 2.5
# Reading the whole page: steps down each scroller at most, and characters at most.
WHOLE_STEPS = 60
TEXT_MOST = 400_000
# How long `wait` with `until` or `gone` waits for the words (Playwright MCP's wait_for), seconds.
WAIT_S = 15.0
# Mouse moves a drag takes from one control to the other, so a list that follows the mouse sees it go by.
DRAG_STEPS = 8
# Playwright's actionability (NOTICE): a control not yet visible, stable, enabled or free to take the click is tried
# again after these waits (its retry ladder, the last repeating) until ACTION_S have gone, as its default action
# timeout in Playwright MCP.
ACTION_S = 5.0
ACTION_WAITS = (0.0, 0.02, 0.1, 0.1, 0.5)
NOT_YET = re.compile(r"Target (hidden|disabled|covered|outside viewport|not stable)")
# Keys a press may name; a chord holds them with modifiers, by Playwright's names and the usual short ones, as
# CDP's mask (Alt 1, Control 2, Meta 4, Shift 8); one letter, digit or sign is a key too.
KEYS = {"Enter": 13, "Escape": 27, "Tab": 9, "Backspace": 8, "Delete": 46, "ArrowDown": 40, "ArrowUp": 38,
        "ArrowLeft": 37, "ArrowRight": 39, "PageDown": 34, "PageUp": 33, "Home": 36, "End": 35, " ": 32}
KEY_NAMES = {**{k.lower(): k for k in KEYS}, "space": " ", "esc": "Escape", "del": "Delete", "return": "Enter"}
MODIFIERS = {"alt": ("Alt", 1), "control": ("Control", 2), "ctrl": ("Control", 2), "meta": ("Meta", 4),
             "cmd": ("Meta", 4), "command": ("Meta", 4), "shift": ("Shift", 8)}
SIGNS = {"/": 191, ".": 190, ",": 188, "-": 189, "=": 187, ";": 186, "'": 222, "[": 219, "]": 221}
# The ids in a frame from another site: the frame's number in the page times this, plus the id in its own process.
FRAME_IDS = 10_000_000
# Frames the person fills in themselves, beside browsed.secret's sign-in and payment addresses: card fields of the
# payment services and bot checks, by host or path.
PERSON_FRAME = re.compile(r"(^|\.)(stripe\.com|stripe\.network|braintreegateway\.com|braintree-api\.com|adyen\.com|"
                          r"checkout\.com|klarna\.com|paypalobjects\.com|hcaptcha\.com|recaptcha\.net|"
                          r"challenges\.cloudflare\.com|arkoselabs\.com|geetest\.com)(/|$)|/recaptcha/", re.I)


def for_person(url: str) -> bool:
    """A frame never read or acted in: payment, sign-in or a bot check."""
    parts = urlsplit(url)
    return secret_address(url) or bool(PERSON_FRAME.search(f"{parts.hostname or ''}{parts.path}"))


def norm(s: Any) -> str:
    if isinstance(s, bool):
        s = "true" if s else "false"
    return re.sub(r"[ \t\n\r\f﻿]+", " ", "" if s is None else str(s)).strip()


def fold(s: str) -> str:
    return re.sub(r"[\s﻿]+", " ", s).lower()


def clip(s: str, n: int) -> str:
    return f"{s[:n]}…" if len(s) > n else s


def chord(keys: str) -> tuple[str, int, str]:
    """A key or chord as the model writes it ("Control+Enter", "shift+tab", "Delete"): its spelling, the modifier
    mask and the key. ValueError for a modifier or key it does not know."""
    *held, key = [p.strip() for p in str(keys).split("+")] if str(keys).strip() else [" "]
    key = KEY_NAMES.get(key.lower(), key)
    if not (key in KEYS or key in SIGNS or (len(key) == 1 and key.isascii() and key.isalnum())):
        raise ValueError(f"no key {key[:20]!r}")
    if any(p.lower() not in MODIFIERS for p in held):
        raise ValueError(f"no chord {str(keys)[:30]!r}")
    mods = dict(MODIFIERS[p.lower()] for p in held)
    named = [m for m in ("Control", "Alt", "Meta", "Shift") if m in mods]
    return "+".join(named + ["Space" if key == " " else key]), sum(mods[m] for m in named), key


def quote(s: str) -> str:
    return SURROGATE.sub(lambda m: f"\\u{ord(m.group()):04x}", json.dumps(s, ensure_ascii=False))


def well_formed(s: str) -> str:
    return SURROGATE.sub("�", s)


def val(o: Any) -> Any:
    return o.get("value") if isinstance(o, dict) else None


def prop(n: dict, name: str) -> Any:
    return next(((p.get("value") or {}).get("value") for p in n.get("properties") or [] if p.get("name") == name),
                None)


def is_context_loss(e: BaseException) -> bool:
    return bool(CONTEXT_LOSS.search(str(e)))


def now() -> float:
    return time.monotonic() * 1000


DATE_YMD = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
DATE_SHOWN = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})")
CLOCK = re.compile(r"(上午|下午|晚上)?\s*(\d{1,2})\s*[:：时点]\s*(\d{1,2})?(?:\s*[:：]\s*(\d{1,2}))?\s*分?\s*(?:([ap])\.?\s*m\.?)?",
                   re.I)


def as_value(kind: dict, text: str) -> str:
    """A date, time, month, week or colour as a person writes it, in the form its field takes (SET_BY_VALUE);
    a date given by numbers only is read in the order the field shows it. Anything else as it is."""
    t, typ = norm(text), kind["type"]

    def day(s: str) -> tuple[str | None, str]:
        m = DATE_YMD.search(s)
        if m:
            y, mo, d = m.groups()
        else:
            m = DATE_SHOWN.search(s)
            if not m:
                return None, s
            a, b, y = m.groups()
            mo, d = (b, a) if kind["order"].startswith("d") else (a, b)
        return f"{y}-{int(mo):02}-{int(d):02}", s[m.end():]

    def clock(s: str) -> str | None:
        m = CLOCK.search(s)
        if not m:
            return None
        part, h, mi, sec, half = m.groups()
        hour = int(h)
        if half:
            hour = hour % 12 + (12 if half.lower() == "p" else 0)
        elif part in ("下午", "晚上") and hour < 12:
            hour += 12
        return f"{hour:02}:{int(mi or 0):02}" + (f":{int(sec):02}" if sec else "")
    if typ == "date":
        return day(t)[0] or t
    if typ == "time":
        return clock(t) or t
    if typ == "datetime-local":
        d, rest = day(t)
        c = clock(rest) if d else None
        return f"{d}T{c}" if d and c else t
    if typ == "month":
        m = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})", t)
        return f"{m.group(1)}-{int(m.group(2)):02}" if m else t
    if typ == "week":
        m = re.search(r"(\d{4})\s*[-年]?\s*(?:W|第)?\s*(\d{1,2})", t, re.I)
        return f"{m.group(1)}-W{int(m.group(2)):02}" if m else t
    if typ == "color":
        m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", t, re.I)
        return "#" + (m.group(1) if len(m.group(1)) == 6 else "".join(c * 2 for c in m.group(1))).lower() if m else t
    return t


# URL, title, document identity, a count of DOM changes and form input; same-origin frames count as the page.
PROBE_JS = r"""() => {
  const w = window;
  const watch = (win, counted) => {
    const doc = win.document;
    if (doc.__paWatched) return;
    doc.__paWatched = true;
    if (counted) { w.__paLast = performance.now(); w.__paN++; }
    const styled = new WeakMap();
    new MutationObserver((records) => {
      const now = performance.now();
      let real = false;
      for (const r of records) {
        if (r.attributeName && r.oldValue === r.target.getAttributeNS(r.attributeNamespace, r.attributeName)) continue;
        // The ring the app draws to show where it acts is not the page changing.
        if ([...r.addedNodes, ...r.removedNodes].some((x) => x.dataset && 'paRing' in x.dataset)) continue;
        if (r.attributeName !== 'style') real = true;
        else if (now - (styled.get(r.target) ?? -1e9) > 200) real = true;
        if (r.attributeName === 'style') styled.set(r.target, now);
      }
      if (real) { w.__paLast = now; w.__paN++; }
    }).observe(doc, {subtree: true, childList: true, attributes: true, attributeOldValue: true, characterData: true});
    for (const type of ['input', 'change']) win.addEventListener(type, () => w.__paN++, true);
  };
  const first = !w.__paObs;
  if (first) { w.__paObs = true; w.__paLast = performance.now(); w.__paN = 0; w.__paDoc = Math.random(); watch(w, false); }
  const frames = (win) => {
    for (let i = 0; i < win.frames.length; i++) {
      let f;
      try { f = win.frames[i]; if (!f.document) continue; } catch { continue; }
      watch(f, !first);
      frames(f);
    }
  };
  frames(w);
  // Busy as the page says (aria-busy on something shown), for 10 s at most: some pages never say they are done.
  const busy = [...document.querySelectorAll('[aria-busy=true]')].some((e) => e.checkVisibility());
  if (!busy) w.__paBusy = null; else if (w.__paBusy == null) w.__paBusy = performance.now();
  const el = document.scrollingElement || document.documentElement;
  return {idle: performance.now() - w.__paLast, ready: document.readyState, url: location.href, title: document.title,
          n: w.__paN, doc: w.__paDoc, files: !!document.querySelector('input[type=file]'),
          busy: busy && performance.now() - w.__paBusy < 10000, sy: el.scrollTop, sh: el.scrollHeight, vh: innerHeight};
}"""

# Text actually shown, lowercased: the AX tree keeps opacity:0 text, which reads as a success that is not there.
VISIBLE_JS = r"""() => {
  const out = [];
  const shown = new Map();
  const roots = [document];
  for (let i = 0; i < roots.length; i++) {
    const walk = document.createTreeWalker(roots[i], NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (n.nodeType === Node.ELEMENT_NODE) {
        if (n.shadowRoot) roots.push(n.shadowRoot);
        if (n.contentDocument) roots.push(n.contentDocument);
        continue;
      }
      let e = n.parentElement;
      if (!e || !n.textContent?.trim()) continue;
      while (e.parentElement && getComputedStyle(e).display === 'contents') e = e.parentElement;
      if (!shown.has(e)) shown.set(e, e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
      if (shown.get(e)) out.push(n.textContent);
    }
  }
  return out.join(' ').replace(/\s+/g, ' ').toLowerCase();
}"""

# Text on the screen now, lowercased: shown, inside the window and not scrolled out of the boxes it sits in (for
# `scroll ... until`, which stops once the words are where a person sees them).
SCREEN_JS = r"""() => {
  const out = [], roots = [document], clips = new Map();
  const clip = (e) => {
    if (!clips.has(e)) { const s = getComputedStyle(e); clips.set(e, s.overflowX !== 'visible' || s.overflowY !== 'visible'); }
    return clips.get(e);
  };
  for (let i = 0; i < roots.length; i++) {
    const walk = document.createTreeWalker(roots[i], NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (n.nodeType === Node.ELEMENT_NODE) { if (n.shadowRoot) roots.push(n.shadowRoot); continue; }
      const e = n.parentElement;
      if (!e || !n.textContent.trim() || !e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      let {top, bottom, left, right} = range.getBoundingClientRect();
      top = Math.max(top, 0); bottom = Math.min(bottom, innerHeight); left = Math.max(left, 0); right = Math.min(right, innerWidth);
      for (let a = e; a && top < bottom && left < right; a = a.parentElement) {
        if (a === document.body || a === document.documentElement || !clip(a)) continue;
        const r = a.getBoundingClientRect();
        top = Math.max(top, r.top); bottom = Math.min(bottom, r.bottom); left = Math.max(left, r.left); right = Math.min(right, r.right);
      }
      if (top < bottom && left < right) out.push(n.textContent);
    }
  }
  return out.join(' ').replace(/\s+/g, ' ').toLowerCase();
}"""

# The words of the whole document as plain lines, past what the window shows (browser-use's extract reads the page
# as markdown the same way): headings as `## `, list items as `- `, a table row by row with its cells between `|`
# and its header row marked, through open shadow roots and frames of the page's own site; what fields hold is left
# out, and so are the site's navigation and footer (nav, footer and their ARIA roles), as Readability and
# trafilatura drop them. With `this` an element, only what is in it.
TEXT_JS = r"""function(most) {
  const root = this && this.nodeType === 1 ? this : document.body;
  const out = [];
  let line = [], size = 0;
  const end = () => { const t = line.join(' ').replace(/\s+/g, ' ').trim(); if (t) { out.push(t); size += t.length; } line = []; };
  const cell = (c) => (c.innerText || '').replace(/\s+/g, ' ').trim().replace(/\|/g, '/');
  const walk = (n) => {
    if (size > most) return;
    if (n.nodeType === 3) { if (n.textContent.trim()) line.push(n.textContent); return; }
    if (n.nodeType !== 1) return;
    const tag = n.tagName;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'INPUT', 'TEXTAREA', 'SELECT', 'svg'].includes(tag)) return;
    if (tag === 'SLOT') { const given = n.assignedNodes({flatten: true}); for (const c of given.length ? given : n.childNodes) walk(c); return; }
    if (!n.checkVisibility({checkVisibilityCSS: true})) return;
    if (n !== root && (tag === 'NAV' || tag === 'FOOTER' || /^(navigation|contentinfo)$/.test(n.getAttribute('role') || ''))) return;
    if (tag === 'IFRAME') {
      try { const d = n.contentDocument; if (d && d.body) { end(); walk(d.body); end(); } } catch (_) {}
      return;
    }
    if (tag === 'BR') { end(); return; }
    if (tag === 'TABLE') {
      end();
      for (const row of n.rows) {
        const cells = [...row.cells].map(cell);
        if (!cells.some(Boolean)) continue;
        out.push(`| ${cells.join(' | ')} |`);
        size += out[out.length - 1].length;
        if (row.parentElement.tagName === 'THEAD' || [...row.cells].every((c) => c.tagName === 'TH')) out.push(`|${' --- |'.repeat(cells.length)}`);
      }
      return;
    }
    const heading = /^H[1-6]$/.test(tag);
    const block = heading || /^(block|flex|grid|list-item|table|flow-root)/.test(getComputedStyle(n).display);
    if (block) end();
    if (heading) line.push('##');
    else if (tag === 'LI') line.push('-');
    for (const c of (n.shadowRoot || n).childNodes) walk(c);
    if (block) end();
  };
  walk(root);
  end();
  return out.join('\n');
}"""

# A scroller (`this`, or the page when it is none) moved: "down" on most of a screen, "end" to its end, a number to
# that place, "where" nowhere; how far down it is, how tall, how much of it is below what it shows, and whether it
# moved.
SCROLL_JS = r"""function(to) {
  const e = this && this.nodeType === 1 ? this : (document.scrollingElement || document.documentElement);
  const was = e.scrollTop;
  if (to === 'down') e.scrollTop = was + Math.max(80, e.clientHeight * 0.85);
  else if (to === 'end') e.scrollTop = e.scrollHeight;
  else if (to !== 'where') e.scrollTop = to;
  return {top: e.scrollTop, height: e.scrollHeight, below: e.scrollHeight - e.scrollTop - e.clientHeight,
          moved: e.scrollTop !== was};
}"""

# A box that scrolls by itself (`this`): how much of it is below what it shows, how tall it is, and how far down its
# words reach (the lowest bottom of any text drawn in it, in its own height). A list that draws only the rows in
# view has words in a small part of its height.
DRAWN_JS = r"""function() {
  const top = this.getBoundingClientRect().top - this.scrollTop, walk = document.createTreeWalker(this, 4);
  const r = document.createRange();
  let words = 0;
  for (let n = walk.nextNode(), k = 0; n && k < 5000; n = walk.nextNode(), k++) {
    if (!n.data.trim()) continue;
    r.selectNodeContents(n);
    const b = r.getBoundingClientRect();
    if (b.height) words = Math.max(words, b.bottom - top);
  }
  return {below: this.scrollHeight - this.scrollTop - this.clientHeight, height: this.scrollHeight, words};
}"""

# The caret at the end of a field or an editable box, for typing that adds to what it holds.
END_JS = r"""function() {
  const e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  if (typeof e.setSelectionRange === 'function') {
    try { e.setSelectionRange(e.value.length, e.value.length); return; } catch (x) {}
  }
  const r = document.createRange();
  r.selectNodeContents(e);
  r.collapse(false);
  getSelection().removeAllRanges();
  getSelection().addRange(r);
}"""

# Where an element sits among its own: the words of the item before and after it (the element, or the nearest of
# its ancestors, up to five, with neighbours that have words), so a drag can say where it put something.
PLACE_JS = r"""function() {
  let e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  const words = (n) => n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40) : '';
  for (let up = 0; up < 5 && e.parentElement && e.parentElement !== document.body
       && !words(e.previousElementSibling) && !words(e.nextElementSibling); up++) e = e.parentElement;
  return {before: words(e.previousElementSibling), after: words(e.nextElementSibling)};
}"""

POINT_JS = r"""function(editableCover) {
  const forms = """ + json.dumps(SET_BY_VALUE) + r""";
  const e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  if (!e || !e.isConnected) throw Error('Target detached');
  // Playwright's enabled: a native control's own disabled, a fieldset's, and aria-disabled on it or what holds it.
  if (e.matches(':disabled') || e.closest('[inert]') || e.closest('[aria-disabled=true]')) throw Error('Target disabled');
  if (e.tagName === 'INPUT' && forms[e.type]) throw Error(`a ${e.type} field opens a picker that cannot be seen; type ` +
    `the value into its id as ${e.type === 'range' ? `a number from ${e.min || 0} to ${e.max || 100}` : forms[e.type]}`);
  if (!e.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) throw Error('Target hidden');
  // Playwright's point: the middle of the first box of the element with area on screen (a link wrapped over two
  // lines has two), and a hit anywhere in the button or link the element is part of.
  const rects = [...e.getClientRects()].map((r) => {
    const left = Math.max(r.left, 0), top = Math.max(r.top, 0);
    return {x: left, y: top, w: Math.min(r.right, innerWidth) - left, h: Math.min(r.bottom, innerHeight) - top};
  }).filter((r) => r.w > 0 && r.h > 0 && r.w * r.h > 0.99);
  if (!rects.length) throw Error('Target outside viewport');
  const x = rects[0].x + rects[0].w/2, y = rects[0].y + rects[0].h/2;
  const t = e.matches('input,textarea,select') || e.isContentEditable ? e : e.closest('button,[role=button],a,[role=link]') || e;
  const hit = e.getRootNode().elementFromPoint(x, y);
  const textField = (n) => n && (['INPUT','TEXTAREA'].includes(n.tagName) || n.isContentEditable);
  const covered = !t.contains(hit) && !(hit && hit.contains(e) && getComputedStyle(hit).pointerEvents !== 'none' && hit.tagName === 'LABEL') && !(editableCover && textField(e) && textField(hit));
  // An option of a list where several can be chosen is clicked as a person adds one: with Ctrl.
  const multi = e.tagName === 'OPTION' && !!e.closest('select[multiple]');
  let px = x, py = y;
  for (let w = e.ownerDocument.defaultView; w && w.frameElement; w = w.parent) {
    const f = w.frameElement, fr = f.getBoundingClientRect(), cs = getComputedStyle(f);
    px += fr.x + f.clientLeft + parseFloat(cs.paddingLeft);
    py += fr.y + f.clientTop + parseFloat(cs.paddingTop);
  }
  return {x: px, y: py, covered, multi};
}"""

# Playwright's stable: the same box over two animation frames running (an element sliding in is waited for). A tab
# out of sight may get no frames, so a frame is also 50 ms at most.
STABLE_JS = r"""function() {
  const e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  const box = () => { const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].join(); };
  const frame = () => new Promise((done) => { requestAnimationFrame(done); setTimeout(done, 50); });
  return (async () => {
    let last = box();
    for (let n = 0; n < 2; n++) {
      await frame();
      const now = box();
      if (now !== last) throw Error('Target not stable');
      last = now;
    }
    return true;
  })();
}"""

# Where a frame's own document starts inside its frame element, in the coordinates of the process's top page.
INNER_JS = r"""function() {
  const f = this, r = f.getBoundingClientRect(), cs = getComputedStyle(f);
  let x = r.x + f.clientLeft + parseFloat(cs.paddingLeft), y = r.y + f.clientTop + parseFloat(cs.paddingTop);
  for (let w = f.ownerDocument.defaultView; w && w.frameElement; w = w.parent) {
    const o = w.frameElement, or = o.getBoundingClientRect(), os = getComputedStyle(o);
    x += or.x + o.clientLeft + parseFloat(os.paddingLeft);
    y += or.y + o.clientTop + parseFloat(os.paddingTop);
  }
  return {x, y};
}"""

# Where an element is, in the top page's coordinates (for the ring and the marks).
BOX_JS = r"""function() {
  const e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  const r = e.getBoundingClientRect();
  let x = r.x, y = r.y;
  for (let w = e.ownerDocument.defaultView; w && w.frameElement; w = w.parent) {
    const f = w.frameElement.getBoundingClientRect();
    x += f.x + w.frameElement.clientLeft;
    y += f.y + w.frameElement.clientTop;
  }
  return {x, y, w: r.width, h: r.height};
}"""

# The ring held round a control while a card waits on it, drawn in a world of the app's own (the page's scripts
# cannot reach its script): it follows the control as the page scrolls or lays itself out again, until the next
# ring or UNRING_JS takes it away.
HOLD_JS = r"""function() {
  const e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement, doc = e.ownerDocument;
  for (const old of doc.querySelectorAll('[data-pa-ring]')) old.remove();
  const d = doc.createElement('div');
  d.dataset.paRing = '';
  d.style.cssText = 'position:fixed;box-sizing:border-box;border:3px solid #1f1f22;border-radius:10px;' +
    'box-shadow:0 0 0 3px #fff,0 0 0 6px rgba(31,31,34,.25);pointer-events:none;z-index:2147483647;';
  doc.documentElement.appendChild(d);
  const follow = () => {
    if (!d.isConnected) return;
    const r = e.getBoundingClientRect();
    Object.assign(d.style, {left: `${r.x - 5}px`, top: `${r.y - 5}px`, width: `${r.width + 10}px`,
      height: `${r.height + 10}px`, display: e.isConnected ? '' : 'none'});
    requestAnimationFrame(follow);
  };
  follow();
}"""
UNRING_JS = "for (const old of document.querySelectorAll('[data-pa-ring]')) old.remove()"

# The element showing these words, the smallest and the one in view first; into shadow roots and same-origin
# frames. Text is searched, then the words a control keeps in its attributes.
LOCATE_JS = r"""(want) => {
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const w = norm(want);
  if (!w) return null;
  const roots = [document], found = [];
  for (let i = 0; i < roots.length; i++) {
    const walk = document.createTreeWalker(roots[i], NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (n.nodeType === Node.ELEMENT_NODE) {
        if (n.shadowRoot) roots.push(n.shadowRoot);
        if (n.tagName === 'IFRAME') { try { if (n.contentDocument) roots.push(n.contentDocument); } catch (_) {} }
        const said = norm(n.getAttribute('aria-label') || n.getAttribute('title') || n.getAttribute('placeholder') ||
          n.getAttribute('alt') || n.getAttribute('data-tooltip') || '');
        if (said && said.includes(w)) found.push([n, said === w ? 0 : 2]);
        continue;
      }
      const e = n.parentElement;
      if (!e || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(e.tagName) || !norm(n.textContent).includes(w)) continue;
      found.push([e, norm(e.innerText) === w ? 0 : 1]);
    }
  }
  const shown = found.filter(([e]) => e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
  const away = (e) => { const r = e.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight ? 0 : 1; };
  shown.sort((a, b) => a[1] - b[1] || away(a[0]) - away(b[0]));
  return shown.length ? shown[0][0] : null;
}"""

# A control the page drew anew since it was found (a live board draws every row again each second): the one
# element now showing the same words after the same text (letters only, as `near` keeps it). With `press` it is
# pressed in the same moment by the page's own events, so no redraw comes between finding it and pressing it.
AGAIN_JS = r"""(name, near, press) => {
  const letters = (s) => String(s || '').replace(/[^\p{L}]/gu, '').toLowerCase();
  const want = letters(name), ctx = letters(near);
  if (!want) return null;
  const roots = [document], all = [];
  for (let i = 0; i < roots.length; i++) {
    for (const e of roots[i].querySelectorAll('*')) {
      if (e.shadowRoot) roots.push(e.shadowRoot);
      if (e.tagName === 'IFRAME') { try { if (e.contentDocument) roots.push(e.contentDocument); } catch (_) {} }
      if (letters(e.getAttribute('aria-label') || e.textContent || e.value) === want && e.checkVisibility()) all.push(e);
    }
  }
  // The outermost of nested matches (a button and the span in it).
  const tops = all.filter((e) => !all.some((o) => o !== e && o.contains(e)));
  const fits = tops.filter((e) => {
    if (!ctx) return true;
    for (let a = e.parentElement, i = 0; a && i < 6; a = a.parentElement, i++) {
      if (letters(a.textContent).includes(ctx)) return tops.filter((o) => a.contains(o)).length === 1;
    }
    return false;
  });
  if (fits.length !== 1) return null;
  const e = fits[0];
  if (press) {
    const r = e.getBoundingClientRect();
    const o = {bubbles: true, cancelable: true, composed: true, view: window, button: 0,
      clientX: r.x + r.width / 2, clientY: r.y + r.height / 2};
    e.dispatchEvent(new PointerEvent('pointerdown', {...o, pointerId: 1, isPrimary: true}));
    e.dispatchEvent(new MouseEvent('mousedown', o));
    if (e.focus) e.focus();
    e.dispatchEvent(new PointerEvent('pointerup', {...o, pointerId: 1, isPrimary: true}));
    e.dispatchEvent(new MouseEvent('mouseup', o));
    e.click();
  }
  return e;
}"""

# The nearest box around an element that scrolls by itself, else the page: how far down it is.
SCROLLER_JS = r"""function() {
  let e = this.nodeType === Node.ELEMENT_NODE ? this : this.parentElement;
  const up = (a) => a.parentElement || (a.getRootNode() instanceof ShadowRoot ? a.getRootNode().host : null);
  for (; e && e !== document.body && e !== document.documentElement; e = up(e)) {
    if (/(auto|scroll|overlay)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 8) break;
  }
  const el = !e || e === document.body || e === document.documentElement
    ? (document.scrollingElement || document.documentElement) : e;
  return {top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight};
}"""

CENTER_JS = ("function(){const e=this.nodeType===1?this:this.parentElement;"
             "e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});}")

KIND_JS = ("function(){const e=this.nodeType===1?this:this.parentElement;if(!e||!e.isConnected)throw Error('Target detached');"
           "if(e.matches(':disabled')||e.closest('[inert]'))throw Error('Target disabled');"
           "if(!e.checkVisibility({checkVisibilityCSS:true}))throw Error('Target hidden');"
           # The order a date field shows its parts in (mdy, dmy, ymd), as the browser's language writes a date.
           "const order=new Intl.DateTimeFormat(navigator.language).formatToParts(new Date(2001,10,22))"
           ".filter(p=>p.type!=='literal').map(p=>p.type[0]).join('');"
           "return {tag:e.tagName,type:(e.type||'').toLowerCase(),min:e.min||'',max:e.max||'',order};}")

# A value put into a date, time, colour or range field through the native setter, then input and change, as
# Playwright fills them; the field's own read-back, or null (and the old value back) when it did not take it.
SET_JS = r"""function(v) {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set, old = this.value;
  this.focus();
  set.call(this, v);
  if (this.value !== v && this.type !== 'range') { set.call(this, old); return null; }
  this.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
  this.dispatchEvent(new Event('change', {bubbles: true}));
  return this.value;
}"""

# Playwright's selectOption: one option, or for a list where several can be chosen, exactly the ones named
# (commas between).
SELECT_JS = r"""function(want){
  const n = s => String(s).replace(/\s+/g,' ').trim().toLowerCase(), opts = [...this.options].filter(o => !o.disabled);
  const pick = (w) => {
    const pre = opts.filter(o => n(o.label).startsWith(n(w)));
    const o = opts.find(o => n(o.label) === n(w) || n(o.value) === n(w)) ?? (pre.length === 1 ? pre[0] : undefined);
    if (!o) throw Error((pre.length > 1 ? 'several options start with ' : 'no option ') + JSON.stringify(w) + '. Options: ' + (pre.length > 1 ? pre : opts).slice(0, 40).map(o => o.label).join(' | '));
    return o;
  };
  let chosen;
  if (this.multiple) {
    const whole = opts.find(o => n(o.label) === n(want) || n(o.value) === n(want));
    chosen = whole ? [whole] : String(want).split(/\s*[,，;；|、\n]\s*/).filter(Boolean).map(pick);
    for (const o of this.options) o.selected = chosen.includes(o);
  } else {
    chosen = [pick(want)];
    this.value = chosen[0].value;
  }
  this.dispatchEvent(new Event('input',{bubbles:true,composed:true})); this.dispatchEvent(new Event('change',{bubbles:true}));
  return chosen.map(o => o.label).join(' | ');
}"""

MOVED_JS = ("function(){const e=this.nodeType===1?this:this.parentElement;let a=document.activeElement;"
            "while(a&&a.shadowRoot&&a.shadowRoot.activeElement)a=a.shadowRoot.activeElement;"
            "return !!a&&a!==e&&!e.contains(a)&&(['INPUT','TEXTAREA'].includes(a.tagName)||a.isContentEditable);}")

VALUE_JS = ("function(){const e=this.nodeType===1?this:this.parentElement;"
            "return e.isContentEditable?e.innerText:(e.value??'');}")

# The element with focus, as an object CDP can name (for Enter and Space), into shadow roots and same-origin frames.
ACTIVE_JS = ("(() => {let a = document.activeElement; for (;;) { if (a && a.shadowRoot && a.shadowRoot.activeElement) "
             "a = a.shadowRoot.activeElement; else if (a && a.contentDocument && a.contentDocument.activeElement) "
             "a = a.contentDocument.activeElement; else return a; }})()")

# The first empty secret field of the target's form, focused for the person; its label, or null.
SECRET_EMPTY_JS = """function (selector) {
  const form = this.form || this.closest('form');
  const field = form && [...form.querySelectorAll(selector)].find((e) => !e.value);
  if (!field) return null;
  field.focus();
  return ((field.labels && field.labels[0] && field.labels[0].innerText) || field.getAttribute('aria-label')
    || field.placeholder || field.name || '').trim().slice(0, 80);
}"""
# What the gate and the card need of a target, read off the page: its form (method, a search form, a password in
# it, the button Enter goes through), the text box it belongs to and what that holds, the heading over that box
# (to whom), a money amount by it, whether it is secret, and the filled fields a press sends, for the card.
FACTS_JS = r"""function() {
  const secretWords = new RegExp(""" + json.dumps(SECRET_LABEL.pattern) + r""", 'i');
  const e = this.nodeType === 1 ? this : this.parentElement;
  const tag = e.tagName.toLowerCase(), type = (e.type || '').toLowerCase(), role = e.getAttribute('role') || '';
  const ac = (e.getAttribute('autocomplete') || '').toLowerCase();
  const own = (n) => [n.getAttribute('aria-label'), n.getAttribute('name'), n.id, n.getAttribute('placeholder'),
    n.getAttribute('data-placeholder'), n.title, n.labels && n.labels[0] && n.labels[0].innerText].filter(Boolean).join(' ');
  // What an icon-only control says on hover or in its drawing.
  const svgTitle = e.querySelector('svg title'), img = e.querySelector('img[alt]');
  const tip = [e.getAttribute('data-tooltip'), e.getAttribute('data-tip'), e.getAttribute('data-title'),
    e.getAttribute('data-original-title'), e.getAttribute('aria-description'), e.title, svgTitle && svgTitle.textContent,
    img && img.alt].filter(Boolean).join(' ').trim().slice(0, 120);
  const label = own(e);
  const text = (e.innerText || e.value || '').trim().replace(/\s+/g, ' ').slice(0, 200);
  const isText = (n) => n && (n.tagName === 'TEXTAREA' || n.isContentEditable ||
    (n.tagName === 'INPUT' && ['text', 'search', 'email', 'url', 'tel', ''].includes((n.type || '').toLowerCase())));
  const searchy = (n) => (n.type || '').toLowerCase() === 'search' || ['searchbox', 'combobox'].includes(n.getAttribute('role') || '') ||
    !!n.closest('[role=search],search') || /search|搜索|查找|搜一搜/i.test(own(n));
  const form = e.form || e.closest('form');
  let f = null;
  if (form) {
    const els = [...form.elements];
    const fields = els.filter((x) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(x.tagName) && !['hidden', 'submit', 'image', 'button', 'reset'].includes(x.type));
    const subs = els.filter((x) => (x.tagName === 'BUTTON' && (!x.getAttribute('type') || x.type === 'submit')) || (x.tagName === 'INPUT' && ['submit', 'image'].includes(x.type)));
    const method = (form.getAttribute('method') || 'get').toLowerCase();
    f = {method, action: form.action, fields: fields.length, submit: subs.length > 0,
      password: !!form.querySelector('input[type=password],input[autocomplete=current-password],input[autocomplete=new-password]'),
      search: form.getAttribute('role') === 'search' || !!form.closest('[role=search],search') || els.some((x) => x.type === 'search') ||
        // One field sent by GET is a search only when it says so: a page's script can post any form.
        (method === 'get' && fields.length === 1 && fields[0].tagName !== 'TEXTAREA' && (searchy(fields[0]) ||
          /^(?:q|query|keywords?|kw|wd|word|s|k|search|term)$/i.test(fields[0].name || '') ||
          /search|find|query|搜索/i.test(form.getAttribute('action') || ''))),
      button: subs[0] ? (subs[0].innerText || subs[0].value || subs[0].getAttribute('aria-label') || '').trim().slice(0, 80) : ''};
  }
  const submitter = !!form && ((tag === 'button' && (!e.getAttribute('type') || type === 'submit')) || (tag === 'input' && ['submit', 'image'].includes(type)));
  let composer = isText(e) && !searchy(e) ? e : null;
  // Up through a shadow root too: a button and its box may sit straight under one.
  const up = (n) => n.parentElement || (n.parentNode instanceof ShadowRoot ? n.parentNode : n instanceof ShadowRoot ? n.host : null);
  for (let a = up(e), i = 0; !composer && a && i < 5; a = up(a), i++) {
    const near = [...a.querySelectorAll('textarea,[contenteditable],input')].filter((x) => isText(x) && !searchy(x) && x.checkVisibility());
    if (near.length === 1) composer = near[0];
    else if (near.length > 1) break;
  }
  const said = composer ? (composer.isContentEditable ? composer.innerText : (composer.value || '')).trim() : '';
  const hint = composer ? own(composer) : '';
  let to = '';
  // `from` is the box, or the shadow host holding it once the walk has gone out of its shadow root.
  let from = composer || e, a = from.parentElement;
  if (!a && from.getRootNode().host) { from = from.getRootNode().host; a = from.parentElement; }
  // Ours: a chat's header is often no heading but a short bold line at the top of its pane.
  const before = (h) => h.checkVisibility() && !h.contains(from) && (h.compareDocumentPosition(from) & Node.DOCUMENT_POSITION_FOLLOWING);
  const bold = (h) => [...h.childNodes].some((c) => c.nodeType === 3 && /\p{L}/u.test(c.textContent)) && h.innerText.trim().length <= 40 &&
    parseInt(getComputedStyle(h).fontWeight) >= 600 && !h.closest('a,button,label,li,[role=button],[role=option],[role=listitem],[role=row]');
  while (a && !to) {
    // Ours: a pane's own short <header> holds whom a chat is with, even in plain type (a site's banner is longer).
    let heads = [...a.querySelectorAll('h1,h2,h3,h4,[role=heading],header')].filter((h) => before(h) &&
      (h.tagName !== 'HEADER' || h.innerText.trim().length <= 40));
    if (!heads.length) heads = [...a.querySelectorAll('div,span,b,strong,header,p')].slice(0, 800).filter((h) => before(h) && bold(h));
    if (heads.length) to = heads[heads.length - 1].innerText.trim().split('\n')[0].slice(0, 80);
    if (a === document.body) break;
    if (a.parentElement) { a = a.parentElement; continue; }
    const host = a.getRootNode().host;
    if (!host) break;
    from = host;
    a = host.parentElement;
  }
  const money = ((e.innerText || '') + ' ' + (e.parentElement ? e.parentElement.innerText : '')).match(
    /(?:[$€£¥￥₹₩]\s?\d[\d.,]*|\d[\d.,]*\s?(?:USD|EUR|GBP|JPY|CNY|RMB|元|円))/);
  const testIds = ['data-testid', 'data-test', 'data-qa'].map((a) => e.getAttribute(a) || '').join(' ');
  // Ours: where a link goes, when it goes to another page (not "#", not a script).
  const anchor = e.closest('a[href]');
  const away = anchor && /^https?:/.test(anchor.href) && anchor.href.split('#')[0] !== location.href.split('#')[0] ? anchor.href : '';
  // Ours: the item a control acts on (its post, its row), for a card with no words of its own to quote.
  let row = '';
  for (let a = e.parentElement, i = 0; a && i < 6 && !row; a = a.parentElement, i++) {
    const t = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length > text.length + 4) row = t.slice(0, 160);
  }
  // The fields a press sends (its form's, or those in the nearest box that holds fields), filled and not secret,
  // as [caption, value]: 就诊日期 2026-12-05, 时段 14:00.
  const short = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const secretOf = (x) => x.type === 'password' || /^(?:cc-|one-time-code|current-password|new-password)/i.test(
    x.getAttribute('autocomplete') || '') || secretWords.test(own(x));
  const captionOf = (x) => {
    const lab = x.labels && [...x.labels].find((l) => !l.contains(x));
    let c = short(lab && lab.innerText) || short(x.getAttribute('aria-label')) || short(x.placeholder) || short(x.title);
    let box = x;
    if (!c && (x.type === 'radio' || x.type === 'checkbox')) {
      const legend = x.closest('fieldset') && x.closest('fieldset').querySelector('legend');
      c = short(legend && legend.innerText) || short((x.closest('[role=radiogroup],[role=group]') || x).getAttribute('aria-label'));
      // The box that holds the whole group, whose words stand before it: 时段 over a row of radios.
      const group = x.name ? [...document.getElementsByName(x.name)] : [x];
      for (let i = 0; i < 5 && box.parentElement && !group.every((g) => box.contains(g)); i++) box = box.parentElement;
    }
    for (let b = box, i = 0; !c && b && i < 3; b = b.parentElement, i++) {
      const prev = b.previousElementSibling;
      if (prev && !prev.querySelector('input,select,textarea') && short(prev.innerText).length <= 24) c = short(prev.innerText);
    }
    return (c || x.name || x.id || '').slice(0, 24);
  };
  const valueOf = (x) => {
    if (x.tagName === 'SELECT') return [...x.selectedOptions].map((o) => short(o.text)).join('、');
    if (x.type === 'checkbox' || x.type === 'radio') return x.checked ? short(x.labels && x.labels[0] && x.labels[0].innerText) || x.value : '';
    if (x.type === 'file') return [...(x.files || [])].map((f) => f.name).join('、');
    return x.checkVisibility() ? short(x.value) : '';
  };
  let scope = form;
  for (let a = e.parentElement, i = 0; !scope && a && i < 6; a = a.parentElement, i++) {
    if (a.querySelector('input,select,textarea')) scope = a;
  }
  const fields = [];
  for (const x of scope ? [...(form ? form.elements : scope.querySelectorAll('input,select,textarea'))].slice(0, 60) : []) {
    if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(x.tagName) || ['hidden', 'submit', 'image', 'button', 'reset'].includes(x.type)
      || x.disabled || secretOf(x)) continue;
    const value = valueOf(x).slice(0, 80), caption = captionOf(x);
    const same = caption && fields.find((f) => f[0] === caption);
    if (!value) continue;
    if (same) same[1] = `${same[1]}、${value}`.slice(0, 80);
    else if (fields.length < 10) fields.push([caption, value]);
  }
  return {tag, type, role, row, fields, label: label.slice(0, 200), text, tip, link: tag === 'a' || role === 'link', away: away.slice(0, 300),
    iconOnly: !text && !e.getAttribute('aria-label') && !!e.querySelector('svg,img,i'), form: f, submitter,
    switch: role === 'switch' || (tag === 'input' && type === 'checkbox' && e.getAttribute('role') === 'switch'),
    textEntry: isText(e), search: searchy(e), composer: !!composer, said: said.slice(0, 2000), hint: hint.slice(0, 120),
    to, money: money ? money[0] : '', classes: `${typeof e.className === 'string' ? e.className : ''} ${testIds} ${e.id}`.trim().slice(0, 200),
    secret: type === 'password' || /^cc-/.test(ac) || ['one-time-code', 'current-password', 'new-password'].includes(ac)};
}"""


def merge(merged: list[str], last: list[str], now: list[str]) -> list[str]:
    """What a scroller shows after a step (`now`) put after what it showed before it (`last`, already in `merged`):
    the lines after the part the two have in common; all of `now` when they share none (a page that only grew
    keeps its first lines and adds the rest)."""
    if not last:
        return list(now)
    blocks = [b for b in difflib.SequenceMatcher(None, last, now, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return merged + now
    end = blocks[-1]
    return merged + now[end.b + end.size:]


def view_of(lines: list[str], spans: dict, *, ref: int | None = None, interactive: bool = False, find: str = "",
            start: int = 0, most: int = STATE_MAX) -> tuple[str, str]:
    """A part of a print's uncut lines, a page's or a window's (desk/uia.py prints windows in the same grammar), as
    the snapshot tools give one: `ref` the lines of one id and under it (`spans`), `interactive` only the lines of
    ids, `find` the id lines holding these words (FIND_MAX, as read_page's find), `start` the lines from that one
    (1 for the first). (the lines, what was left out)."""
    numbered = list(enumerate(lines, 1))
    if ref is not None:
        if ref not in spans:
            return "", f"no ref {ref} in the last look; look again for the refs now"
        a, b = spans[ref]
        numbered = numbered[a:b]
    if interactive:
        numbered = [(n, x) for n, x in numbered if ID_LINE.match(x)]
    if find:
        want = [w for w in fold(find).split() if w]
        # Words are looked for in what a line says, not in its ref.
        hits = [(n, x) for n, x in numbered if want and all(w in fold(ID_LINE.sub("", x, 1)) for w in want)]
        ids = [(n, x) for n, x in hits if ID_LINE.match(x)]
        numbered = (ids or hits)[:FIND_MAX]
        more = len(ids or hits) - len(numbered)
        if not numbered:
            return "", f"no line holds “{clip(find, 60)}”"
    numbered = [(n, x) for n, x in numbered if n >= start]
    shown, size = [], 0
    for n, x in numbered:
        size += len(x) + 1
        if size > most:
            break
        shown.append((n, x))
    left = []
    if find and more > 0:
        left.append(f"{more} more lines hold these words (report_limit)")
    if len(shown) < len(numbered):
        left.append(f"lines {numbered[len(shown)][0]}-{numbered[-1][0]} are not shown (report_limit): look "
                    f"from {numbered[len(shown)][0]} goes on")
    return well_formed("\n".join(x for _, x in shown)), "; ".join(left)


class Lines(list):
    """The state's lines, each with where it stands down the page (`at`, from the last node the walk passed that
    has a place: `y`), so a page too long for one look shows what is on the screen."""

    def __init__(self) -> None:
        super().__init__()
        self.at: list[float | None] = []
        self.y: float | None = None

    def append(self, line: str) -> None:
        super().append(line)
        self.at.append(self.y)

    def insert(self, i: int, line: str) -> None:
        super().insert(i, line)
        self.at.insert(i, self.y)

    def pop(self, i: int = -1) -> str:
        self.at.pop(i)
        return super().pop(i)


class Refused(Exception):
    """An action the page operations will not do; its text goes back to the loop as is."""


class Page:
    """The state print and id-based actions on one Tab. Ids are Chrome backendNodeIds, valid until the next look."""

    def __init__(self, tab):
        self.tab = tab
        # Role and name of every id in the last print, and which of them are secret.
        self.last: dict[int, tuple[str, str]] = {}
        self.secrets: set[int] = set()
        self.interactive = 0
        # The text line before each id (digits out: a live count moves), the print before this one, and every
        # line of the last print before it was cut (for `grep`).
        self.near: dict[int, str] = {}
        self.before: tuple[dict, dict] = ({}, {})
        self.lines: list[str] = []
        # Where in those lines each id's own line and the lines under it are ([start, end), for a look at one
        # part), and the stretches the print left out ([start, end)).
        self.spans: dict[int, tuple[int, int]] = {}
        self.cut: list[tuple[int, int]] = []
        # Prints made of this page so far: a look's snapshot number.
        self.looks = 0
        # The whole value of each field whose line shows only its start (for `grep`).
        self.values: dict[int, str] = {}
        # The app's own script world in the page, for the ring: (document, context id); the last document a
        # blank page was waited for on.
        self._world: tuple[Any, int] | None = None
        self._waited: Any = None
        # Frames from another site read in the last look: number -> {"session", "url", "owner"}, the owner being
        # its frame element as (session, id) in the document that holds it; each frame's number by its frame id,
        # kept across looks so its ids stay the same.
        self.frames: dict[int, dict] = {}
        self._numbers: dict[str, int] = {}
        # What a drag the browser caught carries (Input.dragIntercepted), and whether that event is heard yet.
        self._dragged: dict | None = None
        self._drag_heard = False

    async def cdp(self, method: str, **params: Any) -> dict:
        return await self.tab.cdp(method, params)

    async def _in(self, session: str, method: str, **params: Any) -> dict:
        """A DevTools command to the page ("") or to a frame from another site."""
        return await self.tab.cdp(method, params, session)

    def _node(self, ident: int) -> tuple[str, int]:
        """The session an id is in ("" for the page) and its id there."""
        k, local = divmod(ident, FRAME_IDS)
        return (self.frames[k]["session"] if k else ""), local

    def _id(self, session: str, local: int) -> int:
        return local + FRAME_IDS * next((k for k, f in self.frames.items() if f["session"] == session), 0)

    def frame_url(self, ident: int) -> str:
        """The address of the frame from another site an id is in; "" for one of the page's own."""
        k = ident // FRAME_IDS
        return self.frames[k]["url"] if k else ""

    # --- page probes

    async def _evaluate(self, fn: str, session: str = "") -> Any:
        r = await self._in(session, "Runtime.evaluate", expression=f"({fn})()", timeout=CDP_TIMEOUT_MS,
                           awaitPromise=True, returnByValue=True, userGesture=True)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError((d.get("exception") or {}).get("description") or d.get("text"))
        return (r.get("result") or {}).get("value")

    async def probe(self, session: str = "") -> dict:
        return await self._evaluate(PROBE_JS, session)

    async def settle(self, cap_ms: float = 3000, quiet_ms: float = 120, since: float = 0, session: str = "") -> dict:
        """Wait until the document (the page's, or a frame's from another site) is parsed and the DOM has been
        quiet for quiet_ms, at most cap_ms."""
        start, probe = now(), None
        limit = cap_ms
        while now() - start < limit:
            try:
                probe = await self.probe(session)
                if probe["ready"] == "loading":
                    limit = max(cap_ms, 6000)
                # The page says it is still answering (a lookup, a list coming): it is given up to 10 s.
                if probe.get("busy"):
                    limit = max(limit, 10_000)
                probe["idle"] = min(probe["idle"], now() - (since or start))
                if probe["ready"] != "loading" and probe["idle"] >= quiet_ms and not probe.get("busy"):
                    return {"why": "quiet", "probe": probe}
            except Exception as e:
                if not is_context_loss(e):
                    return {"why": "error"}
                probe = None
            await asyncio.sleep((max(20, quiet_ms - probe["idle"]) if probe and probe["ready"] != "loading" else 60) / 1000)
        return {"why": "cap", "probe": probe}

    async def visible_text(self, session: str = "") -> str:
        return await self._evaluate(VISIBLE_JS, session) or ""

    async def screen_text(self) -> str:
        return await self._evaluate(SCREEN_JS) or ""

    async def _scroller(self, box: int | None, to: Any) -> Any:
        """SCROLL_JS on a box that scrolls by itself, or with None on the page."""
        if box is None:
            return await self._evaluate(f"function(){{return ({SCROLL_JS}).call(null, {json.dumps(to)})}}")
        return await self.on_node(box, SCROLL_JS, to)

    def _boxes(self) -> list[int]:
        """The boxes of the last look that scroll by themselves, in the page itself."""
        return [i for i, (role, _) in self.last.items() if role == "scrollable" and i < FRAME_IDS]

    async def _text(self, box: int | None = None) -> str:
        """TEXT_JS over the document, or over a box that scrolls by itself."""
        got = await (self._evaluate(f"function(){{return ({TEXT_JS}).call(null, {TEXT_MOST})}}")
                     if box is None else self.on_node(box, TEXT_JS, TEXT_MOST))
        return str(got or "")

    async def document(self) -> tuple[str, bool]:
        """What the document holds now (TEXT_JS), past what the window shows and with nothing scrolled, as the
        leaders read a page; and whether the page or a box goes on below what it shows. A box whose words fill
        less than half its height draws only the rows in view: its rows are not in the document until scrolled to,
        so it is read through now, as `whole` reads it, and is not counted as going on."""
        parts, goes_on = [await self._text()], False
        for box in self._boxes():
            try:
                drawn = await self.on_node(box, DRAWN_JS)
                if drawn["below"] > 40 and drawn["words"] < drawn["height"] / 2:
                    parts.append(self._all_of(box, await self._through(box)))
                else:
                    goes_on = goes_on or drawn["below"] > 40
            except Exception:
                # A box gone since the look.
                continue
        goes_on = goes_on or (await self._scroller(None, "where"))["below"] > 40
        return well_formed("\n".join(parts))[:TEXT_MOST], goes_on

    async def step(self, at: dict) -> tuple[str, bool]:
        """One step on, for a question what was read does not answer: the page taken to its end, where a feed loads
        more, and waited on up to MORE_S for it; each box that scrolls by itself one screen down from where the
        last step left it (`at`, by box, kept between steps). The document's words then, with what each box showed
        there, and whether any of them goes on still. Each is put back where it was."""
        began = (await self._scroller(None, "where"))["top"]
        height = (await self._scroller(None, "end"))["height"]
        waited = time.monotonic()
        while time.monotonic() - waited < MORE_S and (await self._scroller(None, "where"))["height"] == height:
            await asyncio.sleep(0.3)
        await self.settle(cap_ms=1500)
        now = await self._scroller(None, "where")
        goes_on = now["height"] > height or now["below"] > 40
        parts = [await self._text()]
        for box in self._boxes():
            try:
                was = (await self._scroller(box, "where"))["top"]
                await self._scroller(box, at.get(box, was))
                moved = await self._scroller(box, "down")
                at[box] = moved["top"]
                await self.settle(cap_ms=1500)
                parts.append(await self._text(box))
                goes_on = goes_on or moved["below"] > 40
                await self._scroller(box, was)
            except Exception:
                # A box gone since the look.
                continue
        await self._scroller(None, began)
        return well_formed("\n".join(parts))[:TEXT_MOST], goes_on

    async def whole(self) -> str:
        """The whole page's words (TEXT_JS), as far down as it goes, for a question about a whole list (its
        largest, a count): the page and each box that scrolls by itself read through (`_through`). At most
        TEXT_MOST characters."""
        parts = ["\n".join(await self._through(None))]
        for box in self._boxes():
            try:
                parts.append(self._all_of(box, await self._through(box)))
            except Exception:
                # Gone since the look.
                continue
        return well_formed("\n".join(parts))[:TEXT_MOST]

    async def _through(self, box: int | None) -> list[str]:
        """The lines of a scroller (a box, or the page with None) as far down as it goes: taken to its end (again
        while a feed loads more there); one that no longer holds what it showed at its top (a list that draws only
        the rows in view) is read step by step from the top, what each step shows merged in order. Put back where
        it was. At most WHOLE_STEPS steps."""
        async def capture() -> list[str]:
            return [x for x in (await self._text(box)).split("\n") if x]

        began = (await self._scroller(box, "where"))["top"]
        first = await capture()
        for _ in range(WHOLE_STEPS):
            height = (await self._scroller(box, "end"))["height"]
            waited = time.monotonic()
            while (time.monotonic() - waited < MORE_S
                   and (await self._scroller(box, "where"))["height"] == height):
                await asyncio.sleep(0.3)
            if (await self._scroller(box, "where"))["height"] == height:
                break
            await self.settle(cap_ms=1500)
        lines = await capture()
        kept = set(lines)
        if sum(1 for x in first if x in kept) < 0.8 * len(first):
            await self._scroller(box, 0)
            await self.settle(cap_ms=1500)
            lines, last = [], []
            for _ in range(WHOLE_STEPS):
                now_lines = await capture()
                lines, last = merge(lines, last, now_lines), now_lines
                if not (await self._scroller(box, "down"))["moved"]:
                    break
                await self.settle(cap_ms=1500)
            else:
                lines.append("... it goes on past what was read")
        await self._scroller(box, began)
        return lines

    def _all_of(self, box: int, rows: list[str]) -> str:
        """A box's rows read through, under a line that names the box."""
        name = self.last[box][1]
        return (f"--- the box{' ' + quote(clip(name, 40)) if name else ''} that scrolls by itself, all of it:\n"
                + "\n".join(rows))

    async def seek(self, words: str) -> int | None:
        """Something showing these words that is not drawn yet (a row far down a list that draws only the rows in
        view): each box that scrolls by itself, then the page, scrolled from its top until the words are there;
        its id, or None with each put back where it was."""
        for box in [*self._boxes(), None]:
            try:
                began = (await self._scroller(box, "where"))["top"]
            except Exception:
                continue
            await self._scroller(box, 0)
            for _ in range(WHOLE_STEPS):
                await self.settle(cap_ms=1500)
                found = await self.locate(words)
                if found is not None:
                    return found
                if not (await self._scroller(box, "down"))["moved"]:
                    break
            await self._scroller(box, began)
        return None

    async def _capture(self, session: str = "") -> list[dict]:
        """AX trees of a document (the page's, or with `session` a frame's from another site) and of every child
        frame in its process; a payment or sign-in frame among those is left to the person. A frame from another
        site is not listed here: `_layout` finds where it sits."""
        for i in range(21):
            try:
                nodes = (await self._in(session, "Accessibility.getFullAXTree"))["nodes"]
                children: list[tuple[str, str]] = []

                def walk(tree: dict) -> None:
                    for child in tree.get("childFrames") or []:
                        children.append((child["frame"].get("url", ""), child["frame"]["id"]))
                        walk(child)
                walk((await self._in(session, "Page.getFrameTree"))["frameTree"])
                frames = [{"url": "", "nodes": nodes}]
                for url, ident in children:
                    if for_person(url):
                        frames.append({"url": url, "nodes": [], "person": True})
                        continue
                    try:
                        frames.append({"url": url, "nodes": (await self._in(session, "Accessibility.getFullAXTree",
                                                                            frameId=ident))["nodes"]})
                    except Exception:
                        frames.append({"url": url, "nodes": []})
                return frames
            except Exception as e:
                if i >= 20 or not is_context_loss(e):
                    raise
                await asyncio.sleep(0.05)
        raise AssertionError("unreachable")

    async def _secret_ids(self, session: str = "") -> set[int]:
        """Fields secret by their type or autocomplete, wherever they are in a document."""
        root = (await self._in(session, "DOM.getDocument", depth=0))["root"]
        found = set()
        for node in (await self._in(session, "DOM.querySelectorAll", nodeId=root["nodeId"],
                                     selector=SECRET_SELECTOR))["nodeIds"]:
            found.add((await self._in(session, "DOM.describeNode", nodeId=node))["node"]["backendNodeId"])
        return found

    async def _frames(self, url: str, frames: list[dict]) -> list[dict]:
        """The documents of a look: the page's (`url`, its `frames` as captured) and each frame's from another
        site, with its owner and its number; a payment or sign-in frame is named and not read. Each comes with
        what `_layout`, the visible text and the secret fields give of it."""
        docs = [{"session": "", "url": url, "k": 0, "frames": frames}]
        found = []
        for session in await self.tab.frames():
            try:
                top = (await self._in(session, "Page.getFrameTree"))["frameTree"]["frame"]
            except Exception:
                # Gone since it was listed.
                continue
            found.append((session, top["id"], top.get("url", "")))
        self.frames = {}
        for session, frame, address in found:
            owner = None
            for where in ["", *(s for s, _, _ in found if s != session)]:
                try:
                    owner = (where, (await self._in(where, "DOM.getFrameOwner", frameId=frame))["backendNodeId"])
                    break
                except Exception:
                    # Its frame element is in another document.
                    continue
            if owner is None:
                continue
            k = self._numbers.setdefault(frame, len(self._numbers) + 1)
            doc = {"session": session, "url": address, "k": k, "owner": owner, "person": for_person(address)}
            if not doc["person"]:
                try:
                    doc["frames"] = await self._capture(session)
                except Exception:
                    continue
                self.frames[k] = {"session": session, "url": address, "owner": owner}
            docs.append(doc)
        for doc in docs:
            if doc.get("person"):
                continue
            try:
                doc["layout"] = await self._layout(doc["session"])
            except Exception:
                doc["layout"] = {"click": {}, "scroll": {}, "hint": {}, "type": {}, "frame": {}, "y": {}}
            try:
                doc["visible"] = await self.visible_text(doc["session"])
            except Exception:
                doc["visible"] = None
            try:
                doc["secret"] = await self._secret_ids(doc["session"])
            except Exception:
                doc["secret"] = set()
        return docs

    async def _layout(self, session: str = "") -> dict:
        """From one DOM snapshot: elements shown as clickable that the tree names no control (a pointer cursor,
        the outermost of its kind), boxes that scroll by themselves with how far down they are, the words a
        control with no name keeps (its tooltip, else its icon's and its class's words), the type of each date,
        time, colour or range field, and the frames shown. Keys are backendNodeIds."""
        snap = await self._in(session, "DOMSnapshot.captureSnapshot",
                              computedStyles=["cursor", "overflow-y", "visibility"], includeDOMRects=True)
        strings = snap.get("strings") or []
        out: dict[str, dict] = {"click": {}, "scroll": {}, "hint": {}, "type": {}, "frame": {}, "y": {}}

        def text(i: int) -> str:
            return strings[i] if isinstance(i, int) and 0 <= i < len(strings) else ""

        def readable(w: str) -> bool:
            """Words a person could read ("like-btn", "icon-share"), not machine ones ("css-1x2y3z")."""
            return len(w) > 2 and not re.search(r"\d\D*\d|^[a-z]{1,4}-(?=[a-z]*\d)[a-z0-9]{5,}$|__", w, re.I)
        for doc in snap.get("documents") or []:
            nodes, layout = doc.get("nodes") or {}, doc.get("layout") or {}
            parent, backend = nodes.get("parentIndex") or [], nodes.get("backendNodeId") or []
            names, kinds = nodes.get("nodeName") or [], nodes.get("nodeType") or []
            attrs = nodes.get("attributes") or []
            inner = set((nodes.get("contentDocumentIndex") or {}).get("index") or [])
            # What an icon is called in its drawing (svg <use href="#icon-trash">, an image's file name, data-icon),
            # given to the elements up to four over it; "" for a drawing with no such word.
            icon: dict[int, str] = {}
            for i in range(len(names)):
                tag = text(names[i]).upper()
                pairs = attrs[i] if i < len(attrs) else []
                found = {text(pairs[j]).lower(): text(pairs[j + 1]) for j in range(0, len(pairs) - 1, 2)}
                word = found.get("data-icon", "")
                if not word and tag == "USE":
                    word = (found.get("href") or found.get("xlink:href") or "").rsplit("#", 1)[-1]
                if not word and tag == "IMG" and not found.get("src", "").startswith("data:"):
                    word = re.sub(r"\.\w+$", "", found.get("src", "").split("?")[0].rstrip("/").rsplit("/", 1)[-1])
                if not word and tag not in ("SVG", "IMG", "I"):
                    continue
                word = word if readable(word) else ""
                a = i
                for _ in range(5):
                    if a < 0:
                        break
                    if not icon.get(a):
                        icon[a] = word
                    a = parent[a] if a < len(parent) else -1
            style: dict[int, list] = {}
            box: dict[int, int] = {}
            for k, i in enumerate(layout.get("nodeIndex") or []):
                style[i] = [text(v) for v in (layout.get("styles") or [[]])[k]]
                box[i] = k
            bounds = layout.get("bounds") or []
            if doc is (snap.get("documents") or [None])[0]:
                # Where each node of the page itself stands down the document, text included, for the state to
                # show what is on the screen when the page is too long for one look.
                for i, k in box.items():
                    if i < len(backend) and k < len(bounds) and len(bounds[k]) == 4 and bounds[k][3] > 0:
                        out["y"][backend[i]] = bounds[k][1]
            scrolls, clients = layout.get("scrollRects") or [], layout.get("clientRects") or []

            def styled(i: int) -> int:
                """The nearest node at or over i that has a layout, or -1."""
                while i >= 0 and i not in style:
                    i = parent[i] if i < len(parent) else -1
                return i
            for i, k in box.items():
                if i >= len(kinds) or kinds[i] != 1:
                    continue
                cursor, overflow, seen = (style[i] + ["", "", ""])[:3]
                b = bounds[k] if k < len(bounds) else [0, 0, 0, 0]
                if seen == "hidden" or not (b[2] > 0 and b[3] > 0):
                    continue
                name = text(names[i]).upper()
                if cursor == "pointer" and name not in POINTER_SKIP:
                    up = styled(parent[i]) if i < len(parent) else -1
                    if up < 0 or style[up][0] != "pointer":
                        out["click"][backend[i]] = True
                if overflow in ("auto", "scroll", "overlay") and k < len(scrolls) and k < len(clients):
                    whole, shown = scrolls[k], clients[k]
                    if len(whole) == 4 and len(shown) == 4 and shown[3] > 40 and whole[3] > shown[3] + 8:
                        down = whole[1] / max(1.0, whole[3] - shown[3])
                        out["scroll"][backend[i]] = ("at the top" if down < 0.02 else "at the end" if down > 0.98
                                                     else f"{round(down * 100)}% down")
                pairs = attrs[i] if i < len(attrs) else []
                found = {text(pairs[j]): text(pairs[j + 1]) for j in range(0, len(pairs) - 1, 2)}
                if name == "INPUT" and found.get("type", "").lower() in SET_BY_VALUE:
                    out["type"][backend[i]] = found["type"].lower()
                if name == "SELECT" and "multiple" in found:
                    out["type"][backend[i]] = "multiple"
                if name in ("IFRAME", "FRAME") and b[2] >= 20 and b[3] >= 20 and i not in inner:
                    # A frame shown whose document this snapshot does not hold: from another site, in its own process.
                    out["frame"][backend[i]] = urlsplit(found.get("src", "")).hostname or "?"
                hint = next((found[a] for a in HINT_ATTRS if found.get(a, "").strip()), "")
                if not hint:
                    words = [icon.get(i, ""), *(w for w in re.split(r"\s+", found.get("class", "")) if readable(w))]
                    hint = " ".join([w for w in dict.fromkeys(words) if w][:3]) or ("icon" if i in icon else "")
                if re.search(r"(^|\s)(on|active|is-active|is-on|liked|starred|selected|checked|pressed|faved)(\s|$)",
                             found.get("class", "")):
                    # An icon's own state, shown only by its class (a star filled in).
                    hint = f"{hint} · on".strip(" ·")
                if hint:
                    out["hint"][backend[i]] = norm(hint)[:40]
        return out

    async def state(self, max: int = STATE_MAX) -> str:
        """The page as one list in page order: `[id] role "name" = value states`, `## headings` and visible
        text, with `*` on ids new since the last look and secret fields masked. `[id] clickable "words"` is
        shown as clickable with no control named there; `[id] scrollable (…)` is a box that scrolls by itself;
        `{words}` after a control with no name is its tooltip, icon or class; `(form)` after a date, time, colour,
        range or several-choice field is what to type into it; a frame from another site is printed where it sits,
        between `--- frame <host> (another site) ---` and `--- end of frame`, or named in one line when it is for
        the person (payment, sign-in) or cannot be read."""
        settled = await self.settle()
        probe = settled.get("probe") or await self._evaluate("() => ({url: location.href, title: document.title})")
        frames = await self._capture()
        # A blank page is still drawing (its script comes late): it is given a few seconds.
        began = time.monotonic()
        while self._waited != probe.get("doc") and (not any(len(f["nodes"]) > 3 for f in frames) or not await self._evaluate(
                "() => !!(document.body && document.body.innerText.trim())")) and time.monotonic() - began < BLANK_S:
            await asyncio.sleep(0.5)
            settled = await self.settle()
            probe = settled.get("probe") or probe
            frames = await self._capture()
        self._waited = probe.get("doc")
        docs = await self._frames(probe.get("url", ""), frames)
        previous = self.last
        self.before = (self.last, self.near)
        self.last, self.secrets, self.interactive, self.near, self.values = {}, set(), 0, {}, {}
        self.spans, self.cut = {}, []
        self.looks += 1
        lines = Lines()
        text: list[str] = []
        tops = docs[0]["layout"]["y"]

        def flush() -> None:
            if text:
                lines.append(" ".join(text))
                text.clear()

        def context() -> str:
            """The text just before a control, digits out, to find it again once the page draws it anew."""
            here = " ".join(text) if text else next((x for x in reversed(lines) if not x.startswith(("[", "*["))), "")
            return re.sub(r"[\d\s.,:%+-]+", " ", here).strip()[-60:]

        def emit(doc: dict) -> None:
            """The lines of one document, the page's or a frame's from another site, its ids numbered by its frame."""
            base = doc["k"] * FRAME_IDS
            visible, secret, layout = doc["visible"], doc["secret"], doc["layout"]
            clickable, scrollable, hints, types = layout["click"], layout["scroll"], layout["hint"], layout["type"]
            here = urlsplit(doc["url"]).hostname
            away = {k: host for k, host in layout["frame"].items() if host != here}
            for frame in doc["frames"]:
                if frame.get("person"):
                    flush()
                    lines.append(f"--- frame {urlsplit(frame['url']).hostname}: payment or sign-in, for the person; "
                                 f"it is not read")
                    continue
                part(frame, base, visible, secret, clickable, scrollable, hints, types, away, doc)

        def part(frame: dict, base: int, visible: Any, secret: set, clickable: dict, scrollable: dict, hints: dict,
                 types: dict, away: dict, doc: dict) -> None:
            by_id = {n["nodeId"]: n for n in frame["nodes"]}
            start = len(lines)

            def words_in(n: dict | None, out: list[str], depth: int = 0) -> bool:
                """The shown words under n into out; whether a control is under it."""
                if not n or depth > 40:
                    return False
                role = str(val(n.get("role")) or "")
                if role == "StaticText":
                    t = norm(val(n.get("name")))
                    if t and (visible is None or fold(t) in visible):
                        out.append(t)
                    return False
                found = False
                for c in n.get("childIds") or []:
                    child = by_id.get(c)
                    if child and not child.get("ignored") and (
                            str(val(child.get("role")) or "") in CONTROLS or child.get("backendDOMNodeId") in clickable):
                        found = True
                    found = words_in(child, out, depth + 1) or found
                return found

            def walk(n: dict | None, inside: str) -> None:
                # Each id's lines, from its own line to the last line under it.
                start = len(lines)
                walk_one(n, inside)
                local = (n or {}).get("backendDOMNodeId")
                ident = base + local if local else local
                if ident in self.last and ident not in self.spans:
                    own = re.compile(rf"\*?\[{ident}\]")
                    at = next((i for i in range(start, len(lines)) if own.match(lines[i])), start)
                    self.spans[ident] = (at, len(lines))

            def walk_one(n: dict | None, inside: str) -> None:
                if not n:
                    return
                role = val(n.get("role"))
                role = "" if role is None else str(role)
                name = norm(val(n.get("name")))
                kids = n.get("childIds") or []
                local = n.get("backendDOMNodeId")
                ident = base + local if local else local
                if not base and local in tops:
                    lines.y = tops[local]
                if role in CELLS and text:
                    # The cells of a table's row stay apart, so its columns can be compared.
                    text.append("|")
                if local in scrollable and ident not in self.last:
                    # A box that scrolls by itself: a conversation list, a feed in a column.
                    flush()
                    self.near[ident] = context()
                    self.last[ident] = ("scrollable", name)
                    lines.append(("*" if previous and ident not in previous else "")
                                 + f"[{ident}] scrollable" + (f' "{clip(name, 60)}"' if name else "")
                                 + f" ({scrollable[local]}; scroll it by this id)")
                if local in away:
                    # A frame from another site (a chat widget, a comment box), printed where it sits.
                    flush()
                    inner = next((d for d in docs if d.get("owner") == (doc["session"], local)), None)
                    host = urlsplit(inner["url"]).hostname if inner else away[local]
                    if inner is None:
                        lines.append(f"--- frame {host}: from another site, cannot be read")
                    elif inner.get("person"):
                        lines.append(f"--- frame {host}: payment or sign-in, for the person; it is not read")
                    else:
                        lines.append(f"--- frame {host} (another site) ---")
                        emit(inner)
                        flush()
                        lines.append("--- end of frame")
                    return
                if (local in clickable and ident not in self.last and role not in CONTROLS
                        and not (prop(n, "focusable") and role != "RootWebArea")):
                    # Shown as clickable though the tree names no control: a row, a menu entry, a day.
                    said: list[str] = []
                    holds = words_in(n, said)
                    shown = clip(name or norm(" ".join(said)), 40 if holds else 100)
                    # An icon with no words (a trash can, a heart) is named by its tooltip, drawing or class.
                    hint = "" if shown else hints.get(local, "")
                    if shown or holds or hint:
                        flush()
                        self.near[ident] = context()
                        self.last[ident] = ("clickable", shown)
                        self.interactive += 1
                        lines.append(("*" if previous and ident not in previous else "") + f"[{ident}] clickable"
                                     + (f' "{shown}"' if shown else f" {{{hint}}}" if hint else ""))
                        if not holds:
                            return
                        for c in kids:
                            walk(by_id.get(c), fold(f"{inside} {shown}"))
                        return flush()
                if n.get("ignored"):
                    for c in kids:
                        walk(by_id.get(c), inside)
                    return
                if role == "StaticText":
                    t = fold(name)
                    if t and t not in inside and (visible is None or t in visible):
                        text.append(name)
                    return
                if role == "heading" and name:
                    flush()
                    lines.append(f"## {name}")
                    for c in kids:
                        walk(by_id.get(c), fold(name))
                    return
                focusable = (prop(n, "focusable") and role != "RootWebArea"
                             and (name or val(n.get("value")) or prop(n, "editable")))
                if not ident or not (role in CONTROLS or role in NATIVE_INPUTS or focusable):
                    if role not in BLOCKS:
                        for c in kids:
                            walk(by_id.get(c), inside)
                        return
                    flush()
                    for c in kids:
                        walk(by_id.get(c), inside)
                    return flush()
                near = context()
                flush()
                if lines and lines[-1].lower() == name.lower():
                    lines.pop()
                value = norm(val(n.get("value")))
                is_secret = local in secret or (role in ("textbox", "searchbox", "generic") and bool(
                    SECRET_LABEL.search(name)))
                checked, expanded = prop(n, "checked"), prop(n, "expanded")
                states = [s for s in [
                    "" if checked is None else "unchecked" if checked == "false" else "checked",
                    "" if expanded is None else "expanded" if expanded else "collapsed",
                    # aria-haspopup: a menu or a list opens from it, on a click or under the pointer.
                    "has menu" if prop(n, "hasPopup") not in (None, False, "false") else "",
                    "selected" if prop(n, "selected") else "",
                    "disabled" if prop(n, "disabled") and not prop(n, "focusable") else "",
                    "focused" if prop(n, "focused") else ""] if s]
                popup = next((c for c in map(by_id.get, kids) if c and val(c.get("role")) == "MenuListPopup"), None)
                options: list[str] = []

                def collect(c: dict | None) -> None:
                    if not c:
                        return
                    if val(c.get("role")) == "option":
                        options.append(norm(val(c.get("name"))))
                    else:
                        for k in c.get("childIds") or []:
                            collect(by_id.get(k))
                collect(popup)
                kind = types.get(local, "")
                shown = ("select" if popup else "file" if role == "button" and value
                         else "textbox" if role == "generic" and prop(n, "editable")
                         else kind if kind not in ("", "range", "multiple") else role)
                # What to type into a field set by value, or into a list where several can be chosen.
                form = (f"a number from {prop(n, 'valuemin')} to {prop(n, 'valuemax')}" if kind == "range"
                        else SET_BY_VALUE.get(kind) or ('several: "a, b"' if kind == "multiple" else ""))
                self.last[ident] = (shown, name)
                self.near[ident] = near
                self.interactive += 1
                hint = hints.get(local, "") if NAMELESS.match(name) and shown in ("button", "link", "generic") else ""
                if not hint and hints.get(local, "").endswith("· on") and shown in ("button", "link", "generic"):
                    states = [*states, "on"]
                if is_secret:
                    self.secrets.add(ident)
                    value = SECRET_MASK if value else ""
                elif len(value) > VALUE_MAX:
                    self.values[ident] = value
                more = f" | … {len(options) - OPTIONS_MAX} more" if len(options) > OPTIONS_MAX else ""
                lines.append(("*" if previous and ident not in previous else "") + f"[{ident}] {shown}"
                             + (" [secret]" if is_secret else "")
                             + (f' "{clip(name, 150)}"' if name else "")
                             + (f" {{{hint}}}" if hint and fold(hint) != fold(name) else "")
                             + (f" ({form})" if form else "")
                             + (f" = {quote(clip(value, VALUE_MAX))}" if value else "")
                             + (" " + " ".join(states) if states else "")
                             + ("  options: " + " | ".join(options[:OPTIONS_MAX]) + more if options else ""))
                if popup or role in NATIVE_INPUTS or is_secret:
                    return
                inside = fold(f"{inside} {clip(name, 150)} {value}")
                for c in kids:
                    walk(by_id.get(c), inside)
                flush()

            walk(frame["nodes"][0] if frame["nodes"] else None, "")
            flush()
            if frame["url"] and len(lines) > start:
                lines.insert(start, f"--- frame {frame['url']} ---")
                self.spans = {k: (a + (a >= start), b + (b > start)) for k, (a, b) in self.spans.items()}

        emit(docs[0])
        header = f"[state] {probe.get('title', '')} | {probe.get('url', '')}"
        if probe.get("sh") and probe.get("vh") and probe["sh"] > probe["vh"] + 40:
            # How much of the page is above and below the screen.
            down = probe.get("sy", 0) / (probe["sh"] - probe["vh"])
            header += (" | page at the top, more below" if down < 0.02 else " | page at the end" if down > 0.98
                       else f" | page {round(down * 100)}% down")
        self.lines = lines

        def fit(src: list[str], budget: float) -> int:
            size = 0
            for i, line in enumerate(src):
                size += len(line) + 1
                if size > budget:
                    return i
            return len(src)

        body = list(lines) or ["(nothing in the accessibility tree: the page is blank or still loading)"]
        if fit(lines, max) < len(lines):
            # Over max: the top of the page (its title and menu), the last lines (a dialog's Send is usually its
            # last line), and between them what stands on the screen now, as browser-use shows what is in view:
            # scrolling shows the rest.
            head, tail = fit(lines, max * 0.15), fit(lines[::-1], max * 0.1)
            end = len(lines) - tail
            room = max * 0.75
            sy, vh, at = probe.get("sy", 0), probe.get("vh", 0), lines.at
            # The lines on the screen (a box scrolled inside the page included); the window holding most of them
            # starts at the first of these.
            seen = [i for i in range(head, end) if at[i] is not None and sy - 40 <= at[i] <= sy + vh]
            shown = next((i for i in range(head, end) if at[i] is not None and at[i] >= sy), end)
            if seen:
                shown = sorted(seen, key=lambda c: (bisect.bisect_left(seen, c + fit(lines[c:end], room))
                                                    - bisect.bisect_left(seen, c)) * -1)[0]
            # A few lines over the screen's first, then on down as far as there is room.
            # (`max` here is the state's size, so the larger of two is taken by hand.)
            start = shown - fit(lines[head:shown][::-1], room * 0.15)
            start = start if start > head else head
            stop = start + fit(lines[start:end], room)
            if stop == end:
                start = end - fit(lines[head:end][::-1], room)
                start = start if start > head else head
            self.cut = [(a, b) for a, b in ((head, start), (stop, end)) if b > a]
            body = [*lines[:head],
                    *([f"... {start - head} lines above cut here: scroll up to see them, or find or read them"]
                      if start > head else []),
                    *lines[start:stop],
                    *([f"... {end - stop} lines below cut here: scroll down to see them, or find or read them"]
                      if stop < end else []),
                    *lines[end:]]
        return well_formed(f"{header}\n" + "\n".join(body) + "\n[/state]")

    # --- one node

    async def on_node(self, ident: int, fn: str, argument: Any = None) -> Any:
        return await self._call_on(*self._node(ident), fn, argument)

    async def _call_on(self, session: str, local: int, fn: str, argument: Any = None) -> Any:
        obj = (await self._in(session, "DOM.resolveNode", backendNodeId=local))["object"]
        if not obj.get("objectId"):
            raise RuntimeError("Node unavailable.")
        try:
            r = await self._in(session, "Runtime.callFunctionOn", objectId=obj["objectId"], functionDeclaration=fn,
                               arguments=[{} if argument is None else {"value": argument}], returnByValue=True,
                               awaitPromise=True)
            if r.get("exceptionDetails"):
                d = (r["exceptionDetails"].get("exception") or {}).get("description")
                raise RuntimeError(("Node operation failed." if d is None else d).split("\n    at ")[0])
            return (r.get("result") or {}).get("value")
        finally:
            try:
                await self._in(session, "Runtime.releaseObject", objectId=obj["objectId"])
            except Exception:
                pass

    async def _found(self, session: str, r: dict) -> int | None:
        """The id of the element a Runtime.evaluate in `session` gave back, or None."""
        obj = r.get("result") or {}
        if not obj.get("objectId"):
            return None
        try:
            local = (await self._in(session, "DOM.describeNode", objectId=obj["objectId"]))["node"].get("backendNodeId")
        finally:
            try:
                await self._in(session, "Runtime.releaseObject", objectId=obj["objectId"])
            except Exception:
                pass
        return None if local is None else self._id(session, local)

    async def _offset(self, session: str) -> tuple[float, float]:
        """Where a frame from another site starts in the top page, through every frame it sits in."""
        x = y = 0.0
        while session:
            where, owner = next(f["owner"] for f in self.frames.values() if f["session"] == session)
            at = await self._call_on(where, owner, INNER_JS)
            x, y, session = x + at["x"], y + at["y"], where
        return x, y

    async def focus(self, ident: int) -> None:
        session, local = self._node(ident)
        await self._in(session, "DOM.focus", backendNodeId=local)

    async def facts(self, ident: int) -> dict:
        return await self.on_node(ident, FACTS_JS) or {}

    async def value(self, ident: int) -> str:
        return str(await self.on_node(ident, VALUE_JS) or "")

    async def secret_unfilled(self, ident: int) -> str | None:
        """The form of `ident` holds a secret field still empty (a password, a card number, a code): that field
        gets the focus, for the person to type into, and its label comes back; None when there is none."""
        return await self.on_node(ident, SECRET_EMPTY_JS, SECRET_SELECTOR)

    async def active(self) -> int | None:
        """The id of the element with focus, inside a frame from another site when the focus is there; or None."""
        session = ""
        while True:
            ident = await self._found(session, await self._in(session, "Runtime.evaluate", expression=ACTIVE_JS))
            inner = next((f["session"] for f in self.frames.values() if ident is not None
                          and f["owner"] == (session, self._node(ident)[1])), None)
            if inner is None:
                return ident
            session = inner

    async def secret(self, ident: int) -> bool:
        """A field never typed into: by its type, autocomplete, or its own words (read off the DOM each time)."""
        session, local = self._node(ident)
        node = (await self._in(session, "DOM.describeNode", backendNodeId=local))["node"]
        attrs = node.get("attributes") or []
        at = {attrs[i - 1].lower(): attrs[i] for i in range(1, len(attrs), 2)}
        if at.get("type", "").lower() == "password" or SECRET_AUTOCOMPLETE.match(at.get("autocomplete", "")):
            return True
        words = " ".join(at.get(k, "") for k in ("name", "id", "placeholder", "aria-label", "title"))
        return ident in self.secrets or bool(SECRET_LABEL.search(f"{words} {self.last.get(ident, ('', ''))[1]}"))

    def describe(self, ident: int) -> str:
        n = self.last.get(ident)
        if not n:
            return f"[{ident}]"
        return f"[{ident}] {n[0]}" + (f' "{clip(n[1], 60)}"' if n[1] else "")

    def grep(self, words: str) -> str:
        """The lines of the last look (before it was cut) that hold these words, each with the line before it and
        the two after it (a table row whose name is a link goes on in the next line); and in a field's whole
        value, the part around them."""
        want = [w for w in fold(words).split() if w]
        hits: list[str] = []
        for i, line in enumerate(self.lines):
            if want and all(w in fold(line) for w in want):
                hits += [x for x in self.lines[max(0, i - 1):i + 3] if x not in hits]
        for ident, value in self.values.items():
            if not want or all(w in fold(value[:VALUE_MAX]) for w in want):
                continue
            at = fold(value).find(want[0]) if all(w in fold(value) for w in want) else -1
            if at >= 0:
                hits.append(f"{self.describe(ident)} = …{quote(value[max(0, at - 150):at + 250])}…")
        return clip("\n".join(hits), 1500)

    async def locate(self, words: str) -> int | None:
        """The element showing these words (see LOCATE_JS), in the page or else in a frame from another site
        read in the last look, as an id an action takes; it joins the last look as role "text" so the gate and
        the card can name it."""
        ident = None
        for session in ["", *(f["session"] for f in self.frames.values())]:
            ident = await self._found(session, await self._in(session, "Runtime.evaluate",
                                                               expression=f"({LOCATE_JS})({json.dumps(words)})"))
            if ident is not None:
                break
        if ident is not None and ident not in self.last:
            self.last[ident] = ("text", clip(norm(words), 80))
        return ident

    async def alive(self, ident: int) -> bool:
        try:
            return bool(await self.on_node(ident, "function(){return (this.nodeType===1?this:this.parentElement)"
                                                  ".isConnected;}"))
        except Exception:
            return False

    async def relocate(self, ident: int) -> int | None:
        """A control the page drew again since the look (a live list redraws every second): a fresh look, and
        the one control there with the same role and name, or among several the one after the same text."""
        role, name = self.last.get(ident) or self.before[0].get(ident) or ("", "")
        near = self.near.get(ident, self.before[1].get(ident, ""))
        if not role:
            return None
        await self.state()
        same = [i for i, (r, n) in self.last.items() if r == role and fold(n) == fold(name)]
        if len(same) > 1:
            same = [i for i in same if self.near.get(i, "") == near] or same[:0]
        return same[0] if len(same) == 1 else None

    async def again(self, ident: int, press: bool = False) -> int | None:
        """`ident` drawn anew since the look, found in one step in the page (AGAIN_JS) by its name and the text
        before it; the new id joins the last look. With `press` it is pressed in that same step. None when not
        exactly one is found."""
        role, name = self.last.get(ident) or self.before[0].get(ident) or ("", "")
        near = self.near.get(ident, self.before[1].get(ident, ""))
        if not name:
            return None
        session = self._node(ident)[0]
        found = await self._found(session, await self._in(
            session, "Runtime.evaluate", userGesture=True,
            expression=f"({AGAIN_JS})({json.dumps(name)}, {json.dumps(near)}, {json.dumps(press)})"))
        if found is not None:
            self.last[found], self.near[found] = (role, name), near
        return found

    async def click_again(self, ident: int) -> str:
        """A click on a control the page keeps drawing anew: found and pressed in one step (`again`), so the
        press cannot land on another row that moved under the pointer."""
        async def body() -> str:
            if await self.again(ident, press=True) is None:
                raise RuntimeError("it was drawn anew and could not be found again; nothing was clicked")
            return " (drawn anew since the look: pressed where it is now)"
        return await self._act("click", ident, body)

    async def box(self, ident: int) -> dict:
        b = await self.on_node(ident, BOX_JS)
        x, y = await self._offset(self._node(ident)[0])
        return {**b, "x": b["x"] + x, "y": b["y"] + y}

    async def _ring_world(self) -> int:
        """The app's own script world in the page now, made once per document."""
        probe = await self.probe()
        if self._world is None or self._world[0] != probe.get("doc"):
            frame = (await self.cdp("Page.getFrameTree"))["frameTree"]["frame"]["id"]
            world = await self.cdp("Page.createIsolatedWorld", frameId=frame, worldName="mibo-ring")
            self._world = (probe.get("doc"), world["executionContextId"])
        return self._world[1]

    async def hold_ring(self, ident: int | None) -> None:
        """A ring that stays round `ident` while a card waits on it, in the app's own world; None takes it away.
        Only for the person watching: a page that will not take it is left as it is."""
        try:
            world = await self._ring_world()
            if ident is None:
                await self.cdp("Runtime.evaluate", expression=UNRING_JS, contextId=world)
                return
            session, local = self._node(ident)
            while session:
                # In a frame from another site: the ring goes round the frame that holds it, in the page.
                session, local = next(f["owner"] for f in self.frames.values() if f["session"] == session)
            obj = (await self.cdp("DOM.resolveNode", backendNodeId=local, executionContextId=world))["object"]
            await self.cdp("Runtime.callFunctionOn", objectId=obj["objectId"], functionDeclaration=HOLD_JS)
            await self.cdp("Runtime.releaseObject", objectId=obj["objectId"])
        except Exception:
            self._world = None

    async def screenshot(self) -> str:
        """The viewport as a small JPEG, base64, for the card."""
        metrics = await self.cdp("Page.getLayoutMetrics")
        view = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
        wide, high = float(view.get("clientWidth") or 1280), float(view.get("clientHeight") or 800)
        return (await self.cdp("Page.captureScreenshot", format="jpeg", quality=60,
                        clip={"x": float(view.get("pageX") or 0), "y": float(view.get("pageY") or 0),
                              "width": wide, "height": high, "scale": min(1.0, 720 / max(wide, 1))}))["data"]

    # --- actions

    async def _point(self, ident: int, editable_cover: bool = False) -> dict:
        """Playwright's actionability checks before a pointer action (NOTICE): attached, visible, stable over two
        frames, enabled and receiving the events at its point, tried again on its retry ladder (ACTION_WAITS)
        until ACTION_S when one is not yet so. The point, unobstructed, in the top page; else raises why not."""
        began, tries = time.monotonic(), 0
        while True:
            try:
                return await self._point_once(ident, editable_cover)
            except RuntimeError as error:
                if not NOT_YET.search(str(error)) or time.monotonic() - began > ACTION_S:
                    raise
            await asyncio.sleep(ACTION_WAITS[min(tries, len(ACTION_WAITS) - 1)])
            tries += 1

    async def _point_once(self, ident: int, editable_cover: bool = False) -> dict:
        """Scroll into view and return an unobstructed centre point in the top page, or raise why not."""
        session, local = self._node(ident)
        frame = session
        while frame:
            # The frames it sits in come into view first.
            frame, owner = next(f["owner"] for f in self.frames.values() if f["session"] == frame)
            try:
                await self._in(frame, "DOM.scrollIntoViewIfNeeded", backendNodeId=owner)
            except Exception:
                pass
        try:
            await self._in(session, "DOM.scrollIntoViewIfNeeded", backendNodeId=local)
        except Exception:
            pass
        try:
            await self.on_node(ident, STABLE_JS)
            p = await self.on_node(ident, POINT_JS, editable_cover)
        except RuntimeError as error:
            if "outside viewport" not in str(error):
                raise
            # Still out of the window after the browser's own scroll (inside a box that clips it, a row of a
            # carousel): brought to the middle, as a person scrolls to it.
            await self.on_node(ident, CENTER_JS)
            p = await self.on_node(ident, POINT_JS, editable_cover)
        if p["covered"]:
            try:
                await self.on_node(ident, CENTER_JS)
                p = await self.on_node(ident, POINT_JS, editable_cover)
            except Exception:
                pass
        if p["covered"]:
            raise RuntimeError("Target covered by another element; nothing was clicked")
        x, y = await self._offset(session)
        return {**p, "x": p["x"] + x, "y": p["y"] + y}

    async def _click_at(self, x: float, y: float, modifiers: int = 0, button: str = "left", count: int = 1) -> None:
        if not all(math.isfinite(v) for v in (x, y)):
            raise ValueError("Coordinates must be finite.")
        # A double click is two presses, the second counted as such (Playwright's clickCount).
        for n in range(1, count + 1):
            await self.cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button=button, clickCount=n,
                           modifiers=modifiers)
            await self.cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button=button, clickCount=n,
                           modifiers=modifiers)

    async def _act(self, op: str, ident: int | None, body) -> str:
        """Runs one action and returns one line: navigated to <url> / page changed / no change. An action in a
        frame from another site counts what changed in that frame too."""
        session = self._node(ident)[0] if ident is not None else ""
        try:
            before = await self.probe()
        except Exception:
            before = None
        try:
            inner = await self.probe(session) if session else None
        except Exception:
            inner = None
        what = self.describe(ident) if ident is not None else ""
        try:
            detail = await body()
        except Refused:
            raise
        except Exception as e:
            return well_formed(f"[failed] {op} {what}: {e}")
        began = now()
        moved = await self.settle(since=began, session=session) if session else {}
        settled = await self.settle(since=began)
        change = await self.change(before, settled.get('probe'))
        if change == "no change" and inner and (moved.get("probe") or {}).get("n", inner["n"]) != inner["n"]:
            change = "page changed"
        return well_formed(f"[ok] {op} {what}{detail} -> {change}")

    async def change(self, before: dict | None, after: dict | None = None) -> str:
        if after is None:
            try:
                after = await self.probe()
            except Exception:
                after = None
        if not before or not after:
            return "page changed"
        if after["doc"] != before["doc"]:
            return f"navigated to {after['url']}"
        if after["url"] != before["url"]:
            # The same document at another address (a fragment, the page's own routing): its refs still hold.
            return f"page changed; the address is now {after['url']}"
        return "page changed" if after["n"] != before["n"] else "no change"

    async def click(self, ident: int, how: str = "") -> str:
        """A click; `how` "double" for a double click, "right" for the right button (the page's own menu)."""
        async def body() -> str:
            try:
                p = await self._point(ident)
            except Exception:
                if await self.alive(ident):
                    raise
                fresh = await self.again(ident, press=not how)
                if fresh is None:
                    raise
                if not how:
                    # Drawn anew between the look and now: found and pressed in one step.
                    return " (drawn anew since the look: pressed where it is now)"
                p = await self._point(fresh)
            # Ctrl (2) adds an option to a list where several can be chosen, as a person does.
            await self._click_at(p["x"], p["y"], 2 if p["multi"] and not how else 0, "right" if how == "right" else "left",
                                 2 if how == "double" else 1)
            return ""
        return await self._act(f"{how} click".strip(), ident, body)

    async def click_point(self, x: float, y: float) -> str:
        """A click on a point of the page as shown (a canvas has no parts to name)."""
        async def body() -> str:
            await self._click_at(x, y)
            return f" at {round(x)},{round(y)}"
        return await self._act("click", None, body)

    async def type(self, ident: int, text: str, enter: bool = False, append: bool = False) -> str:
        """Replace the field's text with `text` (with `append`, add it at the end of what the field holds): a list's
        option (several, commas between, where several can be chosen), a date, time, colour or range field's value,
        else typed keys; never into a secret field (Refused)."""
        if await self.secret(ident):
            raise Refused(f"[refused] {self.describe(ident)} is a secret field (password, payment or code): "
                          f"it is for the person to fill")

        async def body() -> str:
            kind = await self.on_node(ident, KIND_JS)
            if kind["tag"] == "SELECT":
                return f" = {quote(await self.on_node(ident, SELECT_JS, text))}"
            form = SET_BY_VALUE.get(kind["type"]) if kind["tag"] == "INPUT" else None
            if form:
                # A date, time, colour or range field: its value set straight, as Playwright fills it.
                if kind["type"] == "range":
                    form = f"a number from {kind['min'] or 0} to {kind['max'] or 100}"
                want = as_value(kind, text)
                got = await self.on_node(ident, SET_JS, want)
                if got is None:
                    raise RuntimeError(f"it does not take {quote(clip(text, 40))}; type it as {form}")
                return (f" = {quote(got)}" + (f" (from {quote(clip(text, 40))})" if want != norm(text) else "")
                        + (f" (it takes {form})" if got != want else ""))
            p = await self._point(ident, True)
            await self._click_at(p["x"], p["y"])
            try:
                moved = bool(await self.on_node(ident, MOVED_JS))
            except Exception:
                moved = False
            if moved:
                raise Refused(f"[refused] typing into {self.describe(ident)} moved to another field; look again")
            try:
                await self.focus(ident)
            except Exception:
                pass
            if append:
                await self.on_node(ident, END_JS)
            else:
                a = {"key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65, "modifiers": 2}
                await self.cdp("Input.dispatchKeyEvent", type="rawKeyDown", **a, commands=["selectAll"])
                await self.cdp("Input.dispatchKeyEvent", type="keyUp", **a)
            if text == "" and not append:
                await self.key("Backspace")
            else:
                *chars, last = text
                if not last.isprintable():
                    # A line break sent as the last real key is Enter: it would submit or send past the gate.
                    chars, last = list(text), ""
                if chars:
                    await self.cdp("Input.insertText", text="".join(chars))
                if last:
                    await self.cdp("Input.dispatchKeyEvent", type="keyDown", key=last, text=last)
                    await self.cdp("Input.dispatchKeyEvent", type="keyUp", key=last)
            try:
                actual = await self.value(ident)
            except Exception:
                actual = None
            if enter:
                await self.key("Enter")
            return (f" {'+=' if append else '='} {quote(clip(text, 60))}"
                    + (f" (field now shows {quote(clip(actual, 60) if not append or len(actual) <= 60 else '…' + actual[-60:])})"
                       if actual is not None and actual != text else "")
                    + (" — it did not take the text: a field like this is set by its picker or list; click it"
                       if actual is not None and text.strip() and fold(text).strip() not in fold(actual) else "")
                    + (" +Enter" if enter else ""))
        return await self._act("type", ident, body)

    async def key(self, key: str, modifiers: int = 0) -> None:
        """One key (a name of KEYS, a letter, digit or sign) with the modifier mask held, as Playwright presses it:
        the character it types goes with it only when no Control, Alt or Meta is held."""
        if key in KEYS:
            code, name = KEYS[key], "Space" if key == " " else key
        elif key in SIGNS:
            code, name = SIGNS[key], ""
        else:
            code, name = ord(key.upper()), ("Key" if key.isalpha() else "Digit") + key.upper()
        text = "" if modifiers & 7 else "\r" if key == "Enter" else (key.upper() if modifiers & 8 else key) \
            if len(key) == 1 else ""
        k = {"key": text if text and key != "Enter" else key, "code": name, "windowsVirtualKeyCode": code,
             "nativeVirtualKeyCode": code, "modifiers": modifiers}
        await self.cdp("Input.dispatchKeyEvent", type="rawKeyDown", **k)
        if text:
            await self.cdp("Input.dispatchKeyEvent", type="char", **k, text=text, unmodifiedText=text)
        await self.cdp("Input.dispatchKeyEvent", type="keyUp", **k)

    async def press(self, keys: str) -> str:
        """A key or a chord ("Control+Enter"), as chord() reads it."""
        spelled, modifiers, key = chord(keys)

        async def body() -> str:
            await self.key(key, modifiers)
            return f" {spelled}"
        return await self._act("press", None, body)

    async def scroll(self, down: bool = True, ident: int | None = None, until: str = "") -> str:
        """A wheel over the box `ident` (or the page's middle), as a person scrolls; with `until`, on wheel by wheel
        until those words are on the screen, waiting at the end of a list for more to load."""
        want = fold(until).strip()

        async def where() -> dict:
            if ident is None:
                return await self._evaluate("() => {const el = document.scrollingElement || document.documentElement;"
                                            " return {top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight};}")
            return await self.on_node(ident, SCROLLER_JS)

        async def body() -> str:
            view = (await self.cdp("Page.getLayoutMetrics")).get("cssVisualViewport") or {}
            wide, high = float(view.get("clientWidth") or 800), float(view.get("clientHeight") or 600)
            if ident is not None:
                b = await self.box(ident)
                # The middle of what of the box is on screen.
                x = min(max(b["x"], 0) + min(b["w"], wide) / 2, wide - 2)
                top, bottom = max(b["y"], 0), min(b["y"] + b["h"], high)
                y = (top + bottom) / 2 if bottom > top else high / 2
                step = max(80.0, min(b["h"], high) * 0.8)
            else:
                x, y, step = wide / 2, high / 2, high * 0.8
            wheels = 0
            for wheels in range(1, (UNTIL_WHEELS if want else 1) + 1):
                if want and want in await self.screen_text():
                    return f" until {quote(clip(until, 40))}: it shows after {wheels - 1} wheels"
                before = await where()
                await self.cdp("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0,
                               deltaY=step * (1 if down else -1))
                await self.settle(cap_ms=1500)
                after = await where()
                if after["top"] == before["top"] and after["height"] == before["height"]:
                    # At the end: a feed may still be loading more.
                    began = time.monotonic()
                    while time.monotonic() - began < MORE_S and (await where())["height"] == before["height"]:
                        await asyncio.sleep(0.3)
                    if (await where())["height"] == before["height"]:
                        if want:
                            return (f" until {quote(clip(until, 40))}: not found, the list ended after {wheels} "
                                    f"wheels")
                        return " — it did not move: the end, or not the box that scrolls"
            if want:
                if want in await self.screen_text():
                    return f" until {quote(clip(until, 40))}: it shows after {wheels} wheels"
                return f" until {quote(clip(until, 40))}: not found in {wheels} wheels"
            return " down" if down else " up"
        return await self._act("scroll", ident, body)

    async def hover(self, ident: int) -> str:
        """The pointer over the element and left there: a menu that opens on hover opens."""
        async def body() -> str:
            p = await self._point(ident)
            await self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"], y=p["y"])
            return ""
        return await self._act("hover", ident, body)

    async def wait(self, until: str = "", gone: str = "") -> str:
        """A few seconds for a page that is loading or answering; with `until` (or `gone`), up to WAIT_S until those
        words show on the page (or no longer do), as Playwright MCP's wait_for."""
        words = until or gone
        want = fold(words).strip()

        async def body() -> str:
            if not want:
                await asyncio.sleep(1.0)
                await self.settle(cap_ms=6000, quiet_ms=800)
                return ""
            began = time.monotonic()
            while (want in await self.visible_text()) != bool(until):
                if time.monotonic() - began > WAIT_S:
                    return f" until {quote(clip(words, 40))} {'shows' if until else 'is gone'}: still not after {WAIT_S:.0f} s"
                await asyncio.sleep(0.3)
            return (f" until {quote(clip(words, 40))} {'shows' if until else 'is gone'}: "
                    f"{'it shows' if until else 'gone'} after {time.monotonic() - began:.1f} s")
        return await self._act("wait", None, body)

    async def drag(self, ident: int, to: int) -> str:
        """The pointer pressed on `ident`, moved onto `to` and let go, as Playwright drags: a drag the page starts
        with the browser's own drag and drop is caught (Input.setInterceptDrags) and dropped there, any other
        follows the mouse. It lets go a quarter past the middle of `to`, so in a list it takes `to`'s place; the
        line says where it now sits."""
        if not self._drag_heard:
            await self.tab.events("Input.dragIntercepted", lambda params: setattr(self, "_dragged", params["data"]))
            self._drag_heard = True

        async def body() -> str:
            # Both ends in view before the button goes down.
            await self._point(ident)
            b = await self._point(to)
            a = await self._point(ident)
            box = await self.box(to)
            try:
                was = await self.on_node(ident, PLACE_JS)
            except Exception:
                was = None
            await self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=a["x"], y=a["y"])
            await self.cdp("Input.dispatchMouseEvent", type="mousePressed", x=a["x"], y=a["y"], button="left",
                           clickCount=1)
            # Past the middle along the way it goes: above it when going up, left of it when going left.
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            end = ({"x": b["x"], "y": b["y"] + math.copysign(box["h"] / 4, dy)} if abs(dy) >= abs(dx) else
                   {"x": b["x"] + math.copysign(box["w"] / 4, dx), "y": b["y"]})
            self._dragged = None
            await self.cdp("Input.setInterceptDrags", enabled=True)
            try:
                first = {"x": a["x"] + (end["x"] - a["x"]) / DRAG_STEPS, "y": a["y"] + (end["y"] - a["y"]) / DRAG_STEPS}
                await self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=first["x"], y=first["y"], button="left",
                               buttons=1)
                await asyncio.sleep(0.2)
            finally:
                await self.cdp("Input.setInterceptDrags", enabled=False)
            data, self._dragged = self._dragged, None
            if data is not None:
                await self.cdp("Input.dispatchDragEvent", type="dragEnter", x=first["x"], y=first["y"], data=data)
            for n in range(2, DRAG_STEPS + 1):
                x, y = a["x"] + (end["x"] - a["x"]) * n / DRAG_STEPS, a["y"] + (end["y"] - a["y"]) * n / DRAG_STEPS
                if data is not None:
                    await self.cdp("Input.dispatchDragEvent", type="dragOver", x=x, y=y, data=data)
                else:
                    await self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="left", buttons=1)
                    await asyncio.sleep(0.03)
            if data is not None:
                await self.cdp("Input.dispatchDragEvent", type="drop", x=end["x"], y=end["y"], data=data)
            else:
                await self.cdp("Input.dispatchMouseEvent", type="mouseReleased", x=end["x"], y=end["y"], button="left",
                               clickCount=1)
            await asyncio.sleep(0.2)
            try:
                now = await self.on_node(ident, PLACE_JS)
            except Exception:
                now = None
            where = ("" if now is None or was is None else "; it did not move" if now == was else
                     f"; it now comes after {quote(now['before'])}" if now["before"] else
                     f"; it now comes first, before {quote(now['after'])}")
            return f" onto {self.describe(to)}{where}"
        return await self._act("drag", ident, body)

    async def goto(self, url: str) -> str:
        async def body() -> str:
            await self.cdp("Page.navigate", url=url)
            return f" {url}"
        return await self._act("navigate", None, body)

    async def back(self) -> str:
        async def body() -> str:
            history = await self.cdp("Page.getNavigationHistory")
            index = history.get("currentIndex", 0)
            if index <= 0:
                raise RuntimeError("no page before this one")
            await self.cdp("Page.navigateToHistoryEntry", entryId=history["entries"][index - 1]["id"])
            return ""
        return await self._act("back", None, body)
