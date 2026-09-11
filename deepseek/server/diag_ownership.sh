#!/system/bin/sh
for pkg in com.deepseek.chat com.google.android.apps.bard ai.qwenlm.chat.android com.anthropic.claude; do
  uid=$(pm list packages -U 2>/dev/null | grep "package:$pkg " | sed 's/.*uid://')
  echo "############ $pkg (uid=$uid) ############"
  echo "-- /data/data/$pkg (top):"
  ls -land /data/data/$pkg 2>&1
  echo "-- polozky 1. urovne s jinym ownerem:"
  ls -lan /data/data/$pkg 2>/dev/null | awk -v u="$uid" 'NR>1 && $3!=u {print}'
  echo "-- recursive pocet (maxdepth 4) s ownerem != $uid:"
  find /data/data/$pkg -maxdepth 4 ! -uid "$uid" 2>/dev/null | head -8
  echo "-- celkem:"
  find /data/data/$pkg -maxdepth 4 ! -uid "$uid" 2>/dev/null | wc -l
  echo "-- /data/app cesta:"
  ls -land /data/app/*/$pkg-* 2>/dev/null
  echo "-- soubory v /data/app/$pkg diru s jinym ownerem nez 1000:"
  d=$(ls -d /data/app/*/$pkg-* 2>/dev/null | head -1)
  [ -n "$d" ] && find "$d" -maxdepth 1 ! -uid 1000 2>/dev/null | head -8
  echo ""
done
