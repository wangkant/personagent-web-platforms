"""The stdio loop the MCP stand-ins of tools/mcpbench.py share: one JSON-RPC message a line, each tools/call written
to the call log before it is answered."""
import json
import sys


def serve(name: str, about: str, tools: list, run, log: str) -> None:
    """Answers initialize, ping, tools/list and tools/call (`run(tool, args)` gives the result) on stdin."""
    # The wire is UTF-8 whatever the console's code page; replies are written as ASCII JSON.
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        message = json.loads(line.decode("utf-8"))
        if "id" not in message:
            continue
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-11-25"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": name, "version": "1"}, "instructions": about}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools}
        elif method == "tools/call":
            tool, args = params.get("name"), params.get("arguments") or {}
            with open(log, "a", encoding="utf-8") as out:
                out.write(json.dumps({"server": name, "tool": tool, "args": args}, ensure_ascii=False) + "\n")
            result = run(tool, args)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                              "error": {"code": -32601, "message": "no such method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


def text(value: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": value}], "isError": error}
