"""Stand-in MCP server 'kit': fifteen small tools, all marked read-only.

    python kit.py <folder>     (calls in <folder>/mcp-calls.jsonl)
"""
import datetime
import sys
from pathlib import Path

from _serve import serve, text

S, N = {"type": "string"}, {"type": "number"}


def shape(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props)}


SPEC = [
    ("convert_units", "Convert a value between units of length, weight or temperature (km to miles, kg to lb, C to F).",
     shape({"value": N, "from_unit": S, "to_unit": S})),
    ("world_time", "Current local time in a city or IANA time zone.", shape({"zone": S})),
    ("days_between", "Number of days between two dates (YYYY-MM-DD).", shape({"start": S, "end": S})),
    ("word_count", "Count words and characters in a text.", shape({"text": S})),
    ("exchange_rate", "Exchange rate between two currencies (ISO codes).", shape({"base": S, "quote": S})),
    ("calc", "Evaluate an arithmetic expression.", shape({"expression": S})),
    ("lunar_date", "Chinese lunar calendar date for a Gregorian date.", shape({"date": S})),
    ("holiday_list", "Public holidays of a country in a year.", shape({"country": S, "year": N})),
    ("bmi", "Body mass index from height in cm and weight in kg.", shape({"height_cm": N, "weight_kg": N})),
    ("tip_split", "Split a bill with tip among people.", shape({"total": N, "people": N, "tip_percent": N})),
    ("roman_numeral", "Convert an integer to Roman numerals.", shape({"number": N})),
    ("color_hex", "Convert a color name to its hex code.", shape({"name": S})),
    ("pinyin", "Pinyin of Chinese text.", shape({"text": S})),
    ("timezone_diff", "Hour difference between two time zones.", shape({"zone_a": S, "zone_b": S})),
    ("password_strength", "Rate how strong a password would be (it is not stored).", shape({"password": S})),
]
TOOLS = [{"name": n, "description": d, "annotations": {"readOnlyHint": True}, "inputSchema": s} for n, d, s in SPEC]
FACTORS = {("km", "miles"): 0.621371, ("km", "mi"): 0.621371, ("miles", "km"): 1.609344, ("mi", "km"): 1.609344,
           ("kg", "lb"): 2.20462, ("kg", "lbs"): 2.20462, ("lb", "kg"): 0.453592}


def run(tool: str, args: dict) -> dict:
    if tool == "convert_units":
        value, unit, to = float(args["value"]), str(args["from_unit"]).lower(), str(args["to_unit"]).lower()
        if (unit, to) in FACTORS:
            return text(f"{value} {unit} = {round(value * FACTORS[(unit, to)], 3)} {to}")
        return text(f"unsupported units {unit}->{to}", True)
    if tool == "days_between":
        start, end = (datetime.date.fromisoformat(args[k]) for k in ("start", "end"))
        return text(f"{(end - start).days} days")
    return text(f"{tool}: stand-in answer")


serve("kit", "Everyday converters and calculators.", TOOLS, run, str(Path(sys.argv[1]) / "mcp-calls.jsonl"))
