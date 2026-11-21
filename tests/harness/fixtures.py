"""The harness's fixtures, registered for the whole suite by the root conftest (pytest_plugins):
    model   the ScriptedModel, one server a session, emptied before and after each test; a request no script
            covers fails the test that made it
    app     the app on this test's home, served on 127.0.0.1 with the model behind it
    turn    turn(message, *rounds, cards="yes") -> TurnResult, one whole turn through the page
    site    the stand-in site, one server a session, emptied for each test; the app may reach it
    web     the stand-in public internet: names a test gives answer with public addresses that reach `site`"""
from __future__ import annotations

import functools
import os
from pathlib import Path

import pytest

from harness.app import App, quiet_start
from harness.model import ScriptedModel
from harness.sites import PublicWeb, Site, reachable
from harness.turn import run_turn


@pytest.fixture(scope="session")
def scripted_model_server():
    server = ScriptedModel()
    yield server
    server.close()


@pytest.fixture
def model(scripted_model_server):
    scripted_model_server.reset()
    yield scripted_model_server
    try:
        scripted_model_server.check()
    finally:
        scripted_model_server.reset()


@pytest.fixture
def app(model, monkeypatch, tmp_path):
    """No browser is found (a test that wants one says so), the first start probes no public site, and a failed
    model call is not retried, so a failure shows at once."""
    quiet_start(monkeypatch)
    served = App(Path(os.environ["AGENT_HOME"]), model, PERSONA_BROWSER_PATH=str(tmp_path / "no-browser"),
                 LLM_MAX_RETRIES="0").open()
    yield served
    served.close()


@pytest.fixture
def turn(app):
    return functools.partial(run_turn, app)


@pytest.fixture(scope="session")
def stand_in_site(tmp_path_factory):
    server = Site(tmp_path_factory.mktemp("site"))
    yield server
    server.close()


@pytest.fixture
def site(stand_in_site, monkeypatch):
    stand_in_site.reset()
    reachable(monkeypatch, stand_in_site)
    return stand_in_site


@pytest.fixture
def web(site, monkeypatch):
    """Held lookups are let go when the test ends; a test that holds one starts no app, whose stop would wait."""
    public = PublicWeb(monkeypatch, site)
    yield public
    public.release()
