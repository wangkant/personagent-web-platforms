"""How a model call reads a provider's errors and learns its dialect: retries,
JSON mode, fallback models and their cooldowns."""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from mibo.agent import Agent
from mibo.endpoints import adapt_rejected_payload

from helpers import check, isolated_agent


def make_agent(tmp: Path, base_url: str) -> Agent:
    a = isolated_agent(tmp, persona_name="Luna", base_url=base_url)
    a.model = a.llm_fallback_model = a.llm_judge_model = "m"
    a.api_max_retries = 0
    return a


def _error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def test_only_an_account_status_reads_the_body_for_account_words() -> None:
    classify = Agent._classify_api_error
    for status, body in ((503, "insufficient capacity, try later"),
                         (502, "upstream quota service unavailable")):
        got = classify(_error(status, body))
        check(f"{status} {body!r} is an outage, retried", got == "transient", got)
    for body in ("my bank balance is low", "rate limit exceeded in your text"):
        got = classify(_error(400, body))
        check(f"a 400 echoing {body!r} is the request's fault", got == "fatal_request", got)
    for status, body, want in (
            (401, "invalid api key", "fatal_auth"),
            (403, "Insufficient Balance", "fatal_auth"),
            (429, '{"error": {"type": "insufficient_quota"}}', "fatal_auth"),
            (429, "rate limit reached", "rate_limit")):
        got = classify(_error(status, body))
        check(f"{status} {body!r} -> {want}", got == want, got)


def test_a_400_refuses_a_field_only_when_it_names_the_field() -> None:
    for text, model in (("Model does not exist: Qwen3-235B-Thinking", "Qwen3-235B-Thinking"),
                        ("unknown model kimi-k2-thinking", "other"),
                        ("invalid request: messages[1]: I was thinking about it", "m")):
        payload = {"model": model, "thinking": {"type": "disabled"}}
        check(f"{text!r} keeps `thinking`",
              adapt_rejected_payload(payload, text) == "" and "thinking" in payload)
    for text in ("property 'thinking' is unsupported",
                 "Unrecognized request argument supplied: thinking",
                 "[{'type': 'extra_forbidden', 'loc': ('body', 'thinking'), "
                 "'msg': 'Extra inputs are not permitted'}]",
                 'Invalid JSON payload received. Unknown name "thinking": Cannot find field.'):
        payload = {"model": "m", "thinking": {"type": "disabled"}}
        check(f"{text!r} drops `thinking`", adapt_rejected_payload(payload, text) == "thinking")
    payload = {"model": "m", "temperature": 3.0}
    check("a temperature value error keeps the temperature",
          adapt_rejected_payload(payload, "temperature: unknown value, must be in [0, 2]") == ""
          and payload["temperature"] == 3.0, repr(payload))


class _Endpoint:
    """Fake chat endpoint: `answer(payload)` -> (status, body text)."""

    def __init__(self, answer) -> None:
        self.answer, self.sent = answer, []

    def __call__(self, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.sent.append((json["model"], "thinking" in json))
        status, text = self.answer(json)
        request = httpx.Request("POST", url)
        if status == 200:
            return httpx.Response(200, request=request, json={"choices": [
                {"message": {"content": "ok"}, "finish_reason": "stop"}]})
        return httpx.Response(status, request=request, text=text)


async def _gate(agent: Agent, model: str = "m") -> str:
    try:
        return await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                     model=model, max_tokens=50,
                                     disable_thinking=True)
    except httpx.HTTPStatusError:
        return "error"


# ---------------------------------------------------------------------------
# Routing, retries, JSON mode, fallback models and cooldowns
# ---------------------------------------------------------------------------

async def test_cache_hits_are_logged_in_either_spelling(tmp: Path, caplog) -> None:
    """The cache line read only DeepSeek's usage keys, so it never fired on
    an OpenAI-style provider (prompt_tokens_details.cached_tokens), and a
    zero hit rate was indistinguishable from no telemetry at all."""
    import logging

    agent = isolated_agent(tmp)

    class _Resp:
        def __init__(self, usage: dict) -> None:
            self._usage = usage

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                    "usage": self._usage}

    class _HTTP:
        def __init__(self, usage: dict) -> None:
            self._usage = usage

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return _Resp(self._usage)

    async def cache_line(usage: dict) -> str:
        caplog.clear()
        agent._http = lambda **kw: _HTTP(usage)
        with caplog.at_level(logging.INFO, logger="agent"):
            await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                  model="m", max_tokens=100)
        lines = [r.getMessage() for r in caplog.records if "cache:" in r.getMessage()]
        return lines[0] if lines else ""

    line = await cache_line({"prompt_tokens": 500,
                             "prompt_tokens_details": {"cached_tokens": 300}})
    check("cache: the OpenAI-style spelling is read",
          "hit=300" in line and "in=500" in line, repr(line))
    line = await cache_line({"prompt_tokens": 500, "prompt_cache_hit_tokens": 120,
                             "prompt_cache_miss_tokens": 380})
    check("cache: DeepSeek's spelling still is",
          "hit=120" in line and "miss=380" in line, repr(line))
    line = await cache_line({"prompt_tokens": 500})
    check("cache: a zero hit rate is logged, not silent",
          "hit=0" in line and "in=500" in line, repr(line))


