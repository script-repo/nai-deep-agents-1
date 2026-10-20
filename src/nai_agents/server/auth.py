"""Optional auth for shared deployments: a bearer token, HTTP basic users, or both.

* `APP_AUTH_TOKEN=...`           any client presenting `Authorization: Bearer <token>` is user "token".
* `APP_BASIC_AUTH=alice:pw,bob:pw2` each user gets their own threads. A value may be a sha256 hex digest
  written as `sha256:<hex>` instead of a plain password.
With neither set, the app is open and everyone is user "local" (fine on 127.0.0.1, not on a network).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import HTTPException, Request, status

from nai_agents.core.settings import Settings


def _basic_users(settings: Settings) -> dict[str, str]:
    if not settings.app_basic_auth:
        return {}
    users = {}
    for pair in settings.app_basic_auth.get_secret_value().split(","):
        if ":" in pair:
            user, pw = pair.split(":", 1)
            users[user.strip()] = pw.strip()
    return users


def auth_mode(settings: Settings) -> str:
    modes = []
    if settings.app_auth_token:
        modes.append("token")
    if _basic_users(settings):
        modes.append("basic")
    return "+".join(modes) or "none"


def _check_password(stored: str, given: str) -> bool:
    if stored.startswith("sha256:"):
        digest = hashlib.sha256(given.encode()).hexdigest()
        return hmac.compare_digest(stored.removeprefix("sha256:"), digest)
    return hmac.compare_digest(stored.encode(), given.encode())


def identify(request: Request, settings: Settings) -> str:
    """Return the user name for this request or raise 401."""
    mode = auth_mode(settings)
    if mode == "none":
        return "local"
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    scheme = scheme.lower()
    if (
        scheme == "bearer"
        and settings.app_auth_token
        and hmac.compare_digest(value.strip().encode(), settings.app_auth_token.get_secret_value().encode())
    ):
        return "token"
    if scheme == "basic":
        try:
            user, _, pw = base64.b64decode(value.strip()).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            user, pw = "", ""
        stored = _basic_users(settings).get(user)
        if stored is not None and _check_password(stored, pw):
            return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": 'Bearer realm="nai-agents"'},
    )
