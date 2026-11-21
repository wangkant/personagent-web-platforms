"""The same model without the app, for comparison: AgentDojo's own pipeline (its system message, OpenAILLM, its
tools loop, no defence) on the same cases run.py runs, so the difference between the two rows is what the app adds.

    <agentdojo python> -I tools/bench/agentdojo/bare.py --env <a home's .env holding the key> --out <folder>
        [--suites banking] [--budget 0.05] [--model deepseek/deepseek-v4.1-flash]
        [--upstream https://openrouter.ai/api/v1] [--attack both] [--sample 7:4] [--port 59724]

The key is read from --env and goes only into the Authorization header of the calls; the calls go through
tools/everyday.py's Meter, which refuses calls past --budget. Out: <out>/cases.jsonl in run.py's shape (utility,
security, the calls), <out>/model.jsonl, <out>/agentdojo-logs. report.py reads it as it reads a run of run.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

import openai  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from everyday import PRICE, Meter  # noqa: E402

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig  # noqa: E402
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement  # noqa: E402
from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM  # noqa: E402
from agentdojo.attacks.attack_registry import load_attack  # noqa: E402
from agentdojo.benchmark import run_task_with_injection_tasks, run_task_without_injection_tasks  # noqa: E402
from agentdojo.logging import OutputLogger  # noqa: E402
from agentdojo.models import MODEL_NAMES  # noqa: E402
from agentdojo.task_suite.load_suites import get_suite  # noqa: E402
from run import VERSION, BudgetSpent, read_done, sample_pairs  # noqa: E402


class Metered(BasePipelineElement):
    """The pipeline, refusing a case once the budget is reached, and keeping the case's calls for cases.jsonl."""

    def __init__(self, pipeline: AgentPipeline, meter: Meter):
        self.pipeline, self.meter, self.name = pipeline, meter, pipeline.name
        self.messages: list = []

    def query(self, query, runtime, env, messages=(), extra_args={}):
        if self.meter.spent >= self.meter.budget:
            raise BudgetSpent()
        mark = len(self.meter.rows)
        out = self.pipeline.query(query, runtime, env, messages, extra_args)
        if self.meter.spent >= self.meter.budget:
            raise BudgetSpent()
        self.messages, self.rows = out[3], self.meter.rows[mark:]
        return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--suites", default="banking")
    parser.add_argument("--budget", type=float, default=0.05)
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    parser.add_argument("--upstream", default="https://openrouter.ai/api/v1")
    parser.add_argument("--attack", choices=("both", "none", "important_instructions"), default="both")
    parser.add_argument("--sample", default="7:4")
    parser.add_argument("--port", type=int, default=59724)
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    meter = Meter(args.upstream, out / "model.jsonl", args.budget, PRICE)
    meter.serve(args.port)
    client = openai.OpenAI(base_url=f"http://127.0.0.1:{args.port}/v1",
                           api_key=dotenv_values(args.env)["LLM_API_KEY"])
    MODEL_NAMES["deepseek-v4.1-flash"] = "DeepSeek"
    llm = OpenAILLM(client, args.model)
    llm.name = "deepseek-v4.1-flash"
    pipeline = AgentPipeline.from_config(PipelineConfig(llm=llm, model_id=None, defense=None,
                                                        system_message_name=None, system_message=None))
    pipeline.name = "deepseek-v4.1-flash"
    agent = Metered(pipeline, meter)
    cases_file, cut = out / "cases.jsonl", []
    done = read_done(cases_file)
    for suite_name in args.suites.split(","):
        suite = get_suite(VERSION, suite_name)
        attack = load_attack("important_instructions", suite, agent)
        todo = []
        if args.attack in ("both", "none"):
            todo += [(u, None) for u in suite.user_tasks.values()]
        if args.attack in ("both", "important_instructions"):
            todo += [(suite.user_tasks[u], suite.injection_tasks[i]) for u, i in sample_pairs(suite, args.sample)]
        with OutputLogger(str(out / "agentdojo-logs")):
            for user_task, injection_task in todo:
                if (suite_name, user_task.ID, injection_task.ID if injection_task else None) in done:
                    continue
                agent.messages, agent.rows = [], []
                try:
                    if injection_task is None:
                        utility, security = run_task_without_injection_tasks(suite, agent, user_task, None, True,
                                                                              VERSION)
                    else:
                        result = run_task_with_injection_tasks(suite, agent, user_task, attack, None, True,
                                                               [injection_task.ID], VERSION)
                        utility, security = (v[(user_task.ID, injection_task.ID)] for v in result)
                except BudgetSpent:
                    cut.append(f"{suite_name} {user_task.ID} {injection_task.ID if injection_task else 'none'}")
                    continue
                calls = [{"function": c.function, "args": dict(c.args)} for m in agent.messages
                         if m["role"] == "assistant" for c in m.get("tool_calls") or []]
                row = {"suite": suite_name, "user_task": user_task.ID,
                       "injection_task": injection_task.ID if injection_task else None,
                       "utility": bool(utility), "security": bool(security) if injection_task else None,
                       "calls": calls, "truth": [], "attack_truth": [], "cards": [], "hub": [], "attempts": [],
                       "seconds": 0.0, "cost": round(sum(r["cost"] for r in agent.rows), 6),
                       "version": VERSION, "attack": "important_instructions" if injection_task else "none"}
                with cases_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                print(f"  {user_task.ID} {injection_task.ID if injection_task else 'none'}: utility {utility}"
                      + (f" attack {'WON' if security else 'failed'}" if injection_task else "")
                      + f"  calls {len(calls)} spent ${meter.spent:.4f}", flush=True)
    if cut:
        (out / "cut.json").write_text(json.dumps(cut), encoding="utf-8")
    print(f"spent ${meter.spent:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
