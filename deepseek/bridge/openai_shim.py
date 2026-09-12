#!/usr/bin/env python3
"""openai_shim — lokální OpenAI-compatible endpoint nad DeepSeek API (PoW přes fridu).

pi → http://127.0.0.1:13350/v1/chat/completions → DeepSeek /api/v0/chat/completion

Spuštění:
    .venv/bin/python bridge/openai_shim.py [--port 13350]

Vyžaduje: běžící DeepSeek appku + frida-server (PoW) a secrets/deepseek_token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # <root>/<app>
_REPO = os.path.dirname(_PKG)                                       # <root>
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bridge.deepseek_api import DeepSeekAPI
from common.tokenauto import ensure_token
from common.toolbridge import (TOOLS_REMINDER, StreamSplitter, build_prompt,
                               parse_tool_calls, to_openai_tool_calls, tool_names)
from common.convcache import ConvCache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.path.join(ROOT, "secrets", "deepseek_token")
MODEL = os.environ.get("DEEPSEEK_FREE_MODEL", "deepseek-chat")

_lock = threading.Lock()
_api: DeepSeekAPI | None = None
_pow = None


def get_api() -> DeepSeekAPI:
    """Lazily vytvoří klienta.

    PoW se řeší NATIVNĚ (bridge/pow_native.py — DeepSeekHashV1 v C), takže
    frida ani běžící DeepSeek appka nejsou potřeba. Frida helper se zkouší
    jen jako záložní cesta, když nativní backend není k dispozici (chybí gcc).
    """
    global _api, _pow
    with _lock:
        if _api is None:
            if not ensure_token(TOKEN):
                raise RuntimeError(
                    "chybí DeepSeek token a nejde vytáhnout (je appka nainstalovaná "
                    "a přihlášená? zkus: python3 scripts/ensure_tokens.py)")
            tok = open(TOKEN, encoding="utf-8").read().strip()
            _api = DeepSeekAPI(tok, pow_helper=None)
        if _pow is None and _want_frida_fallback():
            try:
                from bridge.powd import PowHelper
                p = PowHelper()
                p.attach()
                _pow = p
                _api.pow = p          # doplnit do už vytvořeného klienta
                print("[shim] frida PoW helper připojen (záložní cesta)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[shim] frida attach selhal: {e}", file=sys.stderr)
        return _api


def _want_frida_fallback() -> bool:
    """Fridu zkoušet jen když nativní PoW nejde (nebo když si to vynutí env)."""
    if os.environ.get("ELF_POW_FRIDA"):
        return True
    try:
        from bridge import pow_native
        return pow_native.lib() is None
    except Exception:  # noqa: BLE001
        return True


def _ensure_attached() -> None:
    """Overi, ze PoW helper ZIJE (ne jen ze proces existuje); kdyz ne, znovu attachne."""
    global _pow
    if _pow is not None and _pow.alive():
        return
    if _pow is not None:
        print("[shim] PoW skript je mrtvy -> re-attach", file=sys.stderr)
        _pow = None
    get_api()


def flatten(messages: list[dict]) -> str:
    """OpenAI messages → jeden prompt (DeepSeek /completion bere jen prompt).

    Historie se posílá celá, protože každý request jde do nové session.
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # OpenAI content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "system":
            parts.append(f"[SYSTEM]\n{content}")
        elif role == "assistant":
            parts.append(f"[ASSISTANT]\n{content}")
        else:
            parts.append(f"[USER]\n{content}")
    return "\n\n".join(parts)


# Cache konverzaci: drzi jeden chat pro celou konverzaci a posila jen delta.
# (Puvodne se pro kazdou zpravu vytvarel NOVY chat a posilala se cely historie
#  znovu — to je napadne a pomale; server si konverzaci drzi sam.)
_convs = ConvCache()


def _delta_prompt(delta: list[dict], has_tools: bool):
    """Prompt pro dalsi tah v uz existujicim chatu.

    Server uz zna vsechno predchozi, takze posilame jen nove zpravy. Kdyz
    jsou k dispozici nastroje, pripomeneme, ze plati (bez opakovani schemat).
    """
    if has_tools:
        head = [{"role": "system", "content": TOOLS_REMINDER}]
    else:
        head = []
    return build_prompt(head + delta, None, trailer=True)


