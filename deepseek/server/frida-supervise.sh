#!/system/bin/sh
# frida-server supervision loop (frida-mcp)
LOG=/data/local/tmp/frida-server.log
while true; do
  echo "[$(date)] starting frida-server" >> "$LOG"
  /data/local/tmp/frida-server -l 127.0.0.1:27042 >> "$LOG" 2>&1
  echo "[$(date)] frida-server exited rc=$?, restart in 1s" >> "$LOG"
  sleep 1
done
