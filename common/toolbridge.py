#!/usr/bin/env python3
"""toolbridge — premosteni tool callingu pro OpenAI-compatible shimy.

Problem: pi posila `tools` (OpenAI JSON schemas) a ceka `tool_calls` v odpovedi.
Nase backendy (DeepSeek /api/v0/chat/completion, Qwen /api/v2/chat/completions)
jsou ale "prompt in -> text out" a nativni tools neumi. Model tedy tool call
predtim jen vymyslel.

Reseni:
  1. definice toolu se vlozi do promptu jako instrukce + schema
  2. model odpovi textem — podporujeme dva formaty:
       A) <tool_call>{"name":..,"arguments":{..}}</tool_call>       (kratke argumenty)
       B) <invoke name=".."><parameter name="..">text</parameter>   (dlouhy text,
          NEescapuje se — to je i DeepSeek DSML nativni format)
  3. odpoved se rozparsuje a prelozi na OpenAI tool_calls
  4. `role: tool` zpravy zpet na text, kteremu model rozumi
"""

from __future__ import annotations

import json
import re
import uuid

# ------------------------------------------------------------------ instrukce

# Kratky, ale razantni blok, ktery MUSI byt na zacatku systemove zpravy.
# (Qwen/DeepSeek ignoruji instrukce zahrabane za dlouhym pi system promptem.)
TOOL_PREAMBLE = """STOP. Mas na k dispozici NASTROJE (tools) a MUSIS je pouzivat.

Kdyz chces neco spustit, precist, zapsat nebo editovat, TVUJ CELY VYSTUP bude
JEN jeden z techto bloků — zadny text pred nim ani po nem:

A) kratsi argumenty (JSON):
<tool_call>
{"name": "bash", "arguments": {"command": "pwd"}}
</tool_call>

B) delsi text / vice radku (obsah se NEescapuje, pises ho jak je):
<tool_call>
<invoke name="write">
<parameter name="path">/tmp/a.txt</parameter>
<parameter name="content">radek 1
radek 2</parameter>
</invoke>
</tool_call>

Pro zapis a editaci souboru VZDY pouzij formát B — nemusis resit escapovani
uvozovek ani novych radku. Vic volani za sebou = vic <invoke> bloků v jednom
<tool_call>.

NIKDY netvrď, ze nastroj neexistuje. NIKDY si nevymyslej vysledek — pockej,
az ti ho system posle jako vysledek nastroje. Kdyz nastroj nepotrebujes,
odpovez normalnim textem.

DULEZITE: Nikdy neopisuj do sve odpovedi uredni znacky z konverzace
(zadne "[VYSLEDEK NASTROJE ...]", "[USER]", "[ASSISTANT]", "[SYSTEM]").
Odpovidej jen obsahem.
"""

SYSTEM_TOOL_RULES = """\
## Dostupne nastroje

Parametry kazdeho nastroje jsou dane JSON schematem nize. `arguments` musi byt
JSON objekt odpovidajici tomu schematu.

- Muzes poslat i nekolik volani v jedne odpovedi (paralelne).
- Do bloku nepatri zadny dalsi text ani markdown fence.

"""

# Instrukce na UPLNY KONEC promptu. Model ji vidi jako posledni vec pred
# odpovedi, takze ma nejvetsi vahu. Resi konkretni selhani: DeepSeek si po
# tool callu domysli jeho vysledek a cely dalsi tah (viz _ECHO vysvetleni).
TURN_TRAILER = """\
[INSTRUKCE PRO TENTO TAH]
Odpovidas jako posledni "assistant" v konverzaci. Rozhodni se:
  A) Potrebujes nastroj  -> posli POUZE tool call. Nic dalsiho.
  B) Mas vse potrebne    -> napis finalni odpoved. Zadny tool call.

NIKDY sam nevypisuj vysledek nastroje ani dalsi tah. Znacky
[VYSLEDEK NASTROJE ...], [ASSISTANT], [USER], [SYSTEM] pise VYHRADNE system.
Kdyz je opises, rozbijes konverzaci.
Po tool callu okamzite skonci — vysledek dostanes v dalsi zprave."""

