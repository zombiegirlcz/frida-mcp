#!/usr/bin/env python3
"""Regresni testy pro streamovani v qwen shimu.

Kazdy test odpovida konkretnimu selhani, ktere se opravdu stalo v logu
logs/qwen-free.log:

    [qwen-shim] stream selhal (The read operation timed out) -> novy chat
    ConnectionResetError: [Errno 104] Connection reset by peer
    BrokenPipeError: [Errno 32] Broken pipe

Priciny (a co testy hlidaji):

  1. `emit()` chybu nezachytaval -> ConnectionResetError/BrokenPipeError
     vyletly z `do_POST` a zabily vlakno requestu (a do logu hazely traceback).
     -> klient, ktery odpoji uprostred streamu, NESMI shodit handler.

  2. Mezi requestem a prvnim tokenem z Qwenu sel do pi nula bajtu. U velkych
     promptu Qwen dlouho mlci a pi spojeni ukonci ("Stream ended without
     finish_reason").
     -> behem ticha se posila SSE komentar `: ping`.

  3. Po timeoutu se opakoval PLNÝ megaprompt (ktery uz jednou vyprsel).
     -> retry pouziva kratsi kontext (RETRY_PROMPT_CHARS).

    python3 tests/test_qwen_stream.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qwen.bridge import qwen_shim  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}" + (f"\n     {detail}" if detail else ""))


# ------------------------------------------------------------- pomocnici

def _server(monkeypatch_stream):
    """Spusti shim na nahodnem portu s podvrzenym `complete_stream`."""
    old = qwen_shim.complete_stream
    qwen_shim.complete_stream = monkeypatch_stream
    srv = ThreadingHTTPServer(("127.0.0.1", 0), qwen_shim.Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, old


def _post_stream(port: int, body: dict) -> socket.socket:
    """Posle streaming request a vrati otevreny socket (cteni na volajicim)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    payload = json.dumps(body).encode()
    req = (b"POST /v1/chat/completions HTTP/1.1\r\n"
           b"Host: 127.0.0.1\r\n"
           b"Content-Type: application/json\r\n"
           b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
           b"Connection: close\r\n\r\n" + payload)
    s.sendall(req)
    return s


def _read_until(s: socket.socket, needle: bytes, max_bytes: int = 200_000,
                timeout: float = 10.0) -> bytes:
    s.settimeout(timeout)
    buf = b""
    while needle not in buf and len(buf) < max_bytes:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
    return buf


BODY = {"model": "qwen3.7-plus", "stream": True,
        "messages": [{"role": "user", "content": "ahoj"}]}


# ---------------------------------------------------------- 1) emit guard

def test_client_disconnect_does_not_kill_handler():
    """Klient odpoji uprostred streamu -> handler nesmi spadnout.

    Presne z logu: pi zavrel spojeni (ConnectionResetError) a shim pak pri
    dalsim zapisu vyhodil BrokenPipeError z `emit()` az ven z `do_POST`.
    """
    errors: list[BaseException] = []

    def fake_stream(messages, model, thinking, tools):
        # posle prvni chunk, pak pocka, aby klient stihl odpojit
        yield "answer", "prvni "
        time.sleep(0.4)
        # tenhle zapis uz pujde do mrtveho socketu — NESMI vyhodit
        yield "answer", "druhy"
        time.sleep(0.2)
        yield "answer", " treti"

    srv, old = _server(fake_stream)
    real_handle = qwen_shim.Handler.handle_one_request

    def spy(self):
        try:
            real_handle(self)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            raise

    qwen_shim.Handler.handle_one_request = spy
    try:
        port = srv.server_address[1]
        s = _post_stream(port, BODY)
        got = _read_until(s, b"prvni", timeout=5)
        check("1. stream: klient dostane prvni chunk", b"prvni" in got, repr(got[:120]))
        # hrube odpojeni uprostred streamu
        s.close()
        time.sleep(0.8)
        check("2. stream: odpojeni klienta neshodi handler",
              not errors, f"vyjimky={errors!r}")
    finally:
        qwen_shim.Handler.handle_one_request = real_handle
        qwen_shim.complete_stream = old
        srv.shutdown()
        srv.server_close()


