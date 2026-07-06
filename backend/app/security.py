"""Authentication helpers: password check + JWT issue/verify."""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .config import Settings, get_settings

# Token URL is relative to the API root; used by Swagger UI's auth button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ALGORITHM = "HS256"


def verify_credentials(username: str, password: str, settings: Settings) -> bool:
    """Constant-time check of the submitted credentials against configuration."""
    user_ok = hmac.compare_digest(username or "", settings.dashboard_username)
    pass_ok = hmac.compare_digest(password or "", settings.dashboard_password)
    return user_ok and pass_ok


def create_access_token(subject: str, settings: Settings) -> str:
    """Create a signed JWT for the given subject (username)."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    """FastAPI dependency that validates the bearer token and returns the username."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise credentials_error from exc

    username = payload.get("sub")
    if not username:
        raise credentials_error
    return username
