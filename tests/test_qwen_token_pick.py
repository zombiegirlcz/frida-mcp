#!/usr/bin/env python3
"""Regresni test: vyber NEJNOVEJSIHO qwen tokenu z Cookies DB.

V realne DB je 16 radku `name='token'` a VSECHNY maji stejnou delku (209 B).
Puvodni kod radil `ORDER BY length(value) DESC`, coz pri shodne delce vraci
LIBOVOLNY radek — v praxi to vybralo token z 07:42 misto z 21:30, takze
shim jel na starem tokenu a novy ucet se vubec neprojevil.

    python3 tests/test_qwen_token_pick.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from common.qwen_token import pick_newest_token
except ImportError as e:
    print(f"FAIL chybi common/qwen_token.py: {e}")
    raise SystemExit(1)

FAILED = []


def build_db(rows):
    p = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE cookies (creation_utc INTEGER, host_key TEXT, "
        "name TEXT, value TEXT, last_update_utc INTEGER)"
    )
    con.executemany("INSERT INTO cookies VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return p


# 16 radku se STEJNOU delkou hodnoty (209) a stejnym jmenem, ruzny cas
rows = [(1000 + i, "chat.qwen.ai", "token", "x" * 209, 5_000_000 + i * 1000)
        for i in range(16)]
rows.append((2000, "chat.qwen.ai", "token", "y" * 209, 9_999_999))   # nejnovejsi
rows.append((3000, "chat.qwen.ai", "other", "z" * 209, 99_999_999))  # jine jmeno

p = build_db(rows)
con = sqlite3.connect(p)
got = pick_newest_token(con)
con.close()
os.unlink(p)

if got == "y" * 209:
    print("OK  vybran NEJNOVEJSI token (last_update_utc), ne podle delky")
else:
    FAILED.append("nejnovejsi token")
    print(f"FAIL vybran {got[:12] if got else None}... (ocekavan 'yyy...')")

# prazdna DB -> None
p2 = build_db([])
con = sqlite3.connect(p2)
got2 = pick_newest_token(con)
con.close()
os.unlink(p2)
if got2 is None:
    print("OK  prazdna DB -> None")
else:
    FAILED.append("prazdna DB")
    print(f"FAIL prazdna DB vratila {got2!r}")

print()
if FAILED:
    print(f"SELHALO: {len(FAILED)} - {', '.join(FAILED)}")
    raise SystemExit(1)
print("Vsechny testy vyberu tokenu prosly")
