#!/usr/bin/env python3
"""Testy: shim posila `temperature` VZDY jako normalni vychozi hodnotu.

Kontext (2026-11-03): appka `temperature` neposila, takze shim ho drive
vynechaval. Jenze uplne chybejici pole je pro server taky anomalie — bot,
ktery nezna sve vlastni parametry (a detekuje se to jako bot, ne jako
bypass). Proto shim posila `temperature` vzdy:

    request klienta > SHIM_TEMPERATURE / FRIDA_MCP_TEMPERATURE
                    > DEFAULT_TEMPERATURE (0.3)

Testy jsou offline: `urllib.request.urlopen` je nahrazeny fake odpovedi,
PoW je stubovany.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "deepseek"))

from bridge import deepseek_api as A  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}  {detail}")


class FakeResp:
    """Minimalni nahrada za urlopen odpoved (iterovatelna po radcich)."""

    def __init__(self, lines: list[str]):
        self._lines = [l.encode() for l in lines]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


CHUNK = json.dumps({"v": "ahoj"})


def run_once(**kw) -> dict:
    """Zavola _completion_once se stubly a vrati telo requestu, ktere odeslo."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        captured["headers"] = dict(req.headers)
        return FakeResp([f"data: {CHUNK}", ""])

    api = A.DeepSeekAPI("test-token")
    api.create_pow_challenge = lambda: {"challenge": "x"}
    api._solve = lambda ch: "1"
    api.pow_header = lambda ch, ans: "hdr"

    old = A.urllib.request.urlopen
    A.urllib.request.urlopen = fake_urlopen
    try:
        list(api._completion_once("sess-1", "prompt", **kw))
    finally:
        A.urllib.request.urlopen = old
    return captured["body"]


# ------------------------------------------------------------------ zaklad
body = run_once()
check("1. temperature se posila VZDY (i kdyz klient nic neda)",
      "temperature" in body, f"body={sorted(body)}")
check("2. vychozi hodnota je DEFAULT_TEMPERATURE",
      body.get("temperature") == A.DEFAULT_TEMPERATURE,
      f"{body.get('temperature')} != {A.DEFAULT_TEMPERATURE}")
check("3. DEFAULT_TEMPERATURE je 0.3", A.DEFAULT_TEMPERATURE == 0.3,
      str(A.DEFAULT_TEMPERATURE))


# -------------------------------------------------- teplota z requestu klienta
body = run_once(temperature=0.9)
check("4. teplota z requestu ma prednost", body.get("temperature") == 0.9,
      str(body.get("temperature")))

body = run_once(temperature=0)
check("5. teplota 0.0 se posle (neni to 'chybi')",
      body.get("temperature") == 0, str(body.get("temperature")))


# ------------------------------------------------------------ env promenne
def with_env(**kw):
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


restore = with_env(SHIM_TEMPERATURE="0.7", FRIDA_MCP_TEMPERATURE=None)
try:
    body = run_once()
    check("6. SHIM_TEMPERATURE prebije default",
          body.get("temperature") == 0.7, str(body.get("temperature")))
    body = run_once(temperature=0.1)
    check("7. request klienta prebije i SHIM_TEMPERATURE",
          body.get("temperature") == 0.1, str(body.get("temperature")))
finally:
    restore()

restore = with_env(SHIM_TEMPERATURE=None, FRIDA_MCP_TEMPERATURE="0.5")
try:
    body = run_once()
    check("8. FRIDA_MCP_TEMPERATURE funguje jako alias",
          body.get("temperature") == 0.5, str(body.get("temperature")))
finally:
    restore()

restore = with_env(SHIM_TEMPERATURE="nesmysl", FRIDA_MCP_TEMPERATURE=None)
try:
    body = run_once()
    check("9. nevalidni env se ignoruje -> default",
          body.get("temperature") == A.DEFAULT_TEMPERATURE,
          str(body.get("temperature")))
finally:
    restore()

restore = with_env(SHIM_TEMPERATURE="  ", FRIDA_MCP_TEMPERATURE="")
try:
    body = run_once()
    check("10. prazdny env se ignoruje -> default",
          body.get("temperature") == A.DEFAULT_TEMPERATURE,
          str(body.get("temperature")))
finally:
    restore()

restore = with_env(SHIM_TEMPERATURE="0.7", FRIDA_MCP_TEMPERATURE="0.5")
try:
    body = run_once()
    check("11. SHIM_TEMPERATURE ma prednost pred FRIDA_MCP_TEMPERATURE",
          body.get("temperature") == 0.7, str(body.get("temperature")))
finally:
    restore()


# ---------------------------- zbytek requestu zustava jako realna appka
body = run_once()
for key in ("chat_session_id", "parent_message_id", "prompt",
            "thinking_enabled", "search_enabled", "preempt"):
    check(f"12. request ma pole {key}", key in body, f"chybi {key}")
check("13. ref_file_ids je prazdny seznam",
      body.get("ref_file_ids") == [], str(body.get("ref_file_ids")))


print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — " + ", ".join(FAILED))
    raise SystemExit(1)
print("Vsechny testy prosly ✅")