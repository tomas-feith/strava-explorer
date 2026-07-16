"""Pick a free TCP port, preferring a given one.

Dash aborts if its port is taken, which happens whenever another local
dashboard is already running on 8050. app.py calls find_free_port() on startup
rather than hard-coding the port.

    python freeport.py        # prints 8050, or the next free port
    python freeport.py 8600   # start looking at 8600 instead

The chosen port goes to stdout (so a script can capture it); anything human-
facing goes to stderr. Stdlib only, so this runs before any deps are installed.
"""

from __future__ import annotations

import socket
import sys

DEFAULT_PORT = 8050
SEARCH_LIMIT = 50


def is_free(port: int) -> bool:
    """True if a server can bind this port on all interfaces, as Dash does.

    Binds without SO_REUSEADDR on purpose: on Windows that option permits
    binding a port another process already holds, which would report every
    port as free.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("", port))
        except OSError:
            return False
    return True


def find_free_port(preferred: int = DEFAULT_PORT, limit: int = SEARCH_LIMIT) -> int:
    for port in range(preferred, preferred + limit):
        if is_free(port):
            return port
    raise RuntimeError(f"no free port in {preferred}..{preferred + limit - 1}")


def main(argv: list[str]) -> int:
    preferred = int(argv[0]) if argv else DEFAULT_PORT
    try:
        port = find_free_port(preferred)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if port != preferred:
        print(f">> port {preferred} is in use, using {port} instead", file=sys.stderr)
    print(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
