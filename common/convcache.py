#!/usr/bin/env python3
"""convcache — drzi konverzaci v JEDNOM chatu a posila jen novou cast.

Proc to je dulezite
-------------------
Puvodni shimy vytvarely **novy chat pro kazdou zpravu** a posilaly celou
historii znovu jako jeden prompt. To je:

1. **Napadne** — bezny uzivatel ma jeden chat s N zpravami, my jsme meli
   N chatu s 1 zpravou. Presne podle toho se da poznat automatizace.
2. **Pomale a drahe** — cely prompt se posila pri kazdem tahu.

Server si pritom konverzaci drzi sam, staci ji spravne navazat:
    {"chat_session_id": <id>, "parent_message_id": <id posledni odpovedi>}
a poslat pouze novou zpravu. Overeno: model si pak pamatuje i obsah
z predchozich tahu ("MODRA-ZIRAFA-42" ✅).

Jak to funguje tady
-------------------
`prefix_lookup` porovna, jestli uz jsme tuhle konverzaci videli: hleda
takovy zaznam, jehoz ulozeny seznam zprav je PRESNY prefix toho, co
prichazi. Kdyz ano, vrati session + jen nove zpravy (delta). Kdyz ne,
vrati None a cely seznam (zacina se novy chat).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

MAX_ENTRIES = 24
MAX_AGE = 60 * 60 * 6  # 6 h


def fingerprint(messages: list[dict]) -> str:
    """Stabilni otisk seznamu zprav (role + obsah + tool_calls)."""
    norm = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") for p in c
                        if isinstance(p, dict) and p.get("type") == "text")
        tcs = [
            [(tc.get("function") or {}).get("name"), (tc.get("function") or {}).get("arguments")]
            for tc in (m.get("tool_calls") or [])
        ]
        norm.append([m.get("role"), c or "", tcs])
    blob = json.dumps(norm, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class Entry:
    __slots__ = ("n", "fp", "session_id", "parent_id", "at")

    def __init__(self, n: int, fp: str, session_id: str, parent_id=None):
        self.n = n
        self.fp = fp
        self.session_id = session_id
        self.parent_id = parent_id
        self.at = time.time()


class ConvCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[Entry] = []

    def _gc(self) -> None:
        now = time.time()
        self._entries = [e for e in self._entries if now - e.at < MAX_AGE]
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]

    def lookup(self, messages: list[dict]):
        """Vrati (session_id, parent_id, zpravy_k_poslani).

        Bud navaze existujici chat (a vrati jen delta), nebo vrati
        (None, None, vsechny zpravy) = zacni novy chat.
        """
        with self._lock:
            self._gc()
            n_all = len(messages)
            for e in reversed(self._entries):
                if e.n > n_all:
                    continue
                if fingerprint(messages[: e.n]) == e.fp:
                    e.at = time.time()
                    if e.n == n_all:
                        # stejny stav jako minule (retry) -> posli vse znovu
                        return e.session_id, e.parent_id, messages
                    return e.session_id, e.parent_id, messages[e.n:]
        return None, None, messages

    def bind(self, messages: list[dict], session_id: str, parent_id=None) -> None:
        """Zapise, ze tahle konverzace ma danou session a je u zpravy `len(messages)`."""
        with self._lock:
            n = len(messages)
            # nahrad existujici zaznam pro stejnou session
            self._entries = [e for e in self._entries if e.session_id != session_id]
            self._entries.append(Entry(n, fingerprint(messages), session_id, parent_id))
            self._gc()

    def update_parent(self, session_id: str, parent_id) -> None:
        with self._lock:
            for e in self._entries:
                if e.session_id == session_id:
                    e.parent_id = parent_id

    def drop(self, session_id: str) -> None:
        """Zahodi konverzaci (napr. kdyz delta request selhal) -> priste novy chat."""
        with self._lock:
            self._entries = [e for e in self._entries if e.session_id != session_id]

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._entries),
                    "sessions": [e.session_id for e in self._entries]}