# Kratka pripominka pro DELTA tahy: schemata nastroju uz server zna z prvniho
# pozadavku, takze je neposilame znovu — staci rict, ze plati.
TOOLS_REMINDER = """\
Nastroje z prvni zpravy teto konverzace plati dal. Kdyz potrebujes nastroj,
posli tool call ve stejnem formatu (bare JSON nebo <invoke>), nic dalsiho."""


def render_tool(tool: dict) -> str:
    fn = tool.get("function") or tool
    name = fn.get("name", "?")
    desc = (fn.get("description") or "").strip().replace("\n", " ")
    params = fn.get("parameters")
    out = [f"### {name}", f"{desc}"]
    if params:
        try:
            schema = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            schema = "{}"
        if len(schema) > 4000:
            schema = schema[:4000] + "…(zkraceno)"
        out.append(f"Parametry (JSON schema): {schema}")
    return "\n".join(out)


def build_prompt(messages: list[dict], tools: list[dict] | None = None,
                 trailer: bool | None = None) -> str:
    """OpenAI messages (+tools) -> jeden prompt pro nasi chat API.

    trailer: pripojit zaverecnou instrukci. None = automaticky (jen kdyz jsou
             tools), True = vzdy (pouziva se u delta tahu, kde tools neposilame).
    """
    tools = tools or []
    schemas = SYSTEM_TOOL_RULES + "\n\n".join(render_tool(t) for t in tools) if tools else ""
    # mapa tool_call_id -> jmeno nastroje (pi u role:tool posila name=None)
    _call_names: dict[str, str] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if tc.get("id"):
                _call_names[tc["id"]] = (tc.get("function") or {}).get("name") or "tool"
    parts: list[str] = []
    seen_system = False
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        content = content or ""

        if role == "system":
            if tools and not seen_system:
                # razantni instrukce PRED pi system promptem, schemata az za nim
                parts.append(f"[SYSTEM]\n{TOOL_PREAMBLE}\n\n{content}\n\n{schemas}")
                seen_system = True
            else:
                parts.append(f"[SYSTEM]\n{content}")
        elif role == "assistant":
            # Sanitizace historie: kdyz se model v minulosti "naučil" psat
            # nase markery (vznikla echo smycka), zustaly ve session otrávené
            # zpravy. Kdybychom je poslali zpatky, model by v nich videl svuj
            # vlastni vzor a opakoval by ho dal. Proto je pri skladani promptu
            # ocistime je (ponechame jen text pred prvni ozvenou).
            em = _ECHO.search(content)
            if em:
                content = content[: em.start()].rstrip()
            block = f"[ASSISTANT]\n{content}" if content else "[ASSISTANT]"
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                block += "\n" + (
                    "<tool_call>\n"
                    + json.dumps({"name": fn.get("name"), "arguments": args}, ensure_ascii=False)
                    + "\n</tool_call>"
                )
            parts.append(block)
        elif role == "tool":
            name = m.get("name") or _call_names.get(m.get("tool_call_id") or "") or "tool"
            parts.append(f"[VYSLEDEK NASTROJE {name}]\n{content}")
        else:
            # pi posila obsah jako list casti
            parts.append(f"[USER]\n{content}")

    # Zaverecna instrukce PATRI NA KONEC — tam ji model nejspis poslechne.
    # Bez ni model casto "pokracuje v prepisu": domysli si vysledek nastroje
    # a dalsi tah (v realne session 483x), cimz si otravi vlastni historii.
    if trailer is True or (trailer is None and tools):
        parts.append(TURN_TRAILER)
    return "\n\n".join(parts)


def tool_names(tools: list[dict] | None) -> set[str] | None:
    """Jmena dostupnych nastroju (pro validaci parsovanych volani)."""
    if not tools:
        return None
    names = set()
    for t in tools:
        fn = t.get("function") or t
        if fn.get("name"):
            names.add(fn["name"])
    return names or None


# ------------------------------------------------------------------ stream

# Znaky, po kterych muze zacit tool call.
_TRIGGERS = ("<", "{", "`")

