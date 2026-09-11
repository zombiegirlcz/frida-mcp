# AGENTS.md — frida-mcp

> Pracovní název. **Není to MCP protokol.** Žádný cloud, žádný tunel, žádné API klíče.
> Cílem je lokální bridge: Frida sedí v chat appce na zařízení, zachytí konverzaci
> a předá ji do proot guestu, kde ji zpracuje **pi**.

---

## 0. Goal

Postavit obousměrný most mezi **chat appkou v Androidu** a **proot guestem**:

```
chat appka (DeepSeek)                    proot guest
  uživatel píše do chatu                    pi (worker)
        │                                       │
        │  ① Frida agent hookuje                 │
        │     request/response                  │
        ▼                                       │
   [ bridge: JSON ]  ───────── ② ──────────────►│  pi přečte prompt
                                                │  udělá práci (shell, kód, testy)
   [ bridge: JSON ]  ◄──────── ③ ───────────────│  pi pošle výsledek
        │                                       │
        │  ④ Frida vloží odpověď do chatu        │
        ▼                                       │
  odpověď se objeví v chatu
```

**Proč:** chat appky mají free quota na model. Pi je "ruce" — umí běhat
v prootu (Kali/Parrot, gcc, python, git, …). Chceme, aby model v chatu mohl
zadávat práci a dostávat výsledky, aniž bychom platili API.

### Co NENÍ cílem
- Nepsat MCP server, neřešit MCP transport, OAuth, Streamable HTTP, tunely.
- Nemodifikovat cílovou appku (žádný re-podpis APK).
- Nesahat na ownership/perms systémových složek.

---

## 1. Prostředí (ověřená fakta, 2026-09-11)

### Zařízení
| | |
|---|---|
| kernel (host) | `4.14.190-perf-g7da93debc0ee` |
| kernel (guest) | `6.17.0-nethunter` — **fake**, pokud `NH_FAKE_SYS=1`. S `NH_FAKE_SYS=0` je reálný |
| root | **Magisk**, `/product/bin/su` → `uid=0`, kontext `u:r:magisk:s0` |
| app UID | `u0_a315` (`com.linux_core`), SELinux `u:r:untrusted_app_27:s0` |

### proot guest
| | |
|---|---|
| rootfs | `/data/user/0/com.linux_core/files/nh/distro/parrot` |
| distro | ParrotOS (Debian 13) |
| spuštění | `boot parrot -- <cmd>`, `nh distro login`, Terminál v appce |
| nástroje | Python **3.13.5**, pip 25.1.1, `curl`, `wget`, `jq`, `unzip`, `aapt`, `gcc` |
| root v guestu | `sudo <cmd>` → ano (přes `su_daemon`) |

### ⚠️ Nejdůležitější vlastnost: **sdílený network namespace**
proot **nesdílí** mount namespace s Androidem, ale **sdílí netns**. Takže:
- guest → `127.0.0.1:1337` (core REST) ✅
- guest → `127.0.0.1:27042` (frida-server) ✅
- host → `127.0.0.1:6000` (Xvfb) ✅

**Důsledek:** bridge mezi guestem a hostem může být prostý TCP na loopbacku.
Žádný `adb reverse`, žádný tunel.

### Host shell z guestu
```bash
ashell -c '<host command>'      # spustí příkaz na Android hostu jako u0_a315
ashell -c 'sudo ...'            # pozor: su najdeš na /product/bin/su (mimo PATH)
ashell -c '/product/bin/su -c id'   # → uid=0
```
`ashell` jde i z guestu přes `nethunter-ashell` / `nh` CLI.

### sudo v guestu — GOTCHA
`sudo` v guestu nejde přes appku:
```
guest sudo → su_wrapper → su_daemon (root) → execv "boot -- <cmd>"
```
Dítě **dědí jen prostředí daemonu**, které nemá žádné `NH_*`. Proto
`ProotManager` zapisuje `/data/user/0/com.linux_core/files/nh/root_env`
(bindy, `NH_FAKE_SYS`, …) a `boot` ho sourcuje, **jen když `NH_ENV_FROM_APP != 1`**.
→ **Když přidáš do appky nový bind/flag, musí jít i do tohoto souboru, jinak
ho sudo session neuvidí.**

### Porty na loopbacku (obsazené)
| port | služba |
|---|---|
| 1337 | `LocalApiServer` (core REST, Bearer token) |
| 13338 | AI agent démon |
| 13339 | VPN bypass proxy |
| 13340 | `ashell_pty` (streaming shell) |
| 6000 | Xvfb (X11 desktop) |
| **27042** | **frida-server — zatím CLOSED (nenainstalovaný)** |

