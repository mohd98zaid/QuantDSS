"""Auth router — Single-user JWT login."""
from fastapi import APIRouter, HTTPException, status

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()

# Pre-hash the default password at startup
_hashed_password = get_password_hash(settings.default_password)


@router.post("/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Authenticate and receive a JWT access token."""
    if request.username != settings.default_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not verify_password(request.password, _hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(data={"sub": request.username})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_seconds,
    )
