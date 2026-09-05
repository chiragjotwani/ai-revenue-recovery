"""API-key authentication and role-based authorization (Phase 15:
Security & Fintech Hardening).

Every mutating endpoint in this system (ingest an event, transition a
recovery case, run a diagnosis, approve/execute a recovery action,
rebuild the analytics warehouse) changes state that ultimately traces
back to real money movement in later phases. Before Phase 15 none of
these endpoints required any credential at all.

Design, deliberately minimal (no session/JWT/OAuth machinery -- this is
a service-to-service backend with no end-user login flow anywhere in
this codebase):

* A caller presents a key via the ``X-API-Key`` header.
* ``Settings.api_keys`` maps each configured key to a ``Role``
  (``operator`` or ``readonly``), parsed once from an environment
  variable of the form ``key1:operator,key2:readonly``.
* ``require_role(Role.READONLY)`` accepts either role (readonly is the
  floor); ``require_role(Role.OPERATOR)`` accepts only operator keys.
* An unknown or missing key is a ``401``; a known key with insufficient
  role is a ``403`` -- callers can tell "who are you" apart from "you
  can't do that" (Section 15-style fintech hardening expectation).

In production (``Settings.is_production``), an empty ``api_keys`` map is
a hard startup failure (``RuntimeError`` in ``create_app``) rather than
silently leaving every endpoint open -- see ``app/main.py``. In
development/test, an empty map is allowed (keeps `docker compose up`
usable without provisioning keys first) but every request is then
rejected with ``401`` all the same -- there is no "auth disabled" mode,
only "no keys are valid yet".
"""

from __future__ import annotations

import hmac
from enum import Enum
from typing import Any

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

API_KEY_HEADER_NAME = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


class Role(str, Enum):
    """Ordered by privilege: OPERATOR can do everything READONLY can."""

    READONLY = "readonly"
    OPERATOR = "operator"


#: Roles a caller may hold that satisfy a `require_role(minimum)` check,
#: keyed by the minimum role a route declares.
_SATISFIES: dict[Role, frozenset[Role]] = {
    Role.READONLY: frozenset({Role.READONLY, Role.OPERATOR}),
    Role.OPERATOR: frozenset({Role.OPERATOR}),
}


def parse_api_keys(raw: str) -> dict[str, Role]:
    """Parses ``Settings.api_keys_raw`` (``"key1:operator,key2:readonly"``)
    into a key -> Role map. Blank entries are ignored (so a trailing
    comma or an unset env var both yield an empty map, not an error).
    """
    keys: dict[str, Role] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        key, _, role_name = entry.partition(":")
        key = key.strip()
        role_name = role_name.strip().lower()
        if not key or role_name not in (Role.READONLY.value, Role.OPERATOR.value):
            raise ValueError(
                f"Malformed API_KEYS entry {entry!r}: expected 'key:operator' or 'key:readonly'"
            )
        keys[key] = Role(role_name)
    return keys


def _lookup_role(presented_key: str, configured: dict[str, Role]) -> Role | None:
    """Constant-time-per-candidate lookup -- avoids a timing side channel
    that would let an attacker learn which configured key is "closest"
    to theirs one byte at a time. The number of configured keys is not
    secret, only their values are, so iterating all of them is fine.
    """
    for candidate, role in configured.items():
        if hmac.compare_digest(candidate, presented_key):
            return role
    return None


def require_role(minimum: Role) -> Any:
    """Returns a FastAPI dependency that enforces ``minimum`` as the
    floor role for the route it guards. Use as
    ``Depends(require_role(Role.OPERATOR))``.
    """

    async def _dependency(
        presented_key: str | None = Security(_api_key_header),
    ) -> Role:
        if not presented_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key",
                headers={"WWW-Authenticate": API_KEY_HEADER_NAME},
            )
        configured = get_settings().api_keys
        role = _lookup_role(presented_key, configured)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": API_KEY_HEADER_NAME},
            )
        if role not in _SATISFIES[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {role.value!r} may not perform this action",
            )
        return role

    return Depends(_dependency)
