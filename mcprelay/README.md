# mcprelay — MCP relay pro Grok appku

Dává **Grok appce** (`ai.x.grok`) „ruce a oči" v proot guestu na telefonu.

## Proč relay

Grok umí **custom MCP connector** (BYO), ale jeho UI tvrdě vyžaduje:

```
Enter a valid HTTPS server URL.
Locally hosted servers aren't supported.
```

→ `localhost` je zakázaný a musí to být **veřejné HTTPS**.
Telefon ale nemá veřejnou IP. Relay to řeší takhle:

```
Grok app ──HTTPS──► Render web service (veřejné HTTPS, free)
                        ├─ POST /mcp            MCP: initialize / tools/list / tools/call
                        ├─ GET  /agent/poll     ← long-poll (telefon)
                        └─ POST /agent/result
                                │  fronta
                                ▼  JEN ODCHOZÍ HTTPS (NAT-friendly)
                     telefon: agent.py v proot guestu
                                └─ spustí bash → pošle výsledek
```

Telefon **nikdy nepotřebuje veřejnou IP** — jen odchozí HTTPS.
Long-poll zároveň **drží free instanci Renderu vzhůru** (spin-down po 15 min nečinnosti).

## Nástroje

| nástroj | co dělá |
|---|---|
| `bash` | spustí shell příkaz v guestu (root) |
| `read_file` | přečte soubor |
| `write_file` | zapíše soubor |

## Nasazení (Render CLI)

```bash
render services create \
  --name mcprelay --type web_service --runtime docker \
  --repo https://github.com/zombiegirlcz/frida-mcp \
  --root-directory mcprelay --plan free \
  --health-check-path /health \
  --env-var RELAY_TOKEN=<tajny-token>
```

Výsledná URL: `https://mcprelay.onrender.com`

## Telefonní agent

```bash
RELAY_URL=https://mcprelay.onrender.com RELAY_TOKEN=<tajny-token> python3 agent.py
```

## Konfigurace

| env | default | význam |
|---|---|---|
| `PORT` | 10000 | port (Render nastavuje sám) |
| `RELAY_TOKEN` | — | bearer token pro `/agent/*` (prázdný = bez auth) |
| `RELAY_MAX_WAIT` | 55 | jak dlouho MCP volání čeká na telefon |
| `RELAY_POLL_WAIT` | 25 | jak dlouho drží long-poll |

### Agent

| env | default | význam |
|---|---|---|
| `RELAY_URL` | `http://127.0.0.1:18080` | URL relaye |
| `RELAY_TOKEN` | — | musí sedět s relayem |
| `AGENT_CMD_TIMEOUT` | 120 | timeout příkazu (s) |
| `AGENT_MAX_OUT` | 60000 | max znaků výstupu |

## Bez závislostí

Server i agent používají **jen stdlib** → malý Docker image, žádné pip instalace.
