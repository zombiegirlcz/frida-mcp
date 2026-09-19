#!/usr/bin/env python3
"""Idempotentni oprava: vyber NEJNOVEJSIHO qwen tokenu + zruseni 12h skipu.

Spustit z rootu repa:  python3 tests/_fix_qwen_tokens.py
Kazdy krok se nejdriv zepta, zda uz neni hotovy (lze pustit opakovane).
"""
import io
import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT = []


# ---------------------------------------------------------------- 1) modul
QWEN_TOKEN_MOD = '''#!/usr/bin/env python3
"""Vyber qwen tokenu z Cookies DB — jedno misto pro cely repo.

PROBLEM: v Cookies DB appky je hromada radku `name='token'` a VSECHNY maji
stejnou delku (JWT ~209 B). Puvodni dotaz radil `ORDER BY length(value) DESC`,
coz pri shode delky vraci LIBOVOLNY radek — po prihlaseni jineho uctu se tak
porad pouzival stary token (realne: disk id=bfc28bc8-…, DB uz id=0289d7f3-…).

Spravne je radit podle CASU zapisu (`last_update_utc DESC`), s fallbackem na
`creation_utc` a teprve nakonec na delku.
"""

from __future__ import annotations


def cookie_columns(con) -> set:
    """Nazvy sloupcu tabulky `cookies` (podle nich volime radeni)."""
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(cookies)")}
    except Exception:  # noqa: BLE001
        return set()


def pick_newest_token(con, host_like: str = "%qwen%") -> str | None:
    """Vrati hodnotu NEJNOVEJSIHO qwen tokenu z otevrene Cookies DB.

    Vraci None, kdyz tam zadny token neni (uzivatel neni prihlaseny).
    Poradi preferenci:
      1. last_update_utc DESC  — kdy appka cookie naposledy zapsala/obnovila
      2. creation_utc DESC     — kdy vznikla (starsi schema)
      3. length(value) DESC    — nouzove, nedeterministicke pri shode delek
    """
    cols = cookie_columns(con)
    for col in ("last_update_utc", "creation_utc"):
        if col in cols:
            row = con.execute(
                "SELECT value FROM cookies "
                "WHERE name='token' AND host_key LIKE ? "
                f"ORDER BY {col} DESC",
                (host_like,),
            ).fetchone()
            if row and row[0]:
                return row[0]
    row = con.execute(
        "SELECT value FROM cookies "
        "WHERE name='token' AND host_key LIKE ? "
        "ORDER BY length(value) DESC",
        (host_like,),
    ).fetchone()
    return row[0] if row else None
'''

path = "common/qwen_token.py"
if not os.path.exists(path) or "pick_newest_token" not in io.open(path, encoding="utf-8").read():
    io.open(path, "w", encoding="utf-8").write(QWEN_TOKEN_MOD)
    REPORT.append("napsan common/qwen_token.py")
else:
    REPORT.append("common/qwen_token.py uz je OK")


# ------------------------------------------------ 2) ensure_tokens.py
p = "scripts/ensure_tokens.py"
s = io.open(p, encoding="utf-8").read()
changed = []

if "from common.qwen_token import pick_newest_token" not in s:
    old = 'HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n'
    assert s.count(old) == 1, ("HERE", s.count(old))
    s = s.replace(old, old +
                  '\n# Spolecny vyber tokenu (jedno misto pro cely repo).\n'
                  'if HERE not in sys.path:\n'
                  '    sys.path.insert(0, HERE)\n'
                  'from common.qwen_token import pick_newest_token  # noqa: E402\n')
    changed.append("import")

if "fresh(tok_file, 3600 * 12)" in s:
    old = ('    if not force and fresh(tok_file, 3600 * 12):\n'
           '        log("qwen: token je cerstvy, preskakuji")\n'
           '        return True\n')
    assert s.count(old) == 1, ("skip", s.count(old))
    s = s.replace(old,
                  '    # ZADNY "fresh" skip pro qwen: token v Cookies DB se meni pri\n'
                  '    # kazdem prihlaseni/obnove a disk muze mit jinou identitu\n'
                  '    # (realne: disk id=bfc28bc8-… vs DB id=0289d7f3-…). Kdyz se\n'
                  '    # cteni preskoci, novy ucet se nikdy neprojevi. Cteni DB je\n'
                  '    # par ms, takze ho delame vzdy.\n')
    changed.append("skip")

