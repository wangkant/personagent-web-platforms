# MCPMark Filesystem bench

Mibo itself on MCPMark's Filesystem standard suite: the app's own reply loop, gates and cards, with the official
filesystem MCP server added as a connector. Each task's own `verify.py` scores the files left on disk.

| | |
|---|---|
| Benchmark | MCPMark (ICLR 2026, arXiv 2509.24002), Filesystem service, standard suite: 30 tasks in 10 categories. The repository's standard tasks are the MCPMark Verified set since 2026-06-12 |
| Source | https://github.com/eval-sys/mcpmark, Apache-2.0. Measured at commit cd45b7f (2026-06-12) |
| Data | Ten test folders from `storage.mcpmark.ai/filesystem/<category>.zip`, 51 MB zipped and 65 MB unpacked, kept outside the repo |
| Server | `npx -y @modelcontextprotocol/server-filesystem@2025.12.18 <task folder>`, the version MCPMark Verified pins. Node.js comes from the app's own `runtimes.install` |
| Prompt | `description.md` plus the note MCPMark's harness appends (`_format_task_instruction`) |
| Score | Each task's `verify.py` with `FILESYSTEM_TEST_DIR` set to the task's folder; exit code 0 is a pass |

## Run

```
python tools/bench/mcpmark/run.py fetch --data <data folder>
python tools/bench/mcpmark/run.py check --mcpmark <mcpmark checkout> --data <data folder> --work <folder>
python tools/bench/mcpmark/run.py run --mcpmark <mcpmark checkout> --data <data folder> --work <folder> \
    --keys <.env with LLM_API_KEY and LLM_BASE_URL> [--budget 0.75] [--model deepseek/deepseek-v4.1-flash] \
    [--only desktop/music_report,...] [--sample 12 --seed 7] [--out <json>]
```

Use the `.venv-web-platforms` interpreter from the repository root, so the checkout's own `mibo` is imported.

- `fetch` unpacks each file with the modification time from the zip's extended timestamp, as Info-ZIP's `unzip`
  does in MCPMark's harness.
- `check` runs every `verify.py` on its untouched folder, which must fail. It then sends three tasks through the
  whole app with a stand-in model: a right answer must pass and a wrong one must fail. MCPMark publishes no
  reference solutions, so the right answers come from the expected data in the verify scripts.
- `run` gives each task:
  - a fresh home under `--work`;
  - a fresh copy of its folder;
  - the connector, added through `POST /api/mcp {"add": snippet}` as Settings adds a pasted snippet;
  - the app started without a window at below-normal priority;
  - one chat turn;
  - then `verify.py`.

  One task runs at a time.
- `--sample N --seed S` runs a part of the suite, stratified by category:
  - seats go by largest remainder of each category's share, at least one each;
  - `random.Random(S)` breaks ties and shuffles each category's sorted tasks, and the first ones are drawn;
  - a task whose scorer a correct answer cannot pass on Windows (`UNTRUSTED_ON_WINDOWS`, with the evidence) is
    replaced by the next one of its category.

  The summary states the categories, the draw, the exclusions and the tasks measured. A part is not the suite:
  its pass rate cannot be set beside published 30-task scores as equal.

## Cards

Each card is answered as the person who asked for the task would answer it:

- Yes: the connector's start, its tools, turning an ability on, and a folder or command inside the task's folder.
- No: a folder card for another folder, a command naming a path outside the task's folder or running elsewhere on
  relative names, a program's window, or the browser.

The count of each kind is recorded per task.

## Spend

Model calls go through `tools/everyday.py`'s metering pass-through. It refuses calls past `--budget`, and no task
starts with less than $0.03 left. It passes the host's model list on uncounted, so the app learns the model's
context window there as it would from the host itself; each task's record states the window used (`window`).
The key is read from `--keys` and written only to each scratch home's `.env`.

## Records

| Where | What |
|---|---|
| `<work>/<task>/result.json` | Reply, steps, cards, MCP log, model calls (tokens and cost), verify output |
| `<work>/model.jsonl` | One row per model call |
| `--out` | Summary: model, commit, command, totals, per-task rows, each published model's pass@1 on the same tasks |
| `published.json` | mcpmark.ai's board pass@1 and each model's passes of 4 runs per task, fetched 2026-11-21 |

Python runs `verify.py` in UTF-8 mode, as on the macOS and Linux systems the scripts were validated on. Several
of them read files without naming an encoding.
