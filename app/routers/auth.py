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
    result = client.auth.get_user(token)
    if not result.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return result.user


@router.post("/signup")
async def signup(body: SignUpRequest):
    client = _anon_client()
    result = client.auth.sign_up({"email": body.email, "password": body.password})
    if not result.user:
        raise HTTPException(status_code=400, detail="Sign-up failed")
    return {
        "user_id": result.user.id,
        "email": result.user.email,
        "message": "Account created. Check email for confirmation if enabled.",
    }


@router.post("/login")
async def login(body: LoginRequest):
    client = _anon_client()
    result = client.auth.sign_in_with_password(
        {"email": body.email, "password": body.password}
    )
    if not result.session:
        raise HTTPException(status_code=401, detail="Invalid credentials")
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
    client.auth.sign_out(token)
    return {"message": "Logged out"}


@router.get("/me")
async def me(authorization: str = Header(...)):
    user = _get_user_from_token(authorization)
    return {"user_id": user.id, "email": user.email}
