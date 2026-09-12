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
import urllib.request

BASE = "https://chat.deepseek.com"

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
        bd = r.get("data", {}).get("biz_data", r)
        return bd.get("chat_session", bd)

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
        """
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
        h = dict(self.headers)
        h["Authorization"] = "Bearer " + self.token
        h["Content-Type"] = "application/json"
        h["X-DS-PoW-Response"] = hdr
        req = urllib.request.Request(
            BASE + "/api/v0/chat/completion",
            data=json.dumps(body).encode(), headers=h, method="POST")
        got = False
        fallback: list[str] = []
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
        if not got and fallback:
            # endpoint nevratil SSE (napr. chyba) — posli raw, at je videt proc
            yield "answer", "\n".join(fallback)

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
