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

# 3) retry se stejnymi zpravami -> posli vse znovu (ale stejny chat)
sid, parent, delta = c.lookup([SYS, A1])
check("3. retry = stejny chat, cela historie",
      sid == "chat-A" and len(delta) == 2, f"sid={sid} delta={len(delta)}")

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

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
