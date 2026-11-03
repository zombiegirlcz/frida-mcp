#!/usr/bin/env python3
"""Testy pro common/convcache.py.

Cache resi dve veci naraz:
1. **jeden chat na konverzaci** (ne novy chat na kazdou zpravu — to je
   napadne a je to presne to, podle ceho se da automatizace poznat)
2. **posilani jen delta** (server si konverzaci drzi sam pres
   parent_message_id, takze nema smysl posilat celou historii znovu)

    python3 tests/test_convcache.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile  # noqa: E402

from common.convcache import ConvCache, fingerprint  # noqa: E402


def new_cache() -> ConvCache:
    """Izolovana cache — nikdy nesaha na realny logs/convstate.json."""
    return ConvCache(state_path=os.path.join(tempfile.mkdtemp(), "convstate.json"))

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}  {detail}")


SYS = {"role": "system", "content": "Jsi asistent."}
A1 = {"role": "user", "content": "Pocet .py souboru?"}
A2 = {"role": "assistant", "content": "Podivam se.",
      "tool_calls": [{"id": "c1", "type": "function",
                      "function": {"name": "bash", "arguments": '{"command":"ls"}'}}]}
A3 = {"role": "tool", "tool_call_id": "c1", "content": "3"}

c = new_cache()

# 1) prvni request -> novy chat, posila se vse
sid, parent, delta = c.lookup([SYS, A1])
check("1. request = novy chat", sid is None and len(delta) == 2,
      f"sid={sid} delta={len(delta)}")
c.bind([SYS, A1], "chat-A", 2)

# 2) navazani -> stejny chat, posila se JEN delta
sid, parent, delta = c.lookup([SYS, A1, A2, A3])
check("2. request = stejny chat + delta",
      sid == "chat-A" and parent == 2 and [m["role"] for m in delta] == ["assistant", "tool"],
      f"sid={sid} parent={parent} delta={[m['role'] for m in delta]}")

# 3) retry se stejnymi zpravami -> NESMI posilat celou historii znovu
#    (DeepSeek uz tyto zpravy MA — druhy prompt by byl duplikat a model by
#    videl stejne tool cally/vysledky 2x, viz session.jsonl). delta=None je
#    signal pro volajici "prehraj cached_output(), nevolej API znovu".
sid, parent, delta = c.lookup([SYS, A1])
check("3. retry = stejny chat, BEZ re-poslani historie (delta=None)",
      sid == "chat-A" and delta is None, f"sid={sid} delta={delta}")

# 3b) bind() s last_output ulozi cache pro presny retry
c.bind([SYS, A1], "chat-A", 2, last_output=[("answer", "Mam 3 .py soubory.")])
sid, parent, delta = c.lookup([SYS, A1])
check("3b. po bind(last_output=...) je retry stale delta=None",
      sid == "chat-A" and delta is None, f"sid={sid} delta={delta}")
check("3c. cached_output() vrati presne to, co bylo nabindovano",
      c.cached_output("chat-A") == [("answer", "Mam 3 .py soubory.")],
      f"cached={c.cached_output('chat-A')}")
check("3d. cached_output() pro neznamou session vrati None",
      c.cached_output("chat-neexistuje") is None)

# 4) jina konverzace se NESMI splest
B = [SYS, {"role": "user", "content": "Uplne jina otazka"}]
sid, parent, delta = c.lookup(B)
check("4. jina konverzace = novy chat", sid is None and len(delta) == 2, f"sid={sid}")

# 5) dve konverzace vedle sebe se nepletu
c.bind(B, "chat-B", 20)
sidA, parentA, _ = c.lookup([SYS, A1, A2])
sidB, parentB, _ = c.lookup(B + [{"role": "assistant", "content": "x"}])
check("5. dve konverzace paralelne",
      sidA == "chat-A" and sidB == "chat-B", f"A={sidA} B={sidB}")

# 6) ruzny obsah = ruzny otisk
check("6. fingerprint rozlisi obsah",
      fingerprint([SYS, A1]) != fingerprint(B))

# 7) drop zahodi konverzaci
c.drop("chat-A")
sid, _, delta = c.lookup([SYS, A1, A2])
check("7. drop -> pristi request je novy chat", sid is None, f"sid={sid}")

# 8) clear() zapomene vsechno -> dalsi request posle CELY kontext
c2 = new_cache()
c2.bind([SYS, A1], "chat-X", 2)
c2.bind(B, "chat-Y", 2)
n = c2.clear()
sid, _, delta = c2.lookup([SYS, A1, A2])
check("8. clear() zapomene chaty (plny kontext)",
      n == 2 and sid is None and len(delta) == 3, f"n={n} sid={sid} delta={len(delta)}")

# 9) prazdny seznam nesmi spadnout
try:
    c.lookup([])
    check("9. prazdny seznam nespadne", True)
except Exception as e:  # noqa: BLE001
    check("9. prazdny seznam nespadne", False, str(e))

# ---------------------------------------------------------------- pi session
# 10) ID pi session urcuje chat; prezije "restart" (novy objekt nad stejnym souborem)
tmp = os.path.join(tempfile.mkdtemp(), "convstate.json")
c3 = ConvCache(state_path=tmp)
c3.set_current("pi-session-AAA")
sid, _, delta = c3.lookup([SYS, A1])
check("10. s ID session = novy chat", sid is None and len(delta) == 2)
c3.bind([SYS, A1], "chat-PI", 7)

# "restart shimu": novy objekt, stejny stavovy soubor
c4 = ConvCache(state_path=tmp)
c4.set_current("pi-session-AAA", fresh=False)
sid, parent, delta = c4.lookup([SYS, A1, A2, A3])
check("11. po restartu navaze STEJNY chat (resume)",
      sid == "chat-PI" and parent == 7 and len(delta) == 2,
      f"sid={sid} parent={parent} delta={len(delta)}")

# 12) reason=new zahodi stav -> novy chat
c4.set_current("pi-session-AAA", fresh=True)
sid, _, delta = c4.lookup([SYS, A1, A2, A3])
check("12. reason=new -> novy chat", sid is None and len(delta) == 4,
      f"sid={sid} delta={len(delta)}")

# 13) jina pi session ma vlastni chat
c5 = ConvCache(state_path=os.path.join(tempfile.mkdtemp(), "s.json"))
c5.set_current("pi-session-AAA")
c5.bind([SYS, A1], "chat-A2", 1)
c5.set_current("pi-session-BBB")
sid, _, delta = c5.lookup([SYS, A1, A2, A3])
check("13. jina pi session = vlastni chat", sid is None, f"sid={sid}")

# ------------------------------------------- race: request driv nez /session
# V praxi (2026-11-03): pi poslalo prvni chat/completions driv, nez extension
# stihla POST /session. bind() nemel _current -> ulozil entry pod "fp:…".
# Kdyz pak /session dorazil, set_current() jen nastavil _current a osirelou
# entry nechal pod fp: klicem -> lookup() ji nenasel -> NOVY chat.
# Z jedne konverzace tak byly 4 chaty v appce.
c6 = ConvCache(state_path=os.path.join(tempfile.mkdtemp(), "s6.json"))
sid, _, delta = c6.lookup([SYS, A1])
check("14. bez /session je to novy chat (no-session-id)",
      sid is None and c6.last_new_reason == "no-session-id",
      f"sid={sid} reason={c6.last_new_reason}")
c6.bind([SYS, A1], "chat-ORPHAN", 3)
check("15. entry se ulozila pod fp: (osirela)",
      any(k.startswith("fp:") for k in c6._entries),
      f"klice={list(c6._entries)}")

c6.set_current("pi-session-CCC")
check("16. set_current adoptoval osirelou fp: entry",
      "pi-session-CCC" in c6._entries
      and not any(k.startswith("fp:") for k in c6._entries),
      f"klice={list(c6._entries)}")
sid, parent, delta = c6.lookup([SYS, A1, A2, A3])
check("17. adoptovana entry -> STEJNY chat, ne novy",
      sid == "chat-ORPHAN" and parent == 3 and len(delta) == 2,
      f"sid={sid} parent={parent} delta={len(delta)}")

# 18) fresh (compact) i po adopci zalozi novy chat a rekne duvod
c6.set_current("pi-session-CCC", fresh=True)
sid, _, _ = c6.lookup([SYS, A1, A2, A3])
check("18. compact/fresh -> novy chat s duvodem 'fresh'",
      sid is None and c6.last_new_reason == "fresh",
      f"sid={sid} reason={c6.last_new_reason}")

# 19) prvni request v nove pi session = first-in-session
c7 = ConvCache(state_path=os.path.join(tempfile.mkdtemp(), "s7.json"))
c7.set_current("pi-session-DDD")
sid, _, _ = c7.lookup([SYS, A1])
check("19. prvni request = first-in-session",
      sid is None and c7.last_new_reason == "first-in-session",
      f"reason={c7.last_new_reason}")

# 20) zmena historie (komprese) = history-changed
c7.bind([SYS, A1], "chat-D", 2)
sid, _, _ = c7.lookup([SYS, B[1]])
check("20. zmenena historie = history-changed",
      sid is None and c7.last_new_reason == "history-changed",
      f"reason={c7.last_new_reason}")

# 21) uspesne navazani nema zadny "new reason"
sid, _, _ = c7.lookup([SYS, A1, A2, A3])
check("21. navazani delta tahu nema duvod novyho chatu",
      sid == "chat-D" and c7.last_new_reason is None,
      f"sid={sid} reason={c7.last_new_reason}")

# 22) adopce sirotka prezije restart (zapis do stavoveho souboru)
tmp2 = os.path.join(tempfile.mkdtemp(), "s8.json")
c8 = ConvCache(state_path=tmp2)
c8.bind([SYS, A1], "chat-R", 5)
c8.set_current("pi-session-EEE")
c9 = ConvCache(state_path=tmp2)
c9.set_current("pi-session-EEE")
sid, parent, _ = c9.lookup([SYS, A1, A2, A3])
check("22. adoptovany chat prezije restart shimu",
      sid == "chat-R" and parent == 5, f"sid={sid} parent={parent}")

# ------------------------------------- restart shimu UPROSTRED session
# V praxi (2026-11-03): extension si shim sam restartoval (detekce
# zastaraleho kodu) uprostred session. `_current` se neperzistoval, takze
# novy proces nemel ID pi session -> kazdy request sel pres `fp:` vetev
# a z jednoho dotazu "test" byly 2 chaty (d9b9434b, 1ee47147).
tmp3 = os.path.join(tempfile.mkdtemp(), "s9.json")
c10 = ConvCache(state_path=tmp3)
c10.set_current("pi-session-FFF")
c10.lookup([SYS, A1])
c10.bind([SYS, A1], "chat-F", 4)

c11 = ConvCache(state_path=tmp3)  # simulace restartu shimu
check("23. _current prezije restart shimu",
      c11.current() == "pi-session-FFF", f"current={c11.current()}")
sid, parent, delta = c11.lookup([SYS, A1, A2, A3])
check("24. po restartu se navaze STEJNY chat (ne novy)",
      sid == "chat-F" and parent == 4 and len(delta) == 2,
      f"sid={sid} parent={parent} delta={len(delta)}")

# 25) fresh (compact) po restartu porad znamena novy chat
c11.set_current("pi-session-FFF", fresh=True)
sid, _, _ = c11.lookup([SYS, A1, A2, A3])
check("25. fresh po restartu -> novy chat",
      sid is None and c11.last_new_reason == "fresh",
      f"sid={sid} reason={c11.last_new_reason}")

# 26) ukazatel na neexistujici session se pri nacteni ignoruje
c12 = ConvCache(state_path=tmp3)
c12.clear()
c13 = ConvCache(state_path=tmp3)
check("26. osirely _current se pri nacteni zahodi",
      c13.current() is None, f"current={c13.current()}")

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
