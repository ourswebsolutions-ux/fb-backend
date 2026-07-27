import asyncio
import json as _json
import re
import time
import traceback
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
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

        for selector in [
            '[data-pagelet="ProfileTimeline"]',
            '[data-testid="profile-name"]',
            'h1',
            '[aria-label="Profile"]',
            '[data-pagelet="FBPage"]',
        ]:
            try:
                locator = page.locator(selector).first
                if await locator.count() > 0:
                    text = (await locator.inner_text()).strip()
                    if text:
                        profile["facebook_display_name"] = text
                        break
            except Exception:
                continue

        if not profile["facebook_display_name"]:
            try:
                text = (await page.locator('meta[property="og:title"]').first.get_attribute('content') or '').strip()
                if text:
                    profile["facebook_display_name"] = text
            except Exception:
                pass

        if not profile["facebook_user_id"]:
            try:
                for candidate in [page.url, await page.evaluate("() => document.body.innerHTML")]:
                    if isinstance(candidate, str):
                        match = re.search(r"/(?:profile\.php\?id=|)([0-9]+)", candidate)
                        if match:
                            profile["facebook_user_id"] = match.group(1)
                            break
            except Exception:
                pass

        profile["last_verified_at"] = datetime.now(timezone.utc).isoformat()
        return profile
    except Exception:
        return profile


