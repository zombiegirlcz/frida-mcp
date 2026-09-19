#!/usr/bin/env python3
"""qwen_api — primy klient Qwen chat API (chat.qwen.ai) pres anonymni/prihlasenou session.

Autentizace: cookie `token` (JWT) z WebView Cookies DB appky (cteni pres sudo).
WAF: hlavicky x-mini-wua / app_waf z appky (viz bridge/qwen_hdrd.py).

    ./bin/qwen "hlavni mesto Francie?"
    python3 bridge/qwen_api.py "test"          # rychly test
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, "secrets")
HDR_CACHE = os.path.join(SECRETS, "qwen_headers.json")

# repo root (kvuli common/) — zadna hardcoded cesta, balicek je prenositelny
_REPO = os.path.dirname(ROOT)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
from common import tokenauto as _auto  # noqa: E402
from common.qwen_token import pick_newest_token  # noqa: E402

TOKEN_CACHE = os.path.join(SECRETS, "qwen_token")
COOKIES_DB = os.environ.get(
    "QWEN_COOKIES_DB",
    "/mnt/data/data/ai.qwenlm.chat.android/app_webview/Default/Cookies",
)
BASE = "https://chat.qwen.ai"
DEFAULT_MODEL = os.environ.get("QWEN_MODEL", "qwen3.7-plus")


# ---------------------------------------------------------------- token / hlavicky

def _sudo_cat(path: str) -> bytes:
    """Precte soubor, ktery je citelny jen pro realny root (v guestu `sudo`)."""
    r = subprocess.run(["sudo", "cat", path], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"sudo cat {path} selhalo: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout


def read_token(refresh: bool = False) -> str:
    """Vytahne JWT z cookie `token` v Cookies DB appky (nejnovejsi = nejdelsi)."""
    if not refresh and os.path.exists(TOKEN_CACHE) and not _token_stale(TOKEN_CACHE):
        return open(TOKEN_CACHE, encoding="utf-8").read().strip()
    if not os.path.exists(TOKEN_CACHE):
        _auto.ensure_token(TOKEN_CACHE)
    try:
        with tempfile.NamedTemporaryFile(delete=False) as t:
            t.write(_sudo_cat(COOKIES_DB))
            tmp = t.name
        try:
            con = sqlite3.connect(tmp)
            # NEJNOVEJSI token (last_update_utc), NE podle delky —
            # vsechny JWT maji stejnou delku (~209 B), takze
            # `length DESC` vybiral nahodne a po prehlaseni uctu
            # se porad pouzival stary token.
            tok = pick_newest_token(con)
        finally:
            os.unlink(tmp)
        os.makedirs(SECRETS, exist_ok=True)
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            f.write(tok)
        os.chmod(TOKEN_CACHE, 0o600)
        return tok
    except Exception:
        if os.path.exists(TOKEN_CACHE):
            return open(TOKEN_CACHE, encoding="utf-8").read().strip()
        raise


def _token_stale(path: str, max_age: float = 900) -> bool:
    """Je cache tokenu zastarala? (default 15 min, driv 12 h.)

    12 h bylo prilis: po prehlaseni uctu v appce se novy token
    vubec neprecetl a shim jel na stare identite, dokud cache
    nevyprsela. 15 min je kompromis mezi cerstvosti a poctem
    sudo cteni.
    """
    try:
        return (time.time() - os.path.getmtime(path)) > max_age
    except OSError:
        return True


def load_headers() -> dict:
    """Hlavicky zachycene z appky (x-device-id, user-agent, cookie...).

    Historicky se myslelo, ze je nutne zachytavat `x-mini-wua` a `app_waf`
    (Aliyun WAF) pres fridu. Testy ale ukazaly, ze chat.qwen.ai tyto hlavicky
    VUBEC nevyzaduje — staci cookie s tokenem. Kdyz cache existuje, pouzijeme
    ji (nic neuskodi); kdyz ne, fungujeme s rozumnymi vychozimi hodnotami.
    """
    try:
        with open(HDR_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def device_id() -> str:
    """Stabilni (ale nahodny) identifikator zarizeni — Qwen ho nijak nevaliduje.

    Ulozi se do secrets/qwen_device_id, aby byl mezi behy konzistentni.
    """
    p = os.path.join(SECRETS, "qwen_device_id")
    try:
        v = open(p, encoding="utf-8").read().strip()
        if v:
            return v
    except OSError:
        pass
    v = "ai" + uuid.uuid4().hex
    try:
        os.makedirs(SECRETS, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(v)
        os.chmod(p, 0o600)
    except OSError:
        pass
    return v


DEFAULT_UA = ("Dalvik/2.1.0 (Linux; U; Android 13; M2101K6G Build/TKQ1.221013.002) "
              "AliApp(QWENCHAT/2.1.1) AppType/Release")


# ---------------------------------------------------------------- klient

class QwenError(RuntimeError):
    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


class QwenAPI:
    def __init__(self, token: str | None = None, headers: dict | None = None,
                 model: str = DEFAULT_MODEL):
        self.model = model
        self.token = token or read_token()
        self.h = headers or load_headers()

    # -- hlavicky -----------------------------------------------------------
    def _headers(self, stream: bool = False) -> dict:
        cookie = f"token={self.token}; x-ap=eu-central-1; " + (self.h.get("cookie", "") or "")
        hdrs = {
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": self.h.get("user-agent") or DEFAULT_UA,
            "Cookie": cookie,
            "x-device-id": self.h.get("x-device-id") or device_id(),
            "source": "app",
            "x-request-id": str(uuid.uuid4()),
        }
        # tyto dve Qwen aktualne nevyzaduje — posleme je, jen kdyz je mame
        if self.h.get("x-mini-wua"):
            hdrs["x-mini-wua"] = self.h["x-mini-wua"]
        if self.h.get("app_waf"):
            hdrs["app_waf"] = self.h["app_waf"]
        if stream:
            hdrs["Accept"] = "*/*,text/event-stream"
            hdrs["Cache-Control"] = "no-store"
        return hdrs

    # -- HTTP ---------------------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None, timeout: float = 90):
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers=self._headers(stream=True))
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise QwenError(f"HTTP {e.code}", e.read().decode(errors="replace")[:300]) from e
        ctype = resp.headers.get("Content-Type", "")
        if "event-stream" in ctype:
            return resp  # stream
        raw = resp.read()
        obj = json.loads(raw or b"{}")
        if not obj.get("success", True):
            d = obj.get("data") or {}
            raise QwenError(d.get("code", "?"), d.get("details") or d.get("template") or str(d))
        return obj

    # -- verejne ------------------------------------------------------------
    def new_chat(self, chat_mode: str = "guest") -> str:
        obj = self._request("POST", "/api/v2/chats/new", {"chat_mode": chat_mode, "project_id": ""})
        return obj["data"]["id"]

    def completion(self, chat_id: str, prompt: str, model: str | None = None,
                   thinking: bool = False, search: bool = False, timeout: float = 120):
        """Generator: {'phase': 'think'|'answer'|'', 'text': str}."""
        m = model or self.model
        ts = int(time.time())
        body = {
            "stream": True, "incremental_output": True, "chat_id": chat_id,
            "chat_mode": "guest", "model": m,
            "messages": [{
                "chat_type": "t2t", "content": prompt, "role": "user",
                # POZOR (overeno na zivem API):
                #   thinking_format="summary"                    -> jen 'answer'
                #   thinking_format="full" + auto_thinking=True   -> 'think' + 'answer'
                # Faze mysleni se pak jmenuje "think" (ne "thinking_summary").
                "feature_config": {
                    "output_schema": "phase", "thinking_enabled": bool(thinking),
                    "thinking_format": "full", "auto_thinking": bool(thinking),
                    "auto_search": bool(search),
                },
                "timestamp": ts, "sub_chat_type": "t2t", "models": [m],
                "fid": str(uuid.uuid4()), "user_action": "chat",
                "extra": {"meta": {"subChatType": "t2t"}},
            }],
            "timestamp": ts, "share_id": "", "origin_branch_message_id": "",
        }
        resp = self._request("POST", f"/api/v2/chat/completions?chat_id={chat_id}", body, timeout)
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for ch in obj.get("choices") or []:
                d = ch.get("delta") or {}
                txt = d.get("content") or ""
                if txt:
                    yield {"phase": d.get("phase") or "", "text": txt}

    def ask(self, prompt: str, model: str | None = None, thinking: bool = False) -> str:
        """Jeden dotaz do noveho chatu -> text odpovedi."""
        cid = self.new_chat()
        out = []
        for ch in self.completion(cid, prompt, model=model, thinking=thinking):
            if ch["phase"] in ("answer", ""):
                out.append(ch["text"])
        return "".join(out)


def main() -> int:
    q = QwenAPI()
    prompt = " ".join(sys.argv[1:]) or "Odpovez jednim slovem: hlavni mesto Francie?"
    t0 = time.time()
    text = q.ask(prompt)
    print(f"({time.time()-t0:.1f}s) {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
