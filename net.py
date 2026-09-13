"""Force IPv4 for all outbound connections.

This machine has no IPv6 egress, and several clients (the Supabase SDK's httpx,
httpcore's sync backend) have no Happy Eyeballs: when DNS returns an IPv6
address first, the connection stalls until timeout. Restricting name
resolution to IPv4 removes that whole class of intermittent hangs across every
dependency at once.

Falls back to the normal resolver for any host with no A record, so an
IPv6-only host would still work if one were ever added.
"""

from __future__ import annotations

import socket

_original_getaddrinfo = socket.getaddrinfo
_patched = False


def force_ipv4() -> None:
    """Monkeypatch socket.getaddrinfo to return IPv4 results. Idempotent."""
    global _patched
    if _patched:
        return

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        try:
            return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        except socket.gaierror:
            return _original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = ipv4_only
    _patched = True
