"""Helpers shared by the suites."""

import socket


def closed_port() -> int:
    """A loopback port nothing listens on: bound, read, released."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
