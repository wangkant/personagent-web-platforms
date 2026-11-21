"""One AgentDojo suite's tools as a local MCP server over stdio, added to a home as a person adds a connector
(tools/bench/agentdojo/run.py writes it into the home's mcp.json). The tools, their descriptions and parameters
come from the suite through run.py's bridge; each call goes back to the bridge, which runs it on the case's
environment with AgentDojo's own FunctionsRuntime and formats the result as AgentDojo's ToolsExecutor does.

    python mcp_server.py <bridge address, e.g. http://127.0.0.1:59723>

The JSON-RPC lines are the protocol's own (initialize, tools/list, tools/call); the standard library only, so the
app may start it with any Python.
"""
import json
import sys
import urllib.request

BRIDGE = sys.argv[1].rstrip("/")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def bridge(path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(BRIDGE + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with OPENER.open(request, timeout=120) as answer:
        return json.loads(answer.read().decode())


for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": message["params"].get("protocolVersion"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "agentdojo", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": bridge("/tools")["tools"]}
    elif method == "tools/call":
        params = message["params"]
        done = bridge("/call", {"name": params["name"], "arguments": params.get("arguments") or {}})
        result = {"content": [{"type": "text", "text": done["error"] or done["text"]}], "isError": bool(done["error"])}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
