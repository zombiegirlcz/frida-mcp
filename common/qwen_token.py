#!/usr/bin/env python3
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
