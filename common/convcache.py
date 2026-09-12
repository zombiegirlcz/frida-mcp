#!/usr/bin/env python3
"""convcache — drzi konverzaci v JEDNOM chatu a posila jen novou cast.

Proc to je dulezite
-------------------
Puvodni shimy vytvarely **novy chat pro kazdou zpravu** a posilaly celou
historii znovu jako jeden prompt. To je:

1. **Napadne** — bezny uzivatel ma jeden chat s N zpravami, my jsme meli
   N chatu s 1 zpravou. Presne podle toho se da automatizace poznat.
2. **Pomale a drahe** — cely prompt se posila pri kazdem tahu.

Server si pritom konverzaci drzi sam, staci ji spravne navazat:
    {"chat_session_id": <id>, "parent_message_id": <id posledni odpovedi>}
a poslat pouze novou zpravu.

Synchronizace s pi session
--------------------------
Extension posila pri startu session **ID pi session** (`ctx.sessionManager.
getSessionId()`) na `POST /session`. Podle nej se urci, ktery chat pouzit:

* `reason=resume`  -> navaze se **stejny chat** (kontext zustava na serveru)
* `reason=new`     -> zacne se cely chat od znova
* `startup`        -> pokracuje se, pokud mame ulozeny stav

Mapovani se **uklada na disk**, takze prezije restart shimu i pi.
Kdyz ID neznamе (napr. jiny klient), spadne se zpet na porovnavani prefixu
zprav.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time

MAX_ENTRIES = 24
MAX_AGE = 60 * 60 * 24 * 14  # 14 dni
DEFAULT_STATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "convstate.json")


def fingerprint(messages: list[dict]) -> str:
    """Stabilni otisk seznamu zprav (role + obsah + tool_calls)."""
    norm = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") for p in c
                        if isinstance(p, dict) and p.get("type") == "text")
        tcs = [
            [(tc.get("function") or {}).get("name"),
             (tc.get("function") or {}).get("arguments")]
            for tc in (m.get("tool_calls") or [])
        ]
        norm.append([m.get("role"), c or "", tcs])
    blob = json.dumps(norm, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class Entry:
    __slots__ = ("key", "n", "fp", "session_id", "parent_id", "at")

    def __init__(self, key: str, n: int, fp: str, session_id: str, parent_id=None):
        self.key = key
        self.n = n
        self.fp = fp
        self.session_id = session_id
        self.parent_id = parent_id
        self.at = time.time()


class ConvCache:
    """Drzi mapovani "konverzace -> chat na strane API".

    Klicem je ID pi session (kdyz ji zname), jinak otisk prefixu zprav.
    """

    def __init__(self, state_path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, Entry] = {}
        self._current: str | None = None
        self.state_path = state_path if state_path is not None else DEFAULT_STATE
        self._load()

    # ------------------------------------------------------------ perzistence
    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:  # noqa: BLE001
            return
        now = time.time()
        for key, e in (raw.get("entries") or {}).items():
            try:
                if now - float(e.get("at", 0)) > MAX_AGE:
                    continue
                self._entries[key] = Entry(
                    key, int(e["n"]), e["fp"], e["session_id"], e.get("parent_id"))
            except Exception:  # noqa: BLE001
                continue

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            data = {
                "entries": {
                    k: {"n": e.n, "fp": e.fp, "session_id": e.session_id,
                        "parent_id": e.parent_id, "at": e.at}
                    for k, e in self._entries.items()
                },
            }
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.state_path)
            os.chmod(self.state_path, 0o600)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------- aktualni session
    def set_current(self, key: str | None, fresh: bool = False) -> None:
        """Nastavi klic aktualni konverzace (ID pi session).

        fresh=True (reason=new) zahodi stav, aby se zacalo novym chatem.
        """
        with self._lock:
            self._current = key or None
            if fresh and self._current:
                self._entries.pop(self._current, None)
                self._save()

    def current(self) -> str | None:
        return self._current

    # ------------------------------------------------------------------ lookup
    def _gc_locked(self) -> None:
        now = time.time()
        self._entries = {k: e for k, e in self._entries.items()
                         if now - e.at < MAX_AGE}
        if len(self._entries) > MAX_ENTRIES:
            keep = sorted(self._entries.items(), key=lambda kv: kv[1].at)[-MAX_ENTRIES:]
            self._entries = dict(keep)

    def lookup(self, messages: list[dict]):
        """Vrati (session_id, parent_id, zpravy_k_poslani).

        Bud navaze existujici chat (a vrati jen delta), nebo vrati
        (None, None, vsechny zpravy) = zacni novy chat.
        """
        with self._lock:
            self._gc_locked()
            n_all = len(messages)

            # 1) podle ID pi session (deterministicke, prezije restart)
            if self._current:
                e = self._entries.get(self._current)
                if e is not None:
                    if e.n == n_all and fingerprint(messages) == e.fp:
                        # retry stejneho stavu -> posli celou historii do stejneho chatu
                        return e.session_id, e.parent_id, messages
                    if (n_all > e.n
                            and fingerprint(messages[: e.n]) == e.fp):
                        e.at = time.time()
                        return e.session_id, e.parent_id, messages[e.n :]
                    # historie se zmenila (komprese/rewrite) -> novy chat
                return None, None, messages

            # 2) fallback: porovnani prefixu (klient bez ID session)
            for e in sorted(self._entries.values(), key=lambda x: -x.n):
                if e.n > n_all:
                    continue
                if fingerprint(messages[: e.n]) == e.fp:
                    e.at = time.time()
                    if e.n == n_all:
                        return e.session_id, e.parent_id, messages
                    return e.session_id, e.parent_id, messages[e.n :]
            return None, None, messages

    def bind(self, messages: list[dict], session_id: str, parent_id=None) -> None:
        """Zapise, ze konverzace ma danou session a je u zpravy len(messages)."""
        with self._lock:
            key = self._current or f"fp:{fingerprint(messages)[:16]}"
            self._entries = {k: e for k, e in self._entries.items()
                             if e.session_id != session_id}
            self._entries[key] = Entry(key, len(messages), fingerprint(messages),
                                       session_id, parent_id)
            self._gc_locked()
            self._save()

    def update_parent(self, session_id: str, parent_id) -> None:
        with self._lock:
            for e in self._entries.values():
                if e.session_id == session_id:
                    e.parent_id = parent_id
            self._save()

    def drop(self, session_id: str) -> None:
        """Zahodi konverzaci (napr. kdyz delta request selhal)."""
        with self._lock:
            self._entries = {k: e for k, e in self._entries.items()
                             if e.session_id != session_id}
            self._save()

    def clear(self) -> int:
        """Zapomene vsechny konverzace -> dalsi request posle CELY kontext."""
        with self._lock:
            n = len(self._entries)
            self._entries = {}
            self._save()
            return n

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._entries),
                "current": self._current,
                "sessions": [e.session_id for e in self._entries.values()],
            }
