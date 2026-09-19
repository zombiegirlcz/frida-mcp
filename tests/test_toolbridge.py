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
import json as _json

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

def U(s: str) -> str:
    """Identity helper: realne fullwidth znaky uz jsou v literalu."""
    return s


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


# 19) REGRESE: model jen PISE o tagach invoke/parameter (bez tool callu).
#     Driv to shodil dve veci:
#       - uklidovy regex urezl text od prvniho <invoke> do konce odpovedi
#       - splitter zacal drzet stream a odpoved se "zastavila"
#     Realne se to stalo v tahu, kde se uzivatel ptal, co tagy znamenaji.
_PROZA = "Tagy `<invoke>` a `<parameter>` se v XML pouzivaji jako obal. KONEC-123"
check("proza zminujici tagy zustava CELE", _PROZA, [], _PROZA)
check("proza s tagy bez name= neni call",
      "Vysvetleni: <invoke> je obal a <parameter> hodnota.",
      [], "Vysvetleni: <invoke> je obal a <parameter> hodnota.")

# 20) REGRESE: splitter NESMI zacit drzet stream na proze s holym tagem
#     (jinak se odpoved "zastavi" a nedorazi vubec nic).
_prose_hold = ("Tagy `<invoke>` a `<parameter>` se v XML pouzivaji jako obal. "
               "KONEC-123")
_sp = StreamSplitter(TOOLS, SPECS)
_out = ""
for _i in range(0, len(_prose_hold), 4):
    _out += _sp.feed(_prose_hold[_i:_i + 4])
_tl, _cl = _sp.finish()
_out += _tl
if _out != _prose_hold or _cl or _sp.holding:
    FAILED.append("proza s tagy zablokovala stream")
    print(f"❌ proza s tagy: out={_out[:60]!r} calls={len(_cl)} holding={_sp.holding}")
else:
    print("✅ proza s tagy nezablokuje stream (splitter nedrzi)")

# 21) REGRESE: JSON uvnitr tool callu obsahuje tagy -> normalizace ho NESMI
#     rozbit (jinak by se obsah souboru poslal zkomoleny).
check("JSON s tagy uvnitr zustava validni",
      '<tool_call>{"name":"write","arguments":{"path":"/tmp/x",'
      '"content":"text s <invoke> a <\uff5cDSML\uff5ccalls> tagem"}}</tool_call>',
      ["write"])


# ---------------------------------------------------------------------------
# 22) REGRESE (dukladnejsi): PROZA zminujici tagy invoke/parameter.
#
#     Presne tenhle pripad zpusobil, ze se odpoved "zastavila":
#       - splitter videl hole <invoke bez name= a zacal drzet stream
#       - uklidovy regex urezl text od prvniho <invoke> do konce odpovedi
#     Testujeme VICE variant prozy, ne jen jednu, protoze kazda mohla
#     projit jinou vetvi parseru (tag na zacatku / uprostred / na konci).
_PROSE_VARIANTS = [
    "Tagy `<invoke>` a `<parameter>` se v XML pouzivaji jako obal. KONEC-123",
    "<invoke> je obal a <parameter> hodnota. KONEC-123",
    "Nejdriv text. Pak <invoke name='bash'> bez zavorky. KONEC-123",
    "Vysvetleni <tool_calls> a <parameter> na konci vety.",
    "Zminim <parameter name='command'> ale neni to platny JSON call.",
]
for _i, _p in enumerate(_PROSE_VARIANTS):
    _t, _c = parse_tool_calls(_p, TOOLS, SPECS)
    if _t != _p or _c:
        FAILED.append(f"proza #{_i}")
        print(f"❌ proza #{_i}: text={_t[:70]!r} calls={len(_c)}")
    else:
        print(f"✅ proza #{_i} zustava CELE ({len(_p)} znaku)")

# 23) REGRESE (dukladnejsi): splitter nesmi drzet stream na ZADNE variante
#     prozy s tagy — jinak se odpoved "zastavi" a nedorazi vubec nic.
for _i, _p in enumerate(_PROSE_VARIANTS):
    for _sz in (1, 2, 3, 7, 64):
        _sp = StreamSplitter(TOOLS, SPECS)
        _o = ""
        for _j in range(0, len(_p), _sz):
            _o += _sp.feed(_p[_j:_j + _sz])
        _tl, _cl = _sp.finish()
        _o += _tl
        if _o != _p or _cl or _sp.holding:
            FAILED.append(f"proza stream #{_i}/{_sz}")
            print(f"❌ proza stream #{_i} sz={_sz}: out={_o[:50]!r} hold={_sp.holding}")
            break
    else:
        continue
    break
else:
    print("✅ proza s tagy nezablokuje stream (5 variant x 5 delek)")

# 24) REGRESE (dukladnejsi): EMBEDDED TAGY uvnitr JSON tool callu.
#     Normalizace (DSML / oprava zkomolenych tagu) musi JSON obejit — jinak
#     by se obsah souboru odeslal zkomoleny. Overujeme to `json.loads`,
#     ne jen tim, ze call existuje (slaby test 21 vyse).
_EMBED = [
    "text s <invoke> uvnitr",
    "<parameter name='x'>hodnota</parameter>",
    f"DSML marker ｜DSML｜ calls> uvnitr",
    "<tool_call>{</tool_call> uvnitr JSONu",
    "mix <calls> a ｜DSML｜ invoke name='bash'>",
]
for _i, _content in enumerate(_EMBED):
    _raw = ('<tool_call>{"name":"write","arguments":{"path":"/tmp/e",'
            '"content":' + _json.dumps(_content) + '}}</tool_call>')
    _t, _c = parse_tool_calls(_raw, TOOLS | {"write"}, SPECS)
    if len(_c) != 1 or _c[0]["name"] != "write":
        FAILED.append(f"embed #{_i}: call")
        print(f"❌ embed #{_i}: calls={_c}")
        continue
    _args = _c[0]["arguments"]
    if isinstance(_args, str):
        try:
            _args = _json.loads(_args)
        except Exception as _e:
            FAILED.append(f"embed #{_i}: json")
            print(f"❌ embed #{_i}: arguments nejsou validni JSON: {_e}; {_args[:80]!r}")
            continue
    _got = _args.get("content")
    if _got != _content:
        FAILED.append(f"embed #{_i}: content")
        print(f"❌ embed #{_i}: obsah zkomolen\n     cekano: {_content!r}\n     dostal: {_got!r}")
    else:
        print(f"✅ embed #{_i}: JSON validni, obsah byte-identical")

# 25) REGRESE: porad musi fungovat REALNY tool call (fix nesmi rozbit happy path).
for _raw, _want in [
    ('<tool_call>{"name":"bash","arguments":{"command":"ls"}}</tool_call>', "bash"),
    (U('''<tool_calls>
<invoke name="bash">
<parameter name="command">ls</parameter>
</invoke>
</tool_calls>'''), "bash"),
]:
    _t, _c = parse_tool_calls(_raw, TOOLS, SPECS)
    if len(_c) != 1 or _c[0]["name"] != _want:
        FAILED.append("realny call po fixu")
        print(f"❌ realny call: {_c}")
    else:
        print(f"✅ realny tool call po fixu: {_want}")

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
