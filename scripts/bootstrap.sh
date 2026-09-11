#!/bin/bash
# bootstrap.sh — vytvoří Python prostředí s fridou pro frida-mcp.
#
#   bash scripts/bootstrap.sh
#
# Zkouší postupně (první co projde, vyhraje):
#   1. uv venv + uv pip install      (nejrychlejší, nepotřebuje ensurepip)
#   2. python3 -m venv + pip
#   3. python3 -m venv --without-pip + get-pip.py
#   4. pip3 install --user frida     (krajní případ, bez venv)
#
# Idempotentní: když už frida jde importovat, hned skončí.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="${PYTHON:-python3}"
FRIDA_VER="${FRIDA_VERSION:-17.18.0}"

log() { echo "[bootstrap] $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

ok() { [ -x "$1" ] && "$1" -c "import frida" 2>/dev/null; }

# už hotovo?
if ok "$VENV/bin/python"; then
  log "už hotovo: $VENV ($("$VENV/bin/python" -c 'import frida;print(frida.__version__)'))"
  exit 0
fi

# ---- 1) uv ---------------------------------------------------------------
if have uv; then
  log "zkouším uv (verze $FRIDA_VER)"
  rm -rf "$VENV"
  if uv venv "$VENV" >/dev/null 2>&1 && \
     uv pip install --python "$VENV/bin/python" "frida==$FRIDA_VER" >/dev/null 2>&1 && \
     ok "$VENV/bin/python"; then
    log "hotovo přes uv: $("$VENV/bin/python" -c 'import frida;print(frida.__version__)')"
    exit 0
  fi
  log "uv cesta nevyšla, zkouším dál"
fi

# ---- 2/3) stdlib venv ----------------------------------------------------
if have "$PY"; then
  log "zkouším $PY -m venv"
  rm -rf "$VENV"
  if "$PY" -m venv "$VENV" >/dev/null 2>&1; then
    :
  elif "$PY" -m venv --without-pip "$VENV" >/dev/null 2>&1; then
    log "ensurepip chybí → doinstaluji pip přes get-pip.py"
    if curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py 2>/dev/null; then
      "$VENV/bin/python" /tmp/get-pip.py --quiet >/dev/null 2>&1 || true
    fi
  fi
  if [ -x "$VENV/bin/python" ]; then
    "$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
    if "$VENV/bin/python" -m pip install --quiet "frida==$FRIDA_VER" >/dev/null 2>&1 \
       && ok "$VENV/bin/python"; then
      log "hotovo přes venv: $("$VENV/bin/python" -c 'import frida;print(frida.__version__)')"
      exit 0
    fi
    log "venv cesta nevyšla"
  fi
fi

# ---- 4) --user bez venv --------------------------------------------------
if have pip3; then
  log "poslední pokus: pip3 install --user frida==$FRIDA_VER"
  if pip3 install --quiet --user "frida==$FRIDA_VER" >/dev/null 2>&1 \
     && "$PY" -c "import frida" 2>/dev/null; then
    log "hotovo do uživatelských balíčků: $("$PY" -c 'import frida;print(frida.__version__)')"
    log "POZN.: extension používá $PY (bez venv)"
    exit 0
  fi
fi

log "CHYBA: nepodařilo se nainstalovat fridu."
log "Zkus ručně:"
log "  apt install python3-venv    (nebo python3.13-venv)"
log "  curl -LsSf https://astral.sh/uv/install.sh | sh"
exit 1
