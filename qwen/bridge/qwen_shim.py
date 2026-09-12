#!/usr/bin/env python3
"""qwen_shim — lokalni OpenAI-compatible endpoint nad Qwen chat API.

    pi -> http://127.0.0.1:13360/v1/chat/completions -> chat.qwen.ai

Spusteni:
    .venv/bin/python bridge/qwen_shim.py [--port 13360]

Konverzace: prvni request v "konverzaci" posle celou historii jako jeden prompt
do noveho chatu; dalsi requesty posilaji jen novou zpravu s parent_id (server
si drzi kontext). Klic konverzace = hash system promptu + prvni user zpravy.
"""

from __future__ import annotations

import argparse
import hashlib
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

from bridge.qwen_api import DEFAULT_MODEL, QwenAPI, QwenError
from common.convcache import ConvCache
from common.toolbridge import (StreamSplitter, build_prompt, parse_tool_calls,
                               to_openai_tool_calls, tool_names)

MODELS = ["qwen3.7-plus", "qwen3.8-max"]
_lock = threading.Lock()
_api: QwenAPI | None = None


def get_api() -> QwenAPI:
    global _api
    with _lock:
        if _api is None:
            _api = QwenAPI()
        return _api


def has_tool_context(messages: list[dict]) -> bool:
    return any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)


# cache konverzaci: jeden chat na konverzaci (viz common/convcache.py)
_convs = ConvCache()


def complete_stream(messages: list[dict], model: str, thinking: bool, tools: list | None):
    """Generator textovych chunku (zivi stream z Qwenu, nic se nebufferuje).

    POZOR: Qwen API bere jen JEDNU zpravu ("Invalid input too many messages")
    a v `chat_id` si kontext nedrzi, takze historii musime posilat cely jako
    jeden prompt. Co ale delame: **reuse chat_id** — jeden chat na konverzaci
    misto noveho chatu pro kazdou zpravu (to je napadne a je to presne to,
    podle ceho se da automatizace poznat).
    """
    api = get_api()
    session_id, _parent, _delta = _convs.lookup(messages)
    if not session_id:
        session_id = api.new_chat()
    prompt = build_prompt(messages, tools)
    got = False
    try:
        for ch in api.completion(session_id, prompt, model=model, thinking=thinking):
            if ch["phase"] in ("answer", ""):
                got = True
                yield ch["text"]
    except Exception:  # noqa: BLE001
        # chat uz nemusi existovat / vyprsel -> zaloz novy a zkus znovu
        _convs.drop(session_id)
        session_id = api.new_chat()
        for ch in api.completion(session_id, prompt, model=model, thinking=thinking):
            if ch["phase"] in ("answer", ""):
                got = True
                yield ch["text"]
    if got:
        _convs.bind(messages, session_id)


def complete(messages: list[dict], model: str, thinking: bool, tools: list | None) -> str:
    """Stateless: vzdy novy chat + cela historie v promptu.

    Pozor: NESMIME posilat jen posledni zpravu a spolehat na serverovou
    pamet chatu — klic konverzace by kolidoval (system prompt je u vsech
    pi session stejny) a do novych rozhovoru by prosakovala stara historie.
    """
    raw = "".join(complete_stream(messages, model, thinking, tools))
    if os.environ.get("SHIM_DUMP"):
        with open(os.environ["SHIM_DUMP"] + ".raw", "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": raw}, ensure_ascii=False) + "\n")
    return raw


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        if os.environ.get("SHIM_VERBOSE"):
            sys.stderr.write("[qwen-shim] " + (fmt % args) + "\n")

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        p = self.path.rstrip("/")
        if p.endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "created": int(time.time()),
                 "owned_by": "qwen-free(nativni)"} for m in MODELS]})
            return
        if p.endswith("/conversations") or p.endswith("/status"):
            self._json(200, {"ok": True, **_convs.stats()})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        # Reset konverzaci -> pristi request zalozi novy chat s celou historii.
        if self.path.rstrip("/").endswith("/reset"):
            st = _convs.stats()
            _convs.clear()
            print(f"[qwen-shim] reset konverzaci ({st['entries']} zruseno)",
                  file=sys.stderr)
            self._json(200, {"ok": True, "cleared": st["entries"],
                             "next": "full-context"})
            return
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

        model = body.get("model") or DEFAULT_MODEL
        if model not in MODELS:
            model = DEFAULT_MODEL
        thinking = bool(body.get("reasoning_effort"))
        messages = body.get("messages", [])
        tools = body.get("tools")
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(delta: dict, finish=None) -> None:
                obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
                       "model": model,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

            emit({"role": "assistant", "content": ""})
            sp = StreamSplitter(tool_names(tools))
            try:
                for piece_in in complete_stream(messages, model, thinking, tools):
                    piece = sp.feed(piece_in)
                    if piece:
                        emit({"content": piece})
                tail, calls = sp.finish()
                if tail:
                    emit({"content": tail})
                if calls:
                    for i, tc in enumerate(to_openai_tool_calls(calls)):
                        emit({"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                               "function": tc["function"]}]})
                    emit({}, "tool_calls")
                else:
                    emit({}, "stop")
            except QwenError as e:
                emit({"content": f"\n\n[chyba Qwen API: {e}]"})
                emit({}, "stop")
            except Exception as e:  # noqa: BLE001
                emit({"content": f"\n\n[chyba: {e}]"})
                emit({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        try:
            text = complete(messages, model, thinking, tools)
        except QwenError as e:
            self._json(500, {"error": {"message": str(e), "type": "qwen_error"}})
            return
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": {"message": str(e)}})
            return
        text, calls = parse_tool_calls(text, tool_names(tools))
        msg = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = to_openai_tool_calls(calls)
        self._json(200, {
            "id": cid, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": msg,
                         "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("QWEN_SHIM_PORT", "13360")))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[qwen-shim] qwen-free na http://{a.host}:{a.port}/v1  modely={','.join(MODELS)}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
