"""The profile panel: what the character knows of its owner, for the owner to read and edit.

`GET /api/profile` is the texts, the source leans, the walk words, the pinned
lines and the hard rules, the sources a lean may name, and the character's
model calls per month. `POST /api/profile` takes the owner's edits; a changed
text asks for a rewrite at once, so a "not this" becomes a rule. `POST
/api/profile/answer` is the first run's free-text answer, rewritten at once.

`GET /api/profile/taste` is Settings › 推荐's "your taste" in the page's
language: the picked topics with their leans, what the reactions taught, every
other lean (set from a card or a taught row), what was said in the chat (likes, wishes for now, "not this" rules, the form wish),
the pace with each pace's day cap, and the quiet hours. `POST
/api/profile/taste` takes one change of those ({"leans"}, {"likes"},
{"rules"}, {"form"}, {"pace"}, {"quiet_hours"}, as profile.edit reads them,
or {"reset": true}) and answers with the view again.
"""
from __future__ import annotations

import asyncio

from fastapi import Request

from .. import langs, life, pacing, profile, subjects, taste

ERRORS: frozenset[str] = frozenset({"profile_invalid"})
TASTE_EDITS = frozenset({"leans", "likes", "rules", "form", "pace", "quiet_hours"})


class Profile:
    def __init__(self, workspace):
        self.ws = workspace

    def view(self) -> dict:
        runtime = self.ws._runtime()
        zh = 1 if langs.normalize(self.ws.config().get("AGENT_LANG")) in ("zh", "zh-TW") else 0
        return {**profile.load(runtime),
                "sources": [{"key": s.key, "name": s.name[zh], "about": s.about[zh]}
                            for s in profile.readable_sources(runtime).values()],
                "calls": profile.calls(runtime)}

    async def edit(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("profile_invalid")
        changed = await asyncio.to_thread(profile.edit, self.ws._runtime(), payload)
        rewritten = changed and await profile.rewrite(self.ws._runtime(), self.ws.home)
        return {**await asyncio.to_thread(self.view), "rewritten": rewritten}

    async def answer(self, payload: dict) -> dict:
        rewritten = await profile.answer(self.ws._runtime(), self.ws.home, (payload or {}).get("text"))
        return {**await asyncio.to_thread(self.view), "rewritten": rewritten}

    def taste(self, lang: str) -> dict:
        from . import feed
        runtime = self.ws._runtime()
        lang = langs.normalize(lang) or langs.normalize(self.ws.config().get("AGENT_LANG")) or "en"
        known = profile.load(runtime, False)
        names = {k: s.name[1 if lang.startswith("zh") else 0] for k, s in profile.readable_sources(runtime).items()}
        # A taught weight the person leaned on (or took out, held at 0 from the next fit on) leaves the taught
        # rows at once for the leans below.
        taught = {side: [row for row in rows if row["key"] not in known["leans"]]
                  for side, rows in taste.taught(runtime, lang).items()}
        keys = {t: "topic:" + t.casefold() for t in feed.load_topics(runtime)}
        # Every other lean, wherever it was set (a card's chip or its no, a taught row), with a row to clear it.
        said = {x["topic"].casefold(): x["topic"] for x in known["likes"]}
        leans = [{"key": key, "name": self._lean_name(key, names, said, lang), "lean": to}
                 for key, to in known["leans"].items() if key not in keys.values()]
        return {"topics": [{"name": t, "key": key, "lean": known["leans"].get(key)} for t, key in keys.items()],
                "offered": feed.suggested(lang),
                **taught,
                "leans": leans,
                "likes": [x for x in known["likes"] if not x.get("until")],
                "wishes": [x for x in known["likes"] if x.get("until")],
                "rules": [{**r, "name": names.get(r.get("key"), r.get("key"))} if r.get("kind") == "hide_source" else r
                          for r in known["rules"]],
                "form": known["form"] if known["form"].get("short") is not None or known["form"].get("kind") else None,
                "pace": known["pace"], "caps": {k: int(pacing.DAY_MAX // v) for k, v in pacing.SLOW.items()},
                "quiet_hours": known["quiet_hours"]}

    @staticmethod
    def _lean_name(key: str, sources: dict, said: dict, lang: str) -> str:
        """What a lean's feature is called on the page: a subject's or a source's name, a said like as it was
        said, a site or a word as it is; a form ("kind:video") by its kind, for the page to put in words."""
        family, _, rest = key.partition(":")
        if family == "subj":
            return subjects.name(rest, lang) if rest in subjects.SUBJECTS else rest
        if family == "like":
            return said.get(rest, rest)
        if family == "origin":
            where, _, name = rest.partition(":")
            return sources.get(name, name) if where == "src" else name
        return rest

    def change_taste(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("profile_invalid")
        edits = {k: v for k, v in payload.items() if k != "lang"}
        if edits == {"reset": True}:
            profile.reset(self.ws._runtime(), life.clock())
        elif len(edits) == 1 and edits.keys() <= TASTE_EDITS:
            profile.edit(self.ws._runtime(), edits)
        else:
            raise ValueError("profile_invalid")
        return self.taste(payload.get("lang") or "")


def mount(app, workspace, body) -> None:
    panel = Profile(workspace)

    @app.get("/api/profile")
    async def profile_view():
        return await asyncio.to_thread(panel.view)

    @app.post("/api/profile")
    async def profile_edit(request: Request):
        return await panel.edit(await body(request))

    @app.post("/api/profile/answer")
    async def profile_answer(request: Request):
        return await panel.answer(await body(request))

    @app.get("/api/profile/taste")
    async def profile_taste(lang: str = ""):
        return await asyncio.to_thread(panel.taste, lang)

    @app.post("/api/profile/taste")
    async def profile_taste_change(request: Request):
        return await asyncio.to_thread(panel.change_taste, await body(request))
