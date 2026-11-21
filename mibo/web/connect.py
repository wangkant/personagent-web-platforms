"""The chat's way to connect a site: "帮我链接一下抖音" does what 登录 on the Connections page does.

The site is found by the name the person used (web/signin.SITES, in any of the
chat languages) or by its address; its sign-in page comes up in the app's own
browser with the column waiting (panels/signins.py), the person signs in there
themselves, and the tool returns once they are in, with what can now be asked
of the site. A website is never opened with a command or another program.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import signin

NAME = "sign_in_to_site"
TOOL = {"type": "function", "function": {
    "name": NAME,
    "description": (
        "Connect a website for them: when they ask to connect, link, bind, sign in or log in to a site or a "
        # copy: voice (what a person says)
        "mailbox (\"帮我链接一下抖音\", \"登录一下 b 站\", \"connect my Gmail\", \"ログインして\"), by its name or "
        "address. Its sign-in page comes up in the app's own browser and they sign in there themselves; this "
        "returns once they are in. Never open a website with a command, a program or another browser for this, "
        "and never ask them to switch anything on first; a mailbox on the web (Gmail, QQ 邮箱, Outlook, 163) is a "
        "site like any other."),
    "parameters": {"type": "object", "properties": {
        "site": {"type": "string", "description": "the site as they named it (\"抖音\", \"Gmail\") or its address"}},
        "required": ["site"]}}}
# How often the tool asks how the sign-in stands, and for how long.
LOOK_S = 2.0


def place(words: str) -> dict:
    """{"name", "home", "url"} for the site named, or {} when the words name none."""
    row = signin.find(words)
    if row:
        return {"name": row["name"].get("zh") if row.get("lang") == "zh" else row["name"]["en"], "home": row["home"],
                "url": row.get("signin") or row["home"], "site": row["site"]}
    said = str(words or "").strip()
    typed = said if "://" in said else f"https://{said}"
    host = urlsplit(typed).hostname or ""
    if " " in said or "." not in host:
        return {}
    return {"name": host.removeprefix("www."), "home": signin.home_of(typed), "url": typed, "site": signin.key(typed)}


async def use(home: Path, args: dict, lang: str) -> str:
    """The sign-in brought to the pane and waited on; a line for the model on how it went."""
    found = place(str(args.get("site") or ""))
    if not found:
        return "(say which site: its name as they said it, or its address)"
    ask = signin.asker(home)
    first = await asyncio.to_thread(ask, "connect", 40, home=found["home"], url=found["url"], name=found["name"])
    status = first.get("status")
    what = signin.example(first.get("site") or found["site"], lang)
    if status == "in":
        return (f"[already signed in] {found['name']} is already signed in in the app's browser; its page is open there "
                f"now. Tell them so in a sentence, and what they can ask you there, such as \"{what}\".")
    if status in ("away", "no_app"):
        return "(the app window is not open in front of them: the sign-in needs them at the app; tell them so)"
    if status == "busy":
        return "(the app's browser is busy with another task; tell them to try again once it is done)"
    if status != "waiting":
        return f"(the sign-in page of {found['name']} could not be opened: {status})"
    began = time.monotonic()
    while time.monotonic() - began < signin.WAIT_S:
        await asyncio.sleep(LOOK_S)
        now = (await asyncio.to_thread(ask, "connected", 15, site=first["site"])).get("status")
        if now == "ok":
            return (f"[signed in] {found['name']} is signed in in the app's browser now; they did it themselves. Tell "
                    f"them in one or two sentences that it is connected and what they can ask you there, such as "
                    f"\"{what}\". Nothing on the site was read or sent.")
        if now != "waiting":
            return (f"[not signed in] they closed or stopped the sign-in of {found['name']}; it is not connected. Say "
                    f"so in a sentence; they can ask again any time.")
    return f"[not signed in] no sign-in to {found['name']} came in time; it is not connected."