### Bind diry (co guest vidí z hostu)
| bind | pref | obsah |
|---|---|---|
| `/mnt/app` | `bind_app` | `/data/user/0/com.linux_core` (app filesDir) |
| `/mnt/aiapp` | `bind_aiapp` | `/data/user/0/com.kali.aiassistant` |
| `/mnt/data` | `bind_data` | `/data` — **obsah čitelný jen pod `sudo`** (DAC + SELinux) |
| `/data/app` | vždy (D mód) | APKčka nainstalovaných appek |

⚠️ `bind_data` default **off** a bindy platí až od **příštího startu session**.
Když je `/mnt/data` "Permission denied" i pod sudo → bind v této session není.

### Komunikace s appkou
- **NE** Android Binder (Linux proces v prootu na něj nedosáhne rozumně).
- **ANO** TCP/loopback, Unix socket ve sdíleném dir, nebo JSONL soubor.

---

## 2. Cílové appky

| package | typ | poznámka |
|---|---|---|
| **`com.deepseek.chat`** | **nativní Java/Kotlin** | **cíl #1.** 3 dex (classes.dex 12 MB), žádný Flutter/RN/Hermes |
| `com.google.android.apps.bard` | Java/Kotlin, **split APK** | base + `split_config.arm64_v8a.apk` (libs jsou ve splitu) |
| `ai.qwenlm.chat.android` | neověřeno | ověřit `libflutter`/`libapp.so` |
| `com.anthropic.claude` | neověřeno | — |

### DeepSeek detaily (cíl #1)
- cesta: `/data/app/~~mTkThGoCz6dRSWQLOSJfuQ==/com.deepseek.chat-ftGCDdcDBYHf-_dFyzvJKQ==/`
- `base.apk` (14.7 MB) + `split_config.arm64_v8a.apk` (2.9 MB) + `split_config.cs.apk`
- nativní libs: `libWCDB.so` (SQLite), `libmmkv.so` (KV store), `librscrypto.so`,
  `libEncryptorP.so`, `libflipped.so`, `libsmsdk.so`, `libvolc_log.so`,
  `libapminsighta/b.so` (ByteDance APM)
- ⚠️ **`libEncryptorP` + `librscrypto`** → část payloadů může být šifrovaná/signovaná.
  Hookovat **nad** šifrováním (Java vrstva), ne na nativní TLS, dokud se neověří.

---

## 3. Hook vrstvy — co je možné

| vrstva | hook | dostaneš | kdy použít |
|---|---|---|---|
| **Java HTTP** | `okhttp3.RequestBody`/`ResponseBody`, `java.net.HttpURLConnection.getOutputStream`, `HttpURLConnection.getInputStream` | hotové JSON tělo requestu/odpovědi | **DeepSeek — začni tady** |
| **Java SSE/stream** | `okhttp3.internal.http2.*`, `ResponseBody.source()` | streamované chunky odpovědi | chat odpovědi chodí po SSE |
| **nativní TLS** | `SSL_write`/`SSL_read` v `libssl.so` (BoringSSL) | HTTP/2 framy, nutné HPACK parsování | app-agnostické, fallback |
| **Dart/Flutter** | Dart VM (`libapp.so`) | — | jen když je appka Flutter (DeepSeek **není**) |
| **WebView** | `WebViewClient.shouldInterceptRequest` | URL + postData | když chat běží ve WebView |

### Frida messaging — jak to funguje (to je odpověď na „JSON nebo curl?")
- Agent (JS) běží **uvnitř cílového procesu**.
- `send(payload)` → host dostane `{type:"send", payload:{...}}`
- host → agent: `script.post(payload)` → v agentu `recv(cb)`
- payload **musí být JSON-serializovatelný**; binárka jde zvlášť:
  `send(meta, rawBytes)` (ArrayBuffer) → dorazí jako oddělený binary blob.
- **`curl` není formát**, jen HTTP klient. Volba je jen (a) co poslat a (b) jakým
  transportem to poslat mezi hostem a guestem.

### ⚠️ OVĚŘENO NA ŽIVÉM ZAŘÍZENÍ (2026-09-11) — čti dřív, než začneš

