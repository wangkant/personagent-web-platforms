"""Where a control is in its window and, for one without a name, what it looks like: the words a look prints so a
model that reads no pictures can still tell "the black round one at the top" from the rest.

Pure functions over boxes (left, top, width, height in screen pixels) and a Pillow picture of the window, so the
same words come out on every system and in tests.
"""
from __future__ import annotations

# Colour words by hue, from red round the wheel.
HUES = ((15, "red"), (40, "orange"), (65, "yellow"), (160, "green"), (250, "blue"), (290, "purple"), (335, "pink"),
        (360, "red"))
# Two colours this far apart (0 to 441 over RGB) are told apart.
APART = 60


def place(box: tuple, frame: tuple) -> str:
    """Where `box` sits in the window `frame` (left, top, right, bottom): "top left", "middle", "bottom right"..."""
    x, y, wide, high = box
    left, top, right, bottom = frame
    cx = (x + wide / 2 - left) / max(right - left, 1)
    cy = (y + high / 2 - top) / max(bottom - top, 1)
    row = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else ""
    col = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else ""
    return " ".join(part for part in (row, col) if part) or "middle"


def overlap(a: tuple, b: tuple) -> int:
    """The area two boxes share."""
    wide = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    high = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(wide, 0) * max(high, 0)


def colour(rgb: tuple) -> str:
    """A plain word for a colour: black, white, the greys, or its hue with dark or light in front."""
    r, g, b = (v / 255 for v in rgb[:3])
    high, low = max(r, g, b), min(r, g, b)
    light = (high + low) / 2
    if high - low < 0.12:
        return "black" if light < 0.18 else "dark grey" if light < 0.4 else "grey" if light < 0.65 else (
            "light grey" if light < 0.9 else "white")
    d = high - low
    hue = 60 * (((g - b) / d) % 6 if high == r else (b - r) / d + 2 if high == g else (r - g) / d + 4)
    word = next(name for edge, name in HUES if hue < edge)
    return ("dark " if light < 0.3 else "light " if light > 0.75 else "") + word


def _mean(pixels: list) -> tuple:
    n = max(len(pixels), 1)
    return tuple(sum(p[i] for p in pixels) / n for i in range(3))


def _apart(a: tuple, b: tuple) -> bool:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 > APART


def looks(image, box: tuple, origin: tuple) -> str:
    """What the control in `box` looks like in `image`, the window's picture whose top left is at `origin` on the
    screen: its size, "round" when its corners show what is round it and its middle does not, and its colour."""
    x, y, wide, high = (int(v) for v in box)
    x, y = x - origin[0], y - origin[1]
    if wide < 4 or high < 4 or x < 0 or y < 0 or x + wide > image.size[0] or y + high > image.size[1]:
        return f"{wide}x{high}"
    rgb = image.convert("RGB")
    mid = _mean([rgb.getpixel((x + i * wide // 6, y + j * high // 6)) for i in range(2, 5) for j in range(2, 5)])
    inset = max(1, min(wide, high) // 10)
    right, bottom = x + wide - 1 - inset, y + high - 1 - inset
    corners = [rgb.getpixel(p) for p in ((x + inset, y + inset), (right, y + inset), (x + inset, bottom),
                                         (right, bottom))]
    # Just left and right of the box: what the control sits on.
    outside = [rgb.getpixel(p) for p in ((max(x - 2, 0), y + high // 2),
                                         (min(x + wide + 1, rgb.size[0] - 1), y + high // 2))]
    round_ = all(not _apart(c, _mean(outside)) for c in corners) and _apart(mid, _mean(outside))
    return f"{wide}x{high}" + (", round" if round_ else "") + f", {colour(mid)}"


def label(box: tuple, texts: list[tuple[str, tuple]]) -> str:
    """The text that names a nameless control by being next to it: under it, beside it on the right, or over it,
    as `"Lumen" under it`; "" when no text is that near."""
    x, y, wide, high = box
    best = None
    for text, (tx, ty, tw, th) in texts:
        across = min(x + wide, tx + tw) - max(x, tx) > 0
        level = min(y + high, ty + th) - max(y, ty) > 0
        if across and 0 <= ty - (y + high) <= max(high, 24):
            near, where = ty - (y + high), "under it"
        elif level and 0 <= tx - (x + wide) <= max(wide, 24):
            near, where = tx - (x + wide), "beside it"
        elif across and 0 <= y - (ty + th) <= max(high // 2, 12):
            near, where = y - (ty + th), "over it"
        else:
            continue
        if best is None or near < best[0]:
            best = (near, f'"{text[:40]}" {where}')
    return best[1] if best else ""
