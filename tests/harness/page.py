"""The interface's own scripts in a headless browser: one Chromium-family browser a test file (the one installed
on this computer, found as the app finds it, else Playwright's own Chromium), a fresh context per page, and the
app's page opened with its token. Headless draws nothing on screen and moves no pointer; the pointer and keys
Playwright sends are events inside the page.

A page records what a person would see go wrong: script errors, requests that reached past 127.0.0.1 (refused),
and every word the scripts took from the catalog (i18n.js), so a test can name the words a language lacks. The
pet's page gets a stand-in for the window's bridge (window.pywebview.api) that records each call."""
from __future__ import annotations

import json
import re
from pathlib import Path

from harness.app import TOKEN
from harness.model import MODEL
from mibo import abilities, i18n, installed_browser
from mibo.endpoints import chat_completions_url
from mibo.storage import atomic_write_text

# Anything but the app's own address is refused before it leaves the browser.
FOREIGN = re.compile(r"^(?!https?://(127\.0\.0\.1|localhost)[:/])[a-z]+://")
# The plural forms a catalog key may carry (i18next's, as Intl.PluralRules names them).
PLURALS = ("zero", "one", "two", "few", "many", "other")
# What every page gets before its own scripts run: the catalog's words as the scripts take them, the words no
# catalog had, and fetches still out (for settle).
WATCH = """
(() => {
  const taken = (window.__taken = []);
  const lost = (window.__lost = []);
  const absent = (window.__absent = []);
  let out = 0;
  window.__out = () => out;
  const fetched = window.fetch;
  window.fetch = (...args) => {
    out += 1;
    return fetched(...args).finally(() => (out -= 1));
  };
  // A namespace's words as a script reads them: each string it took, and each name it asked for that is not there.
  const watch = (tree, path) =>
    new Proxy(tree, {
      get(target, prop, receiver) {
        const value = Reflect.get(target, prop, receiver);
        if (typeof prop !== "string") return value;
        if (typeof value === "string") taken.push(`${path}.${prop}`);
        else if (value === undefined && prop) absent.push(`${path}.${prop}`);
        else if (value && typeof value === "object" && !Array.isArray(value)) return watch(value, `${path}.${prop}`);
        return value;
      },
    });
  let i18n;
  Object.defineProperty(window, "paI18n", {
    configurable: true,
    get: () => i18n,
    set(value) {
      const { t, words } = value;
      // A word no catalog has comes back as its key.
      value.t = (key, values) => {
        const said = t(key, values);
        taken.push(key);
        if (said === key) lost.push(key);
        return said;
      };
      value.words = (ns) => watch(words(ns), ns);
      i18n = value;
    },
  });
})();
"""
# The pet window's bridge (window.py's PetApi) as a stand-in: every call recorded, each answered as the window
# would with nothing to report; the figure is Mibo.
BRIDGE = """
(() => {
  const calls = (window.__bridge = []);
  const answers = { figure: "mibo", words: {}, card: "right" };
  window.pywebview = {
    api: new Proxy({}, {
      get: (target, name) => (...args) => {
        calls.push([name, ...args]);
        return Promise.resolve(answers[name] ?? null);
      },
    }),
  };
  addEventListener("DOMContentLoaded", () => setTimeout(() => dispatchEvent(new Event("pywebviewready"))));
})();
"""


# Text and labels on the page that say "undefined": a word looked up that was not there.
SHOWN_UNDEFINED = """
(() => {
  const found = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) if (/\bundefined\b/.test(walk.currentNode.data)) found.push(walk.currentNode.data.trim());
  for (const node of document.querySelectorAll("[aria-label], [title], [placeholder]"))
    for (const name of ["aria-label", "title", "placeholder"])
      if (/\bundefined\b/.test(node.getAttribute(name) || "")) found.push(`${name}: ${node.getAttribute(name)}`);
  return found;
})()
"""


class NoBrowser(RuntimeError):
    """No Chromium-family browser is installed here."""


def _executable(playwright) -> str:
    found = installed_browser.find_browser()
    if found:
        return str(found)
    own = Path(playwright.chromium.executable_path)
    if own.is_file():
        return str(own)
    raise NoBrowser("no Edge, Chrome or Chromium here, and Playwright's own Chromium is not installed")


