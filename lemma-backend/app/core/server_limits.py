"""What the API server reads off the wire before the app sees it.

uvicorn assembles a websocket message whole before handing it to the app, so a
check in the app -- the live socket's 16 KiB frame limit -- comes after the
memory is spent. ``WS_MAX_BYTES`` is the server's own ceiling: a message past it
is refused while it arrives, and the socket closed with 1009 (message too big).

It is one value for every socket the API serves, and every launcher passes it:
``ws_max_size=WS_MAX_BYTES`` where uvicorn is started from Python,
``--ws-max-size 16777216`` where it is started from a command line (the
Dockerfile, Railway, compose, ``make dev``, Desktop and the local stack). The
image also sets ``UVICORN_WS_MAX_SIZE`` for a command that leaves the flag out.
``test_ws_max_size_everywhere`` holds every launcher to this number.

Why 16 MiB, not less: the Agent Host link (``/agent-host/link``) is a socket of
the same server, and a Desktop host already installed sends messages past
1 MiB on it -- an op's data is up to 1 MiB before base64 (a file read, a
process's output), and a batch of run events carries generated images inline,
up to 256 events with no byte bound. A smaller ceiling closes those links with
1009, and the host resends the same message after every reconnect.
"""

from __future__ import annotations

#: The largest websocket message the API server reads: 16 MiB.
WS_MAX_BYTES = 16 * 1024 * 1024

__all__ = ["WS_MAX_BYTES"]
