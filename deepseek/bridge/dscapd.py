#!/usr/bin/env python3
"""dscapd — odposlech DeepSeek appky: co presne posila na server.

Hookuje `SSL_write` v procesu DeepSeek appky (agent/dscap.js) a uklada kazdy
odchozi HTTP request. U chat/completion je v tele cely prompt a vsechny
prepinace (thinking_enabled, search_enabled, ...), takze je videt rozdil mezi
"premyslenim" a beznym dotazem.

    python3 deepseek/bridge/dscapd.py            # attach + stream do konzole
    python3 deepseek/bridge/dscapd.py --jsonl deepseek/logs/capture.jsonl

Ctrl+C ukonci (appka zustane bezet).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(ROOT)))  # repo root

AGENT = os.path.join(ROOT, "agent", "dscap.js")
DEVICE = "127.0.0.1:27042"
PROC_NAME = "DeepSeek"  # frida vidi appku jako "DeepSeek"

# co nas zajima prednostne
IMPORTANT = ("/api/v0/chat/completion", "/chat/completion", "/completion")


def find_pid(dev):
    for p in dev.enumerate_processes():
        if p.name == PROC_NAME:
            return p.pid
    return None


def brief(payload: dict) -> str:
    path = payload.get("path", "")
    body = payload.get("body") or ""
    hdrs = payload.get("headers") or {}
    parts = [f"{payload.get('method')} {path}"]
    interesting = {k: v for k, v in hdrs.items()
                   if k.lower() in ("content-type", "x-ds-pow-response", "authorization",
                                    "accept", "x-request-id", "cookie")}
    for k, v in interesting.items():
        parts.append(f"    {k}: {v[:90]}{'…' if len(v) > 90 else ''}")
    if body:
        try:
            obj = json.loads(body)
            keep = {k: v for k, v in obj.items() if k != "prompt"}
            parts.append(f"    body: {json.dumps(keep, ensure_ascii=False)[:500]}")
            if "prompt" in obj:
                prompt = obj["prompt"]
                parts.append(f"    prompt ({len(prompt)} znaku) zacatek: "
                             f"{prompt[:200]!r}")
        except Exception:  # noqa: BLE001
            parts.append(f"    body: {body[:300]!r}")
    return "\n".join(parts)


def log(*a):
    print("[dscap]", *a, file=sys.stderr, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proc", default=PROC_NAME)
    ap.add_argument("--jsonl", default=os.path.join(ROOT, "logs", "capture.jsonl"))
    ap.add_argument("--all", action="store_true", help="ukazovat i nezajimave requesty")
    a = ap.parse_args()

    import frida  # az tady, at jde skript importovat i bez fridy

    dev = frida.get_device_manager().add_remote_device(DEVICE)
    os.makedirs(os.path.dirname(a.jsonl), exist_ok=True)
    fh = open(a.jsonl, "a", encoding="utf-8")

    agent_src = open(AGENT, encoding="utf-8").read()

    def on_msg(message, _data):
        if message.get("type") != "send":
            return
        p = message.get("payload") or {}
        if p.get("type") == "error":
            log(f"agent error: {p.get('error')}")
            return
        if p.get("type") == "hooked":
            log(f"  hook: {p.get('mod')} :: {p.get('label')} @ {p.get('addr')}")
            return
        if p.get("type") == "ready":
            log(f"agent nalozen, zahaknuto {p.get('count')} SSL_write")
            return
        if p.get("type") != "req":
            return
        path = p.get("path", "")
        rec = {"at": time.time(), **p}
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        interesting = any(k in path for k in IMPORTANT)
        if interesting or a.all:
            tag = "★" if interesting else " "
            print(f"\n{tag} [{time.strftime('%H:%M:%S')}] " + brief(p), flush=True)

    # DeepSeek appka casto umira (malo pameti) -> kdyz se pid zmeni, znovu se
    # pripojime, at neprideme o zavery testu.
    try:
        while True:
            pid = find_pid(dev)
            if pid is None:
                log("proces nebezi — cekam, az se appka spusti")
                time.sleep(2)
                continue
            log(f"attach na pid={pid} ({a.proc})")
            session = None
            try:
                session = dev.attach(pid)
                script = session.create_script(agent_src)
                script.on("message", on_msg)
                script.load()
                log(f"zapisuji do {a.jsonl}")
                while find_pid(dev) == pid:
                    time.sleep(2)
                log("proces zmizel — re-attach")
            except Exception as e:  # noqa: BLE001
                log(f"chyba: {e}")
                time.sleep(2)
            finally:
                try:
                    if session:
                        session.detach()
                except Exception:  # noqa: BLE001
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
