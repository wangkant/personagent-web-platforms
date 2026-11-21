## Change

<!-- What the change does and the problem it fixes. For a prompt change, the reply or action seen and the one wanted. -->

## Checks

- [ ] `python -m pytest -q`
- [ ] `ruff check . --select F401,F811,F821,F841`
- [ ] `python tools/copy_check.py` lists none of the new interface text
- [ ] `tools/package_smoke.py` and `tools/welcome_smoke.py`, if packaging, the CLI, the local page or shipped files changed
- [ ] Interface text in all ten catalogs (`mibo/locales/`); `settingsfile.Settings` and the documents updated where a setting or a fact changed
- [ ] `CHANGELOG.md` has a line under `## [Unreleased]` for a visible change
- [ ] No keys, tokens or real conversations in the diff
