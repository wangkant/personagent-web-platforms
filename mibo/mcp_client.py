"""One connection to an MCP server, on the official Python SDK (`mcp`).

The SDK speaks the protocol: stdio (a child process, in a Windows job object or
its own process group, no console window) and Streamable HTTP, the handshake of
every protocol version, paging, and the server's own requests: elicitation (a
form for the person to fill, or a page for them to open) goes to the caller's
`elicit`, which shows it on the card; sampling and roots are not declared and
are refused. What is ours: the program's cleaned environment, its folder and
its stderr kept for the person, one task that holds the session open until it
is closed or the server goes away, and failures named by a short code the page
turns into words.

Everything a server sends is third-party material: text is passed on, pictures
shrunk for a model that takes them, and other content is named.

A remote server that asks for a sign-in gets the SDK's own OAuth client
(OAuthClientProvider: metadata discovery, dynamic registration, PKCE, refresh),
its tokens and registration kept in the system's credential store (KeptTokens).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from . import __version__, secretstore

logger = logging.getLogger("agent.tools")

#: Pages of tools/list read from one server.
MAX_LIST_PAGES = 20
#: Characters of a server's stderr kept to explain why it stopped.
STDERR_TAIL = 1500
#: Characters of a server's `instructions` kept.
INSTRUCTIONS_CHARS = 600
#: Pictures of one result passed on, the long side they are shrunk to, and the largest one read.
PICTURES = 3
PICTURE_SIDE = 1280
PICTURE_BYTES = 20_000_000

# What a server program may inherit besides the SDK's own short list: proxies and
# certificates, and the settings of the runtimes servers are started with.
_ENV_KEEP = frozenset({
    "PATH", "PATHEXT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERNAME", "USER", "LOGNAME",
    "SHELL", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "LANG", "LANGUAGE", "TZ", "TERM", "OS", "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "NODE_PATH", "NVM_DIR", "NVM_HOME",
    "NVM_SYMLINK", "VOLTA_HOME", "PNPM_HOME", "CARGO_HOME", "LD_LIBRARY_PATH"})
_ENV_KEEP_PREFIXES = ("LC_", "XDG_", "UV_", "NPM_CONFIG_")
_SECRET_NAME = re.compile(r"(_KEY|_TOKEN|_SECRET)$|PASSWORD|PASSWD|SECRET|_AUTH|APIKEY")


class McpError(Exception):
    """A failure the page names: `code` is one of not_found, exited, refused,
    timeout, oauth, http, protocol, closed."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    #: Content that was neither text nor a picture kept, by its type ("audio", "image", ...).
    left_out: list = field(default_factory=list)
    #: Pictures as data URLs, shrunk (picture()).
    pictures: list = field(default_factory=list)


def uv_dirs() -> list[Path]:
    """Where uv's own installer puts it when it is not on PATH."""
    dirs = [Path.home() / ".local" / "bin"]
    if os.name == "nt":
        dirs.append(Path.home() / ".cargo" / "bin")
    return dirs


def program_env(own: Optional[dict] = None) -> dict:
    """What a server program starts with: what a program needs to run and find
    its tools here, never this app's own settings or anything named like a
    secret, plus the server's own `env`."""
    from .settingsfile import FROM_SETTINGS

    out = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if name in FROM_SETTINGS or upper in FROM_SETTINGS or _SECRET_NAME.search(upper):
            continue
        if upper in _ENV_KEEP or upper.startswith(_ENV_KEEP_PREFIXES):
            out[name] = value
    out.update(own or {})
    return out


def find_program(command: str, env: Optional[dict] = None, cwd: Optional[str] = None) -> str:
    """The full path of `command`, or "" when it cannot be found. uv and uvx
    are also looked for where the installers put them; a relative path is
    taken from `cwd`."""
    if not command:
        return ""
    if cwd and not Path(command).is_absolute() and ("/" in command or "\\" in command):
        command = str(Path(cwd) / command)
    found = shutil.which(command, path=(env or {}).get("PATH") if env else None)
    if found:
        return found
    if Path(command).name in ("uv", "uvx") and not Path(command).parent.name:
        for folder in uv_dirs():
            found = shutil.which(command, path=str(folder))
            if found:
                return found
    return ""


