"""Content understanding: the links and pictures in a message.

All network egress goes through the SSRF guard; third-party URLs are untrusted."""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import io
import ipaddress
import logging
import re
import socket
import ssl
import time
import warnings
import zlib
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from .config_env import env_int
from .textproc import (_detect_image_mime)

logger = logging.getLogger("agent")

# Hard ceiling on any image the bot decodes or forwards.
VISION_MAX_IMAGE_BYTES = env_int("VISION_MAX_IMAGE_BYTES", 5_000_000, minimum=1)

# A reading of a picture takes this many tokens at most: a screenshot's or a receipt's text, whole.
VISION_READ_TOKENS = 1500
# A busy vision model is asked this many times in all, waiting VISION_BACKOFF_S, then twice that, between.
VISION_ATTEMPTS = 3
VISION_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
VISION_BACKOFF_S = 1.0

MAX_URL_LENGTH = 4096
MAX_HTML_WIRE_BYTES = 128 * 1024
MAX_HTML_DECODED_BYTES = 128 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8192

# Internal by definition (RFC 6761/2606), so rejected without a lookup.
_RESERVED_LOCAL_NAMES = frozenset({
    "localhost", "localhost.localdomain",
    "ip6-localhost", "ip6-loopback",
})


@dataclass(frozen=True)
class SafeFetchResult:
    """A bounded body plus the final URL after validated redirects."""

    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes


def _ip_obj_is_internal(ip) -> bool:
    # A mapped address reaches the IPv4 host it names, so judge that host.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # `not is_global` catches CGNAT 100.64.0.0/10 (a cloud metadata range no
    # flag below covers); site-local fec0::/10 still reports is_global on 3.11.
    return (not ip.is_global or getattr(ip, "is_site_local", False)
            or ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _is_internal_ip(value: str) -> bool:
    """Unparsable counts as internal: the SSRF guard fails closed."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    return _ip_obj_is_internal(ip)


def _url_for_log(url: str) -> str:
    """`scheme://host[:port]/…` of a fetched URL, for a log line: the path can
    carry a token (Telegram file URLs do)."""
    try:
        parts = urlsplit(str(url))
        scheme = parts.scheme.lower()
        if scheme and scheme not in ("http", "https"):
            # base64:// and file:// "hosts" are payload or path, not a host.
            return f"{scheme[:16]}://…"
        host = parts.hostname or ""
        port = parts.port
    except (TypeError, ValueError):
        return "<unparsable url>"
    if not scheme or not host:
        return "<unparsable url>"
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}{f':{port}' if port else ''}/…"


def _cap_cache(cache: dict, cap: int = 200, drop: int = 50) -> None:
    if len(cache) > cap:
        for k in list(cache)[:drop]:
            cache.pop(k, None)


async def _resolve_public_target(url: str) -> tuple[str, str, int] | None:
    """Resolve once and return (hostname, pinned public IP, port), or None.
    Fails closed unless every returned address is public."""
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").strip("[]").rstrip(".")
        if scheme not in ("http", "https") or not host:
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        return None

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except Exception:
            return None
        addresses: list[str] = []
        for info in infos:
            address = str(info[4][0]).split("%", 1)[0]
            if address not in addresses:
                addresses.append(address)
        if not addresses or any(_is_internal_ip(address) for address in addresses):
            return None
        return host, addresses[0], port
    else:
        address = str(literal)
        if _is_internal_ip(address):
            return None
        return host, address, port


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to the address validated for one URL hop."""

    def __init__(self, hostname: str, pinned_ip: str) -> None:
        self.hostname = hostname.casefold().rstrip(".")
        self.pinned_ip = pinned_ip
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        requested = str(host).casefold().rstrip(".")
        if requested != self.hostname:
            raise OSError("pinned transport refused an unexpected hostname")
        return await self._backend.connect_tcp(
            self.pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options=None,
    ):
        raise OSError("pinned HTTP transport does not permit Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


@functools.lru_cache(maxsize=1)
def _default_ssl_context() -> ssl.SSLContext:
    """Shared: creating one loads the system's certificates, 10 ms a fetch."""
    return ssl.create_default_context()


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport that pins TCP while retaining the original Host/SNI."""

    def __init__(self, hostname: str, pinned_ip: str) -> None:
        ssl_context = _default_ssl_context()
        backend = _PinnedNetworkBackend(hostname, pinned_ip)
        super().__init__(
            verify=ssl_context,
            trust_env=False,
            http1=True,
            http2=False,
        )
        # httpcore receives the original URL origin, so HTTP Host and TLS SNI
        # remain the hostname. Only connect_tcp is replaced with the pinned IP.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=10,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            network_backend=backend,
        )
        self.pinned_ip = pinned_ip


def _decode_body(
    chunks: list[bytes],
    encoding: str,
    max_decoded_bytes: int,
) -> bytes | None:
    """Decode one bounded HTTP content-coding without unbounded allocation."""
    if encoding in ("", "identity"):
        body = b"".join(chunks)
        return body if len(body) <= max_decoded_bytes else None
    if encoding == "gzip":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decoder = zlib.decompressobj()
    else:
        return None

    decoded = bytearray()
    try:
        for chunk in chunks:
            remaining = max_decoded_bytes - len(decoded)
            if remaining < 0:
                return None
            part = decoder.decompress(chunk, remaining + 1)
            decoded.extend(part)
            if len(decoded) > max_decoded_bytes or decoder.unconsumed_tail:
                return None
        remaining = max_decoded_bytes - len(decoded)
        decoded.extend(decoder.flush(remaining + 1))
    except zlib.error:
        return None
    if len(decoded) > max_decoded_bytes or not decoder.eof:
        return None
    return bytes(decoded)


async def safe_fetch_url(
    url: str,
    *,
    timeout: float,
    max_wire_bytes: int,
    max_decoded_bytes: int,
    headers: Optional[dict] = None,
    allowed_content_types: tuple[str, ...] = (),
    require_content_type: bool = False,
    max_redirects: int = 5,
    client_factory=None,
) -> SafeFetchResult | None:
    """Fetch with address pinning, redirect checks, and strict body limits."""
    current = url
    request_headers = dict(headers or {})
    request_headers.setdefault("Accept-Encoding", "gzip, deflate")

    for _ in range(max_redirects + 1):
        if not current or len(current) > MAX_URL_LENGTH:
            return None
        try:
            resolved = await asyncio.wait_for(
                _resolve_public_target(current),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[Agent] DNS resolution timed out: %s", _url_for_log(current))
            return None
        if resolved is None:
            logger.warning("[Agent] refusing internal/unresolvable URL hop: %s",
                           _url_for_log(current))
            return None
        hostname, pinned_ip, _port = resolved
        transport = _PinnedAsyncHTTPTransport(hostname, pinned_ip)
        factory = client_factory or httpx.AsyncClient
        try:
            client_cm = factory(
                timeout=timeout,
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            )
            async with client_cm as client:
                async with client.stream(
                    "GET",
                    current,
                    headers=request_headers,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if not location:
                            return None
                        current = str(httpx.URL(current).join(location))
                        continue
                    if response.status_code != 200:
                        return None

                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    if require_content_type and not content_type:
                        return None
                    if allowed_content_types and not any(
                        content_type == allowed.rstrip("/")
                        or content_type.startswith(allowed)
                        for allowed in allowed_content_types
                    ):
                        return None

                    length = response.headers.get("content-length", "")
                    try:
                        if length and int(length) > max_wire_bytes:
                            return None
                    except (TypeError, ValueError):
                        pass

                    chunks: list[bytes] = []
                    wire_size = 0
                    async for chunk in response.aiter_raw():
                        wire_size += len(chunk)
                        if wire_size > max_wire_bytes:
                            return None
                        chunks.append(chunk)

                    encoding = (
                        response.headers.get("content-encoding", "")
                        .strip()
                        .lower()
                    )
                    body = _decode_body(chunks, encoding, max_decoded_bytes)
                    if body is None:
                        return None
                    return SafeFetchResult(
                        url=str(getattr(response, "url", current) or current),
                        status_code=response.status_code,
                        headers={str(k): str(v) for k, v in response.headers.items()},
                        content=body,
                    )
        except Exception as e:
            logger.debug("[Agent] safe fetch failed (%s): %s", _url_for_log(current), e)
            return None
        finally:
            # A fake client factory may ignore the transport; closing twice is
            # safe for httpx and ensures the transport never leaks a pool.
            try:
                await transport.aclose()
            except Exception:
                pass
    logger.warning("[Agent] redirect cap exceeded: %s", _url_for_log(url))
    return None


class ContentIngestion:
    """Mixed into Agent; see agent.py."""

    _WBI_MIXIN_KEY_ENC_TAB = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
        27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
        37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
        22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
    ]

    # ============ Generic URL understanding ============
    # Ends at whitespace, CJK punctuation/ideographs/full-width, or brackets.
    URL_PATTERN = re.compile(
        r'https?://[^\s　-〿一-鿿＀-￯<>{}|`\[\]]+'
    )
    #: Links described per message, message-wide: each is an outbound fetch
    #: before the message is buffered, so uncapped, one message of
    #: cache-busted URLs could stall intake.
    MAX_URLS_PER_MESSAGE = 3
    #: Wall clock for describing one message's links, which run concurrently:
    #: a slow or tarpit host costs its own descriptor, never the turn.
    LINK_ENRICHMENT_BUDGET_SEC = 10.0
    _URL_SKIP_EXT = (".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".msi", ".dmg",
                     ".apk", ".pdf", ".mp4", ".mp3", ".mov", ".avi", ".mkv",
                     ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

    # A picture the person sent in their own chat: the reply model answers from this alone.
    VISION_READ_PROMPT = (
        "Someone sent this picture to their assistant and will ask about it. The assistant "
        "cannot see it and answers from what you write.\n"
        "1. Copy all the text and numbers in it exactly as written, in reading order; a table row by row.\n"
        "2. Then say in a sentence or two what it shows (a screenshot of what, a photo of what).\n"
        "3. If you can't make it out, reply \"can't see\". Never invent anything."
    )

    async def _fetch_image_bytes(self, url: str) -> bytes | None:
        """The bytes of a picture the person attached, from its base64:// form; None when they are not a
        picture within VISION_MAX_IMAGE_BYTES."""
        encoded = url.removeprefix("base64://")
        if len(encoded) > ((VISION_MAX_IMAGE_BYTES + 2) // 3) * 4 + 4:
            logger.warning("[Agent] base64 image exceeds size limit")
            return None
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as e:
            logger.debug("[Agent] base64 image decode failed: %s", e)
            return None
        if len(data) > VISION_MAX_IMAGE_BYTES or not _detect_image_mime(data):
            return None
        return data

    @staticmethod
    def _format_bili_line(info: dict, title_fallback: str = "") -> str:
        """The `[bilibili-video] "title" — by <up>, ...` line fed to the model."""
        title = info.get("title") or title_fallback
        up = info.get("up", "")
        summary = (info.get("summary", "") or "").strip().replace("\n", " ")
        desc = (info.get("desc", "") or "").strip().replace("\n", " ")[:80]
        line = f"[bilibili-video] \"{title}\""
        if up:
            line += f" — by {up}"
        if summary:
            line += f", AI summary: {summary[:200]}"
        elif desc:
            line += f", description: {desc}"
        return line

    async def _fetch_bili_info(self, url: str) -> dict:
        """Bilibili title/uploader/desc (and AI summary) for a share URL, via
        b23.tv and the web view API; {} on any failure."""
        if not url or len(url) > MAX_URL_LENGTH:
            return {}

        cache_key = self._url_cache_key(url)
        if cache_key in self.bili_info_cache:
            return self.bili_info_cache[cache_key]

        real_url = url
        if "b23.tv" in url:
            # "contains b23.tv" is not "is a shortlink": never fetch internal hosts.
            if self._host_is_internal(url):
                logger.warning("[Agent] refusing internal-address b23 url: %s", url)
                self.bili_info_cache[cache_key] = {}
                return {}
            # _safe_get re-checks every redirect hop.
            r = await self._safe_get(url, timeout=5,
                                     headers={"User-Agent": "Mozilla/5.0"})
            if r is not None:
                real_url = str(r.url)

        m = re.search(r"BV[a-zA-Z0-9]{10}", real_url)
        if not m:
            self.bili_info_cache[cache_key] = {}
            return {}
        bvid = m.group(0)

        info: dict = {}
        cid: int = 0
        up_mid: int = 0
        try:
            async with self._http(timeout=5) as client:
                r = await client.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"bvid": bvid},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                r.raise_for_status()
                data = r.json()
                if data.get("code") == 0:
                    d = data.get("data") or {}
                    cid = int(d.get("cid") or 0)
                    # "owner" is Bilibili's field for the uploader.
                    up_mid = int((d.get("owner") or {}).get("mid") or 0)
                    info = {
                        "title": (d.get("title") or "")[:80],
                        "up": ((d.get("owner") or {}).get("name") or "")[:30],
                        "desc": (d.get("desc") or "")[:200],
                    }
        except Exception as e:
            logger.debug("[Agent] Bili view API failed (%s): %s", bvid, e)

        if info and cid and up_mid:
            summary = await self._fetch_bili_summary(bvid, cid, up_mid)
            if summary:
                info["summary"] = summary

        self.bili_info_cache[cache_key] = info
        _cap_cache(self.bili_info_cache)
        logger.info("[Agent] bili view %s: %s", bvid, (info.get("title") or "(empty)")[:60])
        return info

    async def _fetch_wbi_keys(self) -> tuple[str, str]:
        """(img_key, sub_key) for WBI signing, cached 24h; ('', '') on failure."""
        now = time.time()
        if self._wbi_keys[0] and now - self._wbi_keys_ts < 86400:
            return self._wbi_keys
        try:
            async with self._http(timeout=5) as client:
                r = await client.get(
                    "https://api.bilibili.com/x/web-interface/nav",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                data = (r.json().get("data") or {})
                wbi_img = data.get("wbi_img") or {}
                img_url = wbi_img.get("img_url", "") or ""
                sub_url = wbi_img.get("sub_url", "") or ""
                img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
                sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
                if img_key and sub_key:
                    self._wbi_keys = (img_key, sub_key)
                    self._wbi_keys_ts = now
                    return self._wbi_keys
        except Exception as e:
            logger.debug("[Agent] WBI keys fetch failed: %s", e)
        return ("", "")

    def _wbi_sign_params(
        self, params: dict, img_key: str, sub_key: str
    ) -> dict:
        """Apply WBI signing: appends wts + w_rid. Returns a new params dict."""
        orig = img_key + sub_key
        mixin = "".join(orig[i] for i in self._WBI_MIXIN_KEY_ENC_TAB if i < len(orig))[:32]
        signed = dict(sorted({**params, "wts": int(time.time())}.items()))
        signed = {
            k: "".join(c for c in str(v) if c not in "!'()*")
            for k, v in signed.items()
        }
        sign = hashlib.md5((urlencode(signed) + mixin).encode()).hexdigest()
        signed["w_rid"] = sign
        return signed

    async def _fetch_bili_summary(self, bvid: str, cid: int, up_mid: int) -> str:
        """Bilibili AI summary via view/conclusion/get, or ""."""
        img_key, sub_key = await self._fetch_wbi_keys()
        if not img_key or not sub_key:
            return ""
        params = self._wbi_sign_params(
            {"bvid": bvid, "cid": cid, "up_mid": up_mid},
            img_key, sub_key,
        )
        try:
            async with self._http(timeout=8) as client:
                r = await client.get(
                    "https://api.bilibili.com/x/web-interface/view/conclusion/get",
                    params=params,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": f"https://www.bilibili.com/video/{bvid}",
                    },
                )
                r.raise_for_status()
                data = r.json()
                if data.get("code") != 0:
                    logger.debug("[Agent] bili summary %s: code=%s msg=%s",
                                 bvid, data.get("code"), data.get("message"))
                    return ""
                d = data.get("data") or {}
                mr = d.get("model_result") or {}
                if not mr.get("result_type"):
                    return ""
                summary = (mr.get("summary") or "").strip()
                outline = mr.get("outline") or []
                outline_titles: list[str] = []
                for sec in outline[:5]:
                    t = (sec.get("title") or "").strip()
                    if t:
                        outline_titles.append(t[:30])
                line = summary
                if outline_titles:
                    sep = " | outline:" if line else "outline:"
                    line += sep + " / ".join(outline_titles)
                line = line[:300]
                if line:
                    logger.info("[Agent] bili summary %s: %s", bvid, line[:80])
                return line
        except Exception as e:
            logger.debug("[Agent] bili summary failed (%s): %s", bvid, e)
        return ""

    @classmethod
    def _extract_urls(cls, text: str) -> list[str]:
        """Pull http(s) URLs out of text, deduped, order preserved, capped at
        ``MAX_URLS_PER_MESSAGE``."""
        if not text:
            return []
        urls = []
        seen = set()
        for u in cls.URL_PATTERN.findall(text):
            u = u.rstrip(').,;:!?。，；：！？)』」]>')
            if u in seen:
                continue
            seen.add(u)
            urls.append(u)
            if len(urls) >= cls.MAX_URLS_PER_MESSAGE:
                break
        return urls

    @classmethod
    def _host_is_internal(cls, url: str) -> bool:
        """Network-free SSRF pre-filter: an internal IP literal or reserved name.

        A fast reject, not the boundary (`_resolve_public_target` pins each
        connection). It never resolves: getaddrinfo would block the event loop,
        and a name resolved here says nothing about the address connected later."""
        try:
            host = (urlsplit(url).hostname or "").strip("[]").rstrip(".")
        except Exception:
            return True
        if not host:
            return True
        try:
            return _ip_obj_is_internal(ipaddress.ip_address(host))
        except ValueError:
            pass  # not an IP literal
        # Internal by definition; anything else is left to _resolve_public_target.
        low = host.lower()
        return low in _RESERVED_LOCAL_NAMES or low.endswith(".localhost")

    async def _safe_get(self, url: str, *, timeout: float,
                        headers: Optional[dict] = None,
                        max_redirects: int = 5) -> Optional[httpx.Response]:
        """GET with every redirect hop re-resolved, re-validated and pinned; None
        if a hop is internal or the redirect cap is exceeded."""
        result = await safe_fetch_url(
            url,
            timeout=timeout,
            max_wire_bytes=MAX_HTML_WIRE_BYTES,
            max_decoded_bytes=MAX_HTML_DECODED_BYTES,
            headers=headers,
            allowed_content_types=("text/html", "application/xhtml+xml"),
            require_content_type=True,
            max_redirects=max_redirects,
        )
        if result is None:
            return None
        request = httpx.Request("GET", result.url)
        # The body is already decoded, so drop the framing headers, or httpx
        # re-runs gzip over plaintext and every gzip-served link degrades.
        headers = {
            k: v for k, v in (result.headers or {}).items()
            if k.lower() not in ("content-encoding", "content-length",
                                 "transfer-encoding")
        }
        return httpx.Response(
            result.status_code,
            headers=headers,
            content=result.content,
            request=request,
        )

    @classmethod
    def _should_skip_url(cls, url: str) -> bool:
        u = url.lower()
        if any(u.split('?')[0].endswith(ext) for ext in cls._URL_SKIP_EXT):
            return True
        return cls._host_is_internal(url)

    async def _describe_url(self, url: str) -> str:
        """A descriptor for one URL: Bilibili API, YouTube oEmbed, or the page's
        OG tags. Cached; "[link]" on failure, so the model knows a URL was there."""
        if not url or len(url) > MAX_URL_LENGTH or self._should_skip_url(url):
            return ""
        cache_key = self._url_cache_key(url)
        if cache_key in self.url_info_cache:
            return self.url_info_cache[cache_key]
        if len(self.url_info_cache) >= 200:
            self.url_info_cache.pop(next(iter(self.url_info_cache)))

        result = ""
        host = url.split('//', 1)[-1].split('/', 1)[0].lower()
        if "bilibili.com" in host or "b23.tv" in host:
            info = await self._fetch_bili_info(url)
            if info:
                result = self._format_bili_line(info)
        elif "youtube.com" in host or "youtu.be" in host:
            result = await self._fetch_oembed_youtube(url)
        else:
            result = await self._fetch_og_meta(url)

        if not result:
            result = "[link]"
        self.url_info_cache[cache_key] = result
        return result

    async def _describe_urls(self, urls: list[str]) -> list[str]:
        """One descriptor per URL, aligned with `urls` ("" for a failure or a
        link still loading at ``LINK_ENRICHMENT_BUDGET_SEC``). Concurrent, so a
        straggler costs only its own descriptor."""
        if not urls:
            return []
        tasks = [asyncio.ensure_future(self._describe_url(u)) for u in urls]
        try:
            done, _ = await asyncio.wait(
                tasks, timeout=self.LINK_ENRICHMENT_BUDGET_SEC)
        finally:
            stragglers = [t for t in tasks if not t.done()]
            for t in stragglers:
                t.cancel()
        if stragglers:
            await asyncio.gather(*stragglers, return_exceptions=True)
            logger.info("[Agent] link enrichment: %d of %d links over the %gs "
                        "budget", len(stragglers), len(urls),
                        self.LINK_ENRICHMENT_BUDGET_SEC)
        out: list[str] = []
        for task in tasks:
            desc = task.result() if task in done else ""
            out.append(desc if desc and desc != "[link]" else "")
        return out

    async def _fetch_oembed_youtube(self, url: str) -> str:
        """YouTube exposes a public oEmbed endpoint with no API key needed."""
        try:
            async with self._http(timeout=5, follow_redirects=True, max_redirects=5) as c:
                r = await c.get(
                    "https://www.youtube.com/oembed",
                    params={"url": url, "format": "json"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r.status_code != 200:
                    return ""
                data = r.json()
                title = (data.get("title") or "").strip()
                author = (data.get("author_name") or "").strip()
                if title:
                    line = f'[YouTube] "{title}"'
                    if author:
                        line += f" — {author}"
                    return line
        except Exception as e:
            logger.debug("[Agent] youtube oembed failed (%s): %s", url, e)
        return ""

    async def _fetch_og_meta(self, url: str) -> str:
        """`[site] "title" description` from the page's own head (web/fetch.head), or "" when it names neither."""
        from .web import fetch
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        # _safe_get: redirects re-checked per hop (SSRF via 302 blocked)
        r = await self._safe_get(url, timeout=5, headers=headers)
        if r is None or r.status_code != 200:
            return ""
        facts = fetch.head(fetch.decoded(r.content, r.headers.get("content-type", "")), str(r.url))[0]
        title, desc = facts["title"][:80], " ".join(facts["description"].split())[:120]
        if not title and not desc:
            return ""
        return f"[{facts['site']}]" + (f' "{title}"' if title else "") + (f" {desc}" if desc else "")

    @staticmethod
    def _url_cache_key(url: str) -> str:
        """Fixed-size key: URLs may contain credentials or megabytes of text."""
        return "url:" + hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _image_cache_key(url: str) -> str:
        """Caption-cache key, hashed: base64:// pseudo-URLs can be megabytes."""
        prefix = "b64:" if url.startswith("base64://") else "url:"
        return prefix + hashlib.sha256(
            url.encode("utf-8", errors="replace"),
        ).hexdigest()

    def _accept_vision_caption(self, url: str, text: str) -> str:
        """The reading of a picture, kept whole as the model wrote it; "" for one too short to say anything."""
        text = (text or "").strip()
        if len(text) >= 4:
            self.image_caption_cache[self._image_cache_key(url)] = text
            self._gc_image_cache()
            logger.info("[Agent] vision (%s): %s", _url_for_log(url), text[:60])
            return text
        logger.info("[Agent] vision gave too little (%s): %r", _url_for_log(url), text)
        return ""

    @staticmethod
    def _gif_first_frame_png(gif_bytes: bytes) -> bytes:
        """A GIF's first frame as PNG, or b"" so the caller falls back to OCR."""
        class _BoundedBuffer(io.BytesIO):
            def write(self, data) -> int:
                if self.tell() + len(data) > VISION_MAX_IMAGE_BYTES:
                    raise ValueError("converted image exceeds byte limit")
                return super().write(data)

        im = None
        try:
            from PIL import Image
            Image.MAX_IMAGE_PIXELS = min(
                Image.MAX_IMAGE_PIXELS or MAX_IMAGE_PIXELS,
                MAX_IMAGE_PIXELS,
            )
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                im = Image.open(io.BytesIO(gif_bytes))
                width, height = im.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    return b""
                im.seek(0)
                out = _BoundedBuffer()
                converted = im.convert("RGB")
                try:
                    converted.save(out, format="PNG")
                finally:
                    if converted is not im:
                        converted.close()
                return out.getvalue()
        except Exception as e:
            logger.debug("[Agent] GIF→PNG failed: %s: %s", type(e).__name__, e)
            return b""

        finally:
            if im is not None:
                im.close()

    def _vision_configured(self) -> bool:
        """Model, key and base URL all set, so no image is uploaded by default."""
        return bool(self.vision_model and self.vision_api_key and self.vision_base_url)

    async def _vision_data_url(self, img_bytes: bytes, mime: str) -> str:
        """Base64 data URL for the vision endpoint (raw URLs trip some providers);
        a GIF goes as its first frame, decoded off the loop, or "" if that fails."""
        if mime == "image/gif":
            img_bytes = await asyncio.to_thread(self._gif_first_frame_png, img_bytes)
            if not img_bytes:
                return ""
            mime = "image/png"
        return f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"

    async def _vision_post(self, url: str, data_url: str) -> tuple[httpx.Response | None, Exception | None]:
        """Ask the vision model at `url` to read one picture; asked again, after VISION_BACKOFF_S and then twice
        that, while it is busy (VISION_RETRY_STATUSES) or the connection fails. A 400 refusing a field is resent
        at once in the spelling the endpoint takes, which `_post_chat` remembers as it does for a reply. The last
        response and the last error; no response when every try raised, or when the endpoint refused the picture
        itself, since what it said without it is no reading of it."""
        payload = {
            "model": self.vision_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": self.VISION_READ_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            "max_tokens": VISION_READ_TOKENS,
            "temperature": 0.3,
        }
        # Hidden reasoning off, and K2 at the one temperature it takes that way.
        self._thinking_off(payload, url, k2_temperature=True)
        r = None
        last_exc = None
        for attempt in range(VISION_ATTEMPTS):
            try:
                r = await self._post_chat(url, self.vision_api_key, payload, timeout=30)
            except Exception as e:
                r, last_exc = None, e
            else:
                if r.status_code not in VISION_RETRY_STATUSES:
                    break
            if attempt < VISION_ATTEMPTS - 1:
                await asyncio.sleep(VISION_BACKOFF_S * 2 ** attempt)
        if not isinstance(payload["messages"][0]["content"], list):
            return None, last_exc
        return r, last_exc

    @staticmethod
    def _vision_text(r: httpx.Response) -> str:
        return (r.json().get("choices", [{}])[0]
                .get("message", {})
                .get("content", "") or "")

    async def _describe_image_vision(self, url: str) -> str | None:
        """A picture read out by the OpenAI-compatible vision endpoint. None means the call failed and may
        succeed later; "" means no usable reading."""
        try:
            endpoint = f"{self.vision_base_url}/chat/completions"
            if ("images", endpoint, self.vision_model) in self._api_quirks:
                # This model has said it takes no pictures.
                return ""
            img_bytes = await self._fetch_image_bytes(url)
            if not img_bytes:
                return None
            if len(img_bytes) < 200:
                logger.debug("[Agent] vision: image too small (%d bytes), skipping", len(img_bytes))
                return ""
            mime = _detect_image_mime(img_bytes)
            # Vision endpoints commonly reject these.
            if mime in ("image/heic", "image/avif"):
                logger.info("[Agent] vision: skip %s", mime)
                return ""
            data_url = await self._vision_data_url(img_bytes, mime)
            if not data_url:
                logger.info("[Agent] vision: skip GIF (first-frame extract failed)")
                return ""
            r, last_exc = await self._vision_post(endpoint, data_url)
            if r is None or r.status_code != 200:
                logger.warning("[Agent] vision HTTP %d: %s (exc=%s)",
                               r.status_code if r else 0,
                               (r.text if r else "")[:200], last_exc)
                return None
            return self._accept_vision_caption(url, self._vision_text(r))
        except Exception as e:
            logger.debug("[Agent] vision failed: %s: %s",
                         type(e).__name__, e)
            return None

    async def _describe_image(self, url: str) -> str:
        """A picture in words, from the vision model; "" without one or when it could not tell."""
        if not url:
            return ""
        cache_key = self._image_cache_key(url)
        if cache_key in self.image_caption_cache:
            return self.image_caption_cache[cache_key]
        if not self._vision_configured():
            return ""
        return await self._describe_image_vision(url) or ""

    def _gc_image_cache(self) -> None:
        _cap_cache(self.image_caption_cache)