async def test_truncated_reply_retries_once(tmp: Path) -> None:
    """An empty reply with finish_reason=length must be diagnosed, not shrugged at.

    A reasoning model spends the budget on its chain of thought and can hit the
    cap before emitting a single visible token, so every turn comes back empty.
    The only clue used to be "finish_reason=length" in a warning, which does not
    tell an operator that their model choice is the cause. Found by running the
    benchmark against deepseek-v4-pro: every reply empty, every self-eval 1/5,
    and nothing in the logs pointing at why."""
    agent = isolated_agent(tmp)
    budgets: list[int] = []

    class _Resp:
        def __init__(self, content: str) -> None:
            self._c = content

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"choices": [{"message": {"content": self._c},
                                 "finish_reason": "length" if not self._c else "stop"}]}

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            budgets.append(json["max_tokens"])
            return _Resp("" if len(budgets) == 1 else '{"reply":"recovered"}')

    agent._http = lambda **kw: _HTTP()
    out = await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                model="reasoner", max_tokens=1200)
    check("truncation: retried once with thinking off at the same budget",
          budgets == [1200, 1200], repr(budgets))
    check("truncation: the retry's answer is returned",
          out == '{"reply":"recovered"}', repr(out))

    # json_object mode: a truncated non-empty JSON with finish=length must
    # also retry -- it used to bypass the empty-only check and get dropped by
    # the fail-closed parser with no length warning at all. And the payload
    # must actually carry response_format, or none of this mode exists.
    budgets.clear()
    payloads = []

    class _Trunc(_Resp):
        def json(self):
            return {"choices": [{"message": {"content": '{"reasoning": "half'},
                                 "finish_reason": "length" if len(budgets) <= 2
                                 else "stop"}]}

    class _HTTP3(_HTTP):
        async def post(self, url, headers=None, json=None):
            budgets.append(json["max_tokens"])
            payloads.append(json)
            if len(budgets) <= 2:
                return _Trunc("")
            return _Resp('{"reply":"whole"}')

    agent._http = lambda **kw: _HTTP3()
    out = await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                model="m", max_tokens=1200,
                                json_object=True)
    check("truncation: half-emitted JSON retries too, thinking off and then a larger budget",
          budgets == [1200, 1200, 4800], repr(budgets))
    check("truncation: whole JSON comes back from the retry",
          out == '{"reply":"whole"}', repr(out))
    check("json_object: payload carries response_format",
          all(pl.get("response_format") == {"type": "json_object"}
              for pl in payloads), repr(payloads[:1]))

    # A non-length empty reply must NOT trigger the retry.
    budgets.clear()

    class _EmptyStop(_Resp):
        def json(self):
            return {"choices": [{"message": {"content": ""},
                                 "finish_reason": "stop"}]}

    class _HTTP2(_HTTP):
        async def post(self, url, headers=None, json=None):
            budgets.append(json["max_tokens"])
            return _EmptyStop("")

    agent._http = lambda **kw: _HTTP2()
    await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                          model="m", max_tokens=1200)
    check("truncation: an empty stop is not retried", budgets == [1200], repr(budgets))


