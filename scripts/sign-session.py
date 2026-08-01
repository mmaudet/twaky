"""One-shot: print a signed session cookie value.

Usage: uv run python scripts/sign-session.py <email>
"""

import sys

from twaky.api.session import sign_session


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sign-session.py <email>", file=sys.stderr)
        return 2
    print(sign_session(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
