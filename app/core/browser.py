import asyncio
import base64
import hashlib
import hmac
import json
import random
import re
import struct
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional

# Python version check for Playwright compatibility
if sys.version_info >= (3, 16):
    raise RuntimeError(
        "Python 3.16+ is not supported."
    )

# ── Windows event-loop safety ─────────────────────────────────────────────────
# Playwright (via asyncio.create_subprocess_exec) requires a ProactorEventLoop on
# Windows.  Make *absolutely sure* the policy is installed before any import of
# playwright.async_api instantiates its own event loop.
#
# This is a belt-and-suspenders approach:
#   1. Set the global policy immediately at module level (belt).
#   2. Re-check inside BrowserManager.start() and patch the *running* loop
#      if it somehow ended up being a SelectorEventLoop (suspenders).
if sys.platform == "win32":
    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, asyncio.WindowsProactorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


from playwright.async_api import async_playwright, Browser, BrowserContext, Page

FB_BASE = "https://www.facebook.com"


# ── TOTP / 2FA helpers ────────────────────────────────────────────────────────

def generate_totp(secret: str) -> str:
    """
    Generate a 6-digit TOTP code from a base32 secret using only the standard library.
    Compatible with Google Authenticator / Facebook 2FA.
    """
    # Normalize secret: strip spaces, uppercase, add padding
    secret = secret.replace(" ", "").strip().upper()
    missing_padding = len(secret) % 8
    if missing_padding:
        secret += "=" * (8 - missing_padding)
    key = base64.b32decode(secret, casefold=True)
    t = int(time.time() / 30)
    msg = struct.pack(">Q", t)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[19] & 15
    token = (struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{token:06d}"


def extract_2fa_secret(notes: Optional[str]) -> Optional[str]:
    """
    Extract 2FA TOTP secret from account notes field.
    Supported formats:
        2FA_SECRET: JBSWY3DPEHPK3PXP
        2fa: JBSWY3DPEHPK3PXP
        totp_secret: JBSWY3DPEHPK3PXP
        totp: JBSWY3DPEHPK3PXP
    Returns the secret string if found, None otherwise.
    """
    if not notes:
        return None
    pattern = re.compile(
        r"(?:2fa_secret|2fa|totp_secret|totp)\s*[:=]\s*([A-Z2-7a-z2-7=]+)",
        re.IGNORECASE,
    )
    m = pattern.search(notes)
    return m.group(1).strip() if m else None


# ── Centralized authentication helper ────────────────────────────────────────
# All services must call this instead of duplicating login logic.
# Rules:
#   • If the account has saved cookies → restore them and call is_logged_in().
#     If valid, return True immediately. Never trigger a fresh login.
#   • If no cookies → fall back to BrowserManager.login() (headless credential login).
#   • The Verify API (accounts.py /verify) is the ONLY place that creates a new
#     Facebook login session and saves fresh cookies to the database.

async def do_login(
    session: "BrowserSession",
    account: dict,
    browser_manager: "BrowserManager",
) -> bool:
    """
    Authenticate a BrowserSession using the account's saved cookies or credentials.

    - If cookies are present and valid → return True immediately.
    - If cookies are expired/invalid → clear them and fall through to credential login.
    - If no cookies → attempt credential-based login directly.

    Returns True if the session is authenticated, False otherwise.
    """
    if account.get("cookies"):
        # First check c_user cookie — if present, session is likely valid
        # (headless browsers sometimes fail DOM indicator checks even with valid cookies)
        cookies_list = await session.context.cookies()
        has_c_user = any(c.get("name") == "c_user" for c in cookies_list)
        if has_c_user:
            print("[do_login] c_user cookie present — session valid, skipping DOM check")
            return True
        if await session.is_logged_in():
            return True
        print("[do_login] Cookies expired (no c_user), clearing and falling back to credential login")
        await session.context.clear_cookies()

    # Attempt credential-based login (whether cookies expired or were absent)
    from app.core.config import settings
    from app.core.encryption import decrypt_password

    password = account.get("password", "")
    if settings.encryption_key and password:
        try:
            password = decrypt_password(password)
        except Exception:
            password = ""  # Decryption failed — don't pass encrypted blob

    identifier = account.get("email") or account.get("phone", "")
    two_factor_secret = extract_2fa_secret(account.get("notes"))
    return await browser_manager.login(session, identifier, password, two_factor_secret)


class BrowserSession:
    def __init__(self, context: BrowserContext, page: Page):
        self.context = context
        self.page = page

    async def close(self):
        await self.context.close()

    async def save_cookies(self) -> str:
        cookies = await self.context.cookies()
        return json.dumps(cookies)

    async def human_delay(self, min_ms: int = 800, max_ms: int = 2500):
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    async def human_type(self, selector: str, text: str):
        await self.page.click(selector)
        for char in text:
            await self.page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.18))

    async def random_scroll(self, times: int = 3):
        for _ in range(times):
            scroll_y = random.randint(200, 600)
            await self.page.evaluate(f"window.scrollBy(0, {scroll_y})")
            await self.human_delay(500, 1500)

    async def check_logged_in_at(self, url: str) -> bool:
        """
        Navigate to a specific URL and check if we're logged in by seeing
        if FB redirects us to login or stays on the requested page.
        More efficient than is_logged_in() — only one navigation.
        """
        try:
            await self.page.goto(url, timeout=15000)
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            current = self.page.url
            return "login" not in current and "checkpoint" not in current
        except Exception:
            return False

    async def is_logged_in(self) -> bool:
        """Check login status by examining current page or navigating to FB homepage.

        Uses TWO independent checks before confirming logged-in:
          1. URL check — must be on facebook.com and NOT on /login /checkpoint
          2. Profile indicator check — must see '[aria-label="Your profile"]',
             '[aria-label="Profile"]', or '[data-pagelet="FBPage"]' on the page

        Cookie presence alone is NEVER trusted — expired cookies can be in the
        jar but the session may be dead.
        """
        try:
            current_url = self.page.url
            print(f"[is_logged_in] Current URL: {current_url}")

            # ── helper: run profile-indicator DOM checks ──────────────────
            async def _has_profile_indicators() -> bool:
                cookies = await self.context.cookies()
                has_c_user = any(c.get("name") == "c_user" for c in cookies)
                if not has_c_user:
                    print("[is_logged_in]   no c_user cookie found -> NOT logged in")
                    return False

                checks = await asyncio.gather(
                    self.page.locator('[aria-label="Your profile"]').count(),
                    self.page.locator('[aria-label="Profile"]').count(),
                    self.page.locator('a[href*="/me/"]').count(),
                    self.page.locator('a[href*="profile.php"]').count(),
                    self.page.locator('[aria-label="Account controls and settings"]').count(),
                    self.page.locator('[data-pagelet="FBPage"]').count(),
                    return_exceptions=True,
                )
                ok = [r for r in checks if isinstance(r, int)]
                indicators_found = any(r > 0 for r in ok)
                print(f"[is_logged_in]   profile indicators: {indicators_found}  has_c_user: {has_c_user}  counts={ok}")
                return indicators_found

            # ── helper: does URL look like a login/checkpoint page? ──────
            def _is_login_url(url: str) -> bool:
                return any(k in url for k in ("/login", "checkpoint", "two_step", "approvals"))

            # ── helper: detect redirect loop (login.php?next=) ───────────
            def _is_redirect_loop(url: str) -> bool:
                return "login.php" in url and "next=" in url

            # ──────────────────────────────────────────────────────────────
            if "facebook.com" in current_url:
                if _is_redirect_loop(current_url):
                    print(f"[is_logged_in] Redirect loop detected — cookies invalid")
                    return False
                if _is_login_url(current_url):
                    print(f"[is_logged_in] On login/checkpoint page — not logged in")
                    return False

                if await _has_profile_indicators():
                    print(f"[is_logged_in] [OK] Logged in (profile indicators/cookies found)")
                    return True

                print(f"[is_logged_in] On FB but no profile indicators — likely logged out")
                return False

            # ──────────────────────────────────────────────────────────────
            # Not on Facebook, navigate to homepage to check
            print(f"[is_logged_in] Not on FB, navigating to {FB_BASE} ...")
            try:
                await self.page.goto(FB_BASE, timeout=30000)
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception as nav_err:
                print(f"[is_logged_in] Nav error: {nav_err}")

            await asyncio.sleep(2)
            current_url = self.page.url
            print(f"[is_logged_in] After navigation URL: {current_url}")

            if _is_redirect_loop(current_url):
                print(f"[is_logged_in] Redirect loop detected — cookies invalid")
                return False
            if _is_login_url(current_url):
                print(f"[is_logged_in] On login/checkpoint after nav — not logged in")
                return False

            if "facebook.com" in current_url:
                if await _has_profile_indicators():
                    print(f"[is_logged_in] [OK] Logged in after navigation")
                    return True
                print(f"[is_logged_in] On FB after nav but no profile indicators — logged out")
                return False

            print(f"[is_logged_in] Not on Facebook at all — logged out")
            return False
        except Exception as e:
            print(f"[is_logged_in] Exception: {e}")
            return False

    async def has_critical_cookies(self) -> bool:
        """Check if c_user and xs (the session cookies) are present."""
        cookies = await self.context.cookies()
        names = {c["name"] for c in cookies}
        return "c_user" in names and "xs" in names


