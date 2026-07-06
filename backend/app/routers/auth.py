"""Authentication routes: issue a JWT for valid dashboard credentials."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings, get_settings
from ..schemas import LoginRequest, TokenResponse
from ..security import create_access_token, get_current_user, verify_credentials

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Validate credentials and return a bearer token."""
    if not verify_credentials(payload.username, payload.password, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(payload.username, settings)
    return TokenResponse(access_token=token, username=payload.username)


@router.get("/me")
async def me(current_user: str = Depends(get_current_user)) -> dict[str, str]:
    """Return the current authenticated user (used to validate a stored token)."""
    return {"username": current_user}
