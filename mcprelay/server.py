#!/usr/bin/env python3
"""mcprelay — MCP relay, ktery dava Grok appce "ruce" v proot guestu.

Proc relay: Grok connector vyzaduje VEREJNE HTTPS a lokalne hostovane servery
odmita. Telefon ale nema verejnou IP. Relay to resi takhle:

    Grok --HTTPS--> relay (Render)  <--long-poll-- telefon (jen odchozi HTTPS)
                       ^                                |
                       |                                v
                  tools/call                     spusti bash v guestu

Tok jednoho volani:
  1. Grok posle tools/call na /mcp
  2. relay zavolani zaradi do fronty a ceka na vysledek (max WAIT sekund)
  3. telefon long-polluje /agent/poll -> dostane volani OKAMZITE
  4. telefon spusti prikaz a posle vysledek na /agent/result
  5. relay vrati vysledek Grokovi

Polling zaroven drzi free instanci Renderu vzhuru (spindown po 15 min).

Bez externich zavislosti (jen stdlib) -> maly Docker image.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("RELAY_TOKEN", "").strip()
MAX_WAIT = float(os.environ.get("RELAY_MAX_WAIT", "55"))
POLL_WAIT = float(os.environ.get("RELAY_POLL_WAIT", "25"))

_lock = threading.Lock()
_pending: dict[str, dict] = {}      # id -> {name, arguments}  (ceka na telefon)
_waiting: dict[str, threading.Event] = {}   # id -> Event (ceka MCP volani)
_done: dict[str, dict] = {}         # id -> {output}

TOOLS = [
    {
        "name": "bash",
        "description": ("Spusti shell prikaz v Linux prostredi na uzivatelove "
                        "telefone (proot guest, root). Pouzij pro praci se "
                        "soubory, build, testy, git, spousteni programu."),
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string",
                                       "description": "Shell prikaz ke spusteni"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Precte soubor z telefonu.",
        "inputSchema": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "Zapise obsah do souboru na telefonu.",
        "inputSchema": {"type": "object",
                        "properties": {"path": {"type": "string"},
                                       "content": {"type": "string"}},
                        "required": ["path", "content"]},
    },
]
PROTOCOL = "2025-06-18"


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, msg):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def dispatch(msg: dict):
    """Zpracuje jeden JSON-RPC pozadavek MCP."""
    rid = msg.get("id")
    method = msg.get("method") or ""
    params = msg.get("params") or {}

    if method == "initialize":
        return _ok(rid, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mcprelay", "version": "1.0.0"},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return _err(rid, -32602, f"neznamy nastroj: {name}")
        out = run_on_phone(name, args)
        if out is None:
            return _err(rid, -32000, "telefon neodpovedel vcas (je daemon spusteny?)")
        return _ok(rid, {"content": [{"type": "text", "text": out}]})
    return _err(rid, -32601, f"neznama metoda: {method}")


def run_on_phone(name: str, args: dict) -> str | None:
    """Zaradi volani a pocka, az ho telefon vykona."""
    cid = uuid.uuid4().hex
    ev = threading.Event()
    with _lock:
        # POZOR: Event musi byt v SAMOSTATNEM registru. /agent/poll totiz
        # volani z _pending vybira (aby se neposlalo 2x), takze by se Event
        # ztratil a /agent/result by nemel co probudit -> MCP vratilo null.
        _pending[cid] = {"name": name, "arguments": args, "at": time.time()}
        _waiting[cid] = ev
    try:
        if ev.wait(MAX_WAIT):
            with _lock:
                res = _done.pop(cid, None)
            return (res or {}).get("output")
        return None
    finally:
        with _lock:
            _pending.pop(cid, None)
            _waiting.pop(cid, None)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # noqa: A003
        if os.environ.get("RELAY_VERBOSE"):
            print("[relay] " + (fmt % a), flush=True)

    def _send(self, code: int, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj,
                                                             ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self) -> bool:
        if not TOKEN:
            return True
        h = self.headers.get("Authorization") or ""
        return h == f"Bearer {TOKEN}"

    def do_GET(self):  # noqa: N802
        p = self.path.split("?")[0].rstrip("/")
        if p in ("/health", ""):
            self._send(200, {"ok": True, "pending": len(_pending),
                             "tools": len(TOOLS)})
            return
        if p == "/agent/poll":
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            self._poll()
            return
        self._send(404, {"error": "not found"})

    def _poll(self):
        """Long-poll: vrati volani hned, jak prijde (nebo po POLL_WAIT)."""
        deadline = time.time() + POLL_WAIT
        while time.time() < deadline:
            with _lock:
                if _pending:
                    cid = next(iter(_pending))
                    item = _pending.pop(cid)   # uz se neposle znovu
                    self._send(200, {"id": cid, "name": item["name"],
                                     "arguments": item["arguments"]})
                    return
            time.sleep(0.4)
        self._send(200, {})          # prazdno -> daemon polluje znovu

    def do_POST(self):  # noqa: N802
        p = self.path.split("?")[0].rstrip("/")
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            self._send(400, {"error": "bad json"})
            return

        if p == "/agent/result":
            if not self._auth_ok():
                self._send(401, {"error": "unauthorized"})
                return
            cid = body.get("id")
            with _lock:
                _done[cid] = {"output": body.get("output", "")}
                ev = _waiting.get(cid)
            if ev:
                ev.set()
            self._send(200, {"ok": True, "id": cid})
            return

        if p in ("/mcp", "/"):
            # MCP streamable HTTP: odpovidame jednim JSON objektem (ne SSE).
            if isinstance(body, list):
                out = [r for r in (dispatch(m) for m in body) if r is not None]
                self._send(200, out)
                return
            r = dispatch(body)
            if r is None:
                self._send(202, {})
                return
            self._send(200, r)
            return
        self._send(404, {"error": "not found"})


def main() -> int:
    port = int(os.environ.get("PORT", "10000"))
    print(f"[relay] start na 0.0.0.0:{port} (token={'ano' if TOKEN else 'ne'})",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