_CMD_META = re.compile(r'([()\][%!^"`<>&|;, *?])')
# The child's own variable holding its command line, read by cmd.exe (see batch_launch).
BATCH_LINE = "MIBO_BATCH_LINE"


def _batch_arg(arg: str) -> str:
    """One argument as cmd.exe hands it to a batch file's %*: quoted for the program that reads it, then
    every cmd metacharacter escaped twice, once for the line and once for the batch file's own reading."""
    if any(c in arg for c in "\r\n\0"):
        raise McpError("not_found", "a batch program cannot take an argument that spans lines")
    quoted = re.sub(r'(\\*)"', r'\1\1\\"', arg)
    quoted = '"' + re.sub(r"(\\*)$", r"\1\1", quoted) + '"'
    return _CMD_META.sub(r"^\1", _CMD_META.sub(r"^\1", quoted))


def batch_launch(program: str, args: list, env: dict) -> tuple[str, list, dict]:
    """A .cmd or .bat shim (npx, most npm servers) run through cmd.exe with its arguments intact, `&`, `|`, `%`
    and quotes included. The SDK hands a list to CreateProcess, which cmd.exe would read with its own rules,
    so the escaped line goes in a variable of the child's and cmd.exe is given only its name."""
    comspec = env.get("COMSPEC") or os.path.join(env.get("SYSTEMROOT") or "C:\\Windows", "System32", "cmd.exe")
    line = " ".join([_CMD_META.sub(r"^\1", program), *(_batch_arg(str(a)) for a in args)])
    return comspec, ["/d", "/s", "/c", f"%{BATCH_LINE}%"], {**env, BATCH_LINE: line}


def picture(data: str) -> str:
    """A server's base64 picture as a JPEG or PNG data URL at most PICTURE_SIDE pixels on its long side, the size
    the app sends pictures to a model at; "" when it is not a picture."""
    from PIL import Image
    try:
        raw = base64.b64decode(data, validate=True)
        if len(raw) > PICTURE_BYTES:
            return ""
        with Image.open(io.BytesIO(raw)) as image:
            image.seek(0)
            image.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
            clear = image.mode in ("RGBA", "LA", "P")
            image = image.convert("RGBA" if clear else "RGB")
            out = io.BytesIO()
            image.save(out, "PNG" if clear else "JPEG", quality=80)
    except (ValueError, OSError, Image.DecompressionBombError):
        return ""
    return f"data:image/{'png' if clear else 'jpeg'};base64,{base64.b64encode(out.getvalue()).decode()}"


def result_of(raw: dict) -> ToolResult:
    """A tools/call result: its text, and up to PICTURES of its pictures shrunk for a model that takes them;
    anything else is named, not passed on."""
    texts, pictures, left_out = [], [], []
    for item in raw.get("content") or ():
        kind = item.get("type") if isinstance(item, dict) else None
        # A picture comes as image content, or as a resource holding an image's bytes.
        held = (item.get("resource") or {}) if kind == "resource" else item
        data = held.get("data" if kind == "image" else "blob") if kind in ("image", "resource") else None
        shown = picture(data) if isinstance(data, str) and str(held.get("mimeType") or "").startswith("image/") \
            and len(pictures) < PICTURES else ""
        if kind == "text" and isinstance(item.get("text"), str):
            texts.append(item["text"])
        elif kind == "resource" and isinstance(held.get("text"), str):
            texts.append(held["text"])
        elif shown:
            pictures.append(shown)
        else:
            left_out.append(str(kind or "unknown")[:32])
    structured = raw.get("structuredContent")
    if not texts and structured is not None:
        # The spec asks servers to send it as text as well; some do not.
        texts.append(json.dumps(structured, ensure_ascii=False))
    return ToolResult("\n".join(texts), bool(raw.get("isError")), left_out, pictures)


class _ReadWatch:
    """The session's read stream, which sets `closed` when the server's side ends."""

    def __init__(self, stream, closed: asyncio.Event) -> None:
        self._stream, self._closed = stream, closed

    async def receive(self):
        import anyio
        try:
            return await self._stream.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError):
            self._closed.set()
            raise

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        self._closed.set()
        return await self._stream.__aexit__(*exc)

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextlib.asynccontextmanager
async def _watched(transport, closed: asyncio.Event):
    async with transport as (read, write):
        yield _ReadWatch(read, closed), write