def test_broken_pipe_marks_client_gone():
    """Kdyz uz klient neexistuje, dalsi zapisy se jen preskoci (nezapisuje se)."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "qwen", "bridge", "qwen_shim.py"), encoding="utf-8").read()
    check("3. stream: zapis chyta BrokenPipeError", "BrokenPipeError" in src)
    check("4. stream: zapis chyta ConnectionResetError", "ConnectionResetError" in src)
    check("5. stream: existuje priznak client_gone", "client_gone" in src)
    check("6. stream: smycka se pri client_gone ukonci",
          "if client_gone:" in src and "break" in src)


# --------------------------------------------------------------- 2) ping

def test_heartbeat_ping_when_upstream_silent():
    """Kdyz Qwen dlouho mlci, posle shim SSE komentar `: ping`.

    Bez toho pi spoji casovy limit a ukonci stream driv, nez prijde prvni
    token (v logu: "Stream ended without finish_reason").
    """
    old_hb = qwen_shim.HEARTBEAT_S
    qwen_shim.HEARTBEAT_S = 0.3  # aby test necekal 10 s

    def slow_stream(messages, model, thinking, tools):
        time.sleep(1.0)          # Qwen "premysli" — zadny token
        yield "answer", "hotovo"

    srv, old = _server(slow_stream)
    try:
        port = srv.server_address[1]
        s = _post_stream(port, BODY)
        got = _read_until(s, b"hotovo", timeout=5)
        check("7. ping: behem ticha dorazi SSE komentar : ping",
              b": ping" in got, repr(got[:200]))
        check("8. ping: po pingu dorazi i odpoved",
              b"hotovo" in got, repr(got[-200:]))
        s.close()
    finally:
        qwen_shim.HEARTBEAT_S = old_hb
        qwen_shim.complete_stream = old
        srv.shutdown()
        srv.server_close()


def test_heartbeat_configurable():
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "qwen", "bridge", "qwen_shim.py"), encoding="utf-8").read()
    check("9. ping: interval je konfigurovatelny (QWEN_HEARTBEAT)",
          "QWEN_HEARTBEAT" in src and "HEARTBEAT_S" in src)


# -------------------------------------------------------- 3) retry kontext

def test_retry_uses_shorter_context():
    """Po selhani se NESMI opakovat plny megaprompt — pouzije se kratsi."""
    src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "qwen", "bridge", "qwen_shim.py"), encoding="utf-8").read()
    check("10. retry: existuje RETRY_PROMPT_CHARS", "RETRY_PROMPT_CHARS" in src)
    check("11. retry: retry pouziva _cap_messages s kratsim limitem",
          "_cap_messages(messages, RETRY_PROMPT_CHARS)" in src)
    check("12. retry: retry stavi novy prompt (build_prompt)",
          "retry_prompt = build_prompt" in src)


def test_retry_shorter_than_main():
    """RETRY limit musi byt ostře mensi nez hlavni limit (jinak je to k nicemu)."""
    check("13. retry: RETRY_PROMPT_CHARS < MAX_PROMPT_CHARS",
          qwen_shim.RETRY_PROMPT_CHARS < qwen_shim.MAX_PROMPT_CHARS,
          f"retry={qwen_shim.RETRY_PROMPT_CHARS} max={qwen_shim.MAX_PROMPT_CHARS}")


def test_retry_really_happens_with_shorter_prompt():
    """Kdyz prvni pokus selze, shim posle DRUHY pozadavek s kratsim promptem."""
    import qwen.bridge.qwen_shim as shim  # noqa: PLC0415

    captured: list[str] = []

    class FakeAPI:
        def new_chat(self):
            return "fake-chat-id"

        def completion(self, sid, prompt, model=None, thinking=False):
            captured.append(prompt)
            if len(captured) == 1:
                raise RuntimeError("The read operation timed out")
            yield {"phase": "answer", "text": "opraveno"}

    # patch get_api (ne _api): get_api() jinak kvuli zmene mtime tokenu
    # vytvori novy QwenAPI a fake se zahodi
    old_get_api = shim.get_api
    shim.get_api = lambda: FakeAPI()

    msgs = [{"role": "developer", "content": "Jsi agent."}]
    msgs += [{"role": "user", "content": "x" * 100_000} for _ in range(10)]
    msgs.append({"role": "user", "content": "Posledni dotaz."})

    try:
        out = "".join(t for k, t in shim.complete_stream(msgs, "qwen3.7-plus", False, None)
                      if k == "answer")
    finally:
        shim.get_api = old_get_api

    check("14. retry: druhy pokus probehl", len(captured) == 2,
          f"volani={len(captured)}")
    if len(captured) == 2:
        check("15. retry: druhy prompt je KRATSI nez prvni",
              len(captured[1]) < len(captured[0]),
              f"prvni={len(captured[0])} druhy={len(captured[1])}")
        check("16. retry: druhy prompt respektuje RETRY_PROMPT_CHARS",
              len(captured[1]) <= qwen_shim.RETRY_PROMPT_CHARS * 1.5,
              f"druhy={len(captured[1])} limit={qwen_shim.RETRY_PROMPT_CHARS}")
    check("17. retry: odpoved dorazila", out == "opraveno", repr(out))


def main() -> int:
    test_client_disconnect_does_not_kill_handler()
    test_broken_pipe_marks_client_gone()
    test_heartbeat_ping_when_upstream_silent()
    test_heartbeat_configurable()
    test_retry_uses_shorter_context()
    test_retry_shorter_than_main()
    test_retry_really_happens_with_shorter_prompt()
    print()
    if FAILED:
        print(f"SELHALO: {len(FAILED)} — " + ", ".join(FAILED))
        return 1
    print("Vsechny testy prosly ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())