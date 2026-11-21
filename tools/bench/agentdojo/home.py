"""A scratch home for the AgentDojo run: the first character, one suite's tools added as an MCP server the way a
person adds a connector (mcp_tools.write_servers, which writes mcp.json), and the person's computer an empty folder
beside the home (<home>-pc): the only folder the app's own file tools may read without asking, as a person grants
one in Settings (shell/grants.py's shell.json), so nothing of the computer the run happens on reaches the model.
run.py also gives it as the Desktop (USER_DESKTOP_DIR). Run with the app's Python from the repo root.

    python tools/bench/agentdojo/home.py <home> <server name> <python for the server> <bridge address>
"""
import sys
from pathlib import Path

from dotenv import dotenv_values

from mibo import mcp_tools, setup_wizard
from mibo.shell.grants import Grants

home, name, python, bridge = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
lang = dotenv_values(home / ".env").get("AGENT_LANG") or "en"
setup_wizard.write_persona(home, setup_wizard.CHARACTERS[0].key, name="Mibo", lang=lang)
pc = Path(f"{home}-pc")
pc.mkdir(exist_ok=True)
grants = Grants(home / "runtime" / "desktop", home)
for folder in grants.folders():
    grants.remove(folder)
assert not grants.add(str(pc), read_only=True)
server = str(Path(__file__).resolve().with_name("mcp_server.py"))
# One suite at a time: the other suite's server, if an earlier run added it, goes.
for other in mcp_tools.load_config(home)["mcpServers"]:
    if other != name:
        mcp_tools.remove_server(home, other)
mcp_tools.write_servers(home, {name: {"command": python, "args": ["-I", server, bridge]}}, lambda values: None,
                        replace=True)
print(f"{home}: character written, MCP server {name!r} added")
