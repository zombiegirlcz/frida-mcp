#!/usr/bin/env python3
"""pow_native — DeepSeekHashV1 PoW bez fridy, bez appky, bez site.

DeepSeekHashV1 je SHA3-256 s jedinou zmenou: **prvni kolo permutace
Keccak-f[1600] se preskoci**. Vse ostatni je standardni SHA3-256
(rate 136, padding 0x06, 24 RC konstant, 25x64bit stav).

Overeno proti realnemu vystupu nativni funkce v librscrypto.so:
    msg = "6fe4581ae0fcf306e50d_1789139155175_35873"
    -> 886a0939c788d0ef9b2ef84e5b98c48494fe2474ea3f62cc5abe036879415a3a

Pouziti:
    from deepseek.bridge import pow_native
    nonce = pow_native.solve(salt, expire_at, challenge_hex, difficulty)

Rychlost: C knihovna (~1.2M hash/s) -> cely PoW ~0,1 s.
Fallback: pure Python (~10k hash/s) -> ~15 s (kdyz neni gcc).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE_DIR = os.path.normpath(os.path.join(HERE, "..", "native"))
C_SRC = os.path.join(NATIVE_DIR, "dspow.c")
SO_PATH = os.path.join(NATIVE_DIR, "libdspow.so")

_lib = None
_lib_tried = False


# --------------------------------------------------------------- C knihovna

def _build() -> str | None:
    """Zkompiluje libdspow.so, pokud chybi (a je cim)."""
    if not os.path.exists(C_SRC):
        return None
    cc = shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if not cc:
        return None
    out = SO_PATH
    try:
        os.makedirs(NATIVE_DIR, exist_ok=True)
        tmp = out + ".tmp"
        r = subprocess.run(
            [cc, "-O3", "-shared", "-fPIC", "-o", tmp, C_SRC],
            capture_output=True, timeout=180,
        )
        if r.returncode != 0:
            return None
        os.replace(tmp, out)
        return out
    except Exception:  # noqa: BLE001
        return None


def lib():
    """Vrati nacitnou C knihovnu (nebo None -> pouzijeme Python)."""
    global _lib, _lib_tried
    if _lib is not None:
        return _lib
    if _lib_tried:
        return None
    _lib_tried = True
    for path in (SO_PATH,):
        if not os.path.exists(path):
            path = _build()
        if not path or not os.path.exists(path):
            continue
        try:
            h = ctypes.CDLL(path)
            h.deepseek_solve.argtypes = [
                ctypes.c_char_p, ctypes.c_longlong, ctypes.c_char_p,
                ctypes.c_longlong, ctypes.c_longlong,
            ]
            h.deepseek_solve.restype = ctypes.c_longlong
            h.deepseek_hash_str.argtypes = [
                ctypes.c_char_p, ctypes.c_longlong, ctypes.c_char_p,
            ]
            _lib = h
            return _lib
        except Exception:  # noqa: BLE001
            continue
    return None


# ------------------------------------------------------------ pure Python

_M = (1 << 64) - 1
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808a,
    0x8000000080008000, 0x000000000000808b, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008a,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
    0x000000008000808b, 0x800000000000008b, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800a, 0x800000008000000a, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
# rotace pro lane (x + 5*y)
_ROT = [
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
]
_RATE = 136


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _keccak_skip_first(s: list[int]) -> None:
    """Keccak-f[1600] bez kola 0."""
    for rnd in range(1, 24):
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                i = x + 5 * y
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(s[i], _ROT[i])
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] = (b[x + 5 * y]
                                ^ ((~b[((x + 1) % 5) + 5 * y]) & _M)
                                & b[((x + 2) % 5) + 5 * y])
        s[0] ^= _RC[rnd]


def deepseek_hash_py(data: bytes) -> bytes:
    s = [0] * 25
    m = bytearray(data)
    m.append(0x06)
    while len(m) % _RATE:
        m.append(0)
    m[-1] ^= 0x80
    for off in range(0, len(m), _RATE):
        blk = m[off:off + _RATE]
        for i in range(_RATE // 8):
            s[i] ^= int.from_bytes(blk[8 * i:8 * i + 8], "little")
        _keccak_skip_first(s)
    return b"".join(x.to_bytes(8, "little") for x in s[:4])


# ----------------------------------------------------------------- verejne

def deepseek_hash(data: bytes) -> bytes:
    """DeepSeekHashV1(data) -> 32 bajtu."""
    h = lib()
    if h is not None:
        out = ctypes.create_string_buffer(32)
        h.deepseek_hash_str(data, len(data), out)
        return out.raw
    return deepseek_hash_py(data)


def solve(salt: str, expire_at, challenge_hex: str, difficulty: int,
          start: int = 0) -> int:
    """Najde nonce, pro ktery hash(f"{salt}_{expire_at}_{nonce}") == challenge.

    Vraci nonce nebo -1 (nenalezeno).
    """
    prefix = f"{salt}_{expire_at}_".encode()
    try:
        target = bytes.fromhex(challenge_hex)
    except ValueError:
        return -1
    difficulty = int(difficulty)

    h = lib()
    if h is not None:
        ans = h.deepseek_solve(prefix, len(prefix), target, difficulty, start)
        return int(ans)

    # fallback: pure Python
    for n in range(start, difficulty):
        if deepseek_hash_py(prefix + str(n).encode()) == target:
            return n
    return -1


def backend() -> str:
    """Ktery backend se pouzije ('c' nebo 'python')."""
    return "c" if lib() is not None else "python"


if __name__ == "__main__":
    import time

    msg = b"6fe4581ae0fcf306e50d_1789139155175_35873"
    want = "886a0939c788d0ef9b2ef84e5b98c48494fe2474ea3f62cc5abe036879415a3a"
    got = deepseek_hash(msg).hex()
    print(f"backend: {backend()}")
    print(f"hash   : {got}")
    print(f"ocekav.: {want}")
    print("hash test:", "OK" if got == want else "CHYBA")

    # self-test solveru: najdi nonce, jehoz hash zname
    salt, expire, _ = msg.decode().split("_")
    n_known = 4242
    target = deepseek_hash(f"{salt}_{expire}_{n_known}".encode())
    t0 = time.time()
    found = solve(salt, expire, target.hex(), 20000)
    dt = time.time() - t0
    print(f"solve  : hledano {n_known} -> {found}  ({dt:.3f} s)")
    print("solve test:", "OK" if found == n_known else "CHYBA")

    t0 = time.time()
    solve(salt, expire, target.hex(), 20000)
    print(f"rychlost: {20000/(time.time()-t0):,.0f} hash/s")
