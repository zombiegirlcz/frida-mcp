#!/bin/bash
# Najde python s fridou pro frida-mcp balíček.
frida_mcp_python() {
  local root="$1"
  for p in "$root/.venv/bin/python" "$root/deepseek/.venv/bin/python" "$root/qwen/.venv/bin/python"; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  command -v python3
}
