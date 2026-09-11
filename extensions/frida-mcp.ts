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
import { appendFileSync, existsSync, mkdirSync, openSync } from "node:fs";
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
const LOG_FILE = join(ROOT, "logs", "extension.log");

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

export const PROVIDERS: Record<string, Record<string, unknown>> = {
  "deepseek-free": {
    name: "DeepSeek Free (frida)",
    api: "openai-completions",
    apiKey: "frida",
    baseUrl: `http://127.0.0.1:${DEEPSEEK_PORT}/v1`,
    compat: COMPAT,
    models: [
      {
        id: "deepseek-chat",
        name: "DeepSeek Chat (free, frida)",
        input: ["text"],
        contextWindow: 500000,
        maxTokens: 8192,
        reasoning: false,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
    ],
  },
  "qwen-free": {
    name: "Qwen Free (frida)",
    api: "openai-completions",
    apiKey: "frida",
    baseUrl: `http://127.0.0.1:${QWEN_PORT}/v1`,
    compat: COMPAT,
    models: [
      {
        id: "qwen3.7-plus",
        name: "Qwen3.7 Plus (free, frida)",
        input: ["text"],
        contextWindow: 131072,
        maxTokens: 8192,
        reasoning: false,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
      {
        id: "qwen3.8-max",
        name: "Qwen3.8 Max (free, frida)",
        input: ["text"],
        contextWindow: 131072,
        maxTokens: 8192,
        reasoning: false,
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
];

export function findPython(): string | null {
  for (const p of PY_CANDIDATES) if (existsSync(p)) return p;
  return null;
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
  log("spouštím bootstrap.sh (venv + frida) na pozadí");
  runDetached("bash", [sh], "bootstrap.log");
}

export function ensureTokens(py: string): void {
  const sh = join(ROOT, "scripts", "ensure_tokens.py");
  if (!existsSync(sh)) return;
  runDetached(py, [sh]);
}

export async function startShims(py: string, force = false): Promise<string[]> {
  const out: string[] = [];
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
  if (!py || !venvHasFrida(py)) {
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
      if (py && venvHasFrida(py)) await startShims(py);
    } catch (e) {
      log(`background init selhalo: ${e}`);
    }
  })();

  // 3) při každé session zkontroluj, že shimy žijí
  pi.on("session_start", async () => {
    try {
      if (bootstrapRunning) {
        // až bootstrap doběhne, dojde k tomu v příští session
        return;
      }
      const py = findPython();
      if (py && venvHasFrida(py)) await startShims(py);
    } catch (e) {
      log(`session_start init selhalo: ${e}`);
    }
  });

  // 4) ruční ovládání: /frida-mcp [status|start|tokens]
  pi.registerCommand("frida-mcp", {
    description: "frida-mcp: stav providerů, start shimů, refresh tokenů",
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
          ensureTokens(py);
          lines.push("tokeny: spouštím refresh na pozadí (scripts/ensure_tokens.py)");
        } else {
          lines.push("tokeny: chybí .venv s fridou — spouštím bootstrap");
          startBootstrap();
        }
      } else if (sub === "start") {
        if (py) lines.push(...(await startShims(py, true)));
        else lines.push("chybí .venv s fridou — spouštím bootstrap");
        if (!py) startBootstrap();
      } else {
        for (const [port, , name] of [
          [DEEPSEEK_PORT, "", "deepseek-free"],
          [QWEN_PORT, "", "qwen-free"],
        ] as [number, string, string][]) {
          lines.push(`${name}: port ${port} ${(await portOpen(port)) ? "OPEN ✅" : "zavřený ❌"}`);
        }
        lines.push(`python: ${py ?? "nenalezen (spouštím bootstrap)"}`);
        lines.push(`root: ${ROOT}`);
      }
      const text = lines.join("\n");
      if (ctx?.ui?.notify) ctx.ui.notify(text, "info");
      else console.error(text);
    },
  });
}
