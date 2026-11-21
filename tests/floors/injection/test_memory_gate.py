"""What is kept in memory reaches every later prompt, so a note that would instruct the character (a role,
an identity, a reply language, a trigger, a standing rule, a waived limit) is refused, while facts about the
person and the people around them, worded much like those, are kept. The notes are tests/data/attacks/
memory.jsonl, grouped by family."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from harness import attacks, app
from mibo import welcome
from mibo.agent import Agent
from mibo import settingsfile

NOTES = attacks.load("memory")
TOKEN = "test-local-capability"
HEADERS = {welcome.TOKEN_HEADER: TOKEN}


@pytest.mark.parametrize("note", NOTES)
def test_the_memory_gate_refuses_instructions_and_keeps_facts(note: dict) -> None:
    # Every memory write (a 记住, an import, an edit, the fold) passes its note through this gate.
    assert bool(Agent._validate_memory_candidate(note["text"])) is not note["refused"]


@pytest.mark.slow
def test_a_note_that_instructs_is_kept_nowhere_when_added_or_pasted_on_the_memory_page(tmp_path, monkeypatch):
    # Slow: the character starts and stops with the page, as it does for a person (2 to 3 s).
    rows = [param.values[0] for param in NOTES]
    attack_of = {row["family"]: row["text"] for row in reversed(rows) if row["refused"]}
    fact = next(row["text"] for row in rows if row["family"] == "fact")
    app.quiet_start(monkeypatch)
    workspace = welcome.Workspace(tmp_path)
    settingsfile.write(workspace.home, {"LLM_API_KEY": "key", "LLM_BASE_URL": "http://127.0.0.1:9/v1", "LLM_MODEL": "m"})
    with TestClient(welcome.create_app(workspace.home, TOKEN, workspace=workspace),
                    base_url="http://127.0.0.1") as client:
        # One attack of each family, pasted as another assistant's memory list and then added one by one.
        pasted = client.post("/api/person/import", headers=HEADERS, json={"lines": [*attack_of.values(), fact]})
        assert pasted.status_code == 200 and pasted.json()["imported"] == 1
        for attack in attack_of.values():
            added = client.post("/api/person/add", headers=HEADERS, json={"text": attack})
            # The page says why: it reads as an instruction.
            assert added.status_code == 400 and added.json()["error"] == "memory_not_instructions", attack
        # Kept as a sentence: its first letter may be raised.
        assert [row["text"].lower() for row in client.get("/api/person", headers=HEADERS).json()["facts"]] \
            == [fact.lower()]
    kept = "".join(path.read_text(encoding="utf-8", errors="replace")
                   for path in (tmp_path / "runtime").rglob("*") if path.is_file()).lower()
    assert fact.lower() in kept and not [attack for attack in attack_of.values() if attack.lower() in kept]
