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
                               tool_specs,
                               to_openai_tool_calls, tool_names)

# Overeno proti zivemu API (appka jich nabizi vic, ale API zna tyto):
#   qwen3.7-plus, qwen3.8-max, qwen3.7-max, qwen3.6-plus, qwen3.5-plus,
#   qwen3.5-omni-plus  (text+image+video; my posilame jen text)
# Neznamy model se tise prepne na DEFAULT_MODEL.
MODELS = [
    "qwen3.8-max",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3.5-omni-plus",
]
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


# Qwen bere CELOU historii v JEDNOM promptu. U velkych session to prekroci
# limit API, nebo to trva dele nez timeout -> pi jen visi na "working" a nic
# neprijde. Proto prompt orezavame (drzime system + nejnovejsi zpravy).
MAX_PROMPT_CHARS = int(os.environ.get("QWEN_MAX_PROMPT", "300000"))


def _cap_messages(messages: list[dict], limit: int | None = None):
    """Vrati (zpravy, zkraceno?). Drzi prvni system zpravu + nejnovejsi zpravy."""
    limit = limit or MAX_PROMPT_CHARS
    def _size(m):
        c = m.get("content")
        return len(c) if isinstance(c, str) else len(str(c or ""))
    total = sum(_size(m) for m in messages)
    if total <= limit:
        return messages, False
    sys_msg = [m for m in messages if m.get("role") == "system"][:1]
    rest = [m for m in messages if m.get("role") != "system"]
    keep, used = [], 0
    for m in reversed(rest):
        ln = _size(m)
        if keep and used + ln > limit:
            break
        keep.append(m)
        used += ln
    keep.reverse()
    return sys_msg + keep, True


def _wants_thinking(body: dict) -> bool:
    """Pozna, zda klient (pi) chce mysleni.

    pi posila pri `compat.thinkingFormat` pole `thinking` a/nebo
    `reasoning_effort`; ruzne verze se lisi, proto kontrolujeme vsechno.
    """
    th = body.get("thinking")
    if isinstance(th, dict) and th.get("type") == "enabled":
        return True
    if isinstance(th, bool) and th:
        return True
    if body.get("thinking_enabled") is True:
        return True
    r = body.get("reasoning")
    if isinstance(r, dict) and r.get("enabled") is True:
        return True
    eff = body.get("reasoning_effort")
    if isinstance(eff, str) and eff.lower() not in ("", "none", "off", "disabled"):
        return True
    return False


def complete_stream(messages: list[dict], model: str, thinking: bool, tools: list | None):
    """Generator textovych chunku (zivi stream z Qwenu, nic se nebufferuje).

    POZOR: Qwen API bere jen JEDNU zpravu ("Invalid input too many messages")
    a v `chat_id` si kontext nedrzi, takze historii musime posilat cely jako
    jeden prompt. Co ale delame: **reuse chat_id** — jeden chat na konverzaci
    misto noveho chatu pro kazdou zpravu (to je napadne a je to presne to,
    podle ceho se da automatizace poznat).
    """
    api = get_api()
    messages, trimmed = _cap_messages(messages)
    session_id, _parent, _delta = _convs.lookup(messages)
    fresh = not session_id
    if fresh:
        session_id = api.new_chat()
    prompt = build_prompt(messages, tools)
    print(f"[qwen-shim] chat {session_id[:8]}… (kontext: {len(prompt)} znaku, "
          f"{len(messages)} zprav{', ZKRACENO' if trimmed else ''})", file=sys.stderr)
    got = False

    def _run(sid):
        """Preklad faze Qwenu na (kind, text); mysleni jde jako 'think'."""
        for ch in api.completion(sid, prompt, model=model, thinking=thinking):
            txt = ch.get("text") or ""
            if not txt:
                continue
            if (ch.get("phase") or "") in ("think", "thinking_summary"):
                yield "think", txt
            else:
                yield "answer", txt

    try:
        for kind, txt in _run(session_id):
            got = True
            yield kind, txt
    except Exception as e:  # noqa: BLE001
        # chat uz nemusi existovat / vyprsel -> zaloz novy a zkus znovu
        print(f"[qwen-shim] stream selhal ({e}) -> novy chat", file=sys.stderr)
        _convs.drop(session_id)
        session_id = api.new_chat()
        for kind, txt in _run(session_id):
            got = True
            yield kind, txt
    if got:
        _convs.bind(messages, session_id)


