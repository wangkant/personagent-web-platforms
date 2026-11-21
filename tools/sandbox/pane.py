"""The pane suite: the built-in browser in the app's real window, through tools/'s own measuring scripts, each on a
new home with its stand-in model and practice site: a page of tools/opsite opened and a request typed beside it done
in the page (panedock.py), the one compact bar at four window widths (panebar.py), tabs, a sign-in popup and the
private tab (panetabs.py), and the sign-in wait card with its 前往登录 and the character never typing a password
(signincheck.py), a card pressed with the real pointer where the chat lies over the page (panecard.py), and a
profile above an endless feed read for its numbers in one call with nothing scrolled (panereading.py). Their
pictures and results.json are kept under <out>/<script>.

    python tools/sandbox/pane.py --out <folder>

Windows only: the built-in browser is WebView2 inside the app window (README: "Program control and the built-in
browser are Windows-only"); on Linux and macOS the suite says so.
"""
from __future__ import annotations

import json
import sys

from _suite import Suite, run_tool, scratch_home, scrub_env

# feature, script, its arguments after the home, and the stand-in model's port the home's settings name.
SCRIPTS = [
    ("open and operate a page", "panedock.py", ["--site-port", "59409", "--model-port", "59408"], 59408),
    ("compact bar", "panebar.py", ["--site-port", "59751", "--model-port", "59752"], 59752),
    ("tabs", "panetabs.py", ["--site-port", "59647", "--model-port", "59649"], 59649),
    ("sign-in wait card", "signincheck.py", ["--ports", "59740"], 59744),
    ("a card pressed over the page", "panecard.py", ["--site-port", "59411", "--model-port", "59410"], 59410),
    ("a page read in one call", "panereading.py", ["--site-port", "59413", "--model-port", "59412"], 59412),
]


def judge(suite: Suite, feature: str, script: str, r: dict) -> None:
    """The verdicts on a script's numbers (they write numbers, not verdicts)."""
    if script == "panedock.py":
        o, q = r.get("open", {}), r.get("request", {})
        suite.check(feature, "a page of tools/opsite opens in the pane, shown and not cut",
                    o.get("pane") is True and o.get("shown") is True and o.get("cut") is False, o)
        suite.check(feature, "a request typed beside the page is done in the page (the site's own record)",
                    q.get("site_got_it") is True, {k: q.get(k) for k in ("site_says", "reply", "steps", "cards")})
        suite.check(feature, "the page stays in sight while it acts", q.get("page_in_sight_all_the_way") is True)
    elif script == "panebar.py":
        for width, row in r.items():
            if isinstance(row, dict) and "lines" in row:
                suite.check(feature, f"{width}: one row, nothing past its end, the page gets 90 % of the height or more",
                            row["lines"] == 1 and not row["past_end"] and row["page_share"] >= 0.9,
                            {k: row.get(k) for k in ("lines", "past_end", "page_share", "bar")})
    elif script == "panetabs.py":
        link, popup, private, keys = (r.get(k, {}) for k in ("blank_link", "popup_signin", "private", "keys"))
        suite.check(feature, "a link a site opens in a new tab opens there, the first page kept",
                    link.get("b_open") is True and link.get("a_kept") is True, link)
        suite.check(feature, "a sign-in popup hands its result back to its page and closes",
                    popup.get("finished") is True and popup.get("popup_closed") is True
                    and str(popup.get("a_title", "")).startswith("Signed in"), popup)
        suite.check(feature, "a private tab opens and the other tabs keep their pages", private.get("private_now") is True
                    and private.get("a_kept") is True and private.get("field_kept") is True, private)
        suite.check(feature, "Ctrl+T opens a tab, Ctrl+W closes it, a closed tab can be opened again",
                    keys.get("after_ctrl_t") == keys.get("strip_before", -9) + 1
                    and keys.get("after_ctrl_w") == keys.get("strip_before") and keys.get("a_reopened") is True, keys)
        suite.check(feature, "the run's tab is the one in front, paused when another is chosen", r.get("bound", {}).get("ok") is True,
                    r.get("bound"))
    elif script == "panecard.py":
        suite.check(feature, "a card pressed with the real pointer where the chat lies over the page is answered",
                    r.get("answered") is True, {k: r.get(k) for k in ("card", "pressed_at", "cut_under_the_card")})
        suite.check(feature, "that press never pauses the run", r.get("paused") == [], r.get("paused"))
        suite.check(feature, "the site gets the message", r.get("site_got_it") is True)
    elif script == "panereading.py":
        suite.check(feature, "a profile's numbers above an endless feed are read in one reading call",
                    r.get("reading_calls") == 1 and r.get("numbers_in_first_read") is True,
                    {k: r.get(k) for k in ("reading_calls", "numbers_in_first_read", "seconds")})
        suite.check(feature, "nothing on the page scrolls while it is read", r.get("scrolled_px") == 0, r.get("scrolled_px"))
    else:
        b, c, d = r.get("b", {}), r.get("c", {}), r.get("d", {})
        suite.check(feature, "a page behind a sign-in raises the wait card with its sentence",
                    b.get("waited") is True and bool(b.get("wait_line")), b)
        suite.check(feature, "a code page keeps the wait", c.get("still_waiting") is True, c)
        suite.check(feature, "a provider's popup comes to the front and hands the sign-in back",
                    d.get("popup_in_front") is True and d.get("back_on") == "/inbox", d)
        typed = {k: (v.get("status"), v.get("field_after")) for k, v in r.items() if k.startswith("i_")}
        suite.check(feature, "a password, a code or a card number is never typed by the character",
                    bool(typed) and all(v == ("handover", "empty") for v in typed.values()), typed)
        leaks = {where: [k for k in ("pw", "pw2", "prov", "code", "card") if row.get(k)] + (["cookies"] if row.get("cookies") else [])
                 for where, row in r.get("secrets", {}).items() if isinstance(row, dict)}
        suite.check(feature, "no test secret or session cookie reaches the model, the home's files or Settings",
                    bool(leaks) and not any(leaks.values()), leaks)


def main() -> int:
    suite = Suite("pane", __doc__.split("\n\n")[0])
    if sys.platform != "win32":
        for feature, *_ in SCRIPTS:
            suite.not_built(feature, "the built-in browser is Windows-only (WebView2 in the app window)")
        return suite.finish()
    scrub_env()
    for feature, script, extra, model_port in SCRIPTS:
        home = scratch_home(f"http://127.0.0.1:{model_port}/v1", lang="zh")
        out = suite.out / script.removesuffix(".py")
        run_tool(suite, feature, [f"tools/{script}", str(home), "--out", str(out), *extra], script.removesuffix(".py"))
        numbers = out / "results.json"
        judge(suite, feature, script, json.loads(numbers.read_text(encoding="utf-8")) if numbers.is_file() else {})
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