class BrowserManager:
    """
    Shared Playwright Chromium lifecycle.

    start()/stop()/new_session() are serialized with an asyncio.Lock so that
    concurrent background tasks cannot observe a half-initialized state where
    _playwright is set but _browser is still None (the NoneType.new_context crash).
    """

    _LAUNCH_ARGS = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--window-size=1366,768",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-popup-blocking",
        "--disable-web-security",
        "--ignore-certificate-errors",
    ]

    def __init__(self, headless: bool = True):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._headless = headless
        self._lock = asyncio.Lock()
        self._active_sessions = 0

    def _browser_ready(self) -> bool:
        return (
            self._browser is not None
            and self._playwright is not None
            and self._browser.is_connected()
        )

    async def _reset_unlocked(self):
        """Tear down playwright/browser without acquiring the lock (caller holds it)."""
        browser, playwright = self._browser, self._playwright
        self._browser = None
        self._playwright = None
        if browser is not None:
            try:
                await browser.close()
            except Exception as e:
                print(f"[BrowserManager] browser.close() during reset: {e}")
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as e:
                print(f"[BrowserManager] playwright.stop() during reset: {e}")

    async def _ensure_proactor_loop(self):
        """
        Windows-only: ensure the *running* event loop is a ProactorEventLoop.
        If the running loop is a SelectorEventLoop, attempt to re-wrap the policy
        and create a new loop — this is our last line of defence when Uvicorn's
        reloader spawns a child process with the wrong loop type.

        Raises RuntimeError if we cannot fix the situation.
        """
        if sys.platform != "win32":
            return

        loop = asyncio.get_running_loop()
        if not isinstance(loop, asyncio.SelectorEventLoop):
            # All good — ProactorEventLoop or uvloop, both work.
            return

        # ── We are on a SelectorEventLoop — try to fix it ──────────────────
        print(
            "[BrowserManager] CRITICAL: Running on Windows SelectorEventLoop. "
            "Attempting to switch to ProactorEventLoop..."
        )

        # Force the global policy to ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        # We cannot change the type of a running loop.  The only safe option
        # is to raise and let the user restart the server correctly.
        raise RuntimeError(
            "Playwright cannot create subprocesses on Windows SelectorEventLoop.\n"
            "This happens when Uvicorn's reloader spawns a child process that "
            "inherits a SelectorEventLoop instead of ProactorEventLoop.\n\n"
            "FIX: Restart your server WITHOUT the --reload flag, or use:\n"
            "    python run_backend.py\n\n"
            "The module-level event loop policy has now been set to "
            "WindowsProactorEventLoopPolicy. A second restart should work."
        )

    async def start(self):
        """
        Ensure Chromium is launched. Safe to call from concurrent tasks.

        Raises on launch failure after resetting internal state so a later
        start() retries from a clean slate instead of returning with _browser=None.
        """
        async with self._lock:
            await self._ensure_proactor_loop()

            if self._browser_ready():
                return

            # Half-initialized or disconnected: wipe before relaunch
            if self._playwright is not None or self._browser is not None:
                await self._reset_unlocked()

            try:
                self._playwright = await async_playwright().start()
                launch_kwargs = {
                    "headless": self._headless,
                    "args": list(self._LAUNCH_ARGS),
                }
                import os
                for path in [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                ]:
                    if os.path.exists(path):
                        launch_kwargs["executable_path"] = path
                        break

                try:
                    self._browser = await self._playwright.chromium.launch(**launch_kwargs)
                except Exception:
                    if "executable_path" in launch_kwargs:
                        launch_kwargs.pop("executable_path")
                        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
                    else:
                        raise
            except Exception:
                await self._reset_unlocked()
                raise

            if self._browser is None:
                await self._reset_unlocked()
                raise RuntimeError("chromium.launch() returned None")

    async def stop(self):
        """Close the shared browser. Blocks while sessions are still active."""
        async with self._lock:
            if self._active_sessions > 0:
                raise RuntimeError(
                    f"Cannot stop BrowserManager while {self._active_sessions} "
                    "session(s) are still open"
                )
            await self._reset_unlocked()

    @asynccontextmanager
    async def new_session(
        self,
        proxy: Optional[str] = None,
        cookies_json: Optional[str] = None,
    ):
        await self.start()

        if self._browser is None:
            raise RuntimeError("Playwright browser is not initialized")

        proxy_config = None
        if proxy:
            parts = proxy.split(":")
            if len(parts) == 4:
                proxy_config = {
                    "server": f"http://{parts[0]}:{parts[1]}",
                    "username": parts[2],
                    "password": parts[3],
                }
            elif len(parts) == 2:
                proxy_config = {"server": f"http://{parts[0]}:{parts[1]}"}

        async with self._lock:
            if not self._browser_ready():
                raise RuntimeError("Playwright browser is not initialized")
            self._active_sessions += 1
            browser = self._browser

        try:
            context = await browser.new_context(
                proxy=proxy_config,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
                accept_downloads=False,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                },
            )
        except Exception:
            async with self._lock:
                self._active_sessions = max(0, self._active_sessions - 1)
            raise

        if cookies_json:
            try:
                cookies = json.loads(cookies_json)
                await context.add_cookies(cookies)
            except Exception:
                pass

        page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = {runtime: {}};
        """)

        session = BrowserSession(context=context, page=page)
        try:
            yield session
        finally:
            try:
                await session.close()
            finally:
                async with self._lock:
                    self._active_sessions = max(0, self._active_sessions - 1)

    async def login(
        self,
        session: "BrowserSession",
        email: str,
        password: str,
        two_factor_secret: Optional[str] = None,
    ) -> bool:
        """
        Log in to Facebook with optional 2FA support.

        If two_factor_secret is provided and a 2FA code entry screen appears,
        it will automatically generate and fill the TOTP code using the
        standard library (no external packages required).
        """
        page = session.page
        try:
            print(f"[browser.login] Navigating to Facebook login page...")
            await page.goto(FB_BASE, timeout=20000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await session.human_delay(1000, 2000)

            current_url = page.url
            print(f"[browser.login] Current URL after navigation: {current_url}")
            if "login" not in current_url and "checkpoint" not in current_url:
                if await session.is_logged_in():
                    print(f"[browser.login] Already logged in, skipping credential entry")
                    return True
                print(f"[browser.login] On Facebook but not logged in, filling credentials...")

            email_sel = '#email, input[name="email"], input[type="email"], input[placeholder*="Email"], input[placeholder*="Mobile"]'
            pass_sel = '#pass, input[name="pass"], input[type="password"], input[placeholder*="Password"]'

            print(f"[browser.login] Looking for email input...")
            email_input = page.locator(email_sel).first
            try:
                await email_input.wait_for(state="visible", timeout=10000)
                print(f"[browser.login] Email input found and visible")
            except Exception as e:
                print(f"[browser.login] Email input not found: {e}")
                print(f"[browser.login] Current URL: {page.url}")
                return False
            await email_input.click()
            await session.human_delay(300, 600)
            print(f"[browser.login] Filling email: {email[:3]}***{email[-3:] if len(email) > 6 else ''}")
            await email_input.fill(email)
            await session.human_delay(500, 1000)

            pass_input = page.locator(pass_sel).first
            await pass_input.click()
            await session.human_delay(300, 600)
            print(f"[browser.login] Filling password (length: {len(password)})")
            await pass_input.fill(password)
            await session.human_delay(500, 1000)

            login_btn = page.locator(
                '[name="login"], button[type="submit"], '
                'div[role="button"]:has-text("Log in"), button:has-text("Log in")'
            ).first
            print(f"[browser.login] Clicking login button...")
            await login_btn.click()
            print(f"[browser.login] Login button clicked, waiting for page load...")
            await page.wait_for_load_state("networkidle", timeout=25000)
            print(f"[browser.login] Waiting for session cookies...")
            await asyncio.sleep(3)

            current_url = page.url
            print(f"[browser.login] After click — URL: {current_url}")

            if "/login" in current_url or "login.php" in current_url:
                print("[browser.login] Still on login page — wrong credentials")
                return False

            # ── 2FA / checkpoint handling ────────────────────────────────────
            if (
                "checkpoint" in current_url
                or "two_step" in current_url
                or "approvals" in current_url
                or "two_factor" in current_url
            ):
                print(f"[browser.login] 2FA / checkpoint detected: {current_url}")
                if two_factor_secret:
                    handled = await self._handle_2fa(session, two_factor_secret)
                    if handled:
                        await asyncio.sleep(4)
                        current_url = page.url
                        print(f"[browser.login] Post-2FA URL: {current_url}")
                        if "checkpoint" not in current_url and "two_step" not in current_url and "login" not in current_url:
                            return True
                    print("[browser.login] 2FA handling did not complete login")
                    return False
                else:
                    print(
                        "[browser.login] 2FA required but no secret provided. "
                        "Add '2fa: <secret>' to account notes for automatic login, "
                        "or use the Verify button for manual 2FA."
                    )
                    return False

            if current_url in ("https://www.facebook.com/", "https://www.facebook.com"):
                return await session.is_logged_in()

            return await session.is_logged_in()
        except Exception as e:
            print(f"[browser.login] Exception: {e}")
            return False

    async def _handle_2fa(
        self,
        session: "BrowserSession",
        two_factor_secret: str,
    ) -> bool:
        """
        Handle the Facebook 2FA code entry screen by generating a TOTP code
        and submitting it. Also handles post-2FA "Save browser" / "Continue" prompts.
        Returns True if successfully past 2FA, False otherwise.
        """
        page = session.page
        print("[browser._handle_2fa] Attempting to fill 2FA code...")

        try:
            code = generate_totp(two_factor_secret)
            print(f"[browser._handle_2fa] Generated TOTP code: {code}")
        except Exception as e:
            print(f"[browser._handle_2fa] Failed to generate TOTP: {e}")
            return False

        code_selectors = [
            'input[name="approvals_code"]',
            'input[id="approvals_code"]',
            'input[name="code"]',
            'input[autocomplete="one-time-code"]',
            'input[type="text"][maxlength="6"]',
            'input[placeholder*="code" i]',
            'input[placeholder*="digit" i]',
            'input[aria-label*="code" i]',
            'input[aria-label*="digit" i]',
        ]

        code_input = None
        for sel in code_selectors:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="visible", timeout=5000)
                code_input = el
                print(f"[browser._handle_2fa] Found code input: {sel}")
                break
            except Exception:
                continue

        if not code_input:
            print("[browser._handle_2fa] No 2FA code input found — page may be unexpected")
            try:
                await page.screenshot(path="debug_2fa_screen.png")
            except Exception:
                pass
            return False

        await code_input.click()
        await asyncio.sleep(0.3)
        await code_input.fill(code)
        await asyncio.sleep(0.5)

        submit_selectors = [
            'button[type="submit"]',
            'div[role="button"]:has-text("Continue")',
            'button:has-text("Continue")',
            '#checkpointSubmitButton',
            'button:has-text("Submit")',
            'input[type="submit"]',
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    await btn.click()
                    submitted = True
                    print(f"[browser._handle_2fa] Clicked submit: {sel}")
                    break
            except Exception:
                continue

        if not submitted:
            await page.keyboard.press("Enter")
            print("[browser._handle_2fa] Pressed Enter to submit 2FA")

        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(3)

        # Handle post-2FA prompts: "Save browser", "Trust this browser", "Continue"
        for _ in range(5):
            current_url = page.url
            print(f"[browser._handle_2fa] Post-2FA URL: {current_url}")
            if "checkpoint" not in current_url and "two_step" not in current_url:
                print("[browser._handle_2fa] Past checkpoint/2FA page")
                break
            cont_selectors = [
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
            clicked = False
            for sel in cont_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible():
                        await btn.click()
                        clicked = True
                        print(f"[browser._handle_2fa] Post-2FA: clicked {sel}")
                        await asyncio.sleep(3)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        break
                except Exception:
                    continue
            if not clicked:
                break

        final_url = page.url
        print(f"[browser._handle_2fa] Final URL after 2FA: {final_url}")
        success = (
            "checkpoint" not in final_url
            and "two_step" not in final_url
            and "login" not in final_url
        )
        print(f"[browser._handle_2fa] 2FA success: {success}")
        return success
