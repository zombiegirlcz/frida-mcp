# frida-mcp

Chat apps on Android as **pi providers** — free, locally, without API keys.

```
pi ──► localhost:13350/v1   (deepseek-free)  ──► DeepSeek app (token + PoW via frida)
   └─► localhost:13360/v1   (qwen-free)      ──► Qwen app     (token + WAF via frida)
```

Idea: chat apps have **free quota** on the model and the **valid session** lives on the phone.
Instead of paying for the API or clicking in the UI, we extract the app's own login token
and signatures (`PoW`, WAF headers) — via **frida in the same process** — and expose them to pi
as regular OpenAI-compatible models.

**Nothing is paid for, nothing is tunneled, no data leaves the phone.** Everything runs on `127.0.0.1`.

---

## Contents

- [What it can do](#what-it-can-do)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [How it works](#how-it-works)
  - [DeepSeek: token + PoW](#deepseek-token--pow)
  - [Qwen: token + WAF](#qwen-token--waf)
  - [Tool calling](#tool-calling)
  - [Streaming](#streaming)
- [Auto-extracting tokens](#auto-extracting-tokens)
- [Repository structure](#repository-structure)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Development](#development)

---

## What it can do

| provider | model | what's extracted from app |
|---|---|---|
| `deepseek-free` | `deepseek-chat`, `deepseek-reasoner` | Bearer token from MMKV + **native `DeepSeekHashV1` PoW** (without frida) |
| `qwen-free` | `qwen3.7-plus`, `qwen3.8-max` | cookie `token` from WebView |

Additionally:

- **`bard/`** — Gemini (`com.google.android.apps.bard`). App is just a shell that passes UI
  to Google app, which crashes on this device in ROM (`libmedia`). Contains a ready workaround
  (`bard/agent/nocamcrash.js`, `bard/bin/gemini-fix`).
- **`deepseek/`** — besides the provider, also original bridge to chat (`bin/frida-chat`):
  reads conversation via accessibility, runs code blocks in proot guest and returns result
  back to the chat.

---

## Requirements

| | |
|---|---|
| **Device** | Android with **root** (Magisk) and **proot guest** (NetHunter / Parrot) running pi |
| **Python** | 3.11+ (for venv and frida) |
| **Python** | 3.11+ (shims run on **stdlib** — no pip packages) |
| **C compiler** | `gcc`/`cc`/`clang` — only for fast PoW (~0.1 s instead of ~70 s) |
| ~~frida-server~~ | **no longer needed** — DeepSeekHashV1 is computed natively (legacy paths: `FRIDA_MCP_WITH_FRIDA=1`) |
| **Target apps** | DeepSeek (`com.deepseek.chat`) and/or Qwen (`ai.qwenlm.chat.android`), **logged in** |
| **Node** | 20+ (for pi) |

> Guest shares **network namespace** with Android, so loopback is enough — no `adb reverse`, no tunnels.

---

## Installation

```bash
pi install git:github.com/zombiegirlcz/frida-mcp
```

Package will be cloned to `~/.pi/agent/git/github.com/zombiegirlcz/frida-mcp` and on pi startup:

1. **Writes providers** `deepseek-free` and `qwen-free` to `~/.pi/agent/models.json`
   (backup: `models.json.bak.frida-mcp`)
2. Runs `scripts/bootstrap.sh` — verifies Python and **compiles native PoW**
   (`deepseek/native/libdspow.so`). No pip packages are installed.
3. **Extracts tokens** from apps when missing (`scripts/ensure_tokens.py`)
4. **Starts shims** on ports 13350 / 13360

Manual environment setup (if pi doesn't have time for it):

```bash
cd ~/.pi/agent/git/github.com/zombiegirlcz/frida-mcp
bash scripts/bootstrap.sh              # .venv + frida
bash scripts/deploy_frida_server.sh    # frida-server to /data/local/tmp
python3 scripts/ensure_tokens.py       # tokens from apps
```

### Control from pi

```
/frida-mcp              # status: are shims running? where's python? where's repo root?
/frida-mcp start        # (re)start both shims
/frida-mcp tokens       # re-extract tokens from apps
/frida-mcp full-context # FORGET chat -> next turn will send ENTIRE history
/frida-mcp chats        # how many chats does the shim hold
```

Use `full-context` when the previous turn **was interrupted** (Esc, crash, shim restart)
and the model "starts fresh" without context — the delta turn would go into a session
that no longer has history. The command tells the shim to forget the cached chats,
so the next message is sent as a **new chat with full history**.

This is also done automatically on **every pi session start** (safe default: full context
is always correct, delta is just an optimization).

---

## Usage

```bash
pi --model deepseek-free/deepseek-chat
pi --model qwen-free/qwen3.8-max
```

Or from shell (dev tools):

```bash
./deepseek/bin/deepseek-free start     # start/stop/status of shim
./deepseek/bin/deepseek "question"     # direct query
./qwen/bin/qwen-free start
./qwen/bin/qwen "question"
```

---

## How it works

```
pi ─► shim (OpenAI API, stdlib http.server)
         │  1. OpenAI messages + tools  →  single prompt
         │  2. asks app for signature (frida RPC / SSL hook)
         │  3. calls real app API
         │  4. SSE → OpenAI SSE (stream + tool_calls)
         └─► back to pi
```

### DeepSeek: token + PoW

- **Token** is unencrypted in the app's MMKV:
  `files/mmkv/mmkv.default` → `key_user_info` = `{"token":"<base64>","id":"<uuid>",…}`
- **PoW**: `POST /api/v0/chat/create_pow_challenge` returns `challenge` (64 hex),
  `salt`, `difficulty` (~144000). Client must find `nonce` such that

  ```
  deepseek_hash(f"{salt}_{expire_at}_{nonce}") == bytes.fromhex(challenge)
  ```

  ⚠️ Input must include **`expire_at`** from the challenge, **not** current time.

  **`DeepSeekHashV1` = SHA3-256, but skips first round of Keccak-f[1600] permutation
  (rounds 1..23)**. Everything else is standard SHA3-256
  (rate 136, padding `0x06`, 24 RC constants, 25×64bit state).

  Implementation is **native** (`deepseek/native/dspow.c` + `deepseek/bridge/pow_native.py`)
  — **frida or running app are not needed**:

  ```
  hash(6fe4581ae0fcf306e50d_1789139155175_35873)
    = 886a0939c788d0ef9b2ef84e5b98c48494fe2474ea3f62cc5abe036879415a3a   ✅
  ```

  C library **compiles itself** on first use (`gcc -O3`, ~0.1 s for entire PoW).
  If gcc is not available, pure-Python fallback is used (same result, just ~15 s)
  and only then legacy frida helper.

  Header `X-DS-PoW-Response` = `base64(JSON{algorithm,challenge,salt,signature,answer,target_path})`.

### Qwen: token + WAF

- **Token** is a cookie `token` (JWT, valid ~30 days) in WebView:
  `app_webview/Default/Cookies` (SQLite).
- `chat.qwen.ai` is behind **Aliyun WAF** — without headers it returns JS challenge:
  - `x-mini-wua` (185 B, per-request, SecurityGuard)
  - `app_waf` (48 B, stable, only on `/chat/completions`)
  - `x-device-id`, `source: app`, UA `… AliApp(QWENCHAT/2.1.1)`

  They are intercepted by `qwen/agent/qwen_hdr.js` (hook `SSL_write` in `libssl.so`) and
  `qwen/bridge/qwen_hdrd.py` saves them to `qwen/secrets/qwen_headers.json`.
  > ⚠️ **Update:** thorough testing showed that `chat.qwen.ai` **doesn't require** these
  > headers at all — just the cookie with token (and `x-device-id` can be random). Capture
  > is thus **optional** (`--capture-headers`), Qwen works even with completely empty `qwen_headers.json`.
- Flow: `POST /api/v2/chats/new` → `chat_id` → `POST /api/v2/chat/completions?chat_id=…` (SSE).
- Anonymous mode works even without login, but has **daily limit** — logged in account doesn't.

### Thinking (thinking) — `reasoning_content`

DeepSeek app can **think** and sends it **in single SSE stream** along with response.
Distinguished by fragment type:

| what arrives | fragment type | what to do |
|---|---|---|
| `response/fragments/-1/content` APPEND | `THINK` / `RESPONSE` | text to last fragment |
| `response/fragments` APPEND `[{type, content}]` | new fragment | appears at THINK → RESPONSE transition |
| `response/content` APPEND | — | normal response chunks |

Thinking is sent as **`delta.reasoning_content`** (pi displays this as thinking block),
response as `delta.content`. Tool calls and `StreamSplitter` only concern **response**.

Enabled via `thinking_enabled: true` in app API request; pi does this automatically
because the model has `reasoning: true` and `compat.thinkingFormat: "deepseek"`
→ pi sends `thinking: {type: "enabled"}`.

```bash
pi --model deepseek-free/deepseek-reasoner    # with thinking
pi --model deepseek-free/deepseek-chat        # thinking per thinking level
```

Verified on live API: `6*7` → 147 chars of thinking + response `42`; tool calling works with thinking too.

### 1 pi session = 1 chat (sync with app)

Extension sends **pi session ID** on startup to `POST /session` (`ctx.sessionManager.getSessionId()`),
so shim knows exactly which chat to use — doesn't have to guess by message prefix:

| `session_start` reason | what happens |
|---|---|
| `startup` | continues in saved chat if exists |
| `resume` | **attaches same chat** — context stays on server |
| `new` | chat starts fresh |
| `fork` | new branch |

Mapping `pi session → chat` is **saved to disk** (`logs/convstate.json`),
so it survives shim and pi restarts. This makes `resume` work even after interruption:
no "starts fresh without context".

> Without session ID (different client) message prefix comparison is used.

### One chat per conversation + just delta (to avoid detection)

Originally, shim created **new chat for each message** and sent entire history again. That's bad
for two reasons:

1. **It's conspicuous.** Normal user has one chat with N messages, we had N chats with one message each —
   perfect for automated detection (and it did happen).
2. Entire prompt is unnecessarily sent on every turn.

Server keeps the conversation **itself**, just need to attach it correctly:

```json
{"chat_session_id": "<id>", "parent_message_id": "<id of last response>"}
```

Then just send **new message**. Verified on live API — model remembers content from previous turns.

`common/convcache.py` handles this automatically: compares if we've seen this conversation before
(message prefix), and if yes, returns session + delta only:

```
[shim] delta turn in chat 49ff9183… (2 new messages, 785 chars)
```

Difference is significant — instead of entire history (single-digit KB) just a few hundred chars are sent.

> **Qwen**: its API takes only **one** message (`Invalid input too many messages`) and doesn't keep
> context in `chat_id`, so history is sent entirely. But `chat_id` is **reused** — one chat per
> conversation instead of new one per message.

### Tool calling

Our backends don't support native function calling, so `common/toolbridge.py` acts as bridge:

1. Tool definitions are inserted into prompt (strong instructions **at the start** of system message
   + JSON schemas after)
2. Model responds either
   - `<tool_call>{"name":…,"arguments":{…}}</tool_call>`, or
   - `<tool_call><invoke name="write"><parameter name="path">…</parameter>…</invoke></tool_call>`
     (longer texts — **unescaped**, which is also native DeepSeek **DSML** format)
3. Response is parsed (tolerantly: DSML, `<invoke>`, ```json fence, **bare JSON** without wrapper)
   and translated to OpenAI `tool_calls`
4. `role: tool` messages are translated back to text for the model

Parsed names are validated against actual tool list — normal JSON in response doesn't confuse
with tool call.

### Streaming

Shims **stream live**: `completion_stream()` reads SSE line-by-line and `StreamSplitter`
releases text immediately — holds only what could be start of tool call (looks for `<`, `{`, `` ` ``).
When marker appears, stops streaming and translates to `tool_calls` at the end.

Verified: deepseek ~420 chunks / 3 s, qwen ~130 chunks / 5 s.

---

## Auto-extracting tokens

`scripts/ensure_tokens.py` is idempotent and does both:

```bash
python3 scripts/ensure_tokens.py            # extract what's missing
python3 scripts/ensure_tokens.py --force    # regenerate
python3 scripts/ensure_tokens.py --check    # just report
```

App data is readable only under **real root** → script uses `sudo` ⚠️ this doesn't apply to termux proot,
I use my own application for that with special tricks https://[...]
(in proot guest). Paths are tried progressively (`/mnt/data/data`, `/data/data`, …),
because `os.path.exists()` on foreign app data won't work.

**Self-healing:** when shim's token is missing, shim extracts it itself by calling this script
and saves it (`common/tokenauto.py`). Extension does the same on startup.

---

## Repository structure

```
extensions/frida-mcp.ts     pi extension — models, tokens, start shims, /frida-mcp
common/
  toolbridge.py             tool calling bridge + StreamSplitter
  tokenauto.py              "token missing? extract it"
scripts/
  bootstrap.sh              .venv + frida
  deploy_frida_server.sh    frida-server → /data/local/tmp
  ensure_tokens.py          tokens from DeepSeek MMKV + Qwen cookies
deepseek/
  bridge/deepseek_api.py    API client (session, PoW, SSE)
  bridge/pow_native.py      DeepSeekHashV1 natively (ctypes + Python fallback)
  native/dspow.c            C implementation (auto-build on first use)
  bridge/powd.py            LEGACY: PoW via frida (no longer used)
  bridge/openai_shim.py     OpenAI-compatible server (port 13350)
  bridge/dsui.py, runner.py, bin/frida-chat   bridge to chat (accessibility + input)
  agent/pow_rpc.js          LEGACY: frida agent for PoW
  cli/deepseek.py           CLI
qwen/
  bridge/qwen_api.py        API client (cookie token, WAF headers, SSE)
  bridge/qwen_hdrd.py       frida daemon for WAF headers
  bridge/qwen_shim.py       OpenAI-compatible server (port 13360)
  agent/qwen_hdr.js         frida agent: hook SSL_write
bard/
  agent/nocamcrash.js       workaround for Gemini crash (see below)
  bridge/gemini_fixd.py     daemon that keeps it running
```

### Gemini (`bard/`) — why it crashes

`com.google.android.apps.bard` is just a shell → UI runs in Google app
(`com.google.android.googlequicksearchbox`). When opening Gemini it initializes CameraX
→ `android.media.CamcorderProfile.hasProfile()` → JNI → `MediaProfiles::hasCamcorderProfile`
in ROM `libmedia.so` **dereferences NULL** (MIUI 14 bug on Redmi Note 10, `fault addr 0x3a8`).

It's not Google's defense, not frida, not the account. XML profiles are fine.

Workaround: hook Java method and return `false`, so native call never happens → `bard/bin/gemini-fix start`
(daemon, connects to `:search`).

---

## Troubleshooting

| symptom | cause / solution |
|---|---|
| `Connection error.` in pi | shim not running / was restarted. `/frida-mcp start` |
| `[error: script has been destroyed]` | app crashed and frida script inside it died → should re-attach itself; if not, restart both app and shim |
| `missing DeepSeek token and can't extract it` | app not installed/logged in, or missing `sudo` |
| Qwen: `RateLimited … guest chat limit` | you're not logged in to Qwen app (anon has daily limit) |
| Qwen: returns HTML with `aliyun_waf_aa` | Qwen currently doesn't require WAF headers; if it appears, try `qwen/bin/qwen-free capture` |
| Qwen: `missing x-mini-wua` | old code — update (`pi install git:github.com/zombiegirlcz/frida-mcp`) |
| `frida attach failed` | harmless: native PoW doesn't need frida (frida is just fallback) |
| `500: missing pow helper` | old version (before native PoW) → update package |
| everything is slow | device is swapping; close background apps |

### Diagnostics

```bash
# is frida-server running?
timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/27042' && echo OK

# are shims running?
for p in 13350 13360; do timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$p" && echo "$p OK"; done

# what do logs say
tail -20 deepseek/logs/shim.log qwen/logs/qwen-shim.log logs/extension.log

# are there tokens?
python3 scripts/ensure_tokens.py --check
```

Logs are written to `logs/` and `logs/extension.log` (extension never prints to terminal
to avoid breaking TUI).

---

## Security

⚠️ **Tokens are live credentials to the account.** Apps keep them in plaintext and
we copy them to `*/secrets/`. Therefore:

- `secrets/`, `*_token`, `*_uid`, `*.jsonl`, `logs/` are in **`.gitignore`** —
  and not in git (`git ls-files | grep -iE 'secret|token'` must be empty)
- Files have mode `600`
- **Never** commit `secrets/` or put them in issues
- If token accidentally leaks → log out/in in the app (token is exchanged)

WebView cookies contain browser cookies too (github, google…) — script `ensure_tokens.py`
reads **only** cookie `token` for `*.qwen*`.

We write only to:
`/data/local/tmp` (frida-server), app `filesDir`, proot rootfs, `~/.pi/agent/`.
Never to `/system`, `/vendor`, `/product`, `/apex` — and never recursive
`chown`/`chmod` on system folders (see `AGENTS.md`).

---

## Development

```bash
# frida agents are bundled (frida 17 has no Java bridge)
cd deepseek && ./node_modules/.bin/esbuild agent/pow_rpc.src.js \
  --bundle --format=iife --platform=browser --target=es2020 --outfile=agent/pow_rpc.js

# clear cache after editing python modules
rm -rf common/__pycache__ */bridge/__pycache__

# test shim without pi
curl -s localhost:13350/v1/models
curl -s -X POST localhost:13350/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hello"}]}'
```

See also `AGENTS.md` (environment, pitfalls, rules) and `PROTOCOL.md`.

---

## License

MIT. This is not an official DeepSeek, Qwen, or Google tool — use it at your own risk
and in accordance with their terms.
