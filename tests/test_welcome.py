"""The page chat's record, which the reply loop's rebuild rewrites; first-run setup is in tests/interface/ and the
server's boundary is a floor in tests/floors/boundary/."""
from __future__ import annotations


import pytest

from mibo import setup_wizard as sw
from mibo import welcome


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    value = welcome.Workspace(tmp_path)
    monkeypatch.setattr(sw, "probe_key", lambda *a, **kw: sw.Probe("http://127.0.0.1", 200, ""))
    yield value
    value.close()


def test_a_memory_answer_is_marked_and_a_forget_marks_the_words_on_the_page(workspace, monkeypatch):
    sw.write_env(workspace.env, {"LLM_API_KEY": "key", "PERSONA_NAME": "Mira", "AGENT_LANG": "en"})
    answers = {"my sister lives in Denver. The weather is fine": ["nice city"],
               "Mira, what do you remember": ["Here's what I remember:", "About you: your sister lives in Denver"],
               "Mira, forget Denver": ["forgotten: your sister lives in Denver"],
               "Mira, forget Paris": ["nothing matching to forget"]}
    scrub = {"words": ["Denver"], "texts": [], "lines": [], "mark": "en"}

    async def reply(conversation, parts, *, choices=(), run=None):
        said = parts[-1]["text"]
        return [{"type": "text", "text": t, **({"scrub": scrub} if said == "Mira, forget Denver" else {})}
                for t in answers[said]]

    monkeypatch.setattr(workspace.character, "reply", reply)
    import asyncio
    assert "command" not in asyncio.run(workspace.chat("my sister lives in Denver. The weather is fine"))["messages"][-1]
    recall = asyncio.run(workspace.chat("Mira, what do you remember"))
    said = [m for m in recall["messages"] if m["role"] == "assistant"]
    assert [m.get("command") for m in said] == ["recall", "recall"]
    # The way to Settings › Memory goes under the answer's last line only.
    assert [bool(m.get("manage")) for m in said] == [False, True]
    # Nothing was forgotten: nothing changes on the page.
    kept = asyncio.run(workspace.chat("Mira, forget Paris"))
    assert "forgotLines" not in kept
    gone = asyncio.run(workspace.chat("Mira, forget Denver"))
    texts = [m["text"] for m in workspace.history()["messages"]]
    # Only the sentences holding the words become the mark; the command and its answer stay.
    assert texts == ["(forgotten) The weather is fine", "nice city", "Mira, what do you remember",
                     "Here's what I remember:", "(forgotten)", "Mira, forget Paris",
                     "nothing matching to forget", "Mira, forget Denver", "forgotten: your sister lives in Denver"]
    assert gone["forgotLines"] == 2 and gone["messages"] == workspace.history()["messages"]
    kept = "".join(body for (body,) in workspace.db.read("SELECT body FROM page_messages"))
    assert "Denver" not in kept.replace("forget Denver", "").replace("your sister lives in Denver", "")
