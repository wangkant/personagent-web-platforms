"""The page chat's failure report through the character's private reply seam, which the reply loop's rebuild
replaces; the rest of the page chat is in tests/interface/page_chat/ and its picture route is a floor in
tests/floors/boundary/."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from mibo import setup_wizard as sw
from mibo import welcome

TOKEN = "test-local-capability"
HEADERS = {welcome.TOKEN_HEADER: TOKEN}


@pytest.fixture
def workspace(tmp_path):
    value = welcome.Workspace(tmp_path)
    sw.write_env(value.env, {"LLM_API_KEY": "key"})
    yield value
    value.close()


def _page_agent(tmp_path):
    from helpers import isolated_agent

    return isolated_agent(tmp_path / "agent")


def _through(monkeypatch, workspace, agent):
    """The page's character is `agent`."""
    async def ready():
        return agent

    monkeypatch.setattr(workspace.character, "_ready", ready)


def test_a_model_failure_is_reported_in_the_page_and_never_stored(monkeypatch, workspace, tmp_path):
    """Every failure, not once per cooldown, and the casual excuse never becomes a message."""
    fail = [True]
    agent = _page_agent(tmp_path)

    async def chat_dm(history, pkey="", **kwargs):
        if fail[0]:
            raise TimeoutError("model down")
        return "hi back"

    agent._chat_dm = chat_dm
    _through(monkeypatch, workspace, agent)
    monkeypatch.setattr(workspace.character, "_build", lambda: None)
    with TestClient(welcome.create_app(workspace.home, TOKEN, workspace=workspace),
                    base_url="http://127.0.0.1") as client:
        for _ in range(2):
            answer = client.post("/api/chat", headers=HEADERS, json={"text": "hi"})
            assert answer.status_code == 502 and answer.json() == {"error": "chat_failed"}
            assert workspace.history()["messages"][-1]["failed"] == "chat_failed"
        fail[0] = False
        answer = client.post("/api/chat", headers=HEADERS, json={"text": "hi"}).json()
    assert answer["reply"] == "hi back"
    # The excuse never becomes a message; the unanswered ones stay marked.
    assert [(m["text"], m.get("failed")) for m in workspace.history()["messages"]] == [
        ("hi", "chat_failed"), ("hi", "chat_failed"), ("hi", None), ("hi back", None)]
