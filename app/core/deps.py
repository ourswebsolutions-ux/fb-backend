"""
Shared FastAPI dependencies — authentication helpers.

Usage:
    from app.core.deps import get_current_user

    @router.get("/")
    async def my_route(user = Depends(get_current_user)):
        # user.id    → Supabase user UUID (str)
        # user.email → logged-in user email
        ...
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client
from app.core.config import settings

# HTTPBearer makes Swagger show the "Authorize 🔒" button
_bearer_scheme = HTTPBearer(auto_error=False)


def _anon_client():
    return create_client(settings.supabase_url, settings.supabase_anon_key)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
):
    """
    FastAPI dependency that validates the Bearer JWT token.
    Works both from frontend (Authorization header) and Swagger UI (Authorize button).

    Raises 401 if the token is missing, invalid, or expired.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Provide a Bearer token.",
        )

    token = credentials.credentials.strip()

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
