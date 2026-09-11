"""DeepSeek UI adapter — oči a ruce pro pi.

Vrstvy:
  * "oči"  : `nh device accessibility --json` (čte foreground appku)
  * "ruce" : root `input tap` / `input text` / `input keyevent`
             (POZOR: `nh device tap` na DeepSeeku NEFUNGUJE — accessibility gesta
              jsou appkou ignorována; `input` jde přes input subsystem a funguje)

Vše běží v proot guestu. `input` se pouští na Android hostu přes su.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass

SU = "/product/bin/su"
PKG = "com.deepseek.chat"

# UI chrome / ovládací prvky, které v přepisu konverzace nechceme
NOISE = {
    "Otevřít boční panel", "Nový chat", "Přiložit soubory", "Přepnout na hlas",
    "Přemýšlení", "Hledat", "Kopírovat", "Vygenerováno službou DeepSeek AI",
    "Přejít dolů", "Zobrazit nabídku", "Vícenásobný výběr", "Hledat obsah chatu…",
    "Hledat obsah chatu...", "Odeslat", "Napište zprávu",
    "Napište zprávu nebo podržte a mluvte", "Stop", "Zastavit", "Obnovit",
    "Zavřít",
    # akční tlačítka pod odpovědí / UI rysy
    "Líbí se", "Nelíbí", "Sdílet", "Kopírovat", "Zkopírováno", "Regenerovat",
    "Automatické předčítání", "Rozbalit", "Sbalit", "Upravit", "Smazat",
    "Přehrát", "Zastavit předčítání", "Více",
    # TTS / čtečka
    "Předchozí", "Další", "Přečíst nahlas", "/",
}
NOISE_PREFIX = ("Nalezeno ", "Zdroje", "Vygenerováno", "Přemýšlel ", "Myšlenkový proces")
NOISE_RE = re.compile(r"^Zpráva \d+ z \d+$")

# jazyky code bloků — DeepSeek UI je zobrazuje jako samostatný label vlevo
CODE_LANGS = {
    "bash", "sh", "shell", "zsh", "console", "fish",
    "python", "python3", "py", "pip",
    "json", "yaml", "yml", "toml", "ini", "env", "text", "txt", "plaintext",
    "c", "cpp", "c++", "h", "hpp", "rust", "go", "java", "kotlin", "swift",
    "javascript", "js", "typescript", "ts", "html", "css", "scss", "xml",
    "sql", "makefile", "dockerfile", "diff", "patch", "lua", "php", "ruby",
    "perl", "r", "matlab", "scala", "dart", "objectivec", "asm",
}

TOP_BAR_Y = 180          # uzly nad tímto y jsou horní lišta / chrome


def _sh(cmd: str, timeout: int = 30) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "").rstrip("\n")


def host_root(cmd: str, timeout: int = 30) -> str:
    """Spustí příkaz na Android hostu jako root (přes ashell → su)."""
    safe = cmd.replace('"', '\\"')
    return _sh(f"ashell -c '{SU} -c \"{safe}\"'", timeout=timeout)


@dataclass
class Node:
    text: str
    x: int
    y: int
    clickable: bool
    cls: str

    @property
    def short_cls(self) -> str:
        return self.cls.rsplit(".", 1)[-1]


class UiError(RuntimeError):
    pass


def dump(timeout: int = 30) -> list[Node]:
    """Vrátí accessibility uzly foreground appky."""
    out = _sh("nh device accessibility --json", timeout=timeout)
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise UiError(f"accessibility dump selhal: {out[:200]!r}")
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise UiError(f"accessibility JSON neplatný: {e}; {out[:200]!r}")
    return [Node(t.get("text") or "", int(t.get("x", 0)), int(t.get("y", 0)),
                 bool(t.get("clickable")), t.get("class") or "") for t in raw]


def foreground_pkg(timeout: int = 20) -> str:
    out = host_root("dumpsys window 2>/dev/null | grep mCurrentFocus | head -1", timeout=timeout)
    m = re.search(r"u0 ([A-Za-z0-9_.]+)/", out)
    return m.group(1) if m else ""


def is_foreground() -> bool:
    return foreground_pkg() == PKG


def tap(x: int, y: int) -> None:
    host_root(f"input tap {int(x)} {int(y)}")


def keyevent(code: int) -> None:
    host_root(f"input keyevent {int(code)}")


KEYCODE_DEL = 67
KEYCODE_PASTE = 279


def set_clipboard(text: str) -> None:
    """Zapíše text do systémové schránky (`nh system clipboard set`)."""
    subprocess.run(["nh", "system", "clipboard", "set", text],
                   capture_output=True, text=True, timeout=30)


def paste_text(text: str) -> None:
    """Vloží text přes schránku — jediná cesta, jak dostat víceřádkový text.

    `input text` neumí `\n` (rozsekne shell příkaz) a neescapuje backticky
    spolehlivě. Clipboard + KEYCODE_PASTE zvládne cokoli.
    """
    set_clipboard(text)
    time.sleep(0.3)
    keyevent(KEYCODE_PASTE)
    time.sleep(0.4)


def _escape_text(s: str) -> str:
    # `input text` používá %s jako mezeru; escapujeme shell-neschůdné znaky.
    s = s.replace(" ", "%s")
    for ch in "\n\r\"'`$\\!&|;<>()[]{}*?~#":
        s = s.replace(ch, "\\" + ch)
    return s


def type_text(s: str) -> None:
    """Jednořádkový text přes `input text` (rychlý, ale křehký)."""
    host_root(f"input text {_escape_text(s)}")


def clear_input() -> None:
    """Vymaže obsah inputu (podle skutečné délky, ne naslepo 400x DEL)."""
    try:
        nodes = dump()
    except UiError:
        nodes = []
    n = find_input(nodes)
    cur = (n.text or "") if n else ""
    if cur.startswith("Napište zprávu") or not cur:
        return
    keyevent(123)  # KEYCODE_MOVE_END
    for _ in range(len(cur) + 16):
        keyevent(KEYCODE_DEL)


def find_input(nodes: list[Node]) -> Node | None:
    """Najde Compose text field (v accessibility se jeví jako EditText)."""
    for n in nodes:
        if n.short_cls == "EditText":
            return n
    for n in nodes:
        if "Napište zprávu" in n.text:
            return n
    return None


def find_send(nodes: list[Node]) -> Node | None:
    for n in nodes:
        if n.text.strip() in ("Odeslat", "Send"):
            return n
    return None


# ---------------------------------------------------------------- klasifikace

def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if t in NOISE:
        return True
    if any(t.startswith(p) for p in NOISE_PREFIX):
        return True
    if NOISE_RE.match(t):
        return True
    # krátké číselné / symbolové značky z markdown listů ("1.", "3.", "…")
    if re.fullmatch(r"\d+[.)]?", t):
        return True
    return False


@dataclass
class Message:
    role: str          # "user" | "assistant" | "?"
    text: str
    x: int
    y: int


def messages(nodes: list[Node], width: int = 1080) -> list[Message]:
    """Z accessibility uzlů vytvoří seznam zpráv (heuristika role podle zarovnání).

    DeepSeek je Compose appka: v accessibility tree není strom ani role, jen
    plochý seznam TextView/View. Uživatelské bubliny jsou zarovnané vpravo,
    odpovědi AI zleva (dlouhé span celou šířku → center ≈ 540).

    Code bloky navíc UI rozsekne na **label jazyka** (malé x vlevo) + **obsah**
    a zahodí ``` fence — tady je zase slepíme do ```lang ... ```, aby je
    `runner.extract_commands()` našel.
    """
    msgs: list[Message] = []
    pending_lang: str | None = None
    for n in sorted(nodes, key=lambda x: (x.y, x.x)):
        t = (n.text or "").strip()
        if not t or n.short_cls == "EditText":
            continue
        if n.y < TOP_BAR_Y:                     # horní lišta / chrome
            continue
        if _is_noise(t):
            continue
        low = t.lower()
        if low in CODE_LANGS and n.x < width * 0.30:
            pending_lang = low                # label code bloku
            continue
        role = "user" if n.x > width * 0.62 else "assistant"
        if pending_lang:
            t = f"```{pending_lang}\n{t}\n```"
            role = "assistant"
            pending_lang = None
        msgs.append(Message(role, t, n.x, n.y))
    return msgs


def conversation(nodes: list[Node] | None = None, width: int = 1080) -> list[Message]:
    return messages(nodes if nodes is not None else dump(), width=width)


# ---------------------------------------------------------------- akce

def focus_input(timeout: float = 6.0) -> Node:
    """Fokusuje input a počká, až se objeví klávesnice (layout se posune!)."""
    nodes = dump()
    inp = find_input(nodes)
    if inp is None:
        # zkusit typické místo bez klávesnice
        tap(538, 2154)
    else:
        tap(inp.x, inp.y)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            nodes = dump()
        except UiError:
            continue
        n = find_input(nodes)
        if n is not None and n.short_cls == "EditText":
            return n
    raise UiError("nepodařilo se fokusovat input")


def send_message(text: str, settle: float = 1.2) -> None:
    """Napíše a odešle zprávu do DeepSeeku (ruce). Víceřádkový text přes schránku."""
    focus_input()
    clear_input()
    time.sleep(0.3)
    paste_text(text)
    time.sleep(settle)
    nodes = dump()
    btn = find_send(nodes)
    if btn is None:
        # fallback: zkusit `input text` (jednořádkově)
        one = " ".join(text.split())
        type_text(one)
        time.sleep(settle)
        nodes = dump()
        btn = find_send(nodes)
    if btn is None:
        raise UiError("tlačítko Odeslat nenalezeno (text se nepropsal?)")
    tap(btn.x, btn.y)
    time.sleep(settle)


def status() -> dict:
    try:
        nodes = dump()
    except UiError as e:
        return {"ok": False, "error": str(e), "foreground": foreground_pkg()}
    return {
        "ok": True,
        "foreground": foreground_pkg(),
        "nodes": len(nodes),
        "has_input": find_input(nodes) is not None,
        "has_send": find_send(nodes) is not None,
        "messages": len(conversation(nodes)),
    }
