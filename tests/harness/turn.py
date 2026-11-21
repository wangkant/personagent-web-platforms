"""One whole turn through the page: the person's message posted to /api/chat, each card the turn raises
answered through the page's own card routes as the policy says, and what came of it read back: the reply,
its steps, its sources, the cards, and what reached the model, round by round."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

# The card routes the page polls (petline.js), and how each takes a press of yes, no or always.
CARD_ROUTES = {
    "shell": "/api/shell/card",
    "desk": "/api/programs/card",
    "mcp": "/api/mcp/card",
    "turnon": "/api/turnon/card",
    "operate": "/api/browser/confirm",
}
POLL_S = 0.05


def _press(kind: str, ident: str, answer: str) -> dict:
    yes = answer in ("yes", "always")
    if kind == "mcp":
        return {"id": ident, "answer": {"yes": "once", "always": "always"}.get(answer, "no")}
    if kind == "desk" and answer == "always":
        return {"id": ident, "answer": "always"}
    if kind == "shell" and answer == "always":
        return {"id": ident, "yes": True, "scope": "always"}
    if kind == "operate":
        return {"id": ident, "yes": yes, "never": answer == "never"}
    return {"id": ident, "yes": yes}


@dataclass
class TurnResult:
    status: int
    reply: str
    entries: list
    cards: list = field(default_factory=list)
    requests: list = field(default_factory=list)

    @property
    def trace(self) -> list:
        """The steps the reply took, as stored with it for activity.js to draw."""
        return [step for entry in self.entries for step in entry.get("trace") or []]

    @property
    def sources(self) -> list:
        return [source for entry in self.entries for source in entry.get("sources") or []]

    def asked(self, purpose: str = "reply") -> list:
        """The bodies of this turn's model requests of one purpose, in order."""
        return [r["body"] for r in self.requests if r.get("purpose") == purpose]

    def calls(self) -> list:
        """Every tool call the model made this turn, as {"id", "name", "arguments"}, in order."""
        made = []
        for body in self.asked():
            for message in body["messages"]:
                for one in message.get("tool_calls") or []:
                    if all(one["id"] != m["id"] for m in made):
                        made.append({"id": one["id"], **one["function"]})
        return made

    def result_of(self, call_id: str) -> str | None:
        """The result the app passed back to the model for one call, read from the next request; None when no
        request carried it."""
        for body in self.asked():
            for message in body["messages"]:
                if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                    return message["content"]
        return None


def run_turn(app, message: str, *script, cards="yes", conversation: str = "main",
             attachments: list | None = None) -> TurnResult:
    """Send `message` in a chat, with `attachments` as chat.js sends them ({"kind": "picture", "data": base64});
    `script` rounds are queued on the model first. `cards` answers every card: one of "yes", "no", "always",
    "never", or a list taken in order. Fails when the model was asked something no script covers or a scripted
    round was never asked for."""
    model = app.model
    model.script(*script)
    seen = len(model.requests)
    answers = [cards] if isinstance(cards, str) else list(cards)
    raised: list = []
    done = threading.Event()
    sent: dict = {}

    def post() -> None:
        try:
            sent["response"] = app.post("/api/chat", {"text": message, "chat": conversation,
                                                      "attachments": attachments or []}, timeout=300)
        finally:
            done.set()

    threading.Thread(target=post, daemon=True).start()
    while not done.wait(POLL_S):
        for kind, route in CARD_ROUTES.items():
            card = app.get(route).json().get("card")
            if card and all(card["id"] != c["card"]["id"] for c in raised):
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                raised.append({"kind": kind, "card": card, "answer": answer})
                app.post(route, _press(kind, card["id"], answer), own_page=True)
    response = sent["response"]
    model.check()
    assert model.left() == 0, f"{model.left()} scripted reply round(s) were never asked for"
    body = response.json()
    entries = [m for m in body.get("messages") or [] if m.get("role") != "user"]
    return TurnResult(response.status_code, str(body.get("reply") or ""), entries, raised, model.requests[seen:])
