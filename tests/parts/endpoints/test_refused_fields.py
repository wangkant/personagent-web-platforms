"""Which field a service's 400 refuses (mibo/endpoints.py adapt_rejected_payload), and how each service is asked to
skip hidden reasoning (mibo/llm.py thinking_off): a field is dropped or respelled only when the error names the field
itself, never for its value or a model name that contains it; a caller that cannot learn from a refusal sends a
switch only where it is known to be taken."""
from __future__ import annotations

import pytest

from mibo.endpoints import adapt_rejected_payload
from mibo.llm import known_to_take_thinking, thinking_off


@pytest.mark.parametrize("said, model", [("Model does not exist: Qwen3-235B-Thinking", "Qwen3-235B-Thinking"),
                                         ("unknown model kimi-k2-thinking", "other"),
                                         ("invalid request: messages[1]: I was thinking about it", "m")])
def test_a_400_that_only_mentions_thinking_keeps_the_switch(said, model):
    payload = {"model": model, "thinking": {"type": "disabled"}}
    assert adapt_rejected_payload(payload, said) == "" and "thinking" in payload


@pytest.mark.parametrize("said", ["property 'thinking' is unsupported",
                                  "Unrecognized request argument supplied: thinking",
                                  "[{'type': 'extra_forbidden', 'loc': ('body', 'thinking'), "
                                  "'msg': 'Extra inputs are not permitted'}]",
                                  'Invalid JSON payload received. Unknown name "thinking": Cannot find field.',
                                  "The parameters thinking specified in the request are not supported by this "
                                  "endpoint."])
def test_a_400_that_refuses_thinking_drops_it(said):
    payload = {"model": "m", "thinking": {"type": "disabled"}}
    assert adapt_rejected_payload(payload, said) == "thinking" and "thinking" not in payload


def test_a_refused_value_keeps_its_field_and_only_a_switched_off_reasoning_is_respelled():
    payload = {"model": "m", "temperature": 3.0}
    assert adapt_rejected_payload(payload, "temperature: unknown value, must be in [0, 2]") == ""
    assert payload["temperature"] == 3.0
    mandatory = '{"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}}'
    off = {"model": "m", "reasoning": {"enabled": False}}
    assert adapt_rejected_payload(off, mandatory) == "reasoning" and off["reasoning"] == {"effort": "minimal"}
    lowest = {"model": "m", "reasoning": {"effort": "minimal"}}
    assert adapt_rejected_payload(lowest, "Reasoning is mandatory") == "" and lowest["reasoning"] == {"effort": "minimal"}


def switched(model: str, url: str) -> dict:
    """What a caller that keeps no memory (the doctor's probes) sends to skip reasoning."""
    return thinking_off({"model": model, "temperature": 0.3}, url, known_to_take_thinking(url, model),
                        k2_temperature=True)


def test_a_call_that_cannot_learn_sends_a_switch_only_where_it_is_known_to_be_taken():
    off = {"type": "disabled"}
    assert switched("vision-model", "https://open.bigmodel.cn/api/paas/v4")["thinking"] == off
    assert switched("vision-model", "https://vision.example/v1") == {"model": "vision-model", "temperature": 0.3}
    # A K2 model takes it wherever it is served, at the one temperature it answers at that way.
    for url in ("https://api.moonshot.cn/v1", "https://gateway.example/v1"):
        assert switched("kimi-k2.6", url) == {"model": "kimi-k2.6", "temperature": 0.6, "thinking": off}
    # OpenRouter ignores `thinking` rather than refusing it, so it gets its own switch, whatever the model.
    for model in ("qwen/qwen3.7-flash", "moonshotai/kimi-k2"):
        assert switched(model, "https://openrouter.ai/api/v1") == {"model": model, "temperature": 0.3,
                                                                    "reasoning": {"enabled": False}}
    assert switched("moonshotai/kimi-k2-instruct", "https://api.groq.com/openai/v1") == {
        "model": "moonshotai/kimi-k2-instruct", "temperature": 0.3}
    # DashScope and SiliconFlow take Qwen's own switch.
    assert switched("qwen3.6-plus", "https://dashscope.aliyuncs.com/compatible-mode/v1")["enable_thinking"] is False


def test_k2s_one_temperature_goes_only_to_a_caller_that_asks_and_only_with_the_switch():
    moonshot = "https://api.moonshot.cn/v1/chat/completions"
    off = {"type": "disabled"}
    assert thinking_off({"model": "kimi-k2.6", "temperature": 0.3}, moonshot, True) == {
        "model": "kimi-k2.6", "temperature": 0.3, "thinking": off}
    assert thinking_off({"model": "kimi-k2.6"}, moonshot, True, k2_temperature=True) == {
        "model": "kimi-k2.6", "temperature": 0.6, "thinking": off}
    assert thinking_off({"model": "kimi-k2.6", "temperature": 0.3}, moonshot, False, k2_temperature=True) == {
        "model": "kimi-k2.6", "temperature": 0.3}
    assert thinking_off({"model": "chat-model", "temperature": 0}, moonshot, True,
                        k2_temperature=True)["temperature"] == 0
