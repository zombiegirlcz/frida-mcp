#!/usr/bin/env python3
"""probe_qwen — ZJISTI PRESNE, co shim posila Qwenu a co Qwen vraci.

Zadne hadani: vytiskne
  1. cely PROMPT, ktery jde na chat.qwen.ai (presne bajty)
  2. RAW SSE odpoved z Qwenu (prvni i posledni framy)
  3. co z toho vyleze pres StreamSplitter / parse_tool_calls

Nepotrebuje appku ani fridu — pouzije token z qwen/secrets/qwen_token.

    python3 scripts/probe_qwen.py            # maly prompt + schema bash/read
    python3 scripts/probe_qwen.py --full     # vezme posledni realny request
                                             # z /tmp/qwen_req.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "qwen"))

from bridge.qwen_api import QwenAPI                       # noqa: E402
from common.toolbridge import (StreamSplitter, build_prompt,  # noqa: E402
                               cap_messages, parse_tool_calls,
                               tool_names, tool_specs)

TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Execute a bash command",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read", "description": "Read a file",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
]


def hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="vezmi posledni realny request z /tmp/qwen_req.jsonl")
    ap.add_argument("--model", default=os.environ.get("QWEN_MODEL", "qwen3.8-max"))
    a = ap.parse_args()

    if a.full:
        path = "/tmp/qwen_req.jsonl"
        if not os.path.exists(path):
            print(f"chybi {path} — spust bez --full", file=sys.stderr)
            return 2
        last = None
        for line in open(path, encoding="utf-8"):
            last = line
        body = json.loads(last)
        msgs = body.get("messages") or []
        tools = body.get("tools") or TOOLS
        hr("VZATO Z REALNEHO REQUESTU")
        print(f"zprav v requestu: {len(msgs)}, tools: {len(tools)}")
        msgs, trimmed = cap_messages(msgs, 400000)
        print(f"po cap_messages(400000): {len(msgs)} zprav, zkraceno={trimmed}")
    else:
        tools = TOOLS
        msgs = [
            {"role": "developer",
             "content": "Jsi coding agent. Mas nastroje a MUSIS je pouzivat."},
            {"role": "user",
             "content": "Zjisti, kolik je hodin. Pouzij k tomu nastroj bash."},
        ]

    prompt = build_prompt(msgs, tools)
    hr(f"PROMPT, KTERY JDE NA QWEN ({len(prompt)} znaku)")
    print(prompt[:4000])
    if len(prompt) > 4000:
        print(f"\n…[zbytek, celkem {len(prompt)} znaku]…")

    hr("ODESILAM NA chat.qwen.ai")
    api = QwenAPI(model=a.model)
    cid = api.new_chat()
    print(f"chat_id: {cid}")

    raw_frames: list[str] = []
    phases: dict[str, int] = {}
    chunks: list[tuple[str, str]] = []

    for ch in api.completion(cid, prompt, model=a.model, thinking=False):
        raw_frames.append(json.dumps(ch, ensure_ascii=False))
        phases[ch.get("phase") or "?"] = phases.get(ch.get("phase") or "?", 0) + 1
        chunks.append((ch.get("phase") or "", ch.get("text") or ""))

    hr(f"ODPOVED Z QWENU — {len(chunks)} chunku, faze: {phases}")
    for i, (ph, tx) in enumerate(chunks[:15]):
        print(f"  [{i}] phase={ph!r} text={tx[:120]!r}")
    if len(chunks) > 15:
        print(f"  … a dalsich {len(chunks) - 15} chunku")

    joined = "".join(tx for _, tx in chunks)
    hr(f"CELY TEXT OD QWENU ({len(joined)} znaku)")
    print(joined[:3000])
    if len(joined) > 3000:
        print(f"\n…[zbytek, celkem {len(joined)} znaku]…")

    hr("CO Z TOHO VYLEZE (parse_tool_calls)")
    text, calls = parse_tool_calls(joined, tool_names(tools), tool_specs(tools))
    print(f"viditelny text: {text[:500]!r}")
    print(f"tool_calls: {json.dumps(calls, ensure_ascii=False)[:800]}")

    hr("STREAM SPLITTER (co by dostal pi po chuncich)")
    sp = StreamSplitter(tool_names(tools), tool_specs(tools))
    out = []
    for _, tx in chunks:
        p = sp.feed(tx)
        if p:
            out.append(p)
    tail, scalls = sp.finish()
    if tail:
        out.append(tail)
    print(f"streamovany text: {''.join(out)[:500]!r}")
    print(f"stream tool_calls: {json.dumps(scalls, ensure_ascii=False)[:800]}")
    print(f"obsahuje halucinaci 'does not exist': "
          f"{'does not exist' in joined.lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())