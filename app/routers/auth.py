from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr
from app.core.config import settings
from supabase import create_client, Client

router = APIRouter()


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _anon_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def _get_user_from_token(authorization: str) -> dict:
    token = authorization.replace("Bearer ", "").strip()
    client = _anon_client()
    try:
        result = client.auth.get_user(token)
        if not result or not result.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return result.user
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


@router.post("/signup")
async def signup(body: SignUpRequest):
    client = _anon_client()
    try:
        result = client.auth.sign_up({"email": body.email, "password": body.password})
    except Exception as exc:
        msg = str(exc).lower()
        if "already registered" in msg or "already exists" in msg or "duplicate" in msg:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=400, detail=f"Sign-up failed: {str(exc)}")

    if not result.user:
        raise HTTPException(status_code=400, detail="Sign-up failed. Please try again.")

    # Auto-confirm the user via direct DB so they can login immediately
    # (bypasses Supabase email confirmation setting)
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="aws-0-ap-southeast-2.pooler.supabase.com",
            port=5432,
            dbname="postgres",
            user="postgres.yqsazqjidoecrzmbukxm",
            password="P+L_wrZpXGZ5m8c",
            connect_timeout=10,
            sslmode="require",
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "UPDATE auth.users SET email_confirmed_at = NOW(), updated_at = NOW() "
            "WHERE id = %s AND email_confirmed_at IS NULL",
            (str(result.user.id),)
        )
        cur.close()
        conn.close()
    except Exception:
        pass  # If DB confirm fails, fall back to email confirmation flow

    needs_confirmation = result.session is None

    return {
        "user_id": result.user.id,
        "email": result.user.email,
        "needs_confirmation": needs_confirmation,
        "message": (
            "Account created! Please check your email and click the confirmation link, then sign in."
            if needs_confirmation
            else "Account created successfully."
        ),
    }


@router.post("/login")
async def login(body: LoginRequest):
    client = _anon_client()
    try:
        result = client.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
    except Exception as exc:
        msg = str(exc).lower()
        # Email not confirmed yet
        if "email not confirmed" in msg or "not confirmed" in msg:
            raise HTTPException(
                status_code=403,
                detail="EMAIL_NOT_CONFIRMED: Please check your inbox and confirm your email address before signing in.",
            )
        # Wrong credentials
        if "invalid login" in msg or "invalid credentials" in msg or "bad credentials" in msg:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        # Generic fallback
        raise HTTPException(status_code=401, detail=f"Login failed: {str(exc)}")

    if not result.session:
        raise HTTPException(status_code=401, detail="Login failed. Please try again.")

    return {
        "access_token": result.session.access_token,
        "refresh_token": result.session.refresh_token,
        "expires_at": result.session.expires_at,
        "user_id": result.user.id,
        "email": result.user.email,
    }


@router.post("/logout")
async def logout(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "").strip()
    client = _anon_client()
    try:
        client.auth.sign_out(token)
    except Exception:
        pass  # Always succeed on logout
    return {"message": "Logged out"}


@router.get("/me")
async def me(authorization: str = Header(...)):
    user = _get_user_from_token(authorization)
    return {"user_id": user.id, "email": user.email}
