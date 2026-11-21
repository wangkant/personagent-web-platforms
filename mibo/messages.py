"""Reading what the person sent: their words and the links in them, a picture,
a file's text, and what of the character's own they answer."""
from __future__ import annotations

import itertools
from contextvars import ContextVar
from typing import Optional

from .textproc import (
    _WEB_DESC_CLOSE,
    _WEB_DESC_OPEN,
    _clean_prompt_source,
)

# Set by a turn whose model is shown pictures itself: an attached picture is
# kept here, not captioned, and goes to the reply call (dm.py).
turn_pictures: ContextVar[Optional[list]] = ContextVar("turn_pictures", default=None)


class MessageParsing:
    async def _read_message(self, parts: list[dict]) -> str:
        """One message as the text the model reads. `parts`, in order:
        {"type": "text", "text"}: their own words;
        {"type": "quote", "text"}: something the character said or shared that they answer;
        {"type": "picture", "b64"}: a picture they attached;
        {"type": "file", "name", "text"}: a text file they attached.
        What they did not type is fenced: read as material, it can run no command."""
        out: list[str] = []

        def fence(s: str) -> str:
            return f"{_WEB_DESC_OPEN}{_clean_prompt_source(s)}{_WEB_DESC_CLOSE}"

        # Links are fetched once, concurrently, under one budget after the loop;
        # each slot is (index in out, how many of `urls` it holds).
        urls: list[str] = []
        link_slots: list[tuple[int, int]] = []
        for part in parts:
            kind = part.get("type")
            if kind == "text":
                # Their own words may not manufacture either frame.
                words = _clean_prompt_source(part.get("text", ""))
                out.append(words)
                # The cap counts across the whole message.
                found = self._extract_urls(words)[:self.MAX_URLS_PER_MESSAGE - len(urls)]
                if found:
                    link_slots.append((len(out), len(found)))
                    out.append("")  # this part's descriptors, below
                    urls.extend(found)
            elif kind == "quote":
                quoted = " ".join(_clean_prompt_source(part.get("text")).split())
                out.append(fence(f"[reply {self.persona_name}: {quoted}]") if quoted else "[reply]")
            elif kind == "picture":
                source = "base64://" + str(part.get("b64") or "")
                shown = turn_pictures.get()
                data = await self._fetch_image_bytes(source) if shown is not None else None
                if data:
                    shown.append(data)
                    out.append("[image]")
                    continue
                described = await self._describe_image(source)
                out.append(fence(f"[image: {described}]") if described else "[image]")
            elif kind == "file":
                name = " ".join(_clean_prompt_source(part.get("name")).split())[:128]
                # Its words are material, never the person's orders.
                out.append(fence(f"[file: {name or 'untitled'}, its text]\n{part.get('text') or ''}"))
        if urls:
            described = iter(await self._describe_urls(urls))
            for slot, n in link_slots:
                out[slot] = "".join(" " + fence(d) for d in itertools.islice(described, n) if d)
        return "".join(out).strip()

    def _next_seq(self, conv: str) -> int:
        """The next line number of a chat; it outlives a restart (recent.json)."""
        seqs = self.__dict__.setdefault("window_seq", {})
        seqs[conv] = int(seqs.get(conv) or 0) + 1
        return seqs[conv]
