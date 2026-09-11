# qwen-free — Qwen chat jako pi provider

Přístup k **`chat.qwen.ai`** API přes session z Qwen Android appky. Žádný login
flow, žádné API klíče — stačí cookie `token` (JWT) z WebView appky (WAF
hlavičky se zachytí z reálného provozu appky přes fridu.

```
pi ──► http://127.0.0.1:13360/v1/chat/completions   (OpenAI API)
   ──► bridge/qwen_shim.py
   ──► bridge/qwen_api.py   (token z Cookies DB; WAF hlavičky jsou nepovinná cache)
   ──► https://chat.qwen.ai/api/v2/chat/completions
```

## Rychlý start

```bash
cd /root/frida-mcp/qwen

./bin/qwen-free capture     # NEPOVINNÉ: zachytí WAF hlavičky (Qwen je nevyžaduje)
./bin/qwen-free start       # spustí shim (+ daemon pro průběžný refresh hlaviček)

pi --model qwen-free/qwen3.7-plus      # a pi jede na Qwenu
./bin/qwen "hlavní město Francie?"     # nebo přes CLI
```

Registrováno v `~/.pi/agent/models.json` jako provider **`qwen-free`**:
| model | |
|---|---|
| `qwen-free/qwen3.7-plus` | Qwen3.7 Plus |
| `qwen-free/qwen3.8-max` | Qwen3.8 Max |

## Jak to funguje

### 1. Auth = cookie `token` (JWT)
Qwen appka (WebView) si drží session v cookie `token` pro `chat.qwen.ai`:
```
{"id":"e0fe1b9e-…","last_password_change":…,"exp":1791732646}   # exp ~30 dní
```
Čte se z `/data/data/ai.qwenlm.chat.android/app_webview/Default/Cookies`
(SQLite; v guestu přes `sudo`, protože je to cizí app data).
**Token se nevaliduje podpisem** — server ho bere jako session cookie.

### 2. WAF hlavičky — ukázalo se, že nejsou potřeba

`chat.qwen.ai` je sice za **Aliyun WAF**, ale **aktuálně WAF hlavičky
nevyžaduje** — stačí cookie s tokenem. Ověřeno opakovaně:

```
jen token (bez wua/waf)   -> HTTP 200 OK
zadny qwen_headers.json   -> funguje     ("BEZ_HEADERS_OK")
x-device-id nahodne       -> HTTP 200 OK
```

Co API **skutečně** potřebuje: `Cookie: token=<jwt>; x-ap=eu-central-1;`,
`source: app`, `x-request-id` (uuid), `User-Agent` a `x-device-id` — ten může být
i náhodný (generuje ho `qwen_api.device_id()`, ukládá do `secrets/qwen_device_id`).

Historicky se mělo za to, že bez `x-mini-wua` (185 B, per-request, SecurityGuard)
a `app_waf` (48 B) WAF provoz zablokuje. Zachytávaly se z reálného provozu appky
hooknutím `libssl.so` → `SSL_write` (`agent/qwen_hdr.js` → `bridge/qwen_hdrd.py`
→ `secrets/qwen_headers.json`). Tato mašinérie je **zachovaná, ale nepovinná**:
`python3 scripts/ensure_tokens.py --capture-headers` (nebo `./bin/qwen-free capture`).
Kdyby se WAF jednou vrátil, stačí je zase doplnit do cache.

### 3. Flow API
```
POST /api/v2/chats/new            {"chat_mode":"guest","project_id":""}  → {"data":{"id":"<chat_id>"}}
POST /api/v2/chat/completions?chat_id=<id>
     {"stream":true,"incremental_output":true,"chat_mode":"guest","model":"qwen3.7-plus",
      "messages":[{…,"content":"<prompt>","role":"user",…}]}
   → SSE: {"choices":[{"delta":{"content":"…","phase":"answer"|"thinking_summary"}}]}
```
Text odpovědi = `choices[].delta.content` kde `phase == "answer"`.
`phase: "thinking_summary"` = shrnutí uvažování (posílá se zvlášť).

## ⚠️ Provozní poznámky

- **Anonymní (guest) režim má denní limit** — *"You've reached the guest chat limit
  for today's usage. Log in to continue."* S přihlášeným účtem limit mizí.
- Appka posílá `x-mini-wua` **per-request**, replay se starší hodnotou projde,
  ale **aktuálně se nevyžaduje** (viz výše). Když by API začalo vracet WAF chybu:
  `./bin/qwen-free capture` (nebo prostě otevři Qwen appku — daemon si ji vezme sám).
- **SecurityGuard (`libsgmain.so`)** = anti-frida/root. Attach fridy projde
  (ověřeno), ale není záruka do budoucna → když appka začne padat, použij
  přejmenovaný frida-server nebo gadget mód.
- Verze appky: **2.1.1** (UID 10302). API se může změnit.
- Token expiruje ~30 dní → pak stačí být v appce přihlášený (refresh je automatický).

## Soubory
| soubor | co dělá |
|---|---|
| `bridge/qwen_api.py` | API klient (token, hlavičky, chats/new, SSE completions) |
| `bridge/qwen_shim.py` | OpenAI-compatible server pro pi (port 13360) |
| `bridge/qwen_hdrd.py` | frida daemon: tahá WAF hlavičky z appky |
| `agent/qwen_hdr.js` | hook `libssl.so` SSL_write (plain JS, bez bundlingu) |
| `bin/qwen-free` | `capture`/`start`/`stop`/`status` |
| `bin/qwen` | CLI (jeden dotaz / REPL) |
| `secrets/qwen_headers.json` | cache WAF hlaviček (chmod 600) |
| `secrets/qwen_token` | cache JWT (chmod 600) |

Konverzace: shim drží mapu `hash(system+první user) → chat_id`; první request
pošle celou historii jako jeden prompt do nového chatu, další jen novou zprávu.
