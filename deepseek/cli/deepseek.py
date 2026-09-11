#!/usr/bin/env python3
"""deepseek — přímý DeepSeek chat klient (token z MMKV, PoW přes fridu).

Použití:
    python3 bin/deepseek "otázka"       # jeden dotaz, nový session, vypíše odpověď
    python3 bin/deepseek                # interaktivní chat (REPL)
    python3 bin/deepseek --system "..." # se system promptem

Vyžaduje: běžící DeepSeek appku (kvůli PoW přes fridu) + frida-server na 27042.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.deepseek_api import DeepSeekAPI
from bridge.powd import PowHelper

TOKEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "secrets", "deepseek_token")
PKG = "com.deepseek.chat"


def ensure_app_running(timeout: float = 20.0) -> None:
    """Spustí DeepSeek, pokud neběží (am start + poll)."""
    try:
        PowHelper()._find_pid()
        return
    except RuntimeError:
        pass
    subprocess.run(f"ashell -c '/product/bin/su -c \"am start -n {PKG}/{PKG}.MainActivity\"'",
                   shell=True, capture_output=True, timeout=30)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            PowHelper()._find_pid()
            return
        except RuntimeError:
            time.sleep(0.5)
    print("⚠️ DeepSeek appku se nepodařilo spustit", file=sys.stderr)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="DeepSeek chat (přímý API klient)")
    ap.add_argument("prompt", nargs="*", help="jednorázový dotaz")
    ap.add_argument("--system", default="", help="system prompt")
    ap.add_argument("--no-pow", action="store_true", help="jen otestovat auth (bez PoW)")
    a = ap.parse_args()

    if not os.path.exists(TOKEN):
        print("chybí secrets/deepseek_token", file=sys.stderr)
        return 2

    tok = open(TOKEN, encoding="utf-8").read().strip()

    if a.no_pow:
        api = DeepSeekAPI(tok)
        print(api.users_current()["data"]["biz_data"]["id_profile"]["name"])
        return 0

    ensure_app_running()
    powh = PowHelper()
    powh.attach()
    api = DeepSeekAPI(tok, pow_helper=powh)

    session = api.create_session()
    sid = session["id"]

    prompt = " ".join(a.prompt).strip()
    if prompt:
        full = (a.system + "\n\n" + prompt).strip() if a.system else prompt
        print(api.completion(sid, full))
        return 0

    # interaktivní REPL
    print(f"[deepseek] session {sid[:8]}… (Ctrl-D pro konec)")
    if a.system:
        print("[deepseek] system:", a.system[:60])
    while True:
        try:
            line = input("👤> ")
        except EOFError:
            break
        if not line.strip():
            continue
        print("🤖", api.completion(sid, line))


if __name__ == "__main__":
    raise SystemExit(main())
