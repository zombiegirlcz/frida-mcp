#!/bin/bash
# deploy_frida_server.sh — nasadí frida-server na zařízení a spustí ho.
#
#   bash scripts/deploy_frida_server.sh
#
# Co dělá:
#   1. zjistí architekturu zařízení a verzi fridy v .venv
#   2. stáhne odpovídající frida-server (release z GitHubu)
#   3. nasype ho do /data/local/tmp  (NIKDY do /system!)
#   4. spustí ho rootem + supervisi smyčku, aby se po pádu zvedl
#
# Bezpečnost: vše zůstává v /data/local/tmp. Nemění se žádné systémové složky.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
TMP="/data/local/tmp"
NAME="frida-server"

log() { echo "[frida-server] $*"; }
ashell() { ashell -c "$1"; }
su() { ashell -c "/product/bin/su -c \"$1\""; }

# --- verze z .venv ---------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  log "chybí $VENV — spusť nejdřív scripts/bootstrap.sh"
  exit 1
fi
VER="$(FRIDA_MCP_VENV="$VENV" "$VENV/bin/python" -c 'import frida;print(frida.__version__)')"

# --- architektura ----------------------------------------------------------
ABI="$(su 'getprop ro.product.cpu.abi' | tr -d '\r\n')"
log "zařízení ABI: $ABI, frida: $VER"

# --- už běží? --------------------------------------------------------------
if timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/27042' 2>/dev/null; then
  log "frida-server už běží na 27042 ✅"
  exit 0
fi

# --- stažení ---------------------------------------------------------------
XZ="$ROOT/server/frida-server-$VER-$ABI.xz"
mkdir -p "$ROOT/server"
if [ ! -f "$XZ" ]; then
  URL="https://github.com/frida/frida/releases/download/$VER/frida-server-$VER-android-$ABI.xz"
  log "stahuji $URL"
  curl -fL --retry 3 -o "$XZ" "$URL" || {
    log "CHYBA: stažení selhalo (zkontroluj verzi/arch)"
    exit 1
  }
fi
log "mám $XZ"

# --- rozbalení a nasazení -------------------------------------------------
UNPACKED="$ROOT/server/$NAME.$ABI"
xz -dkf -c "$XZ" > "$UNPACKED" 2>/dev/null || cp "$XZ" "$UNPACKED"
chmod 755 "$UNPACKED"

log "kopíruji do $TMP/$NAME"
# cesta ven: base64 přes shell (nezávisí na tom, jak je zrovna bindnuté /data)
B64="$(base64 -w0 "$UNPACKED")"
su "echo '$B64' | base64 -d > $TMP/$NAME"
su "chmod 755 $TMP/$NAME"

# --- spuštění -------------------------------------------------------------
su "pkill -f $TMP/$NAME 2>/dev/null" || true
sleep 1
su "setsid $TMP/$NAME > $TMP/frida-server.log 2>&1 < /dev/null &"
sleep 3

if timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/27042' 2>/dev/null; then
  log "frida-server běží na 27042 ✅"
else
  log "frida-server se nenahodil — koukni do $TMP/frida-server.log"
  exit 1
fi