async def test_blank_content_with_reasoning_retries_thinking_disabled(tmp: Path) -> None:
    """A thinking model (deepseek-v4-flash) can put the ENTIRE protocol answer
    in reasoning_content and leave `content` whitespace while finishing
    normally -- finish_reason="stop", not "length", so the truncation retry
    above never fires, and the stripped "" became a silent turn. Intermittent
    and prompt-dependent. The fix is one retry with thinking forced off; text
    is never salvaged from reasoning_content itself, because a fluent
    reasoning fragment is indistinguishable from an ordinary chat line."""
    agent = isolated_agent(tmp)
    payloads: list[dict] = []
    reasoning_blob = "the user hit a missing-header build error, " * 8
    answer = '{"reasoning":"","intent":"chat","reply":"try the include path","mem":""}'

    class _Resp:
        def __init__(self, content: str, reasoning: str = "") -> None:
            self._c, self._r = content, reasoning

        def raise_for_status(self) -> None:
            pass

        def json(self):
            msg = {"content": self._c}
            if self._r:
                msg["reasoning_content"] = self._r
            return {"choices": [{"message": msg, "finish_reason": "stop"}]}

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            payloads.append(json)
            if len(payloads) == 1:
                return _Resp("   ", reasoning=reasoning_blob)
            return _Resp(answer)

    async def call(**kw) -> str:
        payloads.clear()
        agent._http = lambda **_kw: _HTTP()
        return await agent._call_llm(
            "sys", [{"role": "user", "content": "hi"}], model="deepseek-v4-flash",
            max_tokens=1200, json_object=True, **kw)

    out = await call()
    check("blank+reasoning: retried exactly once", len(payloads) == 2, repr(payloads))
    check("blank+reasoning: the first ask left thinking on",
          "thinking" not in payloads[0], repr(payloads[0]))
    check("blank+reasoning: the retry forces thinking off",
          payloads[1].get("thinking") == {"type": "disabled"}, repr(payloads[1]))
    check("blank+reasoning: the retry keeps the same token budget",
          payloads[1].get("max_tokens") == 1200, repr(payloads[1]))
    check("blank+reasoning: the retry keeps json_object mode",
          payloads[1].get("response_format") == {"type": "json_object"},
          repr(payloads[1]))
    check("blank+reasoning: the retry's reply is returned, not the reasoning",
          out == answer, repr(out))

    # Thinking was already off: the same request again would answer the same.
    out = await call(disable_thinking=True)
    check("blank+reasoning: no retry when the caller already disabled thinking",
          len(payloads) == 1 and out == "", repr((len(payloads), out)))

    class _HTTPNormal(_HTTP):
        async def post(self, url, headers=None, json=None):
            payloads.append(json)
            return _Resp('{"reply":"fine"}', reasoning=reasoning_blob)

    payloads.clear()
    agent._http = lambda **kw: _HTTPNormal()
    out = await agent._call_llm(
        "sys", [{"role": "user", "content": "hi"}], model="deepseek-v4-flash",
        max_tokens=1200, json_object=True)
    check("normal reply: reasoning beside real content does not retry",
          len(payloads) == 1 and out == '{"reply":"fine"}',
          repr((len(payloads), out)))


class _429Resp:
    """Minimal httpx.Response stand-in: raise_for_status() always throttles."""

    def raise_for_status(self) -> None:
        raise RuntimeError("429 too many requests (status 429)")

    def json(self):
        return {}


def _llm_http(posts: list, fail_models: set) -> type:
    """Fake ``self._http()`` context manager: 429s for any model in
    ``fail_models``, otherwise a normal stop-finished reply that names the
    model that answered (so the caller can tell which one won)."""

    class _OKResp:
        def __init__(self, mdl: str) -> None:
            self._mdl = mdl

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"choices": [{"message": {"content": f"ok from {self._mdl}"},
                                 "finish_reason": "stop"}]}

    class _HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            mdl = json["model"]
            posts.append(mdl)
            if mdl in fail_models:
                return _429Resp()
            return _OKResp(mdl)

    return _HTTP


def _two_model_agent(tmp: Path) -> Agent:
    a = isolated_agent(tmp)
    a.model, a.llm_fallback_model = "primary", "fallback"
    a.llm_fallback_duration_s = 300
    a.api_max_retries = 0
    return a


async def test_a_throttled_model_cools_in_seconds_not_minutes(tmp: Path) -> None:
    """A 429 is not a breakage, and treating it as one threw away most of a
    working model: under the one shared 300s window, a primary that 429s on
    one call in four spent most of its time routed around. The call that hit
    the 429 already failed over; the window only has to keep the next few
    turns off the same wall. So `llm_rate_limit_cooldown_s` is for metered, and
    `llm_fallback_duration_s` for a model that answered 400 or ran out of retries."""
    agent = _two_model_agent(tmp)
    agent.llm_rate_limit_cooldown_s = 20
    posts: list = []
    agent._http = lambda **kw: _llm_http(posts, {"primary"})()

    before = time.time()
    out = await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                model="primary", max_tokens=100)
    check("throttled: the turn is still served, by the fallback",
          out == "ok from fallback", repr(out))
    armed = agent._fallback_until.get("primary", 0.0) - before
    check("throttled: the window is the SHORT one",
          19 <= armed <= 22, f"{armed:.1f}s")

    # The other class of failure keeps the long window: a model that answers
    # 400 will answer 400 again, and five minutes of not asking is cheap.
    broken = _two_model_agent(tmp / "b")
    broken.llm_rate_limit_cooldown_s = 20
    bad: list = []

    class _Bad:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            bad.append(json["model"])
            if json["model"] == "primary":
                raise httpx.HTTPStatusError(
                    "bad request",
                    request=httpx.Request("POST", "https://x.invalid"),
                    response=httpx.Response(400))
            return await _llm_http([], set())().post(url, headers, json)

    broken._http = lambda **kw: _Bad()
    t0 = time.time()
    out = await broken._call_llm("sys", [{"role": "user", "content": "hi"}],
                                 model="primary", max_tokens=100)
    long_window = broken._fallback_until.get("primary", 0.0) - t0
    check("a 400 is served by the fallback too",
          out == "ok from fallback" and bad == ["primary", "fallback"],
          repr((out, bad)))
    check("a 400 still cools for the long window",
          long_window > 200, f"{long_window:.1f}s")


