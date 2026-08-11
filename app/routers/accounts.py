import asyncio
import json as _json
import re
import time
import traceback
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends
from app.models import (
    FBAccountCreate,
    FBAccountUpdate,
    ImportSessionCreateRequest,
    ImportSessionCreateResponse,
    ImportSessionRequest,
    ImportSessionResponse,
    ImportSessionValidation,
)
from app.core.database import get_supabase
from app.core.browser import BrowserManager, normalize_session_payload, restore_session_payload
from app.core.encryption import encrypt_password, decrypt_password
from app.core.config import settings
from app.core.deps import get_current_user

router = APIRouter()


def _build_import_session_payload(request: ImportSessionCreateRequest) -> dict:
    """Create an import-session-only account payload using the same cookie field as automation."""
    payload = {
        "email": "",
        "phone": None,
        "password": "",
        "status": "active",
        "cookies": _extract_session_cookie_payload(request.session),
        "notes": _json.dumps({
            "import_session": {
                "display_name": request.display_name or "",
                "facebook_user_id": request.facebook_user_id or "",
                "profile_url": request.profile_url or "",
                "verification_status": request.verification_status,
                "last_verified_at": request.last_verified_at or "",
            }
        }),
    }
    return payload


def _extract_session_cookie_payload(session_payload: object) -> str:
    """Extract a cookie-array payload from uploaded session JSON for the existing automation schema."""
    if session_payload is None:
        return "[]"

    if isinstance(session_payload, str):
        try:
            session_payload = _json.loads(session_payload)
        except Exception:
            return "[]"

    normalized = normalize_session_payload(session_payload)
    cookies = normalized.get("cookies") or []
    if not isinstance(cookies, list):
        return "[]"
    return _json.dumps(cookies)


