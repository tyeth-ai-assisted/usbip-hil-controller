"""Bearer-token authentication.

In production: tokens are argon2id hashes stored in the DB (see scripts/mint-token.py).
Bootstrap: HIL_STATIC_TOKEN env var accepts a plaintext token for initial setup.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


def _get_static_token() -> str:
    from hil_controller.config import get_settings

    return get_settings().static_token


async def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Dependency: validate bearer token, return the token string for audit."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    token = credentials.credentials
    static = _get_static_token()

    # Static bootstrap token (plaintext)
    if static and token == static:
        return token

    # DB-backed argon2id hash check
    if await _check_db_token(request, token):
        return token

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def _check_db_token(request: Request, token: str) -> bool:
    try:
        import argon2
        from argon2 import PasswordHasher

        db_path = request.app.state.db_path
        from hil_controller.db.connection import get_db

        # token format: hil_<id>_<secret>
        parts = token.split("_", 2)
        if len(parts) != 3 or parts[0] != "hil":
            return False
        token_id, secret = parts[1], parts[2]

        async with get_db(db_path) as db:
            async with db.execute(
                "SELECT hash FROM tokens WHERE id = ? AND revoked_at IS NULL", (token_id,)
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    return False

        ph = PasswordHasher()
        try:
            ph.verify(row["hash"], secret)
            return True
        except argon2.exceptions.VerifyMismatchError:
            return False
    except Exception:
        return False