if "ORDER BY length(value) DESC" in s:
    old = '''        rows = con.execute(
            "SELECT value FROM cookies WHERE name='token' AND host_key LIKE '%qwen%' "
            "ORDER BY length(value) DESC"
        ).fetchall()'''
    assert s.count(old) == 1, ("select", s.count(old))
    s = s.replace(old,
                  '        # NEJNOVEJSI token (last_update_utc), NE podle delky —\n'
                  '        # vsechny JWT maji stejnou delku, takze `length DESC`\n'
                  '        # vybiral nahodne.\n'
                  '        tok = pick_newest_token(con)')
    old2 = ('    if not rows:\n'
            '        log("qwen: cookie `token` neni — prihlas se v Qwen appce '
            '(anonymni rezim ma denni limit)")\n'
            '        return os.path.exists(tok_file)\n'
            '    tok = rows[0][0]\n')
    assert s.count(old2) == 1, ("rows", s.count(old2))
    s = s.replace(old2, '')
    changed.append("select")

if changed:
    io.open(p, "w", encoding="utf-8").write(s)
    REPORT.append("ensure_tokens.py: " + ", ".join(changed))
else:
    REPORT.append("ensure_tokens.py uz je OK")


# ------------------------------------------------ 3) qwen_api.py
p = "qwen/bridge/qwen_api.py"
s = io.open(p, encoding="utf-8").read()
changed = []

if "from common.qwen_token import pick_newest_token" not in s:
    old = 'from common import tokenauto as _auto  # noqa: E402\n'
    assert s.count(old) == 1, ("import", s.count(old))
    s = s.replace(old, old + 'from common.qwen_token import pick_newest_token  # noqa: E402\n')
    changed.append("import")

if "ORDER BY length(value) DESC" in s:
    old = '''            rows = con.execute(
                "SELECT value FROM cookies WHERE name='token' AND host_key LIKE '%qwen%' "
                "ORDER BY length(value) DESC"
            ).fetchall()'''
    assert s.count(old) == 1, ("select", s.count(old))
    s = s.replace(old,
                  '            # NEJNOVEJSI token (last_update_utc), NE podle delky —\n'
                  '            # vsechny JWT maji stejnou delku (~209 B), takze\n'
                  '            # `length DESC` vybiral nahodne a po prehlaseni uctu\n'
                  '            # se porad pouzival stary token.\n'
                  '            tok = pick_newest_token(con)')
    old2 = ('        if not rows:\n'
            '            raise RuntimeError("cookie `token` nenalezena — jsi '
            'prihlaseny v Qwen appce?")\n'
            '        tok = rows[0][0]\n')
    assert s.count(old2) == 1, ("rows", s.count(old2))
    s = s.replace(old2, '')
    changed.append("select")

if "max_age: float = 3600 * 12" in s:
    old = 'def _token_stale(path: str, max_age: float = 3600 * 12) -> bool:'
    assert s.count(old) == 1
    s = s.replace(old,
                  'def _token_stale(path: str, max_age: float = 900) -> bool:\n'
                  '    """Je cache tokenu zastarala? (default 15 min, driv 12 h.)\n\n'
                  '    12 h bylo prilis: po prehlaseni uctu v appce se novy token\n'
                  '    vubec neprecetl a shim jel na stare identite, dokud cache\n'
                  '    nevyprsela. 15 min je kompromis mezi cerstvosti a poctem\n'
                  '    sudo cteni.\n'
                  '    """')
    changed.append("stale")

if changed:
    io.open(p, "w", encoding="utf-8").write(s)
    REPORT.append("qwen_api.py: " + ", ".join(changed))
else:
    REPORT.append("qwen_api.py uz je OK")


for r in REPORT:
    print("*", r)
