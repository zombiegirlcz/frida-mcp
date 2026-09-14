# frida-mcp


## ⛔ NIKDY `frida.spawn()` na Androidu

Frida na Androidu implementuje **spawn** tak, že se **injektuje do `zygote`**
(hookuje `nativeForkAndSpecialize`). Tím zapíše kód do paměti `zygote64`
a **všechny děti ten patch zdědí** (copy-on-write) → rozbité PLT/GOT stránky
v knihovnách → náhodné crashe, které se projeví až za hodiny:

* `MediaProfiles::hasCamcorderProfile` null deref (Gemini)
* crash při inicializaci kamery (Paysafe)
* `com.miui.aod:settings` patchnutý, `com.miui.aod` ne

**Správně:** jen `device.attach(pid)` na **běžící** appku. `attach` injektuje
přímo do cílového procesu a `zygote` se nedotkne.

Pokud už k patchi došlo, **jediná oprava je REBOOT** po zastavení
`frida-server` (`sh /data/local/tmp/frida-stop.sh`).