class _EndpointSpy:
    """Fake ``self._http()`` that records (url, bearer key, model) for every
    POST: 429 for a model in ``fail``, otherwise a 200 whose content also
    parses as a self-eval verdict."""

    def __init__(self, seen: list, fail: set = frozenset()) -> None:
        self._seen, self._fail = seen, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self._seen.append((url, headers["Authorization"], json["model"]))
        if json["model"] in self._fail:
            return _429Resp()

        class _OK:
            status_code = 200

            def raise_for_status(self) -> None:
                pass

            def json(self):
                return {"choices": [{"message": {"content": '{"score": 5, "reason": "ok"}'},
                                     "finish_reason": "stop"}]}
        return _OK()


class _ThinkingSpy:
    """Fake ``self._http()`` recording ``(url, model, sent thinking?)`` per
    POST. A host containing ``rejects`` answers 400 to ``thinking``, as Groq
    does (`400 property 'thinking' is unsupported`); every other answer is a
    reply that is also a web_search tool call."""

    def __init__(self, seen: list, rejects: str = "") -> None:
        self._seen, self._rejects = seen, rejects

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self._seen.append((url, json["model"], "thinking" in json))
        refused = bool(self._rejects) and self._rejects in url and "thinking" in json

        class _Resp:
            status_code = 400 if refused else 200
            text = "property 'thinking' is unsupported" if refused else ""

            def raise_for_status(self) -> None:
                if refused:
                    raise RuntimeError("400 bad request: property 'thinking' is unsupported")

            def json(self):
                call = {"function": {"name": "web_search",
                                     "arguments": '{"query": "gold price"}'}}
                return {"choices": [{"message": {"content": "ok", "tool_calls": [call]},
                                     "finish_reason": "stop"}]}
        return _Resp()


async def test_the_startup_probe_learns_whether_an_endpoint_takes_thinking(tmp: Path) -> None:
    """A host of unknown dialect is asked once, by the startup probe, rather
    than by every gate call: if it takes `thinking` the gate gets it from then
    on, and if it 400s the probe is retried without it and nothing else is
    ever sent it."""
    for rejects, want in (("", True), ("custom.example", False)):
        agent = isolated_agent(tmp)
        agent.base_url = "https://custom.example/v1"
        agent.model = agent.llm_fallback_model = agent.llm_judge_model = "m"
        seen: list = []
        agent._http = lambda **kw: _ThinkingSpy(seen, rejects=rejects)
        await agent.probe_models()
        url = "https://custom.example/v1/chat/completions"
        check(f"the probe asked about `thinking` (rejects={bool(rejects)})",
              seen[0] == (url, "m", True), repr(seen))
        check("...and answered, retried without it when refused",
              [t for _u, _m, t in seen] == ([True] if want else [True, False]),
              repr(seen))
        seen.clear()
        await agent._call_llm("sys", [{"role": "user", "content": "hi"}], model="m",
                              max_tokens=100,
                              disable_thinking=True)
        check(f"a gate call afterwards sends it only if it was taken ({want})",
              seen == [(url, "m", want)], repr(seen))
    agent.base_url = "https://api.openai.com"
    agent._api_quirks.clear()
    seen.clear()
    await agent.probe_models()
    check("a vendor known to refuse it is not asked",
          [t for _u, _m, t in seen] == [False], repr(seen))


async def test_a_reasoning_model_gets_the_spelling_it_takes(tmp: Path) -> None:
    """OpenAI's reasoning models 400 `max_tokens` and any non-default
    `temperature`. The call is retried once with `max_completion_tokens` and
    without the temperature, and the endpoint and model are remembered."""
    agent = isolated_agent(tmp)
    agent.base_url = "https://api.openai.com"
    agent.model = agent.llm_fallback_model = agent.llm_judge_model = "gpt-5-mini"
    sent: list = []

    class _Resp:
        def __init__(self, status: int, text: str = "") -> None:
            self.status_code, self.text = status, text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"{self.status_code} bad request")

        def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    class _OpenAI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(sorted(k for k in json if k not in ("model", "messages")))
            if "max_tokens" in json:
                return _Resp(400, "Unsupported parameter: 'max_tokens' is not "
                                  "supported with this model. Use "
                                  "'max_completion_tokens' instead.")
            if "temperature" in json:
                return _Resp(400, "Unsupported value: 'temperature' does not "
                                  "support 0.3 with this model. Only the default "
                                  "(1) value is supported.")
            return _Resp(200)

    agent._http = lambda **kw: _OpenAI()

    async def gate() -> str:
        return await agent._call_llm(
            "sys", [{"role": "user", "content": "hi"}], model="gpt-5-mini",
            max_tokens=1500, disable_thinking=True,
            temperature=0.3)

    check("the gate answers on a reasoning model", await gate() == "ok")
    check("...after one retry per refused field",
          sent == [["max_tokens", "reasoning_effort", "temperature"],
                   ["max_completion_tokens", "reasoning_effort", "temperature"],
                   ["max_completion_tokens", "reasoning_effort"]], repr(sent))
    sent.clear()
    check("the next call is spelled right the first time", await gate() == "ok")
    check("...with no 400 at all", sent == [["max_completion_tokens", "reasoning_effort"]], repr(sent))


