#!/usr/bin/env python3
"""gemini_fixd — trvalý daemon, který drží v Google appce hook proti MIUI crashu.

Problem: Gemini (com.google.android.apps.bard) je jen shell, skutečné UI běží
v com.google.android.googlequicksearchbox. Při startu inicializuje CameraX →
zavolá android.media.CamcorderProfile.hasProfile(cameraId, quality) → JNI →
MediaProfiles::hasCamcorderProfile v ROM libmedia.so **null-deref** (bug MIUI 14
na Redmi Note 10 / sweet) → SIGSEGV v threadu CameraX-core_ca.

Reseni: hooknout Java metodu a vrátit false, takže se nativní volání vůbec
neprovede. Hook se volá jen párkrát při startu kamery → zanedbatelná režie.

    python3 bridge/gemini_fixd.py            # daemon (drží se, re-attach)
    python3 bridge/gemini_fixd.py --once     # jen ověří, že to jde
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import frida

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(ROOT, "agent", "nocamcrash.js")
PIDFILE = os.path.join(ROOT, "logs", "gemini-fixd.pid")
PROC = os.environ.get("GEMINI_PROC", "com.google.android.googlequicksearchbox:search")


def log(*a):
    print(f"[gemini-fixd {time.strftime('%H:%M:%S')}]", *a, flush=True)


def wake_google_app() -> None:
    """Vzbudí Google appku, aby vznikl :search proces (bez toho se hook nema kam dat)."""
    import subprocess
    cmd = ("am start -n com.google.android.googlequicksearchbox/"
           "com.google.android.googlequicksearchbox.SearchActivity")
    try:
        subprocess.run(["ashell", "-c", f'/product/bin/su -c "{cmd}"'],
                       capture_output=True, timeout=40)
    except Exception as e:  # noqa: BLE001
        log("wake selhal:", e)


def find_pid(dev) -> int | None:
    """PID ciloveho procesu pres fridu (guest /proc host procesy NEVIDI)."""
    try:
        for p in dev.enumerate_processes():
            if p.name == PROC:
                return p.pid
    except Exception:  # noqa: BLE001
        pass
    return None
    return None


def run(once: bool) -> int:
    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    session = None
    script = None
    pid = None
    hits = 0

    def on_msg(m, _data):
        nonlocal hits
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if p.get("hook") == "hasProfile":
            hits += 1
            if hits <= 4:
                log(f"zachyceno hasProfile(cameraId={p.get('cameraId')}, "
                    f"quality={p.get('quality')}) -> false")
        elif p.get("ready"):
            log("hook aktivni")
        elif p.get("error"):
            log("agent chyba:", p["error"])

    if once:
        pid = find_pid(dev)
        if pid is None:
            log(f"proces {PROC} neběží")
            return 1
        s = dev.attach(pid)
        sc = s.create_script(open(AGENT, encoding="utf-8").read())
        sc.on("message", on_msg)
        sc.load()
        time.sleep(4)
        log(f"OK — hook šel naložit do pid {pid}")
        s.detach()
        return 0

    log(f"start, cilim na {PROC}")
    log(f"pozn.: :search proces zije i minuty nez Gemini otevre UI — staci se pripojit vcas")
    while True:
        try:
            if session is None:
                pid = find_pid(dev)
                if pid is None:
                    time.sleep(1.0)
                    continue
                session = dev.attach(pid)
                script = session.create_script(open(AGENT, encoding="utf-8").read())
                script.on("message", on_msg)
                script.load()
                log(f"attached pid={pid}, hook naložen — Gemini ted muze otevrit UI")
            else:
                # detekce smrti procesu (crash) -> re-attach
                if find_pid(dev) != pid:
                    log("proces zmizel (crash/restart) -> re-attach")
                    try:
                        session.detach()
                    except Exception:  # noqa: BLE001
                        pass
                    session = None
                    script = None
                    pid = None
                    continue
            time.sleep(1.0)
        except frida.ProcessNotFoundError:
            session = None
            time.sleep(1)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001
            log("chyba:", e)
            session = None
            time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
    if not a.once:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    try:
        return run(a.once)
    finally:
        if not a.once and os.path.exists(PIDFILE):
            os.unlink(PIDFILE)


if __name__ == "__main__":
    raise SystemExit(main())
