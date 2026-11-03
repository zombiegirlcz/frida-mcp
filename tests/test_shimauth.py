#!/usr/bin/env python3
"""Testy pro common/shimauth.py — volitelny Bearer klic na shimu.

Shim byl dosud jen na loopbacku a `apiKey` v models.json ignoroval. Kdyz se
ale vystavi na sit (`SHIM_HOST=0.0.0.0`), musi se dat zamknout — jinak
komukoli na siti otevre DeepSeek/Qwen ucet a PoW.

    python3 tests/test_shimauth.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import shimauth  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}  {detail}")


def with_env(**kw):
    """Docasne nastavi env (None = smazat) a vrati cleanup funkci."""
    old = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return restore


# ------------------------------------------------------ auth vypnuta (default)
restore = with_env(SHIM_API_KEY=None, SHIM_HOST=None, FRIDA_MCP_BIND=None)
try:
    check("1. bez SHIM_API_KEY je auth vypnuta", shimauth.api_key() is None)
    ok, why = shimauth.check_header(None)
    check("2. bez klice projde i request bez hlavicky", ok and why == "auth-off", why)
    ok, why = shimauth.check_request("/v1/chat/completions", None)
    check("3. bez klice projde i completions", ok, why)
    check("4. default bind je loopback", shimauth.bind_host() == "127.0.0.1",
          shimauth.bind_host())
finally:
    restore()


# ------------------------------------------------------------- auth zapnuta
restore = with_env(SHIM_API_KEY="tajne123")
try:
    check("5. SHIM_API_KEY se nacte", shimauth.api_key() == "tajne123")

    ok, why = shimauth.check_header(None)
    check("6. bez hlavicky -> 401", not ok and "Authorization" in why, why)

    ok, why = shimauth.check_header("Bearer tajne123")
    check("7. spravny Bearer projde", ok and why == "ok", why)

    ok, _ = shimauth.check_header("bearer tajne123")
    check("8. 'bearer' malymi pismeny taky projde", ok)

    ok, _ = shimauth.check_header("  Bearer   tajne123  ")
    check("9. mezery okolo se ignoruji", ok)

    ok, why = shimauth.check_header("Bearer spatny")
    check("10. spatny klic -> 401", not ok and "neplatny" in why, why)

    ok, why = shimauth.check_header("Basic dXNlcjpwYXNz")
    check("11. Basic se odmitne s jasnou zpravou",
          not ok and "Bearer" in why, why)

    # holy klic bez prefixu (nektere nastroje posilaji jen apiKey)
    ok, _ = shimauth.check_header("tajne123")
    check("12. holy klic bez 'Bearer' projde", ok)

    ok, _ = shimauth.check_request("/v1/models", "Bearer tajne123")
    check("13. /v1/models se spravnym klicem projde", ok)
    ok, _ = shimauth.check_request("/v1/models", None)
    check("14. /v1/models bez klice -> 401", not ok)

    # healthcheck musi projit VZDY (jinak ho nikdo neoveri)
    for p in ("/health", "/healthz", "/ping", "/v1/health"):
        ok, why = shimauth.check_request(p, None)
        check(f"15. {p} projde bez klice", ok, why)

    ok, why = shimauth.check_request("/v1/chat/completions", None)
    check("16. completions porad chranene", not ok, why)

    # query string se do porovnani cest neplete
    check("17. path_only odrizne query",
          shimauth.path_only("/v1/models?x=1") == "/v1/models")
    ok, _ = shimauth.check_request("/health?probe=1", None)
    check("18. /health s query projde bez klice", ok)
finally:
    restore()


# ------------------------------------------------------------------ bind host
restore = with_env(SHIM_API_KEY=None, SHIM_HOST="0.0.0.0", FRIDA_MCP_BIND=None)
try:
    check("19. SHIM_HOST prebije default", shimauth.bind_host() == "0.0.0.0",
          shimauth.bind_host())
    check("20. --host ma prednost pred SHIM_HOST (default arg)",
          shimauth.bind_host("10.0.0.1") == "0.0.0.0",
          "env vyhrava jen kdyz je --host nezadan")
finally:
    restore()

restore = with_env(SHIM_API_KEY=None, SHIM_HOST=None, FRIDA_MCP_BIND="192.168.1.5")
try:
    check("21. FRIDA_MCP_BIND funguje jako alias",
          shimauth.bind_host() == "192.168.1.5", shimauth.bind_host())
finally:
    restore()


# -------------------------------------------------- shimy skutecne pouzivaji
SRC_DS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "deepseek", "bridge", "openai_shim.py")
SRC_QW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "qwen", "bridge", "qwen_shim.py")
for name, path in (("deepseek", SRC_DS), ("qwen", SRC_QW)):
    src = open(path, encoding="utf-8").read()
    check(f"22. {name}: shim importuje shimauth", "import shimauth" in src)
    check(f"23. {name}: do_GET overuje klic", "def do_GET" in src and
          "self._authed()" in src)
    check(f"24. {name}: do_POST overuje klic",
          src.count("self._authed()") >= 2, src.count("self._authed()"))
    check(f"25. {name}: --host bere z env", "shimauth.bind_host()" in src)
    check(f"26. {name}: varuje pri siti bez klice",
          "SHIM_API_KEY" in src and "POZOR" in src)
    check(f"{27 if name == 'deepseek' else 28}. {name}: ma /health",
          "/health" in src)


print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — " + ", ".join(FAILED))
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