def complete(messages: list[dict], model: str, thinking: bool, tools: list | None) -> str:
    """Stateless: vzdy novy chat + cela historie v promptu.

    Pozor: NESMIME posilat jen posledni zpravu a spolehat na serverovou
    pamet chatu — klic konverzace by kolidoval (system prompt je u vsech
    pi session stejny) a do novych rozhovoru by prosakovala stara historie.
    """
    raw = "".join(t for k, t in complete_stream(messages, model, thinking, tools)
                  if k == "answer")
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
        if self.path.rstrip("/").endswith("/session"):
            # Synchronizace s pi: extension posila ID pi session, podle ktereho
            # urcime, ktery chat pouzit (a ktery prezije restart).
            n = int(self.headers.get("Content-Length", "0") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:  # noqa: BLE001
                body = {}
            sid = body.get("id") or None
            reason = body.get("reason") or ""
            # fresh=True se posila pri kompakci: pi prestavi kontext (summary +
            # par poslednich zprav), takze stara session v appce uz nema smysl —
            # zahodime ji a dalsi tah posle zkompaktovany kontext do NOVEHO chatu.
            fresh = bool(body.get("fresh")) or reason == "new"
            _convs.set_current(sid, fresh=fresh)
            print(f"[qwen-shim] pi session {str(sid)[:8]}… ({reason or 'n/a'}"
                  f"{' , fresh' if fresh else ''})",
                  file=sys.stderr)
            self._json(200, {"ok": True, "session": sid, "reason": reason,
                             "fresh": fresh, **_convs.stats()})
            return
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
        # Debug dump: staci vytvorit /tmp/qwen_dump_on (bez restartu s env).
        _dump = os.environ.get("SHIM_DUMP")
        if not _dump and os.path.exists("/tmp/qwen_dump_on"):
            _dump = "/tmp/qwen_req.jsonl"
        if _dump:
            with open(_dump, "a", encoding="utf-8") as f:
                f.write(json.dumps(body, ensure_ascii=False) + "\n")

        model = body.get("model") or DEFAULT_MODEL
        if model not in MODELS:
            model = DEFAULT_MODEL
        thinking = _wants_thinking(body)
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

            sent_finish = False

            def emit(delta: dict, finish=None) -> None:
                # `sent_finish` je kvuli pojistce nize: pi jinak hlasi
                # "Stream ended without finish_reason" a model ztrati nit.
                nonlocal sent_finish
                obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
                       "model": model,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                if finish:
                    sent_finish = True

            emit({"role": "assistant", "content": ""})
            sp = StreamSplitter(tool_names(tools), tool_specs(tools))
            try:
                # complete_stream vraci (kind, text): 'think' = mysleni modelu,
                # 'answer' = odpoved. Mysleni posilame jako `reasoning_content`
                # (pi z toho udela thinking blok); splitter/cally jedou jen
                # na odpovedi.
                for kind, piece_in in complete_stream(messages, model, thinking, tools):
                    if kind == "think":
                        if piece_in:
                            emit({"reasoning_content": piece_in})
                        continue
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
            finally:
                # POJISTKA: kazda odpoved MUSI mit finish_reason. Kdyz se
                # nejaka cesta vynecha (nebo klient ztrati spojeni), pi hlasi
                # "Stream ended without finish_reason" a chova se zmatene.
                if not sent_finish:
                    try:
                        emit({}, "stop")
                    except Exception:  # noqa: BLE001
                        pass
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
        text, calls = parse_tool_calls(text, tool_names(tools), tool_specs(tools))
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