# Co je prokazatelny zacatek tool callu. Pozor: DeepSeek casto pouzije nativni
# DSML markup `<｜DSML｜...` (｜ = U+FF5C) a jeho tagy byvaji bez `tool_`
# prefixu (`< calls>`, `< invoke ...>`). Kdyz je splitter nepozna, streamuje
# je jako text a pi dostane markup misto tool callu.
_TRIG_REAL = (
    "<tool_call", "<tool_calls", "<invoke", "```",
    "<\uff5c", "\uff5cdsml\uff5c", "|dsml|",
    "< calls>", "<calls>", "< invoke", "<invoke ", "< parameter",
)

# zkomolene tagy bez `tool_` prefixu (napr. "< calls>", "< invoke name=...")
_MANGLED_OPEN = re.compile(
    r"<\s*/?\s*(?:calls|tool_calls|tool_call|invoke|parameter)\b", re.I)


class StreamSplitter:
    """Streamuje text po chuncich, ale drzi zpet to, co muze byt zacatek tool callu.

    - Bezna odpoved -> chunky tecou hned (drzi se jen pripadny zacatek markeru).
    - Jakmile se objevi prokazatelny marker tool callu -> prestat streamovat,
      vse drzet a na konci prelozit na tool_calls.
    """

    HOLD = 12

    def __init__(self, names: set[str] | None = None):
        self.names = names
        self.full: list[str] = []
        self.pending = ""
        self.holding = False
        self.emitted: list[str] = []

    @staticmethod
    def _is_marker(s: str) -> bool:
        low = s.lower()
        if any(low.startswith(m) for m in _TRIG_REAL):
            return True
        # zkomolene tagy bez `tool_` prefixu: "< calls>", "< invoke name=..."
        if _MANGLED_OPEN.match(s):
            return True
        if s.startswith("{"):
            head = s[:160]
            return '"name"' in head or "'name'" in head
        return False

    def feed(self, chunk: str) -> str:
        """Vrati text k okamzitemu odeslani (muze byt prazdny)."""
        if not chunk:
            return ""
        self.full.append(chunk)
        if self.holding:
            return ""
        self.pending += chunk

        # najdi posledni mozny zacatek markeru
        idx = -1
        for c in _TRIGGERS:
            i = self.pending.rfind(c)
            if i > idx:
                idx = i
        if idx == -1:
            # zadny potencialni marker -> vse hned ven
            out, self.pending = self.pending, ""
            self.emitted.append(out)
            return out

        out, self.pending = self.pending[:idx], self.pending[idx:]
        if self._is_marker(self.pending):
            self.holding = True
            self.emitted.append(out)
            return out
        # neni to (zatim) marker — drz jen kratky konec, zbytek ven
        if len(self.pending) > self.HOLD:
            cut = len(self.pending) - self.HOLD
            out += self.pending[:cut]
            self.pending = self.pending[cut:]
        self.emitted.append(out)
        return out

    def finish(self) -> tuple[str, list[dict]]:
        """Vrati (zbyly_text, tool_calls)."""
        full = "".join(self.full)
        if not self.holding:
            tail, self.pending = self.pending, ""
            return tail, []
        text, calls = parse_tool_calls(full, self.names)
        already = "".join(self.emitted)
        if text.startswith(already):
            return text[len(already):], calls
        return text, calls


# ------------------------------------------------------------------ normalizace

# DeepSeek posila nativni DSML markup; ｜ je U+FF5C (fullwidth vertical line).
# Vypada to takto (jmeno tagu byva "rozsekne" markerem):
#   <｜DSML｜tool_calls>  <｜DSML｜invoke name="bash">  <｜DSML｜parameter name="command">ls</parameter>
_DSML_MARKER = re.compile(r"[\uff5c|]+\s*DSML\s*[\uff5c|]+", re.I)
_DSML_TAG = re.compile(
    r"<(/?)\s*(calls|tool_?calls?|invoke|parameter)([^>]*)>", re.I
)

