#!/usr/bin/env python3
"""Halucinace 'Tool X does not exists.' a ozveny promptu nesmi otravi kontext.

Soubor byl v gitu commitnuty useknuty (commit 4d3b950, 33 radku, konci
uprostred stringu) a NIKDY nebezel — tady je kompletni verze, ktera pokryva:

  1. parse_tool_calls vycisti halucinaci 'Tool X does not exists.' z textu,
     ale realny obsah za ni zachova a zadny tool call si nevymysli
  2. build_prompt orizne ozveny naseho promptu ([VYSLEDEK NASTROJE ...],
     [ASSISTANT], 'Odpovidas jako posledni', 'NIKDY sam nevypisuj') z historie
     roli assistant — jinak se model uci vlastni vzor a opakuje ho
  3. build_prompt vlozi TOOL_PREAMBLE + schemata do prvni developer zpravy
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.toolbridge import build_prompt, parse_tool_calls, TOOL_PREAMBLE  # noqa: E402

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


def t1_hallucination_removed():
    text, calls = parse_tool_calls(HALL, {"bash"}, None)
    check("1. halucinace odstranena", "does not exist" not in text, repr(text[:200]))
    check("2. realny text ZUSTAL", "startDaemonInGuest" in text, repr(text[:300]))
    check("3. zadny vymysleny call", calls == [], repr(calls))


def t2_real_call_behind_hallucination():
    raw = ("Tool bash does not exists.\n"
           '<tool_call>{"name":"bash","arguments":{"command":"ls"}}</tool_call>')
    text, calls = parse_tool_calls(raw, {"bash"}, None)
    check("4. halucinace + realny call -> call zustal",
          len(calls) == 1 and calls[0]["arguments"] == {"command": "ls"}, repr(calls))
    check("5. halucinace zmizela i tady", "does not exist" not in text, repr(text))


def t3_build_prompt_strips_echo():
    msgs = [
        {"role": "developer", "content": "jsi agent"},
        {"role": "user", "content": "spust ls"},
        {"role": "assistant", "content":
            "Podivam se.\n\n[VYSLEDEK NASTROJE bash]\nfile1\nfile2\n[ASSISTANT]\nHotovo."},
    ]
    p = build_prompt(msgs, TOOLS)
    check("6. ozvena [VYSLEDEK NASTROJE] zmizela z promptu",
          "[VYSLEDEK NASTROJE bash]\nfile1" not in p)
    check("7. text PRED ozvenou zustal", "Podivam se." in p)


def t4_build_prompt_injects_tools():
    msgs = [{"role": "developer", "content": "jsi agent"},
            {"role": "user", "content": "ahoj"}]
    p = build_prompt(msgs, TOOLS)
    check("8. TOOL_PREAMBLE je v promptu", TOOL_PREAMBLE.strip().splitlines()[0] in p)
    check("9. schema nastroje bash je v promptu", "### bash" in p)


def t5_no_tool_no_trailer():
    msgs = [{"role": "user", "content": "ahoj"}]
    p = build_prompt(msgs, None)
    check("10. bez nastroju zadny tool preamble", "MUSIS je pouzivat" not in p)


for fn in (t1_hallucination_removed, t2_real_call_behind_hallucination,
           t3_build_prompt_strips_echo, t4_build_prompt_injects_tools,
           t5_no_tool_no_trailer):
    fn()

print()
if FAILED:
    print("SELHALO: %d - %s" % (len(FAILED), ", ".join(FAILED)))
    raise SystemExit(1)
print("Vsechny testy halucinaci prosly")