| věc | výsledek |
|---|---|
| `nh device accessibility --json` | ✅ **oči** — čte foreground appku (text/x/y/class) |
| `nh device tap` / `click` | ❌ **nefunguje na DeepSeeku** (appka ignoruje accessibility gesta) |
| root `input tap` / `input text` / `keyevent` | ✅ **ruce** (input subsystem) |
| `uiautomator dump` | ✅ funguje, ale **nevidí Compose input text** (accessibility ano) |
| `nh fix permission <path>` | ✅ opraví vlastnictví souborů vytvořených pod real rootem |
| Frida `dev.spawn()` | ❌ `unable to locate android.os.Process.setArgV0() slot` (17.18 + MIUI) → **attach** |
| Frida attach + `libssl.so!SSL_write/SSL_read` hook | ✅ funguje (HTTP/1.1 i HTTP/2 preface) |

- **DeepSeek je Compose appka** → v accessibility není strom ani role, jen plochý seznam.
  Input se jeví jako `EditText` až po fokusu; klávesnice posouvá layout (`y 2154 → 1315`).
- **Frida 17 nemá Java bridge** → bundlovat `frida-java-bridge` esbuildem
  (`--bundle --format=iife --platform=browser --target=es2020`, polyfill `buffer`).
- **`sudo` = real root** → soubory pod `sudo` mají na hostu `root:root`, ne `u0_a315`.
  Vždy pak `nh fix permission <path>`.
- Incident „nefunguje OAuth" (2026-09-11) způsobil **jiný agent smazáním dat účtů**, ne frida.

### Implementováno (MVP)

- `bridge/dsui.py` — oči + ruce (accessibility + `input`), klasifikace zpráv, `send_message()`.
- `bin/frida-chat` — `status` / `read` / `send` / `watch [--port N | --connect H:P]`.
- stream JSONL na `127.0.0.1:13341` (+ `bridge/chat.jsonl` jako audit).
- `server/frida-supervise.sh`, `server/frida-stop.sh`, `bridge/attach_when_ready.py`.
- `skills/frida-chat/SKILL.md` (i v `/root/.agents/skills/frida-chat/`).

### Rizika u DeepSeeku
1. **Certificate pinning** — OkHttp `CertificatePinner` → pro hooky na Java vrstvě
   vlastně nevadí (neděláme MITM, jsme v procesu). Vadilo by jen u TLS vrstvy.
2. **Integrity/root detekce** (ByteDance APM `libapminsight`) — může appku shodit
   při detekci `frida-server`. Řešit přejmenováním frida-serveru / gadget módem.
3. **Payload šifrování** (`libEncryptorP`) — pokud je tělo šifrované, hookuj
   `RequestBody.writeTo` (plaintext) ne nativní `SSL_write`.

---

## 4. Formát přenosu (návrh kontraktu)

Jeden JSON objekt na řádek (**JSONL**), vždy s verzí a směrem:

```json
{"v":1,"dir":"in", "ts":1757580000123,"src":"com.deepseek.chat","kind":"prompt","text":"..."}
{"v":1,"dir":"out","ts":1757580000456,"src":"com.deepseek.chat","kind":"reply", "text":"..."}
{"v":1,"dir":"to_chat","ts":1757580001000,"kind":"inject","text":"výsledek..."}
```

- `dir`: `in` = co uživatel poslal, `out` = co model odpověděl, `to_chat` = chceme vložit
- `kind`: `prompt` | `reply` | `inject` | `error`
- Zapisovat **atomicky**: zapsat do `.tmp` + `mv` (jinak čtenář uvidí půlku řádku)

---

## 5. Transport host ↔ guest (vyber jeden, doporučení = UDS)

| # | varianta | pro | proti |
|---|---|---|---|
| 1 | **Unix domain socket** ve sdíleném dir | nejnižší latence, žádný port ven, netřeba token | UDS musí být v diru viditelném z obou stran |
| 2 | **TCP `127.0.0.1:PORT`** | triviální pro pi (`curl`/`nc`) | na port se dostane každá appka → **nutný token** |
| 3 | **JSONL soubor** | restart-safe, žádná služba | polling, latence, musí být atomický zápis |

Doporučení: **#1 pro živý stream** (+ #3 jako audit log pro replay/debug).
Porty držet v rozsahu **13341+** (1337–13340 jsou obsazené).

### Kdo co spouští
Frida **host** (ten, kdo injectne agenta) může běžet **v prootu**:
`python3` + `frida` modul, který se připojí na `frida-server` na `127.0.0.1:27042`.
→ pi, agent-logika i bridge jsou na jednom místě, do appky vede jen injekce.

---

## 6. Co má být postaveno (deliverables)

### Fáze 0 — probe (nic neměnit)
- [ ] `frida --version` / `python3 -c "import frida"` v guestu
- [ ] `frida-ps -U` (přes frida-server) → vidí procesy?
- [ ] ověřit hooknutelnost DeepSeeku: `frida -U -f com.deepseek.chat -l probe.js`
- [ ] zjistit, čím appka posílá requesty (`okhttp3` v `classes.dex`?)

