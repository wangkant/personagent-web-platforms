# WebVoyager subset for the browser hands

> **Runner retired on 2026-11-21.** `run.py` and `report.py` measured `web/operate.run`, the browser errand loop, which
> went with it: the chat now works pages itself with `act` (mibo/surface.py). The task data here stays. A new
> runner sends each task through the chat as `tools/oploop.py` does (the app in process, a stand-in pane over
> headless Chromium, every model call counted). The rest of this file describes the retired runner and its
> numbers as they were.

Thirty tasks from WebVoyager on real sites, each worked the way Mibo works a page: pageops' lines from the
accessibility tree, `web/operate.run`'s prompt, reply form and budget, every action sorted by `gate.py`. The page is
a headless Edge tab (Playwright, `channel="msedge"`), signed out, fresh for each task, with the headless browser's
own user agent. The bench answers no to every card the gate raises and the run stops there. The outcome is judged
WebJudge-style from the final page.

## Data

WebVoyager (He et al., ACL 2024, arXiv:2401.13919), https://github.com/MinorJerry/WebVoyager,
`data/WebVoyager_data.jsonl`, 643 tasks on 15 sites. Licence: Apache-2.0. `tasks.json` holds the 30 tasks used here,
with their ids, start pages and words unchanged.

The subset follows one rule. Sites whose start page a signed-out headless browser could read (checked
2026-11-20) and that show no consent banner a run might accept, take the first tasks of each site in the dataset's
order. Tasks that need a sign-in, a cart or another site as their goal are skipped: Huggingface--1 (Inference API),
Amazon--1 (save to a list) and Google Search--4 (Steam).

- In: Apple 0-3, ArXiv 0-3, GitHub 0-3, Coursera 0-2, ESPN 0-2, Huggingface 0, 2, 3, Wolfram Alpha 0-2,
  Amazon 0, 2, 3, Google Search 0-2.
- Out:
  - Allrecipes shows a Cloudflare check.
  - Booking answers with a challenge page.
  - BBC News and Cambridge Dictionary open with a consent banner ("Accept and Continue", "Accept Default
    Settings"). `gate.classify` lets these be pressed without a card.
  - Google Maps and Google Flights are drawn pages for a text model.

## Run

```
python -I tools/bench/webvoyager/run.py --env <.env with LLM_API_KEY and LLM_BASE_URL> [--model deepseek/deepseek-v4.1-flash] [--out <folder>] [--cheapest]
python -I tools/bench/webvoyager/report.py <folder>
```

Flags:

- `--ids a,b`: a part of the thirty.
- `--workers 1`: the default. Tasks run one at a time with a three-second pause between them.
- `--budget 0.05`: dollars, from OpenRouter's usage.cost. No new task starts past it.
- `--tools no|yes`: what the app's check recorded. The default is the least able model's ceiling of 12 calls.
- `--cheapest`: OpenRouter's provider routing by price, for the same model.

Python: the `.venv-web-platforms` interpreter (playwright, httpx). The model call is the MiniWoB bench's
(`../miniwob/run.py`). The key is never printed or written.

The judge follows WebJudge (Online-Mind2Web, arXiv:2504.01382), with the final page's lines in place of key
screenshots:

1. One call lists the task's key points from its words alone.
2. A second call decides from the key points, the run's steps, its answer and the final page. It says success
   only when every key point is met, a named filter or sort shows as applied, and the answer agrees with the page.

The judge is the same cheap model. A person reading a sample writes `<folder>/hand.json`; `report.py` sets the two
side by side.

## First run (2026-11-20, deepseek/deepseek-v4.1-flash, least able budget)

Thirty tasks were run against the tree at 04a212a (16:34 to 16:50 PST). The product file hashes are in each row's
`code`. The run used the least able budget: 12 calls, a 20,000-character page and 3,000 answer tokens. The first
three tasks went through OpenRouter's default routing and the other 27 used `--cheapest`. Results and hand reading
are kept outside the repo.

- Done: 14/30 = 47% by a person reading each final page and answer, and 17/30 = 57% by the judge. They agree on
  27/30. All three disagreements are the judge being lenient:
  - It passed two runs that stopped at a card and gave the person no answer (GitHub--2, Wolfram Alpha--0).
  - It passed newest models offered as "popular" (Huggingface--2).
- Per site, by the person: GitHub 3/4, Apple 3/4, ArXiv 3/4, ESPN 3/3, Coursera 1/3, Amazon 1/3, Huggingface 0/3,
  Wolfram Alpha 0/3, Google Search 0/3.
- How the 16 misses ended:
  - 7 stopped at a card the bench declined:
    - 6 were the typed-text hold firing on the site's own telemetry: Wolfram's `POST /wal.png` carrying the
      query, GitHub's sponsor-button batch, Coursera's event batch and Amazon's `/s/query`.
    - 1 was an order card on the link "Buy, iPhone 18 Pro", which only opens the store page.
  - 3 were Google's reCAPTCHA on the first page. This is the headless browser, handed back as designed.
  - 3 were wrong or partial answers (a 2-week course called 1-3 months, trending taken for most likes, newest
    taken for popular).
  - 2 hit the 12-call ceiling.
  - 1 was a filter link outside the viewport, clicked four times and handed back.
- Per task: 4.8 model calls, 28k input tokens (8k cached), 412 output tokens. Time was 19 s on average, 16.5 s
  median and 42 s p90. Cost was $0.0014 for the run and $0.0003 for the judge. The default routing cost about
  three times that ($0.0029 to $0.0107 a task). The 30 tasks cost $0.051 in all.
- 10 of the 17 done answers are in Chinese and one is in Finnish, although every task and the bench's `lang` are
  English. The loop does not tell the model the person's language.

For scale, these are published numbers from other harnesses and judges:

- WebVoyager: frontier agents 85-99% (browser-use 89.1%, Stagehand with Sonnet 90%, Operator 87% self-reported
  and 68.6% audited).
- Hard sets: cheap models about 35-37% (BU Bench V1: gpt-5-mini 37.0%, gemini-2.5-flash 35.2%).

Thirty tasks give a wide interval. With 47% measured, the 95% range is about 30-65%.
