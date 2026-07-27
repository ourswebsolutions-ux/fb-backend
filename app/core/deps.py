"""
Shared FastAPI dependencies — authentication helpers.

Usage:
    from app.core.deps import get_current_user

    @router.get("/")
    async def my_route(user = Depends(get_current_user)):
        # user.id  → Supabase user UUID (str)
        # user.email → logged-in user email
        ...
"""

from fastapi import Depends, HTTPException, Header
from supabase import create_client
from app.core.config import settings


def _anon_client():
    return create_client(settings.supabase_url, settings.supabase_anon_key)


async def get_current_user(authorization: str = Header(...)):
    """
    FastAPI dependency that validates the Bearer JWT token sent by the frontend
    and returns the Supabase user object.

    Raises 401 if the token is missing, invalid, or expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header missing or malformed. Expected: 'Bearer <token>'",
        )

    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token is empty")

    try:
        client = _anon_client()
        result = client.auth.get_user(token)
        if not result or not result.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return result.user
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token validation failed: {str(exc)}")
