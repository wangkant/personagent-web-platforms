"""Put a few sample finds into a home's library, so the Feed page and the
pet's card can be looked at without a model or a walk.

These are samples written for this tool, not anything the character found.
Each goes in through the library's own functions, as a walk would keep it.

Usage:
    python tools/feed_seed.py --home <dir> [--lang zh|en]
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from mibo import settingsfile
from mibo.home import looks_like_home
from mibo.library import Item, Library, item_id
from mibo.roam import player_for

HOUR = 3600

# url, site, kind, title, description, text, picture colours (or None), hours ago, fits, words zh, words en
SAMPLES = [
    ("https://www.bilibili.com/video/BV1GJ411x7h7", "Bilibili", "video", "【样例】一段老歌现场",
     "样例视频的简介：一首老歌的现场版。", "", ((236, 112, 99), (245, 176, 65)), 1, True,
     "这首我循环了三遍，你应该也会喜欢。", "I played this one three times. I think you'll like it too."),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "YouTube", "video", "Me at the zoo",
     "A sample video: the first one ever put on the site, eighteen seconds at the elephants.", "",
     ((88, 160, 220), (120, 210, 180)), 5, True,
     "网站上第一个视频，才十八秒，看大象。", "The very first video on the site. Eighteen seconds of elephants."),
    ("https://en.wikipedia.org/wiki/Tardigrade", "Wikipedia", "article", "Tardigrade",
     "A sample page about the tiny animals that survive almost anything.",
     "Tardigrades are animals about half a millimetre long that live in moss, sand and sea water.\n\n"
     "When their home dries out they pull in their legs, lose nearly all their water and wait, "
     "sometimes for years, until water comes back.\n\n"
     "In that state they have lived through deep cold, strong heat and even a trip outside a spacecraft. "
     "(Sample text written for mibo's feed seed.)",
     ((150, 120, 200), (230, 180, 220)), 9, True,
     "它们能干到几乎没有水，再等好几年醒过来。", "They dry out almost completely and wake up years later."),
    ("https://zh.wikipedia.org/wiki/酸种", "维基百科", "article", "酸种面包",
     "样例页面：用野生酵母做面包。",
     "酸种是面粉和水放在一起，让空气里的野生酵母和乳酸菌慢慢长起来的面种。\n\n"
     "每天喂一次新的面粉和水，过一周左右它就会冒泡、变酸，可以用来发面。\n\n"
     "做出来的面包有一点酸味，外皮很脆。（这是 mibo 动态样例写的文字。）",
     None, 20, False,
     "你上次说想学做面包，这个看起来不难。", "You said you wanted to bake bread. This one looks doable."),
    ("https://news.ycombinator.com/item?id=1", "Hacker News", "post", "A sample post: what are you building?",
     "A sample thread where people say what they are working on this week.", "", None, 30, False,
     "大家都在做些小东西，挺有意思。", "Everyone is making small things. Fun to read."),
    ("https://en.wikipedia.org/wiki/Lighthouse", "Wikipedia", "article", "Lighthouse",
     "A sample page about towers that light the way for ships.",
     "A lighthouse is a tower with a strong lamp at the top that helps ships find their way at night.\n\n"
     "Each one flashes in its own pattern, so a sailor can tell which coast they are near.\n\n"
     "Most now run on their own, with no keeper living in them. (Sample text written for mibo's feed seed.)",
     ((40, 60, 110), (240, 200, 90)), 50, True,
     "每座灯塔闪的节奏都不一样，像在打招呼。", "Every lighthouse blinks its own rhythm, like a hello."),
]


def runtime_of(home: Path) -> Path:
    # Where the service keeps its state, read as the page reads it.
    configured = settingsfile.values(home, keys=False).get("AGENT_RUNTIME_DIR") or "runtime"
    runtime = Path(configured.strip() or "runtime").expanduser()
    return runtime if runtime.is_absolute() else home / runtime


def cover(top: tuple, bottom: tuple) -> bytes:
    """A small made-up cover: a gradient and a sun, as a JPEG."""
    width, height = 480, 270
    picture = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(picture)
    for y in range(height):
        t = y / (height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    draw.ellipse((width - 150, 40, width - 70, 120), fill=(255, 250, 235))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=80)
    return out.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Put sample finds into a home's library.")
    parser.add_argument("--home", required=True, help="the home folder (with settings.json and persona.txt)")
    parser.add_argument("--lang", choices=("zh", "en"), default="en", help="the language of the character's words")
    args = parser.parse_args()
    home = Path(args.home).expanduser().resolve()
    if not looks_like_home(home):
        print(f"feed_seed: {home} is not a mibo home (no settings.json and persona.txt); nothing written.")
        return 2
    library = Library(runtime_of(home))
    now = int(time.time())
    for (url, site, kind, title, description, text, colours, hours, fits, zh, en) in SAMPLES:
        item = Item(url=url, site=site, kind=kind, title=title, description=description, text=text,
                    embed=player_for(url), seen_at=now - hours * HOUR, outing="sample", fits=fits,
                    why=zh if args.lang == "zh" else en, id=item_id(url))
        if colours:
            item.picture = library.save_picture(item.id, cover(*colours))
        library.add(item)
    print(f"feed_seed: put {len(SAMPLES)} SAMPLE finds into {library.db.path}.")
    print("These are samples written for testing the Feed page, not anything the character found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
