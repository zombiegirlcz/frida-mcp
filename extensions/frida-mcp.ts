/**
 * frida-mcp — pi extension
 *
 * Zpřístupní chat appky v Androidu jako pi providery:
 *   - deepseek-free  → DeepSeek chat (token z MMKV appky, PoW přes fridu)
 *   - qwen-free      → Qwen chat (cookie token z WebView, WAF hlavičky přes fridu)
 *
 * Co extension dělá:
 *   1. zapíše providery do ~/.pi/agent/models.json (a zaregistruje je pro tuto session)
 *   2. ověří, že existuje .venv s fridou (případně spustí scripts/bootstrap.sh)
 *   3. vytáhne tokeny z appek, když chybí (scripts/ensure_tokens.py)
 *   4. nastartuje lokální OpenAI-compatible shimy (porty 13350 a 13360)
 *
 * Vše je idempotentní: co už běží / existuje, se nedělá znovu.
 * Těžké věci (bootstrap, tokeny) běží na pozadí, aby nezdržovaly start pi.
 */

import { spawn, spawnSync } from "node:child_process";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { readFile, rename, writeFile } from "node:fs/promises";
import net from "node:net";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// ---------------------------------------------------------------- cesty

function packageRoot(): string {
  try {
    return dirname(dirname(fileURLToPath(import.meta.url)));
  } catch {
    return process.cwd();
  }
}

const ROOT = packageRoot();
const MODELS_JSON = join(homedir(), ".pi", "agent", "models.json");
const CONFIG_JSON = join(ROOT, ".frida-mcp-config.json");
const LOG_FILE = join(ROOT, "logs", "extension.log");

interface McpConfig {
  temperature?: number;
}

function loadConfig(): McpConfig {
  try { return JSON.parse(readFileSync(CONFIG_JSON, "utf8")); }
  catch { return {}; }
}

function saveConfig(cfg: McpConfig): void {
  writeFileSync(CONFIG_JSON, JSON.stringify(cfg, null, 2));
}

// Načti uloženou teplotu na startu
const savedCfg = loadConfig();
if (savedCfg.temperature !== undefined) {
  process.env.FRIDA_MCP_TEMPERATURE = String(savedCfg.temperature);
  log(`načtena uložena teplota: ${savedCfg.temperature}`);
}

export function log(msg: string): void {
  try {
    mkdirSync(dirname(LOG_FILE), { recursive: true });
    appendFileSync(LOG_FILE, `[${new Date().toISOString().slice(11, 19)}] ${msg}\n`);
  } catch {
    /* ticho — log nesmí shodit start pi */
  }
}

// ---------------------------------------------------------------- modely

export const DEEPSEEK_PORT = 13350;
export const QWEN_PORT = 13360;

const COMPAT = {
  supportsDeveloperRole: false,
  supportsReasoningEffort: false,
  supportsStrictMode: false,
  supportsUsageInStreaming: false,
  supportsFinishReason: true,
};

// DeepSeek appka ma vlastni mysleni: posila fragmenty typu THINK (mysleni)
// a RESPONSE (odpoved). pi to umi zobrazit, kdyz model dostane `reasoning: true`
// a `thinkingFormat: "deepseek"` -> pi posle `thinking: {type: "enabled"}`.
const COMPAT_THINKING = { ...COMPAT, thinkingFormat: "deepseek" };