async def test_a_model_that_must_reason_is_asked_for_the_lowest_effort(tmp: Path) -> None:
    """Some OpenRouter models refuse `reasoning: {enabled: false}` with "Reasoning
    is mandatory". The call is retried once at the lowest effort instead of
    failing, and that model is asked that way from then on."""
    agent = isolated_agent(tmp)
    agent.base_url = "https://openrouter.ai/api/v1"
    agent.model = agent.llm_fallback_model = agent.llm_judge_model = "z-ai/glm-5.3-flash"
    sent: list = []

    class _Resp:
        def __init__(self, status: int, text: str = "") -> None:
            self.status_code, self.text = status, text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"{self.status_code} bad request")

        def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    class _OpenRouter:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json.get("reasoning"))
            if json.get("reasoning") == {"enabled": False}:
                return _Resp(400, '{"error": {"message": "Reasoning is mandatory for '
                                  'this endpoint and cannot be disabled."}}')
            return _Resp(200)

    agent._http = lambda **kw: _OpenRouter()

    async def gate() -> str:
        return await agent._call_llm(
            "sys", [{"role": "user", "content": "hi"}], model="z-ai/glm-5.3-flash",
            max_tokens=200, disable_thinking=True)

    check("the gate answers on a model that must reason", await gate() == "ok")
    check("...after one retry at the lowest effort",
          sent == [{"enabled": False}, {"effort": "minimal"}], repr(sent))
    sent.clear()
    check("the next call asks that way the first time", await gate() == "ok")
    check("...with no 400 at all", sent == [{"effort": "minimal"}], repr(sent))
    payload = {"model": "m", "reasoning": {"effort": "minimal"}}
    check("only a switched-off `reasoning` is rewritten",
          adapt_rejected_payload(payload, "Reasoning is mandatory") == ""
          and payload["reasoning"] == {"effort": "minimal"}, repr(payload))


async def test_an_exhausted_balance_is_fatal_and_said_once(tmp: Path, caplog) -> None:
    """A 402 is the account, not the network: no retries, no failover to a
    model on the same account, and one operator line naming the host and the
    settings that fix it."""
    import logging

    import httpx

    agent = _two_model_agent(tmp)
    agent.api_max_retries = 2
    agent.base_url = "https://api.deepseek.com"
    posts: list = []

    class _Pay:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posts.append(json["model"])
            request = httpx.Request("POST", url)
            return httpx.Response(402, request=request,
                                  json={"error": {"message": "Insufficient Balance"}})

    agent._http = lambda **kw: _Pay()
    check("402 classifies as fatal", agent._classify_api_error(httpx.HTTPStatusError(
        "402", request=httpx.Request("POST", "https://x"),
        response=httpx.Response(402))) == "fatal_auth")
    check("an exhausted OpenAI quota (a 429) is fatal too",
          agent._classify_api_error(httpx.HTTPStatusError(
              "429", request=httpx.Request("POST", "https://x"),
              response=httpx.Response(429, json={"error": {
                  "type": "insufficient_quota",
                  "message": "You exceeded your current quota"}}))) == "fatal_auth")
    with caplog.at_level(logging.ERROR, logger="agent"):
        for _ in range(2):
            try:
                await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                      model="primary", max_tokens=10)
            except Exception:
                pass
    check("no retry and no failover on a 402", posts == ["primary", "primary"],
          repr(posts))
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    check("one operator line, naming the host and the settings",
          len(lines) == 1 and "api.deepseek.com" in lines[0]
          and "balance or quota" in lines[0] and "LLM_API_KEY" in lines[0]
          and "LLM_BASE_URL" in lines[0], repr(lines))


async def test_a_separate_fallback_endpoint_is_probed_at_startup(tmp: Path) -> None:
    """It exists for the primary's outage, so a typo in its URL or key would
    otherwise surface during that outage and not before."""
    agent = _two_model_agent(tmp)
    agent.base_url = "https://primary.example"
    seen: list = []
    agent._http = lambda **kw: _EndpointSpy(seen)

    await agent.probe_models()
    check("probe: one endpoint, one probe",
          [m for _u, _k, m in seen] == ["primary"], repr(seen))
    agent.llm_fallback_base_url = "https://fallback.example"
    seen.clear()
    await agent.probe_models()
    check("probe: a fallback on its own endpoint is probed there too",
          [(u, m) for u, _k, m in seen]
          == [("https://primary.example/v1/chat/completions", "primary"),
              ("https://fallback.example/v1/chat/completions", "fallback")],
          repr(seen))


