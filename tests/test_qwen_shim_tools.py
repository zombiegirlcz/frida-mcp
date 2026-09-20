#!/usr/bin/env python3
"""Regresni testy pro qwen_shim: system prompt + schemata nastroju + tool_calls.

Vychazi z REALNE session (session.html z 2026-09-20) a z dumpu
/tmp/qwen_req.jsonl, kde pi poslalo:
    messages: [developer 36 200 znaku, user ...]
    tools: 71 schemat

Vyvraci dve tvrzeni, ktera se ukazala jako MYLNA:
  1. "Qwen nedostane system prompt" — NEPRAVDA: build_prompt vlozi
     [SYSTEM] + TOOL_PREAMBLE + vsechna schemata nastroju do jednoho promptu
     (v realnem tahu 89 231 znaku).
  2. "Qwen nefunguje nastroje" — NEPRAVDA: zive volani vraci
     finish_reason=tool_calls s platnymi, NEprazdnymi argumenty.

Testy 1-4 jsou deterministicke (bez site). Test 5 se prida, jen kdyz bezi
shim na portu 13360 — jinak se preskoci.

    python3 tests/test_qwen_shim_tools.py
"""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.toolbridge import (  # noqa: E402
    build_prompt, cap_messages, tool_specs)

FAILED = []


def ok(label, cond, detail=""):
    if cond:
        print("OK  " + label)
    else:
        FAILED.append(label)
        print("FAIL %s %s" % (label, detail))


# Schemata jako z realneho dumpu (zkracena, ale stejny tvar)
TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Spusti prikaz",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "number"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "edit", "description": "Upravi soubor",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "edits": {"type": "array", "items": {"type": "object"}}},
            "required": ["path", "edits"]}}},
]

DEVELOPER = ("You are an expert coding assistant operating inside pi. "
             "Help users by reading files, executing commands.")


def request():
    return {
        "model": "qwen3.8-max",
        "messages": [
            {"role": "developer", "content": DEVELOPER},
            {"role": "user", "content": "Pouzij nastroj bash: echo QWEN-TOOL-OK"},
        ],
        "tools": TOOLS,
        "stream": True,
    }


# --- 1) build_prompt musi obsahovat system prompt I schemata nastroju ---
print("=== 1) prompt pro Qwen obsahuje system prompt i schemata ===")
req = request()
capped, _trimmed = cap_messages(req["messages"], 400000)
prompt = build_prompt(capped, req["tools"])

ok("developer prompt je v promptu", DEVELOPER[:60] in prompt)
ok("[SYSTEM] obal pritomen", "[SYSTEM]" in prompt)
ok("[USER] obal pritomen", "[USER]" in prompt)
ok("schema nastroje bash je v promptu", "bash" in prompt)
ok("schema nastroje edit je v promptu", "edit" in prompt)
ok("parametr command je ve schematu", "command" in prompt)
ok("zaverecna instrukce je v promptu", "NIKDY" in prompt)
ok("prompt neni prazdny", len(prompt) > 1000, "len=%d" % len(prompt))


# --- 2) role developer se bere jako system (ne user) ---
print()
print("=== 2) developer se bere jako system (ne user) ===")
only_dev = [{"role": "developer", "content": "SYSTEM-MARKER-123"}]
p2 = build_prompt(cap_messages(only_dev, 400000)[0], TOOLS)
ok("developer -> [SYSTEM]", "[SYSTEM]" in p2 and "SYSTEM-MARKER-123" in p2)
# POZOR: "[USER]" se v promptu vyskytuje legitimne (2x) — ale jen
# uvnitr TOOL_PREAMBLE, ktery dokumentuje, ze znacky [USER]/[SYSTEM]
# pise vyhradne system. Kontrolujeme proto, ze obsah developer zpravy
# NENI pod obalem [USER].
under_user = ("[USER]" + chr(10) + "SYSTEM-MARKER-123") in p2
ok("developer obsah neni pod obalem [USER]", not under_user)


# --- 3) tool_specs zna parametry i typy (pro unwrap/koerci) ---
print()
print("=== 3) tool_specs zna parametry a typy ===")
specs = tool_specs(TOOLS) or {}
ok("specs ma bash", "bash" in specs)
ok("bash.params ma command", "command" in specs.get("bash", {}).get("params", set()))
ok("bash.types zna number",
   specs.get("bash", {}).get("types", {}).get("timeout") == "number")


# --- 4) bez nastroju se neposila preambule, prompt funguje ---
print()
print("=== 4) bez nastroju se neposila preambule ===")
p4 = build_prompt(cap_messages([{"role": "user", "content": "ahoj"}], 400000)[0], None)
ok("bez tools neni schema", "bash" not in p4)
ok("bez tools prompt existuje", len(p4) > 3, "len=%d" % len(p4))


# --- 5) ZIVY test (jen kdyz shim bezi) ---
print()
print("=== 5) zivy test proti shimu (jen kdyz bezi) ===")
PORT = 13360


def port_open(p):
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", p)) == 0
    finally:
        s.close()


if not port_open(PORT):
    print("SKIP shim na %d nebezi -> zivy test preskocen" % PORT)
else:
    import urllib.request
    data = json.dumps(request()).encode()
    r = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % PORT,
        data=data, headers={"Content-Type": "application/json"})
    text, tcs, fin = "", [], None
    try:
        for raw in urllib.request.urlopen(r, timeout=240):
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            pl = line[5:].strip()
            if pl in ("", "[DONE]"):
                continue
            try:
                ch = json.loads(pl)
            except json.JSONDecodeError:
                continue
            c = (ch.get("choices") or [{}])[0]
            d = c.get("delta") or {}
            text += d.get("content") or ""
            if d.get("tool_calls"):
                tcs.extend(d["tool_calls"])
            if c.get("finish_reason"):
                fin = c["finish_reason"]
    except Exception as e:  # noqa: BLE001
        ok("zivy tah probehl", False, "%s: %s" % (type(e).__name__, e))
    else:
        ok("zivy tah: finish_reason=tool_calls", fin == "tool_calls", "fin=%r" % fin)
        ok("zivy tah: prisel tool_call", len(tcs) >= 1, "n=%d" % len(tcs))
        if tcs:
            fn = tcs[0].get("function") or {}
            ok("tool_call ma jmeno", bool(fn.get("name")), repr(fn.get("name")))
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            ok("tool_call ma NEprazdne argumenty", bool(args),
               repr(fn.get("arguments"))[:120])
        ok("zivy tah: zadny leak tagu",
           not any(x in text.lower() for x in ("<tool_call", "<invoke", "dsml")),
           repr(text[:120]))


print()
if FAILED:
    print("SELHALO: %d - %s" % (len(FAILED), ", ".join(FAILED)))
    raise SystemExit(1)
print("Vsechny qwen_shim testy prosly")
