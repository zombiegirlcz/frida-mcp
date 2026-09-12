#!/bin/bash
# bootstrap.sh — pripravi prostredi pro frida-mcp.
#
#   bash scripts/bootstrap.sh
#
# Od verze s NATIVNIM DeepSeekHashV1 uz NENI potreba frida ani zadna pip
# knihovna: oba shimy jedou na cistem Pythonu 3.11+ stdlib.
#
# Co skript dela:
#   1. overi, ze existuje pouzitelny python3
#   2. zkompiluje deepseek/native/libdspow.so (gcc/cc/clang) — tim je PoW
#      hotovy za ~0,1 s misto ~70 s v Pythonu
#   3. (nepovinne) kdyz je FRIDA_MCP_WITH_FRIDA=1, vytvori .venv s fridou
#      pro LEGACY cesty (bridge/powd.py, agenti pro bard/, qwen capture)
#
# Idempotentni.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="${PYTHON:-python3}"
CC_TRY=(gcc cc clang)

log() { echo "[bootstrap] $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 1) python ----------------------------------------------------------
if ! have "$PY"; then
  log "CHYBA: '$PY' nenalezen. Nainstaluj python3 (3.11+)."
  exit 1
fi
log "python: $("$PY" -c 'import sys;print(sys.executable, sys.version.split()[0])')"

# ---- 2) nativni PoW (C) ------------------------------------------------
C_SRC="$ROOT/deepseek/native/dspow.c"
C_SO="$ROOT/deepseek/native/libdspow.so"
if [ -f "$C_SO" ]; then
  log "nativni PoW uz je zkompilovany: $C_SO"
elif [ -f "$C_SRC" ]; then
  built=0
  for cc in "${CC_TRY[@]}"; do
    have "$cc" || continue
    if "$cc" -O3 -shared -fPIC -o "$C_SO.tmp" "$C_SRC" >/dev/null 2>&1; then
      mv "$C_SO.tmp" "$C_SO"
      log "nativni PoW zkompilovan pres '$cc' — cely PoW ~0,1 s"
      built=1
      break
    fi
  done
  if [ "$built" = 0 ]; then
    log "POZOR: zadny C kompilator (gcc/cc/clang) -> PoW pobezi v Pythonu."
    log "       Je to funkcni, ale pomale (~1-2 min). Instaluj: apt install gcc"
  fi
else
  log "POZOR: chybi $C_SRC — PoW pobezi v Pythonu (pomale)"
fi

# ---- 3) frida (uz jen nepovinne, pro legacy cesty) ----------------------
if [ "${FRIDA_MCP_WITH_FRIDA:-0}" != "1" ]; then
  log "frida se neinstaluje (uz ji nepotrebujeme; FRIDA_MCP_WITH_FRIDA=1 ji vynuti)"
  exit 0
fi

FRIDA_VER="${FRIDA_VERSION:-17.18.0}"
ok() { [ -x "$1" ] && "$1" -c "import frida" 2>/dev/null; }
if ok "$VENV/bin/python"; then
  log "frida uz je: $("$VENV/bin/python" -c 'import frida;print(frida.__version__)')"
  exit 0
fi

if have uv; then
  log "instaluji fridu $FRIDA_VER pres uv"
  rm -rf "$VENV"
  if uv venv "$VENV" >/dev/null 2>&1 \
     && uv pip install --python "$VENV/bin/python" "frida==$FRIDA_VER" >/dev/null 2>&1 \
     && ok "$VENV/bin/python"; then
    log "hotovo: $("$VENV/bin/python" -c 'import frida;print(frida.__version__)')"
    exit 0
  fi
fi

if "$PY" -m venv "$VENV" >/dev/null 2>&1 || "$PY" -m venv --without-pip "$VENV" >/dev/null 2>&1; then
  if [ ! -x "$VENV/bin/pip" ] && have curl; then
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py 2>/dev/null \
      && "$VENV/bin/python" /tmp/get-pip.py --quiet >/dev/null 2>&1 || true
  fi
  "$VENV/bin/python" -m pip install --quiet "frida==$FRIDA_VER" >/dev/null 2>&1 || true
  if ok "$VENV/bin/python"; then
    log "hotovo: $("$VENV/bin/python" -c 'import frida;print(frida.__version__)')"
    exit 0
  fi
fi

log "fridu se nepodarilo nainstalovat (nevadi — legacy cesty proste nepujdou)"
exit 0
