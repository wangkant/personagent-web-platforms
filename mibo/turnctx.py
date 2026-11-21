"""Lines a module adds to a turn's context: each registers a provider (said, chat) -> lines, and the prompt
(prompt.py) appends what they give after its own context, in the order they registered."""
from __future__ import annotations

from typing import Callable

Provider = Callable[[str, str], list]

_PROVIDERS: list[Provider] = []


def register(provider: Provider) -> None:
    if provider not in _PROVIDERS:
        _PROVIDERS.append(provider)


def lines(said: str, chat: str) -> list[str]:
    """Every provider's lines for the person's message `said` in the chat `chat`, in registration order."""
    return [str(line) for provider in _PROVIDERS for line in provider(said, chat) or []]
