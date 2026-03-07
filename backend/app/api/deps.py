"""
Shared FastAPI dependencies — DB session, auth verification.
"""
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_token

security_scheme = HTTPBearer()


async def get_session() -> AsyncSession:
    """Yields an async database session."""
    async for session in get_db():
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> dict:
    """Verify JWT token and return user info."""
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_current_user_sse(token: str = Query(..., description="JWT Token via Query string for SSE")) -> dict:
    """Verify JWT token from query parameter specifically for SSE Streams."""
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return payload

