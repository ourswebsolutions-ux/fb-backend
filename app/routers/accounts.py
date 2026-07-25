import asyncio
import json as _json
import time
import traceback

from fastapi import APIRouter, HTTPException
from app.models import FBAccountCreate, FBAccountUpdate
from app.core.database import get_supabase
from app.core.browser import BrowserManager
from app.core.encryption import encrypt_password, decrypt_password
from app.core.config import settings

router = APIRouter()


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
    """
    Add a new FB account.
    Saves the account to the database first, then attempts quick headless verification.
    If headless verification times out or requires 2FA/CAPTCHA, account remains saved as unverified.
    """
    db = get_supabase()

    if not body.email and not body.phone:
        raise HTTPException(status_code=400, detail="Either email or phone number is required")

    if body.email:
        existing = db.table("fb_accounts").select("id").eq("email", body.email).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail=f"Account '{body.email}' already exists")
    if body.phone:
        existing = db.table("fb_accounts").select("id").eq("phone", body.phone).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail=f"Account '{body.phone}' already exists")

    identifier = body.phone or body.email
    print(f"[create_account] Creating account for {identifier}")

    data = body.model_dump()
    if settings.encryption_key and data.get("password"):
        data["password"] = encrypt_password(data["password"])
        print(f"[create_account] Password encrypted successfully")

    # Insert account into database FIRST so it is guaranteed to be saved and visible
    result = db.table("fb_accounts").insert(data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save account to database")

    created_acc = dict(result.data[0])
    created_id = created_acc["id"]

    # Attempt quick non-blocking/timed verification
    try:
        verification_result = await asyncio.wait_for(
            _verify_headless(identifier, body.password, body.proxy),
            timeout=12.0
        )
        if verification_result.get("success") and verification_result.get("cookies"):
            cookies_json = verification_result["cookies"]
            db.table("fb_accounts").update({"cookies": cookies_json, "status": "active"}).eq("id", created_id).execute()
            created_acc["cookies"] = True
            created_acc["status"] = "active"
            print(f"[create_account] Headless verification succeeded, updated cookies")
            return created_acc
    except Exception as e:
        print(f"[create_account] Headless verification skipped/timed out: {e}")

    # Saved without cookies or verification required manual step
    created_acc["cookies"] = False
    created_acc["warning"] = (
        "Account saved but not yet verified. "
        "Click 'Verify' to open a browser and complete login manually."
    )
    print(f"[create_account] Account saved successfully, needs manual verification")
    return created_acc


@router.post("/{account_id}/verify")
async def verify_account(account_id: str):
    """
    Verify account by opening a visible browser window.
    - Attempts automatic login with stored credentials first
    - If 2FA secret is stored in account notes (format: '2fa: SECRET'), handles 2FA automatically
    - Waits up to 3 minutes checking for c_user + xs session cookies
    - Navigates a few pages after login to collect all cookies
    - Only saves cookies AFTER confirming c_user and xs are present
    - This is the ONLY verification button needed
    """
    from app.core.browser import extract_2fa_secret

    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")

    account = result.data[0]
    identifier = account.get("email") or account.get("phone", "")
    raw_password = account.get("password", "")
    password = ""

    if raw_password:
        if raw_password.startswith("gAAAAA"):
            try:
                password = decrypt_password(raw_password)
                print(f"[verify] Successfully decrypted stored password for {identifier}")
            except Exception as e:
                print(f"[verify] WARNING: Could not decrypt password token for {identifier}: {e}. Password cleared.")
                password = ""
        else:
            password = raw_password
            print(f"[verify] Using stored plaintext password for {identifier}")

    # Extract 2FA secret from account notes
    two_factor_secret = extract_2fa_secret(account.get("notes"))

    if not password:
        print(f"[verify] No valid password for {identifier} — please enter password in the browser window if requested")
    if two_factor_secret:
        print(f"[verify] 2FA secret found in notes — will attempt automatic 2FA")

    bm = BrowserManager(headless=False)

    try:
        async with bm.new_session(proxy=account.get("proxy")) as session:
            page = session.page

            print(f"[verify] Opening FB for {identifier}")
            await page.goto("https://www.facebook.com", timeout=20000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await asyncio.sleep(1)

            # Auto-fill credentials and submit
            try:
                email_sel = (
                    '#email, input[name="email"], input[type="email"], '
                    'input[placeholder*="Email"], input[placeholder*="Mobile"], '
                    'input[placeholder*="Phone"]'
                )
                email_el = page.locator(email_sel).first
                if await email_el.is_visible():
                    await email_el.fill(identifier)
                    await asyncio.sleep(0.5)

                pass_el = page.locator(
                    '#pass, input[name="pass"], input[type="password"], input[placeholder*="Password"]'
                ).first
                if await pass_el.is_visible() and password:
                    await pass_el.fill(password)
                    await asyncio.sleep(0.5)

                login_btn = page.locator(
                    '[name="login"], button[type="submit"], '
                    'div[role="button"]:has-text("Log in"), button:has-text("Log in")'
                ).first
                if await login_btn.is_visible():
                    await login_btn.click()
                    await asyncio.sleep(3)
                    print(f"[verify] Submitted. URL: {page.url}")

                    # If 2FA checkpoint detected and we have a secret, auto-handle it
                    cur = page.url
                    if two_factor_secret and (
                        "checkpoint" in cur or "two_step" in cur
                        or "approvals" in cur or "two_factor" in cur
                    ):
                        print("[verify] 2FA detected — auto-filling TOTP...")
                        handled = await bm._handle_2fa(session, two_factor_secret)
                        print(f"[verify] Auto 2FA result: {handled}")

            except Exception as fe:
                print(f"[verify] Fill error: {fe}")

            # Wait up to 3 min for user to handle 2FA/CAPTCHA
            # AND auto-advance post-CAPTCHA checkpoints, 2FA, or login prompts.
            print("[verify] Waiting for login + c_user cookie (max 3 min)...")
            logged_in = False
            last_login_click_time = time.time()

            for elapsed in range(0, 180, 3):
                await asyncio.sleep(3)
                try:
                    url = page.url
                    ctx_cookies = await session.context.cookies()
                    cookie_names_now = [c["name"] for c in ctx_cookies]
                    has_c_user = "c_user" in cookie_names_now
                    has_xs = "xs" in cookie_names_now
                    print(f"[verify] t={elapsed+3}s url={url[:60]} c_user={has_c_user} xs={has_xs} cookies={cookie_names_now}")

                    if has_c_user and has_xs:
                        logged_in = True
                        print(f"[verify] ✓ c_user + xs cookies present — login confirmed")
                        break

                    # 1. Auto-click post-CAPTCHA checkpoint buttons ("Continue", "Save Browser", "This was me", etc.)
                    checkpoint_buttons = [
                        'div[role="button"]:has-text("Continue")',
                        'button:has-text("Continue")',
                        'div[role="button"]:has-text("Save Browser")',
                        'button:has-text("Save Browser")',
                        'div[role="button"]:has-text("This was me")',
                        'button:has-text("This was me")',
                        'div[role="button"]:has-text("Trust this device")',
                        'button:has-text("Trust this device")',
                        'div[role="button"]:has-text("OK")',
                        'button:has-text("OK")',
                        '#checkpointSubmitButton',
                        'button:has-text("Submit")',
                        'input[type="submit"]',
                    ]
                    for btn_sel in checkpoint_buttons:
                        try:
                            btn = page.locator(btn_sel).first
                            if await btn.is_visible():
                                print(f"[verify] Found visible checkpoint button ({btn_sel}) — clicking...")
                                await btn.click()
                                await asyncio.sleep(2)
                                break
                        except Exception:
                            continue

                    # 2. Check for 2FA requirement post-CAPTCHA
                    if two_factor_secret and (
                        "checkpoint" in url or "two_step" in url
                        or "approvals" in url or "two_factor" in url
                    ):
                        print("[verify] 2FA/checkpoint screen active — attempting 2FA auto-fill...")
                        await bm._handle_2fa(session, two_factor_secret)

                    # 3. Handle case where Facebook redirects to login form post-CAPTCHA verification
                    now = time.time()
                    if ("login" in url or "facebook.com" in url) and not (has_c_user and has_xs):
                        try:
                            pass_el = page.locator(
                                '#pass, input[name="pass"], input[type="password"], input[placeholder*="Password"]'
                            ).first
                            if await pass_el.is_visible():
                                pass_val = await pass_el.input_value()
                                if not pass_val and password:
                                    print("[verify] Password field empty on login page post-CAPTCHA — re-filling password...")
                                    await pass_el.fill(password)
                                    await asyncio.sleep(0.5)

                                login_btn = page.locator(
                                    '[name="login"], button[type="submit"], '
                                    'div[role="button"]:has-text("Log in"), button:has-text("Log in")'
                                ).first
                                if await login_btn.is_visible() and (now - last_login_click_time > 12):
                                    print("[verify] Re-clicking Log In button post-verification...")
                                    await login_btn.click()
                                    last_login_click_time = now
                                    await asyncio.sleep(3)
                        except Exception as re_login_err:
                            print(f"[verify] Re-login check error: {re_login_err}")

                except Exception as loop_err:
                    print(f"[verify] loop check error: {loop_err}")

            if not logged_in:
                raise HTTPException(
                    status_code=408,
                    detail="Timed out — c_user/xs cookies not found after 3 minutes. Please try again and complete any Facebook security prompts."
                )

            # Navigate a few pages so all cookies are written before saving
            print("[verify] Login confirmed. Collecting all session cookies...")
            for nav_url, label in [
                ("https://www.facebook.com", "homepage"),
                ("https://www.facebook.com/me", "profile"),
                ("https://www.facebook.com", "homepage-2"),
            ]:
                try:
                    await page.goto(nav_url, timeout=20000)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await asyncio.sleep(3)
                    print(f"[verify] Navigated to {label}")
                except Exception as e:
                    print(f"[verify] {label} nav error: {e}")

            await asyncio.sleep(3)

            # Final cookie collection with detailed logging
            cookies_json = await session.save_cookies()
            cookies_list = _json.loads(cookies_json)
            cookie_names = [c["name"] for c in cookies_list]
            for c in cookies_list:
                if c["name"] in ("c_user", "xs", "datr", "fr"):
                    print(f"[verify] COOKIE {c['name']}: domain={c.get('domain')} path={c.get('path')} secure={c.get('secure')} httpOnly={c.get('httpOnly')} sameSite={c.get('sameSite')} expires={c.get('expires')}")
            print(f"[verify] Saving {len(cookies_list)} cookies to DB: {cookie_names}")

            critical_cookies = [c for c in cookie_names if c in ["c_user", "xs", "datr", "sb"]]
            print(f"[verify] Critical cookies captured: {critical_cookies}")

            if "c_user" not in cookie_names or "xs" not in cookie_names:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Critical session cookies (c_user, xs) were NOT captured. "
                        f"Got: {cookie_names}. "
                        f"Ensure you are fully logged in and try Verify again."
                    )
                )

            # Save to database
            db.table("fb_accounts").update({
                "cookies": cookies_json,
                "status": "active",
            }).eq("id", account_id).execute()

            print(f"[verify] ✅ Saved {len(cookies_list)} cookies including: {critical_cookies}")

            return {
                "verified": True,
                "message": (
                    f"Account verified! {len(cookies_list)} cookies saved. "
                    f"Critical: {critical_cookies}"
                ),
            }

    except HTTPException:
        raise
    except Exception as e:
        import traceback

        error = traceback.format_exc()
        print(error)

        raise HTTPException(
            status_code=500,
            detail={
                "type": type(e).__name__,
                "error": repr(e),
                "traceback": error
            }
    )


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
