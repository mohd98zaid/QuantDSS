"""Auth router — Single-user JWT login."""
from fastapi import APIRouter, HTTPException, status, Request

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.schemas.auth import LoginRequest, TokenResponse
from app.core.rate_limit import limiter

router = APIRouter()

# Lazy hash: computed on first login attempt so tests can inject settings before the hash is created.
# Uses functools.lru_cache for efficiency — the hash is computed only once.
from functools import lru_cache

@lru_cache(maxsize=1)
def _get_hashed_password() -> str:
    return get_password_hash(settings.default_password)


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(request: Request, data: LoginRequest):
    """Authenticate and receive a JWT access token."""
    if data.username != settings.default_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not verify_password(data.password, _get_hashed_password()):

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(data={"sub": data.username})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_seconds,
    )
