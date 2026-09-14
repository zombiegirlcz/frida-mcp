#!/usr/bin/env python3
"""spawn_probe — ZAKÁZÁNO. Ponecháno jen jako dokumentace pasti.

⛔ NEPOUŽÍVAT `frida.spawn()` NA ANDROIDU!

Frida implementuje `spawn` tak, že se **injektuje do `zygote`** (hookuje
`nativeForkAndSpecialize`, aby appku pozastavila při startu). Tím:

  1. zapíše svůj kód do paměti **zygote64**
  2. VŠECHNY procesy forknuté od té chvíle ten patch zdědí (copy-on-write)
  3. rozbitá PLT/GOT stránka v knihovnách (např. `libmedia`) způsobí, že
     volání skončí na nesmyslné adrese

Projevy, které jsme viděli:
  * Gemini: `MediaProfiles::hasCamcorderProfile` null deref (fault 0x3a8)
  * Paysafe: crash při inicializaci kamery
  * `com.miui.aod:settings` patchnutý, `com.miui.aod` ne (různé časy forku)

Patch je v PAMĚTI → **jediná oprava je REBOOT** (po zastavení frida-serveru).

✅ SPRÁVNĚ: pouze `device.attach(pid)` na konkrétní BĚŽÍCÍ appku.
   `attach` injektuje přímo do cílového procesu a `zygote` se nedotkne.

Původní kód (pro historii):
    pid = dev.spawn(pkg)
    s = dev.attach(pid)
"""
import sys

print(__doc__)
print("[!] spawn je na Androidu zakazany — pouzij attach na bezici appku.",
      file=sys.stderr)
raise SystemExit(2)