def _flat(error: BaseException) -> BaseException:
    """The first real failure inside an exception group (anyio's task groups raise them)."""
    while getattr(error, "exceptions", None):
        error = error.exceptions[0]
    return error


class Connection:
    """One server, held open by a task of its own: a program (`command`, `args`, `env`, `cwd`) or an address
    (`url`, `headers`). `closed` is set when it ends for any reason; `changed` when it says its tools changed."""

    def __init__(self, spec: dict, *, folder: Path, auth=None, elicit=None) -> None:
        self.spec = spec
        self.folder = Path(folder)
        #: The OAuth client of a remote server that signs in (oauth_client), or None.
        self.auth = auth
        #: Answers what the server asks the person during a call: async (the request as a dict) -> the
        #: ElicitResult as a dict. Without it elicitation is not declared.
        self.elicit = elicit
        #: Seconds of the call under way spent waiting for the person, which its time limit does not count.
        self.waited = 0.0
        self.asking = False
        self.closed = asyncio.Event()
        self.changed = asyncio.Event()
        self.client = None
        self.instructions = ""
        self.server_name = ""
        self.last_status = 0
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._errlog = None

    @property
    def stderr_path(self) -> Path:
        return self.folder / "stderr.log"

    def stderr_tail(self) -> str:
        try:
            text = self.stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[-STDERR_TAIL:]

    def _transport(self, stack: contextlib.AsyncExitStack):
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        if self.spec.get("command"):
            env = program_env(self.spec.get("env"))
            cwd = self.spec.get("cwd") or str(self.folder)
            program = find_program(self.spec["command"], env, cwd)
            if not program:
                raise McpError("not_found", self.spec["command"])
            self.folder.mkdir(parents=True, exist_ok=True)
            self._errlog = open(self.stderr_path, "w", encoding="utf-8", errors="replace")
            stack.callback(self._errlog.close)
            args = list(self.spec.get("args") or [])
            if os.name == "nt" and Path(program).suffix.lower() in (".cmd", ".bat"):
                program, args, env = batch_launch(program, args, env)
            params = StdioServerParameters(command=program, args=args, env=env, cwd=cwd,
                                           encoding_error_handler="replace")
            return stdio_client(params, errlog=self._errlog)

        headers = dict(self.spec.get("headers") or {})
        if self.spec.get("type") == "sse":
            from mcp.client.sse import sse_client
            return sse_client(self.spec["url"], headers=headers, auth=self.auth)

        async def note(response) -> None:
            self.last_status = response.status_code
        client = create_mcp_http_client(headers=headers, auth=self.auth)
        client.event_hooks["response"].append(note)
        stack.push_async_callback(client.aclose)
        return streamable_http_client(self.spec["url"], http_client=client)

    async def _on_message(self, message) -> None:
        import mcp_types as types
        if isinstance(message, types.ServerNotification) and getattr(message, "method", "") == \
                "notifications/tools/list_changed":
            self.changed.set()

    async def _on_elicit(self, context, params):
        """The server asks the person for something (a form, or a page to open): `elicit` answers."""
        import mcp_types as types
        began, self.asking = time.monotonic(), True
        try:
            answer = await self.elicit(params.model_dump(by_alias=True, mode="json", exclude_none=True))
        finally:
            self.asking = False
            self.waited += time.monotonic() - began
        return types.ElicitResult(**answer)

    async def _hold(self, ready: asyncio.Future) -> None:
        from mcp import Client
        from mcp_types import Implementation
        try:
            async with contextlib.AsyncExitStack() as stack:
                transport = _watched(self._transport(stack), self.closed)
                client = Client(transport, mode="auto", cache=None, message_handler=self._on_message,
                                elicitation_callback=self._on_elicit if self.elicit else None,
                                client_info=Implementation(name="Mibo", version=__version__))
                await stack.enter_async_context(client)
                self.client = client
                self.instructions = str(client.instructions or "")[:INSTRUCTIONS_CHARS]
                self.server_name = str(getattr(client.server_info, "name", "") or "")[:80]
                ready.set_result(None)
                stop = asyncio.ensure_future(self._stop.wait())
                gone = asyncio.ensure_future(self.closed.wait())
                try:
                    await asyncio.wait({stop, gone}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    stop.cancel()
                    gone.cancel()
        except BaseException as e:
            if not ready.done():
                ready.cancel() if isinstance(e, asyncio.CancelledError) else ready.set_exception(e)
            elif not isinstance(e, asyncio.CancelledError):
                logger.debug("[Tools] a session ended: %r", _flat(e))
        finally:
            self.client = None
            self.closed.set()

    async def open(self, timeout: float) -> None:
        """Start the program or reach the address, and finish the handshake; raises McpError."""
        ready = asyncio.get_running_loop().create_future()
        # Read once here, so a failure that lands after a stop is not reported as never read.
        ready.add_done_callback(lambda f: f.cancelled() or f.exception())
        self._task = asyncio.ensure_future(self._hold(ready))
        try:
            await asyncio.wait_for(asyncio.shield(ready), timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise McpError("timeout", f"{timeout:g} s") from None
        except BaseException as e:
            await self.close()
            raise self.failure(e) from None

    def failure(self, error: BaseException) -> McpError:
        """Any failure as an McpError, with the server's own words where there are some."""
        error = _flat(error)
        if isinstance(error, McpError):
            return error
        from mcp.client.auth import OAuthFlowError, OAuthRegistrationError
        from mcp.shared.exceptions import MCPError
        if isinstance(error, OAuthRegistrationError):
            return McpError("oauth", "registration")
        if isinstance(error, OAuthFlowError):
            return McpError("oauth", str(error)[:300])
        status = self.last_status
        has_key = any(k.lower() == "authorization" for k in self.spec.get("headers") or {})
        if (status == 401 or status == 403) and not has_key:
            return McpError("oauth", str(status))
        if status >= 400:
            return McpError("http", str(status))
        name = type(error).__name__
        if self.spec.get("command"):
            tail = self.stderr_tail()
            if isinstance(error, FileNotFoundError):
                return McpError("not_found", self.spec["command"])
            if tail or isinstance(error, (MCPError, OSError)) or "Closed" in name:
                return McpError("exited", tail or str(error)[:300])
        if "Connect" in name or isinstance(error, ConnectionError):
            return McpError("refused", str(error)[:300])
        if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            return McpError("timeout", "")
        if isinstance(error, MCPError):
            return McpError("protocol", str(error)[:300])
        logger.debug("[Tools] unexpected failure: %r", error, exc_info=error)
        return McpError("protocol", name)

    async def list_tools(self, timeout: float, limit: int) -> list[dict]:
        """Every tool the server lists, page by page, up to `limit`; plain dicts as the wire has them."""
        tools: list[dict] = []
        cursor = None
        for _ in range(MAX_LIST_PAGES):
            page = await asyncio.wait_for(self._client().list_tools(cursor=cursor), timeout)
            tools += [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in page.tools]
            cursor = page.next_cursor
            if not cursor or len(tools) >= limit:
                break
        self.changed.clear()
        return tools[:limit]

    async def call_tool(self, name: str, arguments: dict, timeout: float) -> ToolResult:
        """One call, given `timeout` seconds besides the time the person spends on what the server asks them."""
        self.waited = 0.0
        call = asyncio.ensure_future(self._client().call_tool(name, arguments))
        began = time.monotonic()
        try:
            while not call.done():
                left = began + timeout + self.waited - time.monotonic()
                if left <= 0 and not self.asking:
                    raise McpError("timeout", f"{timeout:g} s")
                await asyncio.wait({call}, timeout=max(left, 0.5))
            raw = call.result()
        except McpError:
            raise
        except Exception as e:
            from mcp.shared.exceptions import MCPError
            if isinstance(_flat(e), MCPError) and not self.closed.is_set():
                # The server answered the call with an error of its own: the call failed, not the server.
                return ToolResult(str(_flat(e))[:1000], True)
            raise self.failure(e) from None
        finally:
            call.cancel()
        # Pictures are shrunk off the loop.
        return await asyncio.to_thread(result_of, raw.model_dump(by_alias=True, mode="json", exclude_none=True))

    def _client(self):
        if self.client is None:
            raise McpError("closed")
        return self.client

    async def close(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), 8)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
        self.closed.set()


# ------------------------------------------------------------------ sign-in --

class KeptTokens:
    """The SDK's TokenStorage for one server, as one entry of the credential store:
    {"tokens", "expires_at", "client", "revoke"}."""

    def __init__(self, home: Path, server: str, redirect: str = "") -> None:
        self.home, self.key, self.redirect = home, f"MIBO_OAUTH_{server}", redirect

    def read(self) -> dict:
        return json.loads(secretstore.get(self.home, self.key) or "{}")

    def _write(self, **changes) -> None:
        secretstore.put(self.home, self.key, json.dumps({**self.read(), **changes}))

    def clear(self) -> None:
        secretstore.delete(self.home, self.key)

    def signed_in(self) -> bool:
        return bool(self.read().get("tokens"))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        tokens = self.read().get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens) -> None:
        self._write(tokens=tokens.model_dump(mode="json", exclude_none=True),
                    expires_at=time.time() + tokens.expires_in if tokens.expires_in else None)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        client = self.read().get("client")
        # A registration made for another callback address is made again.
        if not client or (self.redirect and self.redirect not in (client.get("redirect_uris") or [])):
            return None
        return OAuthClientInformationFull.model_validate(client)

    async def set_client_info(self, client_info) -> None:
        self._write(client=client_info.model_dump(mode="json", exclude_none=True))

    def keep_revocation(self, provider) -> None:
        """Where the grant is ended on signing out, from the metadata the sign-in found."""
        metadata = provider.context.oauth_metadata
        if metadata is not None and metadata.revocation_endpoint:
            self._write(revoke=str(metadata.revocation_endpoint))


