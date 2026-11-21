"""The fit of one turn's requests (mibo/context.py Fit, context-design.json's fit ladder): a tool result held to
its share when it comes in, older results and the pictures tools returned cut once each past the ceiling, the
newest round left whole, and a request that cannot fit said so."""
from __future__ import annotations

from mibo import context

PICTURES = "The pictures the tools returned:"
PLAN = context.budget(65536)


def _han(n: int, chars: int) -> str:
    # Distinct Chinese text, so a head and a tail can be told apart.
    return "".join(chr(0x4E00 + (n * 997 + i * 7) % 20000) for i in range(chars))


def _count(convo: list) -> int:
    return round(sum(context.message_tokens(m) for m in convo))


def _fit() -> context.Fit:
    return context.Fit(PLAN, _count, 1.0, PICTURES)


def _calls(n: int, *results: str) -> list:
    calls = [{"id": f"c{n}_{k}", "type": "function", "function": {"name": "read_page", "arguments": "{}"}}
             for k in range(len(results))]
    return [{"role": "assistant", "content": "", "tool_calls": calls},
            *({"role": "tool", "tool_call_id": c["id"], "content": r} for c, r in zip(calls, results))]


def _picture() -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}


def test_a_result_past_its_share_keeps_its_head_and_tail_and_says_how_much_is_left_out():
    text = _han(1, 40_000)
    convo = [{"role": "user", "content": "读一下"}, *_calls(1, text)]
    assert _fit()(convo)
    kept = convo[-1]["content"]
    assert context.text_tokens(kept) <= PLAN.result
    head, tail = kept.split(context.CUT_MARK.split("{n}")[0])[0], kept.rsplit(context.CUT_MARK.split("{n}")[1])[-1]
    assert text.startswith(head) and text.endswith(tail) and 3.5 < len(head) / len(tail) < 4.5
    assert str(len(text) - len(head) - len(tail)) in kept


def test_a_request_under_the_ceiling_is_sent_as_it_came():
    convo = [{"role": "user", "content": "读一下"}, *_calls(1, _han(1, 3000), _han(2, 3000)), *_calls(2, _han(3, 3000))]
    before = [dict(m) for m in convo]
    fit = _fit()
    assert fit(convo) and convo == before and fit.rewrites == 0


def test_past_the_ceiling_older_results_are_cut_once_oldest_first_and_the_newest_calls_stay_whole():
    fit = _fit()
    convo = [{"role": "user", "content": "读一下"}]
    sent: dict = {}
    for n in range(5):
        convo += _calls(n, *(_han(10 * n + k, 15_000) for k in range(3)))
        assert fit(convo)
        newest = convo[-3:]
        assert all(m["content"] == _han(10 * n + k, 15_000) for k, m in enumerate(newest))
        assert _count(convo) <= PLAN.ceiling
        for m in convo:
            if m.get("role") == "tool":
                sent.setdefault(m["tool_call_id"], []).append(m["content"])
    # Each result was rewritten at most once after it was sent, and the cut ones are small.
    assert all(len(set(contents)) <= 2 for contents in sent.values())
    cut = [c[-1] for c in sent.values() if len(set(c)) == 2]
    assert cut and all(context.text_tokens(c) <= PLAN.small + 20 for c in cut)
    # The oldest went first.
    assert sent["c0_0"][-1] != sent["c0_0"][0]


def test_past_the_ceiling_pictures_the_tools_returned_go_and_the_persons_own_stay():
    own = {"role": "user", "content": [{"type": "text", "text": "看看这张"}, _picture()]}
    returned = {"role": "user", "content": [{"type": "text", "text": PICTURES}, _picture(), _picture()]}
    convo = [own, *_calls(1, "截图已拍"), returned, *_calls(2, *(_han(k, 20_000) for k in range(4)))]
    assert _fit()(convo)
    assert [p["type"] for p in own["content"]] == ["text", "image_url"]
    assert [p["type"] for p in returned["content"]] == ["text", "text", "text"]


def test_a_request_that_cannot_fit_is_said_and_its_last_call_carries_every_result_small():
    convo = [{"role": "user", "content": _han(0, 100_000)}, *_calls(1, _han(1, 20_000), _han(2, 20_000))]
    fit = _fit()
    assert not fit(convo)
    fit.last(convo)
    assert all(context.text_tokens(m["content"]) <= PLAN.small + 20 for m in convo if m.get("role") == "tool")
