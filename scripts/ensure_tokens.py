#!/usr/bin/env python3
"""ensure_tokens — vytahne a ulozi pristupove tokeny z appek (DeepSeek, Qwen).

Idempotentni: kdyz token uz je a neni stary, nic nedela (--force prepsat).

    python3 scripts/ensure_tokens.py            # co chybi, to dotahni
    python3 scripts/ensure_tokens.py --force    # pregeneruj vse
    python3 scripts/ensure_tokens.py --check    # jen report, nic nemen

Odkud se bere:
  DeepSeek : /data/data/com.deepseek.chat/files/mmkv/mmkv.default  (klice key_user_info)
             -> deepseek/secrets/deepseek_token, deepseek/secrets/deepseek_uid
  Qwen     : /data/data/ai.qwenlm.chat.android/app_webview/Default/Cookies (cookie `token`)
             -> qwen/secrets/qwen_token

FRIDA SE TU NEPOUZIVA. Token staci sam — WAF hlavicky (x-mini-wua, app_waf)
nejsou potreba. Volitelny prepinac `--capture-headers` je jen diagnostika
(odposlech pres fridu) pro pripad, ze by se API zmenilo.

Cteni cizich app dat potrebuje realny root. V proot guestu byva `sudo` jen
fake-root (uid 0 uvnitr namespace), takze se k datum appky nedostane —
tehdy se pouzije fallback na host: `ashell -c '/product/bin/su -c "cat …"'`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mozne umisteni /data (host) z proot guestu
DATA_ROOTS = [
    os.environ.get("FRIDA_MCP_DATA", ""),
    "/mnt/data/data",          # bezny bind v NetHunter prootu
    "/data/data",              # kdyz /data neni bindnute
    "/mnt/data",               # host /data primountovane jinam
]

DEEPSEEK = {
    "pkg": "com.deepseek.chat",
    "mmkv": "files/mmkv/mmkv.default",
    "token": os.path.join(HERE, "deepseek", "secrets", "deepseek_token"),
    "uid": os.path.join(HERE, "deepseek", "secrets", "deepseek_uid"),
}
QWEN = {
    "pkg": "ai.qwenlm.chat.android",
    "activity": "ai.qwenlm.chat.android/.MainActivity",
    "cookies": "app_webview/Default/Cookies",
    "token": os.path.join(HERE, "qwen", "secrets", "qwen_token"),
    "headers": os.path.join(HERE, "qwen", "secrets", "qwen_headers.json"),
}


def log(*a):
    print("[tokens]", *a, flush=True)


def sudo_read(path: str) -> bytes:
    """Precte soubor, ktery je citelny jen pro realny root."""
    r = subprocess.run(["sudo", "cat", path], capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "sudo cat selhalo")
    if not r.stdout:
        raise RuntimeError("prazdny vystup")
    return r.stdout


def host_read(path: str) -> bytes:
    """Precte soubor z HOSTU pres `ashell` + `su`.

    POZOR: v proot guestu byva `sudo` jen **fake root** (uid 0 uvnitr
    namespace), takze se k datum appky nedostane — `sudo cat` vrati
    "Permission denied" nebo "No such file or directory", i kdyz soubor
    existuje. Hostitelske `su` je **realny root** a precte vse.
    """
    r = subprocess.run(
        ["ashell", "-c", f'/product/bin/su -c "cat {path}"'],
        capture_output=True, timeout=120)
    if r.returncode != 0:
        msg = (r.stderr or b"ashell cat selhalo").decode("utf-8", "replace")
        raise RuntimeError(msg.strip()[:140])
    if not r.stdout:
        raise RuntimeError("prazdny vystup")
    return r.stdout


def sudo_read_first(pkg: str, sub: str) -> tuple[bytes, str] | None:
    """Zkusi vsechna mozna umisteni /data a vrati (data, cesta).

    Pozor: os.path.exists() tady NELZE pouzit — data appek jsou citelna jen
    pod realnym rootem (`sudo`), takze z guestu se tvarí jako neexistujici.
    """
    last: str | None = None
    for root in DATA_ROOTS:
        if not root:
            continue
        p = os.path.join(root, pkg, sub)
        try:
            return sudo_read(p), p
        except Exception as e:  # noqa: BLE001
            last = f"{p}: {e}"
            continue
    # 2) FALLBACK: host pres ashell + su (guest sudo byva jen fake-root)
    hp = f"/data/data/{pkg}/{sub}"
    try:
        return host_read(hp), hp
    except Exception as e:  # noqa: BLE001
        last = f"{hp} (ashell): {e}"
    if last:
        log(f"  (zkouseno napr. {last})")
    return None


def fresh(path: str, max_age: float) -> bool:
    try:
        return (time.time() - os.path.getmtime(path)) < max_age
    except OSError:
        return False


def save(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
    os.chmod(path, 0o600)
    log(f"ulozene -> {os.path.relpath(path, HERE)} ({len(value)} B)")


def launch_app(activity: str) -> bool:
    """Spusti appku na hostu (pres ashell + su, protoze am potrebuje root)."""
    return run_host_am(f"am start -n {activity}", expect=b"Starting:")


def force_stop_app(pkg: str) -> bool:
    """Zabije appku, aby pri startu znovu poslala sve inicializacni requesty.

    To je klicove pro zachyceni WAF hlavicek: SecurityGuard je generuje
    pri pozadavku, takze hook musi byt na miste DRIV nez appka ten pozadavek
    posle. Kdyz appka uz bezi, jeji startup requesty uz probehly a chytime jen
    "prazdne" hlavicky.
    """
    return run_host_am(f"am force-stop {pkg}", expect=b"")


def run_host_am(args: str, expect: bytes = b"") -> bool:
    for cmd in (
        ["ashell", "-c", f'/product/bin/su -c "{args}"'],
        ["su", "-c", args],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=90)
            blob = (r.stdout or b"") + (r.stderr or b"")
            if r.returncode == 0 and (not expect or expect in blob):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def headers_complete(path: str) -> bool:
    """Jsou v cache obe klicove WAF hlavicky?"""
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return bool(d.get("x-mini-wua")) and bool(d.get("app_waf"))


def python_for_helpers() -> str:
    """Python, kterym jde spustit frida helpery (venv, jinak sys.executable)."""
    for c in (
        os.path.join(HERE, ".venv", "bin", "python"),
        os.path.join(HERE, "deepseek", ".venv", "bin", "python"),
        os.path.join(HERE, "qwen", ".venv", "bin", "python"),
    ):
        if os.path.exists(c):
            return c
    return sys.executable


# ---------------------------------------------------------------- DeepSeek

def deepseek_token(force: bool, check: bool) -> bool:
    tok_file = DEEPSEEK["token"]
    if not force and fresh(tok_file, 3600 * 12):
        log("deepseek: token je cerstvy, preskakuji")
        return True
    src = sudo_read_first(DEEPSEEK["pkg"], DEEPSEEK["mmkv"])
    if src is None:
        log("deepseek: MMKV nenalezeno (je appka nainstalovana? bezel uz nekdy?)")
        return os.path.exists(tok_file)
    raw, srcpath = src
    log(f"deepseek: ctu {srcpath}")
    text = raw.decode("utf-8", "replace")
    # v MMKV je JSON uzivatele v plaintextu:
    #   key_user_info\x81\x04\xff\x03{"token":"<base64 60 znaku>","id":"<uuid>",...}
    m = re.search(r'"token"\s*:\s*"([A-Za-z0-9+/=_.-]{20,})"', text)
    if not m:
        log("deepseek: v MMKV jsem token nenasel (zkus se v appce odhlasit/prihlasit)")
        return os.path.exists(tok_file)
    tok = m.group(1)
    uid = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{16,})"', text)
    if check:
        log(f"deepseek: nasel bych token {tok[:10]}… ({len(tok)} B)")
        return True
    save(tok_file, tok)
    if uid:
        save(DEEPSEEK["uid"], uid.group(1))
    return True


# ---------------------------------------------------------------- Qwen

def qwen_token(force: bool, check: bool) -> bool:
    tok_file = QWEN["token"]
    if not force and fresh(tok_file, 3600 * 12):
        log("qwen: token je cerstvy, preskakuji")
        return True
    src = sudo_read_first(QWEN["pkg"], QWEN["cookies"])
    if src is None:
        log("qwen: Cookies DB nenalezeno (je appka nainstalovana?)")
        return os.path.exists(tok_file)
    blob, srcpath = src
    log(f"qwen: ctu {srcpath}")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as t:
        t.write(blob)
        tmp = t.name
    try:
        con = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT value FROM cookies WHERE name='token' AND host_key LIKE '%qwen%' "
            "ORDER BY length(value) DESC"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        log(f"qwen: sqlite chyba: {e}")
        return os.path.exists(tok_file)
    finally:
        os.unlink(tmp)
    if not rows:
        log("qwen: cookie `token` neni — prihlas se v Qwen appce (anonymni rezim ma denni limit)")
        return os.path.exists(tok_file)
    tok = rows[0][0]
    if check:
        log(f"qwen: nasel bych token {tok[:14]}… ({len(tok)} B)")
        return True
    save(tok_file, tok)
    return True


def qwen_headers(force: bool, check: bool) -> bool:
    """VOLITELNA DIAGNOSTIKA: WAF hlavicky (x-mini-wua, app_waf) pro chat.qwen.ai.

    NENI potreba pro provoz — Qwen staci token (overeno). Tohle je jen pro
    pripad, ze by se API zmenilo, aby se dalo zachytit, co appka posila navic.

    Nejsou to tokeny — generuje je Aliyun SecurityGuard uvnitr appky pri
    kazdem pozadavku. Zachyti je frida (agent/qwen_hdr.js hookuje SSL_write)
    a ulozi do qwen/secrets/qwen_headers.json, odkud je bere qwen_api.py.

    Spousti se POUZE s `--capture-headers`.
    """
    hdr_file = QWEN["headers"]
    if not force and fresh(hdr_file, 3600 * 24 * 7) and headers_complete(hdr_file):
        log("qwen: WAF hlavicky jsou cerstve, preskakuji")
        return True
    if check:
        ok = os.path.exists(hdr_file) and headers_complete(hdr_file)
        log(f"qwen: WAF hlavicky {'OK' if ok else 'CHYBI/neuplne'}")
        return ok

    daemon = os.path.join(HERE, "qwen", "bridge", "qwen_hdrd.py")
    if not os.path.exists(daemon):
        log("qwen: chybi qwen/bridge/qwen_hdrd.py")
        return os.path.exists(hdr_file)

    # frida-server musi bezet
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 27042), timeout=3):
            pass
    except OSError:
        log("qwen: frida-server nebezi na 27042 -> hlavicky nezachytim")
        return os.path.exists(hdr_file)

    # 1) zabij appku, aby pri startu poslala requesty ZNOVU (a az po nasem hooku)
    log("qwen: zastavuji appku, aby vygenerovala hlavicky znovu")
    force_stop_app(QWEN["pkg"])
    time.sleep(2)

    # 2) nastartuj daemon — pocka si na proces a attachne se hned, jak vznike
    proc = subprocess.Popen(
        [python_for_helpers(), daemon, "--once", "--timeout", "75"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    # dej mu chvili na inicializaci fridy, at hook existuje driv nez appka promluvi
    time.sleep(4)

    # 3) teprve ted spust appku
    log("qwen: spoustim appku")
    launch_app(QWEN["activity"])

    try:
        out, _ = proc.communicate(timeout=110)
        for line in (out or "").splitlines()[-5:]:
            log("  " + line)
    except subprocess.TimeoutExpired:
        proc.kill()
        log("qwen: capture vyprsel cas")

    if headers_complete(hdr_file):
        log("qwen: WAF hlavicky zachyceny ✅")
        return True
    log("qwen: hlavicky se nepodarilo zachytit — otevri Qwen appku a posli zpravu")
    return os.path.exists(hdr_file)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="prepsat i cerstve tokeny")
    ap.add_argument("--check", action="store_true", help="jen zjistit, nic neukladat")
    ap.add_argument("--capture-headers", action="store_true",
                    help="navic zkusit zachytit WAF hlavicky pres fridu "
                         "(NENI potreba — Qwen staci token; zbytek jsou jen bonusova metadata)")
    a = ap.parse_args()
    results = [deepseek_token(a.force, a.check), qwen_token(a.force, a.check)]
    if a.capture_headers:
        results.append(qwen_headers(a.force, a.check))
    else:
        log("qwen: WAF hlavicky neresim (Qwen staci token)")
    if not all(results):
        log("POZOR: ne vsechno se podarilo zajistit")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
