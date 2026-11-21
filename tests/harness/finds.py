"""A home's finds and a person's reactions to them, recorded the way the pet's card, the chat and the app's
browser record them: things in the library, and the feed's events through the feed's own `remember`. The
taste model is fitted only when a test says (`learn`), at the harness clock's now: nothing refits in the
background, so what a test reads is what its reactions taught."""
from __future__ import annotations

from pathlib import Path

from mibo import situation, taste
from mibo.library import Item, Library
from mibo.panels import feed

# A listed player, so a video find is shown as a video.
PLAYER = "https://www.youtube-nocookie.com/embed/"


class Shelf:
    """`Shelf(runtime, clock, monkeypatch)`; `add` a find, `react` to it, `learn`, then `ranked` or `first`."""

    def __init__(self, runtime: Path, clock, monkeypatch) -> None:
        self.runtime, self.clock = Path(runtime), clock
        self.library = Library(self.runtime)
        monkeypatch.setattr(taste, "fit", lambda runtime, soon=False: None)
        # One language read on every machine: the system's own list differs from one computer to the next.
        situation.set_value(self.runtime, "readable", ["en"])
        self._count = 0

    def add(self, title: str, *, site: str = "shore.example", kind: str = "article", text: str = "",
            length: int | None = None, hours_ago: float = 1.0, source: str = "", love: int = -1) -> Item:
        """A find the walks or a source brought `hours_ago`, with its cover kept (so nothing fetches one); a
        video carries a listed player."""
        self._count += 1
        number = f"{self._count:011d}"
        return self.library.add(Item(
            url=f"https://{site}/{kind}/{number}", site=site, kind=kind, title=title, description=text,
            length=length, embed=PLAYER + number if kind == "video" else "", picture="cover.jpg", source=source,
            love=love, judged=love >= 0, seen_at=int(self.clock.now - hours_ago * 3600)))

    def react(self, item: Item, action: str, surface: str = "card", **told) -> bool:
        """One reaction as the page sends it (feed.remember): seen, open, like, skip, hide_source, play, read
        (`play_s`), end (`end`, `shown_s`); whether it was kept."""
        return feed.remember(self.runtime, item, action, self.clock.now, surface=surface, **told)

    def events(self) -> list:
        """The feed's events, as they are kept."""
        return feed.events(self.runtime)

    def learn(self) -> dict:
        """Fit the taste model on every reaction so far, as the worker thread would; the model written."""
        return taste.train(self.runtime, self.clock.now)

    def ranked(self, topics: tuple = (), seed: int = 1) -> list[Item]:
        """What the feed would bring, in order: the unseen things first."""
        rows = feed.ranked(self.library, self.events(), self.clock.now, list(topics), seed=seed)
        return [item for shaped, item in rows if not shaped["seen"]]
