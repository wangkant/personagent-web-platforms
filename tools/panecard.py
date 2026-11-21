"""A card pressed with the real pointer where the chat lies over the page (a narrow window), in the real app window:
the press answers the card and never pauses the run (browser.on_page), and the site gets the message. For the
sandbox runner only (tools/sandbox/pane.py): it moves the machine's own pointer, which a desk in use must not.

    python tools/panecard.py <scratch home> --out <folder> [--site-port 59411] [--model-port 59410]

The home's settings must name the stand-in model started here (LLM_BASE_URL=http://127.0.0.1:<model-port>/v1).
Writes results.json ({"card": what the card said, "pressed_at", "paused": the holds seen while the card was up,
"answered", "site_got_it"}) and exits 0 when the card was answered by the press, nothing paused the run and the
site got the message, else 1."""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import panedock  # noqa: E402

CARD = ".browse-run-card .op-card"
YES = CARD + " .pa-btn-primary"


def press_for_real(d, selector: str) -> list:
    """The pointer moved to the element's centre on the screen and its left button pressed and let go there, as
    the person's hand does (SendInput's mouse_event): the input Windows itself records."""
    at = d.centre(selector)
    if not at:
        return []
    point: list = []

    def where() -> None:
        from System.Drawing import Point
        form = d.app._window.native
        origin = form.browser.webview.Location
        screen = form.PointToScreen(Point(origin.X + round(at[0] * form._scale), origin.Y + round(at[1] * form._scale)))
        point.extend([screen.X, screen.Y])
    d._ui(where)
    user32 = ctypes.windll.user32
    user32.SetCursorPos(*point)
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    return point


def walk(d, site: str, results: dict) -> None:
    pane = d.app._browser
    time.sleep(3)
    d.app.open_link(site + "/messages.html")
    d.until("document.querySelector('.browse') && !document.querySelector('.browse').hidden", 20)
    time.sleep(4)

    def narrow() -> None:
        from System.Drawing import Size
        form = d.app._window.native
        form.Size = Size(round(760 * form._scale), form.Height)
    d._ui(narrow)
    time.sleep(2)
    # The chat brought over the page, as the person does to type in a narrow window.
    if not panedock.look(d, "narrow").get("cut"):
        d.click(".browse-beside")
        time.sleep(1.5)
    panedock.get(site + "/__reset", {})
    d.type_into("#chat-view .chat:not([hidden]) .chat-input", panedock.ASK)
    d.key("Enter", 13)
    began, paused = time.time(), []
    while time.time() - began < 90:
        held = (pane.op or {}).get("held") or ""
        if held:
            paused.append(held)
        text = d.js(f"(() => {{ const c = document.querySelector({json.dumps(CARD)}); return c ? c.innerText : ''; }})()")
        if text and "card" not in results:
            results["card"] = " / ".join(str(text).split())
            results["cut_under_the_card"] = bool(panedock.window_facts(d).get("cut"))
            d.shot("card")
            results["pressed_at"] = press_for_real(d, YES)
            time.sleep(1.0)
            held = (pane.op or {}).get("held") or ""
            if held:
                paused.append(held)
            results["answered"] = not d.js(f"!!document.querySelector({json.dumps(CARD)})")
        if not d.js("!!document.querySelector('.line.typing')"):
            break
        time.sleep(0.3)
    time.sleep(1.5)
    results["paused"] = sorted(set(paused))
    results["site_got_it"] = bool(panedock.get(site + "/__check?task=R1").get("ok"))
    d.shot("after")
    d.check("a card pressed with the real pointer over the page is answered", results.get("answered") is True)
    d.check("the press on the chat laid over the page never pauses the run", results["paused"] == [])
    d.check("the site got the message", results["site_got_it"])


def main() -> int:
    args = sys.argv[1:]
    home, out = Path(args[0]), Path(args[args.index("--out") + 1])
    site_port = int(args[args.index("--site-port") + 1]) if "--site-port" in args else 59411
    model_port = int(args[args.index("--model-port") + 1]) if "--model-port" in args else 59410
    out.mkdir(parents=True, exist_ok=True)
    import window_driver

    from mibo import window as appwindow
    from mibo.welcome import save_app_settings
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    made = appwindow.AppWindow.__init__

    def no_figure(self, *a, **k):
        made(self, *a, **k)
        self.pet = False
    appwindow.AppWindow.__init__ = no_figure
    window_driver.Model = panedock.StandIn
    window_driver.ThreadingHTTPServer = lambda address, handler: ThreadingHTTPServer((address[0], model_port), handler)
    site = f"http://127.0.0.1:{site_port}"
    server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("opsite") / "serve.py"), str(site_port),
                               str(out / "site.log")])
    results: dict = {}
    try:
        # On screen: the press goes where the card is drawn.
        failed = window_driver.run(home, lambda d: walk(d, site, results), out=out, offscreen=False, lang="zh")
    finally:
        server.terminate()
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
