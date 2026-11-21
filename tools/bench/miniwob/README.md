# MiniWoB++ bench for the browser hands

> **Runner retired on 2026-11-21.** `run.py` and `report.py` measured `web/operate.run`, the browser errand loop, which
> went with it: the chat now works pages itself with `act` (mibo/surface.py). The task data here stays. A new
> runner sends each task through the chat as `tools/oploop.py` does (the app in process, a stand-in pane over
> headless Chromium, every model call counted). The rest of this file describes the retired runner and its
> numbers as they were.

Forty small web tasks from MiniWoB++, each worked the way Mibo works a page: pageops' numbered lines from the
accessibility tree, `web/operate.run`'s prompt, reply form and budget (`operate.budget`: 12 model calls for the
least able model, 40 with `--tools yes`; three calls in which nothing moved hand the page back), every action
sorted by `gate.py`. The page is a headless Chromium tab (Playwright
over the installed Edge, `channel="msedge"`: no window, no browser download). The card the gate raises is answered
"yes" by the bench and kept. The task's own reward decides success: done, raw reward 1.0.

## Data

MiniWoB++, Farama Foundation, https://github.com/Farama-Foundation/miniwob-plusplus (maintained; checkout
33c3b4d, 2026-08-13). Licence: MIT (Copyright OpenAI 2016, Stanford 2018, Farama Foundation 2022), which allows
use, copying, modification and redistribution with the notice kept. Only `miniwob/html` is fetched (5.7 MB),
outside the repo:

```
cd ../ref/datasets/miniwob      # next to the repo: work/ref/datasets/miniwob
git clone --depth 1 --filter=blob:none --sparse https://github.com/Farama-Foundation/miniwob-plusplus.git src
cd src && git sparse-checkout set miniwob/html
```

The pages are served read-only from 127.0.0.1 (port 59260 by default) to the headless tab; nothing in the
download is run by Python.

## Run

```
python -I tools/bench/miniwob/run.py --env <.env with LLM_API_KEY and LLM_BASE_URL> --model deepseek/deepseek-v4-flash [--out <folder>]
python -I tools/bench/miniwob/report.py <folder>
```

Another model is one flag: `--model <openrouter id>`. Other flags: `--tasks a,b` (a subset, or other MiniWoB++ tasks such as drag-items), `--seeds 0,1,2,3,4`
(default), `--workers 4`, `--budget 0.38` (dollars from OpenRouter's usage.cost; no new episode past it),
`--port`, `--data <miniwob html folder>`, `--tools no|yes` (whether the app's check found the model calling a tool
by itself; the context window is read from the host's model list), `--cheapest` (OpenRouter's provider routing by
price, same model). Without `--out` the run goes to `../ref/bench/miniwob/<model>`. A rerun into the same folder skips the episodes already in
`results.jsonl`. Each episode's pages, model answers and cards go to `<folder>/episodes/`; the key is never
printed or written. Python: the `.venv-web-platforms` interpreter (playwright, httpx).

One episode: the task page fresh in its own browser context, `Math.seedrandom(seed)` (what the gym env's
`reset(seed=...)` does), the task's timer lifted to ten minutes (a model step takes seconds; the raw reward has no
time penalty), `core.startEpisodeReal()`, then `operate.run` with the task's words (`core.getUtterance()`). It ends
when the task's reward fires or `operate.run` stops. The model call is search.py's: max_tokens from
`operate.budget`, temperature 0.2, a JSON object, thinking off.

`report.py` sorts failures by a first guess from how each ended; `<folder>/causes.json`
(`{"task-seed": [cause, why]}`) holds the causes read by hand and overrides the guess.

## The forty tasks

