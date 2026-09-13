#!/usr/bin/env python3
"""agent — daemon na telefonu, ktery vykonava MCP volani z relaye.

Bezi v proot guestu (tam, kde pi) a dela jedinou vec:
  1. long-polluje relay  (GET /agent/poll?wait=25)
  2. dostane volani nastroje (bash / read_file / write_file)
  3. vykona ho lokalne
  4. posle vysledek  (POST /agent/result)

Diky long-pollu dostane volani OKAMZITE (ne cekani na dalsi tick) a zaroven
tim drzi free instanci Renderu vzhuru (spindown po 15 min neaktivity).

Veskery provoz je ODCHOZI HTTPS -> telefon nepotrebuje verejnou IP.

    RELAY_URL=https://xxx.onrender.com RELAY_TOKEN=... python3 agent.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

URL = (os.environ.get("RELAY_URL") or "http://127.0.0.1:18080").rstrip("/")
TOKEN = os.environ.get("RELAY_TOKEN", "").strip()
POLL_WAIT = int(os.environ.get("RELAY_POLL_WAIT", "25"))
CMD_TIMEOUT = int(os.environ.get("AGENT_CMD_TIMEOUT", "120"))
MAX_OUT = int(os.environ.get("AGENT_MAX_OUT", "60000"))


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _get(path: str, timeout: int):
    req = urllib.request.Request(URL + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _post(path: str, obj: dict, timeout: int = 30):
    req = urllib.request.Request(
        URL + path, data=json.dumps(obj).encode(), headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def run_tool(name: str, args: dict) -> str:
    """Vykona nastroj lokalne a vrati textovy vystup."""
    try:
        if name == "bash":
            cmd = str(args.get("command") or "")
            if not cmd.strip():
                return "(prazdny prikaz)"
            p = subprocess.run(["bash", "-lc", cmd], capture_output=True,
                               text=True, timeout=CMD_TIMEOUT)
            out = (p.stdout or "") + (p.stderr or "")
            if not out.strip():
                out = f"(zadny vystup, exit={p.returncode})"
            elif p.returncode:
                out += f"\n(exit={p.returncode})"
            return out[:MAX_OUT]

        if name == "read_file":
            path = str(args.get("path") or "")
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()[:MAX_OUT]

        if name == "write_file":
            path = str(args.get("path") or "")
            content = str(args.get("content") or "")
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"zapsano {len(content)} B -> {path}"

        return f"(neznamy nastroj: {name})"
    except subprocess.TimeoutExpired:
        return f"(prikaz prekrocil limit {CMD_TIMEOUT} s)"
    except Exception as e:  # noqa: BLE001
        return f"(chyba: {type(e).__name__}: {e})"


def main() -> int:
    print(f"[agent] relay={URL} token={'ano' if TOKEN else 'ne'}", flush=True)
    fails = 0
    while True:
        try:
            job = _get(f"/agent/poll?wait={POLL_WAIT}", timeout=POLL_WAIT + 20)
            fails = 0
        except Exception as e:  # noqa: BLE001
            fails += 1
            wait = min(2 ** min(fails, 5), 30)
            print(f"[agent] poll selhal ({str(e)[:70]}) — cekam {wait}s",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
            continue

        cid = job.get("id")
        if not cid:
            continue                       # prazdno -> hned pollujeme znovu
        name = job.get("name") or ""
        args = job.get("arguments") or {}
        print(f"[agent] {name}: {str(args)[:120]}", flush=True)
        out = run_tool(name, args)
        try:
            _post("/agent/result", {"id": cid, "output": out})
        except Exception as e:  # noqa: BLE001
            print(f"[agent] odeslani vysledku selhalo: {str(e)[:90]}",
                  file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
