"""Token-based authentication for the data server."""

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "movesense"
TOKEN_FILE = CONFIG_DIR / "token"

_bearer_scheme = HTTPBearer(auto_error=False)


def get_or_create_token() -> str:
    """Load existing token or generate a new one."""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            log.info(f"Loaded existing token from {TOKEN_FILE}")
            return token

    token = secrets.token_hex(16)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    log.info(f"Generated new token, saved to {TOKEN_FILE}")
    return token


# Module-level token, set during app startup
_active_token: str = ""


def set_active_token(token: str) -> None:
    global _active_token
    _active_token = token


def get_active_token() -> str:
    return _active_token


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    """FastAPI dependency that verifies the bearer token."""
    if credentials is None or credentials.credentials != _active_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return credentials.credentials