async def _navigate_to_marketplace(session, timeout_ms: int = 80000) -> str:
    """Navigate to Marketplace using DOM readiness instead of waiting for a full page load."""
    for attempt in range(1, 3):
        print(f"[verify_session] Marketplace navigation attempt {attempt}/2 started")
        try:
            await session.page.goto(
                "https://www.facebook.com/marketplace",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            current_url = session.page.url
            print(f"[verify_session] Marketplace navigation attempt {attempt}/2 completed: {current_url}")
            return current_url
        except Exception as exc:
            message = str(exc).lower()
            print(f"[verify_session] Marketplace navigation attempt {attempt}/2 failed: {type(exc).__name__}: {exc}")
            if attempt == 1 and "timeout" in message:
                print("[verify_session] Retrying Marketplace navigation once after timeout")
                continue
            raise


def _merge_profile_notes(existing_notes: str | None, profile: dict) -> str:
    """Store profile metadata in the existing notes field without breaking manual-login notes."""
    payload: dict = {}
    if existing_notes:
        try:
            parsed = _json.loads(existing_notes)
            if isinstance(parsed, dict):
                payload = parsed
            else:
                payload = {"notes": existing_notes}
        except Exception:
            payload = {"notes": existing_notes}

    payload["facebook_profile"] = {
        key: value for key, value in profile.items() if value not in (None, "")
    }
    return _json.dumps(payload)


@router.get("/")
async def list_accounts():
    db = get_supabase()
    result = db.table("fb_accounts").select(
        "id,email,phone,status,warmup_level,last_used_at,notes,created_at,proxy,cookies"
    ).order("created_at", desc=True).execute()
    accounts = []
    for acc in result.data:
        acc["cookies"] = bool(acc.get("cookies"))
        accounts.append(acc)
    return accounts


@router.post("/")
async def create_account(body: FBAccountCreate):
    """Create a Facebook account from a verified session upload using the existing cookie-based schema."""
    db = get_supabase()

    if not body.email:
        raise HTTPException(status_code=400, detail="Email is required")

    if not body.session_data:
        raise HTTPException(status_code=400, detail="Session JSON is required")

    existing = db.table("fb_accounts").select("id").eq("email", body.email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail=f"Account '{body.email}' already exists")

    print(f"[create_account] Creating account for {body.email}")

    data = body.model_dump()
    session_payload = data.pop("session_data", None)
    data.pop("password", None)

    cookies_json = _extract_session_cookie_payload(session_payload)
    if not cookies_json or cookies_json == "[]":
        raise HTTPException(status_code=400, detail="Session JSON must contain browser cookies")

    data["email"] = body.email
    data["password"] = ""
    data["cookies"] = cookies_json
    data["status"] = "active"

    result = db.table("fb_accounts").insert(data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save account to database")

    created_acc = dict(result.data[0])
    created_acc["cookies"] = bool(created_acc.get("cookies"))
    created_acc["status"] = created_acc.get("status") or "active"
    print(f"[create_account] Account saved successfully using cookie-based session data")
    return created_acc


@router.post("/import-session", response_model=ImportSessionCreateResponse)
async def create_import_session_account(body: ImportSessionCreateRequest) -> ImportSessionCreateResponse:
    """Persist a verified import-session account without using the manual-login flow."""
    print("[import_session] Incoming request payload:", body.model_dump())

    validation_errors = []
    if not body.display_name and not body.facebook_user_id and not body.profile_url and not body.session:
        validation_errors.append("At least one import-session field is required")
    if not body.session:
        validation_errors.append("Import session data is required")

    print("[import_session] Validation result:", {"valid": not validation_errors, "errors": validation_errors})
    if validation_errors:
        raise HTTPException(status_code=400, detail="; ".join(validation_errors))

    db = get_supabase()
    payload = _build_import_session_payload(body)
    print("[import_session] Database insert attempt with payload:", payload)
    result = db.table("fb_accounts").insert(payload).execute()
    print("[import_session] Database insert success:", result.data)
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save import-session account")

    created = result.data[0]
    response = ImportSessionCreateResponse(
        id=str(created.get("id")),
        display_name=body.display_name or "",
        facebook_user_id=body.facebook_user_id or "",
        profile_url=body.profile_url or "",
        verification_status=body.verification_status,
        last_verified_at=body.last_verified_at or "",
        status="active",
        message="Import session account created successfully",
    )
    print("[import_session] Response returned:", response.model_dump())
    return response


@router.post("/verify-session")
async def verify_session(
    session_file: UploadFile | None = File(default=None),
    session_data: str | None = Form(default=None),
    account_name: str | None = Form(default=None),
    email: str | None = Form(default=None),
    phone: str | None = Form(default=None),
) -> ImportSessionResponse:
    """Verify an uploaded Playwright/browser session file without logging in with password."""
    request = ImportSessionRequest(account_name=account_name, session_data=session_data)
    validation = ImportSessionValidation()
    if not request.session_data and session_file is None:
        raise HTTPException(status_code=400, detail="Session JSON is required")

    print("[verify_session] ===== Endpoint Called =====")
    print(f"[verify_session] account_name: {account_name}")
    print(f"[verify_session] email: {email}")
    print(f"[verify_session] phone: {phone}")
    print(f"[verify_session] session_file: {session_file.filename if session_file else 'null'}")
    print(f"[verify_session] session_data length: {len(session_data) if session_data else 0}")
    
    payload_text = session_data
    if not payload_text and session_file is not None:
        print("[verify_session] Reading payload from uploaded file...")
        payload_text = await session_file.read()
        try:
            payload_text = payload_text.decode("utf-8")
        except Exception:
            payload_text = payload_text.decode("utf-8", errors="ignore")

    if not payload_text or not payload_text.strip():
        print("[verify_session] ERROR: No session data provided")
        raise HTTPException(status_code=400, detail="Session JSON is required")

    print("[verify_session] Parsing JSON payload...")
    try:
        payload = _json.loads(payload_text)
        print(f"[verify_session] JSON parsed successfully, keys: {list(payload.keys())}")
    except Exception as exc:
        print(f"[verify_session] ERROR: JSON parse failed: {exc}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    print("[verify_session] Normalizing session payload...")
    try:
        normalized = normalize_session_payload(payload)
        print(f"[verify_session] Normalized keys: {list(normalized.keys())}")
    except Exception as exc:
        print(f"[verify_session] ERROR: Normalization failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    bm = BrowserManager(headless=True)
    try:
        print("[verify_session] Creating browser session...")
        async with bm.new_session() as session:
            print(f"[verify_session] Restoring session for: {account_name or email or phone or 'account'}")
            await restore_session_payload(session, normalized)
            print("[verify_session] Session restored, sleeping 2s...")
            await asyncio.sleep(2)
            
            print("[verify_session] Navigating to Facebook Marketplace...")
            current_url = await _navigate_to_marketplace(session)
            await asyncio.sleep(2)

            print(f"[verify_session] Current URL: {current_url}")
            
            # Check if we're logged in by looking for authentication indicators
            is_authenticated = await session.is_logged_in()
            print(f"[verify_session] Authentication status: {is_authenticated}")
            
            is_on_marketplace = "marketplace" in current_url
            print(f"[verify_session] Marketplace detection: {is_on_marketplace}")
            
            if is_authenticated and is_on_marketplace:
                print("[verify_session] SUCCESS: Session verified!")
                profile = await _extract_verified_profile(session)
                return {
                    "verified": True,
                    "success": True,
                    "message": "Session verified successfully",
                    "profile": profile,
                }
            elif is_on_marketplace:
                print("[verify_session] On marketplace but authentication unclear")
                return {
                    "verified": True,
                    "success": True,
                    "message": "Session verified successfully"
                }
            else:
                print("[verify_session] FAILED: Not on marketplace")
                return {
                    "verified": False,
                    "success": False,
                    "message": f"Session could not be verified. Ended at: {current_url}"
                }
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {str(exc)}"
        print(f"[verify_session] ERROR: {error_msg}")
        print(f"[verify_session] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Session verification failed: {error_msg}") from exc


@router.post("/{account_id}/verify")
async def verify_account(account_id: str):
    """Re-verify an existing account by reusing the same session verification workflow as the initial Verify Session."""
    print(f"[verify_account] Re-verification started for account {account_id}")
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")

    account = result.data[0]
    raw_cookies = account.get("cookies")
    if not raw_cookies:
        print(f"[verify_account] No saved session cookies for account {account_id}")
        db.table("fb_accounts").update({"status": "idle"}).eq("id", account_id).execute()
        raise HTTPException(status_code=400, detail="No saved session cookies found. Please add the account again using a verified session JSON file.")

    if isinstance(raw_cookies, str):
        try:
            raw_cookies = _json.loads(raw_cookies)
        except Exception as exc:
            print(f"[verify_account] Invalid stored session payload for account {account_id}: {exc}")
            db.table("fb_accounts").update({"status": "idle"}).eq("id", account_id).execute()
            raise HTTPException(status_code=400, detail="Saved session data is invalid") from exc

    bm = BrowserManager(headless=True)
    try:
        print(f"[verify_account] Restoring stored session for account {account_id}")
        async with bm.new_session(proxy=account.get("proxy")) as session:
            await restore_session_payload(session, raw_cookies)
            print(f"[verify_account] Session restoration completed for account {account_id}")
            await asyncio.sleep(2)

            print(f"[verify_account] Marketplace navigation started for account {account_id}")
            current_url = await _navigate_to_marketplace(session)
            await asyncio.sleep(2)
            print(f"[verify_account] Marketplace navigation completed for account {account_id}: {current_url}")

            is_authenticated = await session.is_logged_in()
            is_on_marketplace = "marketplace" in current_url
            print(f"[verify_account] Authentication status: {is_authenticated}")
            print(f"[verify_account] Marketplace detection: {is_on_marketplace}")

            if is_authenticated and is_on_marketplace:
                print(f"[verify_account] Verification succeeded for account {account_id}")
                cookies_json = await session.save_cookies()
                db.table("fb_accounts").update({"cookies": cookies_json, "status": "active"}).eq("id", account_id).execute()
                print(f"[verify_account] Account status updated to active for account {account_id}")
                return {"verified": True, "message": "Session restored and verified successfully"}

            print(f"[verify_account] Verification failed for account {account_id}; marking account as idle")
            db.table("fb_accounts").update({"status": "idle"}).eq("id", account_id).execute()
            raise HTTPException(status_code=400, detail="Saved session cookies are no longer valid. Please re-add the account with a fresh verified session JSON file.")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[verify_account] Verification error for account {account_id}: {exc}")
        db.table("fb_accounts").update({"status": "idle"}).eq("id", account_id).execute()
        raise HTTPException(status_code=500, detail=f"Session verification failed: {exc}") from exc


# Backward compatibility alias
@router.post("/{account_id}/verify-interactive")
async def verify_interactive_alias(account_id: str):
    return await verify_account(account_id)


@router.get("/{account_id}")
async def get_account(account_id: str):
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")
    data = result.data[0]

    print(f"[get_account] Account {account_id}:")
    print(f"[get_account] - Email: {data.get('email')}")
    print(f"[get_account] - Phone: {data.get('phone')}")
    print(f"[get_account] - Has password: {bool(data.get('password'))}")
    print(f"[get_account] - Password length: {len(data.get('password', ''))}")
    print(f"[get_account] - Has cookies: {bool(data.get('cookies'))}")
    print(f"[get_account] - Cookie length: {len(data.get('cookies', '')) if data.get('cookies') else 0}")
    print(f"[get_account] - Status: {data.get('status')}")

    data.pop("password", None)
    return data


@router.patch("/{account_id}")
async def update_account(account_id: str, body: FBAccountUpdate):
    db = get_supabase()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "password" in patch and settings.encryption_key:
        patch["password"] = encrypt_password(patch["password"])

    result = db.table("fb_accounts").update(patch).eq("id", account_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")
    return result.data[0]


@router.delete("/{account_id}")
async def delete_account(account_id: str):
    db = get_supabase()
    db.table("fb_accounts").delete().eq("id", account_id).execute()
    return {"deleted": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

_headless_bm: BrowserManager | None = None

def _get_headless_bm() -> BrowserManager:
    global _headless_bm
    if _headless_bm is None:
        _headless_bm = BrowserManager(headless=True)
    return _headless_bm


async def _stop_headless_bm():
    """Properly shut down the headless browser manager to avoid resource leaks."""
    global _headless_bm
    bm = _headless_bm
    _headless_bm = None
    if bm is not None:
        try:
            await bm.stop()
        except Exception as e:
            print(f"[accounts] _stop_headless_bm error: {e}")


async def _verify_headless(identifier: str, password: str, proxy: str = None) -> dict:
    """Quick headless verification — works for accounts without 2FA."""
    bm = _get_headless_bm()
    try:
        async with bm.new_session(proxy=proxy) as session:
            login_success = await bm.login(session, identifier, password)
            current_url = session.page.url

            landed_on_home = current_url in (
                "https://www.facebook.com/",
                "https://www.facebook.com",
            )

            if login_success or landed_on_home:
                await asyncio.sleep(3)
                cookies_json = await session.save_cookies()
                return {"success": True, "cookies": cookies_json}

            if "checkpoint" in current_url or "two_step" in current_url or "approvals" in current_url:
                return {"success": False, "error": "2FA/checkpoint — use Verify button for manual completion"}
            if "/login" in current_url or "login.php" in current_url:
                return {"success": False, "error": "Wrong email or password"}
            return {"success": False, "error": f"Login did not succeed (URL: {current_url})"}

    except Exception as e:
        print(f"[verify_headless] {traceback.format_exc()}")
        return {"success": False, "error": f"{type(e).__name__}: {str(e) or 'no message'}"}
    # finally:
        # Do NOT call _stop_headless_bm() here because the browser might be
        # reused by a subsequent _verify_headless call from another request.
        # Shutdown is managed externally (e.g. via FastAPI lifespan shutdown event).
