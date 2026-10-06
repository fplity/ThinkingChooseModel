"""Standalone local inference for the Laya English checkpoint."""

import argparse
import json
import sys
from pathlib import Path

from laya import Router


DEFAULT_REQUEST = Path(__file__).with_name("demo-request.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one decision locally with Laya.")
    parser.add_argument("--input", type=Path, default=DEFAULT_REQUEST, help="JSON file with state and questions")
    args = parser.parse_args()

    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Request file not found: {args.input}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {args.input}: {exc}", file=sys.stderr)
        return 2

    if not isinstance(request, dict) or not request.get("state") or not request.get("questions"):
        print("Request JSON must contain non-empty 'state' and 'questions' fields.", file=sys.stderr)
        return 2

    print("Loading Laya locally on CUDA (the first load can take a little while)...", file=sys.stderr)
    router = Router(max_loaded=1, device="cuda", preload=False)
    result = router.predict(request["state"], request["questions"], model="english")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