class Browser:
    """`Browser()` starts the browser; `open(app, path)` gives a Page on the app; `close()` ends it all."""

    def __init__(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(executable_path=_executable(self._playwright),
                                                             headless=True)
        except BaseException:
            self._playwright.stop()
            raise
        self.pages: list[Page] = []

    def open(self, app, path: str = "/", *, lang: str = "", bridge: bool = False, width: int = 1200,
             height: int = 820, settle: bool = True) -> Page:
        """`lang`: the page in that language whatever the home's, with /locale.js as the app serves it for one
        (welcome.py), so pages in several languages can be open at once. `settle`: wait until the page has drawn
        what it fetched; without it, only until its document has come."""
        context = self._browser.new_context(viewport={"width": width, "height": height}, service_workers="block")
        page = Page(context, app, bridge)
        if lang:
            script = f"paI18n.load(...{json.dumps([lang, i18n.catalog(lang), i18n.catalog('en')], ensure_ascii=False)});"
            context.route(f"{app.url}/locale.js", lambda route: route.fulfill(
                body=script, content_type="text/javascript; charset=utf-8"))
        page.goto(path, settle)
        self.pages.append(page)
        return page

    def close_pages(self) -> None:
        for page in self.pages:
            page.context.close()
        self.pages.clear()

    def close(self) -> None:
        self.close_pages()
        self._browser.close()
        self._playwright.stop()


class Page:
    """One page of the app. `page` is Playwright's; `errors` the script errors; `refused` the foreign requests."""

    def __init__(self, context, app, bridge: bool) -> None:
        self.context, self.app = context, app
        self.errors: list[str] = []
        self.refused: list[str] = []
        context.add_init_script(WATCH + (BRIDGE if bridge else ""))
        context.route(FOREIGN, self._refuse)
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def _refuse(self, route) -> None:
        self.refused.append(route.request.url)
        route.abort()

    def goto(self, path: str = "/", settle: bool = True) -> None:
        """The page at `path`, its token in the fragment as the app opens it."""
        self.page.goto(f"{self.app.url}{path}#token={TOKEN}", wait_until="load" if settle else "commit")
        if settle:
            self.settle()

    def reload(self) -> None:
        self.page.reload()
        self.settle()

    def settle(self, quiet_ms: int = 150, timeout_ms: int = 15000) -> None:
        """Wait until no fetch has been out for `quiet_ms`: what the page asked for has come and been drawn."""
        self.page.wait_for_function(
            """(quiet) => {
              const now = performance.now();
              if (window.__out() > 0) window.__busy = now;
              return now - (window.__busy ?? 0) >= quiet;
            }""", arg=quiet_ms, polling=25, timeout=timeout_ms)

    def calls(self) -> list[list]:
        """The calls the pet's page made on its window's bridge, in order: [name, *arguments]."""
        return self.page.evaluate("window.__bridge")

    def missing_words(self, lang: str) -> list[str]:
        """What the page shows in `lang` that is not that language's own: each catalog key the scripts took that
        mibo/locales/<lang>.json lacks (English stood in), each key no catalog had, and each text on the page that
        reads "undefined"."""
        catalog = i18n.catalog(lang)
        taken = dict.fromkeys(self.page.evaluate("window.__taken"))
        lacking = [key for key in taken if not _has(catalog, key)]
        # A name asked for in a list of words (setup.names) that English lacks too is a word no catalog has. A
        # name asked for beside other parts (a namespace's own errors, laid over the shared ones) may be optional.
        english = i18n.catalog("en")
        nowhere = [key for key in dict.fromkeys(self.page.evaluate("window.__absent"))
                   if _words_only(english, key) and not _has(english, key)]
        return lacking + nowhere + self.page.evaluate("window.__lost") + self.page.evaluate(SHOWN_UNDEFINED)


def measured(app, **found) -> None:
    """The scripted model as a finished ability check keeps it (abilities.check): a page that shows the model's
    control then asks the model nothing on its own. `found` overrides what the check found."""
    url = chat_completions_url(app.model.url)
    record = {"url": url, "model": MODEL, "at": 0, "tools": "yes", "pictures": "no", "json": "yes", "thinking": "no",
              "context": 0, "quirks": [], **found}
    atomic_write_text(app.home / "runtime" / abilities.FILE,
                      json.dumps({"models": {abilities.key_of(url, MODEL): record}}))


def _node(catalog: dict, path: list[str]):
    for part in path:
        catalog = catalog.get(part) if isinstance(catalog, dict) else None
    return catalog


def _words_only(catalog: dict, key: str) -> bool:
    """Whether the part of the catalog a key names a name in holds words alone, no further parts."""
    node = _node(catalog, key.split(".")[:-1])
    return isinstance(node, dict) and all(isinstance(value, str) for value in node.values())


def _has(catalog: dict, key: str) -> bool:
    """A key, or one of its plural forms, in a catalog. A plural form taken by name (key_one, read when a script
    spreads a namespace) counts as there when the language has any form of it: languages have different forms."""
    *path, last = key.split(".")
    node = _node(catalog, path)
    stem = re.sub(f"_({'|'.join(PLURALS)})$", "", last)
    return isinstance(node, dict) and any(name in node for name in (last, *(f"{stem}_{form}" for form in PLURALS)))
