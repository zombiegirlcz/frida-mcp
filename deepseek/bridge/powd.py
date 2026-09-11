"""powd — PoW solver přes frida (volá nativní DeepSeekHashV1 v appce).

Použití:
    from bridge.powd import PowHelper
    p = PowHelper()
    p.attach()                       # připojí se na běžící DeepSeek
    answer = p.solve(challenge, salt, difficulty)

Pozn.: `salt` se zde bere v původní podobě (bez timestampu) — formát
`<salt>_<ts>_` vytvoří solve() sám (odpovídá odchycenému reálnému volání).
"""

from __future__ import annotations

import os
import time

import frida

AGENT = os.path.join(os.path.dirname(__file__), "..", "agent", "pow_rpc.js")
PKG = "com.deepseek.chat"
PROC_NAME = "DeepSeek"
SERVER = "127.0.0.1:27042"


class PowHelper:
    def __init__(self, server: str = SERVER):
        self.server = server
        self.dev = frida.get_device_manager().add_remote_device(server)
        self.session = None
        self.script = None
        self.pid: int | None = None

    def _find_pid(self) -> int:
        for p in self.dev.enumerate_processes():
            if p.name == PROC_NAME:
                return p.pid
        raise RuntimeError(f"proces {PROC_NAME} neběží (spusť DeepSeek)")

    def attach(self) -> None:
        pid = self._find_pid()
        self.session = self.dev.attach(pid)
        self.script = self.session.create_script(open(AGENT, encoding="utf-8").read())
        self.script.load()
        self.pid = pid

    def detach(self) -> None:
        if self.session:
            try:
                self.session.detach()
            except Exception:  # noqa: BLE001
                pass
            self.session = None
        self.script = None
        self.pid = None

    def alive(self) -> bool:
        """Zije skript i proces, do ktereho jsme ho nalozili?

        Appka casto umira (malo pameti) a frida skript v ni zustane mrtvy
        ("script has been destroyed"); PID se pritom zmeni.
        """
        if self.script is None or self.pid is None:
            return False
        try:
            return self._find_pid() == self.pid
        except Exception:  # noqa: BLE001
            return False

    def solve(self, challenge: dict) -> int:
        """Vrátí nonce (answer). challenge = dict z create_pow_challenge.

        Důležité: nativní funkce očekává arg1 = f"{salt}_{expire_at}_"
        (NE aktuální čas — jinak vrátí -1).
        """
        salt_ts = f"{challenge['salt']}_{challenge['expire_at']}_"
        args = (salt_ts, challenge['challenge'], challenge['difficulty'])
        for attempt in (1, 2):
            if not self.alive():
                self.detach()
                self.attach()
            try:
                return int(self.script.exports_sync.solve(*args))
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                dead = any(k in msg for k in
                           ("destroyed", "gone", "not found", "invalid", "closed", "detached"))
                if attempt == 2 or not dead:
                    raise
                # skript umrel -> re-attach a zkus jeste jednou
                self.detach()
