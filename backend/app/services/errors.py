"""Shared transport error base (leaf module — imports nothing from the app).

Every server-type transport raises a subclass of :class:`CommandError` so the
generic API layer can catch one type regardless of protocol (Source RCON,
Satisfactory HTTPS API, ...).
"""

from __future__ import annotations


class CommandError(Exception):
    """A server command could not be executed (transport, auth or protocol)."""
