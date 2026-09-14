#!/usr/bin/env python3
"""mscapd — odposlech Mistral appky (ai.mistral.chat).

Appka je React Native/Expo, komunikuje pres tRPC na chat.mistral.ai:
    /api/trpc/...        (chat/work mody)
    /api/code-trpc/...   (code mod)
Auth: Ory session token (`ory_st_...`) v MMKV -> hlavicka/cookie.

Hookuje SSL_write napric moduly (agent/mscap.js) a uklada kazdy odchozi
HTTP request vcetne tela (tam je cely prompt, mod, model, tools, mcp).

    python3 mistral/bridge/mscapd.py                 # stream do konzole
    python3 mistral/bridge/mscapd.py --jsonl PATH    # navic JSONL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(ROOT, "agent", "mscap.js")
DEVICE = "127.0.0.1:27042"
PROC_NAME = "Vibe"          # frida vidi appku ai.mistral.chat jako "Vibe"


def find_pid(dev):
    for p in dev.enumerate_processes():
        if p.name == PROC_NAME:
            return p.pid
    return None


def brief(p: dict) -> str:
    h = p.get("headers") or {}
    auth = ""
    for k, v in h.items():
        lk = k.lower()
        if lk in ("authorization", "cookie"):
            auth = f"  {k}={str(v)[:40]}…"
    body = p.get("body") or ""
    return (f"{p.get('method')} {p.get('url')}\n"
            f"    [{p.get('mod')}::{p.get('sym')}]{auth}\n"
            f"    body({len(body)}): {body[:400]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=None)
    ap.add_argument("--seconds", type=int, default=0, help="0 = do Ctrl+C")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    try:
        import frida
    except ImportError:
        print("chybí frida (pip install frida)", file=sys.stderr)
        return 2

    dev = frida.get_device_manager().add_remote_device(DEVICE)
    while True:
        pid = find_pid(dev)
        if pid:
            break
        print("[mscapd] appka neběží — čekám (spusť ai.mistral.chat)", file=sys.stderr)
        time.sleep(3)

    # attach umi selhat (ProcessNotRespondingError), kdyz je appka zaneprazdnena
    # (treba hned po startu). Zkusime to nekolikrat s odstupem.
    sess = None
    for attempt in range(6):
        pid = find_pid(dev) or pid
        try:
            print(f"[mscapd] attach na {PROC_NAME} (pid {pid}) pokus {attempt+1}",
                  file=sys.stderr)
            sess = dev.attach(pid)
            break
        except Exception as e:  # noqa: BLE001
            print(f"[mscapd] attach selhal ({str(e)[:80]}) — zkusim za 4 s",
                  file=sys.stderr)
            time.sleep(4)
    if sess is None:
        print("[mscapd] attach se nepovedl (appka zaneprazdnena?)", file=sys.stderr)
        return 1
    with open(AGENT, encoding="utf-8") as f:
        js = f.read()
    script = sess.create_script(js)
    out = None
    if a.jsonl:
        os.makedirs(os.path.dirname(a.jsonl), exist_ok=True)
        out = open(a.jsonl, "a", encoding="utf-8")

    def on_msg(msg, data):
        if msg.get("type") != "send":
            print(f"[mscapd] {msg}", file=sys.stderr)
            return
        p = msg["payload"]
        if "info" in p:
            print(f"[mscapd] {p['info']}", file=sys.stderr)
            return
        print(brief(p), flush=True)
        if out:
            out.write(json.dumps(p, ensure_ascii=False) + "\n")
            out.flush()

    script.on("message", on_msg)
    script.load()
    print("[mscapd] poslouchám (v appce teď něco pošli)...", file=sys.stderr)
    t0 = time.time()
    try:
        while True:
            time.sleep(1)
            if a.seconds and time.time() - t0 > a.seconds:
                break
    except KeyboardInterrupt:
        pass
    sess.detach()
    if out:
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
