"""netfix — odolný DNS pro proot guest.

V proot guestu občas selže resolvování:

    <urlopen error [Errno -3] Temporary failure in name resolution>

Typicky po přepnutí WiFi→data, po restartu netd, nebo když je
`/etc/resolv.conf` rozbitý. Jeden takový výkyv ale **shodil celý tah
agenta** — shim vrátil chybu jako obsah zprávy a model se zacyklil
(v reálné session se to opakovalo 6× za sebou).

`getaddrinfo()` proto obalíme:

1. **cache** (host → výsledek) — po prvním úspěchu už DNS nepotřebujeme
2. **opakování** s prodlevou (3 pokusy, 0,3 / 0,9 / 2,7 s)
3. **fallback** na naposledy úspěšný výsledek, když DNS selže
4. **persistentní cache** (`logs/dns_cache.json`) — přežije restart shimu
   i `pi update`, který smaže `logs/` (pak se prostě znovu resolvne)

Použití (na začátku shimu):

    from common.netfix import install_dns_cache
    install_dns_cache()
"""

from __future__ import annotations

import json
import os
import socket
import time

# kolikrát to zkusit a jak dlouho čekat mezi pokusy
RETRIES = 3
DELAYS = (0.3, 0.9, 2.7)

_CACHE: dict[str, list] = {}
_CACHE_FILE: str | None = None
_installed = False
_orig_getaddrinfo = None

# chyby, které znamenají "DNS teď neodpovídá" (ne "hostname neexistuje")
_TRANSIENT = (
    "temporary failure in name resolution",
    "name or service not known",
    "no address associated with hostname",
    "servname not supported",
    "resource temporarily unavailable",
    "try again",
)


def _is_transient(e: BaseException) -> bool:
    low = str(e).lower()
    return any(k in low for k in _TRANSIENT)


def _key(host, port, family, type_, proto) -> str:
    return f"{host}|{port}|{family}|{type_}|{proto}"


def _deep_tuple(x):
    """JSON vraci seznamy — socket ale chce TUPLE (i ve vnorene sockaddr).

    Bez toho by cache po restartu shimu vratila `[['1.2.3.4', 443]]` misto
    `[('1.2.3.4', 443)]` a `socket.connect()` by spadl na TypeError.
    """
    if isinstance(x, list):
        return tuple(_deep_tuple(i) for i in x)
    return x


def load_cache(path: str | None) -> None:
    """Načte persistentní cache (když existuje)."""
    global _CACHE_FILE, _CACHE
    _CACHE_FILE = path
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list) and v:
                    # getaddrinfo vraci LIST tuplu: vnejsi obal musi zustat list,
                    # vnitrni (vc. sockaddr) zase tuple.
                    _CACHE[k] = [_deep_tuple(item) for item in v]
    except Exception:  # noqa: BLE001
        pass


def save_cache() -> None:
    """Uloží cache na disk (atomic, chmod 600)."""
    if not _CACHE_FILE:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: [list(x) for x in v] for k, v in _CACHE.items()}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _CACHE_FILE)
    except Exception:  # noqa: BLE001
        pass


def resolve(host, port, family=0, type_=0, proto=0, flags=0):
    """getaddrinfo s cache + opakováním + fallbackem (viz docstring modulu)."""
    key = _key(host, port, family, type_, proto)

    last: BaseException | None = None
    for i in range(RETRIES):
        try:
            res = _orig_getaddrinfo(host, port, family, type_, proto, flags)
            if res:
                if _CACHE.get(key) != res:
                    _CACHE[key] = res
                    save_cache()
                return res
        except BaseException as e:  # noqa: BLE001
            last = e
            if not _is_transient(e):
                raise
            if i < RETRIES - 1:
                time.sleep(DELAYS[min(i, len(DELAYS) - 1)])

    # DNS neodpovídá -> použij naposledy úspěšný výsledek
    if key in _CACHE:
        return _CACHE[key]
    # zkus ještě "jakýkoli" záznam pro tenhle host (jiný port/rodina)
    prefix = f"{host}|"
    for k, v in _CACHE.items():
        if k.startswith(prefix) and v:
            return v
    raise last if last else socket.gaierror(-3, "Temporary failure in name resolution")


def install_dns_cache(cache_file: str | None = None) -> None:
    """Nainstaluje cache nad socket.getaddrinfo (idempotentně)."""
    global _installed, _orig_getaddrinfo
    if _installed:
        return
    _orig_getaddrinfo = socket.getaddrinfo

    def patched(host, port, family=0, type_=0, proto=0, flags=0):
        # IP adresy a prázdný host neřešíme
        if not host or _looks_like_ip(host):
            return _orig_getaddrinfo(host, port, family, type_, proto, flags)
        return resolve(host, port, family, type_, proto, flags)

    socket.getaddrinfo = patched
    _installed = True
    if cache_file:
        load_cache(cache_file)


def _looks_like_ip(host: str) -> bool:
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        return ":" in host and all(c in "0123456789abcdefABCDEF:." for c in host)