async def test_one_refused_request_does_not_take_a_model_out(tmp: Path) -> None:
    """A provider's 400 for one message (a content filter, say) is about that
    message. The next call still starts on the model it asked for; and when a
    model is skipped while it cools down and the fallback then fails, the
    skipped model gets its one try."""
    agent = _two_model_agent(tmp)
    posts: list = []
    refuse = {"primary"}

    class _Resp:
        def __init__(self, model: str) -> None:
            self._model = model

        def raise_for_status(self) -> None:
            if self._model in refuse:
                raise RuntimeError("400 bad request (status 400)")
            if self._model in throttled:
                raise RuntimeError("429 too many requests (status 429)")

        def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    class _Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posts.append(json["model"])
            return _Resp(json["model"])

    throttled: set = set()
    agent._http = lambda **kw: _Http()
    ask = dict(model="primary", max_tokens=100)
    await agent._call_llm("sys", [{"role": "user", "content": "hi"}], **ask)
    check("a refused request is answered by the fallback", posts == ["primary", "fallback"], repr(posts))
    refuse.clear()
    posts.clear()
    await agent._call_llm("sys", [{"role": "user", "content": "hi"}], **ask)
    check("and the next call asks the same model again", posts == ["primary"], repr(posts))

    throttled.add("primary")
    posts.clear()
    await agent._call_llm("sys", [{"role": "user", "content": "hi"}], **ask)
    throttled.clear()
    throttled.add("fallback")
    posts.clear()
    await agent._call_llm("sys", [{"role": "user", "content": "hi"}], **ask)
    check("a skipped model still gets a try when the fallback fails",
          posts == ["fallback", "primary"], repr(posts))


# ---- the three single retries ------------------------------------------------