### Fáze 1 — frida-server deploy
- [ ] stáhnout `frida-server-<verze>-android-arm64` (verze **musí** odpovídat
      verzi python `frida` modulu)
- [ ] nasadit do **`/data/local/tmp`** (NIKDY `/system`), `chmod 755`, spustit rootem
- [ ] supervision: restart při pádu (jednoduchý `while true` loop v shellu stačí)

### Fáze 2 — agent + host runner
- [ ] `agent/deepseek.js` — hookuje OkHttp, `send()`uje prompt/reply
- [ ] `bridge/host.py` — attach/spawn, přijímá `send()`, zapisuje do bridge
- [ ] `bridge/inject.py` — přijme `to_chat`, vloží do appky
- [ ] reconnection logika (appka se restartuje, proces umírá)

### Fáze 3 — pi strana
- [ ] CLI `frida-chat` v guestu: `frida-chat tail`, `frida-chat send "..."`,
      `frida-chat status`
- [ ] **pi skill** (`SKILL.md`) s README stylu pi — viz `/root/.agents/skills/`
- [ ] loop: prompt → pi → inject

### Fáze 4 — hardening
- [ ] pokud appka detekuje fridu → přejmenovat server, nebo gadget mód
- [ ] rate limiting, aby se chat nezahltil
- [ ] audit log (JSONL) pro replay

---

## 7. PRAVIDLA — co nikdy nedělat

### Ownership (bootloop incident 2026-08-23)
- **NIKDY** rekurzivní `chown`/`chmod` na `/system`, `/data`, `/vendor`, `/product`, `/apex`.
- **NIKDY** měnit ownership/perms systémových složek.
- Zapisovat **jen** do: `/data/local/tmp`, `/data/adb`, app `filesDir`,
  nebo do rootfs guestu.
- Ownership fixy používat cíleně (`chown <uid>:<gid> <soubor>`), nikdy `-R /data`.

### Ostatní
- Cílovou appku **nere-podepisovat** a nemodifikovat její APK.
- `frida-server` **nikdy** do `/system` — jen `/data/local/tmp` nebo `/data/adb`.
- Nezvyšovat `versionCode` appek, nic neinstalovat přes `pm` z untrusted_app
  (nejde to — chybí oprávnění).
- Nezakládat tunely/veřejné porty — všechno je lokální.

### Ověřování
- **Nevěřit, ověřit.** Před tvrzením „funguje" spustit příkaz a ukázat výstup.
- Testovat na **živém zařízení** (`ashell -c`, guest), ne jen na papíře.
- Když něco nejde, najít **root cause** (reprodukce + důkaz), ne hádat.

### Komunikace
- S uživatelem **česky**.

---

## 8. Rychlé probe příkazy

```bash
# jsem v guestu?
uname -a; id

# root?
sudo id

# host pohled
ashell -c 'id'
ashell -c '/product/bin/su -c id'

# co je na loopbacku
ashell -c 'for p in 1337 13338 13339 13340 6000 27042; do toybox nc -w1 127.0.0.1 $p </dev/null >/dev/null 2>&1 && echo "$p OPEN" || echo "$p closed"; done'

# vidí guest /data?
ls /mnt/data | head            # Permission denied = bind není nebo nejsi root
sudo ls /mnt/data | head        # musí fungovat (bind_data + sudo)

# APK cílové appky (root)
sudo ls -d /mnt/data/app/*deepseek*/      # jen když je bind_data AKTIVNÍ v této session
sudo ls -la /data/app/*deepseek*/         # funguje vždy pod sudo

# jakou vrstvu appka má (Flutter/RN/Java)
sudo unzip -l <base.apk> | grep -oE 'lib/arm64-v8a/[A-Za-z0-9_.-]+\.so' | sort -u

# frida v guestu
python3 -c "import frida; print(frida.__version__)" 2>&1
frida-ps -U 2>&1 | head
```

### Zjištěné pasti
- Assety z `pull_full_assets()` **nemají exec bit** → `assets/usr/bin/boot`
  spouštět přes `/system/bin/sh <cesta>`, ne napřímo.
- `/mnt/data` je v session **jen když byl `bind_data` zapnutý při jejím startu**.
- `pm` a `su` **nejsou v guest PATH** (guest PATH má jen guest diry) → používat
  `/system/bin/pm`, `/product/bin/su`.
- Glob nad `/data/app/*/` se nerozšíří jako ne-root (nelze číst dir) → spouštět
  celé jako `sudo sh -c`.
