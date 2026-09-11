#!/system/bin/sh
# Zastav supervisor loop (sh running frida-supervise.sh) PRVNI, pak frida-server.
ME=$$
for p in $(ps -A -o PID,ARGS 2>/dev/null | grep 'frida-supervise.sh' | grep -v grep | awk '{print $1}'); do
  [ "$p" = "$ME" ] && continue
  kill -9 "$p" 2>/dev/null
done
sleep 1
killall -9 frida-server 2>/dev/null
sleep 1
echo "--- zbyle frida procesy ---"
ps -A -o PID,NAME | grep -i frida | grep -v grep || echo "(zadne)"