`tasks.py`. Taken: buttons and links (click-button, -sequence, click-link, click-dialog, -2, click-widget,
click-option, click-menu), forms (enter-text, -2, enter-date, enter-time, form-sequence-2, multi-layouts,
multi-orderings, login-user, login-user-popup), pickers (choose-list, choose-date-easy, choose-date), toggles
(click-checkboxes, -soft, -transfer, click-tab, -2, click-collapsible-2, navigate-tree), search
(search-engine, use-autocomplete), scrolling (click-scroll-list, scroll-text), reading a value and using it
(read-table, find-word, copy-paste, phone-book), inbox and feed (email-inbox, email-inbox-forward-nl,
email-inbox-reply, social-media-some), a flow (book-flight). Left out: drawing and dragging, canvas shapes and
colours, timing and dexterity games, puzzles and arithmetic, the terminal and text editor, and the
-nodelay/-easy/-hard copies of a task already in.

`published.json`: other agents' per-task rates on the same tasks, from the Synapse paper's appendix table
(arXiv:2306.07863), and BrowserGym's overall MiniWoB rates for general models (arXiv:2412.05467).

## First run (2026-11-19, deepseek/deepseek-v4-flash)

200 episodes (40 tasks x seeds 0-4), kept with the hand-read causes in `../ref/bench/miniwob/deepseek_deepseek-v4-flash`.

- Success 126/200 = 63%. Buttons and links 35/40, toggles 34/35, reading a value 14/20, forms 25/45, pickers 9/15,
  search 6/10, scrolling 3/10, inbox and feed 0/20, book-flight 0/5.
- Per episode: 3.3 model calls, 4.6k input tokens (3.2k of them cached), 270 output tokens, 13 s, $0.0003.
  The whole run cost $0.06.
- Failures (74), read by hand: could not see it 33 (icons with no name: the send and star icons in the inbox and
  the feed's Like/Share; a textarea cut at 100 characters; a trailing space folded away; options behind "… N
  more"), could not do it 24 (native date and time inputs; hover menus; a multi-select; the datepicker box that
  clears typed text; a covered link), the gate 10 (login-user and login-user-popup: the password is never typed,
  by design), misread the task 5, ran out of steps 2.
- Cards: 124, in 91 episodes. 88 are on Submit buttons. The card named a button that is on the page 116 times; the
  other 8 were Enter presses. It showed the text that was typed 121 times. Where a card could not let the person
  catch a mistake: "Reply Forward" (two buttons printed as one), and "Share" without the post it belongs to (one
  episode shared the wrong post). Where it could: choose-date-3, whose card showed the wrong date 03/10/2016.

For comparison, published per-task rates on these tasks are near 1.0 (Synapse, RCI, AdaPlanner, CC-Net; humans
0.83-1.00, `published.json`). Those agents had task demonstrations or millions of training episodes. General models
with BrowserGym's generic agent and no demonstrations score 57-70% over the whole 125-task set, and that set
includes the drawing and dexterity tasks left out here. These are other people's pages and tasks, not Mibo's
people: the numbers show what the lines and actions can and cannot reach, not how often an errand of theirs works.
Two product versions are mixed: 192 episodes ran on one, and the 8 rerun episodes ran on a newer operate.py and
pageops.py (the hashes are in each row's `code`).

## Rerun (2026-11-20, deepseek/deepseek-v4.1-flash)

80 episodes (seeds 0 and 1) on the tree at 04a212a, with the least able budget (`--tools no`, 12 calls) and
`--cheapest`.

- Success 58/80 = 72.5%. The first run had 49/80 = 61% on the same seeds, with another model (v4-flash) and older
  code.
  - Up on 13 episodes: native date and time 4/4 (were 0/4), choose-list, scroll-text, click-scroll-list,
    choose-date, phone-book, click-widget and one email-inbox.
  - Down on 4: click-checkboxes-soft twice, search-engine and multi-layouts.
  - Still at 0: login-user and login-user-popup (by design), book-flight, search-engine. Inbox and feed are 1/8.
- Per episode: 3.2 model calls, 7.3k input tokens (5.8k cached), 188 output tokens, 11.6 s, $0.00012. The 80
  episodes cost $0.0096 in all.
- Cards: 47, in 7 episodes, all on the inbox and feed tasks, the same comment card asked again on each press. The
  first run had 44 in 35 of these 80 episodes. No card was raised on a bare Submit.
