"""Small standalone client for the TypeSafe Jev System One API."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


API_ROOT = "https://api.typesafe.ai/v1"
DEFAULT_REQUEST = Path(__file__).with_name("demo-request.json")


def call_api(path: str, api_key: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{API_ROOT}{path}", data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"TypeSafe API returned HTTP {exc.code}: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
    except urllib.error.URLError as exc:
        print(f"Could not reach the TypeSafe API: {exc.reason}", file=sys.stderr)
        raise SystemExit(1) from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Try Jev's typed decision API independently.")
    parser.add_argument("--input", type=Path, default=DEFAULT_REQUEST, help="JSON request file (default: demo-request.json)")
    parser.add_argument("--list-models", action="store_true", help="List models available to this TypeSafe account")
    args = parser.parse_args()

    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        print("TYPESAFE_API_KEY is not set. Run this through Run-Demo.ps1 to enter it without echo.", file=sys.stderr)
        return 2

    if args.list_models:
        result = call_api("/models", api_key)
    else:
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"Request file not found: {args.input}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON in {args.input}: {exc}", file=sys.stderr)
            return 2

        if not isinstance(payload, dict) or not payload.get("state") or not payload.get("questions"):
            print("Request JSON must contain non-empty 'state' and 'questions' fields.", file=sys.stderr)
            return 2
        payload.setdefault("model", "jev-latest")
        result = call_api("/systemone", api_key, payload)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
