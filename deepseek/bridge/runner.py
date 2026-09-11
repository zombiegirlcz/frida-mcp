"""runner — vykoná code bloky z odpovědí DeepSeeku a vrátí výsledek.

Smyčka:
    assistant zpráva ──extract_commands──► seznam (lang, code)
        └─► run_command (bash/python v prootu, timeout, truncate)
              └─► format_result ──dsui.send_message──► zpět do chatu
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass

# ```bash ... ```  (i s prázdným nebo neznámým jazykem)
FENCE_RE = re.compile(r"```([A-Za-z0-9_+.-]*)[ \t]*\n(.*?)```", re.S)

SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", ":"}
PY_LANGS = {"python", "py", "python3"}

DEFAULT_TIMEOUT = 60
MAX_OUTPUT = 16_000          # bajtů na příkaz
CWD = "/root"

# Tvrdý blok: nikdy nespouštět (ownership/bootloop incident, destrukce zařízení)
FORBIDDEN = (
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", "dd of=/dev/block",
    "chown -R", "chmod -R 777 /", "> /dev/block", "reboot", "shutdown",
    "fastboot", "pm uninstall", "pm clear",
)


@dataclass
class Command:
    lang: str
    code: str

    @property
    def runnable(self) -> bool:
        return self.lang in SHELL_LANGS or self.lang in PY_LANGS


@dataclass
class Result:
    lang: str
    code: str
    exit_code: int
    output: str
    timed_out: bool = False
    blocked: bool = False


def extract_commands(text: str) -> list[Command]:
    """Vytáhne code bloky. Bloky bez jazyka se považují za shell."""
    out: list[Command] = []
    for m in FENCE_RE.finditer(text or ""):
        lang = (m.group(1) or "").strip().lower()
        code = m.group(2).strip("\n")
        if not code:
            continue
        if lang == "":
            lang = "bash"
        out.append(Command(lang=lang, code=code))
    return out


PLACEHOLDER_RE = re.compile(r"^\s*<[^>]{1,40}>\s*$", re.M)

def _blocked(code: str) -> str | None:
    # placeholdery z dokumentace (<příkazy>, <kód>, ...) — nespouštět
    stripped = "\n".join(l for l in code.splitlines() if l.strip())
    if stripped and all(PLACEHOLDER_RE.match(l) or l.strip().startswith("#") for l in stripped.splitlines()):
        return "placeholder (dokumentace)"
    low = code.lower()
    for bad in FORBIDDEN:
        if bad in low:
            return bad
    return None


def run_command(cmd: Command, timeout: int = DEFAULT_TIMEOUT, cwd: str = CWD) -> Result:
    if not cmd.runnable:
        return Result(cmd.lang, cmd.code, 0, "(nepodporovaný jazyk — nespouštěno)")

    bad = _blocked(cmd.code)
    if bad:
        return Result(cmd.lang, cmd.code, 126, f"⛔ zablokováno bezpečnostním filtrem ({bad!r})",
                      blocked=True)

    if cmd.lang in PY_LANGS:
        argv = ["python3", "-c", cmd.code]
    else:
        argv = ["bash", "-c", cmd.code]

    env = dict(os.environ)
    env.setdefault("LANG", "C.UTF-8")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return Result(cmd.lang, cmd.code, 124, f"⏱ timeout po {timeout}s", timed_out=True)

    out = (p.stdout or "") + (p.stderr or "")
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n… (zkráceno, celkem {len(out)} B)"
    return Result(cmd.lang, cmd.code, p.returncode, out)


def format_result(r: Result) -> str:
    head = f"### `{r.lang}` — exit {r.exit_code}"
    if r.timed_out:
        head += " (timeout)"
    body = r.output.rstrip() or "(žádný výstup)"
    return f"{head}\n```\n{body}\n```"


def run_commands(cmds: list[Command], timeout: int = DEFAULT_TIMEOUT,
                 cwd: str = CWD) -> str:
    """Spustí bloky popořadě, zastaví se na první chybě."""
    parts: list[str] = []
    for c in cmds:
        if not c.runnable:
            continue
        r = run_command(c, timeout=timeout, cwd=cwd)
        parts.append(format_result(r))
        if r.exit_code != 0:
            break
    return "\n\n".join(parts)


def should_continue(text: str) -> bool:
    """True = odpověď obsahuje spustitelné bloky, má smysl pokračovat."""
    return any(c.runnable for c in extract_commands(text))
