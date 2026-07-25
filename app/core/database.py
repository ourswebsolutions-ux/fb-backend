from supabase import create_client, Client
from fastapi import HTTPException

from app.core.config import settings

_client: Client | None = None


def get_supabase() -> Client:
    """
    Returns a cached Supabase client.

    Defensive behavior:
    - If required Supabase env vars are missing/invalid, we raise a 503 instead of crashing with 500.
    """
    global _client
    if _client is None:
        if not settings.supabase_service_role_key:
            raise HTTPException(
                status_code=503,
                detail="Supabase is not configured: missing SUPABASE_SERVICE_ROLE_KEY",
            )

        try:
            # Remove proxy argument as it's not supported in current Supabase version
            _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Supabase client initialization failed: {e}",
            ) from e

    return _client