async def _extract_verified_profile(session) -> dict:
    """Extract profile metadata from the already-authenticated Facebook session."""
    profile = {
        "facebook_user_id": "",
        "facebook_display_name": "",
        "facebook_profile_url": "",
        "verification_status": "verified",
        "last_verified_at": "",
        "verified_session_state": "active",
    }

    try:
        page = session.page
        current_url = page.url
        profile["facebook_profile_url"] = current_url if current_url else ""

        try:
            display_name = await page.evaluate("""
                () => {
                    const selectors = [
                        '[aria-label="Your profile"] span',
                        'div[data-testid="royal_login_button"]',
                        'span.x193iq5w',
                    ]
                    for (const sel of selectors) {
                        const el = document.querySelector(sel)
                        if (el && el.textContent?.trim()) return el.textContent.trim()
                    }
                    return ''
                }
            """)
            profile["facebook_display_name"] = display_name or ""
        except Exception:
            pass

        try:
            user_id = ""
            if "facebook.com" in current_url:
                match = re.search(r'facebook\.com/(?:profile\.php\?id=)?(\d+)', current_url)
                if match:
                    user_id = match.group(1)
            if not user_id:
                user_id = await page.evaluate("""
                    () => {
                        const m = document.cookie.match(/c_user=(\d+)/)
                        return m ? m[1] : ''
                    }
                """)
            profile["facebook_user_id"] = user_id or ""
        except Exception:
            pass

        profile["last_verified_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        print(f"[_extract_verified_profile] Error extracting profile: {e}")

    return profile


def _update_notes_with_profile(existing_notes: str, profile: dict) -> str:
    """Merge verified profile data into the existing notes JSON."""
    try:
        if existing_notes:
            try:
                payload = _json.loads(existing_notes)
            except Exception:
                payload = {"notes": existing_notes}
        else:
            payload = {}
    except Exception:
        try:
            if existing_notes:
                payload = _json.loads(existing_notes)
            else:
                payload = {}
        except Exception:
            payload = {"notes": existing_notes}

    payload["facebook_profile"] = {
        key: value for key, value in profile.items() if value not in (None, "")
    }
    return _json.dumps(payload)


@router.get("/")
async def list_accounts(user=Depends(get_current_user)):
    db = get_supabase()
    result = db.table("fb_accounts").select(
        "id,email,phone,status,warmup_level,last_used_at,notes,created_at,proxy,cookies"
    ).eq("user_id", user.id).order("created_at", desc=True).execute()
    accounts = []
    for acc in result.data:
        acc["cookies"] = bool(acc.get("cookies"))
        accounts.append(acc)
    return accounts


@router.post("/")
async def create_account(body: FBAccountCreate, user=Depends(get_current_user)):
    """Create a Facebook account from a verified session upload using the existing cookie-based schema."""
    db = get_supabase()

    if not body.email and not body.phone:
        raise HTTPException(status_code=400, detail="Email or phone number is required")

    if not body.session_data:
        raise HTTPException(status_code=400, detail="Session JSON is required")

    identifier = body.phone or body.email or ""
    if body.phone:
        existing = db.table("fb_accounts").select("id").eq("phone", body.phone).eq("user_id", user.id).execute()
    else:
        existing = db.table("fb_accounts").select("id").eq("email", body.email).eq("user_id", user.id).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail=f"Account '{identifier}' already exists")

    print(f"[create_account] Creating account for {identifier} (user: {user.id})")

    data = body.model_dump()
    session_payload = data.pop("session_data", None)
    data.pop("password", None)

    cookies_json = _extract_session_cookie_payload(session_payload)
    if not cookies_json or cookies_json == "[]":
        raise HTTPException(status_code=400, detail="Session JSON must contain browser cookies")

    data["email"] = body.email or ""
    data["phone"] = body.phone or None
    data["password"] = ""
    data["cookies"] = cookies_json
    data["status"] = "active"
    data["user_id"] = user.id

    # Only keep columns that actually exist in fb_accounts table
    allowed_columns = {"email", "phone", "password", "proxy", "notes", "cookies", "status", "user_id"}
    data = {k: v for k, v in data.items() if k in allowed_columns}

    result = db.table("fb_accounts").insert(data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save account to database")

    created_acc = dict(result.data[0])
    created_acc["cookies"] = bool(created_acc.get("cookies"))
    created_acc["status"] = created_acc.get("status") or "active"
    print(f"[create_account] Account saved successfully using cookie-based session data")
    return created_acc


@router.post("/import-session", response_model=ImportSessionCreateResponse)
async def create_import_session_account(
    body: ImportSessionCreateRequest,
    user=Depends(get_current_user),
) -> ImportSessionCreateResponse:
    """Persist a verified import-session account without using the manual-login flow."""
    print("[import_session] Incoming request payload:", body.model_dump())

    validation_errors = []
    if not body.display_name and not body.facebook_user_id and not body.profile_url and not body.session:
        validation_errors.append("At least one of display_name, facebook_user_id, profile_url, or session is required")

    if validation_errors:
        raise HTTPException(status_code=422, detail=validation_errors)

    db = get_supabase()
    payload = _build_import_session_payload(body)
    payload["user_id"] = user.id

    result = db.table("fb_accounts").insert(payload).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save import session account")

    created = result.data[0]
    notes_data = {}
    try:
        notes_data = _json.loads(created.get("notes") or "{}")
    except Exception:
        pass

    import_info = notes_data.get("import_session", {})
    return ImportSessionCreateResponse(
        id=str(created.get("id", "")),
        display_name=import_info.get("display_name", ""),
        facebook_user_id=import_info.get("facebook_user_id", ""),
        profile_url=import_info.get("profile_url", ""),
        verification_status=import_info.get("verification_status", False),
        last_verified_at=import_info.get("last_verified_at", ""),
        status=created.get("status", "active"),
    )


@router.get("/{account_id}")
async def get_account(account_id: str, user=Depends(get_current_user)):
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")
    acc = dict(result.data[0])
    acc["cookies"] = bool(acc.get("cookies"))
    return acc


@router.patch("/{account_id}")
async def update_account(account_id: str, body: FBAccountUpdate, user=Depends(get_current_user)):
    db = get_supabase()
    # Verify ownership
    existing = db.table("fb_accounts").select("id").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Account not found")

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "password" in patch:
        patch["password"] = encrypt_password(patch["password"])

    result = db.table("fb_accounts").update(patch).eq("id", account_id).eq("user_id", user.id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")
    acc = dict(result.data[0])
    acc["cookies"] = bool(acc.get("cookies"))
    return acc


@router.delete("/{account_id}")
async def delete_account(account_id: str, user=Depends(get_current_user)):
    db = get_supabase()
    existing = db.table("fb_accounts").select("id").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Account not found")
    db.table("fb_accounts").delete().eq("id", account_id).eq("user_id", user.id).execute()
    return {"deleted": True}


# ── Headless browser manager for verify endpoints ─────────────────────────────
_headless_bm: BrowserManager | None = None
_headless_bm_lock = asyncio.Lock()


async def _get_headless_bm() -> BrowserManager:
    global _headless_bm
    async with _headless_bm_lock:
        if _headless_bm is None or not _headless_bm._browser_ready():
            _headless_bm = BrowserManager(headless=True)
            await _headless_bm.start()
    return _headless_bm


async def _stop_headless_bm():
    global _headless_bm
    if _headless_bm and _headless_bm._browser_ready():
        await _headless_bm.stop()
        _headless_bm = None


@router.post("/{account_id}/verify")
async def verify_account(account_id: str, user=Depends(get_current_user)):
    """Headless-verify that the saved session/cookies are still valid for this account."""
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")

    account = result.data[0]
    cookies_raw = account.get("cookies")
    if not cookies_raw:
        raise HTTPException(status_code=400, detail="No session data (cookies) saved for this account")

    try:
        bm = await _get_headless_bm()
        async with bm.new_session(cookies_json=cookies_raw) as session:
            try:
                cookies = _json.loads(cookies_raw) if isinstance(cookies_raw, str) else cookies_raw
                await restore_session_payload(session, {"cookies": cookies})
                await session.page.goto("https://www.facebook.com/", timeout=30000)
                await asyncio.sleep(2)

                is_logged_in = await session.page.evaluate("""
                    () => {
                        return !document.querySelector('[data-testid="royal_login_button"]') &&
                               !document.querySelector('#email') &&
                               (document.querySelector('[aria-label="Facebook"]') !== null ||
                                window.location.pathname !== '/login/')
                    }
                """)

                if is_logged_in:
                    profile = await _extract_verified_profile(session)
                    notes_updated = _update_notes_with_profile(account.get("notes", ""), profile)
                    db.table("fb_accounts").update({
                        "status": "active",
                        "last_used_at": datetime.now(timezone.utc).isoformat(),
                        "notes": notes_updated,
                    }).eq("id", account_id).execute()
                    return {"verified": True, "status": "active", "profile": profile}
                else:
                    db.table("fb_accounts").update({"status": "banned"}).eq("id", account_id).execute()
                    return {"verified": False, "status": "banned", "message": "Session appears to be logged out or banned"}
            except Exception as inner_e:
                raise inner_e

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Verification failed: {str(e)}")


@router.post("/{account_id}/verify-interactive")
async def verify_account_interactive(account_id: str, user=Depends(get_current_user)):
    """Open a visible browser window so the user can manually fix the session."""
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")

    account = result.data[0]
    try:
        visible_bm = BrowserManager(headless=False)
        await visible_bm.start()
        async with visible_bm.new_session(cookies_json=account.get("cookies")) as session:
            cookies_raw = account.get("cookies")
            if cookies_raw:
                cookies = _json.loads(cookies_raw) if isinstance(cookies_raw, str) else cookies_raw
                await restore_session_payload(session, {"cookies": cookies})

            await session.page.goto("https://www.facebook.com/", timeout=30000)
            await asyncio.sleep(15)

            profile = await _extract_verified_profile(session)
            cookies_after = await session.context.cookies()
            notes_updated = _update_notes_with_profile(account.get("notes", ""), profile)

            db.table("fb_accounts").update({
                "status": "active",
                "cookies": _json.dumps(cookies_after),
                "last_used_at": datetime.now(timezone.utc).isoformat(),
                "notes": notes_updated,
            }).eq("id", account_id).execute()

        await visible_bm.stop()
        return {"verified": True, "status": "active", "profile": profile}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Interactive verification failed: {str(e)}")


@router.post("/verify-session")
async def verify_session_upload(
    file: UploadFile = File(None),
    session_file: UploadFile = File(None),
    session_json: str = Form(None),
    session_data: str = Form(None),
):
    """
    Verify an uploaded session JSON before saving it as an account.
    No auth required — this is a pre-save validation step.
    Accepts both field name variants used by the frontend:
      file / session_file  — multipart upload
      session_json / session_data — raw JSON string
    """
    # Accept both field name variants from frontend
    upload   = file or session_file
    raw_json = session_json or session_data

    raw = None
    if upload:
        content = await upload.read()
        try:
            raw = _json.loads(content)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON in uploaded file")
    elif raw_json:
        try:
            raw = _json.loads(raw_json)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON in session_data field")
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or session_json field")

    normalized = normalize_session_payload(raw)
    cookies = normalized.get("cookies") or []

    if not cookies:
        return {
            "valid": False,
            "message": "No cookies found in session data",
            "cookie_count": 0,
        }

    fb_cookies = [c for c in cookies if "facebook" in c.get("domain", "").lower()]

    return {
        "valid": len(fb_cookies) > 0,
        "message": "Session appears valid" if fb_cookies else "No Facebook cookies found",
        "cookie_count": len(cookies),
        "fb_cookie_count": len(fb_cookies),
    }
