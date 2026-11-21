"""The remarks panel: the character now and then says a line of its own about
what its person is doing ("Doing homework, huh? You're working hard.").

The lifespan starts `app.state.remarks.loop()`: every EVERY_S it reads what is
in front (`activity.sense`, on this computer only) and, when `remarks.Pace`
says a line is due and the character is neither asleep nor out, has one
written. The pet is told at once, and `GET /api/remark/next` gives it the
line once, to say in a bubble; the same line goes into the main chat through
`workspace.remark`. What is in front comes from quiet's hook (activity.py).
`GET/POST /api/remarks/settings` is the switch and the programs never looked at.
"""
from __future__ import annotations

import asyncio
import traceback
from datetime import datetime

from fastapi import Request

from .. import activity, life, profile, quiet, remarks
from ..config_env import parse_tz_offset
from ..library import Library
from ..textproc import default_tz_offset

ERRORS: frozenset[str] = frozenset({"remarks_invalid"})
# A line the pet has not taken by then is about a moment gone.
STALE_S = 180


class Remarks:
    def __init__(self, workspace):
        self.ws = workspace
        self.started = life.clock()
        self.pace: remarks.Pace | None = None
        self.pending: dict | None = None
        # remarks.json as last read or written here, its only writer.
        self.state: dict | None = None
        # Tells the pet a line is waiting (mount sets it).
        self.said = lambda: None
        # Set when the settings are saved: switched off, the loop sleeps until then.
        self.switched = asyncio.Event()

    def _state(self) -> dict:
        if self.state is None:
            self.state = remarks.load(self.ws._runtime())
        return self.state

    def settings(self) -> dict:
        state = self._state()
        return {"on": state["on"], "ignore": state["ignore"]}

    def set_settings(self, payload: dict) -> dict:
        state = self._state()
        on, ignore = payload.get("on", state["on"]), payload.get("ignore", state["ignore"])
        if not isinstance(on, bool) or not isinstance(ignore, list) or not all(isinstance(n, str) for n in ignore):
            raise ValueError("remarks_invalid")
        self.state = remarks.save(self.ws._runtime(), on=on, ignore=remarks.clean_ignore(ignore))
        if not on:
            self.pace = self.pending = None
        return self.settings()

    def take(self) -> dict:
        line, self.pending = self.pending, None
        return {"remark": line if line and life.clock() - line["at"] <= STALE_S else None}

    def _quiet(self, values: dict, now: float) -> bool:
        """The character is asleep on its own clock, or out walking."""
        hours = parse_tz_offset(values.get("PERSONA_TZ_OFFSET_HOURS", ""))
        if hours is None:
            # As the character reads it, with the language from the home's settings.
            hours = default_tz_offset(values.get("AGENT_LANG") or "")
        return bool(life.asleep_until(now, hours)) or Library(self.ws._runtime()).outing().state == "out"

    async def step(self, now: float) -> None:
        runtime = self.ws._runtime()
        state = self.state or await asyncio.to_thread(self._state)
        if not state["on"]:
            self.pace = None
            return
        sample = await asyncio.to_thread(activity.sense, state["ignore"])
        # The same look tells the feed's pacing what is in front (pacing.py).
        await self.ws.pace.look(sample)
        if self.pace is None:
            # Started with the app, or when the switch was turned on.
            self.pace = remarks.Pace(max(self.started, now - remarks.EVERY_S))
        self.pace.see(now, *sample)
        count = remarks.today(state, now)
        known = await asyncio.to_thread(profile.load, runtime, False)
        due = self.pace.due(now, state["at"], count, known["fewer"])
        if due is None:
            return
        values = await asyncio.to_thread(self.ws.config)
        if await asyncio.to_thread(self._quiet, values, now):
            return
        # How the person asked to be treated holds for the pet's lines too.
        manner = await asyncio.to_thread(profile.manner_rule, runtime)
        text = await remarks.write(self.ws.home, values, due, now, manner)
        # Into the main chat first; while a reply is under way there it says False, and the line waits a tick.
        if not await self.ws.remark(text):
            return
        self.pace.done(due["kind"])
        self.state = await asyncio.to_thread(remarks.save, runtime, at=now,
                                             day=datetime.fromtimestamp(now).date().isoformat(), count=count + 1)
        self.pending = {"id": f"{int(now * 1000)}", "text": text, "activity": due["activity"], "at": int(now)}
        self.said()

    async def loop(self) -> None:
        while not self.ws._closed:
            await asyncio.sleep(remarks.EVERY_S)
            await quiet.wait()
            try:
                await self.step(life.clock())
            except Exception:
                traceback.print_exc()
            if not self._state()["on"]:
                # Switched off: nothing is looked at until the settings are saved again.
                await self.switched.wait()
                self.switched.clear()


def mount(app, workspace, body) -> None:
    panel = Remarks(workspace)
    app.state.remarks = panel

    def said() -> None:
        # The pet asks for the line at once, not at its next look (the window app only).
        window = getattr(app.state, "window", None)
        if window is not None:
            window.pet_look()

    panel.said = said

    @app.get("/api/remark/next")
    async def remark_next():
        # A video in a window in front: the pet keeps still, and the line waits for after it.
        if quiet.state()["video_front"]:
            return {"remark": None, "video_front": True}
        return {**panel.take(), "video_front": False}

    @app.get("/api/remarks/settings")
    async def remarks_settings():
        return await asyncio.to_thread(panel.settings)

    @app.post("/api/remarks/settings")
    async def remarks_save(request: Request):
        saved = await asyncio.to_thread(panel.set_settings, await body(request))
        panel.switched.set()
        return saved
