"""Link cards in the chat: a web address's own title, site, description, picture and player, read once from
the page through the app's safe fetch (public addresses only) and kept; and site icons for the cards and the
sources row. linkcard.js and activity.js draw them."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..storage import atomic_write_text

ERRORS: frozenset[str] = frozenset()
PAGE_BYTES = 1_500_000
PICTURE_BYTES = 6_000_000
ICON_BYTES = 300_000
FETCH_S = 8.0
# A page read is kept a week; one that could not be read is tried again after an hour.
KEPT_S = 7 * 86400
FAILED_S = 3600
KEPT_MOST = 600
ICON_SIDE = 64
NAME = re.compile(r"[0-9a-f]{32}\.(jpg|png)")
HOST = re.compile(r"[a-z0-9.-]{1,253}")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36",
           "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


class Links:
    """The kept previews and their pictures and icons, under runtime/desktop/links."""

    def __init__(self, home: Path) -> None:
        self.folder = Path(home) / "runtime" / "desktop" / "links"
        self.pictures = self.folder / "pictures"
        self.path = self.folder / "previews.json"
        self.busy: dict[str, asyncio.Task] = {}
        self.gate = asyncio.Semaphore(4)
        try:
            self.kept: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.kept = {}

    async def _save(self) -> None:
        """Keep the newest KEPT_MOST rows; once some go, the pictures no row shows go with them."""
        dropped = sorted(self.kept, key=lambda u: self.kept[u].get("at", 0))[:max(0, len(self.kept) - KEPT_MOST)]
        for url in dropped:
            self.kept.pop(url, None)
        text = json.dumps(self.kept, ensure_ascii=False)
        shown = {row.get(k) for row in self.kept.values() for k in ("picture", "icon", "name")} if dropped else None
        await asyncio.to_thread(self._write, text, shown)

    def _write(self, text: str, shown: set | None) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, text)
        for path in self.pictures.glob("*") if shown is not None and self.pictures.is_dir() else ():
            # A picture kept in the last hour may belong to a page still being read.
            if NAME.fullmatch(path.name) and path.name not in shown and time.time() - path.stat().st_mtime > 3600:
                path.unlink(missing_ok=True)

    def _keep_file(self, data: bytes, kind: str) -> str:
        name = f"{hashlib.sha256(data).hexdigest()[:32]}.{kind}"
        self.pictures.mkdir(parents=True, exist_ok=True)
        path = self.pictures / name
        if not path.is_file():
            path.write_bytes(data)
        return name

    async def _fetch(self, url: str, limit: int, types: tuple[str, ...]):
        from ..ingestion import safe_fetch_url
        async with self.gate:
            return await safe_fetch_url(url, timeout=FETCH_S, max_wire_bytes=limit, max_decoded_bytes=limit * 2,
                                        headers=HEADERS, allowed_content_types=types)

    async def _picture(self, url: str) -> str:
        from ..roam import small_picture
        got = await self._fetch(url, PICTURE_BYTES, ("image/",)) if url else None
        small = await asyncio.to_thread(small_picture, got.content) if got else b""
        return self._keep_file(small, "jpg") if small else ""

    async def _icon(self, urls: list[str]) -> str:
        for url in urls:
            got = await self._fetch(url, ICON_BYTES, ("image/",))
            png = await asyncio.to_thread(_as_png, got.content) if got else b""
            if png:
                return self._keep_file(png, "png")
        return ""

    async def _read(self, url: str) -> dict:
        from ..roam import player_for, site_name
        from ..web import fetch
        host = (urlsplit(url).hostname or "").lower()
        out = {"url": url, "site": site_name(url), "host": host.removeprefix("www."), "title": "",
               "description": "", "picture": "", "embed": player_for(url), "length": None, "kind": "page"}
        got = await self._fetch(url, PAGE_BYTES, ("text/html", "application/xhtml"))
        icons: list[str] = []
        if got:
            types = {k.lower(): v for k, v in got.headers.items()}.get("content-type", "")
            facts, icons = fetch.head(fetch.decoded(got.content, types), got.url)
            out.update(title=facts["title"], site=facts["site"] or out["site"], description=facts["description"],
                       embed=facts["embed"] or out["embed"], length=facts["length"], kind=facts["kind"])
            if facts["picture"]:
                out["picture"] = await self._picture(facts["picture"])
        base = got.url if got else url
        icons = [urljoin(base, href) for href in icons[:3]] + [f"https://{host}/favicon.ico"]
        out["icon"] = self.icon_kept(host) or await self._icon(icons)
        self.kept[f"icon:{host}"] = {"name": out["icon"], "at": time.time()}
        out["ok"] = bool(got)
        return out

    def icon_kept(self, host: str) -> str | None:
        """The kept icon's name, "" for a host known to have none, None when not asked yet or long ago."""
        row = self.kept.get(f"icon:{host}") or {}
        return row.get("name", "") if time.time() - row.get("at", 0) < KEPT_S else None

    async def preview(self, url: str) -> dict:
        row = self.kept.get(url)
        if row and time.time() - row.get("at", 0) < (KEPT_S if row.get("ok") else FAILED_S):
            return row
        if url not in self.busy:
            self.busy[url] = asyncio.ensure_future(self._read(url))
        try:
            row = await self.busy[url]
        finally:
            self.busy.pop(url, None)
        row["at"] = time.time()
        self.kept[url] = row
        await self._save()
        return row

    async def icon(self, host: str) -> str:
        kept = self.icon_kept(host)
        if kept is not None:
            return kept
        key = f"icon:{host}"
        if key not in self.busy:
            self.busy[key] = asyncio.ensure_future(self._icon([f"https://{host}/favicon.ico"]))
        try:
            name = await self.busy[key]
        finally:
            self.busy.pop(key, None)
        # Kept even when there is none, so a host without an icon is not asked again each time.
        self.kept[key] = {"name": name, "at": time.time()}
        await self._save()
        return name

    def file(self, name: str) -> Path | None:
        path = self.pictures / name
        return path if NAME.fullmatch(name) and path.is_file() and not path.is_symlink() else None