class _Scripted:
    """Fake `agent._http`: each POST takes the next scripted answer, either a
    response body or an exception to raise; `payloads` keeps what was sent."""

    def __init__(self, *answers) -> None:
        self.answers, self.payloads = list(answers), []

    def __call__(self, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.payloads.append(json)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return httpx.Response(200, request=httpx.Request("POST", url), json=answer)


def _answer(content: str, finish: str = "stop", *, tokens: int,
            reasoning: str = "") -> dict:
    return {"choices": [{"message": {"content": content,
                                     "reasoning_content": reasoning},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": tokens}}


async def _ask(agent: Agent, caplog, *answers, **call) -> tuple:
    """(text, payloads, log lines) of one `_call_llm` against `answers`."""
    import logging

    http = _Scripted(*answers)
    agent._http = http
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent"):
        out = await agent._call_llm(
            "sys", [{"role": "user", "content": "hi"}],
            **{"model": "m", "max_tokens": 100, **call})
    return out, http.payloads, [r.getMessage() for r in caplog.records]


def _has(lines: list, *parts: str) -> bool:
    return any(all(part in line for part in parts) for line in lines)


async def test_the_larger_budget_retry_in_every_outcome(tmp: Path, caplog) -> None:
    agent = isolated_agent(tmp)
    cut_off = _answer("", "length", tokens=11)
    # Thinking already off: the larger budget is the first rung (with it on,
    # a thinking-off retry at the same budget comes first).
    off = {"disable_thinking": True}

    out, sent, lines = await _ask(agent, caplog, cut_off,
                                  _answer('{"reply":"whole"}', tokens=22), **off)
    check("budget: the retry's answer is returned", out == '{"reply":"whole"}', out)
    check("budget: asked again at four times the cap",
          [p["max_tokens"] for p in sent] == [100, 400], repr(sent))
    check("budget: the usage line is the retry's", _has(lines, "cache:", "in=22"),
          repr(lines))

    out, sent, lines = await _ask(agent, caplog, cut_off, httpx.ReadTimeout("slow"), **off)
    check("budget, retry raised: blank, and nothing asked a third time",
          out == "" and len(sent) == 2, repr((out, len(sent))))
    check("budget, retry raised: the failure is named",
          _has(lines, "retry at a larger budget failed: ReadTimeout: slow"),
          repr(lines))
    check("budget, retry raised: and the dead end said plainly",
          _has(lines, "still empty at max_tokens=400"), repr(lines))
    check("budget, retry raised: the usage line stays the first answer's",
          _has(lines, "cache:", "in=11"), repr(lines))

    out, sent, lines = await _ask(agent, caplog, cut_off,
                                  _answer("", "length", tokens=22), **off)
    check("budget, still cut off: blank, said plainly, one retry only",
          out == "" and len(sent) == 2
          and _has(lines, "still empty at max_tokens=400"), repr(lines))

    malformed = {"choices": "oops", "usage": {"prompt_tokens": 33}}
    out, sent, lines = await _ask(agent, caplog, cut_off, malformed, **off)
    check("budget, unreadable retry: blank and named",
          out == "" and _has(lines, "retry at a larger budget failed: AttributeError"),
          repr(lines))
    check("budget, unreadable retry: its usage is still the one reported",
          _has(lines, "cache:", "in=33"), repr(lines))


async def test_the_thinking_off_retry_in_every_outcome(tmp: Path, caplog) -> None:
    agent = isolated_agent(tmp, base_url="https://api.deepseek.com")
    all_thought = _answer("", tokens=11, reasoning="the whole answer, in here")

    out, sent, lines = await _ask(agent, caplog, all_thought,
                                  _answer('{"reply":"said"}', tokens=22))
    check("thinking: the retry's answer is returned", out == '{"reply":"said"}', out)
    check("thinking: only the retry switches it off",
          ["thinking" in p for p in sent] == [False, True], repr(sent))
    check("thinking: the usage line is the retry's", _has(lines, "cache:", "in=22"),
          repr(lines))

    out, sent, lines = await _ask(agent, caplog, all_thought, httpx.ReadTimeout("slow"))
    check("thinking, retry raised: blank after one retry",
          out == "" and len(sent) == 2, repr((out, len(sent))))
    check("thinking, retry raised: the failure is named",
          _has(lines, "retry with thinking disabled failed: ReadTimeout: slow"),
          repr(lines))
    check("thinking, retry raised: and the empty answer reported",
          _has(lines, "LLM returned empty text; finish_reason=stop"), repr(lines))
    check("thinking, retry raised: the usage line stays the first answer's",
          _has(lines, "cache:", "in=11"), repr(lines))

    out, sent, lines = await _ask(agent, caplog, all_thought,
                                  _answer("", "length", tokens=22))
    check("thinking, retry blank: reported with the retry's finish reason",
          out == "" and _has(lines, "LLM returned empty text; finish_reason=length"),
          repr(lines))


# ---- one switch for hidden reasoning -----------------------------------------

def _noise_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.effect_noise((64, 64), 64).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def test_a_call_that_cannot_ask_goes_by_what_is_known_of_the_endpoint() -> None:
    """The doctor's probes run once and keep no memory, so they cannot learn
    from a refusal: they send `thinking` only where it is known to be taken,
    and an unknown endpoint is left alone."""
    from mibo.llm import known_to_take_thinking, thinking_off

    def switched(model: str, url: str) -> dict:
        payload = {"model": model, "temperature": 0.3}
        return thinking_off(payload, url, known_to_take_thinking(url, model),
                            k2_temperature=True)

    check("a host that takes it gets it, for any model",
          switched("vision-model", "https://open.bigmodel.cn/api/paas/v4")
          == {"model": "vision-model", "temperature": 0.3,
              "thinking": {"type": "disabled"}})
    check("an unknown host is not asked",
          switched("vision-model", "https://vision.example/v1")
          == {"model": "vision-model", "temperature": 0.3})
    check("a K2 model on its own host: thinking off at its one temperature",
          switched("kimi-k2.6", "https://api.moonshot.cn/v1")
          == {"model": "kimi-k2.6", "temperature": 0.6,
              "thinking": {"type": "disabled"}})
    check("a K2 model takes it wherever it is served, at its one temperature",
          switched("kimi-k2.6", "https://gateway.example/v1")
          == {"model": "kimi-k2.6", "temperature": 0.6,
              "thinking": {"type": "disabled"}})
    # OpenRouter ignores `thinking` rather than refusing it: a reasoning
    # vision model there spent its whole budget thinking and captioned nothing.
    check("OpenRouter gets its own switch, for any model",
          switched("qwen/qwen3.7-flash", "https://openrouter.ai/api/v1")
          == {"model": "qwen/qwen3.7-flash", "temperature": 0.3,
              "reasoning": {"enabled": False}})
    check("...and the endpoint wins over a K2 model's name",
          switched("moonshotai/kimi-k2", "https://openrouter.ai/api/v1")
          == {"model": "moonshotai/kimi-k2", "temperature": 0.3,
              "reasoning": {"enabled": False}})
    check("a host that refuses `thinking` is not sent it, K2 or not",
          switched("moonshotai/kimi-k2-instruct", "https://api.groq.com/openai/v1")
          == {"model": "moonshotai/kimi-k2-instruct", "temperature": 0.3})


def test_k2s_one_temperature_is_given_only_to_a_caller_that_asks() -> None:
    """One function holds the rule. `textproc.apply_k2_quirks`, the older
    switch, had no caller left but its own tests and would have drifted."""
    from mibo import textproc
    from mibo.llm import thinking_off

    check("the older switch is gone", not hasattr(textproc, "apply_k2_quirks"))
    moonshot = "https://api.moonshot.cn/v1/chat/completions"
    off = {"type": "disabled"}
    check("unasked, a K2 call keeps the temperature it set",
          thinking_off({"model": "kimi-k2.6", "temperature": 0.3}, moonshot, True)
          == {"model": "kimi-k2.6", "temperature": 0.3, "thinking": off})
    check("asked, a call that set none is given it too",
          thinking_off({"model": "kimi-k2.6"}, moonshot, True, k2_temperature=True)
          == {"model": "kimi-k2.6", "temperature": 0.6, "thinking": off})
    check("it goes with the switch: K2 left thinking keeps its temperature",
          thinking_off({"model": "kimi-k2.6", "temperature": 0.3}, moonshot, False,
                       k2_temperature=True)
          == {"model": "kimi-k2.6", "temperature": 0.3})
    check("another model's temperature is its own",
          thinking_off({"model": "chat-model", "temperature": 0}, moonshot, True,
                       k2_temperature=True)["temperature"] == 0)


class _ByModel:
    """Fake `agent._http`: each model answers with its own scripted callable,
    which returns a JSON body or raises."""

    def __init__(self, **answers) -> None:
        self.answers, self.models = answers, []

    def __call__(self, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.models.append(json["model"])
        body = self.answers[json["model"]](json)
        return httpx.Response(200, request=httpx.Request("POST", url), json=body)


def _ok(text: str, **usage):
    return lambda payload: {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                            **({"usage": usage} if usage else {})}


async def test_an_error_inside_a_200_goes_to_the_fallback(tmp: Path, caplog) -> None:
    """OpenRouter reports an upstream outage as a 200 whose body is an error:
    read as a blank answer, the turn went silent and the backup was never asked."""
    import logging

    agent = _two_model_agent(tmp)
    http = _ByModel(primary=lambda p: {"error": {"code": 502, "message": "upstream is down"}},
                    fallback=_ok("from the backup"))
    agent._http = http
    with caplog.at_level(logging.INFO, logger="agent"):
        out = await agent._call_llm("sys", [{"role": "user", "content": "hi"}],
                                    model="primary", max_tokens=50)
    lines = [r.getMessage() for r in caplog.records]
    check("an error body is answered by the fallback",
          out == "from the backup" and http.models == ["primary", "fallback"],
          repr((out, http.models)))
    check("the log names the provider's message", _has(lines, "upstream is down"), repr(lines))

    billing = _two_model_agent(tmp / "b")
    billing.base_url = "https://openrouter.ai/api/v1"
    http = _ByModel(primary=lambda p: {"error": {"code": 402, "message": "Insufficient credits"}},
                    fallback=_ok("never"))
    billing._http = http
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="agent"):
        try:
            await billing._call_llm("sys", [{"role": "user", "content": "hi"}],
                                    model="primary", max_tokens=50)
        except Exception:
            pass
    lines = [r.getMessage() for r in caplog.records]
    check("a 402 inside a 200 is the account: no failover, one billing line",
          http.models == ["primary"] and _has(lines, "balance or quota"),
          repr((http.models, lines)))


async def test_a_content_filter_finish_is_named(tmp: Path, caplog) -> None:
    agent = isolated_agent(tmp)
    out, _sent, lines = await _ask(agent, caplog, _answer("", "content_filter", tokens=5))
    check("a filtered answer stays blank", out == "", repr(out))
    check("the log names the content filter", _has(lines, "content filter"), repr(lines))


async def test_a_hanging_main_model_hands_the_turn_to_the_backup(tmp: Path) -> None:
    """A timeout used to be retried with backoff on the same model, and the
    turn budget ran out before the backup was reached."""
    agent = _two_model_agent(tmp)
    agent.api_max_retries = 2
    agent.llm_timeout_s = 0.4

    def hang(payload):
        raise httpx.ReadTimeout("read timed out")

    http = _ByModel(primary=hang, fallback=_ok("from the backup"))
    agent._http = http
    started = time.monotonic()
    out = await agent._within_turn_budget(agent._call_llm(
        "sys", [{"role": "user", "content": "hi"}], model="primary", max_tokens=50))
    check("the backup answers within the turn",
          out == "from the backup" and http.models == ["primary", "fallback"],
          repr((out, http.models)))
    check("no backoff on the hanging model", time.monotonic() - started < 0.5)

    solo = isolated_agent(tmp / "solo")
    solo.model = solo.llm_fallback_model = "primary"
    solo.api_max_retries = 1
    http = _ByModel(primary=hang)
    solo._http = http
    try:
        await solo._call_llm("sys", [{"role": "user", "content": "hi"}], model="primary",
                             max_tokens=50)
    except httpx.TimeoutException:
        pass
    check("without a backup a timeout keeps today's retry",
          http.models == ["primary", "primary"], repr(http.models))