# Model (hlavne DeepSeek) casto zapise ukoncovaci tag zkomolene.
# V realnych session se objevilo `</call_call>` 313x, `< calls>` po odstraneni
# DSML markeru, a ruzne varianty bez podtrzitka. Vse sjednotime.
_MANGLED_TAG = re.compile(r"<\s*(/?)\s*(call_call|tool_call|tool_calls|calls)\s*>", re.I)


def normalize_tags(text: str) -> str:
    """Sjednoti zkomolene varianty tagu tool callu na <tool_call>/<tool_calls>."""
    def fix(m: re.Match) -> str:
        closing = m.group(1) or ""
        name = m.group(2).lower()
        name = "tool_calls" if name in ("calls", "call_call", "tool_calls") else "tool_call"
        return f"<{closing}{name}>"

    return _MANGLED_TAG.sub(fix, text)


def normalize_dsml(text: str) -> str:
    """<｜DSML｜invoke ...> -> <invoke ...>, <｜DSML｜calls> -> <tool_calls>."""
    if "\uff5c" not in text:
        return text
    text = _DSML_MARKER.sub("", text)

    def fix(m: re.Match) -> str:
        closing, name, rest = m.group(1), m.group(2).strip().lower(), m.group(3).strip()
        if name in ("calls", "toolcalls"):
            name = "tool_calls"
        return f"<{closing}{name}{' ' + rest if rest else ''}>"

    return _DSML_TAG.sub(fix, text)


# ------------------------------------------------------------------ parsovani

_TAG = re.compile(r"</?tool_calls?>\s*", re.I)
_JSON_BLOCK = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_INVOKE = re.compile(r"<invoke\s+name=[\"']([^\"']+)[\"'][^>]*>(.*?)(?:</invoke>|$)", re.S)
_PARAM = re.compile(r"<parameter\s+name=[\"']([^\"']+)[\"'][^>]*>(.*?)(?:</parameter>|$)", re.S)
_FENCE = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.S)

# jmena bez ukoncovaci znacky (model casto zapomene </parameter>)
_PARAM_STOP = re.compile(r"</?\s*(?:parameter|invoke|tool_calls?)\b", re.I)


