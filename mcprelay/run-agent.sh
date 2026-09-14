#!/bin/bash
# Spusti telefonni agent pro mcprelay (dlouhy beh, log do mcprelay/agent.log).
#   ./run-agent.sh            # popredi
#   setsid nohup ./run-agent.sh &   # na pozadi
cd "$(dirname "$0")"
TOKEN="$(cat .relay_token 2>/dev/null)"
export RELAY_URL="${RELAY_URL:-https://mcprelay.onrender.com}"
export RELAY_TOKEN="${RELAY_TOKEN:-$TOKEN}"
exec python3 -u agent.py
