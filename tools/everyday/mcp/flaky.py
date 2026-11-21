"""Stand-in MCP server 'flaky': crash (the program exits in the middle of the call) and repeat (a count of at least
one, so a count of zero never reaches it).

    python flaky.py <folder>     (calls in <folder>/mcp-calls.jsonl)
"""
import os
import sys
from pathlib import Path

from _serve import serve, text

TOOLS = [
    {"name": "crash", "description": "Run the self-test.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "repeat", "description": "Repeat a word a number of times.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"word": {"type": "string"},
                                                      "count": {"type": "integer", "minimum": 1}},
                     "required": ["word", "count"]}},
]


def run(tool: str, args: dict) -> dict:
    if tool == "crash":
        os._exit(3)
    return text(" ".join([str(args["word"])] * int(args["count"])))


serve("flaky", "Small test tools.", TOOLS, run, str(Path(sys.argv[1]) / "mcp-calls.jsonl"))
