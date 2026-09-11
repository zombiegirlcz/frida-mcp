---
name: frida-chat
description: Ovládej DeepSeek chat appku na tomto Android zařízení z proot guestu — čti konverzaci (oči) a posílej zprávy (ruce) přes lokální bridge. Použij, když má AI v chat appce něco spustit v prootu a dostat výsledek zpět, nebo když potřebuješ přečíst/streamovat chat bez API klíčů.
---

# frida-chat — ruce a oči v prootu

Most mezi **DeepSeek appkou** (`com.deepseek.chat`) a **pi** v prootu.
Žádný cloud, žádné API klíče, žádná modifikace APK.

## Rychlý start

```bash
cd /root/frida-mcp

python3 bin/frida-chat status         # běží? foreground? input?
python3 bin/frida-chat read           # přečti konverzaci  (oči)
python3 bin/frida-chat send "text"    # pošli zprávu      (ruce)
python3 bin/frida-chat watch --connect 127.0.0.1:13341   # stream do pi
```

## Pravidla, která musíš dodržet

1. **DeepSeek musí být foreground.** `nh device accessibility` čte *foreground* appku.
   Když je vepředu terminál (`com.linux_core`), čteš terminál. Proto bridge běž jako
   **background daemon**, ne interaktivně.
2. **Nepoužívej `nh device tap/click` na DeepSeeku** — appka ignoruje accessibility gesta.
   Vždy root: `input tap`, `input text`, `input keyevent` (viz `bridge/dsui.py`).
3. **Klávesnice posouvá layout.** Souřadnice inputu se mění (`y≈2154` bez klávesnice,
   `y≈1315` s ní). Vždy nejdřív `dsui.focus_input()`, pak teprve hledej `Odeslat`.
4. **Nikdy nepiš do appek přes `sudo`.** `sudo` = real root → soubor bude `root:root`
   a rozbije to appku. Když se to stane: `nh fix permission <path>`.
5. **Frida `spawn()` je na tomto zařízení rozbitý** (`setArgV0 slot`). Používej attach
   (`bridge/attach_when_ready.py`).

## Datový formát (JSONL)

```json
{"v":1,"dir":"out","ts":1789133261706,"src":"com.deepseek.chat","kind":"reply","role":"assistant","text":"..."}
{"v":1,"dir":"to_chat","ts":...,"kind":"inject","text":"vysledek","src":"pi"}
```

- `dir`: `in` = prompt uživatele · `out` = odpověď modelu · `to_chat` = injektujeme
- Audit log: `bridge/chat.jsonl`

## ⚠️ Omezení, která musíš znát

- **Role zpráv je heuristika.** DeepSeek je Compose appka → v accessibility tree není
  strom ani role. `dsui.messages()` odhaduje: `x > 0.62*1080` → uživatel, jinak AI.
  Dlouhá uživatelská zpráva se může splést s odpovědí AI. Když na přesnosti záleží,
  ověř zprávu ještě jinak (např. network hook na `libssl.so`).
- **Filtruje se UI chrome** (tlačítka „Kopírovat", „Hledat", …) přes `dsui.NOISE`.
  Když se v přepisu objeví smetí, doplň ho tam.
- **`send` spotřebovává reálnou kvótu účtu** a zpráva se objeví v reálném chatu.
  Neposílej testovací zprávy bez vědomí uživatele.

## Smyčka (jak to má fungovat)

```
uživatel píše v DeepSeeku
   → pi přečte prompt (read / watch)
   → spustí práci v prootu (shell, kód, testy)
   → výsledek odešle zpět: frida-chat send "<výsledek>"
   → objeví se v chatu
```

## Frida (pro hlubší zásah)

- `frida-server` na `127.0.0.1:27042`; supervision `server/frida-supervise.sh`,
  stop `server/frida-stop.sh`; python klient `/root/frida-mcp/.venv/bin/python`.
- Frida 17 **nemá** Java bridge → agent se bundluje:
  `./node_modules/.bin/esbuild agent/X.src.js --bundle --format=iife --platform=browser --target=es2020 --outfile=agent/X.js`
- Funguje hook `libssl.so!SSL_write/SSL_read` (`/apex/com.android.conscrypt/lib64/libssl.so`).
- Attach skript: `bridge/probe_run.py <pid> <agent.js>`;
  „poor man's spawn": `bridge/attach_when_ready.py <pkg> <proc_name> <agent.js> <wait>`.
