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

from common.toolbridge import parse_tool_calls, tool_specs  # noqa: E402

TOOLS = {"bash", "read", "write", "edit", "ls", "grep"}

# Skutecne schemas (jmena parametru) — diky nim pozname i HOLY JSON bez "name"
# (model casto posle jen {"command": "..."} a nazev nastroje vynecha).
TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "bash", "parameters": {
        "type": "object", "properties": {"command": {"type": "string"}},
        "required": ["command"]}}},
    {"type": "function", "function": {"name": "read", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"]}}},
    {"type": "function", "function": {"name": "write", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"},
                                            "content": {"type": "string"}},
        "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"},
                                            "oldText": {"type": "string"},
                                            "newText": {"type": "string"}},
        "required": ["path", "oldText", "newText"]}}},
    {"type": "function", "function": {"name": "ls", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"]}}},
    {"type": "function", "function": {"name": "grep", "parameters": {
        "type": "object", "properties": {"pattern": {"type": "string"},
                                            "path": {"type": "string"}},
        "required": ["pattern"]}}},
]
SPECS = tool_specs(TOOL_SCHEMAS)
FAILED: list[str] = []


def check(label: str, raw: str, want_calls: list[str], want_text: str | None = None,
          forbid: tuple[str, ...] = ("VYSLEDEK NASTROJE", "[ASSISTANT]", "[USER]",
                                     "[SYSTEM]", "</call_call>", "<|DSML|"),
          specs=None):
    text, calls = parse_tool_calls(raw, TOOLS, SPECS if specs is None else specs)
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

# 11) DSML s fullwidth barem (｜ = U+FF5C) a tagy bez `tool_` prefixu.
#     Tohle je presny tvar, ktery DeepSeek obcas posle — drive ho splitter
#     nepoznal a streamoval markup jako text.
_B = "\uff5c"
_DSML_RAW = (
    f"<{_B}DSML{_B} calls>\n"
    f"<{_B}DSML{_B} invoke name=\"bash\">\n"
    f"<{_B}DSML{_B} parameter name=\"command\" string=\"true\">ls</{_B}DSML{_B} parameter>\n"
    f"</{_B}DSML{_B} invoke>\n"
    f"</{_B}DSML{_B} calls>"
)
check("DSML s fullwidth barem (tagy bez tool_)", _DSML_RAW, ["bash"], "")

# 12) a hlavne: StreamSplitter ho nesmi streamovat jako text
from common.toolbridge import StreamSplitter  # noqa: E402
sp = StreamSplitter(TOOLS)
leaked = "".join(sp.feed(c) for c in _DSML_RAW)   # po znacich, jako realny stream
tail, calls = sp.finish()
leaked += tail
_ok = ("DSML" not in leaked and _B not in leaked and "<invoke" not in leaked
       and [c["name"] for c in calls] == ["bash"])
if _ok:
    print("✅ StreamSplitter neleakne DSML markup jako text")
else:
    FAILED.append("splitter DSML")
    print(f"❌ StreamSplitter leaknul markup: {leaked[:90]!r} calls={calls}")

# 13) HOLY JSON BEZ "name" — presne to, co model poslal:
#     ```json
#     {"command":"cd /root/elf_loader && sed -n '490,540p' src/elf_loader.c"}
#     ```
#     Nazev nastroje vynechal; dovodime ho z parametru (command -> bash).
check("holy JSON ve fence (bez name)",
      "```json\n{\"command\":\"cd /root/elf_loader && sed -n '490,540p' src/elf_loader.c\"}\n```",
      ["bash"], "")
check("holy JSON bez fence (bez name)", '{"command":"ls -la"}', ["bash"], "")

# 14) bezny JSON v odpovedi se NESMI splest s tool callem
check("bezny JSON v odpovedi", '{"vysledek": 42}', [], '{"vysledek": 42}')
check("bezny JSON s jinym klicem", 'Souhrn: {"count": 3}', [], 'Souhrn: {"count": 3}')

# 15) StreamSplitter: ```json fence + holy JSON se NESMI streamovat jako text
from common.toolbridge import StreamSplitter  # noqa: E402
for _lbl, _raw in (
    ("splitter: fence + holy JSON", "```json\n{\"command\":\"ls -la\"}\n```"),
    ("splitter: holy JSON", '{"command":"ls"}'),
):
    _sp = StreamSplitter(TOOLS, SPECS)
    _leak = "".join(_sp.feed(c) for c in _raw)   # po znacich, jako realny stream
    _tail, _calls = _sp.finish()
    _leak += _tail
    if _leak.strip() or [_c["name"] for _c in _calls] != ["bash"]:
        FAILED.append(_lbl)
        print(f"❌ {_lbl}: leak={_leak[:60]!r} calls={_calls}")
    else:
        print(f"✅ {_lbl}")

# 16) text pred callem se nesmi poslat DVAKRAT (regrese: uz odeslany prefix
#     vs stripnuty text -> duplikace)
_sp = StreamSplitter(TOOLS, SPECS)
_leak = "".join(_sp.feed(c) for c in 'Podivam se.\n{"command":"ls"}')
_tail, _calls = _sp.finish()
_leak += _tail
if _leak.count("Podivam") == 1 and [_c["name"] for _c in _calls] == ["bash"]:
    print("✅ text pred callem se nezdvojil")
else:
    FAILED.append("duplikace textu")
    print(f"❌ text pred callem: {_leak!r}")

# 17) model opsal nasi zaverecnou instrukci -> nesmi se objevit ve viditelnem textu
check("ozvena zaverecne instrukce", "Hotovo.\n[INSTRUKCE PRO TENTO TAH]\nOdpovidas jako posledni \"assistant\"",
      [], "Hotovo.")
check("ozvena instrukce bez zavorek", "OK\nOdpovidas jako posledni assistant v konverzaci",
      [], "OK")

# 18) REGRESE: <tool_call> nesmi leakovat jako text pri REALNEM deleni chunku.
#     Drive se bral POSLEDNI trigger znak, takze v chunku
#         <tool_call>\n{"name":...
#     vyhral "{" uvnitr JSONu a "<tool_call>\n" sel ven jako viditelny text.
_RAW = '<tool_call>\n{"name":"bash","arguments":{"command":"ls"}}\n</tool_call>'
_leak_bad = []
for _pfx in ("", "Hotovo.\n", 'Data: {"a":1} a pak '):
    for _sz in (1, 2, 3, 5, 8, 13, 21, 34, 100):
        _sp = StreamSplitter(TOOLS, SPECS)
        _raw = _pfx + _RAW
        _out = ""
        for _i in range(0, len(_raw), _sz):
            _out += _sp.feed(_raw[_i:_i + _sz])
        _tl, _cl = _sp.finish()
        _out += _tl
        if "<tool_call" in _out or "</tool_call" in _out or not _cl:
            _leak_bad.append((_pfx, _sz, _out[:50], len(_cl)))
if _leak_bad:
    FAILED.append("tool_call leak")
    for _pfx, _sz, _o, _n in _leak_bad[:4]:
        print(f"❌ leak (prefix={_pfx!r} size={_sz}): {_o!r} calls={_n}")
else:
    print("✅ <tool_call> neleakuje pri zadnem deleni chunku (27 kombinaci)")

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
