# frida-mcp

Chat appky v Androidu jako **pi providery** — zdarma, lokálně, bez API klíčů.

```
pi ──► localhost:13350/v1   (deepseek-free)  ──► DeepSeek appka (token + PoW přes fridu)
   └─► localhost:13360/v1   (qwen-free)      ──► Qwen appka     (token + WAF přes fridu)
```

Myšlenka: chat appky mají **free kvótu** na model a v mobilu je **platná session**.
Místo abychom platili API nebo klikali v UI, vytáhneme z appky její vlastní
přihlašovací token a podpisy (`PoW`, WAF hlavičky) — přes **fridu v tom samém
procesu** — a vystavíme je pi jako obyčejné OpenAI-compatible modely.

**Nic se neplatí, nic se netuneluje, žádná data neopouští telefon.** Vše jede
na `127.0.0.1`.

---

## Obsah

- [Co to umí](#co-to-umí)
- [Požadavky](#požadavky)
- [Instalace](#instalace)
- [Použití](#použití)
- [Jak to funguje](#jak-to-funguje)
  - [DeepSeek: token + PoW](#deepseek-token--pow)
  - [Qwen: token + WAF](#qwen-token--waf)
  - [Tool calling](#tool-calling)
  - [Streaming](#streaming)
- [Auto-vytahování tokenů](#auto-vytahování-tokenů)
- [Struktura repa](#struktura-repa)
- [Troubleshooting](#troubleshooting)
- [Bezpečnost](#bezpečnost)
- [Vývoj](#vývoj)

---

## Co to umí

| provider | model | co se používá z appky |
|---|---|---|
| `deepseek-free` | `deepseek-chat`, `deepseek-reasoner` | Bearer token z MMKV + **nativní `DeepSeekHashV1` PoW** (bez fridy) |
| `qwen-free` | `qwen3.7-plus`, `qwen3.8-max` | cookie `token` z WebView |

Navíc:

- **`bard/`** — Gemini (`com.google.android.apps.bard`). Appka je jen shell, která
  předává UI do Google appky, a ta na tomhle zařízení padá v ROM (`libmedia`).
  Obsahuje hotový workaround (`bard/agent/nocamcrash.js`, `bard/bin/gemini-fix`).
- **`deepseek/`** — kromě providera taky původní most do chatu (`bin/frida-chat`):
  čte konverzaci přes accessibility, spouští code-blocky v proot guestu a vrací
  výsledek zpátky do chatu.

---

## Požadavky

| | |
|---|---|
| **Zařízení** | Android s **rootem** (Magisk) a **proot guestem** (NetHunter / Parrot), ve kterém běží pi |
| **Python** | 3.11+ (kvůli venv a fridě) |
| **Python** | 3.11+ (shimy jedou na **stdlib** — žádné pip balíčky) |
| **C kompilátor** | `gcc`/`cc`/`clang` — jen pro rychlý PoW (~0,1 s místo ~70 s) |
| ~~frida-server~~ | **už není potřeba** — DeepSeekHashV1 se počítá nativně (legacy cesty: `FRIDA_MCP_WITH_FRIDA=1`) |
| **Cílové appky** | DeepSeek (`com.deepseek.chat`) a/nebo Qwen (`ai.qwenlm.chat.android`), **přihlášené** |
| **Node** | 20+ (kvůli pi) |

> Guest sdílí s Androidem **network namespace**, proto stačí loopback — žádný
> `adb reverse`, žádné tunely.

---

## Instalace

```bash
pi install git:github.com/zombiegirlcz/frida-mcp
```

Balíček se naklonuje do `~/.pi/agent/git/github.com/zombiegirlcz/frida-mcp`
a pi při startu:

1. **zapíše providery** `deepseek-free` a `qwen-free` do `~/.pi/agent/models.json`
   (záloha: `models.json.bak.frida-mcp`)
2. Spustí `scripts/bootstrap.sh` — ověří Python a **zkompiluje nativní PoW**
   (`deepseek/native/libdspow.so`). Žádné pip balíčky se neinstalují.
3. **vytáhne tokeny** z appek, když chybí (`scripts/ensure_tokens.py`)
4. **nastartuje shimy** na portech 13350 / 13360

Ruční doinstalace prostředí (kdyby na to pi neměl čas):

```bash
cd ~/.pi/agent/git/github.com/zombiegirlcz/frida-mcp
bash scripts/bootstrap.sh              # python + gcc (pro nativni PoW)
python3 scripts/ensure_tokens.py       # tokeny z appek

# VOLITELNE — jen kdyz chces diagnostiku / odposlech (frida NENI potreba):
bash scripts/bootstrap.sh              #   s FRIDA_MCP_WITH_FRIDA=1 -> .venv + frida
# bash scripts/deploy_frida_server.sh  #   frida-server do /data/local/tmp
```

### Ovládání z pi

```
/frida-mcp              # stav: běží shimy? kde je python? kde je root balíčku?
/frida-mcp start        # (re)start obou shimů
/frida-mcp tokens       # znovu vytáhnout tokeny z appek
/frida-mcp full-context # ZAPOMENOUT chat -> další tah pošle CELOU historii
/frida-mcp chats        # kolik chatů si shim drží
```

`full-context` použij, když se předchozí tah **přerušil** (Esc, pád, restart
shimu) a model „začíná znovu" bez kontextu — delta tah by totiž šel do
session, která už historii nemá. Příkaz řekne shimu, aby zapomněl zapamatované
chaty, takže další zpráva se pošle jako **nový chat s celou historií**.

Automaticky se to dělá i při **startu každé pi session** (bezpečný default:
plný kontext je vždy správně, delta je jen optimalizace).

---

## Použití

```bash
pi --model deepseek-free/deepseek-chat
pi --model qwen-free/qwen3.8-max
```

Nebo z shellu (dev nástroje):

```bash
./deepseek/bin/deepseek-free start     # start/stop/status shimu
./deepseek/bin/deepseek "otázka"       # přímý dotaz
./qwen/bin/qwen-free start
./qwen/bin/qwen "otázka"
```

---

## Jak to funguje

```
pi ─► shim (OpenAI API, stdlib http.server)
        │  1. OpenAI messages + tools  →  jeden prompt
        │  2. zeptá se appky na podpis (frida RPC / SSL hook)
        │  3. zavolá reálné API appky
        │  4. SSE → OpenAI SSE (stream + tool_calls)
        └─► zpět do pi
```

### DeepSeek: token + PoW

- **Token** je v appce **nešifrovaně** v MMKV:
  `files/mmkv/mmkv.default` → `key_user_info` = `{"token":"<base64>","id":"<uuid>",…}`
- **PoW**: `POST /api/v0/chat/create_pow_challenge` vrátí `challenge` (64 hex),
  `salt`, `difficulty` (~144000). Klient musí najít `nonce` tak, že

  ```
  deepseek_hash(f"{salt}_{expire_at}_{nonce}") == bytes.fromhex(challenge)
  ```

  ⚠️ Do vstupu patří **`expire_at`** z challenge, **ne** aktuální čas.

  **`DeepSeekHashV1` = SHA3-256, ale přeskočí první kolo permutace
  Keccak-f[1600]** (kola 1..23). Vše ostatní je standardní SHA3-256
  (rate 136, padding `0x06`, 24 RC konstant, 25×64bit stav).

  Implementace je **nativní** (`deepseek/native/dspow.c` + `deepseek/bridge/pow_native.py`)
  — **frida ani běžící appka nejsou potřeba**:

  ```
  hash(6fe4581ae0fcf306e50d_1789139155175_35873)
    = 886a0939c788d0ef9b2ef84e5b98c48494fe2474ea3f62cc5abe036879415a3a   ✅
  ```

  C knihovna se **zkompiluje sama** při prvním použití (`gcc -O3`, ~0,1 s na
  cely PoW). Když gcc není, použije se pure-Python fallback (stejný výsledek,
  jen ~15 s) a teprve pak legacy frida helper.

  Hlavička `X-DS-PoW-Response` = `base64(JSON{algorithm,challenge,salt,signature,answer,target_path})`.

### Frida je jen diagnostika, ne závislost

Projekt **nepotřebuje fridu ani běžící appku** k fungování. Vše, co je
potřeba za běhu, je python3 + (gcc | pure-Python fallback) + token z appky.

Frida se používá **výhradně** pro:
* odposlech síťového provozu (`deepseek/agent/dscap.js`, `qwen/agent/qwen_hdr.js`)
* záchranu WAF hlaviček pro Qwen (`--capture-headers`, volitelné)
* jednorázové sondy (`probe_run.py`, `attach_when_ready.py`)
* záložní PoW cestu, **když nejde zkompilovat nativní `.so`**

Nic z toho se při normálním provozu nespouští — `_want_frida_fallback()`
vrací `False`, jakmile nativní backend žije, a `frida` se **vůbec neimportuje**.

Ověřeno na reálné obnově tokenu (`logs/tokens.log`):
```
[tokens] deepseek: ctu /data/data/…
[tokens] ulozene -> deepseek/secrets/deepseek_token (64 B)
[tokens] qwen: WAF hlavicky neresim (Qwen staci token)
```
→ **žádný frida import, žádný proces navíc**

### Qwen: token + WAF

- **Token** je cookie `token` (JWT, platnost ~30 dní) ve WebView:
  `app_webview/Default/Cookies` (SQLite).
- `chat.qwen.ai` je za **Aliyun WAF** — bez hlaviček vrátí JS challenge:
  - `x-mini-wua` (185 B, per-request, SecurityGuard)
  - `app_waf` (48 B, stabilní, jen na `/chat/completions`)
  - `x-device-id`, `source: app`, UA `… AliApp(QWENCHAT/2.1.1)`

  Zachytí je `qwen/agent/qwen_hdr.js` (hook `SSL_write` v `libssl.so`) a
  `qwen/bridge/qwen_hdrd.py` je ukládá do `qwen/secrets/qwen_headers.json`.
  > ⚠️ **Aktualizace:** důkladné testování ukázalo, že `chat.qwen.ai` tyto
  > hlavičky **vůbec nevyžaduje** — stačí cookie s tokenem (a `x-device-id`
  > může být i náhodné). Capture je tedy jen **nepovinný** (`--capture-headers`),
  > Qwen funguje i s úplně prázdným `qwen_headers.json`.
- Flow: `POST /api/v2/chats/new` → `chat_id` → `POST /api/v2/chat/completions?chat_id=…` (SSE).
- Anonymní režim funguje i bez přihlášení, ale má **denní limit** — přihlášený ne.

### Myšlení (thinking) — `reasoning_content`

DeepSeek appka umí **myšlení** a posílá ho v **jednom SSE streamu** spolu
s odpovědí. Rozliší se podle typu fragmentu:

| co přijde | typu fragmentu | co s tím |
|---|---|---|
| `response/fragments/-1/content` APPEND | `THINK` / `RESPONSE` | text do posledního fragmentu |
| `response/fragments` APPEND `[{type, content}]` | nový fragment | vzniká při přechodu THINK → RESPONSE |
| `response/content` APPEND | — | běžné chunky odpovědi |

Myšlení se posílá jako **`delta.reasoning_content`** (to pi zobrazuje jako
thinking blok), odpověď jako `delta.content`. Tool cally a `StreamSplitter`
se týkají **jen odpovědi**.

Zapíná se přes `thinking_enabled: true` v requestu na app API; pi to dělá
automaticky, protože model má `reasoning: true` a
`compat.thinkingFormat: "deepseek"` → pi pošle `thinking: {type: "enabled"}`.

```bash
pi --model deepseek-free/deepseek-reasoner    # s myšlením
pi --model deepseek-free/deepseek-chat        # myšlení dle thinking levelu
```

Ověřeno na živém API: `6*7` → 147 znaků myšlení + odpověď `42`; tool calling
funguje i s myšlením.

### 1 pi session = 1 chat (synchronizace s appkou)

Extension posílá při startu session **ID pi session** na `POST /session`
(`ctx.sessionManager.getSessionId()`), takže shim přesně ví, který chat
použít — nemusí hádat podle prefixu zpráv:

| `session_start` reason | co se stane |
|---|---|
| `startup` | pokračuje se v uloženém chatu, pokud existuje |
| `resume` | **naváže se stejný chat** — kontext zůstává na serveru |
| `new` | chat se začne od znova |
| `fork` | nová větev |

Mapování `pi session → chat` se **ukládá na disk** (`logs/convstate.json`),
takže přežije restart shimu i pi. Díky tomu funguje `resume` i po přerušení:
žádné „začíná znovu bez kontextu".

> Bez ID session (jiný klient) se použije porovnávání prefixu zpráv.

### Jeden chat na konverzaci + jen delta (proti detekci)

Původně shim vytvářel **nový chat pro každou zprávu** a posílal celou
historii znovu. To je špatně ze dvou důvodů:

1. **Je to nápadné.** Normální uživatel má jeden chat s N zprávami, my jsme
   měli N chatů s jednou zprávou — přesně podle toho se dá automatizace
   poznat (a taky se to stalo).
2. Zbytečně se posílá celý prompt při každém tahu.

Server si konverzaci **drží sám**, jen je potřeba ji správně navázat:

```json
{"chat_session_id": "<id>", "parent_message_id": "<id poslední odpovědi>"}
```

Pak stačí poslat **jen novou zprávu**. Ověřeno na živém API — model si
pamatuje i obsah z předchozích tahů.

`common/convcache.py` to řeší automaticky: porovnává, jestli už tuhle
konverzaci viděl (prefix zpráv), a když ano, vrátí session + jen delta:

```
[shim] delta tah v chatu 49ff9183… (2 novych zprav, 785 znaku)
```

Rozdíl je výrazný — místo celé historie (jednotky KB) se posílá pár set znaků.

> **Qwen**: jeho API bere jen **jednu** zprávu (`Invalid input too many
> messages`) a kontext v `chat_id` nedrží, takže se historie posílá celá.
> Ale `chat_id` se **reuse** — jeden chat na konverzaci místo nového pro
> každou zprávu.

### Tool calling

Naše backendy neumí nativní function calling, takže `common/toolbridge.py` dělá most:

1. definice nástrojů se vloží do promptu (razantní instrukce **na začátku** systémové
   zprávy + JSON schémata za ní)
2. model odpoví buď
   - `<tool_call>{"name":…,"arguments":{…}}</tool_call>`, nebo
   - `<tool_call><invoke name="write"><parameter name="path">…</parameter>…</invoke></tool_call>`
     (delší texty — **neescapuje se**, to je i nativní DeepSeek **DSML** formát)
3. odpověď se rozparsuje (tolerantně: DSML, `<invoke>`, ```json fence, **holý JSON**
   bez obalu) a přeloží na OpenAI `tool_calls`
4. `role: tool` zprávy se zpátky přeloží na text pro model

Parsované názvy se validují proti seznamu skutečných nástrojů — běžný JSON
v odpovědi se tedy **neplete** s tool callem.

### Streaming

Shimy **streamují živě**: `completion_stream()` čte SSE po řádcích a
`StreamSplitter` pustí text okamžitě — drží jen to, co by mohl být začátek tool
callu (hledá `<`, `{`, `` ` ``). Když se objeví marker, přestane streamovat a na
konci to přeloží na `tool_calls`.

Ověřeno: deepseek ~420 chunků / 3 s, qwen ~130 chunků / 5 s.

---

## Auto-vytahování tokenů

`scripts/ensure_tokens.py` je idempotentní a umí obojí:

```bash
python3 scripts/ensure_tokens.py            # co chybí, dotáhni
python3 scripts/ensure_tokens.py --force    # přegeneruj
python3 scripts/ensure_tokens.py --check    # jen report
```

Data appek jsou čitelná jen pod **reálným rootem** → skript používá `sudo` **POZOR** toto neplati pro termux proot, použivam na to vlastni aplikaci ktera ma specialni vychytavky https://github.com/zombiegirlcz/kali_core_emulator
(v proot guestu). Cesty se zkouší postupně (`/mnt/data/data`, `/data/data`, …),
protože `os.path.exists()` na cizí app data neprojde.

**Self-healing:** když token u shimu chybí, shim si ho sám zavoláním tohoto
skriptu vytáhne a uloží (`common/tokenauto.py`). Totéž dělá extension při startu.

---

## Struktura repa

```
extensions/frida-mcp.ts     pi extension — modely, tokeny, start shimů, /frida-mcp
common/
  toolbridge.py             tool calling most + StreamSplitter
  tokenauto.py              „chybí token? vytáhni ho"
scripts/
  bootstrap.sh              python + gcc (frida jen s FRIDA_MCP_WITH_FRIDA=1 — diagnostika)
  deploy_frida_server.sh    frida-server → /data/local/tmp (jen diagnostika)
  ensure_tokens.py          tokeny z DeepSeek MMKV + Qwen cookies
deepseek/
  bridge/deepseek_api.py    API klient (session, PoW, SSE)
  bridge/pow_native.py      DeepSeekHashV1 nativně (ctypes + Python fallback)
  native/dspow.c            C implementace (auto-build při prvním použití)
  bridge/powd.py            LEGACY: PoW přes fridu (už se nepoužívá)
  bridge/openai_shim.py     OpenAI-compatible server (port 13350)
  bridge/dsui.py, runner.py, bin/frida-chat   most do chatu (accessibility + input)
  agent/pow_rpc.js          LEGACY: frida agent pro PoW
  cli/deepseek.py           CLI
qwen/
  bridge/qwen_api.py        API klient (cookie token, WAF hlavičky, SSE)
  bridge/qwen_hdrd.py       frida daemon na WAF hlavičky
  bridge/qwen_shim.py       OpenAI-compatible server (port 13360)
  agent/qwen_hdr.js         frida agent: hook SSL_write
bard/
  agent/nocamcrash.js       workaround na crash Gemini (viz níže)
  bridge/gemini_fixd.py     daemon, který ho drží
```

### Gemini (`bard/`) — proč to padá

`com.google.android.apps.bard` je jen shell → UI běží v Google appce
(`com.google.android.googlequicksearchbox`). Ta při otevření Gemini inicializuje
CameraX → `android.media.CamcorderProfile.hasProfile()` → JNI →
`MediaProfiles::hasCamcorderProfile` v ROM `libmedia.so` **dereferuje NULL**
(chyba MIUI 14 na Redmi Note 10, `fault addr 0x3a8`).

Není to obrana Googlu, není to frida, není to účet. XML profilů je v pořádku.

Workaround: hooknout Java metodu a vrátit `false`, takže se nativní volání
vůbec neprovede → `bard/bin/gemini-fix start` (daemon, připojí se na `:search`).

---

## Troubleshooting

| příznak | příčina / řešení |
|---|---|
| `Connection error.` v pi | shim neběžel / byl restartován. `/frida-mcp start` |
| `[chyba: script has been destroyed]` | appka spadla a frida skript v ní umřel → mělo by se samo re-attachnout; když ne, restartuj appku i shim |
| `chybí DeepSeek token a nejde vytáhnout` | appka není nainstalovaná/přihlášená, nebo chybí `sudo` |
| Qwen: `RateLimited … guest chat limit` | nejsi přihlášený v Qwen appce (anonym má denní limit) |
| Qwen: vrací HTML s `aliyun_waf_aa` | Qwen aktuálně WAF hlavičky nevyžaduje; když se objeví, zkus `qwen/bin/qwen-free capture` |
| Qwen: `chybí x-mini-wua` | stará verze kódu — aktualizuj (`pi install git:github.com/zombiegirlcz/frida-mcp`) |
| `frida attach selhal` | neškodné: nativní PoW fridu nepotřebuje (frida je jen záložní cesta) |
| `500: chybí pow helper` | stará verze (před nativním PoW) → aktualizuj balíček |
| vše je pomalé | zařízení swapuje; zavři appky na pozadí |

### Diagnostika

> Frida je **volitelná** — spouštěj ji jen když potřebuješ odposlech nebo
> když se něco změnilo v appce a chceš zjistit proč. Běžný provoz ji nechce.

```bash
# JEDE vse potrebne bez fridy?
for p in 13350 13360; do timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$p" && echo "$p OK"; done
cat logs/tokens.log          # obnova tokenu (frida se tu nesmi objevit)

# VOLITELNE: bezi frida-server? (jen pro odposlech)
timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/27042' && echo OK

# běží shimy?
for p in 13350 13360; do timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$p" && echo "$p OK"; done

# co říkají logy
tail -20 deepseek/logs/shim.log qwen/logs/qwen-shim.log logs/extension.log

# jsou tokeny?
python3 scripts/ensure_tokens.py --check
```

Logy se píšou do `logs/` a do `logs/extension.log` (extension nikdy netiskne do
terminálu, aby nerozbil TUI).

---

## Bezpečnost

⚠️ **Tokeny jsou živé přihlašovací údaje k účtu.** Appky je drží v plaintextu a
my je kopírujeme do `*/secrets/`. Proto:

- `secrets/`, `*_token`, `*_uid`, `*.jsonl`, `logs/` jsou v **`.gitignore`** —
  a v gitu opravdu nejsou (`git ls-files | grep -iE 'secret|token'` musí být prázdné)
- soubory mají práva `600`
- **nikdy** necommituj `secrets/` ani je nevkládej do issue
- když token omylem unikne → v appce se odhlas/přihlas (token se vymění)

WebView cookies obsahují i cookies prohlížeče (github, google…) — skript
`ensure_tokens.py` čte **jen** cookie `token` pro `*.qwen*`.

Zapisujeme pouze do:
`/data/local/tmp` (frida-server), app `filesDir`, proot rootfs, `~/.pi/agent/`.
Nikdy do `/system`, `/vendor`, `/product`, `/apex` — a nikdy rekurzivní
`chown`/`chmod` na systémové složky (viz `AGENTS.md`).

---

## Vývoj

```bash
# agenti pro fridu se bundlují (frida 17 nemá Java bridge)
cd deepseek && ./node_modules/.bin/esbuild agent/pow_rpc.src.js \
  --bundle --format=iife --platform=browser --target=es2020 --outfile=agent/pow_rpc.js

# po editaci python modulů smazat cache
rm -rf common/__pycache__ */bridge/__pycache__

# otestovat shim bez pi
curl -s localhost:13350/v1/models
curl -s -X POST localhost:13350/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"ahoj"}]}'
```

Viz taky `AGENTS.md` (prostředí, pasti, pravidla) a `PROTOCOL.md`.

---

## Licence

MIT. Není to oficiální nástroj DeepSeeku, Qwenu ani Googlu — používáš ho na
vlastní odpovědnost a v souladu s jejich podmínkami.
