#!/usr/bin/env python3
"""REPRO: args zabalene jeste jednou, ale vnitrni JSON ma NEVALIDNI escape.

Realny pripad ze session.html: model poslal
    {"arguments": "{\"command\": \"... grep -n '\\*.apk' ...\"}"}
Vnitrni string obsahuje backslash-hvezdicku, coz neni platna JSON escape
sekvence. json.loads() ho odmitne -> _unwrap_args vrati args beze zmeny ->
nastroj dostane {'arguments': '...'} misto {'command': '...'} a pi odmitne:
'command: must have required properties command'.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.toolbridge import parse_tool_calls, tool_specs  # noqa: E402

TOOLS = {"bash", "read", "write"}
SCHEMAS = [
    {"type": "function", "function": {"name": "bash", "parameters": {
        "type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "number"}},
        "required": ["command"]}}},
    {"type": "function", "function": {"name": "read", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"]}}},
]
SPECS = tool_specs(SCHEMAS)
FAILED = []

# 1) Spravny vnitrni JSON (model takto posle command)
cmd = "grep -n '*.apk' .gitattributes"
inner_good = json.dumps({"command": cmd})
# 2) Pokazeny: model escapoval hvezdicku backslashem -> neplatna escape
BS = chr(92)
inner_bad = inner_good.replace("*.apk", BS + "*.apk")
print("inner_good:", repr(inner_good))
print("inner_bad :", repr(inner_bad))
for label, s in (("good", inner_good), ("bad", inner_bad)):
    try:
        json.loads(s)
        print("  json.loads(%s): OK" % label)
    except Exception as e:  # noqa: BLE001
        print("  json.loads(%s): FAIL -> %s" % (label, e))

# 3) Cela odpoved modelu: args zabalene jeste jednou (jako v session.html)
for label, inner in (("good", inner_good), ("bad", inner_bad)):
    wrapped = {"name": "bash", "arguments": {"arguments": inner}}
    raw = "<tool_call>" + json.dumps(wrapped) + "</tool_call>"
    text, calls = parse_tool_calls(raw, TOOLS, SPECS)
    ok = (len(calls) == 1 and calls[0]["name"] == "bash"
          and calls[0]["arguments"].get("command") == cmd)
    print("[%s] calls=%s -> %s" % (label, calls, "OK" if ok else "BUG"))

# ---- b) typy: timeout jako string (2x v session.html) ----
for _lbl, _r, _want in [
    ("timeout string se stray tagem",
     '<tool_call>{"name":"bash","arguments":'
     '{"command":"ls","timeout":"60</>"}}</tool_call>', 60),
    ("timeout cisty string",
     '<tool_call>{"name":"bash","arguments":'
     '{"command":"ls","timeout":"30"}}</tool_call>', 30),
]:
    _, _cs = parse_tool_calls(_r, TOOLS, SPECS)
    _v = _cs[0]["arguments"].get("timeout") if _cs else None
    if _cs and _v == _want and isinstance(_v, int):
        print("OK  " + _lbl)
    else:
        FAILED.append(_lbl)
        print("FAIL %s: %r cekano %r" % (_lbl, _v, _want))

# string command se nesmi pretypovat
_, _cs4 = parse_tool_calls(
    '<tool_call>{"name":"bash","arguments":{"command":"123"}}</tool_call>',
    TOOLS, SPECS)
if _cs4 and _cs4[0]["arguments"].get("command") == "123":
    print("OK  stringovy command zustava stringem")
else:
    FAILED.append("string command")
    print("FAIL string command: %r" % (_cs4,))

print()
if FAILED:
    print("SELHALO: %d - %s" % (len(FAILED), ", ".join(FAILED)))
    raise SystemExit(1)
print("Vsechny testy argumentu prosly")