def oauth_client(url: str, storage: KeptTokens, redirect_handler=None, callback_handler=None):
    """The SDK's OAuth client for `url`. Without handlers, a server that wants a new sign-in fails (the SDK
    refuses an authorization with no handler) instead of opening anything: only a sign-in the person
    started opens a browser."""
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    class Provider(OAuthClientProvider):
        async def _initialize(self) -> None:
            await super()._initialize()
            # The SDK does not keep when a stored token expires: without it, a token from before a restart
            # is sent stale and ends in a new sign-in where a refresh would do.
            self.context.token_expiry_time = storage.read().get("expires_at")

    # Kept tokens always come with the registration they were made for.
    redirect = storage.redirect or storage.read()["client"]["redirect_uris"][0]
    metadata = OAuthClientMetadata(client_name="Mibo", redirect_uris=[redirect],
                                   grant_types=["authorization_code", "refresh_token"], response_types=["code"])
    return Provider(url, metadata, storage, redirect_handler=redirect_handler, callback_handler=callback_handler)


async def revoke(record: dict) -> bool:
    """Asks the server to end the grant (RFC 7009), the refresh token first; True when it agreed."""
    import httpx
    url, client, tokens = record.get("revoke"), record.get("client") or {}, record.get("tokens") or {}
    if not url or not tokens:
        return False
    agreed = True
    async with httpx.AsyncClient(timeout=10) as http:
        for hint in ("refresh_token", "access_token"):
            if not tokens.get(hint):
                continue
            data, headers = {"token": tokens[hint], "token_type_hint": hint,
                             "client_id": client.get("client_id", "")}, {}
            secret = client.get("client_secret")
            if secret and client.get("token_endpoint_auth_method") == "client_secret_basic":
                pair = f"{quote(data['client_id'], safe='')}:{quote(secret, safe='')}"
                headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
            elif secret:
                data["client_secret"] = secret
            try:
                answer = await http.post(url, data=data, headers=headers)
            except httpx.HTTPError:
                return False
            agreed = agreed and answer.status_code == 200
    return agreed


def describe(error: BaseException) -> tuple[str, str]:
    """(code, detail) of any failure to reach a server."""
    error = _flat(error)
    if isinstance(error, McpError):
        return error.code, error.detail
    if isinstance(error, asyncio.TimeoutError):
        return "timeout", ""
    return "protocol", type(error).__name__
