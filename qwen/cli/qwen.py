#!/usr/bin/env python3
"""qwen — CLI pro Qwen chat API (jeden dotaz nebo REPL)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge.qwen_api import QwenAPI, DEFAULT_MODEL

def main():
    q = QwenAPI()
    args = [a for a in sys.argv[1:]]
    model = DEFAULT_MODEL
    if args and args[0].startswith("--model="):
        model = args[0].split("=",1)[1]; args = args[1:]
    if args:
        print(q.ask(" ".join(args), model=model)); return 0
    print(f"Qwen REPL (model {model}) — Ctrl-D konec")
    while True:
        try: line = input("▸ ")
        except (EOFError, KeyboardInterrupt): print(); return 0
        if not line.strip(): continue
        print(q.ask(line, model=model))

if __name__ == "__main__":
    raise SystemExit(main())
