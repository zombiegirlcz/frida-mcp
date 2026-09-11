# frida-mcp — most DeepSeek ↔ pi (proot)

> AI v chat appce dostane **ruce a oči** v proot guestu. Lokálně, bez cloudu,
> bez API klíčů, bez modifikace APK.

## 🆕 Pi provider `deepseek-free` (frida)

**pi umí používat DeepSeek appku jako model** — vytáhne token z appky (MMKV),
PoW spočítá nativní funkcí appky přes fridu a mluví přímo s DeepSeek API.

```bash
./bin/deepseek-free start                 # spustí lokální OpenAI shim (127.0.0.1:13350)
pi --model deepseek-free/deepseek-chat    # ... a pi už jede na DeepSeeku
```

Zaregistrováno v `~/.pi/agent/models.json` jako provider **`deepseek-free`**
(`api: openai-completions`, `baseUrl: http://127.0.0.1:13350/v1`).

```
deepseek-free   deepseek-chat   128K   8.2K   no   no
```

| komponenta | soubor |
|---|---|
| OpenAI-compatible shim (pro pi) | `bridge/openai_shim.py` |
| DeepSeek API klient (session+PoW+SSE) | `bridge/deepseek_api.py` |
| PoW přes fridu (nativní DeepSeekHashV1) | `bridge/powd.py` + `agent/pow_rpc.js` |
| CLI (jeden dotaz / REPL) | `bin/deepseek` |
| start/stop shimu | `bin/deepseek-free` |

⚠️ **Vyžaduje běžící DeepSeek appku** (PoW) + frida-server na `127.0.0.1:27042`.

---
## Jak to funguje (ověřeno na živém zařízení)

```
DeepSeek (com.deepseek.chat, foreground)
   │
   │  OČI   nh device accessibility --json     (plochý seznam TextView/View)
   │  RUCE  input tap / input text / keyevent  (root, input subsystem)
   ▼
bridge daemon v proot  ──JSONL──►  TCP 127.0.0.1:13341  ──►  pi
```

### ⚠️ Klíčová zjištění (proč zrovna takhle)

| věc | výsledek |
|---|---|
| `nh device tap` / `click` na DeepSeeku | ❌ **nefunguje** (appka ignoruje accessibility gesta) |
| root `input tap` / `input text` | ✅ **funguje** (input subsystem) |
| `nh device accessibility --json` | ✅ funguje (oči) |
| `uiautomator dump` | ✅ funguje, ale **nevidí Compose input text** (accessibility ano) |
| Frida `dev.spawn()` | ❌ `unable to locate android.os.Process.setArgV0() slot` (frida 17.18 + MIUI) — použij attach |
| Frida attach | ✅ funguje |
| Frida hook `libssl.so!SSL_write/SSL_read` | ✅ funguje (plaintext HTTP/1.1 i HTTP/2) |
| `nh fix permission <path>` | ✅ opraví vlastnictví souborů vytvořených pod real rootem |

DeepSeek je **Compose** appka → v accessibility není strom ani `resource-id`/role,
jen plochý seznam. Uživatelské bubliny jsou vpravo (`x > 0.62 * šířka`),
odpovědi AI vlevo. To je heuristika — viz `bridge/dsui.py:messages()`.

## Instalace / běh

```bash
# oči: přečti konverzaci
python3 bin/frida-chat read
python3 bin/frida-chat read --json

# ruce: pošli zprávu do chatu
python3 bin/frida-chat send "ahoj"

# stav
python3 bin/frida-chat status

# daemon: streamuj nové zprávy jako JSONL
python3 bin/frida-chat watch --port 13341        # frida servíruje, pi se připojí
python3 bin/frida-chat watch --connect 127.0.0.1:13341   # pi poslouchá, frida pushuje

# SMYČKA: AI pošle ```bash``` → pi spustí v prootu → výsledek zpět do chatu
python3 bin/frida-chat loop --connect 127.0.0.1:13341
python3 bin/frida-chat loop --dry-run             # jen ukáže, co by spustil
```

📄 **Kompletní protokol + manuál pro AI (system prompt) je v [`PROTOCOL.md`](PROTOCOL.md).**

### Formát (JSONL, jeden objekt na řádek)

```json
{"v":1,"dir":"out","ts":1789133261706,"src":"com.deepseek.chat","kind":"reply","role":"assistant","x":540,"y":1185,"text":"Ahoj, jak vám mohu pomoci?"}
{"v":1,"dir":"to_chat","ts":1789133261900,"kind":"inject","text":"vysledek z prootu","src":"pi"}
```

- `dir`: `in` = prompt uživatele, `out` = odpověď modelu, `to_chat` = injektujeme do chatu
- `kind`: `prompt` | `reply` | `inject` | `error`
- Vše se duplikuje do `bridge/chat.jsonl` (audit/replay).

## Prostředí / pastí

- **`nh device` čte foreground appku.** Když je vepředu terminál (`com.linux_core`),
  čteš terminál. Proto bridge běží jako **background daemon**, aby DeepSeek mohl být vepředu.
- **Klávesnice posouvá layout** — input má `y≈2154` bez klávesnice, `y≈1315` s ní.
  Vždy re-dumpovat (dělá `dsui.focus_input()`).
- **`sudo` = real root** přes `su_daemon` → soubory vytvořené pod `sudo` mají na hostu
  `root:root` a nikoliv `u0_a315`. Oprava: `nh fix permission <path>`.
- Frida: viz `frida-supervise.sh` (supervision loop) a `bridge/attach_when_ready.py`
  (workaround za rozbitý spawn).

## Stav / TODO

- [x] frida-server 17.18.0 deploy + supervision
- [x] Java bridge zabundlovaný (`frida-java-bridge` + esbuild) — Frida 17 ho nemá
- [x] oči (`read`/`watch`) + ruce (`send`) + TCP stream
- [x] **smyčka** (`loop`): code bloky z odpovědi AI → shell v prootu → výsledek zpět
- [x] protokol + manuál pro AI (`PROTOCOL.md`)
- [x] **pi provider `deepseek-free`** (token z MMKV + PoW přes fridu + OpenAI shim) ✅
- [x] PoW algoritmus rozluštěn: `hash(salt_expireAt_nonce) == challenge` (viz `PROTOCOL.md`)
- [ ] **B**: reimplementovat custom Keccak (DeepSeekHashV1) v Pythonu → shodit frida závislost
- [ ] přesnější role zpráv (ideálně z network hooku místo heuristiky)
- [ ] smyčka: odpověď modelu → pi → výsledek zpět do chatu
- [ ] stealth frida (přejmenovat binárku, jiný port) — zatím nebylo potřeba:
      incident „nefunguje OAuth" způsobil jiný agent smazáním dat účtů, **ne frida**
- [ ] rate limiting, aby se chat nezahltil