export const PROVIDERS: Record<string, Record<string, unknown>> = {
  "deepseek-free": {
    name: "DeepSeek Free (nativni)",
    api: "openai-completions",
    apiKey: "frida",
    baseUrl: `http://127.0.0.1:${DEEPSEEK_PORT}/v1`,
    compat: COMPAT_THINKING,
    models: [
      {
        id: "deepseek-chat",
        name: "DeepSeek Chat (free, nativni PoW)",
        input: ["text"],
        contextWindow: 1000000,
        maxTokens: 8192,
        reasoning: true,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
      {
        id: "deepseek-reasoner",
        name: "DeepSeek Reasoner (free, mysleni)",
        input: ["text"],
        contextWindow: 1000000,
        maxTokens: 8192,
        reasoning: true,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
    ],
  },
  "qwen-free": {
    name: "Qwen Free (nativni)",
    api: "openai-completions",
    apiKey: "frida",
    baseUrl: `http://127.0.0.1:${QWEN_PORT}/v1`,
    compat: COMPAT_THINKING,
    models: [
      {
        id: "qwen3.7-plus",
        name: "Qwen3.7 Plus (free)",
        input: ["text"],
        contextWindow: 1000000,
        maxTokens: 8192,
        reasoning: true,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
      {
        id: "qwen3.8-max",
        name: "Qwen3.8 Max (free)",
        input: ["text"],
        contextWindow: 1000000,
        maxTokens: 8192,
        reasoning: true,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
    ],
  },
};

/** Zapíše/aktualizuje providery v models.json (atomicky, se zálohou). */
export async function upsertModels(): Promise<string> {
  let cfg: Record<string, any> = { providers: {} };
  try {
    cfg = JSON.parse(await readFile(MODELS_JSON, "utf8"));
  } catch {
    /* soubor neexistuje / je rozbitý → začneme z prázdna */
  }
  cfg.providers ??= {};
  const changed = Object.entries(PROVIDERS).filter(
    ([id, p]) => JSON.stringify(cfg.providers[id]) !== JSON.stringify(p),
  );
  if (changed.length === 0) return "models.json je aktuální";
  try {
    await writeFile(`${MODELS_JSON}.bak.frida-mcp`, JSON.stringify(cfg, null, 2));
  } catch {
    /* záloha není kritická */
  }
  for (const [id, p] of Object.entries(PROVIDERS)) cfg.providers[id] = p;
  const tmp = `${MODELS_JSON}.tmp.frida-mcp`;
  await writeFile(tmp, JSON.stringify(cfg, null, 2));
  await rename(tmp, MODELS_JSON);
  return `models.json: aktualizováno ${changed.map(([id]) => id).join(", ")}`;
}

// ---------------------------------------------------------------- python / procesy

const PY_CANDIDATES = [
  join(ROOT, ".venv", "bin", "python"),
  join(ROOT, "deepseek", ".venv", "bin", "python"),
  join(ROOT, "qwen", ".venv", "bin", "python"),
  "/usr/local/bin/python3",
  "/usr/bin/python3",
  "/usr/bin/python",
];

/** Jmena v PATH jako posledni zachrana (kdyz zadny venv neexistuje). */
const PY_PATH_NAMES = ["python3", "python"];

/**
 * Vrati pouzitelny Python.
 *
 * POZOR: od verze s nativnim PoW se .venv uz NEVYTVARI (frida neni potreba),
 * takze spoléhat jen na `.venv/bin/python` znamenalo, ze po `pi update --all`
 * (ktery .venv smaze) se shimy prestaly startovat. Proto fallback na systemovy
 * python z PATH.
 */
export function findPython(): string | null {
  for (const p of PY_CANDIDATES) if (existsSync(p)) return p;
  for (const c of PY_PATH_NAMES) {
    try {
      if (spawnSyncQuiet(c, ["-c", "import sys"]) === 0) return c;
    } catch {
      /* zkus dalsi */
    }
  }
  return null;
}

/** Staci, ze python existuje a umi stdlib — frida uz NENI potreba.
 *  (DeepSeekHashV1 se pocita nativne v C / Pythonu, Qwen WAF hlavicky
 *  nevyzaduje.) */
function pythonOk(py: string): boolean {
  // POZOR: spawnSyncQuiet vraci EXIT STATUS (cislo), ne stdout!
  // (driv tu bylo `=== "3"`, coz nikdy neplatilo -> pythonOk() vzdy false
  //  -> extension jen bootstrapoval a shimy nikdy nenastartovaly)
  return spawnSyncQuiet(py, ["-c", "import sys"]) === 0;
}

function venvHasFrida(py: string): boolean {
  try {
    const r = spawnSyncQuiet(py, ["-c", "import frida;print(frida.__version__)"]);
    return r === 0;
  } catch {
    return false;
  }
}

function spawnSyncQuiet(cmd: string, args: string[]): number {
  const r = spawnSync(cmd, args, { timeout: 30000 });
  return r.status ?? 1;
}

export function portOpen(port: number, timeoutMs = 1200): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.connect({ host: "127.0.0.1", port });
    const done = (v: boolean) => {
      s.destroy();
      resolve(v);
    };
    s.setTimeout(timeoutMs);
    s.once("connect", () => done(true));
    s.once("timeout", () => done(false));
    s.once("error", () => done(false));
  });
}

/**
 * Rekne shimum, ktera pi session je aktivni.
 *
 * Diky tomu plati **1 pi session = 1 chat** na strane API:
 *  - `resume` navaze STEJNY chat (server si drzi kontext, nemusime posilat vse)
 *  - `new`     zacne chat od znova
 *  - mapovani se uklada na disk, takze prezije restart shimu
 */
export async function tellSession(
  sessionId: string | undefined,
  reason: string,
  fresh = false,
): Promise<void> {
  if (!sessionId) return;
  for (const port of [DEEPSEEK_PORT, QWEN_PORT]) {
    await postJson(port, "/session", { id: sessionId, reason, fresh });
  }
}

/** Jednoduchy JSON GET na lokalni shim (null kdyz nebezi). */
async function getJson(port: number, path: string): Promise<any | null> {
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`, {
      signal: AbortSignal.timeout(4000),
    });
    return await r.json();
  } catch {
    return null;
  }
}

/** Jednoduchy JSON POST na lokalni shim (null kdyz nebezi). */
async function postJson(port: number, path: string, body?: any): Promise<any | null> {
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(8000),
    });
    return await r.json();
  } catch {
    return null;
  }
}

/**
 * Zapomene zapamatovane chaty na obou shimech -> dalsi tah posle CELOU
 * historii v novem chatu.
 *
 * Volame pri startu kazde pi session: je to bezpecny default. Kdyz se
 * predchozi tah prerusil (Esc, pad), serverova session uz historii mit
 * nemusi a delta tah by poslal modelu jen zlomek -> "zacal by znovu".
 * Plny kontext je vzdy spravne, delta je jen optimalizace.
 */
export async function resetConversations(): Promise<number> {
  let cleared = 0;
  for (const port of [DEEPSEEK_PORT, QWEN_PORT]) {
    const r = await postJson(port, "/reset");
    if (r && r.ok) cleared += Number(r.cleared) || 0;
  }
  return cleared;
}

/** Spustí proces na pozadí; stdout+stderr jde do souboru (kvůli diagnostice). */
function runDetached(cmd: string, args: string[], logName?: string, cwd = ROOT): void {
  let stdio: any = "ignore";
  if (logName) {
    try {
      mkdirSync(join(ROOT, "logs"), { recursive: true });
      const fd = openSync(join(ROOT, "logs", logName), "a");
      stdio = ["ignore", fd, fd];
    } catch {
      stdio = "ignore";
    }
  }
  const child = spawn(cmd, args, { cwd, detached: true, stdio });
  child.unref();
}

// ---------------------------------------------------------------- akce

let bootstrapRunning = false;

export function startBootstrap(): void {
  if (bootstrapRunning) {
    log("bootstrap už běží — přeskakuji");
    return;
  }
  const sh = join(ROOT, "scripts", "bootstrap.sh");
  if (!existsSync(sh)) {
    log("bootstrap.sh nenalezen — nemůžu vytvořit .venv s fridou");
    return;
  }
  bootstrapRunning = true;
  log("spouštím bootstrap.sh (python + pripadne gcc pro PoW) na pozadí");
  runDetached("bash", [sh], "bootstrap.log");
}

export function ensureTokens(py: string): void {
  const sh = join(ROOT, "scripts", "ensure_tokens.py");
  if (!existsSync(sh)) return;
  runDetached(py, [sh]);
}

// Cesty k tokenum (klon i dev repo si je drzi zvlast)
const DEEPSEEK_TOKEN_FILE = join(ROOT, "deepseek", "secrets", "deepseek_token");
const QWEN_TOKEN_FILE = join(ROOT, "qwen", "secrets", "qwen_token");

/**
 * Vynuti obnovu tokenu: smaze stary token + cache a teprve pak vytahne novy.
 *
 * Proc mazat: `ensure_tokens.py` bez `--force` preskoci token mladsi 12 h,
 * takze po prenuti uctu v appce zustane stary (neplatny) token. Smazanim
 * zarucime, ze se opravdu nacte novy. A protoze shim drzi klienta v `_api`
 * cache, po obnove tokenu RESTARTUJEME shimy — jinak by porad pouzivaly
 * stary token z pameti.
 */
export async function refreshTokens(py: string): Promise<string[]> {
  const out: string[] = [];
  for (const f of [DEEPSEEK_TOKEN_FILE, QWEN_TOKEN_FILE]) {
    try {
      unlinkSync(f);
      out.push(`smazán starý ${f.split("/").slice(-2).join("/")}`);
    } catch {
      /* neexistoval */
    }
  }
  const sh = join(ROOT, "scripts", "ensure_tokens.py");
  if (!existsSync(sh)) {
    out.push("chybí scripts/ensure_tokens.py");
    return out;
  }
  runDetached(py, [sh, "--force"], "tokens.log");
  // pockame, az se novy token objevi (appka ho musi mit zapsany v MMKV)
  const t0 = Date.now();
  while (Date.now() - t0 < 90000) {
    if (existsSync(DEEPSEEK_TOKEN_FILE)) break;
    await new Promise((r) => setTimeout(r, 1500));
  }
  if (existsSync(DEEPSEEK_TOKEN_FILE)) {
    const peek = readFileSync(DEEPSEEK_TOKEN_FILE, "utf8").trim().slice(0, 12);
    out.push(`nový token načten z appky (${peek}…)`);
  } else {
    out.push("token se nepodařilo načíst — koukej do logs/tokens.log "
             + "(je appka přihlášená? je vidět v MMKV?)");
  }
  // restart shimu -> zahodi cache klienta se starym tokenem
  out.push(...(await startShims(py, true)));
  out.push("shimy restartovány s novým tokenem");
  return out;
}

/**
 * Zastavi bezici shimy (deepseek/qwen) skenovanim /proc.
 *
 * Zamerne NEpouzivame `pkill -f <pattern>` — prikazova radka samotneho
 * pkill by pattern obsahovala (a v minulosti to zabilo vlastni shell).
 * Pres /proc zabijeme presne jen procesy, jejichz cmdline obsahuje
 * nazev shim skriptu, a nikdy vlastni pid.
 */
export function stopShims(): number {
  let killed = 0;
  let pids: string[] = [];
  try {
    pids = readdirSync("/proc").filter((p) => /^\d+$/.test(p));
  } catch {
    return 0;
  }
  for (const p of pids) {
    const pid = Number(p);
    if (pid === process.pid) continue;
    let cmd = "";
    try {
      cmd = readFileSync(`/proc/${p}/cmdline`, "utf8");
    } catch {
      continue; // proces mezitim zmizel
    }
    if (cmd.includes("openai_shim.py") || cmd.includes("qwen_shim.py")) {
      try {
        process.kill(pid, "SIGKILL");
        killed++;
      } catch {
        /* uz nebezi */
      }
    }
  }
  return killed;
}

export async function startShims(py: string, force = false): Promise<string[]> {
  const out: string[] = [];
  if (force) {
    const n = stopShims();
    if (n > 0) out.push(`zastaveno starych shimu: ${n}`);
    // pockej, nez se uvolni porty
    await new Promise((r) => setTimeout(r, 1200));
  }
  const jobs: [number, string, string][] = [
    [DEEPSEEK_PORT, join(ROOT, "deepseek", "bridge", "openai_shim.py"), "deepseek-free"],
    [QWEN_PORT, join(ROOT, "qwen", "bridge", "qwen_shim.py"), "qwen-free"],
  ];
  for (const [port, script, name] of jobs) {
    if (!force && (await portOpen(port))) {
      out.push(`${name}: už běží (${port})`);
      continue;
    }
    if (!existsSync(script)) {
      out.push(`${name}: chybí ${script}`);
      continue;
    }
    runDetached(py, [script, "--port", String(port)], `${name}.log`);
    out.push(`${name}: startuji na ${port}`);
    log(`startuji ${name} (${script} --port ${port})`);
  }
  return out;
}

/** Ověří tokeny na pozadí (bez blokování startu pi). */
function ensureTokensSandboxed(): void {
  if (bootstrapRunning) return;
  const py = findPython();
  if (!py || !pythonOk(py)) {
    startBootstrap();
    return;
  }
  ensureTokens(py);
}

// ---------------------------------------------------------------- extension

export default async function fridaMcp(pi: ExtensionAPI): Promise<void> {
  // 1) modely — potřebujeme je dřív, než pi vyhodnotí dostupné modely
  try {
    log(await upsertModels());
  } catch (e) {
    log(`upsertModels selhalo: ${e}`);
  }
  for (const [id, cfg] of Object.entries(PROVIDERS)) {
    try {
      (pi as any).registerProvider(id, cfg);
    } catch (e) {
      log(`registerProvider(${id}) selhalo: ${e}`);
    }
  }

  // 2) zbytek na pozadí (nesmí zdržet start)
  void (async () => {
    try {
      ensureTokensSandboxed();
      const py = findPython();
      if (py && pythonOk(py)) await startShims(py);
    } catch (e) {
      log(`background init selhalo: ${e}`);
    }
  })();

  // 3) při každé session zkontroluj, že shimy žijí
  pi.on("session_start", async (event: any, ctx: any) => {
    try {
      if (bootstrapRunning) {
        // až bootstrap doběhne, dojde k tomu v příští session
        return;
      }
      const py = findPython();
      if (py && pythonOk(py)) await startShims(py);

      // Synchronizace s pi session: jeden pi session = jeden chat.
      const reason = String(event?.reason ?? "startup");
      let sid: string | undefined;
      try {
        sid = ctx?.sessionManager?.getSessionId?.();
      } catch {
        sid = undefined;
      }
      if (!sid) sid = process.env.PI_SESSION_ID;
      await tellSession(sid, reason);
      log(`session_start: reason=${reason} pi-session=${sid ? sid.slice(0, 8) : "?"}…`);
    } catch (e) {
      log(`session_start init selhalo: ${e}`);
    }
  });

  // 4) Kdyz pi zkompaktuje kontext (auto pri prekroceni okna, nebo /compact),
  //    prestavi se prompty na [summary + par poslednich zprav]. Stara session
  //    v appce by dostala jen deltu, ktera nedava smysl -> proto pri kompakci
  //    zahodime chat a dalsi tah posle zkompaktovany kontext do NOVEHO chatu.
  pi.on("session_compact", async (event: any, ctx: any) => {
    try {
      let sid: string | undefined;
      try {
        sid = ctx?.sessionManager?.getSessionId?.();
      } catch {
        sid = undefined;
      }
      if (!sid) sid = process.env.PI_SESSION_ID;
      const reason = String(event?.reason ?? "compact");
      await tellSession(sid, `compact:${reason}`, true);
      log(`session_compact: reason=${reason} -> novy chat v appce (fresh)`);
    } catch (e) {
      log(`session_compact selhalo: ${e}`);
    }
  });

  // 5) rucni ovladani: /frida-mcp [status|start|tokens]
  pi.registerCommand("frida-mcp", {
    description: "frida-mcp: status | start | tokens | temp <0.0-2.0> | full-context (reset chatu) | chats",
    handler: async (args: string, ctx: any) => {
      const sub = (args || "status").trim().split(/\s+/)[0];
      const py = findPython();
      const lines: string[] = [];
      try {
        lines.push(await upsertModels());
      } catch (e) {
        lines.push(`models.json: ${e}`);
      }
      if (sub === "tokens") {
        if (py) {
          lines.push("tokeny: VYNUCENÁ obnova (smažu starý token + cache)");
          lines.push(...(await refreshTokens(py)));
        } else {
          lines.push("tokeny: chybí python — spouštím bootstrap");
          startBootstrap();
        }
      } else if (sub === "start") {
        if (py) lines.push(...(await startShims(py, true)));
        else lines.push("chybí python — spouštím bootstrap");
        if (!py) startBootstrap();
      } else if (sub === "full-context" || sub === "full" || sub === "reset"
                 || sub === "ctx") {
        // Zahodi zapamatovane chaty -> pristi tah posle CELOU historii v novem
        // chatu. Pouzij, kdyz se predchozi tah prerusil a model "zacina znovu".
        let cleared = 0;
        for (const [port, name] of [[DEEPSEEK_PORT, "deepseek-free"],
                                    [QWEN_PORT, "qwen-free"]] as [number, string][]) {
          const r = await postJson(port, "/reset");
          if (r && r.ok) {
            cleared += Number(r.cleared) || 0;
            lines.push(`${name}: chat zapomenut (zruseno ${r.cleared}) -> dalsi tah posle cely kontext`);
          } else if (r === null) {
            lines.push(`${name}: shim nebezi (port ${port})`);
          } else {
            lines.push(`${name}: reset selhal`);
          }
        }
        lines.push(`hotovo (celkem zruseno ${cleared}). Posli dalsi zpravu.`);
      } else if (sub === "temp" || sub === "temperature") {
        // Teplota modelu. Appka ma vysoky default (chat), coz zvyraznuje
        // halucinace; nizsi hodnota (0.2-0.4) dela tool cally spolehlivejsi.
        // Po zmene se shimy RESTARTUJI, aby se nova hodnota nacetla
        // (FRIDA_MCP_TEMPERATURE se cte pri startu procesu).
        const arg = args.trim().split(/\s+/)[1];
        if (!arg) {
          lines.push(`pouziti: /frida-mcp temp <hodnota>   (napr. 0.3)`);
          lines.push(`soucasna teplota: ${process.env.FRIDA_MCP_TEMPERATURE ?? "(default 0.3)"}`);
        } else {
          const val = Number.parseFloat(arg);
          if (Number.isNaN(val) || val < 0 || val > 2) {
            lines.push(`chybna hodnota: ${arg} (ocekava se 0.0-2.0)`);
          } else {
            process.env.FRIDA_MCP_TEMPERATURE = String(val);
            saveConfig({ ...loadConfig(), temperature: val });
            lines.push(`teplota = ${val} (ulozeno do .frida-mcp-config.json)`);
            const py = findPython();
            if (py) {
              lines.push(...(await startShims(py, true)));
              lines.push("shimy restartovany -> nova teplota se nacetla");
            } else {
              lines.push("chybi python - restartuj pres /frida-mcp start");
            }
          }
        }
      } else if (sub === "chats" || sub === "conversations") {
        for (const [port, name] of [[DEEPSEEK_PORT, "deepseek-free"],
                                    [QWEN_PORT, "qwen-free"]] as [number, string][]) {
          const r = await getJson(port, "/conversations");
          if (!r) {
            lines.push(`${name}: shim nebezi`);
            continue;
          }
          lines.push(`${name}: ${r.entries} chatu, aktivni pi-session: `
                     + (r.current ? `${String(r.current).slice(0, 8)}…` : "(neznamá)"));
          for (const s of (r.sessions ?? []).slice(-5)) {
            lines.push(`   chat ${String(s).slice(0, 8)}…`);
          }
        }
      } else {
        for (const [port, , name] of [
          [DEEPSEEK_PORT, "", "deepseek-free"],
          [QWEN_PORT, "", "qwen-free"],
        ] as [number, string, string][]) {
          lines.push(`${name}: port ${port} ${(await portOpen(port)) ? "OPEN ✅" : "zavřený ❌"}`);
        }
        lines.push(`python: ${py ?? "nenalezen (spouštím bootstrap)"}`);
        lines.push(`root: ${ROOT}`);
        lines.push("prikazy: status | start | tokens | temp <0.0-2.0> | full-context | chats");
      }
      const text = lines.join("\n");
      if (ctx?.ui?.notify) ctx.ui.notify(text, "info");
      else console.error(text);
    },
  });
}
