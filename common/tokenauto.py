"""tokenauto — když chybí token, vytáhni ho z appky a ulož.

Volá `scripts/ensure_tokens.py`, který čte data appek pod reálným rootem
(v proot guestu `sudo`). Když to nejde, vrátí False a volající to ohlásí.

Použití:
    from common.tokenauto import ensure_token
    if not ensure_token(TOKEN_PATH):
        raise RuntimeError("token chybí a nedá se vytáhnout")
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "ensure_tokens.py")


def _has(path: str) -> bool:
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def ensure_token(token_path: str, timeout: int = 240) -> bool:
    """Zajistí, že token_path existuje a není prázdný. Když ne, zkusí ho vytáhnout."""
    if _has(token_path):
        return True
    if not os.path.exists(SCRIPT):
        print(f"[tokenauto] {SCRIPT} nenalezen", file=sys.stderr)
        return False
    print(f"[tokenauto] token {token_path} chybí -> vytahuji z appky", file=sys.stderr)
    try:
        r = subprocess.run([sys.executable, SCRIPT], timeout=timeout,
                           capture_output=True, text=True)
        if r.stdout:
            for line in r.stdout.strip().splitlines():
                print(f"[tokenauto] {line}", file=sys.stderr)
        if r.returncode != 0 and r.stderr:
            print(f"[tokenauto] {r.stderr.strip()[:300]}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[tokenauto] ensure_tokens selhalo: {e}", file=sys.stderr)
    return _has(token_path)
