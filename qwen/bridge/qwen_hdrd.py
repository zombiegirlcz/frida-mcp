#!/usr/bin/env python3
"""qwen_hdrd — daemon, ktery z Qwen appky tahá hlavicky pro chat.qwen.ai.

Attachne se na proces "Qwen Studio", hookne libssl.so a z kazdeho odchoziho
requestu si vezme x-mini-wua / app_waf / x-device-id / User-Agent / Cookie.
Uklada je atomicky do secrets/qwen_headers.json, odkud je bere qwen_api.py.

    python3 bridge/qwen_hdrd.py            # bezi dal (daemon)
    python3 bridge/qwen_hdrd.py --once     # ceka na prvni kompletni capture a skonci
    python3 bridge/qwen_hdrd.py --once --timeout 30

Pozn.: x-mini-wua je sice per-request (meni se), ale pro replay staci hodnota
z posledniho requestu — server ji bere jako "podpis klienta", ne jako nonce.
app_waf se objevuje jen na /api/v2/chat/completions a je stabilni.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import frida

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "secrets", "qwen_headers.json")
AGENT = os.path.join(ROOT, "agent", "qwen_hdr.js")
PROC_NAME = os.environ.get("QWEN_PROC", "Qwen Studio")

WANT = ("x-mini-wua", "app_waf", "x-device-id", "user-agent", "cookie")


def log(*a):
    print("[hdrd]", *a, flush=True)


def write_cache(data: dict) -> None:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE)
    os.chmod(CACHE, 0o600)


def load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def find_pid(dev) -> int | None:
    for p in dev.enumerate_processes():
        if p.name == PROC_NAME:
            return p.pid
    return None


def run(once: bool, timeout: float) -> int:
    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    cache = load_cache()
    state = {"got_any": False, "last": time.time()}

    def on_msg(m, _data):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if p.get("type") == "error":
            log("agent error:", p.get("error"))
            return
        if p.get("type") != "req":
            return
        h = p.get("headers") or {}
        changed = False
        for k in WANT:
            v = h.get(k)
            if v and cache.get(k) != v:
                cache[k] = v
                changed = True
        if changed:
            cache["captured_at"] = int(time.time())
            cache["src_path"] = p.get("path")
            cache["src_method"] = p.get("method")
            write_cache(cache)
            state["got_any"] = True
            log(f"capture: {p.get('method')} {p.get('path')} "
                f"(wua={len(cache.get('x-mini-wua',''))}B, waf={len(cache.get('app_waf',''))}B)")
        if "app_waf" in h and h["app_waf"]:
            log("app_waf zachycen")

    pid = None
    session = None
    script = None

    while True:
        if session is None:
            pid = find_pid(dev)
            if pid is None:
                if once and time.time() - state["last"] > timeout:
                    log("timeout: proces nenalezen")
                    return 1
                time.sleep(2)
                continue
            try:
                session = dev.attach(pid)
                script = session.create_script(open(AGENT, encoding="utf-8").read())
                script.on("message", on_msg)
                script.load()
                log(f"attached pid={pid}, agent nalozen")
            except Exception as e:  # noqa: BLE001
                log("attach selhal:", e)
                session = None
                time.sleep(2)
                continue

        if once and state["got_any"] and cache.get("x-mini-wua") and cache.get("app_waf"):
            log("mam cerstve kompletni hlavicky -> koncim")
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass
            return 0
        if once and time.time() - state["last"] > timeout:
            log(f"timeout ({timeout}s) — nemam app_waf. cache: "
                f"wua={bool(cache.get('x-mini-wua'))} waf={bool(cache.get('app_waf'))}")
            return 1

        time.sleep(1)
        # detekce smrti procesu -> re-attach
        if find_pid(dev) is None:
            log("proces zmizel, re-attach")
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass
            session = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--timeout", type=float, default=120.0)
    a = ap.parse_args()
    try:
        return run(a.once, a.timeout)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
