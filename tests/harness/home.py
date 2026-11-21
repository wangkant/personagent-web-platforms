"""A home as a floor reads it back: every file under it that holds given words, and the same home served again
after a restart, so what a turn left behind is found wherever the app put it."""
from __future__ import annotations

import json
from pathlib import Path

from harness.app import App


def forms(words: str) -> list[bytes]:
    """The ways `words` can sit in a file: as UTF-8, and as JSON writes it with ensure_ascii."""
    return list(dict.fromkeys([words.encode("utf-8"), json.dumps(words)[1:-1].encode("ascii")]))


def holding(root: Path, words) -> dict[str, list[str]]:
    """{words: the files under `root` that hold them}, each file read whole as bytes."""
    found: dict[str, list[str]] = {w: [] for w in words}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for w in words:
            if any(form in data for form in forms(w)):
                found[w].append(path.relative_to(root).as_posix())
    return found


def restarted(app: App) -> App:
    """`app` stopped (its character closed with it) and its home served again by a new one behind the same model;
    the home's .env keeps the settings the first was given."""
    app.close()
    return App(app.home, app.model).open()
