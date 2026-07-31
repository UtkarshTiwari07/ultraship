"""CLI: ratecon <file.txt> [--provider openai|anthropic|replay]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .llm import get_client
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ratecon")
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--provider", default="openai",
                    choices=["openai", "anthropic", "deepseek", "replay"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--replay-dir", default="tests/fixtures/replay")
    ap.add_argument("--contract-only", action="store_true",
                    help="print only the 12 contract keys, no diagnostics")
    args = ap.parse_args(argv)

    results = []
    for path in args.files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if args.provider == "replay":
            client = get_client("replay", directory=args.replay_dir, key=path.stem)
        else:
            client = get_client(args.provider, model=args.model)
        res = run(text, client)
        payload = {"source": path.name, "load": res.load.model_dump()}
        if not args.contract_only:
            payload["_meta"] = res.meta
        results.append(payload)

    json.dump(results if len(results) > 1 else results[0], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
