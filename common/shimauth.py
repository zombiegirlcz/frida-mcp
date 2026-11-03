"""Volitelna autentizace lokalnich shimu (Bearer token).

Shim byl dosud jen na `127.0.0.1` a API klic ignoroval (`apiKey: "frida"`
v models.json byl formalita). Kdyz se ale shim vystavi na sit (jiny
stroj/kontejner), musi se dat zamknout — jinak komukoli na siti otevres
DeepSeek/Qwen ucet a PoW.

Zapnuti:
    SHIM_API_KEY=tajne123 ./openai_shim.py --host 0.0.0.0

Klient pak posila:
    Authorization: Bearer tajne123

Kdyz `SHIM_API_KEY` neni nastaven, kontrola se **preskoci** (zpetna
kompatibilita — lokalni pi posila `apiKey: "frida"`, coz je nesmyslny
klic, ale na loopbacku to nikomu nevadi).

`/health` je vzdy bez klice, aby se dal shim healthcheckovat.
"""

from __future__ import annotations

import hmac
import os

# Cesty, ktere nikdy nevyzaduji klic (healthcheck z jinych nastroju).
PUBLIC_PATHS = ("/health", "/healthz", "/ping")


def api_key() -> str | None:
    """Nastaveny klic, nebo None kdyz je auth vypnuta."""
    key = os.environ.get("SHIM_API_KEY", "").strip()
    return key or None


def bind_host(default: str = "127.0.0.1") -> str:
    """Na jakou adresu shim poslouchat.

    `SHIM_HOST` (nebo `FRIDA_MCP_BIND`) prebiji default. Pro sit se pouziva
    `0.0.0.0`; porad to ale chce `SHIM_API_KEY`, jinak je shim otevreny.
    """
    return (os.environ.get("SHIM_HOST")
            or os.environ.get("FRIDA_MCP_BIND")
            or default).strip()


def path_only(path: str) -> str:
    """Cesta bez query stringu (`/v1/models?x=1` -> `/v1/models`)."""
    return (path or "").split("?", 1)[0].rstrip("/") or "/"


def is_public(path: str) -> bool:
    p = path_only(path)
    return any(p == x or p.endswith(x) for x in PUBLIC_PATHS)


def check_header(auth_header: str | None) -> tuple[bool, str]:
    """Overi `Authorization` hlavicku.

    Vraci (ok, duvod). Kdyz auth neni nastavena, vraci (True, "auth-off").
    """
    expected = api_key()
    if expected is None:
        return True, "auth-off"
    if not auth_header:
        return False, "chybi Authorization hlavicka"
    value = auth_header.strip()
    low = value.lower()
    if low.startswith("bearer "):
        got = value[7:].strip()
    elif low.startswith("basic "):
        # nekteri klienti posilaji Basic; nepodporujeme
        return False, "podporovan je jen Bearer token"
    else:
        got = value
    # konstantni cas — chovani ma byt spravne i na lokalni siti
    if hmac.compare_digest(got, expected):
        return True, "ok"
    return False, "neplatny API klic"


def check_request(path: str, auth_header: str | None) -> tuple[bool, str]:
    """Overi request; verejne cesty propusti vzdy."""
    if is_public(path):
        return True, "public"
    return check_header(auth_header)