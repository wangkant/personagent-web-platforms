"""Stand-in MCP server 'weather': one forecast tool, marked read-only. The bench never lets it start.

    python weather.py <folder>     (calls in <folder>/mcp-calls.jsonl)
"""
import sys
from pathlib import Path

from _serve import serve, text

TOOLS = [{"name": "forecast", "description": "Weather forecast for a city.", "annotations": {"readOnlyHint": True},
          "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]
serve("weather", "Weather forecasts.", TOOLS, lambda tool, args: text("sunny"),
      str(Path(sys.argv[1]) / "mcp-calls.jsonl"))
