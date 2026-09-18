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

    other = openai_shim._err_text(RuntimeError("neco divneho"))
    check("14. chyba: neznama chyba je oznacena jako chyba shimu",
          "CHYBA SHIMU" in other and "neopakuj" in other, repr(other[:80]))


def main() -> int:
    test_dns_cache()
    test_dns_cache_survives_and_persists()
    test_dns_non_transient_raises()
    test_double_wrapped_arguments()
    test_normal_args_untouched()
    test_wrapped_not_unwrapped_when_ambiguous()
    test_bad_string_not_unwrapped()
    test_error_text()
    print()
    if FAILED:
        print(f"SELHALO: {len(FAILED)} — " + ", ".join(FAILED))
        return 1
    print("Vsechny testy prosly ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
