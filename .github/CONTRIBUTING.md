# Contributing

## Setup

```bash
git clone https://github.com/wangkant/mibo
cd mibo
python -m pip install -e ".[dev]"
mibo
```

Run from a clone, the clone is the home folder: `.env`, `persona.txt` and `runtime/` sit beside `mibo/` and are gitignored. `mibo --home DIR` uses another folder.

## Checks

Run what CI runs before opening a pull request:

```bash
python -m pytest -q
ruff check . --select F401,F811,F821,F841
python -m compileall -q mibo tools tests
```

The suite stubs the model and needs no key. The ruff line catches dead and undefined names only.

| Workflow | Runs |
|---|---|
| `ci.yml` | The suite on Linux x64 (Python 3.10–3.14), Linux arm64 and macOS (3.12) and Windows (3.12 and 3.14), with `--durations=25`; a browser test that skips fails there (`MIBO_REQUIRE_BROWSER=1`). Builds the wheel and sdist on Linux, Windows and macOS, installs each into a clean venv and runs the two smoke scripts. Each job stops at 15 minutes. These jobs gate a push |
| `window.yml` | Opens the app window on Linux, macOS and Windows with a stand-in model (`tools/window_smoke.py`) |
| `sandbox.yml` | The checks that need a real window, pointer or keys (`tools/sandbox/`), each system on a hosted runner of its own; reports, does not gate |
| `xplat.yml` | The cross-system checks of `tools/xplat/` on Windows, macOS, Linux X11 and Linux Wayland: bash reading, the Electron pane and Cua Driver; reports, does not gate |
| `release.yml` | See [Releasing](#releasing) |

No workflow holds or asks for a model key.

### Smoke scripts

Run both after changing packaging, the CLI, the local page or the files a wheel ships. `package_smoke.py` checks the installed command's `--version`; `welcome_smoke.py` opens the installed page, saves a key, chats and exits, on localhost only.

```bash
python -m pip install build
python -m build
python -m venv /tmp/smoke-env
/tmp/smoke-env/bin/python -m pip install dist/*.whl     # Windows: Scripts\python.exe
python tools/package_smoke.py --mibo /tmp/smoke-env/bin/mibo --version X.Y.Z
python tools/welcome_smoke.py --mibo /tmp/smoke-env/bin/mibo
```

### Real-model benches

The scripts under `tools/` that call a real model (`tools/everyday.py`, `tools/review_score.py`, `tools/memory_life.py`, `tools/recsim/` and the rest) run only on your own computer, by hand, never in CI. Give each run a scratch home of its own (`mibo --home DIR`, or `AGENT_HOME=DIR`) whose `.env` holds `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`; the key stays in that file and is never committed. Pass `--budget` where a script takes one. `tools/sandbox/everyday.py` reads its key from `SANDBOX_MODEL_KEY` and reports itself as not run without one.

### Tests

- A test is a module-level `test_*` function in `tests/test_*.py`; pytest is the only test dependency.
- Tests never write the repository's real state. Use the `tmp` fixture (`tests/conftest.py`) and `isolated_agent(tmp, ...)` (`tests/helpers.py`), a real `Agent` whose state lives under `tmp`.
- Nothing Windows-only runs at import time or in a test without a stand-in: the same suite runs on Linux.

## Code layout

Paths are under `mibo/` unless noted.

| Area | Where |
|---|---|
| App, window and Settings | `cli.py`, `welcome.py` (local page, token, API), `window.py`, `static/welcome/`, `panels/` (one module per Settings section) |
| Character and chat | `agent.py`, `character.py`, `dm.py`, `turns.py`, `prompt.py`, `prompts.py`, `llm.py`, `endpoints.py`, `abilities.py` (what the model can do, measured by probes), `modelcatalog.py` (what the bundled models.dev snapshot lists for it) |
| Memory | `person.py`, `consolidate.py`, `memory.py`, `memstore.py`, `chatlog.py`, `pastchats.py`, `retrieval.py`, `ranking.py`, `embeddings.py` |
| Recommendations and walks | `recommend.py`, `taste.py`, `pacing.py`, `roam.py`, `library.py`, `today.py`, `sources/` |
| Web and built-in browser | `web/`, `browser.py`, `browsed.py`, `signwatch.py`, `installed_browser.py`, `search.py`, `ingestion.py` |
| Other programs (Windows) | `desk/` (UI Automation), `see/` (by screenshot, where no structure can be read) |
| Commands and files | `shell/` |
| Approval | `cardwait.py`, `review.py`, `turnon.py`, and the `gate.py` / `grants.py` of `web/`, `desk/` and `shell/` |
| Skills and MCP | `skills/`, `mcp_*.py`, `runtimes.py`, `secretstore.py` |
| Scheduled tasks | `schedule.py`, `schedule_tool.py` |
| Desktop figure | `petchat.py`, `petoff.py`, `petshot.py`, `remarks.py`, `activity.py`, `quiet.py` |
| Languages | `i18n.py`, `locales/`, `langs.py`, `chatlangs/`, `timewords.py` |
| Home, settings and storage | `home.py`, `paths.py`, `settings.py`, `config_env.py`, `setup_wizard.py`, `storage.py` |
| Packaging | `packaging/windows/` (repository root) |
| Scripts | `tools/` (repository root): measurements and checks, not shipped |

To add a setting, add it to `AgentSettings` in `settings.py`, read it with the `config_env` helpers and document it in `.env.example`. `tests/parts/settings/test_settings_template.py` fails when a setting the code reads is missing there.

## Languages

The interface ships in ten languages: English, 简体中文, 繁體中文, 日本語, 한국어, Español, Français, Deutsch, Português (Brasil) and Русский.

- Catalogs: `mibo/locales/<code>.json`, in i18next's JSON layout. `tools/locale_gaps.py` lists what a catalog lacks; `tests/test_locales.py` fails on a gap.
- Chinese interface text is written, formal Chinese. `tools/copy_check.py` lists spoken forms; a line that is the character's or the person's own words carries a `copy: voice` comment.
- Documents: `README.md`, `README.zh-CN.md`, `docs/readme/README.<code>.md` and `docs/DISCLAIMER[.<code>].md`. English is the source; a change of fact goes into every translation.

## Style

- Code, comments, logs and commit messages are in English.
- Comments state what the code cannot show; they do not narrate the next line.
- A change to prompt text changes behaviour: describe the failure it fixes in the pull request.
- No AI-assistant tooling files (`CLAUDE.md`, `.cursor*`, agent scratch folders) in the repository.
- Never attach `.env`, `runtime/`, keys, tokens or real conversations to an issue or a commit.

## Releasing

A release is a tag on `main`, built into the Windows installer and published as a GitHub Release. There is no PyPI package. Only the maintainer cuts a release.

1. Set the version in `pyproject.toml` and `mibo/__init__.py`; a test fails if they differ.
2. Refresh the model catalogue with `python tools/modelcatalog.py`: it downloads models.dev's `api.json`, keeps the services the provider list in Settings offers, and writes `mibo/modelcatalog.json` with the day and sha256. Commit it if it changed; the line it prints names the Settings services the catalogue does not hold.
3. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add an empty `## [Unreleased]` above it. That section becomes the release notes.
4. Merge to `main`, wait for CI, then tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The tag starts `.github/workflows/release.yml`:

| Job | Does |
|---|---|
| `test` | Refuses a tag not on `main`, runs the suite, checks the tag against `pyproject.toml` and takes the notes from `CHANGELOG.md` (`tools/release_notes.py`) |
| `windows-installer` | `packaging/windows/build.ps1`: PyInstaller, then Inno Setup, giving `mibo-X.Y.Z-setup.exe` |
| `macos-app` | The same PyInstaller spec, giving an unsigned `mibo-X.Y.Z-macos-arm64.dmg` |
| `github-release` | Creates the GitHub Release for the tag with both files and the notes |

A manual run from a branch builds the two files as workflow artifacts and publishes nothing. To build the installer locally:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1   # dist\mibo-X.Y.Z-setup.exe
```
