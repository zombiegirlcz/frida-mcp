#!/usr/bin/env python3
"""REGRESE: model posle CELY tool call jeste jednou zabaleny.

Realny pripad ze session.jsonl (2026-10-04) — 10x se objevilo:
    Validation failed for tool "bash":
      - command: must have required properties command
    Received arguments:
    {"arguments": "{\"command\": \"cd /root/elf_loader && head -60 test-all.sh\"}",
     "name": "bash"}

Pricina: `_unwrap_args` rozbaloval jen objekt s PRESNE JEDNIM klicem
z _WRAPPED_ARG_KEYS. Kdyz mel objekt navic klic `name` (model zabalil cely
call {name, arguments} znovu jako hodnotu `arguments`), unwrap se vubec
nespustil -> nastroj dostal {"name", "arguments"} misto {"command"}.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.toolbridge import parse_tool_calls, tool_specs, tool_names  # noqa: E402

TOOLS = [
    {"type": "function", "function": {"name": "bash", "parameters": {
        "type": "object", "properties": {"command": {"type": "string"}},
        "required": ["command"]}}},
    {"type": "function", "function": {"name": "read", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"},
                                          "limit": {"type": "integer"}},
        "required": ["path"]}}},
]
NAMES = tool_names(TOOLS)
SPECS = tool_specs(TOOLS)
CMD = "cd /root/elf_loader && head -60 test-all.sh"
FAILED = []


def case(label, raw, want_name, want_args):
    _, calls = parse_tool_calls(raw, NAMES, SPECS)
    if not calls:
        FAILED.append(label)
        print("FAIL %s -> zadny call" % label)
        return
    c = calls[0]
    ok = c["name"] == want_name and c["arguments"] == want_args
    print(("OK   " if ok else "FAIL ") + label)
    if not ok:
        FAILED.append(label)
        print("      dostal: %s %s" % (c["name"], json.dumps(c["arguments"], ensure_ascii=False)))


# A) args = STRING obsahujici cely call {name, arguments}
case("A args=string s plnym callem",
     '<tool_call>' + json.dumps({"name": "bash", "arguments":
         json.dumps({"name": "bash", "arguments": {"command": CMD}})})
     + '</tool_call>', "bash", {"command": CMD})

# B) args = OBJEKT obsahujici cely call {name, arguments}
case("B args=objekt s plnym callem",
     '<tool_call>' + json.dumps({"name": "bash", "arguments":
         {"name": "bash", "arguments": {"command": CMD}}})
     + '</tool_call>', "bash", {"command": CMD})

# C) args = {arguments:{command}} (2 urovne, jen 1 klic)
case("C args={arguments:{command}} objekt",
     '<tool_call>' + json.dumps({"name": "bash", "arguments":
         {"arguments": {"command": CMD}}}) + '</tool_call>', "bash", {"command": CMD})

# D) args = STRING {arguments:{command}}
case("D args=string {arguments:{command}}",
     '<tool_call>' + json.dumps({"name": "bash", "arguments":
         json.dumps({"arguments": {"command": CMD}})}) + '</tool_call>',
     "bash", {"command": CMD})

# E) NESMI unwrapovat: cizi jmeno uvnitr (neni to re-wrap stejneho nastroje)
case("E cizi jmeno se NEunwrapuje",
     '<tool_call>' + json.dumps({"name": "bash", "arguments":
         {"name": "read", "arguments": {"path": "/x"}}}) + '</tool_call>',
     "bash", {"name": "read", "arguments": {"path": "/x"}})

# F) NESMI unwrapovat: realny nastroj, ktery ma parametry name i arguments
TOOLS2 = TOOLS + [{"type": "function", "function": {"name": "mcp", "parameters": {
    "type": "object", "properties": {"name": {"type": "string"},
                                      "arguments": {"type": "object"}},
    "required": ["name", "arguments"]}}}]
NAMES2, SPECS2 = tool_names(TOOLS2), tool_specs(TOOLS2)
_, _c = parse_tool_calls(
    '<tool_call>' + json.dumps({"name": "mcp", "arguments":
        {"name": "bash", "arguments": {"command": "ls"}}}) + '</tool_call>',
    NAMES2, SPECS2)
_ok = _c and _c[0]["arguments"] == {"name": "bash", "arguments": {"command": "ls"}}
print(("OK   " if _ok else "FAIL ") + "F mcp s parametry name+arguments zustava")
if not _ok:
    FAILED.append("F")
    print("      dostal: %s" % (_c[0]["arguments"] if _c else None))

print()
if FAILED:
    print("SELHALO: %d - %s" % (len(FAILED), ", ".join(FAILED)))
    raise SystemExit(1)
print("Vsechny testy wrapped-call prosly")
