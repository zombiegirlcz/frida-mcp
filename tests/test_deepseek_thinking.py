#!/usr/bin/env python3
"""Testy pro rozdeleni DeepSeek streamu na mysleni (THINK) a odpoved (RESPONSE).

DeepSeek appka posila mysleni i odpoved v JEDNOM SSE streamu. Rozlisi se podle
typu fragmentu:
  * `response/fragments/-1/content`  o=APPEND  -> text do posledniho fragmentu
  (jeho typ je THINK nebo RESPONSE)
  * `response/fragments`             o=APPEND  -> novy fragment (prechod
  THINK -> RESPONSE), v poli `v` je uz cely text odpovedi
  * `response/content`               o=APPEND  -> bezne chunky odpovedi
  * {"v": "..."}                                -> kratky tvar

Mysleni se v OpenAI vystupu posila jako `reasoning_content`, odpoved jako
`content`. Testy jsou offline (zadne sit).

    python3 tests/test_deepseek_thinking.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "deepseek"))

from bridge.deepseek_api import DeepSeekAPI  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}  {detail}")


api = DeepSeekAPI.__new__(DeepSeekAPI)  # bez site/init
api._last_frag_type = "RESPONSE"


def reset():
    api._last_frag_type = "RESPONSE"


def feed(seq):
    """Prozene sekvenci SSE objektu a vrati (mysleni, odpoved)."""
    reset()
    think, ans = [], []
    for obj in seq:
        for kind, text in api._parse_chunk(obj):
            (think if kind == "think" else ans).append(text)
    return "".join(think), "".join(ans)


# 1) bez mysleni: klasicke chunky odpovedi
t, a = feed([
    {"p": "response/content", "o": "APPEND", "v": "AHO"},
    {"p": "response", "o": "BATCH", "v": []},
])
check("1. bez mysleni -> vse je odpoved", t == "" and a == "AHO", f"t={t!r} a={a!r}")

# 2) mysleni: prvni fragment je THINK, pak prestup na RESPONSE
t, a = feed([
    {"v": {"response": {"message_id": 2, "fragments": [
        {"id": 2, "type": "THINK", "content": "We"}]}}},
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": " need"},
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": " answer"},
    {"p": "response/fragments/-1/elapsed_secs", "o": "SET", "v": 0.65},
    # tady skonci mysleni a zacina odpoved
    {"p": "response/fragments", "o": "APPEND",
     "v": [{"id": 3, "type": "RESPONSE", "content": "42"}]},
    {"p": "response/status", "o": "SET", "v": "FINISHED"},
])
check("2. THINK -> reasoning, RESPONSE -> content",
      t == "We need answer" and a == "42", f"t={t!r} a={a!r}")

# 3) kratky tvar {"v": "..."} se radi k aktualnimu typu fragmentu
t, a = feed([
    {"v": {"response": {"fragments": [{"id": 1, "type": "THINK", "content": "x"}]}}},
    {"v": "y"},
])
check("3. kratky tvar patri k aktualnimu typu", t == "xy" and a == "", f"t={t!r} a={a!r}")

# 4) odpoved po chuncich (bez mysleni, path response/content)
t, a = feed([
    {"p": "response/content", "o": "APPEND", "v": "4"},
    {"p": "response/content", "o": "APPEND", "v": "2"},
])
check("4. chunky odpovedi se spoji", a == "42", f"a={a!r}")

# 5) neznamy/nezajimavy objekt nesmi nic rozbit ani vratit
t, a = feed([
    {"p": "response/status", "o": "SET", "v": "FINISHED"},
    {"updated_at": 12345},
    {},
])
check("5. nezajimave objekty nic nevraci", t == "" and a == "", f"t={t!r} a={a!r}")

# 6) mysleni i odpoved v jednom streamu, ve spravnem poradi
t, a = feed([
    {"v": {"response": {"fragments": [{"id": 1, "type": "THINK", "content": "premyslim"}]}}},
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": " dal"},
    {"p": "response/fragments", "o": "APPEND",
     "v": [{"id": 2, "type": "RESPONSE", "content": "hotovo"}]},
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": "!"},
])
check("6. mysleni a odpoved oddelene",
      t == "premyslim dal" and a == "hotovo!", f"t={t!r} a={a!r}")

# 7) REGRESE: druhy chunk odpovedi ma `o` = None (ne "APPEND") a nesmi se ztratit
t, a = feed([
    {"v": {"response": {"fragments": [{"id": 2, "type": "THINK", "content": "We"}]}}},
    {"p": "response/fragments/-1/content", "o": "APPEND", "v": " need"},
    {"p": "response/fragments", "o": "APPEND",
     "v": [{"id": 3, "type": "RESPONSE", "content": "RE"}]},
    {"p": "response/fragments/-1/content", "v": "ASON"},   # <-- zadne "o"!
    {"p": "response/fragments/-1/content", "v": "ER_OK"},
])
check("7. chybejici 'o' u chunku se nesmi ztratit",
      a == "REASONER_OK" and t == "We need", f"t={t!r} a={a!r}")

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy prosly ✅")
