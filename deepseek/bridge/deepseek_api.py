"""deepseek_api — přímý klient DeepSeek API (bez fridy pro HTTP, PoW přes powd).

Použití:
    from bridge.deepseek_api import DeepSeekAPI
    api = DeepSeekAPI(token_path="secrets/deepseek_token", pow_helper=powh)
    sid = api.create_session()
    for chunk in api.completion(sid, "Ahoj"):
        print(chunk, end="")
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request

BASE = "https://chat.deepseek.com"

# Biznisove kody, ktere znamenaji "neplatny/neprihlaseny token" -> staci
# obnovit token a zkusit znovu (nikoliv trvat error).
AUTH_BIZ_CODES = {40001, 40004, 40008, 40009, 40011, 40013, 40301, 40302}

# Prodlevy mezi pokusy, kdyz server vrati rate limit ("Příliš časté zprávy").
# Agent dela hodne dotazu rychle za sebou, takze se to deje casto.
RATE_LIMIT_DELAYS = (3.0, 8.0, 20.0)


def _hint_error(obj: dict) -> "DeepSeekError | None":
    """Rozpozna chybove hlaseni v SSE.

    DeepSeek posila chyby jako `event: hint` s daty:
        {"type":"error", "content":"Příliš časté zprávy…",
         "finish_reason":"rate_limit_reached"}
    Drive se to ignorovalo -> `got` zustalo False a shim to hlasil jako
    "prazdna odpoved (zadne data:)", coz je matouci a hlavne se to nedalo
    rozlisit od neplatneho tokenu.
    """
    if obj.get("type") != "error":
        return None
    msg = str(obj.get("content") or obj.get("msg") or "").strip()
    fr = str(obj.get("finish_reason") or "")
    low = (msg + " " + fr).lower()
    is_rate = ("rate_limit" in low or "prilis" in low or "příliš" in low
               or "too many" in low or "zkuste to znovu" in low)
    text = msg or fr or "neznamy error"
    if is_rate:
        return DeepSeekError(
            f"chat/completion: RATE LIMIT — {text} "
            f"(agent poslal příliš mnoho dotazů rychle po sobě)",
            retry=True, rate_limited=True)
    return DeepSeekError(f"chat/completion: {text}", retry=False)


class DeepSeekError(RuntimeError):
    """Chyba od DeepSeek API (neplatny token, rate limit, invalid message...).

    Rozsirene o `biz_code` a `retry` (zda staci obnovit token a zkusit znovu).
    """

    def __init__(self, msg: str, biz_code: int | None = None, retry: bool = False,
                 rate_limited: bool = False):
        super().__init__(msg)
        self.biz_code = biz_code
        self.retry = retry
        # rate limit se pozna podle finish_reason=rate_limit_reached
        self.rate_limited = rate_limited

    @staticmethod
    def from_resp(resp: dict, what: str) -> "DeepSeekError | None":
        """Pokud odpoved obsahuje chybu, vratime DeepSeekError, jinak None."""
        data = resp.get("data")
        biz_code = data.get("biz_code") if isinstance(data, dict) else None
        biz_msg = data.get("biz_msg") if isinstance(data, dict) else None
        code = resp.get("code")
        if (biz_code not in (None, 0)) or (code not in (None, 0)):
            msg = biz_msg or resp.get("msg") or f"code={code}"
            # kod chyby byva bud v code (top-level) nebo v data.biz_code
            err_code = biz_code if biz_code not in (None, 0) else code
            low = str(msg).lower()
            # auth chyby: staci obnovit token a zkusit znovu
            retry = (
                err_code in AUTH_BIZ_CODES
                or str(err_code).startswith("4000")
                or any(k in low for k in ("invalid token", "authorization failed",
                                          "unauthorized", "not logged", "login"))
            )
            return DeepSeekError(f"{what}: {msg}", biz_code=err_code, retry=retry)
        return None


# Hlavičky odchycené z reálné appky (DeepSeek/2.5.0)
BASE_HEADERS = {
    "User-Agent": "DeepSeek/2.5.0 Android/33",
    "x-client-platform": "android",
    "x-client-version": "2.5.0",
    "x-client-locale": "cs",
    "x-client-bundle-id": "com.deepseek.chat",
    "x-client-timezone-offset": "7200",
    "x-device-model": "M2101K6G",
    # x-device-id je per-instalace — nastaví se v __init__ (volitelně)
}


class DeepSeekAPI:
    def __init__(self, token: str, pow_helper=None, device_id: str | None = None):
        self.token = token.strip()
        # typ posledniho fragmentu — DeepSeek streamuje mysleni (THINK) i odpoved
        # (RESPONSE) do stejneho chunku, rozlisi se podle typu fragmentu
        self._last_frag_type = "RESPONSE"
        self.pow = pow_helper
        self.headers = dict(BASE_HEADERS)
        if device_id:
            self.headers["x-device-id"] = device_id

    def _req(self, method: str, path: str, body: dict | None = None,
             extra_headers: dict | None = None) -> dict:
        h = dict(self.headers)
        h["Authorization"] = "Bearer " + self.token
        if body is not None:
            h["Content-Type"] = "application/json"
        if extra_headers:
            h.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    # ---------------------------------------------------------------- API

    def users_current(self) -> dict:
        return self._req("GET", "/api/v0/users/current")

    def create_session(self) -> dict:
        r = self._req("POST", "/api/v0/chat_session/create", {})
        err = DeepSeekError.from_resp(r, "chat_session/create")
        if err:
            raise err  # chyba od API (napr. neplatny token)
        data = r.get("data")
        # `data` muze byt None pri chybe — osetrit, jinak .get() spadne
        if not isinstance(data, dict):
            raise DeepSeekError("chat_session/create: neocekavana odpoved: " + str(r)[:160])
        bd = data.get("biz_data") or r
        chat = bd.get("chat_session") if isinstance(bd, dict) else None
        if not chat:
            raise DeepSeekError("chat_session/create: zadny chat_session: " + str(r)[:160])
        return chat

    def create_pow_challenge(self, target_path="/api/v0/chat/completion") -> dict:
        r = self._req("POST", "/api/v0/chat/create_pow_challenge",
                      {"target_path": target_path})
        return r["data"]["biz_data"]["challenge"]

    def pow_header(self, challenge: dict, answer: int) -> str:
        obj = {
            "algorithm": challenge["algorithm"],
            "challenge": challenge["challenge"],
            "salt": challenge["salt"],
            "signature": challenge["signature"],
            "answer": answer,
            "target_path": challenge.get("target_path", "/api/v0/chat/completion"),
        }
        return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()

    def _solve(self, challenge: dict) -> int:
        """Vyresi PoW. Preferuje NATIVNI implementaci (bez fridy, bez appky).

        DeepSeekHashV1 = SHA3-256 bez prvniho kola Keccak-f. Viz
        bridge/pow_native.py (C knihovna, ~5M hash/s -> cely PoW ~0,03 s).
        """
        algo = challenge.get("algorithm", "DeepSeekHashV1")
        if algo == "DeepSeekHashV1":
            try:
                from . import pow_native
                ans = pow_native.solve(
                    challenge["salt"], challenge["expire_at"],
                    challenge["challenge"], challenge["difficulty"],
                )
                if ans >= 0:
                    return int(ans)
            except Exception as e:  # noqa: BLE001
                # kdyz nativni cesta selze, zkusime jeste legacy frida helper
                if not getattr(self, "_pow_native_warned", False):
                    self._pow_native_warned = True
                    print(f"[pow] nativni reseni selhalo: {e}", flush=True)

        # legacy zpetna kompatibilita: reseni pres fridu v bezici appce
        if self.pow is not None:
            ans = self.pow.solve(challenge)
            if ans >= 0:
                return ans
        raise RuntimeError(
            "PoW se nepodarilo vyresit (nativni backend selhal a frida helper neni)")

    def completion(self, session_id: str, prompt: str, **kw) -> str:
        """Odešle prompt a vrátí celou odpověď (BEZ myšlení, spojí stream)."""
        return "".join(t for k, t in self.completion_stream(session_id, prompt, **kw)
                        if k == "answer")

    @staticmethod
    def _kind(frag_type: str) -> str:
        """THINK -> 'think', vse ostatni -> 'answer'."""
        return "think" if (frag_type or "").upper() == "THINK" else "answer"

    def _parse_chunk(self, obj: dict) -> list[tuple[str, str]]:
        """Jeden SSE objekt -> seznam (kind, text) kde kind je 'think'|'answer'.

        DeepSeek posila mysleni a odpoved v jednom streamu:
          * `response/fragments/<i>/content` o=APPEND  -> text do posledniho fragmentu
          * `response/fragments` o=APPEND v=[{type, content}] -> novy fragment
            (vzniká, kdyz mysleni skonci a zacina odpoved)
          * `response/content` o=APPEND                 -> bezne chunky odpovedi
          * {"v": "..."}                                -> kratky tvar
        """
        p = obj.get("p")
        v = obj.get("v")
        o = obj.get("o")
        out: list[tuple[str, str]] = []

        # novy fragment (typicky prechod THINK -> RESPONSE)
        if o == "APPEND" and isinstance(v, list) and (p or "") == "response/fragments":
            for f in v:
                if not isinstance(f, dict):
                    continue
                self._last_frag_type = f.get("type") or "RESPONSE"
                if f.get("content"):
                    out.append((self._kind(self._last_frag_type), f["content"]))
            return out

        # prirustek textu do posledniho fragmentu
        # POZOR: `o` byva "APPEND", ale casto chybi uplne (None) — nesmime
        # ho vyzadovat, jinak se kus odpovedi zahodi (presne to se stavalo:
        # prvni fragment prisel jako RESPONSE "RE", dalsi prisel s o=None
        # a "ASON" se ztratilo).
        is_append = o in (None, "APPEND")
        if isinstance(p, str) and p.endswith("/content") and is_append and isinstance(v, str):
            return [(self._kind(self._last_frag_type), v)]
        if isinstance(p, str) and p == "response/content" and is_append and isinstance(v, str):
            return [("answer", v)]

        # kratky tvar {"v": "text"}
        if isinstance(v, str) and p is None and set(obj.keys()) == {"v"}:
            return [(self._kind(self._last_frag_type), v)]

        # inicialni stav s celymi fragmenty (obsahuje i prvni kus mysleni)
        if isinstance(v, dict):
            resp = v.get("response") or v
            frags = resp.get("fragments") or []
            for f in frags:
                if isinstance(f, dict):
                    self._last_frag_type = f.get("type") or "RESPONSE"
                    if f.get("content"):
                        out.append((self._kind(self._last_frag_type), f["content"]))
            return out
        return out

    def completion_stream(self, session_id: str, prompt: str, **kw):
        """Generator: yields (kind, text) kde kind je 'think' nebo 'answer'.

        Myersleni (thinking) chodi jako kind='think', odpoved jako 'answer'.

        Po dokonceni nastavi `self.last_response_message_id` — to je ID odpovedi,
        ktere se posila jako `parent_message_id` v dalsim tahu (server si tak drzi
        kontext konverzace a nemusime posilat celou historii).

        Pri RATE LIMITu (server posle `event: hint` s
        `{"type":"error","finish_reason":"rate_limit_reached"}`) zkusi
        pozadavek znovu s backoffem — agent dela hodne dotazu rychle za sebou
        a bez tohohle se cely tah ztratil jako "prazdna odpoved".
        """
        delays = RATE_LIMIT_DELAYS
        for attempt in range(len(delays) + 1):
            yielded = False
            try:
                for kind, text in self._completion_once(session_id, prompt, **kw):
                    yielded = True
                    yield kind, text
                return
            except DeepSeekError as e:
                if not (e.retry and e.rate_limited) or yielded or attempt >= len(delays):
                    raise
                wait = delays[attempt]
                print(f"[deepseek] rate limit — cekam {wait:.0f}s a zkousim znovu "
                      f"({attempt + 1}/{len(delays)})", flush=True)
                time.sleep(wait)

    def _completion_once(self, session_id: str, prompt: str, **kw):
        """Jeden pokus o dokonceni (viz completion_stream)."""
        challenge = self.create_pow_challenge()
        answer = self._solve(challenge)
        hdr = self.pow_header(challenge, answer)

        body = {
            "chat_session_id": session_id,
            "parent_message_id": kw.get("parent_message_id"),
            "prompt": prompt,
            "ref_file_ids": kw.get("ref_file_ids", []),
            "thinking_enabled": kw.get("thinking_enabled", False),
            "search_enabled": kw.get("search_enabled", False),
        }
        # Teplota (API ji respektuje — testovano 0.0 vs 2.0):
        # Env FRIDA_MCP_TEMPERATURE přepíše default; jinak posíláme 0.3
        # pro spolehlivost tool-callů místo nekontrolované high-temp.
        temp = None
        if "temperature" in kw and kw["temperature"] is not None:
            temp = kw["temperature"]
        elif (env := os.environ.get("FRIDA_MCP_TEMPERATURE")) is not None:
            try: temp = float(env)
            except ValueError: pass
        if temp is None:
            temp = 0.3  # default pro spolehlivost
        body["temperature"] = temp
        h = dict(self.headers)
        h["Authorization"] = "Bearer " + self.token
        h["Content-Type"] = "application/json"
        h["X-DS-PoW-Response"] = hdr
        req = urllib.request.Request(
            BASE + "/api/v0/chat/completion",
            data=json.dumps(body).encode(), headers=h, method="POST")
        got = False
        fallback: list[str] = []
        seen_error: DeepSeekError | None = None
        self.last_response_message_id = None
        self._last_frag_type = "RESPONSE"
        with urllib.request.urlopen(req, timeout=180) as r:
            for rawline in r:
                line = rawline.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if not line.startswith("data:"):
                    fallback.append(line)
                    continue
                # kazdy data: radek rozparsuj (ID odpovedi + text/mysleni)
                try:
                    obj = json.loads(line[5:].strip() or "{}")
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                # chybova "hint" hlaseni chodi taky jako data: radek
                hint = _hint_error(obj)
                if hint is not None:
                    seen_error = hint
                    continue
                if self.last_response_message_id is None:
                    if obj.get("response_message_id"):
                        self.last_response_message_id = obj["response_message_id"]
                    else:
                        v0 = obj.get("v")
                        if isinstance(v0, dict):
                            rr = v0.get("response") or {}
                            if rr.get("message_id"):
                                self.last_response_message_id = rr["message_id"]
                for kind, text in self._parse_chunk(obj):
                    if text:
                        got = True
                        yield kind, text
        if not got:
            # 1) chyba prisla jako `event: hint` s {"type":"error",...}
            if seen_error is not None:
                raise seen_error
            # 2) nic neseznamene -> hledej chybove JSON v fallback
            for line in fallback:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if isinstance(o, dict):
                    err = DeepSeekError.from_resp(o, "chat/completion")
                    if err:
                        raise err
            # nejsou tam chybove JSON, jen control events (event: ready/hint/close)
            # -> pravdepodobne auth/token problem
            raise DeepSeekError(
                "chat/completion: prazdna odpoved (zadne data:), "
                "pravdepodobne neplatny token nebo limit. "
                "Zkus: /frida-mcp tokens",
                retry=True
            )

    def _parse_line(self, line: str) -> str:
        """Vytahne text z jednoho SSE radku (viz _parse_completion)."""
        line = line.strip()
        if not line.startswith("data:"):
            return ""
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return ""
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        p = obj.get("p")
        v = obj.get("v")
        if isinstance(p, str) and "content" in p and obj.get("o") == "APPEND" and isinstance(v, str):
            return v
        if isinstance(v, str) and p is None and set(obj.keys()) == {"v"}:
            return v
        if isinstance(v, dict):
            resp = v.get("response") or v
            out = []
            for f in resp.get("fragments") or []:
                if f.get("type") == "RESPONSE" and f.get("content"):
                    out.append(f["content"])
            return "".join(out)
        return ""

    def _parse_completion(self, raw: str) -> str:
        """DeepSeek streamuje SSE/NDJSON. Text je v:
        - iniciální fragment:  {"v":{...,"fragments":[{"type":"RESPONSE","content":"..."}]}}
        - append chunky:       {"p":"response/fragments/-1/content","o":"APPEND","v":"..."}
        - krátká forma:        {"v":"..."}
        """
        parts: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            p = obj.get("p")
            v = obj.get("v")
            if isinstance(p, str) and "content" in p and obj.get("o") == "APPEND" and isinstance(v, str):
                parts.append(v)
            elif isinstance(v, str) and p is None and set(obj.keys()) == {"v"}:
                parts.append(v)
            elif isinstance(v, dict):
                resp = v.get("response") or v
                for f in resp.get("fragments") or []:
                    if f.get("type") == "RESPONSE" and f.get("content"):
                        parts.append(f["content"])
        out = "".join(parts)
        return out if out else raw