def complete_stream(body: dict):
    """Generator textovych chunku — zivi stream z DeepSeeku (nic se nebufferuje).

    Konverzace se drzi v jednom chatu (viz common/convcache.py): server si
    pamatuje predchozi tahy, takze posilame jen novou zpravu.
    """
    _ensure_attached()
    api = get_api()
    messages = body.get("messages", [])
    tools = body.get("tools")
    thinking = bool(body.get("reasoning_effort"))

    session_id, parent_id, delta = _convs.lookup(messages)
    if session_id:
        prompt = _delta_prompt(delta, bool(tools))
        print(f"[shim] delta tah v chatu {session_id[:8]}… "
              f"({len(delta)} novych zprav, {len(prompt)} znaku)", file=sys.stderr)
        try:
            yield from api.completion_stream(session_id, prompt,
                                             parent_message_id=parent_id,
                                             thinking_enabled=thinking)
            _convs.bind(messages, session_id, api.last_response_message_id)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[shim] delta tah selhal ({e}) -> zkusim cely novy chat",
                  file=sys.stderr)
            _convs.drop(session_id)

    # novy chat (nebo fallback): cela historie jako jeden prompt
    prompt = build_prompt(messages, tools)
    session = api.create_session()
    sid = session["id"]
    yield from api.completion_stream(sid, prompt, thinking_enabled=thinking)
    _convs.bind(messages, sid, api.last_response_message_id)


def complete(body: dict) -> str:
    return "".join(complete_stream(body))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        if os.environ.get("SHIM_VERBOSE"):
            sys.stderr.write("[shim] " + (fmt % args) + "\n")

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "created": int(time.time()),
                 "owned_by": "deepseek-free(frida)"}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": f"unsupported path {self.path}"}})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:  # noqa: BLE001
            self._json(400, {"error": {"message": f"bad json: {e}"}})
            return
        if os.environ.get("SHIM_DUMP"):
            with open(os.environ["SHIM_DUMP"], "a", encoding="utf-8") as f:
                f.write(json.dumps(body, ensure_ascii=False) + "\n")
        names = tool_names(body.get("tools"))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def chunk(delta: dict, finish=None) -> None:
                obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
                       "model": body.get("model", MODEL),
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

            chunk({"role": "assistant", "content": ""})
            sp = StreamSplitter(names)
            try:
                for piece_in in complete_stream(body):
                    piece = sp.feed(piece_in)
                    if piece:
                        chunk({"content": piece})
                tail, calls = sp.finish()
                if tail:
                    chunk({"content": tail})
                if calls:
                    for i, tc in enumerate(to_openai_tool_calls(calls)):
                        chunk({"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                               "function": tc["function"]}]})
                    chunk({}, "tool_calls")
                else:
                    chunk({}, "stop")
            except Exception as e:  # noqa: BLE001
                chunk({"content": f"\n\n[chyba: {e}]"})
                chunk({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        try:
            text = complete(body)
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": {"message": str(e), "type": "deepseek_free_error"}})
            return
        text, calls = parse_tool_calls(text, names)
        if os.environ.get("SHIM_DUMP"):
            with open(os.environ["SHIM_DUMP"] + ".raw", "a", encoding="utf-8") as f:
                f.write(json.dumps({"parsed_calls": len(calls), "text": text},
                                   ensure_ascii=False) + "\n")

        msg = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = to_openai_tool_calls(calls)
        self._json(200, {
            "id": cid, "object": "chat.completion", "created": created,
            "model": body.get("model", MODEL),
            "choices": [{"index": 0, "message": msg,
                         "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("SHIM_PORT", "13350")))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    # Token se NEVYŽADUJE hned: shim se musí nastartovat i bez něj, aby se pak
    # na první request sám vytáhl z appky (get_api -> ensure_token). Tvrdý
    # exit tady znamenal, že po `pi update --all` (který smaže secrets/)
    # shim vůbec nenaběhl a pi hlásil jen "Connection error."
    if ensure_token(TOKEN):
        print("[shim] token je k dispozici", flush=True)
    else:
        print("[shim] POZOR: token zatím chybí — vytáhnu ho při prvním requestu "
              "(je DeepSeek appka nainstalovaná a přihlášená?)", file=sys.stderr,
              flush=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[shim] deepseek-free (nativni PoW, bez fridy) na http://{a.host}:{a.port}/v1  model={MODEL}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
