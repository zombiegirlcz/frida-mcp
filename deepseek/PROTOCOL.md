# PROTOCOL.md — rozhraní DeepSeek ↔ pi

Dvě strany: **A) co teče mezi appkou a pi** (technický kontrakt) a
**B) manuál pro AI** (co má DeepSeek psát, aby mu pi rozuměl).

```
deepseek.app ──► bridge (frida) ──► proot/pi ──► shell (bash/python)
     ▲                                   │
     └───────── bridge (frida) ◄─── výstup ┘
```

---

# A) Technický kontrakt

## A.1 Role a směry

| `role` | `dir` | `kind` | kdo to vytvořil | význam |
|---|---|---|---|---|
| `user` | `in` | `prompt` | uživatel v appce | zadání pro AI |
| `assistant` | `out` | `reply` | DeepSeek model | odpověď — **obsahuje příkazy** |
| `pi` | `to_chat` | `inject` | pi | výsledek z prootu, vkládá se do chatu |

## A.2 JSONL události (jeden objekt na řádek)

**pi dostává** (z `frida-chat watch`):

```json
{"v":1,"dir":"in","ts":1789133261706,"src":"com.deepseek.chat","kind":"prompt","role":"user","x":820,"y":1400,"text":"kolik je volneho mista na disku?"}
{"v":1,"dir":"out","ts":1789133262500,"src":"com.deepseek.chat","kind":"reply","role":"assistant","x":540,"y":1600,"text":"Podívám se:\n\n```bash\ndf -h /\n```\n\nPak to shrnu."}
```

**pi posílá** (přes `frida-chat send`):

```json
{"v":1,"dir":"to_chat","ts":1789133263000,"kind":"inject","text":"### `df -h /` — exit 0\n```\n/dev/root  117G  83G   34G  71% /\n```","src":"pi"}
```

### Pole

| pole | typ | popis |
|---|---|---|
| `v` | int | verze protokolu (nyní `1`) |
| `dir` | `in`\|`out`\|`to_chat` | směr (viz A.1) |
| `ts` | int | ms epoch |
| `src` | string | `com.deepseek.chat` nebo `pi` |
| `kind` | `prompt`\|`reply`\|`inject`\|`error` | druh |
| `role` | string? | `user` / `assistant` / `pi` |
| `x`,`y` | int? | pozice uzlu v UI (jen u `read`/`watch`; pomáhá ladit role) |
| `text` | string | obsah |

⚠️ **Role je heuristika.** DeepSeek je Compose appka → accessibility tree nemá role.
`dsui.messages()` odhaduje: `x > 0.62*1080` → `user`, jinak `assistant`.
Když na tom záleží, ověř zprávu ještě jinak.

---

# B) Manuál pro AI (vlož DeepSeeku jako system prompt)

