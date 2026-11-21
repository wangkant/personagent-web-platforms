"""Calling the models: clients, endpoints, retries, fallback and probes."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import openai

from . import abilities, context, modelcatalog, schedule_tool, spend, transport
from .endpoints import (PROBLEMS, adapt_rejected_payload, chat_completions_url, endpoint_for,
                        model_problem, without_pictures)
from .usage import USAGE_FILE, UsageBook

logger = logging.getLogger("agent")

# max_tokens for `_call_llm` that means the answer's room in the model's budget (context.budget), thinking included.
FROM_BUDGET = 0

# What the calls in this task are for, as counted in the usage tally
# (usage.PURPOSES); a context, so no fake `_call_llm` needs a new argument.
_call_purpose: contextvars.ContextVar[str] = contextvars.ContextVar(
    "model_call_purpose", default="other")


@contextmanager
def purpose(label: str):
    token = _call_purpose.set(label)
    try:
        yield
    finally:
        _call_purpose.reset(token)


# Whether a model reasons and how far it can be lowered is the catalogue's (modelcatalog.py) or learned; the
# spelling of the switch is not in the catalogue. These hosts spell it as Qwen's `enable_thinking` (DashScope,
# SiliconFlow, Qianfan, Tencent TokenHub): a host that ignores DeepSeek's `thinking` looks to the check as if it
# took it, so this spelling cannot be learned from an answer.
_ENABLE_THINKING_HOSTS = ("aliyuncs.com", "siliconflow.cn", "siliconflow.com",
                          "baidubce.com", "tencentmaas.com", "tencentcloudmaas.com")

# Words in an error that put the account, not the request, at fault.
_BILLING_WORDS = ("payment required", "insufficient", "balance", "quota",
                  "billing", "arrear", "good standing", "overdue", "欠费", "余额不足",
                  "资源包")


def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def _on_host(host: str, domains: tuple) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def thinking_off(payload: dict, url: str, takes_thinking, lowest: str = "") -> dict:
    """Ask the model behind `url` to skip hidden reasoning, in the spellings
    that endpoint accepts. `takes_thinking` is what is known of it and
    DeepSeek's `thinking` switch; None sends the switch to find out, so only
    a caller that survives its refusal (`_post_chat`) passes None. `lowest` is
    the effort that lowers a model with no switch (modelcatalog.lowest_effort)."""
    router = "openrouter.ai" in (url or "")
    qwen_host = _on_host(_host(url), _ENABLE_THINKING_HOSTS)
    if qwen_host:
        payload["enable_thinking"] = False
    elif takes_thinking is not False and not router:
        payload["thinking"] = {"type": "disabled"}
    # A vendor/model name on another host is most often a gateway in front of a router (LiteLLM, a local
    # pass-through to OpenRouter), which ignores `thinking`: it gets the router's switch as well.
    if router or (not qwen_host and "/" in str(payload.get("model") or "")):
        # OpenRouter's own switch for every model; the exclude/max_tokens form is 400-rejected by some upstreams.
        payload["reasoning"] = {"enabled": False}
    if lowest and not router:
        payload["reasoning_effort"] = lowest
    return payload


# Reasoning a model writes into its answer (MiniMax without reasoning_split,
# DeepSeek-R1 and Qwen3 on a local server without a reasoning parser).
_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


def answer_text(message: dict) -> str:
    """The answer's words: a content list (Mistral's reasoning models send
    thinking and text parts) read for its text, a leading <think> block dropped."""
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(str(p.get("text") or "") for p in content
                          if isinstance(p, dict) and p.get("type") == "text")
    return _THINK_RE.sub("", str(content)).strip()


def _provider_message(e: BaseException) -> str:
    """The provider's own message, code and type from an error body (the SDK's `body`: the `error` object, or
    the whole body where a host sends no such object, as SiliconFlow does)."""
    body = getattr(e, "body", None)
    if isinstance(body, str):
        # `{"error": "..."}` is the provider's message; a plain-text body may echo the request.
        return body[:300] if e.response.text.lstrip().startswith("{") else ""
    if not isinstance(body, dict):
        return ""
    return " ".join(str(body.get(k) or "") for k in ("message", "code", "type"))[:300]


def _error_text(e: BaseException) -> str:
    """The error's words, lowercased: the whole body only on a status about the account, the provider's message
    on a refused request (where Gemini says the key is wrong and Claude or DashScope that the balance is gone),
    and nothing of a 5xx body, which describes an outage."""
    if not isinstance(e, openai.APIStatusError):
        return str(e).lower()
    status = e.status_code
    words = ""
    if status in (401, 402, 403, 429):
        words = e.response.text[:500]
    elif status in (400, 404, 422):
        words = _provider_message(e)
    return f"http {status} {words}".lower()


class ProviderError(Exception):
    """An error a provider sent inside a 200, such as OpenRouter's upstream
    failure; `status_code` routes it as the same HTTP status would."""

    def __init__(self, body: dict) -> None:
        error = body.get("error")
        error = error if isinstance(error, dict) else {"message": str(error or "")}
        code = error.get("code")
        try:
            self.status_code = int(code) if code is not None else None
        except (TypeError, ValueError):
            self.status_code = None
        message = str(error.get("message") or "")[:300] or "the answer had no choices"
        super().__init__(f"provider error {code}: {message}" if code is not None
                         else f"provider error: {message}")


class ModelCalls:
    # The context window the person set (CONTEXT_WINDOW); 0 when they did not.
    context_window = 0

    def _retries(self, model: str) -> int:
        """How many times the SDK asks `model` again after a 429, a 5xx or a dropped connection: none where a
        distinct fallback model takes the failure at once (`_recovered`), else the person's LLM_MAX_RETRIES."""
        return 0 if self.llm_fallback_model and model != self.llm_fallback_model else self.api_max_retries

    def _endpoint_for(self, model: str) -> tuple[str, str]:
        """(chat-completions URL, API key) for one model name; the fallback may
        have its own endpoint. Read per call, since callers reassign them."""
        base, key = endpoint_for(
            model, primary_model=self.model, fallback_model=self.llm_fallback_model,
            base_url=self.base_url, api_key=self.api_key,
            fallback_base_url=self.llm_fallback_base_url,
            fallback_api_key=self.llm_fallback_api_key)
        return chat_completions_url(base), key

    @property
    def _api_quirks(self) -> dict:
        """What each endpoint has shown it refuses or takes, learned per
        process: ("thinking", url, model) -> bool, and (field, url, model) ->
        True for a `max_tokens` or `temperature` it refused."""
        quirks = self.__dict__.get("_api_quirks_map")
        if quirks is None:
            quirks = self.__dict__["_api_quirks_map"] = {}
        return quirks

    def _thinking_verdict(self, url: str, model: str):
        """Whether `model` at `url` takes DeepSeek's `thinking` switch: True,
        False, or None when unknown (others 400 it rather than ignore it). What
        the endpoint answered wins; then the catalogue: a switch for a model with
        one, none for a model that does not reason or is lowered by effort."""
        verdict = self._api_quirks.get(("thinking", url, model))
        if verdict is not None:
            return verdict
        line = modelcatalog.entry(url, model)
        if line is not None:
            return modelcatalog.toggles(line)
        if (self.llm_fallback_thinking
                and _host(url) == _host(self.llm_fallback_base_url or self.base_url)):
            return True
        return None

    def _thinking_off(self, payload: dict, url: str) -> dict:
        """`thinking_off` with what this endpoint has shown so far; an unknown
        one is sent `thinking`, and `_post_chat` remembers its answer."""
        model = str(payload.get("model") or "")
        return thinking_off(payload, url, self._thinking_verdict(url, model),
                            modelcatalog.lowest_effort(modelcatalog.entry(url, model)))

    def _apply_quirks(self, url: str, payload: dict) -> None:
        """Spell `payload` the way `url` has shown it accepts."""
        quirks = self._api_quirks
        # What the abilities check and the other process learned (abilities.py).
        abilities.seed(self, quirks)
        model = str(payload.get("model") or "")
        if quirks.get(("thinking", url, model)) is False:
            payload.pop("thinking", None)
        if ("max_tokens", url, model) in quirks and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if ("temperature", url, model) in quirks or modelcatalog.fixed_temperature(url, model):
            payload.pop("temperature", None)
        if ("reasoning", url, model) in quirks and payload.get("reasoning") == {"enabled": False}:
            payload["reasoning"] = {"effort": "minimal"}
        if ("reasoning_switch", url, model) in quirks and payload.get("reasoning") == {"enabled": False}:
            payload.pop("reasoning")
        if ("images", url, model) in quirks:
            without_pictures(payload)
        if ("response_format", url, model) in quirks:
            payload.pop("response_format", None)
        if ("enable_thinking", url, model) in quirks:
            payload.pop("enable_thinking", None)
        if ("reasoning_effort_none", url, model) in quirks and payload.get("reasoning_effort") == "none":
            payload["reasoning_effort"] = "low"
        if ("reasoning_effort", url, model) in quirks:
            payload.pop("reasoning_effort", None)

    def _reply_effort(self, url: str, model: str):
        """(level, parameter) when this reply call should think at the level chosen
        in the composer (abilities.py); None leaves thinking as the call asks."""
        if _call_purpose.get() != "reply":
            return None
        return abilities.reply_effort(abilities.runtime_of(self), url, model)

    def _setting(self, model: str) -> int:
        """The person's CONTEXT_WINDOW as `model`'s window: every model's but a fallback served apart
        (endpoints.endpoint_for)."""
        return 0 if model == self.llm_fallback_model and model != self.model else self.context_window

    def _budget(self, url: str, model: str, effort=None, fixed: int = 0) -> context.Budget:
        """The request budget of `model` at `url` (context.py), replies thinking at `effort`'s level, with `fixed`
        tokens of system prompt and tool definitions."""
        window, _ = context.known_window(self, url, model, self._setting(model))
        return context.budget(window, effort[0] if effort else "", fixed)

    def _shows_pictures(self) -> bool:
        """Whether a turn's pictures go to the reply model itself: not while a
        vision model captions them (VISION_*), nor to one that refused them."""
        if self._vision_configured():
            return False
        url, _ = self._endpoint_for(self.llm_dm_model)
        return ("images", url, self.llm_dm_model) not in self._api_quirks

    def _remember_refusal(self, url: str, model: str, field: str) -> None:
        self._api_quirks[(field, url, model)] = False if field == "thinking" else True
        abilities.note_quirk(self, url, model, field, self._api_quirks[(field, url, model)])
        instead = {"max_tokens": "sending max_completion_tokens instead",
                   "reasoning": "asking for the lowest effort instead",
                   "images": "sending the words alone",
                   "tools": "answering without them",
                   "response_format": "asking for JSON in the prompt alone",
                   "reasoning_effort_none": "asking for low effort instead"}.get(field, "leaving it out")
        logger.info("[Agent] %s does not take `%s` for model %s; %s from now on",
                    _host(url) or url, field, model, instead)

    async def _post_chat(self, url: str, key: str, payload: dict, *,
                         timeout: float, retries: int = 0) -> dict:
        """One chat completion through the SDK: the answer's body as a dict, or the SDK's APIStatusError. A 400
        refusing a field (`thinking`, `max_tokens`, `temperature`, or `reasoning` off) is resent without it, and
        the endpoint's answer remembered; `retries` is the SDK's own (transport.client)."""
        self._apply_quirks(url, payload)
        model = str(payload.get("model") or "")
        asking = ("thinking" in payload
                  and self._thinking_verdict(url, model) is None)
        refused: list[str] = []
        # OpenRouter answers 404 ("no endpoints found that support image input") for pictures to a model without eyes.
        pictured = any(isinstance(m.get("content"), list) for m in payload.get("messages") or [])
        completions = transport.client(url, key, retries).chat.completions
        for tries_left in range(3, -1, -1):
            try:
                body = (await completions.create(**transport.args(payload), timeout=timeout)).to_dict()
                break
            except openai.APIStatusError as e:
                # It answers 404 the same way when no host of the model takes JSON mode.
                if e.status_code not in (400, 422) and not (
                        e.status_code == 404 and (pictured or "response_format" in payload)):
                    raise
                field = adapt_rejected_payload(payload, e.response.text)
                if field:
                    # The service named the field it refuses: known from now on, to a call made beside this one too.
                    self._remember_refusal(url, model, field)
                elif asking and "thinking" in payload and model_problem(e.status_code, e.response.text) != "context":
                    # An endpoint being asked may refuse it in words of its own: a guess, kept once the resend is
                    # answered. A request past the window refused no field: the turn fits it again (loop._reply).
                    payload.pop("thinking")
                    field = "thinking"
                    refused.append(field)
                if not field or not tries_left:
                    raise
        self._count_usage(model, body)
        for field in refused:
            self._remember_refusal(url, model, field)
        if asking and "thinking" in payload:
            self._api_quirks[("thinking", url, model)] = True
        # Working again, so a later failure is worth a line again.
        reported = self._fatal_reported_hosts()
        for item in [k for k in reported if k[0] == _host(url)]:
            reported.discard(item)
        if model == getattr(self, "model", None):
            self.__dict__["model_unreachable"] = False
        return body

    async def _tool_round(self, system: str, messages: list[dict], model: str,
                          tools: list[dict], *, timeout: float, choice: str = "auto") -> dict | None:
        """One reply-model call that may call `tools`, with no response_format
        (with it, models stop calling tools), thinking at the person's effort
        where the model was measured to think (abilities.reply_effort), and room
        for the answer from the model's budget (context.budget): the answer's
        message. A reply's reported prompt tokens feed the count (context.measured). A call that fails is retried and moved to the fallback model
        as every model call is (`_recovered`), and raises when that fails too.
        None when the endpoint takes no tool calls (`_refuses_tools`) or model
        spending is paused.
        `choice` "required": a call must be made, where the endpoint takes it;
        "none": no call may be, and an endpoint that refuses it is asked without
        tools."""
        if spend.paused_for(self, _call_purpose.get()):
            return None
        began = time.monotonic()

        async def ask(mdl: str, room: int = 0) -> dict | None:
            url, key = self._endpoint_for(mdl)
            if ("tools", url, mdl) in self._api_quirks:
                return None
            effort = self._reply_effort(url, mdl)
            payload = {"model": mdl, "max_tokens": room or self._budget(url, mdl, effort).answer, "tools": tools,
                       "tool_choice": "auto" if ("tool_choice", url, mdl) in self._api_quirks else choice,
                       "messages": ([{"role": "system", "content": system}] if system else []) + list(messages)}
            if effort:
                abilities.thinking_on(payload, url, *effort)
            else:
                self._thinking_off(payload, url)

            async def post() -> tuple[dict | None, openai.APIStatusError | None]:
                # The body, or the refusal of this request (400, 404, 422) to look at.
                try:
                    return await self._post_chat(url, key, payload, timeout=timeout, retries=self._retries(mdl)), None
                except openai.APIStatusError as e:
                    if e.status_code not in (400, 404, 422):
                        raise
                    return None, e

            body, refusal = await post()
            if refusal and choice == "none":
                # The last call of a turn: the same request without tools.
                payload.pop("tools")
                payload.pop("tool_choice")
                body, refusal = await post()
            elif refusal and payload["tool_choice"] != "auto":
                # Asked again with "auto", the round's own note saying it in words; taken that way, it is
                # the required call this endpoint refuses.
                payload["tool_choice"] = "auto"
                body, refusal = await post()
                if body is not None:
                    self._remember_refusal(url, mdl, "tool_choice")
            if refusal:
                if (re.search(r"tool|function", refusal.response.text[:300], re.IGNORECASE)
                        and await self._refuses_tools(url, key, mdl, timeout)):
                    self._remember_refusal(url, mdl, "tools")
                    return None
                raise refusal
            if body.get("error") or not body.get("choices"):
                raise ProviderError(body)
            if _call_purpose.get() == "reply":
                context.measured(self, url, mdl, payload, body.get("usage"))
            return body

        body, used = await self._recovered(ask, model)
        if body is not None and body["choices"][0].get("finish_reason") == "length":
            # Cut off at the token cap: asked once more with four times the room, within what the window leaves.
            url = self._endpoint_for(used)[0]
            plan = self._budget(url, used, self._reply_effort(url, used))
            room = plan.retry(int((body.get("usage") or {}).get("prompt_tokens") or 0))
            try:
                body = (await ask(used, room) if room > plan.answer else None) or body
            except Exception as e:
                logger.warning("[Agent] tool round not asked again with more room (model=%s): %s",
                               used, type(e).__name__)
            if body["choices"][0].get("finish_reason") == "length":
                logger.warning("[Agent] tool round answer cut off at the model's token cap (model=%s)", used)
        if body is None:
            return None
        message = body["choices"][0].get("message") or {}
        if _call_purpose.get() == "reply":
            abilities.note_reasoning(abilities.reasoning_of(message), time.monotonic() - began)
        return {**message, "content": answer_text(message)}

    async def _refuses_tools(self, url: str, key: str, model: str, timeout: float) -> bool:
        """Whether the endpoint refuses a tool in the plainest request: one message, one
        tool, no history. A turn's own request can be refused for what that turn carries
        (a malformed tool message), which says nothing of the endpoint."""
        payload = {"model": model, "max_tokens": 16, "tools": [abilities._TOOL],
                   "messages": [{"role": "user", "content": "hi"}]}
        try:
            await self._post_chat(url, key, payload, timeout=timeout)
        except openai.APIStatusError as e:
            return (e.status_code in (400, 404, 422)
                    and bool(re.search(r"tool|function", e.response.text[:300], re.IGNORECASE)))
        return False

    def _count_usage(self, model: str, body: dict) -> None:
        """Add one answered request to the per-model tally the page shows; a
        tally that cannot be kept never costs the call."""
        memory_file = getattr(self, "memory_file", None)
        if not memory_file:
            return
        book = self.__dict__.get("_usage_book")
        if book is None:
            book = self.__dict__["_usage_book"] = UsageBook(
                Path(memory_file).with_name(USAGE_FILE))
        now = time.time()
        book.add(model, body.get("usage"), now, _call_purpose.get())
        # A scheduled run's own tally, for its record (schedule_tool.py).
        schedule_tool.count(model, body.get("usage"))
        wait = book.due(now)
        try:
            if not wait:
                book.flush_soon(now)
            elif not self.__dict__.get("_usage_flush"):
                def later() -> None:
                    self.__dict__["_usage_flush"] = None
                    try:
                        book.flush_soon(time.time())
                    except (OSError, RuntimeError) as e:
                        logger.debug("[Agent] model use tally not written: %s", e)
                self.__dict__["_usage_flush"] = asyncio.get_running_loop().call_later(wait, later)
        except (OSError, RuntimeError) as e:
            logger.debug("[Agent] model use tally not written: %s", e)

    def _fatal_reported_hosts(self) -> set:
        reported = self.__dict__.get("_fatal_reported")
        if reported is None:
            reported = self.__dict__["_fatal_reported"] = set()
        return reported

    def _report_fatal(self, e: BaseException, model: str) -> None:
        """One operator line per provider when the key or the account is the
        problem, naming the settings that fix it."""
        url, _key = self._endpoint_for(model)
        host = _host(url) or url
        status = getattr(e, "status_code", None)
        text = _error_text(e)
        billing = status == 402 or any(w in text for w in _BILLING_WORDS)
        kind = "billing" if billing else "auth"
        reported = self._fatal_reported_hosts()
        if (host, kind) in reported:
            return
        reported.add((host, kind))
        on_fallback = (model == self.llm_fallback_model and model != self.model
                       and bool(self.llm_fallback_base_url or self.llm_fallback_api_key))
        key_name, url_name = (("LLM_FALLBACK_API_KEY", "LLM_FALLBACK_BASE_URL")
                              if on_fallback else ("LLM_API_KEY", "LLM_BASE_URL"))
        if billing:
            logger.error(
                "[Agent] the model provider at %s refused the call: the "
                "account's balance or quota is used up (HTTP %s). Top it up, "
                "or set %s / %s to another account.",
                host, status or "?", key_name, url_name)
        else:
            logger.error(
                "[Agent] the model provider at %s rejected the API key (HTTP "
                "%s). Check %s, and that %s is that provider's address.",
                host, status or "?", key_name, url_name)

    @staticmethod
    def _classify_api_error(e: BaseException) -> str:
        """Pick a recovery strategy for one failed call.

        Returns:
          rate_limit    — throttle/overload: cooldown, and the fallback model now
          transient     — network/timeout/5xx (the SDK has retried it): the fallback model now
          fatal_auth    — auth/billing: neither retry nor model swap helps; re-raise
          fatal_request — 4xx request-level: no retry, but a fallback model may work
        Unknown errors are treated as transient.
        """
        # The status the SDK's error or ProviderError carries; a connection error has none.
        status = getattr(e, "status_code", None)
        msg = _error_text(e)
        # Before the 429 check: OpenAI, Kimi and Zhipu answer an exhausted
        # quota with a 429; a wrong Gemini key is a 400.
        if status == 402 or model_problem(status, msg) in ("no_balance", "key") or any(k in msg for k in (
                "payment required", "insufficient_quota", "insufficient balance",
                "exceeded your current quota")):
            return "fatal_auth"
        if status in (429, 529) or any(k in msg for k in (
                "rate limit", "rate_limit", "too many requests", "overloaded")):
            return "rate_limit"
        if status in (401, 403) or any(k in msg for k in (
                "invalid api key", "authentication", "insufficient",
                "balance", "quota", "billing")):
            return "fatal_auth"
        if status in (400, 404, 422) or any(k in msg for k in (
                "model not found", "bad request", "invalid request", "unprocessable")):
            return "fatal_request"
        return "transient"

    async def _recovered(self, call, model: str):
        """Await `call(model)` with the recovery every model call gets on top of the SDK's own retries
        (`_retries`): throttling or an outage arms a cooldown and moves to a distinct fallback at once; a refused
        request gets one last try there; a key or account problem is said once and raised. Returns (what the call
        returned, the model that answered)."""
        cur_model = model
        # A model that was throttled or unreachable is skipped by every
        # caller while it cools down: a DM would otherwise sit through the
        # retries each turn. One refused request does not count.
        skipped, last_try = "", False
        if (self.llm_fallback_model and cur_model != self.llm_fallback_model
                and self._unreachable_until.get(cur_model, 0.0) > time.time()):
            skipped, cur_model = cur_model, self.llm_fallback_model
        while True:
            try:
                return (await call(cur_model)), cur_model
            except Exception as e:
                kind = self._classify_api_error(e)
                if kind in ("transient", "rate_limit") and self.llm_fallback_model:
                    self._unreachable_until[cur_model] = time.time() + (
                        self.llm_rate_limit_cooldown_s if kind == "rate_limit"
                        else self.llm_fallback_duration_s)
                # The fallback it started on failed too: the model that was
                # cooling down gets its one try after all.
                if skipped and kind != "fatal_auth":
                    cur_model, skipped, last_try = skipped, "", True
                    self._unreachable_until.pop(cur_model, None)
                    continue
                # Anything but the key or the account: the fallback next, which the SDK retries as it needs.
                if (kind != "fatal_auth" and self.llm_fallback_model
                        and cur_model != self.llm_fallback_model and not last_try):
                    logger.warning("[Agent] model=%s failed (%s); asking fallback=%s: %s",
                                   cur_model, kind, self.llm_fallback_model, e)
                    cur_model = self.llm_fallback_model
                    continue
                if kind == "fatal_auth":
                    self._report_fatal(e, cur_model)
                if kind == "transient" and cur_model == self.model:
                    self.__dict__["model_unreachable"] = True
                # A 429's body says which limit it was; the SDK's message gives only its `message`.
                logger.warning("[Agent] LLM call failed (model=%s, %s): %s %s",
                               cur_model, kind, e,
                               e.response.text[:300] if isinstance(e, openai.APIStatusError)
                               and kind == "rate_limit" else "")
                problem = model_problem(getattr(e, "status_code", None), _error_text(e))
                if problem:
                    # For the page to name in the chat (abilities.problem_since).
                    abilities.note_problem(self, problem, self._endpoint_for(cur_model)[0])
                if problem == "context":
                    # The window the error states is the model's from now on.
                    context.overflowed(self, self._endpoint_for(cur_model)[0], cur_model, _provider_message(e))
                if problem in ("not_found", "context", "refused"):
                    logger.warning("[Agent] %s", PROBLEMS[problem][0])
                raise

    async def _call_llm(
        self,
        system: str,
        messages: list[dict],
        model: str,
        max_tokens: int = 4096,
        disable_thinking: bool = False,
        temperature: float | None = None,
        json_object: bool = False,
    ) -> str:
        """OpenAI-compatible chat completion with the SDK's retries and
        error-driven fallback. `json_object` sets response_format, for the
        inner calls that answer in JSON. `max_tokens` FROM_BUDGET: the
        answer's room in the model's budget, thinking included."""
        if not (self.base_url and self.api_key):
            logger.warning("[Agent] missing base_url/api_key; cannot call LLM")
            return ""
        if spend.paused_for(self, _call_purpose.get()):
            return ""
        began = time.monotonic()
        sent = {"room": max_tokens}

        async def _do_call(mtok: int, mdl: str, force_disable_thinking: bool = False, total: int = 0):
            # Per call: `mdl` changes across the rungs, and the endpoint with it. `total`: max_tokens as it is.
            _url, _key = self._endpoint_for(mdl)
            effort = None if force_disable_thinking else self._reply_effort(_url, mdl)
            payload = {"model": mdl, "max_tokens": total or mtok or self._budget(_url, mdl, effort).answer,
                       "messages": _oai_messages}
            if temperature is not None:
                payload["temperature"] = temperature
            if json_object:
                payload["response_format"] = {"type": "json_object"}
            if effort:
                thought = abilities.thinking_on(payload, _url, *effort)
                if mtok and not total:
                    payload["max_tokens"] = mtok + thought
            elif disable_thinking or force_disable_thinking:
                self._thinking_off(payload, _url)
            sent["room"] = payload["max_tokens"]
            body = await self._post_chat(_url, _key, payload, timeout=self.llm_timeout_s,
                                         retries=self._retries(mdl))
            if body.get("error") or not body.get("choices"):
                raise ProviderError(body)
            if _call_purpose.get() == "reply":
                context.measured(self, _url, mdl, payload, body.get("usage"))
            return body

        # OpenAI endpoint uses a single system message; provider auto prefix-caches.
        _oai_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)

        def _pick(d: dict) -> tuple[str, str]:
            choice = (d.get("choices") or [{}])[0]
            return (answer_text(choice.get("message") or {}),
                    choice.get("finish_reason", "?"))

        def _hidden_reasoning(d: dict) -> str:
            # Only to decide on a retry, never to take text from: salvaging a
            # reasoning fragment would cross the protocol boundary.
            choice = (d.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            # `reasoning` is OpenRouter's and Ollama's spelling.
            return str(message.get("reasoning_content") or message.get("reasoning") or "").strip()

        data, used_model = await self._recovered(lambda mdl: _do_call(max_tokens, mdl), model)
        try:
            text, finish = _pick(data)
            reasoning = _hidden_reasoning(data)
        except Exception as e:
            logger.warning("[Agent] failed to parse LLM response: %s; data=%.300s", e, str(data))
            return ""
        if _call_purpose.get() == "reply":
            # The reply's reasoning, where the service returns it, for the page's activity line.
            abilities.note_reasoning(abilities.reasoning_of((data.get("choices") or [{}])[0].get("message") or {}),
                                     time.monotonic() - began)

        def _budget_starved(t: str, fin: str) -> bool:
            # Truncation: no visible text, or a half-emitted JSON object the
            # parser would drop.
            if fin != "length":
                return False
            if not t:
                return True
            if json_object:
                try:
                    json.loads(t)
                except (json.JSONDecodeError, TypeError):
                    return True
            return False

        async def _once_more(what: str, mtok: int, **force):
            """Ask the model that answered one more time: (text, finish), or
            None when that failed. `data` becomes the new response, whose
            usage is the one reported."""
            nonlocal data
            try:
                data = await _do_call(mtok, used_model, **force)
                return _pick(data)
            except Exception as e:
                logger.warning("[Agent] %s failed: %s: %s",
                               what, type(e).__name__, e)
                return None

        if _budget_starved(text, finish) and not disable_thinking:
            # Thinking may have used the whole budget: with it off the answer fits.
            logger.warning(
                "[Agent] empty reply, finish_reason=length (model=%s, max_tokens=%d); "
                "retrying once with thinking disabled", used_model, sent["room"])
            again = await _once_more("retry with thinking disabled", max_tokens,
                                     force_disable_thinking=True)
            if again is not None:
                text, finish = again
        if _budget_starved(text, finish):
            # A reasoning model can spend the whole budget thinking: retry once
            # with four times the room, within what the window leaves, then say
            # plainly what happened.
            used_url = self._endpoint_for(used_model)[0]
            retry_tokens = min(4 * sent["room"], self._budget(used_url, used_model).window
                               - int((data.get("usage") or {}).get("prompt_tokens") or 0))
            logger.warning(
                "[Agent] empty reply, finish_reason=length (model=%s, "
                "max_tokens=%d) — retrying once at %d. If this repeats, the "
                "model is likely a reasoning model whose thinking tokens "
                "exhaust the budget before the answer; pick a non-reasoning "
                "model or raise the cap.",
                used_model, sent["room"], retry_tokens)
            again = (await _once_more("retry at a larger budget", max_tokens, total=retry_tokens)
                     if retry_tokens > sent["room"] else None)
            if again is not None:
                text, finish = again
            if not text:
                # What the background memory's health line reads (health.py).
                self.__dict__["_starved_at"] = time.time()
                logger.warning(
                    "[Agent] still empty at max_tokens=%d (model=%s). This model "
                    "cannot answer within the budget — switch to a non-reasoning "
                    "model, or one that accepts thinking off.",
                    retry_tokens, used_model)
        elif not text and finish == "stop" and not disable_thinking and reasoning:
            # Some thinking models put the whole answer in reasoning_content and
            # finish "stop" with blank content; thinking off answers in content.
            logger.warning(
                "[Agent] blank content with %d chars of reasoning_content "
                "(model=%s); retrying once with thinking disabled",
                len(reasoning), used_model)
            again = await _once_more("retry with thinking disabled", max_tokens,
                                     force_disable_thinking=True)
            if again is not None:
                text, finish = again
            if not text:
                logger.warning("[Agent] LLM returned empty text; finish_reason=%s (model=%s)",
                               finish, used_model)
        elif not text and finish == "content_filter":
            logger.warning("[Agent] the provider's content filter withheld the answer "
                           "(model=%s)", used_model)
        elif not text:
            logger.warning("[Agent] LLM returned empty text; finish_reason=%s (model=%s)",
                           finish, used_model)
        # Cache usage comes in DeepSeek's and the OpenAI-style spelling.
        usage = data.get("usage") or {}
        _hit = usage.get("prompt_cache_hit_tokens")
        _miss = usage.get("prompt_cache_miss_tokens")
        if _hit is None and isinstance(usage.get("prompt_tokens_details"), dict):
            _hit = usage["prompt_tokens_details"].get("cached_tokens")
        _in = usage.get("prompt_tokens")
        # Logged at hit=0 too: a zero hit rate is what is worth noticing.
        if _in or _hit or _miss:
            logger.info("[Agent] cache: hit=%s miss=%s in=%s (model=%s)",
                        _hit or 0, _miss, _in, used_model)
        return text

    async def probe_models(self) -> None:
        """Lightweight probe at startup to confirm what each endpoint actually returns."""
        if not self.enabled:
            return
        gate = self.llm_judge_model
        logger.info("[Agent] models: replies %s, reply gate %s%s", self.model, gate,
                    " (the reply model; LLM_JUDGE_MODEL can name a cheaper one)"
                    if gate == self.model else "")

        # DM shares the primary endpoint, so the group probe covers it. The
        # fallback is probed only on its own endpoint, so a typo there shows
        # now rather than during the outage it exists for.
        probes = [("group", self.model)]
        if self._endpoint_for(self.llm_fallback_model) != self._endpoint_for(self.model):
            probes.append(("fallback", self.llm_fallback_model))
        for label, model in probes:
            url, key = self._endpoint_for(model)
            payload = {"model": model,
                       "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 1}
            # A host of unknown dialect is asked about `thinking` here, before
            # the first gate call; _post_chat remembers the answer.
            if self._thinking_verdict(url, model) is None:
                payload["thinking"] = {"type": "disabled"}
            try:
                with purpose("check"):
                    body = await self._post_chat(url, key, payload, timeout=15)
                actual = body.get("model", "?")
                logger.info("[Agent] %s model probe OK: configured=%s actual=%s",
                            label, model, actual)
            except Exception as e:
                if self._classify_api_error(e) == "fatal_auth":
                    self._report_fatal(e, model)
                logger.warning("[Agent] %s model probe failed: %s", label, e)
                # What the page's status shows, until an answer from the model clears it.
                if label == "group":
                    self.__dict__["model_unreachable"] = True
        await self._probe_embeddings()

    async def _probe_embeddings(self) -> None:
        """Say at startup whether a configured embedding endpoint answers, and
        embed the pools before the first turn needs them."""
        if not self.embedding_model:
            return
        try:
            vectors = await self._embed(["hi"], 15.0)
        except Exception as e:
            self._embedding_failed(e)
            return
        logger.info("[Agent] embedding probe OK: model=%s dims=%d",
                    self.embedding_model, len(vectors[0]))
        self._start_embedding_backfill()
