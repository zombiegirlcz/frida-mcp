#!/usr/bin/env python3
"""Halucinace 'Tool X does not exists.' nesmi otravi kontext."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.toolbridge import build_prompt, parse_tool_calls

FAILED = []
TOOLS = [{"type": "function", "function": {
    "name": "bash", "description": "Execute bash",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                   "required": ["command"]}}}]

HALL = ("Tool bash does not exists.Tool read does not exists."
        "Tool bash does not exists.Tool bash does not exists."
        "Mas pravdu, omlouvam se. Problem je v startDaemonInGuest().")


def check(label, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + label + (("  " + detail) if not ok and detail else ""))
    if not ok:
        FAILED.append(label)


def t1():
    text, calls = parse_tool_calls(HALL, {"bash"}, None)
    check("1. halucinace odstranena", "does not exists" not in text, repr(text[:200]))
    check("2. realny text ZUSTAL", "startDaemonInGuest" in text, repr(text[:300]))
    check("3. zadny vymysleny call", calls == [], repr(calls))


def t2():
    mixed = ('Tool bash does not exists.\n'
             '