"""Sorts the labelled commands in bash_corpus.jsonl with mibo/shell/bashread, the way the app reads a bash
command before running it, and holds the result to the floor below.

    python tools/xplat/bashjudge_check.py <empty scratch folder>

It makes <scratch>/files with a few files (the command's folder), <scratch>/home with a .env (the person's home
and the app's home) and <scratch>/elsewhere, and runs nothing. The floor:

- every "never" and "ask" row is sorted never or ask;
- at least 95% of "read" rows are sorted read;
- no "write", "delete" or "network" row is sorted read.

On a POSIX computer it also holds each reading against read.judge with only <scratch>/files granted: no
"never", "ask", "delete" or "network" row may come out "run". Exits 1 when the floor is not met.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mibo.shell import bashread
from mibo.shell import read as reading
from mibo.shell.grants import Grants

LABELS = ("read", "write", "delete", "network", "ask", "never")
CORPUS = Path(__file__).with_name("bash_corpus.jsonl")


def main() -> int:
    rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
    scratch = Path(sys.argv[1]).resolve()
    files, home = scratch / "files", scratch / "home"
    for folder in (files / "临时" / "子", home / "runtime" / "desktop", scratch / "elsewhere"):
        folder.mkdir(parents=True, exist_ok=True)
    for name, text in (("a.txt", "a" * 10), ("b.txt", "b" * 2000), ("notes.txt", "n"), ("报告.docx", "doc"),
                       ("临时/x.log", "x"), ("临时/子/y.log", "y"), ("tool.sh", "echo hi\n")):
        (files / name).write_text(text, encoding="utf-8")
    (home / ".env").write_text("SECRET=1\n", encoding="utf-8")
    posix = os.sep == "/"
    # Elsewhere the commands are sorted against the same layout by name, with nothing on disk to look at.
    cwd, user = (str(files), str(home)) if posix else ("/scratch/files", "/scratch/home")
    grants = None
    if posix:
        os.environ["AGENT_HOME"] = str(home)
        grants = Grants(home / "runtime" / "desktop", home)
        assert grants.add(str(files)) == "", grants.add(str(files))

    matrix: Counter = Counter()
    findings: list[str] = []
    for row in rows:
        r = bashread.read(row["cmd"], cwd, user)
        got = bashread.gist(r)
        label = row["label"]
        matrix[label, got] += 1
        if got != label:
            print(f"   {label:<7} sorted {got:<7} {row['cmd']!r}")
            for act in r.acts[:2]:
                print(f"        {act.kind}: {act.why[:120]}")
            for error in r.errors[:1]:
                print(f"        error: {error[:120]}")
        if grants is not None:
            verdict = reading.judge(r, grants).action
            if verdict == "run" and label in ("never", "ask", "delete", "network"):
                findings.append(f"judge runs a {label} row: {row['cmd']!r}")

    print(f"\n{len(rows)} rows. Sorted (rows) by label (columns):")
    print("         " + "".join(f"{g:>8}" for g in LABELS))
    for label in LABELS:
        print(f"{label:<9}" + "".join(f"{matrix[label, g]:>8}" for g in LABELS))

    total = {label: sum(matrix[label, g] for g in LABELS) for label in LABELS}
    held = sum(matrix[label, g] for label in ("never", "ask") for g in ("never", "ask"))
    floor = total["never"] + total["ask"]
    reads = matrix["read", "read"]
    false_reads = sum(matrix[label, "read"] for label in ("write", "delete", "network"))
    print(f"never and ask rows sorted never or ask: {held}/{floor}")
    print(f"read rows sorted read: {reads}/{total['read']} ({100 * reads / max(total['read'], 1):.1f}%)")
    print(f"write, delete and network rows sorted read: {false_reads}")
    if held < floor:
        findings.append(f"{floor - held} never or ask rows sorted to run without asking")
    if reads < 0.95 * total["read"]:
        findings.append(f"only {reads}/{total['read']} read rows sorted read")
    if false_reads:
        findings.append(f"{false_reads} write, delete or network rows sorted read")
    if grants is None:
        print("read.judge not run: it holds POSIX places only on a POSIX computer")
    for finding in findings:
        print(f"!! {finding}")
    print("floor met" if not findings else "floor not met")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