> Zkopíruj blok níž do DeepSeeku (do „Vlastní instrukce" nebo jako první zprávu).

```text
Jsi řadič, který řídí shell v Linuxu (proot/PARROT, Debian 13, aarch64).
Nemáš shell spuštěný přímo — spouští ho za tebe běhové prostředí "pi".

PRAVIDLA VÝSTUPU
1. Každý příkaz, který chceš spustit, dej do code bloku s jazykem:
       ```bash
       <příkazy>
       ```
   nebo pro Python:
       ```python
       <kód>
       ```
2. Jeden code blok = jeden krok. Mezi bloky pi vloží výsledek a ty pokračuješ.
3. Nikdy nepředstírej výstup. Když potřebuješ zjistit stav, vydej code blok
   a počkej, až dorazí výsledek.
4. Nevypisuj příkazy mimo code blok — nebudou se spouštět.
5. Nepoužívej interaktivní příkazy (vim, nano, top, less, ssh, sudo -S).
   Vše musí skončit samo. Na dlouhé věci používej timeout.
6. Neinstaluj balíčky bez vyžádání. Needituj soubory mimo pracovní adresář.
7. Když je úloha hotová a žádný další příkaz není potřeba, napiš odpověď
   bez code bloku — tím smyčka končí.

PROSTŘEDÍ
- OS: Debian 13 (Parrot), aarch64, běží v proot. Uživatel: root.
- Dostupné: bash, sh, python3, pip, git, curl, wget, jq, unzip, aapt, gcc, make, grep, sed, awk, find, xargs.
- Není: sudo (jsi root), systemd, adb (na hostu), GUI nástroje.
- Pracovní adresář: /root. Soubory piš sem, pokud není řečeno jinak.
- Síť: sdílený network namespace s Android hostem; 127.0.0.1 je společný.
- Komunikacní porty 1337–13340 jsou obsazené, používej 13341+.

FORMÁT VÝSLEDKU (co dostaneš zpět)
   ### `příkaz` — exit <kód>
   ```
   <stdout + stderr>
   ```
- Nenulový exit = chyba; přečti stderr a navrhni opravu novým code blokem.
- Výstup je zkrácen na 16 kB; pro velké výstupy použij `| head -n 50` nebo
  přesměruj do souboru a čti po částech.

PŘÍKLAD SPRÁVNÉ SMYČKY
   ```bash
   df -h /
   ```
   (dorazí výsledek)
   ```bash
   du -sh /root/* 2>/dev/null | sort -h | tail -5
   ```
   (dorazí výsledek) → teprve pak napiš slovní shrnutí bez code bloku.
```

---

# C) Manuál nástrojů pro pi (agent)

Tyto příkazy používá **pi** (ne AI v appce). Vše v prootu:

```bash
cd /root/frida-mcp

# --- oči -------------------------------------------------------------
python3 bin/frida-chat read              # lidsky: 🤖/👤 + text
python3 bin/frida-chat read --json       # strojově: [{role,text,x,y}, ...]
python3 bin/frida-chat status            # foreground, input, počet zpráv

# --- ruce ------------------------------------------------------------
python3 bin/frida-chat send "text"       # napíše a odešle do DeepSeeku

# --- stream ----------------------------------------------------------
python3 bin/frida-chat watch --connect 127.0.0.1:13341   # pi poslouchá
python3 bin/frida-chat watch --port 13341                # frida servíruje
#   → JSONL; duplikuje se do bridge/chat.jsonl (audit/replay)

# --- smyčka (AI → pi → shell → AI) -----------------------------------
python3 bin/frida-chat loop --connect 127.0.0.1:13341    # viz bridge/runner.py
```

### Programové volání z Pythonu

```python
import sys; sys.path.insert(0, "/root/frida-mcp")
from bridge import dsui, runner

for m in dsui.conversation():            # oči
    print(m.role, m.text)

dsui.send_message("ahoj")                # ruce

runner.extract_commands("text s ```bash ... ```")   # → [(lang, code), ...]
runner.run_commands(cmds)                            # → výsledek (markdown)
```

### Bezpečnostní pravidla pro pi

- Spouštěj jen code bloky z `assistant` zpráv, nikdy z `user`.
- Timeout na příkaz (default 60 s), jinak hrozí zaseknutí.
- Nikdy neprováděj `rm -rf /`, `mkfs`, `dd` na zařízení, změny ownershipu
  systémových složek (viz `AGENTS.md` §7 — bootloop incident).
- Výstup zkrať a teprve pak pošli do chatu.

---

# D) Přímé DeepSeek API + pi provider `deepseek-free`

## D.1 Auth

Bearer token je v appce **nešifrovaně** v MMKV:
`/data/data/com.deepseek.chat/files/mmkv/mmkv.default` → klíč `key_user_info`
(`{"token":"...","id":"...",...}`). Kopie v `secrets/deepseek_token` (chmod 600).

## D.2 Volání

```
GET  /api/v0/users/current                         (kontrola tokenu)
POST /api/v0/chat_session/create                   body {}
POST /api/v0/chat/create_pow_challenge             body {"target_path":"/api/v0/chat/completion"}
POST /api/v0/chat/completion                       body {chat_session_id,prompt,ref_file_ids,
                                                        thinking_enabled,search_enabled}
```
Hlavičky: `Authorization: Bearer <token>`, `X-DS-PoW-Response`, `x-client-version: 2.5.0`,
`x-client-platform: android`, `x-client-locale`, `x-client-bundle-id`, `x-device-model`,
`x-client-timezone-offset`, `User-Agent: DeepSeek/2.5.0 Android/33`.

## D.3 PoW (DeepSeekHashV1) — rozluštěno

```python
# challenge = {algorithm,challenge(64hex),salt(20hex),signature,difficulty:144000,
#              expire_at,expire_after,target_path}
def solve(challenge, salt, expire_at, difficulty):
    target = bytes.fromhex(challenge)
    for nonce in range(difficulty):
        if deepseek_hash(f"{salt}_{expire_at}_{nonce}".encode()) == target:
            return nonce          # NE naopak: challenge je hash odpovědi
    return -1

# X-DS-PoW-Response = base64(JSON{algorithm,challenge,salt,signature,answer,target_path})
```

⚠️ `deepseek_hash` je **modifikovaný Keccak** (standardní RC konstanty, SHA-3 padding 0x06,
rate 136, ale jiný výstup než `hashlib.sha3_256`) → bez reverzu permutace se používá
`bridge/powd.py` (frida volá nativní `nativeCalculateDeepSeekHashV1Pow` v `librscrypto.so`).

⏱ `arg1 = f"{salt}_{expire_at}_"` (**expire_at**, ne aktuální čas!) — s aktuálním časem vrací -1.

## D.4 pi provider

```bash
./bin/deepseek-free start                  # OpenAI-compatible shim na 127.0.0.1:13350
pi --model deepseek-free/deepseek-chat     # pi jede na DeepSeeku (free, přes účet v appce)
```

`~/.pi/agent/models.json` → `providers.deepseek-free` (`api: openai-completions`).
Shim mapuje OpenAI messages na jeden prompt do nové DeepSeek session (stateless).