def _coerce(obj) -> dict | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if not name:
        return None
    args = obj.get("arguments", obj.get("args", obj.get("parameters", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args = {"_raw": args}
    if not isinstance(args, dict):
        args = {}
    return {"name": str(name), "arguments": args}


def _parse_params(body: str) -> dict:
    """Vytahne <parameter name="x">hodnota</parameter>, tolerantni k chybejicim closum."""
    args: dict[str, object] = {}
    pos = 0
    while True:
        m = _PARAM.search(body, pos)
        if not m:
            break
        key = m.group(1)
        val = m.group(2)
        # kdyz closovaci tag chybi, _PARAM (diky |$) vezme vse — orizneme na dalsi tag
        cut = _PARAM_STOP.search(val)
        if cut:
            val = val[:cut.start()]
        val = val.strip("\n")
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            parsed = val
        args[key] = parsed
        nxt = _INVOKE.search(body, m.end())
        pos = m.end() if not nxt else len(body)
        if nxt:
            break
    return args


def _escape_inner_quotes(s: str) -> str:
    """Escapuje uvozovky, ktere model zapomnel escapovat uvnitr stringu.

    Heuristika: uvozovka uvnitr stringu je obsahova, kdyz za ni (po mezerach)
    nenasleduje , } ] : ani konec — pak ji escapujeme.
    """
    out: list[str] = []
    in_str = False
    esc = False
    n = len(s)
    for i, ch in enumerate(s):
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
                continue
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            nxt = s[j] if j < n else ""
            if nxt in (",", "}", "]", ":", ""):
                in_str = False
                out.append(ch)
            else:
                out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


def _loads_lenient(s: str):
    """json.loads, ktere prezije neescapovane uvozovky uvnitr stringu.

    Model casto posle:
        {"command": "... vyhledej \"Executing git\" v .rodata ..."}
    tedy uvozovky uvnitr hodnoty BEZ escapovani. Striktni json.loads to zahodi
    -> tool call zmizi a pi dostane jen text („model neposlal tool call").
    """
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_escape_inner_quotes(s))
    except json.JSONDecodeError:
        return None


# ZACHRANA pro rozbity format: hodnota argumentu jako HOLY text (bez uvozovek),
# ukoncena tagem nebo koncem. Priklad z realne session:
#   {"name": "bash", "arguments": {"command":
#   cd /root/x && echo "a" && grep -n "b" src/x.c | head -60
#   </function>
_RAW_ARG = re.compile(
    r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*\{\s*"([^"]+)"\s*:\s*'
    r'(?![\s]*")(.*?)\s*(?:</[a-z_]+>\s*$|\s*$)',
    re.S,
)


# Zkomolene zbytky tagu tool callu, ktere model obcas vyplivne do textu
# (napr. "<_call>", "</function>", "< calls>"). Nesmi se objevit ve vystupu.
_STRAY_TAG = re.compile(
    r"<\s*/?\s*[a-z_]{0,12}(?:tool_?calls?|calls?|_call|invoke|parameter|function)\s*>",
    re.I,
)


def _strip_stray_tags(text: str) -> str:
    """Odstrani zbytky tagu i ze STREAMOVANEHO textu (delska se po chuncich)."""
    return _STRAY_TAG.sub("", text)


def _find_json_objects(text: str):
    """Najde v textu vsechny vybalancovane JSON objekty {...} (i vic radku)."""
    out = []
    depth = 0
    start = -1
    instr = False
    esc = False
    for i, ch in enumerate(text):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append((start, i + 1, text[start:i + 1]))
                    start = -1
    return out


# znacky, ktere model casto opisuje z promptu do odpovedi
_LEAK = re.compile(
    r"^\s*(?:\[(?:VYSLEDEK NASTROJE|TOOL RESULT|ASSISTANT|USER|SYSTEM)[^\]]*\]\s*\n?)+\s*",
    re.I,
)

# Kdekoliv v textu zacina ozvena naseho promptu. Cokoliv za ni je halucinace
# (model si domysli vysledek nastroje nebo cely dalsi tah) — proto text spis
# odrizneme, nez abychom ho jen vymazali: za markerem nasleduje vymysleny obsah.
_ECHO = re.compile(
    r"\[(?:VYSLEDEK NASTROJE|TOOL RESULT|ASSISTANT|USER|SYSTEM)[^\]]*\]", re.I
)


def parse_tool_calls(text: str, tool_names: set[str] | None = None) -> tuple[str, list[dict]]:
    """Vrati (text_bez_tool_callu, [{'name':..,'arguments':{..}}]).

    tool_names: pokud je zadano, prijmou se jen volani techto nastroju
                (chrani pred falesnymi pozitivy u bezneho JSON v odpovedi).
    """
    text = normalize_dsml(text)
    text = normalize_tags(text)
    # Model casto pokracuje "v nasem prepisu" — sam si domysli vysledek nastroje
    # („[VYSLEDEK NASTROJE bash]\n...") a dalsi tah. Takova cast je halucinace a
    # nesmi se ulozit do viditelneho textu (odtud se model vzor uci a opakuje ho
    # — v realne session 483x).
    #
    # POZOR: cally vsak hledame v CELÉM textu, ne jen pred ozvenou — model casto
    # napise halucinaci a teprve PAK skutecny tool call (naměřeno: ozvena na
    # pozici 2287, realny call na 8270). Orezavame proto jen viditelny text.
    _m = _ECHO.search(text)
    cut = _m.start() if _m else len(text)
    visible = text[:cut]

    head_calls = _extract_calls(visible, tool_names)
    calls = head_calls or _extract_calls(text, tool_names)

    text = _LEAK.sub("", visible)

    # odstran z viditelneho textu pripadny osirely tool-call JSON
    _jm = re.search(r'\{\s*"name"\s*:', text)
    if calls and _jm:
        text = text[: _jm.start()]

    # odstran CELE bloky tool callu (i s obsahem parametru) — jinak by ve
    # viditelnem textu zustal treba prikaz z <parameter name="command">ls</parameter>
    text = re.sub(r"<invoke\b.*?(?:</invoke\s*>|$)", "", text, flags=re.S | re.I)
    text = re.sub(r"<tool_calls?\b.*?(?:</tool_calls?\s*>|$)", "", text, flags=re.S | re.I)

    # 5) uklid obalu, ktere nemaji zustat ve viditelnem textu
    text = _TAG.sub("", text)
    text = re.sub(r"</?(?:invoke|parameter|function|tool_call|tool_calls)\b[^>]*>",
                  "", text, flags=re.I)
    # model obcas odpoved utne uprostred tagu (napr. zbytek "</tool")
    text = re.sub(r"</?(?:tool|call|tool_call|tool_calls|invoke|parameter|function)[a-z_]*\s*$",
                  "", text, flags=re.I)
    # a ruzne zkomolene zbytky tagu kdekoliv v textu, napr. "<_call>"
    text = _STRAY_TAG.sub("", text)

    return text.strip(), calls


def _extract_calls(text: str, tool_names: set[str] | None) -> list[dict]:
    """Vytahne vsechna tool volani z textu (vsechny podporovane formaty)."""
    calls: list[dict] = []

    def ok(c: dict | None) -> bool:
        return bool(c) and (tool_names is None or c["name"] in tool_names)

    # 1) <invoke name="x"><parameter ...>…</parameter></invoke>  (DSML / Anthropic)
    if "<invoke" in text.lower():
        for m in _INVOKE.finditer(text):
            calls.append({"name": m.group(1), "arguments": _parse_params(m.group(2))})
        if calls:
            return calls

    # 2) <tool_call>{"name":..,"arguments":..}</tool_call>
    for m in _JSON_BLOCK.finditer(text):
        c = _coerce(_loads_lenient(m.group(1)))
        if not c:
            sub = re.search(r"\{.*\}", m.group(1), re.S)
            if sub:
                c = _coerce(_loads_lenient(sub.group(0)))
        if ok(c):
            calls.append(c)
    if calls:
        return calls

    # 3) fenced json {"name":..,"arguments":..}
    for m in _FENCE.finditer(text):
        c = _coerce(_loads_lenient(m.group(1)))
        if ok(c):
            calls.append(c)
    if calls:
        return calls

    # 4) BARE JSON bez obalu — DeepSeek to casto posle takhle:
    #    {"name": "read", "arguments": {"path": "..."}}
    #    Skenujeme az OPRAVENY text: na neescapovanych uvozovkach se jinak
    #    rozsype sledovani string stavu a nenajde se nic.
    for _a, _b, chunk in _find_json_objects(_escape_inner_quotes(text)):
        c = _coerce(_loads_lenient(chunk))
        if ok(c):
            calls.append(c)
    if calls:
        return calls

    # 5) ZACHRANA: model rozbije format a posle hodnotu jako HOLY text:
    #    {"name": "bash", "arguments": {"command":
    #    cd /neco && echo "x"
    #    </function>
    #    Chybi uvozovky i zavorky -> vezmeme vse po dvojtečce jako hodnotu.
    for m in _RAW_ARG.finditer(text):
        raw = m.group(3).strip()
        if raw:
            calls.append({"name": m.group(1), "arguments": {m.group(2): raw}})
    return [c for c in calls if ok(c)]


def to_openai_tool_calls(calls: list[dict]) -> list[dict]:
    return [
        {
            "id": "call_" + uuid.uuid4().hex[:20],
            "type": "function",
            "function": {
                "name": c["name"],
                "arguments": json.dumps(c["arguments"], ensure_ascii=False),
            },
        }
        for c in calls
    ]


def stream_tool_call_chunks(calls: list[dict], start_index: int = 0):
    """Vrati seznam `delta` objektu pro OpenAI SSE (kazdy tool call = 1 chunk)."""
    out = []
    for i, tc in enumerate(to_openai_tool_calls(calls)):
        out.append({
            "tool_calls": [{
                "index": start_index + i,
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["function"]["name"],
                             "arguments": tc["function"]["arguments"]},
            }]
        })
    return out
