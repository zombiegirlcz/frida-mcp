#!/usr/bin/env python3
"""Regresni testy pro problemy nalezené v realné session (session.jsonl).

Kazdy test odpovida konkretnimu selhani, ktere se v session opravdu stalo.

    python3 tests/test_session_fixes.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.toolbridge import parse_tool_calls, tool_specs  # noqa: E402
from common import netfix  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}" + (f"\n     {detail}" if detail else ""))


TOOLS = {"bash", "read", "write"}
SCHEMAS = [
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
]
SPECS = tool_specs(SCHEMAS)


# ------------------------------------------------------------------ 1) DNS
# V session se 6x za sebou objevilo:
#   [chyba: <urlopen error [Errno -3] Temporary failure in name resolution>]
# shim vratil chybu jako obsah a model se zacyklil.

def test_dns_cache():
    """Docasny vypadek DNS nesmi shodit dotaz — pouzije se posledni uspesny."""
    ok = [("1.2.3.4", 443, 0, 0, 0, ("1.2.3.4", 443))]
    calls = {"n": 0}

    def flaky(host, port, family=0, type_=0, proto=0, flags=0):
        calls["n"] += 1
        if calls["n"] == 2:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        return ok

    old = netfix._orig_getaddrinfo
    netfix._orig_getaddrinfo = flaky
    netfix._CACHE.clear()
    netfix.RETRIES, old_delays = 3, netfix.DELAYS
    netfix.DELAYS = (0, 0, 0)
    try:
        first = netfix.resolve("example.test", 443)
        check("1. DNS: prvni uspesne resolvnuti se ulozi", first == ok)

        # druhe volani: 2. pokus selze, ale cache/retry to vyresi
        second = netfix.resolve("example.test", 443)
        check("2. DNS: vypadek se prekona (retry nebo cache)", second == ok,
              f"dostali jsme {second!r}")
    finally:
        netfix._orig_getaddrinfo = old
        netfix.RETRIES, netfix.DELAYS = netfix.RETRIES, old_delays
        netfix._CACHE.clear()


def test_dns_cache_survives_and_persists():
    """Kdyz DNS neodpovida vubec, pouzije se ulozena hodnota (i po restartu)."""
    ok = [("5.6.7.8", 443, 0, 0, 0, ("5.6.7.8", 443))]

    def always_fail(host, port, family=0, type_=0, proto=0, flags=0):
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dns_cache.json")
        netfix._CACHE.clear()
        netfix._CACHE[netfix._key("h.test", 443, 0, 0, 0)] = ok
        netfix._CACHE_FILE = path
        netfix.save_cache()
        # simuluj restart: vycisti pamet, nacti ze souboru
        netfix._CACHE.clear()
        netfix.load_cache(path)
        old = netfix._orig_getaddrinfo
        netfix._orig_getaddrinfo = always_fail
        netfix.RETRIES, netfix.DELAYS = 1, (0,)
        try:
            got = netfix.resolve("h.test", 443)
            check("3. DNS: cache prezije restart shimu", got == ok,
                  f"dostali jsme {got!r}")
        finally:
            netfix._orig_getaddrinfo = old
            netfix._CACHE.clear()


def test_dns_non_transient_raises():
    """Skutecne neexistujici hostname se nesmi maskovat cache."""
    def nope(host, port, family=0, type_=0, proto=0, flags=0):
        raise socket.gaierror(-2, "Name or service not known")

    old = netfix._orig_getaddrinfo
    netfix._orig_getaddrinfo = nope
    netfix._CACHE.clear()
    netfix.RETRIES, netfix.DELAYS = 1, (0,)
    try:
        try:
            netfix.resolve("neexistuje.test", 443)
            check("4. DNS: neznamy host se vyhodi jako chyba", False, "nevyhodilo")
        except socket.gaierror:
            check("4. DNS: neznamy host se vyhodi jako chyba", True)
    finally:
        netfix._orig_getaddrinfo = old
        netfix._CACHE.clear()


# ------------------------------------------- 2) dvojite zabalene argumenty
# V session:
#   {"name":"bash","arguments":{"arguments":"{\"command\": \"cd /root/...\"}"}}
#   -> Validation failed for tool "bash":
#        - command: must have required properties command

def test_double_wrapped_arguments():
    inner = '{"command": "cd /root/elf_loader && ls -la"}'
    raw = json.dumps({"name": "bash", "arguments": {"arguments": inner}})
    _, calls = parse_tool_calls(raw, TOOLS, SPECS)
    got = calls[0]["arguments"] if calls else {}
    check("5. args: {arguments:{arguments:'{...}'}} se rozbali",
          got == {"command": "cd /root/elf_loader && ls -la"}, f"arguments={got!r}")

    # dvakrat escapovane uvozovky (presne z logu)
    raw2 = '{"name":"bash","arguments":{"arguments":"{\\"command\\": \\"ls -la\\"}"}}'
    _, calls2 = parse_tool_calls(raw2, TOOLS, SPECS)
    got2 = calls2[0]["arguments"] if calls2 else {}
    check("6. args: dvakrat escapovane uvozovky", got2 == {"command": "ls -la"},
          f"arguments={got2!r}")

    # tri urovne (args -> arguments -> {...})
    raw3 = json.dumps({"name": "bash", "arguments": {"args": {"arguments": inner}}})
    _, calls3 = parse_tool_calls(raw3, TOOLS, SPECS)
    got3 = calls3[0]["arguments"] if calls3 else {}
    check("7. args: rozbali se i vic urovni",
          got3 == {"command": "cd /root/elf_loader && ls -la"}, f"arguments={got3!r}")


def test_normal_args_untouched():
    """Normalni tool call se nesmi rozbit."""
    raw = json.dumps({"name": "bash", "arguments": {"command": "ls -la"}})
    _, calls = parse_tool_calls(raw, TOOLS, SPECS)
    check("8. args: bezny call zustava", calls and calls[0]["arguments"] == {"command": "ls -la"},
          f"calls={calls}")

    raw2 = json.dumps({"name": "write",
                       "arguments": {"path": "/x", "content": "a"}})
    _, calls2 = parse_tool_calls(raw2, TOOLS, SPECS)
    check("9. args: vicklicovy call zustava",
          calls2 and calls2[0]["arguments"] == {"path": "/x", "content": "a"},
          f"calls={calls2}")


def test_wrapped_not_unwrapped_when_ambiguous():
    """Kdyz ma nastroj vic parametru, jednoklicovy {"arguments": ...} rozbalime,
    ale nesmime rozbit nastroj, ktery ma parametr `arguments` mezi jinymi."""
    # "arguments" jako jeden z vice klicu -> nechame byt (neni to obalka)
    obj = {"arguments": {"arguments": "x"}, "path": "/y"}
    from common.toolbridge import _coerce
    out = _coerce({"name": "bash", "arguments": obj})
    check("10. args: s vice klici se nerozbaluje",
          out and out["arguments"] == obj, f"out={out}")


def test_bad_string_not_unwrapped():
    """Kdyz vnitrni hodnota neni JSON, nechame puvodni tvar."""
    from common.toolbridge import _coerce
    out = _coerce({"name": "bash", "arguments": {"arguments": "neni json"}})
    check("11. args: nevalidni vnitrni JSON se necha",
          out and out["arguments"] == {"arguments": "neni json"}, f"out={out}")


# ------------------------------------------------- 3) chybova zprava shimu
# V session se chyba posilala jako "[chyba: <urlopen ...>]" a model ji opsal
# 6x za sebou. Zprava musi jasne rict, ze jde o chybu shimu.

def test_error_text():
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepseek", "bridge"))
    import openai_shim  # noqa: PLC0415

    dns = openai_shim._err_text(
        OSError("<urlopen error [Errno -3] Temporary failure in name resolution>"))
    check("12. chyba: DNS je popsana a označena jako chyba shimu",
          "DNS" in dns and "CHYBA SHIMU" in dns and "neopakuj" in dns, repr(dns[:90]))

    to = openai_shim._err_text(TimeoutError("timed out"))
    check("13. chyba: timeout je popsany", "timeout" in to.lower() and "CHYBA SHIMU" in to,
          repr(to[:80]))

    rl = openai_shim._err_text(
        RuntimeError("chat/completion: RATE LIMIT — Příliš časté zprávy."))
    check("14b. chyba: rate limit ma vlastni srozumitelnou zpravu",
          "RATE LIMIT" in rl and "minutu" in rl and "neopakuj" in rl, repr(rl[:90]))

    other = openai_shim._err_text(RuntimeError("neco divneho"))
    check("14. chyba: neznama chyba je oznacena jako chyba shimu",
          "CHYBA SHIMU" in other and "neopakuj" in other, repr(other[:80]))


# ------------------------------------------------ 4) RATE LIMIT (hlavni pricina)
# V session.jsonl: odpoved bez dat. Syrova odpoved serveru je:
#   event: hint
#   data: {"type":"error","content":"Příliš časté zprávy…",
#          "finish_reason":"rate_limit_reached"}
# Shim to hlasil jako "prazdna odpoved (zadne data:)" — nedalo se to
# rozlisit od neplatneho tokenu a cely tah se ztratil.

RATE_HINT = {"type": "error",
             "content": "Příliš časté zprávy. Zkuste to znovu později.",
             "clear_response": True, "finish_reason": "rate_limit_reached"}


def test_hint_error_detects_rate_limit():
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepseek", "bridge"))
    from deepseek.bridge.deepseek_api import _hint_error  # noqa: PLC0415

    e = _hint_error(RATE_HINT)
    check("15. rate limit: rozpoznan jako retryable",
          e is not None and e.retry and e.rate_limited, f"e={e}")
    check("16. rate limit: zprava obsahuje RATE LIMIT",
          e is not None and "RATE LIMIT" in str(e), str(e)[:80])

    # normalni chunky se nesmi plest s chybou
    check("17. rate limit: bezny chunk neni error",
          _hint_error({"p": "response/content", "o": "APPEND", "v": "ahoj"}) is None)
    check("18. rate limit: event close neni error",
          _hint_error({"click_behavior": "retry", "auto_resume": False}) is None)

    other = _hint_error({"type": "error", "content": "neco jineho"})
    check("19. rate limit: jina chyba se neoznaci jako rate limit",
          other is not None and not other.rate_limited, f"other={other}")


def test_rate_limit_retry():
    """completion_stream musi pri rate limitu zkusit znovu (a pak uspet)."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepseek", "bridge"))
    from deepseek.bridge import deepseek_api as A  # noqa: PLC0415

    api = A.DeepSeekAPI.__new__(A.DeepSeekAPI)
    api.last_response_message_id = None
    calls = {"n": 0}

    def fake_once(session_id, prompt, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise A.DeepSeekError("rate limit", retry=True, rate_limited=True)
        yield "answer", "HOTOVO"

    api._completion_once = fake_once
    old_delays = A.RATE_LIMIT_DELAYS
    A.RATE_LIMIT_DELAYS = (0, 0, 0)
    try:
        got = "".join(t for _k, t in api.completion_stream("s", "p"))
        check("20. rate limit: po 2 selhanich se to povede",
              got == "HOTOVO" and calls["n"] == 3, f"got={got!r} calls={calls['n']}")
    finally:
        A.RATE_LIMIT_DELAYS = old_delays


def test_rate_limit_does_not_retry_after_content():
    """Kdyz uz neco odeslo, retry by obsah zduplikoval -> nezkouset."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepseek", "bridge"))
    from deepseek.bridge import deepseek_api as A  # noqa: PLC0415

    api = A.DeepSeekAPI.__new__(A.DeepSeekAPI)
    calls = {"n": 0}

    def fake_once(session_id, prompt, **kw):
        calls["n"] += 1
        yield "answer", "cast"
        raise A.DeepSeekError("rate limit", retry=True, rate_limited=True)

    api._completion_once = fake_once
    old_delays = A.RATE_LIMIT_DELAYS
    A.RATE_LIMIT_DELAYS = (0, 0, 0)
    try:
        try:
            "".join(t for _k, t in api.completion_stream("s", "p"))
            check("21. rate limit: po odeslani obsahu se neopakuje", False, "nevyhodilo")
        except A.DeepSeekError:
            check("21. rate limit: po odeslani obsahu se neopakuje", calls["n"] == 1,
                  f"pokusu={calls['n']}")
    finally:
        A.RATE_LIMIT_DELAYS = old_delays


# ------------------------------------ 5) Qwen: "nastroje nejsou dostupne"
# Uzivatel: "qwen porad pise ze nastroje jsou nedostupne... jako by to ani
# nezkusila". Pricina: pi posila system prompt s roli "developer"; stara
# `_cap_messages` chranila jen "system", takze pri delsi konverzaci developer
# zpravu ZAHODILA — a s ni i TOOL_PREAMBLE + schemata nastroju, ktere
# `build_prompt` vklada PRAVE do ni.

def test_cap_keeps_developer_message():
    """pi posila system prompt s roli "developer" — pri zkraceni se nesmi zahodit,
    jinak zmizi i schemata nastroju a model hlasi "nastroje nejsou dostupne"."""
    from common.toolbridge import cap_messages, build_prompt  # noqa: PLC0415

    tools = [{"type": "function", "function": {"name": "bash",
              "description": "Spusti prikaz",
              "parameters": {"type": "object",
                             "properties": {"command": {"type": "string"}},
                             "required": ["command"]}}}]
    msgs = [
        {"role": "developer", "content": "Jsi pi agent s nastroji."},
        {"role": "user", "content": "ahoj"},
        {"role": "assistant", "content": "y" * 400},
        {"role": "user", "content": "Posledni dotaz."},
    ]
    kept, trimmed = cap_messages(msgs, limit=200)
    roles = [m.get("role") for m in kept]
    check("22. cap: developer zprava se pri zkraceni ZACHOVA",
          "developer" in roles, f"role po zkraceni={roles}")
    check("23. cap: zkraceni opravdu probehlo", trimmed is True)
    check("24. cap: posledni zprava zustava",
          any(m.get("content") == "Posledni dotaz." for m in kept), f"kept={roles}")

    prompt = build_prompt(kept, tools)
    check("25. cap: po zkraceni zustanou schemata nastroju v promptu",
          '"bash"' in prompt and "command" in prompt,
          "schemata nastroju v promptu chybi")


def test_cap_no_trim_when_small():
    from common.toolbridge import cap_messages  # noqa: PLC0415

    msgs = [{"role": "developer", "content": "a"},
            {"role": "user", "content": "b"}]
    kept, trimmed = cap_messages(msgs, limit=10_000)
    check("26. cap: kratka konverzace se nezkracuje",
          kept == msgs and trimmed is False)


def test_cap_system_role_also_works():
    from common.toolbridge import cap_messages  # noqa: PLC0415

    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "z" * 400}]
    kept, trimmed = cap_messages(msgs, limit=50)
    check("27. cap: role system se chrani taky",
          any(m.get("role") == "system" for m in kept) and trimmed,
          f"kept={[m.get('role') for m in kept]}")


# ------------------------------------ 6) "Dosažen limit délky. Začněte nový chat."
# V praxi: shim poslal prompt s 10 963 352 znaky (11 MB!) a server odmitl.
# Novy chat nepomuze, kdyz je moc dlouhy samotny prompt -> nutne zkracovat.

LENGTH_HINT = {"type": "error", "content": "Dosažen limit délky. Začněte nový chat.",
               "clear_response": True, "finish_reason": "length_limit"}


def test_hint_error_detects_length_limit():
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deepseek", "bridge"))
    from deepseek.bridge.deepseek_api import _hint_error, _is_length_error  # noqa: PLC0415

    e = _hint_error(LENGTH_HINT)
    check("28. limit delky: rozpoznan jako length_limited",
          e is not None and e.length_limited, f"e={e}")
    check("29. limit delky: NENI oznacen jako rate limit",
          e is not None and not e.rate_limited, f"rl={getattr(e, 'rate_limited', None)}")
    check("30. limit delky: nema smysl opakovat stejny chat (retry=False)",
          e is not None and e.retry is False)
    # i cesky bez diakritiky a anglicky
    check("31. limit delky: varianta bez diakritiky",
          _is_length_error("Dosazen limit delky. Zacnete novy chat."))
    check("32. limit delky: anglicka varianta",
          _is_length_error("Maximum context length exceeded"))
    check("33. limit delky: bezna chyba to neni",
          not _is_length_error("invalid token"))


def test_deepseek_shim_caps_context():
    """Shim musi zkratit kontext, aby se 11MB prompt vubec neposlal."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "deepseek", "bridge", "openai_shim.py"), encoding="utf-8").read()
    check("34. deepseek: shim pouziva cap_messages", "cap_messages(" in src)
    check("35. deepseek: ma MAX_PROMPT_CHARS", "MAX_PROMPT_CHARS" in src)
    check("36. deepseek: zkracuje az PO _convs.lookup (kvuli prefixu)",
          src.index("_convs.lookup(messages)") < src.index("cap_messages(messages,"),
          "cap_messages je pred lookup -> rozbije navazani chatu")
    check("37. deepseek: bind() dostava PUVODNI historii",
          "_convs.bind(messages," in src, "bind() musi mit original, ne zkraceny")


def test_cap_really_shrinks_11mb():
    """11 MB konverzace se musi vejit do limitu."""
    from common.toolbridge import cap_messages, build_prompt  # noqa: PLC0415

    msgs = [{"role": "developer", "content": "Jsi agent."}]
    msgs += [{"role": "user", "content": "x" * 100_000} for _ in range(110)]
    msgs.append({"role": "user", "content": "Posledni otazka."})
    total = sum(len(str(m.get("content", ""))) for m in msgs)
    capped, trimmed = cap_messages(msgs, 400_000)
    capped_total = sum(len(str(m.get("content", ""))) for m in capped)
    check("38. limit delky: 11 MB se zkrati pod limit",
          trimmed and capped_total <= 400_000,
          f"pred={total} po={capped_total}")
    check("39. limit delky: posledni otazka zustava",
          capped[-1].get("content") == "Posledni otazka.")
    check("40. limit delky: developer zustava (schémata)",
          any(m.get("role") == "developer" for m in capped))


# ------------------------- 7) cap meril SPATNE (ignoroval tool_calls arguments)
# V praxi: cap rekl "zkracen na 400000 znaku (2 z 2 zprav)", ale prompt mel
# 11 084 794 znaku -> server vratil "Dosažen limit délky". Pricina: `_size()`
# meril jen `content`, ale `build_prompt()` renderuje i
# `tool_calls[].function.arguments`, kde byla ta megabajtova data.

def test_size_counts_tool_call_arguments():
    from common.toolbridge import _msg_size  # noqa: PLC0415

    big = "x" * 5_000_000
    msg = {"role": "assistant", "content": "Zapisuji.",
           "tool_calls": [{"id": "c1", "type": "function", "function": {
               "name": "write",
               "arguments": json.dumps({"path": "/tmp/a", "content": big})}}]}
    size = _msg_size(msg)
    check("41. cap: _msg_size pocita i tool_calls arguments",
          size > 5_000_000, f"_msg_size={size} (mel by byt > 5 MB)")

    # i obsah jako bloky (pi posila content jako list)
    blocks = {"role": "user", "content": [{"type": "text", "text": "y" * 1000}]}
    check("42. cap: _msg_size pocita i content bloky",
          _msg_size(blocks) >= 1000, f"_msg_size={_msg_size(blocks)}")


def test_cap_real_11mb_case():
    """Presne scenar z praxe: 2 zpravy, z toho jedna 11 MB."""
    from common.toolbridge import cap_messages, build_prompt  # noqa: PLC0415

    big = "x" * 11_000_000
    msgs = [
        {"role": "developer", "content": "Jsi agent s nastroji."},
        {"role": "assistant", "content": "Zapisuji.",
         "tool_calls": [{"id": "c1", "type": "function", "function": {
             "name": "write",
             "arguments": json.dumps({"path": "/tmp/a", "content": big})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "Pokracuj a spocitej .py soubory."},
    ]
    capped, trimmed = cap_messages(msgs, 400_000)
    prompt = build_prompt(capped, None)
    check("43. limit delky: 11 MB se opravdu zmensi",
          len(prompt) < 1_000_000, f"prompt={len(prompt)}")
    check("44. limit delky: zkraceni hlaseno", trimmed is True)

    args = capped[1]["tool_calls"][0]["function"]["arguments"]
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError:
        parsed = None
    check("45. limit delky: argumenty zustavaji VALIDNI JSON",
          isinstance(parsed, dict), "rozbity JSON -> build_prompt zahodi obsah")
    check("46. limit delky: klic 'content' zustava (jen zkraceny)",
          isinstance(parsed, dict) and "content" in parsed)
    check("47. limit delky: je videt, ze se kratilo",
          "zkráceno" in args, "chybi znacka o zkraceni")


def test_shrink_size_matches_text_content():
    """_msg_size musi odpovidat realne velikosti promptu (jinak cap klame)."""
    from common.toolbridge import cap_messages, build_prompt, _msg_size  # noqa: PLC0415

    msgs = [{"role": "developer", "content": "d"},
            {"role": "user", "content": "z" * 500_000}]
    capped, trimmed = cap_messages(msgs, 200_000)
    prompt = build_prompt(capped, None)
    check("48. cap: zkraceny prompt se vejde pod limit",
          len(prompt) <= 220_000, f"prompt={len(prompt)} limit=200000")
    check("49. cap: hlaseno zkraceni", trimmed is True)


# --------------------------- 8) _shrink_args: JSON musi zustat VALIDNI
# Uzivatel: "v testu u _shrink_args pridej json.loads(args) — ted jen
# kontrolujes, ze arguments nejsou prazdne, ne ze zustaly validni JSON."
# Je to dulezite: `build_prompt()` dela `json.loads(fn["arguments"])`, a kdyz
# JSON rozbijeme, spadne do `args = {}` a zahodi obsah UPLNE.

def test_shrink_args_keeps_valid_json():
    from common.toolbridge import _shrink_args  # noqa: PLC0415

    big = "x" * 2_000_000
    raw = json.dumps({"path": "/tmp/a", "content": big})
    out = _shrink_args(raw, 10_000)

    # 1) MUSI to byt validni JSON (ne useknuty string)
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as e:
        parsed = None
        check("50. _shrink_args: vystup je validni JSON", False, f"{e}: {out[:80]!r}")
    if parsed is not None:
        check("50. _shrink_args: vystup je validni JSON", isinstance(parsed, dict),
              f"typ={type(parsed).__name__}")

    # 2) struktura zustava — vsechny klice a jejich typy
    check("51. _shrink_args: klice zustavaji",
          isinstance(parsed, dict) and set(parsed) == {"path", "content"},
          f"klice={list(parsed) if isinstance(parsed, dict) else None}")
    check("52. _shrink_args: kratky string se NEMENI",
          isinstance(parsed, dict) and parsed.get("path") == "/tmp/a",
          f"path={parsed.get('path') if isinstance(parsed, dict) else None!r}")

    # 3) dlouha hodnota se zmensi a je videt, ze se kratilo
    got = parsed.get("content") if isinstance(parsed, dict) else None
    check("53. _shrink_args: dlouha hodnota se zmensi",
          isinstance(got, str) and len(got) < 20_000,
          f"delka={len(got) if isinstance(got, str) else None}")
    check("54. _shrink_args: je videt znacka zkraceni",
          isinstance(got, str) and "zkráceno" in got, "chybi znacka")
    check("55. _shrink_args: zachovan zacatek i konec",
          isinstance(got, str) and got.startswith("xxx") and got.endswith("xxx"),
          f"start={got[:6]!r} konec={got[-6:]!r}" if isinstance(got, str) else "n/a")


def test_shrink_args_fallbacks():
    """Rozbity / nezvykly vstup nesmi shodit _shrink_args."""
    from common.toolbridge import _shrink_args  # noqa: PLC0415

    # rozbity JSON -> zkrati se raw string (a nic nespadne)
    broken = '{"command": "' + "y" * 50_000
    out = _shrink_args(broken, 1_000)
    check("56. _shrink_args: rozbity JSON nespadne",
          isinstance(out, str) and len(out) < 5_000, f"delka={len(out)}")

    # JSON ktery neni objekt (napr. seznam) -> zkrati se raw
    out2 = _shrink_args(json.dumps(["a"] * 20_000), 1_000)
    check("57. _shrink_args: neobjektovy JSON nespadne",
          isinstance(out2, str) and len(out2) < 5_000, f"delka={len(out2)}")

    # prazdny objekt projde beze zmeny
    check("58. _shrink_args: prazdny objekt zustava",
          json.loads(_shrink_args("{}", 100)) == {})

    # nezmineny kratky vstup se vrati presne
    small = json.dumps({"command": "ls -la"})
    check("59. _shrink_args: maly vstup beze zmeny",
          _shrink_args(small, 10_000) == small)

    # hodnoty ktere nejsou stringy (cisla, bool, null, vnorene) se nekrátí
    mixed = json.dumps({"n": 12345, "b": True, "z": None, "o": {"k": "v"}})
    check("60. _shrink_args: nestringove hodnoty se nekrátí",
          json.loads(_shrink_args(mixed, 10_000)) == json.loads(mixed))


# ------------------------- 9) qwen3.8-max: bare JSON prerusene zbytkem formatu B
# V session.jsonl (2026-10-01, konec): qwen zacal tool call formatem A
# ({"name":...), ale skoncil zbytkem formatu B misto uzaviraci ")":
#   {"name": "read", "arguments": {"path": "...main.py",
#   "limit": 50}
#   </parameter>
#   </invoke>
# Chybela 1 uzaviraci "}" (jen "arguments" se uzavrelo, cely objekt ne).
# _find_json_objects vyzaduje balanc -> nenasel nic, cely tah zmizel beze
# stopy (zadny call, zadna chyba) a model nevedel, ze se nic nestalo ->
# dal hlasil "Tool bash does not exists.".

def test_qwen_truncated_bare_json_recovered():
    raw = (
        '<tool_call>\n'
        '{"name": "read", "arguments": '
        '{"path": "/root/statistiky/.agents/skills/match-probability/scripts/main.py",\n'
        '"limit": 50}\n'
        '</parameter>\n'
        '</invoke>\n'
        '</tool_call>'
    )
    text, calls = parse_tool_calls(raw, TOOLS, SPECS)
    check("61. qwen: prerusene bare JSON (chybi '}') se dopocita",
          bool(calls) and calls[0]["name"] == "read", f"calls={calls}")
    check("62. qwen: argumenty jsou spravne (path + limit)",
          calls and calls[0]["arguments"] == {
              "path": "/root/statistiky/.agents/skills/match-probability/scripts/main.py",
              "limit": 50}, f"arguments={calls[0]['arguments'] if calls else None!r}")
    check("63. qwen: zbytek formatu B (</parameter></invoke>) nezustane v textu",
          "</parameter>" not in text and "</invoke>" not in text, repr(text))


def test_qwen_truncated_bare_json_end_of_text():
    """Stejna diera, ale model skonci tah uplne (bez zbytku formatu B)."""
    raw = '{"name": "bash", "arguments": {"command": "echo ahoj"'
    _text, calls = parse_tool_calls(raw, TOOLS, SPECS)
    check("64. qwen: prerusene na konci tahu (bez tagu) se taky dopocita",
          bool(calls) and calls[0] == {"name": "bash",
                                       "arguments": {"command": "echo ahoj"}},
          f"calls={calls}")


# ----------------------- 10) qwen: 33 tool callu v jednom tahu (fronta/stream)
# V session.jsonl (2026-10-01, ~20:30): qwen poslal v JEDNOM tahu 33 tool
# callu (read/bash), hodne z nich prekryvajici se varianty tehoz dotazu
# (stejny soubor na ruznych offsetech, podobne grepy) — model nedostal
# zpetnou vazbu k drivejsim volanim (Qwen neni nativni tool-calling API,
# shim cte cely stream az do konce), takze misto cekani na vysledek proste
# "zkousel dal". Vsech 33 se pak najednou poslalo do pi k provedeni.
#
# Oprava: StreamSplitter.pending_call_count() umoznuje shimu zavrit upstream
# spojeni, jakmile pocet UZAVRENYCH volani v bufferu dosahne
# MAX_CALLS_PER_TURN; parse_tool_calls() navic jako pojistku orizne
# vysledny seznam na stejnou hodnotu, i kdyz se stream nestihl zavrit vcas.

def _bare_call_block(i: int) -> str:
    return ('<tool_call>\n{"name": "bash", "arguments": '
            f'{{"command": "echo {i}"}}}}\n</tool_call>\n')


def test_stream_splitter_counts_complete_calls():
    from common.toolbridge import StreamSplitter  # noqa: PLC0415

    sp = StreamSplitter({"bash"}, SPECS)
    sp.feed(_bare_call_block(1))
    check("65. qwen: 1. uzavrene volani se spocita",
          sp.pending_call_count() == 1, f"count={sp.pending_call_count()}")
    sp.feed(_bare_call_block(2))
    sp.feed(_bare_call_block(3))
    check("66. qwen: dalsi uzavrena volani se pricitaji",
          sp.pending_call_count() == 3, f"count={sp.pending_call_count()}")


def test_stream_splitter_ignores_incomplete_tail():
    """Rozepsane (jeste neuzavrene) volani se NESMI pocitat jako hotove —
    jinak by shim zavrel spojeni uprostred toho, jak model teprve pise."""
    from common.toolbridge import StreamSplitter  # noqa: PLC0415

    sp = StreamSplitter({"bash"}, SPECS)
    sp.feed(_bare_call_block(1))
    sp.feed('<tool_call>\n{"name": "bash", "arguments": {"command": "echo rozepsane')
    check("67. qwen: rozepsane volani na konci se NEpocita",
          sp.pending_call_count() == 1, f"count={sp.pending_call_count()}")


def test_parse_tool_calls_caps_runaway_flood():
    """I bez streamovaneho zastaveni: 33 volani v jednom textu se orizne na
    MAX_CALLS_PER_TURN — pojistka pro non-streaming `complete()` i pro
    pripad, ze se stream nestihl zavrit vcas."""
    from common.toolbridge import MAX_CALLS_PER_TURN  # noqa: PLC0415

    raw = "".join(_bare_call_block(i) for i in range(33))
    _text, calls = parse_tool_calls(raw, TOOLS, SPECS)
    check("68. qwen: 33 volani v jednom tahu se orizne na MAX_CALLS_PER_TURN",
          len(calls) == MAX_CALLS_PER_TURN, f"len(calls)={len(calls)}")
    check("69. qwen: oriznute volani jsou PRVNICH N (poradi zachovano)",
          [c["arguments"]["command"] for c in calls] ==
          [f"echo {i}" for i in range(MAX_CALLS_PER_TURN)],
          f"calls={[c['arguments'] for c in calls]}")


def test_parse_tool_calls_small_batch_untouched():
    """Bezny mensi pocet paralelnich volani (ten skill vyslovne doporucuje)
    se oriznout NESMI."""
    from common.toolbridge import MAX_CALLS_PER_TURN  # noqa: PLC0415

    n = min(3, MAX_CALLS_PER_TURN - 1)
    raw = "".join(_bare_call_block(i) for i in range(n))
    _text, calls = parse_tool_calls(raw, TOOLS, SPECS)
    check("70. qwen: mala davka paralelnich volani zustava cela",
          len(calls) == n, f"len(calls)={len(calls)} ocekavano={n}")


# ------------------------- 11) qwen: halucinace unikla DO STREAMU jako proza
# V session.jsonl (2026-10-01, 20:48, novy pokus po kompakci): stejny dotaz
# jako predtim, ale tentokrat qwen NEZKOUSEL zadny tool call — proste napsal
# do bezneho textu "Tool bash does not exists." 5x za sebou jako preambuli
# pred skutecnou odpovedi:
#   "Tool bash does not exists.Tool bash does not exists.Tool bash does not
#    exists.Tool read does not exists.Tool write does not exists.## Jakou AI…"
# _TOOL_NOTFOUND uz tuhle halucinaci umel vycistit UVNITR parse_tool_calls(),
# ale to se vola jen z StreamSplitter.finish() — a do nej se dojde jen tehdy,
# kdyz text nekdy zacal vypadat jako SKUTECNY pokus o tool call (holding).
# Bezna proza bez < { ` triggeru se posle VEN HNED, finish()/parse_tool_calls
# se na ni vubec nedostanou a halucinace unikne nescrubnuta primo klientovi
# (a zpet do historie, kde se podle dosavadnich komentaru v kodu jen
# zesiluje). Oprava: StreamSplitter._scrub_tool_nf() hlida tenhle vzorec
# nezavisle na marker/holding logice, primo v bezne streamovanem textu.

def test_stream_splitter_scrubs_prose_hallucination_whole_chunk():
    """Cely text dorazi v JEDNOM kuse (realisticke SSE API chovani)."""
    from common.toolbridge import StreamSplitter  # noqa: PLC0415

    text = ("Podivam se nejdriv.\n\n"
            "Tool bash does not exists.Tool bash does not exists."
            "Tool read does not exists.Tool write does not exists."
            "## Jakou AI pouziva agent?")
    sp = StreamSplitter(TOOLS, SPECS)
    out = sp.feed(text)
    tail, calls = sp.finish()
    result = out + tail
    check("71. qwen: halucinujici proza bez tool callu se vycisti (1 kus)",
          "does not exist" not in result.lower(), repr(result))
    check("72. qwen: skutecny obsah za halucinaci zustava cely",
          result.endswith("## Jakou AI pouziva agent?"), repr(result))
    check("73. qwen: zadny tool call se nevymysli (zadny byl zamyslen)",
          calls == [], f"calls={calls}")


def test_stream_splitter_scrubs_prose_hallucination_word_chunks():
    """Realisticka granularita: API posila po slovech/vetsich kusech, ne po
    jednotlivych bajtech (viz docstring _scrub_tool_nf)."""
    import re as _re  # noqa: PLC0415
    from common.toolbridge import StreamSplitter  # noqa: PLC0415

    text = ("Podivam se nejdriv.\n\n"
            "Tool bash does not exists.Tool bash does not exists."
            "Tool read does not exists."
            "## Jakou AI pouziva agent?\n\nZadnou AI nepouziva.")
    sp = StreamSplitter(TOOLS, SPECS)
    out = "".join(sp.feed(c) for c in _re.findall(r"\S+\s*|\s+", text))
    tail, _calls = sp.finish()
    result = out + tail
    check("74. qwen: halucinace se vycisti i po slovnich kusech streamu",
          result == ("Podivam se nejdriv.\n\n"
                     "## Jakou AI pouziva agent?\n\nZadnou AI nepouziva."),
          repr(result))


def test_stream_splitter_does_not_eat_legitimate_tool_word():
    """Bezne pouziti slova 'Tool' v proze se nesmi ztratit — jen smi dorazit
    s malym zpozdenim (cap), nikdy ne zmizet."""
    from common.toolbridge import StreamSplitter  # noqa: PLC0415

    text = "Tool volani v tomhle projektu resi common/toolbridge.py."
    sp = StreamSplitter(TOOLS, SPECS)
    out = sp.feed(text)
    tail, _calls = sp.finish()
    check("75. qwen: bezna veta se slovem 'Tool' se NEZTRATI",
          out + tail == text, repr(out + tail))


def main() -> int:
    test_dns_cache()
    test_dns_cache_survives_and_persists()
    test_dns_non_transient_raises()
    test_double_wrapped_arguments()
    test_normal_args_untouched()
    test_wrapped_not_unwrapped_when_ambiguous()
    test_bad_string_not_unwrapped()
    test_error_text()
    test_hint_error_detects_rate_limit()
    test_rate_limit_retry()
    test_rate_limit_does_not_retry_after_content()
    test_cap_keeps_developer_message()
    test_cap_no_trim_when_small()
    test_cap_system_role_also_works()
    test_hint_error_detects_length_limit()
    test_deepseek_shim_caps_context()
    test_cap_really_shrinks_11mb()
    test_size_counts_tool_call_arguments()
    test_cap_real_11mb_case()
    test_shrink_size_matches_text_content()
    test_shrink_args_keeps_valid_json()
    test_shrink_args_fallbacks()
    test_qwen_truncated_bare_json_recovered()
    test_qwen_truncated_bare_json_end_of_text()
    test_stream_splitter_counts_complete_calls()
    test_stream_splitter_ignores_incomplete_tail()
    test_parse_tool_calls_caps_runaway_flood()
    test_parse_tool_calls_small_batch_untouched()
    test_stream_splitter_scrubs_prose_hallucination_whole_chunk()
    test_stream_splitter_scrubs_prose_hallucination_word_chunks()
    test_stream_splitter_does_not_eat_legitimate_tool_word()
    print()
    if FAILED:
        print(f"SELHALO: {len(FAILED)} — " + ", ".join(FAILED))
        return 1
    print("Vsechny testy prosly ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
