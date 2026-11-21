"""The shots suite: the main window as a person first sees it after a few words, for the README. The app's real
window on a scratch home with a stand-in model and the sample finds of tools/feed_seed.py (no sources round runs):
one message, the character's answer, and a find brought into the chat. Each picture is the window's own part of the
screen.

    python tools/sandbox/shots.py --out <folder>

The README's other pictures come from the pet and pane suites of the same run (pet/05-card.png,
pane/panedock/03-request-card.png). On Linux and macOS the picture is the whole screen.
"""
from __future__ import annotations

import subprocess
import sys
import time

import package_smoke
from _suite import REPO, SYSTEM, Suite, Window, api, scratch_home, scrub_env, stand_in_model

ASK = "今晚有什么值得看的吗？"  # copy: voice
package_smoke.REPLY = "给你留了一个，觉得你会喜欢。"  # copy: voice


def window_box(w: Window):
    """The main window's rectangle on the screen (Windows), else None for the whole screen."""
    if SYSTEM != "windows":
        return None
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(w.app._main_hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def shot(suite: Suite, name: str, box) -> None:
    from PIL import ImageGrab

    try:
        picture = ImageGrab.grab(all_screens=SYSTEM == "windows")
    except OSError as error:
        suite.log("no picture:", name, repr(error)[:200])
        return
    (picture.crop(box) if box else picture).save(suite.out / f"{name}.png")


def walk(w: Window, suite: Suite) -> None:
    home = w.home
    api(home, "character", {"key": "mibo"})
    seeded = subprocess.run([sys.executable, str(REPO / "tools" / "feed_seed.py"), "--home", str(home), "--lang", "zh"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    suite.check("main window", "the sample finds are in the library", seeded.returncode == 0, seeded.stdout[-300:])
    w.until("!!document.querySelector('#chat-view .chat-input')", 30)
    w.js("((e) => { e.value = %r; e.dispatchEvent(new Event('input', {bubbles: true})); return true; })"
         "(document.querySelector('#chat-view .chat-input'))" % ASK)
    w.js("document.querySelector('#chat-view .chat-composer').requestSubmit(); true")
    said = w.until("document.querySelector('#chat-view .chat-messages').innerText.includes(%r)" % package_smoke.REPLY, 60)
    suite.check("main window", "the message is answered", said is True)
    # The chooser picks in the background on the first ask and hands the choice over on the next.
    brought = 0
    for _ in range(10):
        brought = api(home, "compose/recommend", {}).get("count", 0)
        if brought:
            break
        time.sleep(2)
    suite.check("main window", "a find is brought into the chat", brought == 1)
    shown = w.until("!!document.querySelector('#chat-view .has-find')", 30)
    suite.check("main window", "its card is drawn under the answer", shown is True)
    time.sleep(2)
    shot(suite, "main-window", window_box(w))


def main() -> int:
    suite = Suite("shots", __doc__.split("\n\n")[0])
    scrub_env()
    home = scratch_home(stand_in_model(), lang="zh", LLM_MODEL="stand-in-model")
    from mibo.sources import catalog
    from mibo.welcome import save_app_settings

    async def no_rounds(self) -> None:
        return None

    # Only the sample finds: no round reads public sources.
    catalog.Rounds.loop = no_rounds
    desktop = home / "runtime" / "desktop"
    desktop.mkdir(parents=True)
    save_app_settings(desktop, toured=True, pet_shown=False)
    Window(suite, home).run(lambda w: walk(w, suite))
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
