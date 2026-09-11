#!/usr/bin/env python3
"""Regresni testy pro common/toolbridge.parse_tool_calls.

Vsechny priklady jsou REALNE vzory z sessions (test/tool-call.jsonl) — kazdy
z nich kdysi zpusobil, ze se tool call neparsoval nebo ze se do historie
dostala halucinace.

    python3 tests/test_toolbridge.py
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.toolbridge import parse_tool_calls  # noqa: E402

TOOLS = {"bash", "read", "write", "edit", "ls", "grep"}
FAILED: list[str] = []


def check(label: str, raw: str, want_calls: list[str], want_text: str | None = None,
          forbid: tuple[str, ...] = ("VYSLEDEK NASTROJE", "[ASSISTANT]", "[USER]",
                                     "[SYSTEM]", "</call_call>", "<|DSML|")):
    text, calls = parse_tool_calls(raw, TOOLS)
    got = [c["name"] for c in calls]
    problems = []
    if got != want_calls:
        problems.append(f"calls {got} != {want_calls}")
    if want_text is not None and text != want_text:
        problems.append(f"text {text[:80]!r} != {want_text[:80]!r}")
    for f in forbid:
        if f in text:
            problems.append(f"v textu zustalo {f!r}")
    if problems:
        FAILED.append(label)
        print(f"❌ {label}\n     " + "\n     ".join(problems))
    else:
        print(f"✅ {label}")


# 1) cisty bare JSON (nejcastejsi format DeepSeeku)
check("bare JSON + zkomoleny </call_call>",
      '{"name": "read", "arguments": {"path": "/tmp/x"}}\n</call_call>',
      ["read"], "")

# 2) NEESCAPOVANE uvozovky uvnitr hodnoty (realny pripad: grep -F 'Executing git')
check("neescapovane uvozovky v argumentu",
      '{"name": "bash", "arguments": {"command": "grep -F \\"Executing git command\\" x"}}\n</tool',
      ["bash"])

# 3) ROZBITY format: hodnota jako holy text, chybi uvozovky i zavorky
#    (presne z session; drive se tool call uplne zahodil)
check("holy text jako hodnota argumentu",
      'Podivam se.\n{"name": "bash", "arguments": {"command":\n'
      'cd /root/elf_loader && echo "=== x ===" && grep -n "tls" src/a.c | head -60\n\n</function>',
      ["bash"], "Podivam se.")

# 4) echo za tool callem -> halucinovany druhy call se NESMI vytahnout
check("ozvena za callem se odrizne",
      'Jdu na to.\n{"name": "bash", "arguments": {"command": "ls"}}\n</call_call>\n'
      '[VYSLEDEK NASTROJE read]\nno output\n\n[ASSISTANT]\nZkusim znovu.\n'
      '{"name": "bash", "arguments": {"command": "sleep 1"}}\n</call_call>',
      ["bash"], "Jdu na to.")

# 5) REALNY call az ZA ozvenou (ozvena 2287, call 8270) -> nesmi se zahodit
check("realny call za ozvenou se najde",
      '[VYSLEDEK NASTROJE bash]\n(no output)\n\n[ASSISTANT]\nVypadky. Zkusim znovu.\n\n'
      '{"name": "bash", "arguments": {"command": "nm -D /tmp/ldso > /tmp/x"}}\n</call>',
      ["bash"])

# 6) DSML / <invoke> format
check("DSML invoke s parametrem",
      '<｜DSML｜tool_calls>\n<｜DSML｜invoke name="write">\n'
      '<｜DSML｜parameter name="path">/tmp/a.txt</parameter>\n'
      '<｜DSML｜parameter name="content">radek 1\nradek 2</parameter>\n'
      '</｜DSML｜invoke>\n</｜DSML｜tool_calls>',
      ["write"])

# 7) paralelni cally
check("dva cally v jedne odpovedi",
      '{"name": "bash", "arguments": {"command": "a"}}\n'
      '{"name": "read", "arguments": {"path": "b"}}',
      ["bash", "read"])

# 8) bezna odpoved bez tool callu se nesmi rozbit
check("text bez tool callu", "Hotovo, vse je zapsane.", [], "Hotovo, vse je zapsane.")

# 9) JSON, ktery NENI tool call (bezna data) se nesmi splest
check("bezny JSON v odpovedi",
      'Server bezi na {"name": "server", "port": 8080}.', [], None)

# 10) fenced json
check("```json fence",
      'Zapisuji:\n```json\n{"name": "write", "arguments": {"path": "/t", "content": "x"}}\n```',
      ["write"])

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
