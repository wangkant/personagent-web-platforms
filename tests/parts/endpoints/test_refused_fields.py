"""Which field a service's 400 refuses (mibo/endpoints.py adapt_rejected_payload), and how each service is asked to
skip hidden reasoning (mibo/llm.py thinking_off): a field is dropped or respelled only when the error names the field
itself, never for its value or a model name that contains it; and the off switch in each service's spelling."""
from __future__ import annotations

import pytest

from mibo.endpoints import adapt_rejected_payload
from mibo.llm import thinking_off


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
    # A gateway with no such field drops the switch.
    gateway = {"model": "a/b", "reasoning": {"enabled": False}}
    assert adapt_rejected_payload(gateway, "Unrecognized request argument supplied: reasoning") == "reasoning_switch"
    assert "reasoning" not in gateway


def test_the_switch_off_is_spelled_as_each_service_takes_it():
    off = {"type": "disabled"}
    # Unknown or known to take it: DeepSeek's switch; known to refuse it: nothing.
    assert thinking_off({"model": "m"}, "https://gateway.example/v1", None) == {"model": "m", "thinking": off}
    assert thinking_off({"model": "m"}, "https://gateway.example/v1", False) == {"model": "m"}
    # A model with efforts and no switch is lowered by the effort the catalogue gives (modelcatalog.lowest_effort).
    assert thinking_off({"model": "m"}, "https://api.openai.com/v1", False, "none") == {
        "model": "m", "reasoning_effort": "none"}
    # OpenRouter ignores `thinking` rather than refusing it, so it gets its own switch, whatever is known.
    for known in (None, True, False):
        assert thinking_off({"model": "m"}, "https://openrouter.ai/api/v1", known, "none") == {
            "model": "m", "reasoning": {"enabled": False}}
    # A vendor/model name on another host (a gateway or pass-through in front of OpenRouter, which ignores
    # `thinking`) gets the router's switch as well.
    assert thinking_off({"model": "deepseek/deepseek-v4.1-flash"}, "http://127.0.0.1:4000/v1", None) == {
        "model": "deepseek/deepseek-v4.1-flash", "thinking": off, "reasoning": {"enabled": False}}
    # DashScope and SiliconFlow take Qwen's own switch.
    assert thinking_off({"model": "qwen3.6-plus"}, "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        None)["enable_thinking"] is False
