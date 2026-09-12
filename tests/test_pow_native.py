#!/usr/bin/env python3
"""Testy pro DeepSeekHashV1 (nativni PoW bez fridy).

Overuje:
1. hash proti REALNEMU vystupu nativni funkce z librscrypto.so
2. ze DeepSeekHashV1 NENI SHA3-256 (aby se to nepleto)
3. ze solver najde spravny nonce
4. ze C a Python backend davaji stejny vysledek

    python3 tests/test_pow_native.py
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "deepseek"))

import hashlib  # noqa: E402

from bridge import pow_native  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"✅ {label}")
    else:
        FAILED.append(label)
        print(f"❌ {label}  {detail}")


# Vzorek z realne session: vstup -> vystup nativni funkce librscrypto.so
MSG = b"6fe4581ae0fcf306e50d_1789139155175_35873"
NATIVE_DIGEST = "886a0939c788d0ef9b2ef84e5b98c48494fe2474ea3f62cc5abe036879415a3a"

# 1) hash proti realnemu nativnimu vystupu
got = pow_native.deepseek_hash(MSG).hex()
check("hash == nativni vystup z librscrypto.so", got == NATIVE_DIGEST,
      f"\n     got : {got}\n     want: {NATIVE_DIGEST}")

# 2) neni to SHA3-256 (kdyby ano, byla by to jen nahoda / spatny predpoklad)
check("DeepSeekHashV1 != SHA3-256",
      got != hashlib.sha3_256(MSG).hexdigest())

# 3) Python backend dava stejny vysledek jako C
check("C backend == Python backend",
      pow_native.deepseek_hash_py(MSG).hex() == NATIVE_DIGEST,
      f"py={pow_native.deepseek_hash_py(MSG).hex()}")

# 4) solver najde nonce, ktery jsme si sami vyrobili
salt, expire, _ = MSG.decode().split("_")
for n_known in (0, 1, 4242, 99999):
    target = pow_native.deepseek_hash(f"{salt}_{expire}_{n_known}".encode())
    t0 = time.time()
    found = pow_native.solve(salt, expire, target.hex(), n_known + 1)
    dt = time.time() - t0
    check(f"solve najde nonce {n_known} ({dt*1000:.1f} ms)", found == n_known,
          f"found={found}")

# 5) solver vrati -1, kdyz nonce neexistuje
target = pow_native.deepseek_hash(f"{salt}_{expire}_123456".encode())
check("solve vrati -1 kdyz nic nenajde",
      pow_native.solve(salt, expire, target.hex(), 1000) == -1)

# 6) spatny challenge hex nesmi shodit
check("neplatny challenge hex -> -1",
      pow_native.solve(salt, expire, "nonsense", 100) == -1)

print()
print(f"backend: {pow_native.backend()}")
if pow_native.backend() == "c":
    t0 = time.time()
    pow_native.solve(salt, expire, pow_native.deepseek_hash(b"x").hex(), 144000)
    rate = 144000 / (time.time() - t0)
    print(f"rychlost: {rate:,.0f} hash/s  (cely PoW ~{144000/rate:.2f} s)")

if FAILED:
    print(f"\nSELHALO: {len(FAILED)} — {', '.join(FAILED)}")
    raise SystemExit(1)
print("\nVsechny testy prosly ✅")
