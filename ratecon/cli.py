"""CLI: ratecon <file> [--provider openai|anthropic|deepseek|replay]

<file> may be .txt, .pdf, .docx or an image (.png/.jpg/...); the text is
extracted by ratecon.ingest before the pipeline runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .ingest import to_text
from .llm import get_client
from .pipeline import run


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into the environment.

    Dependency-free, so `DEEPSEEK_API_KEY` etc. can live in a .env file
    without exporting them by hand. A variable already set in the real
    environment always wins over the file (``setdefault``).
    """
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
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
        # Accept .txt / .pdf / .docx / images; extract text up front.
        text = to_text(path)
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