def _as_png(data: bytes) -> bytes:
    """A site icon as a small PNG, or b"" for what is not a picture."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as icon:
            if icon.width * icon.height > 4096 * 4096:
                return b""
            picture = icon.convert("RGBA")
        picture.thumbnail((ICON_SIDE, ICON_SIDE))
        # An icon drawn white on nothing (for a dark tab bar) shows as nothing on a card: none, then.
        seen = [(r, g, b) for r, g, b, a in picture.getdata() if a > 64]
        if len(seen) < picture.width * picture.height * 0.05 or min(r + g + b for r, g, b in seen) > 3 * 235:
            return b""
        out = io.BytesIO()
        picture.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return b""


def _public(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and len(url) <= 2000


def mount(app, workspace, body) -> None:
    def links() -> Links:
        if getattr(app.state, "links", None) is None:
            app.state.links = Links(workspace.home)
        return app.state.links

    @app.get("/api/link/preview")
    async def link_preview(url: str = ""):
        if not _public(url):
            raise HTTPException(400)
        row = await links().preview(url)
        picture, icon = row.get("picture"), row.get("icon")
        return {**{k: row.get(k) for k in ("url", "site", "host", "title", "description", "embed", "length", "kind", "ok")},
                "picture": f"/api/link/file/{picture}" if picture else "",
                "icon": f"/api/link/file/{icon}" if icon else ""}

    @app.get("/api/link/icon")
    async def link_icon(host: str = ""):
        host = host.strip().lower()
        if not HOST.fullmatch(host) or "." not in host:
            raise HTTPException(400)
        name = await links().icon(host)
        return {"icon": f"/api/link/file/{name}" if name else ""}

    @app.get("/api/link/file/{name}")
    async def link_file(name: str):
        path = links().file(name)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png" if name.endswith(".png") else "image/jpeg")
