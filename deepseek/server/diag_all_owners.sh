#!/system/bin/sh
echo "=== vsechny appky: owner /data/data/<pkg> vs uid ==="
pm list packages -U 2>/dev/null | while read -r line; do
  pkg=$(echo "$line" | sed 's/^package://; s/ uid:.*//')
  uid=$(echo "$line" | sed 's/.*uid://')
  [ -d "/data/data/$pkg" ] || continue
  owner=$(ls -land "/data/data/$pkg" 2>/dev/null | awk '{print $3}')
  if [ "$owner" != "$uid" ]; then
    echo "MISMATCH  pkg=$pkg  expected=$uid  actual=$owner"
  fi
done
echo "=== konec (jen mismatche) ==="
