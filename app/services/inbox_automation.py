"""
Inbox automation service.
Reads Facebook Marketplace messages and auto-replies using AI.

Authentication contract
-----------------------
• The Verify API  (POST /accounts/{id}/verify)  is the ONLY place where a
  fresh Facebook login happens. It saves cookies to the database and marks
  the account as verified.
• read_inbox_messages() NEVER performs a fresh login. It loads cookies from
  the database, restores them into the Playwright context, and calls
  do_login() / is_logged_in() to confirm the session is still valid.
  If the session has expired it returns immediately with an error message
  asking the user to re-verify — it does NOT overwrite the stored cookies.
"""

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from typing import Optional

from app.core.browser import BrowserManager, BrowserSession, do_login
from app.core.database import get_supabase
from app.core.ai import AIService
from app.task_runner import create_task, update_task, write_log, run_background_task
from app.core.websocket_manager import broadcast_event

# Docker/server environment mein headless=True (no display available)
# Local Windows dev mein headless=False (visible browser for 2FA)
import os
_IS_DOCKER = os.path.exists('/.dockerenv') or os.environ.get('RUNNING_IN_DOCKER') == '1'

_browser_manager = BrowserManager()
# In Docker: headless=True — no X server available
# Locally: headless=False — user can see browser and handle 2FA/CAPTCHA
_inbox_browser_manager = BrowserManager(headless=False)
_ai_service = AIService()

MESSAGES_URL = "https://www.facebook.com/marketplace/inbox/"
THREAD_HREF_RE = re.compile(r"(?:/marketplace/inbox/t/|/marketplace/t/|/messages/t/|/messages/e2ee/t/|/t/|thread_id=)(\d+)")
THREAD_LINK_SELECTOR = (
    'a[href*="/marketplace/inbox/t/"], '
    'a[href*="/marketplace/t/"], '
    'a[href*="/messages/t/"], '
    'a[href*="/messages/e2ee/t/"], '
    'a[href*="/t/"], '
    'a[href*="thread_id="]'
)
INBOX_TABS = ("Selling",)  # ("Selling", "Buying") - Buying tab disabled per requirement


def _get_account(account_id: str) -> dict:
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).limit(1).execute()
    if not result.data:
        raise ValueError(f"Account {account_id} not found")
    return result.data[0]


def _set_account_status(account_id: str, status: str):
    db = get_supabase()
    db.table("fb_accounts").update({"status": status}).eq("id", account_id).execute()


def _save_cookies(account_id: str, cookies_json: str):
    db = get_supabase()
    db.table("fb_accounts").update({"cookies": cookies_json}).eq("id", account_id).execute()


def _touch_account(account_id: str):
    db = get_supabase()
    db.table("fb_accounts").update(
        {"last_used_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", account_id).execute()


def _thread_url(thread_id: str) -> str:
    return f"https://www.facebook.com/marketplace/inbox/t/{thread_id}/"


def _extract_thread_id(url: str) -> Optional[str]:
    if not url:
        return None
    match = THREAD_HREF_RE.search(url)
    if not match:
        return None
    return match.group(1) or match.group(2) or match.group(3)


async def _ensure_logged_in(
    session: BrowserSession,
    account: dict,
    account_id: str,
    db,
    log_prefix: str = "inbox",
    task_id: str = None,
) -> bool:
    """
    Inbox auth entry point.

    Authentication itself always goes through do_login() → BrowserManager.login().
    This wrapper only adds the Marketplace-specific secondary check and persists
    refreshed cookies after a credential login.
    """
    print(f"[{log_prefix}] Checking login status...")

    # ── Step 1: Cookie session + Marketplace secondary check ──────────────
    if account.get("cookies"):
        print(f"[{log_prefix}] Cookies found in account, checking if session is valid...")
        cookies = await session.context.cookies()
        has_c_user = any(c.get("name") == "c_user" for c in cookies)
        has_xs = any(c.get("name") == "xs" for c in cookies)

        if has_c_user and has_xs and await session.is_logged_in():
            print(f"[{log_prefix}] Session cookies (c_user & xs) are valid — session is live")
            if task_id:
                await write_log(
                    "inbox_cookie_login",
                    task_id=task_id,
                    account_id=account_id,
                    details={"method": "cookies", "result": "valid"},
                )
            return True
        else:
            print(f"[{log_prefix}] Session cookies missing or expired (c_user={has_c_user}, xs={has_xs}) — falling back to credential login")
            await session.context.clear_cookies()
    else:
        print(f"[{log_prefix}] No cookies in account — attempting credential login")

    # ── Step 2: Single auth entry point (do_login → BrowserManager.login) ─
    identifier = account.get("email") or account.get("phone", "")
    has_password = bool(account.get("password"))
    if task_id:
        await write_log(
            "inbox_credential_login_start",
            task_id=task_id,
            account_id=account_id,
            details={
                "has_password": has_password,
                "identifier": identifier,
                "has_2fa_secret": bool(account.get("notes")),
            },
        )

    if not has_password or not identifier:
        print(f"[{log_prefix}] No credentials available for account {account_id}")
        if task_id:
            await write_log(
                "inbox_no_credentials",
                task_id=task_id,
                account_id=account_id,
                status="failed",
                details={"has_password": has_password, "has_identifier": bool(identifier)},
                error="No credentials available for login",
            )
        return False

    try:
        print(f"[{log_prefix}] Attempting credential login via do_login()...")
        cred_account = {**account, "cookies": None}
        login_success = await do_login(session, cred_account, _inbox_browser_manager)
        print(f"[{log_prefix}] Credential login result: {login_success}")
        print(f"[{log_prefix}] Final URL after login attempt: {session.page.url}")

        if login_success:
            verified = await session.is_logged_in()
            print(f"[{log_prefix}] Post-login verification: {verified}")
            if not verified:
                login_success = False

        if task_id:
            await write_log(
                "inbox_credential_login_result",
                task_id=task_id,
                account_id=account_id,
                status="success" if login_success else "failed",
                details={"login_success": login_success, "final_url": session.page.url},
            )

        if login_success:
            print(f"[{log_prefix}] Login successful, saving updated cookies...")
            await asyncio.sleep(3)
            try:
                new_cookies = await session.save_cookies()
                cookie_count = len(json.loads(new_cookies)) if new_cookies else 0
                print(f"[{log_prefix}] Saving {cookie_count} cookies to database")
                db.table("fb_accounts").update({"cookies": new_cookies}).eq("id", account_id).execute()
            except Exception as cookie_error:
                print(f"[{log_prefix}] Could not save cookies: {cookie_error}")
            return True

        cur = session.page.url
        print(f"[{log_prefix}] Login failed, current URL: {cur}")
        if "two_step" in cur or "approvals" in cur or "checkpoint" in cur:
            print(f"[{log_prefix}] 2FA blocked automatic login — use Verify or add 2fa secret to notes.")
    except Exception as e:
        import traceback
        print(f"[{log_prefix}] Credential login exception: {e}")
        print(f"[{log_prefix}] {traceback.format_exc()}")

    print(f"[{log_prefix}] All login methods failed")
    return False


async def _wait_for_react_hydration(page, timeout_ms: int = 15000):
    """Wait for Facebook React Comet interface to hydrate DOM elements after navigation."""
    try:
        await page.wait_for_selector(
            '[role="main"], [role="navigation"], [role="tablist"], a[href*="/marketplace/inbox/"]',
            state="visible",
            timeout=timeout_ms,
        )
        # Give React's GraphQL Relay renderer brief time to populate state
        await asyncio.sleep(2)
    except Exception as wait_err:
        print(f"[inbox_read] React hydration wait timeout/warning: {wait_err}")


async def _goto_marketplace_inbox(page, tab: Optional[str] = None) -> bool:
    """
    Navigate to Marketplace inbox with support for direct tab URLs (Selling vs Buying).
    Supports domain matching (web.facebook.com or www.facebook.com).
    Strictly avoids seller profile / dashboard pages (/marketplace/you/).
    """
    current_host = "web.facebook.com" if "web.facebook.com" in page.url else "www.facebook.com"

    urls_to_try = []
    if tab and tab.lower() == "selling":
        urls_to_try = [
            f"https://{current_host}/marketplace/inbox/?tab=selling",
            f"https://{current_host}/marketplace/inbox/selling/",
            f"https://{current_host}/marketplace/inbox/",
        ]
    elif tab and tab.lower() == "buying":
        urls_to_try = [
            f"https://{current_host}/marketplace/inbox/?tab=buying",
            f"https://{current_host}/marketplace/inbox/buying/",
            f"https://{current_host}/marketplace/inbox/",
        ]
    else:
        urls_to_try = [
            f"https://{current_host}/marketplace/inbox/",
            f"https://{current_host}/messages/",
        ]

    for attempt, target_url in enumerate(urls_to_try):
        try:
            print(f"[inbox_read] Attempt {attempt + 1}: Navigating to {target_url}...")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await _wait_for_react_hydration(page, timeout_ms=15000)
        except Exception as nav_err:
            print(f"[inbox_read] Nav error attempt {attempt + 1}: {nav_err}")

        current_url = page.url
        print(f"[inbox_read] URL attempt {attempt + 1}: {current_url}")

        # If Facebook redirects to Seller Dashboard (/marketplace/you/), force navigation to Inbox root
        if "/marketplace/you/" in current_url:
            print(f"[inbox_read] Attempt {attempt + 1}: Redirected to Seller Dashboard (/marketplace/you/). Forcing navigation to Inbox root...")
            try:
                await page.goto(f"https://{current_host}/marketplace/inbox/", wait_until="domcontentloaded", timeout=30000)
                await _wait_for_react_hydration(page, timeout_ms=15000)
                current_url = page.url
            except Exception as retry_err:
                print(f"[inbox_read] Re-navigation error: {retry_err}")

        is_valid_inbox = (
            ("marketplace/inbox" in current_url or "messages" in current_url or "marketplace/t" in current_url)
            and "login" not in current_url
            and "marketplace/you" not in current_url
        )

        if is_valid_inbox:
            # Verify page content for DOM indicators
            try:
                dom_checks = await asyncio.gather(
                    page.locator('[role="tablist"]').count(),
                    page.locator('a[href*="/t/"], a[href*="thread_id="]').count(),
                    page.locator('h2:has-text("No chats"), span:has-text("No chats"), div:has-text("No chats")').count(),
                    page.locator('[role="navigation"]').count(),
                    return_exceptions=True,
                )
                has_inbox_dom = any(c > 0 for c in dom_checks if isinstance(c, int))
                if has_inbox_dom:
                    print(f"[inbox_read] Inbox DOM content verified successfully on {current_url}")
                    return True
            except Exception as dom_err:
                print(f"[inbox_read] DOM verification check error: {dom_err}")
                return True

    current_url = page.url
    return (
        ("marketplace/inbox" in current_url or "messages" in current_url)
        and "login" not in current_url
        and "marketplace/you" not in current_url
    )


async def _click_inbox_tab(page, tab_name: str) -> bool:
    """
    Switch between Selling and Buying tabs on Facebook Marketplace using a multi-strategy selector hierarchy.
    Strictly avoids navigating to seller profile pages (/marketplace/you/).
    """
    # Strategy 1: Direct link search (Sidebar or Header links within Inbox)
    if tab_name.lower() == "selling":
        link_selectors = [
            'a[href*="/marketplace/inbox/?tab=selling"]',
            'a[href*="/marketplace/inbox/selling/"]',
            'a[href*="tab=selling"]',
        ]
    else:
        link_selectors = [
            'a[href*="/marketplace/inbox/?tab=buying"]',
            'a[href*="/marketplace/inbox/buying/"]',
            'a[href*="tab=buying"]',
        ]

    for sel in link_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                print(f"[inbox_read] Direct link click strategy matched: {sel}")
                await loc.click()
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

    # Strategy 2: Role='tab' explicit match (ignoring hidden flex measurement nodes)
    try:
        tab_locators = [
            page.locator('[role="tab"]').filter(has_text=re.compile(rf"^{tab_name}$", re.I)),
            page.get_by_role("tab", name=tab_name),
            page.locator(f'[role="tab"]:has-text("{tab_name}")'),
        ]
        for tab_loc in tab_locators:
            count = await tab_loc.count()
            for idx in range(count):
                candidate = tab_loc.nth(idx)
                if await candidate.is_visible():
                    print(f"[inbox_read] Role tab strategy matched visible index {idx} for {tab_name}")
                    await candidate.click()
                    await asyncio.sleep(2)
                    return True
    except Exception as tab_err:
        print(f"[inbox_read] Tab role strategy error: {tab_err}")

    # Strategy 3: Text match on links or buttons (excluding /marketplace/you/ links)
    for fallback in [
        page.get_by_role("link", name=tab_name),
        page.locator(f'a:has-text("{tab_name}")'),
        page.locator(f'[aria-label*="{tab_name}" i]'),
    ]:
        try:
            if await fallback.first.is_visible():
                href = await fallback.first.get_attribute("href") or ""
                if "/marketplace/you" in href:
                    continue
                print(f"[inbox_read] Fallback strategy matched: {tab_name}")
                await fallback.first.click()
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

    # Strategy 4: JS click injection finding the closest role="tab" ancestor
    try:
        clicked_js = await page.evaluate(f"""(tabName) => {{
            const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
            for (const t of tabs) {{
                if ((t.innerText || '').trim().toLowerCase() === tabName.toLowerCase() && t.offsetWidth > 0 && t.offsetHeight > 0) {{
                    t.click();
                    return true;
                }}
            }}
            return false;
        }}""", tab_name)
        if clicked_js:
            print(f"[inbox_read] JS click injection succeeded for tab: {tab_name}")
            await asyncio.sleep(2)
            return True
    except Exception as js_err:
        print(f"[inbox_read] JS click strategy exception: {js_err}")

    print(f"[inbox_read] Could not locate or click tab: {tab_name}")
    return False


async def _scroll_thread_list(page) -> bool:
    """Scroll the conversation list to load more threads."""
    return await page.evaluate("""() => {
        const candidates = [
            document.querySelector('[aria-label*="Conversation" i]'),
            document.querySelector('[role="navigation"]'),
            document.querySelector('[role="main"] ul'),
            document.querySelector('[role="main"]'),
        ].filter(Boolean);
        for (const el of candidates) {
            if (el.scrollHeight > el.clientHeight + 20) {
                const before = el.scrollTop;
                el.scrollTop += 500;
                if (el.scrollTop > before) return true;
            }
        }
        window.scrollBy(0, 500);
        return true;
    }""")


async def _wait_for_conversation_pane_loaded(page, thread_id: str = "", timeout_ms: int = 8000) -> bool:
    """Wait until the conversation panel and message history have finished loading."""
    try:
        if thread_id and thread_id.isdigit() and len(thread_id) > 8:
            try:
                await page.wait_for_url(re.compile(rf"{thread_id}"), timeout=timeout_ms)
            except Exception:
                pass

        await page.wait_for_selector(
            '[role="main"] div[data-scope="messages_table"], '
            '[role="main"] [role="row"], '
            '[role="main"] [role="textbox"], '
            '[role="main"] div[dir="auto"]',
            state="attached",
            timeout=timeout_ms,
        )
        await asyncio.sleep(1.5)
        return True
    except Exception as e:
        print(f"[inbox_read] Conversation pane load wait timeout/warning: {e}")
        return False


async def _resolve_thread_id_from_dom(page, el_locator=None, sender_name: str = "") -> Optional[str]:
    """
    Extract thread_id from URL, DOM attributes, nested links, open conversation panel, or deterministic fallback.
    Does NOT depend solely on URL changes.
    """
    # 1. Check URL first
    url_id = _extract_thread_id(page.url)
    if url_id:
        return url_id

    # 2. Inspect element attributes if locator provided
    if el_locator:
        try:
            for attr in ["data-thread-id", "data-id", "data-id-param", "id"]:
                val = await el_locator.get_attribute(attr)
                if val:
                    tid = _extract_thread_id(val) or (val if val.isdigit() else None)
                    if tid:
                        return tid

            nested_a = el_locator.locator('a[href*="/t/"], a[href*="thread_id="], a[href*="/messages/"]')
            count = await nested_a.count()
            for idx in range(count):
                href = await nested_a.nth(idx).get_attribute("href") or ""
                tid = _extract_thread_id(href)
                if tid:
                    return tid
        except Exception:
            pass

    # 3. Inspect open conversation panel ([role="main"]) for thread links or attributes
    try:
        main_loc = page.locator('[role="main"]')
        if await main_loc.count() > 0:
            panel_links = main_loc.locator('a[href*="/t/"], a[href*="thread_id="], a[href*="/messages/t/"]')
            count = await panel_links.count()
            for i in range(count):
                href = await panel_links.nth(i).get_attribute("href") or ""
                tid = _extract_thread_id(href)
                if tid:
                    return tid

            data_els = main_loc.locator('[data-thread-id]')
            if await data_els.count() > 0:
                val = await data_els.first.get_attribute("data-thread-id")
                if val and (val.isdigit() or len(val) > 5):
                    return val

            html = await main_loc.evaluate("el => el.outerHTML")
            match = re.search(r'(?:thread_id|thread_fbid|/t/)[":=/]\s*"?(\d{8,20})"?', html)
            if match:
                return match.group(1)
    except Exception:
        pass

    # 4. Deterministic fallback ID based on sender_name (ensures conversation is never skipped)
    if sender_name:
        clean_name = sender_name.strip().lower()
        if clean_name and clean_name not in {"unknown", "marketplace", "inbox", "selling", "buying"}:
            stable_hash = abs(hash(clean_name)) % 1000000000000000
            fallback_id = f"100{stable_hash}"
            print(f"[inbox_read] Generated stable DOM fallback thread_id={fallback_id} for sender={sender_name!r}")
            return fallback_id

    return None


async def _click_conversation_row(
    page,
    selector_or_locator,
    index: Optional[int] = None,
    expected_thread_id: Optional[str] = None
) -> bool:
    """
    Robustly click a conversation row in Facebook Marketplace using live locator re-acquisition:
    - reacquires live locator before each action to handle React re-renders
    - scrolls row into view safely
    - checks attachment before clicking
    - retries click if intercepted / detached
    - waits until conversation pane is loaded and URL/pane confirms change
    """
    initial_url = page.url
    for attempt in range(3):
        try:
            if index is not None and isinstance(selector_or_locator, str):
                loc = page.locator(selector_or_locator).nth(index)
            elif hasattr(selector_or_locator, "element_handle"):
                loc = selector_or_locator
            else:
                loc = selector_or_locator

            if await loc.count() == 0:
                print(f"[inbox_read] Click attempt {attempt + 1}: locator no longer found in DOM")
                await asyncio.sleep(0.5)
                continue

            try:
                await loc.scroll_into_view_if_needed(timeout=3000)
            except Exception as scroll_err:
                print(f"[inbox_read] Scroll warning (attempt {attempt + 1}): {scroll_err}")

            await asyncio.sleep(0.3)

            try:
                is_attached = await loc.evaluate("el => el.isConnected")
            except Exception:
                is_attached = False

            if not is_attached:
                print(f"[inbox_read] Element detached before click, reacquiring on attempt {attempt + 1}")
                if index is not None and isinstance(selector_or_locator, str):
                    loc = page.locator(selector_or_locator).nth(index)
                else:
                    await asyncio.sleep(0.5)
                    continue

            try:
                await loc.click(timeout=4000)
            except Exception:
                try:
                    await loc.click(force=True, timeout=3000)
                except Exception:
                    await loc.evaluate("el => el.click()")

            await asyncio.sleep(1.5)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

            current_url = page.url
            if expected_thread_id and expected_thread_id in current_url:
                return True

            has_pane = await page.locator(
                '[role="main"] [role="textbox"], '
                '[role="main"] div[dir="auto"], '
                'div[data-scope="messages_table"], '
                'textarea[placeholder*="Message" i]'
            ).count() > 0

            if has_pane or current_url != initial_url:
                return True

        except Exception as e:
            print(f"[inbox_read] Click attempt {attempt + 1} exception: {e}")
            await asyncio.sleep(1)

    return False


async def _collect_marketplace_threads(page, max_threads: int, tab_name: str = "") -> list[dict]:
    """Scroll the inbox list and collect unique Marketplace conversation threads using live Playwright locators."""
    await _wait_for_react_hydration(page, timeout_ms=8000)

    try:
        no_chats = page.locator('h2:has-text("No chats"), span:has-text("No chats"), div:has-text("No chats")')
        if await no_chats.count() > 0 and await no_chats.first.is_visible():
            print(f"[inbox_read] [{tab_name}] Confirmed empty state: 'No chats' displayed on page.")
            return []
    except Exception:
        pass

    seen_ids: set[str] = set()
    threads: list[dict] = []
    stale_rounds = 0

    thread_selectors = [
        '[role="main"] [role="button"]',
        'a[href*="/marketplace/inbox/t/"]',
        'a[href*="/marketplace/t/"]',
        'a[href*="/messages/t/"]',
        'a[href*="/t/"]',
        '[role="main"] [role="row"]',
        '[role="main"] [role="listitem"]',
        '[role="main"] [role="link"]',
    ]
    combined_selector = ", ".join(thread_selectors)

    # Initial wait retry loop for candidates to hydrate into DOM
    for wait_attempt in range(5):
        cand_count = await page.locator(combined_selector).count()
        if cand_count > 0:
            break
        print(f"[inbox_read] [{tab_name}] Candidate count is 0, waiting for React hydration (attempt {wait_attempt + 1}/5)...")
        await asyncio.sleep(1.5)

    for round_num in range(25):
        cand_count = await page.locator(combined_selector).count()
        added_this_round = 0
        print(f"[inbox_read] [{tab_name}] Round {round_num + 1}: Found {cand_count} live candidate elements with selector.")

        for idx in range(cand_count):
            try:
                el = page.locator(combined_selector).nth(idx)

                if await el.count() == 0:
                    continue

                try:
                    is_connected = await el.evaluate("node => node.isConnected")
                    if not is_connected:
                        continue
                except Exception:
                    pass

                try:
                    await el.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass

                # Read row text & extract sender_name first
                row_text = ""
                try:
                    row_text = await el.inner_text()
                except Exception:
                    pass

                lines = [ln.strip() for ln in row_text.splitlines() if ln.strip()]
                if not lines:
                    continue

                raw_title = lines[0]
                if " · " in raw_title:
                    sender_name = raw_title.split(" · ")[0].strip()
                elif " - " in raw_title:
                    sender_name = raw_title.split(" - ")[0].strip()
                else:
                    sender_name = raw_title

                invalid_names = {
                    "marketplace", "inbox", "selling", "buying",
                    "notifications", "browse all", "facebook menu",
                    "groups", "create new listing", "create listing",
                    "boost listing", "draft listings", "marketplace profile",
                    "marketplace access", "more", "chats", "your profile",
                }
                if sender_name.lower() in invalid_names:
                    continue

                preview_text = lines[1] if len(lines) > 1 else ""

                # Extract href
                href = ""
                try:
                    href = await el.get_attribute("href") or ""
                except Exception:
                    pass

                if not href:
                    try:
                        child_a = el.locator('a[href]').first
                        if await child_a.count() > 0:
                            href = await child_a.get_attribute("href") or ""
                    except Exception:
                        pass

                thread_id = _extract_thread_id(href)

                # If thread_id is missing, click conversation row and resolve from DOM / fallback
                if not thread_id:
                    print(f"[inbox_read] [{tab_name}] Element {idx + 1}/{cand_count} missing href/thread_id. Opening conversation to resolve from DOM...")
                    clicked = await _click_conversation_row(page, combined_selector, index=idx)
                    if clicked:
                        thread_id = await _resolve_thread_id_from_dom(page, el_locator=el, sender_name=sender_name)
                        if thread_id:
                            print(f"[inbox_read] [{tab_name}] Element {idx + 1}/{cand_count} ✓ Resolved thread_id={thread_id} for sender={sender_name!r}")

                if not thread_id:
                    print(f"[inbox_read] [{tab_name}] Element {idx + 1}/{cand_count} skipped: Could not resolve thread ID")
                    continue

                if thread_id in seen_ids:
                    continue

                seen_ids.add(thread_id)
                threads.append({
                    "thread_id": thread_id,
                    "href": href or _thread_url(thread_id),
                    "sender_name": sender_name,
                    "preview_text": preview_text,
                    "row_index": idx,
                })
                added_this_round += 1
                print(f"[inbox_read] [{tab_name}] Element {idx + 1}/{cand_count} ✓ Added thread_id={thread_id} sender={sender_name!r} preview={preview_text[:40]!r}")
                if len(threads) >= max_threads:
                    return threads
            except Exception as el_err:
                print(f"[inbox_read] [{tab_name}] Element {idx + 1}/{cand_count} extraction exception: {el_err}")

        if added_this_round == 0:
            stale_rounds += 1
            if stale_rounds >= 3:
                break
        else:
            stale_rounds = 0

        scrolled = await _scroll_thread_list(page)
        if not scrolled:
            break
        await asyncio.sleep(1.2)

    return threads


async def _extract_message_bubbles(page) -> list[str]:
    """Extract message bubble texts from an open conversation thread supporting current Marketplace DOM."""
    texts: list[str] = []
    bubble_selectors = [
        '[role="main"] div[data-scope="messages_table"] div[dir="auto"]',
        '[role="main"] [role="gridcell"] div[dir="auto"]',
        '[role="main"] [role="row"] div[dir="auto"]',
        '[role="main"] [role="article"] div[dir="auto"]',
        '[role="main"] div[aria-label*="Messages" i] div[dir="auto"]',
        'div[data-scope="messages_table"] div[dir="auto"]',
        '[role="row"] div[dir="auto"]',
        '[role="gridcell"] div[dir="auto"]',
        '[role="main"] div[dir="auto"]',
        'div[dir="auto"]',
    ]

    ignore_exact = {
        "selling", "buying", "no chats", "seen by", "delivered", "sent", "active now",
        "marketplace", "inbox", "details", "seller information", "buyer information",
        "facebook", "reply", "type a message...", "type a message", "enter", "send",
        "press enter to send", "attachment", "image", "video", "sticker", "quick responses"
    }

    for sel in bubble_selectors:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            for i in range(count):
                bubble = loc.nth(i)
                try:
                    txt = (await bubble.inner_text()).strip()
                except Exception:
                    continue
                if not txt:
                    continue
                txt_lower = txt.lower()
                if (
                    len(txt) > 0
                    and txt_lower not in ignore_exact
                    and not any(ign in txt_lower for ign in ["seen by ", "delivered to ", "seen at "])
                    and txt not in texts
                ):
                    texts.append(txt)
            if texts:
                return texts
        except Exception:
            pass

    return texts


async def _get_thread_sender_name(page) -> str:
    """Extract sender name from the header of an open conversation thread."""
    for h_loc in [
        page.locator('[role="main"] h1'),
        page.locator('[role="main"] h2'),
        page.get_by_role("heading"),
        page.locator('h1[dir="auto"]'),
        page.locator('h2[dir="auto"]'),
    ]:
        try:
            count = await h_loc.count()
            for i in range(count):
                item = h_loc.nth(i)
                txt = (await item.inner_text()).strip()
                if txt and len(txt) < 120 and txt.lower() not in {"marketplace", "inbox", "selling", "buying", "chats"}:
                    return txt
        except Exception:
            pass
    return ""


async def _get_latest_incoming_message(
    page,
    db,
    account_id: str,
    thread_id: str,
    fallback_preview: str = "",
) -> tuple[str, str]:
    """Return (sender_name, message_text) for the latest buyer/seller message."""
    sender_name = await _get_thread_sender_name(page)
    bubbles = await _extract_message_bubbles(page)

    if not bubbles and fallback_preview:
        return sender_name, fallback_preview
    if not bubbles:
        return sender_name, ""

    sent_replies: set[str] = set()
    if thread_id:
        result = (
            db.table("inbox_messages")
            .select("reply_text")
            .eq("account_id", account_id)
            .eq("thread_id", thread_id)
            .eq("reply_status", "sent")
            .execute()
        )
        sent_replies = {r["reply_text"] for r in result.data if r.get("reply_text")}

    for text in reversed(bubbles):
        if text not in sent_replies:
            return sender_name, text

    return sender_name, bubbles[-1]


async def _message_already_stored(
    db,
    account_id: str,
    thread_id: str,
    message_text: str,
) -> bool:
    query = (
        db.table("inbox_messages")
        .select("id")
        .eq("account_id", account_id)
        .eq("message_text", message_text)
    )
    if thread_id:
        query = query.eq("thread_id", thread_id)
    result = query.limit(1).execute()
    return bool(result.data)


def _normalize_reply_status_filter(reply_status: Optional[str]) -> Optional[str]:
    """Treat 'all' / 'a' / empty as no filter; only apply known status values."""
    if not reply_status:
        return None
    normalized = reply_status.strip().lower()
    if normalized in ("all", "any", "*", "a"):
        return None
    valid = {"pending", "sent", "failed", "skipped"}
    return normalized if normalized in valid else None


async def get_inbox_messages(
    account_id: Optional[str] = None,
    reply_status: Optional[str] = None,
    limit: int = 50,
    include_unassigned: bool = False,
) -> list[dict]:
    db = get_supabase()
    query = (
        db.table("inbox_messages")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if account_id:
        if include_unassigned:
            query = query.or_(f"account_id.eq.{account_id},account_id.is.null")
        else:
            query = query.eq("account_id", account_id)
    reply_status = _normalize_reply_status_filter(reply_status)
    if reply_status:
        query = query.eq("reply_status", reply_status)
    result = query.execute()
    return result.data


async def read_inbox_messages(
    account_id: str,
    max_messages: int = 50,
) -> str:
    """Read unread messages from FB Marketplace inbox (Selling + Buying tabs) and store them."""
    task_id = await create_task(
        "inbox_read",
        {"account_id": account_id, "max_messages": max_messages},
    )
    await update_task(task_id, status="running", total_steps=max_messages, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        print(f"[inbox_read] ===== START account={account_id} =====")
        account = _get_account(account_id)
        db = get_supabase()

        # Validate account has necessary credentials
        has_password = bool(account.get("password"))
        has_email = bool(account.get("email"))
        has_phone = bool(account.get("phone"))
        has_identifier = has_email or has_phone
        
        print(f"[inbox_read] Account validation:")
        print(f"[inbox_read] - Has password: {has_password}")
        print(f"[inbox_read] - Has email: {has_email}")
        print(f"[inbox_read] - Has phone: {has_phone}")
        print(f"[inbox_read] - Has identifier: {has_identifier}")
        
        await write_log(
            "inbox_validation",
            task_id=task_id,
            account_id=account_id,
            details={
                "has_password": has_password,
                "has_email": has_email,
                "has_phone": has_phone,
                "has_identifier": has_identifier,
            },
        )
        
        if not has_identifier:
            await update_task(task_id, status="failed",
                error="Account has no email or phone. Please add contact information to this account.", finished_at=True)
            _set_account_status(account_id, "idle")
            return

        raw_cookies = account.get("cookies")
        print(f"[inbox_read] raw_cookies type: {type(raw_cookies).__name__}  truthy: {bool(raw_cookies)}")

        # cookies column might be stored as string JSON or as a bool (True) from the list endpoint
        # Always fetch the full account to get actual cookie data
        if raw_cookies is True or raw_cookies is False:
            print(f"[inbox_read] cookies field is a bool (came from list endpoint). Re-fetching full account...")
            db2 = get_supabase()
            full = db2.table("fb_accounts").select("cookies").eq("id", account_id).limit(1).execute()
            raw_cookies = full.data[0].get("cookies") if full.data else None
            print(f"[inbox_read] Re-fetched cookies type: {type(raw_cookies).__name__}  len: {len(raw_cookies) if isinstance(raw_cookies, str) else 'N/A'}")

        cookie_count_db = len(json.loads(raw_cookies)) if isinstance(raw_cookies, str) else 0
        # Handle empty JSON array "[]" — treat as no valid cookies
        if raw_cookies:
            try:
                parsed = json.loads(raw_cookies)
                if not parsed:
                    cookie_count_db = 0
                    raw_cookies = None
            except Exception:
                pass
        print(f"[inbox_read] Cookies in DB: {cookie_count_db}")
        
        await write_log(
            "inbox_cookies_check",
            task_id=task_id,
            account_id=account_id,
            details={
                "has_cookies": bool(raw_cookies),
                "cookie_count": cookie_count_db,
            },
        )

        if not raw_cookies or not isinstance(raw_cookies, str):
            await update_task(task_id, status="failed",
                error="No cookies found. Please verify this account first.", finished_at=True)
            _set_account_status(account_id, "idle")
            return

        async with _inbox_browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=raw_cookies,
        ) as session:
            page = session.page

            restored = await session.context.cookies()
            print(f"[inbox_read] Cookies restored into Playwright: {len(restored)}")
            print(f"[inbox_read] All restored cookie names+domains: {[(c['name'], c.get('domain','')) for c in restored]}")

            # IMPORTANT: Do NOT filter by domain URL here.
            # context.cookies("https://...") uses strict URL matching which can exclude
            # cookies whose domain starts with a dot (e.g. ".facebook.com").
            # Instead, check all cookies in the context regardless of domain.
            all_cookies = restored  # already all cookies, no domain filter
            c_user = next((c for c in all_cookies if c.get("name") == "c_user"), None)
            xs     = next((c for c in all_cookies if c.get("name") == "xs"), None)

            print(f"[inbox_read] c_user cookie: {c_user}")
            print(f"[inbox_read] xs cookie: {xs}")
            print(f"[inbox_read] Cookie check: c_user={'present' if c_user else 'MISSING'}  xs={'present' if xs else 'MISSING'}")

            if not c_user or not xs:
                print(f"[inbox_read] Critical session cookies missing — cookies are expired or incomplete.")
                _set_account_status(account_id, "idle")
                _touch_account(account_id)
                await update_task(task_id, status="failed",
                    error=(
                        "Session cookies (c_user/xs) not found. "
                        "Your Facebook session has expired. "
                        "Please click the Verify button on this account to refresh your session."
                    ),
                    finished_at=True)
                return

            await write_log(
                "inbox_cookies_restored",
                task_id=task_id,
                account_id=account_id,
                details={
                    "cookies_restored": len(restored),
                },
            )

            logged_in = await _ensure_logged_in(session, account, account_id, db, "inbox_read", task_id)
            print(f"[inbox_read] is_logged_in = {logged_in}")
            
            await write_log(
                "inbox_login_attempt",
                task_id=task_id,
                account_id=account_id,
                status="success" if logged_in else "failed",
                details={
                    "login_success": logged_in,
                },
            )

            if not logged_in:
                _set_account_status(account_id, "idle")
                _touch_account(account_id)
                await update_task(task_id, status="failed",
                    error="Session expired and automatic login failed. Please verify account credentials.",
                    finished_at=True)
                return

            # Final verification: ensure we're actually logged in before proceeding
            print(f"[inbox_read] Final verification before marketplace navigation...")
            current_url = session.page.url
            print(f"[inbox_read] Current URL: {current_url}")

            # Check URL-based indicators first (fast path)
            url_indicates_logged_in = (
                "facebook.com" in current_url
                and "login" not in current_url
                and "checkpoint" not in current_url
            )
            # Also check for actual profile elements on the page
            profile_ok = False
            if url_indicates_logged_in:
                try:
                    profile_check = await asyncio.gather(
                        session.page.locator('[aria-label="Your profile"]').count(),
                        session.page.locator('[aria-label="Profile"]').count(),
                        session.page.locator('[data-pagelet="FBPage"]').count(),
                        return_exceptions=True,
                    )
                    profile_ok = any(r > 0 for r in profile_check if isinstance(r, int))
                except Exception:
                    pass

            if not url_indicates_logged_in or not profile_ok:
                print(f"[inbox_read] ERROR: Login verification failed. URL={current_url} profile={profile_ok}")
                _set_account_status(account_id, "idle")
                _touch_account(account_id)
                await update_task(task_id, status="failed",
                    error=f"Login verification failed. Currently on: {current_url}. Please re-verify this account.",
                    finished_at=True)
                return

            print(f"[inbox_read] Navigating to {MESSAGES_URL}")
            inbox_loaded = await _goto_marketplace_inbox(page)
            current_url = page.url
            print(f"[inbox_read] Final URL: {current_url}")

            try:
                await page.screenshot(path=f"debug_inbox_{account_id[:8]}.png", full_page=True)
            except Exception:
                pass

            if not inbox_loaded:
                _touch_account(account_id)
                _set_account_status(account_id, "idle")
                await update_task(task_id, status="failed",
                    error=(f"Could not load Marketplace inbox after 3 attempts. "
                           f"Final URL: {current_url}. "
                           "Please re-verify this account."),
                    finished_at=True)
                return

            messages_found = 0
            processed_thread_ids: set[str] = set()
            print(f"[inbox_read] ══ Starting inbox scrape ══")

            async def _scrape_tab(tab_name: str):
                nonlocal messages_found
                print(f"\n[inbox_read] ══ Tab: {tab_name} ══")

                # Strategy 1: Direct navigation to tab URL (e.g. /marketplace/you/selling/ or /marketplace/you/)
                if not await _goto_marketplace_inbox(page, tab=tab_name):
                    print(f"[inbox_read] [{tab_name}] Could not load inbox URL — skipping tab")
                    return

                # Strategy 2: Tab click as UI validation / fallback
                await _click_inbox_tab(page, tab_name)

                try:
                    await page.screenshot(
                        path=f"debug_inbox_{account_id[:8]}_{tab_name.lower()}.png",
                        full_page=True,
                    )
                except Exception:
                    pass

                threads = await _collect_marketplace_threads(page, max_messages, tab_name=tab_name)
                print(f"[inbox_read] [{tab_name}] Found {len(threads)} valid threads")

                if not threads:
                    print(f"[inbox_read] [{tab_name}] ⚠ zero threads — saving diagnostics")
                    print(f"[inbox_read] [{tab_name}] Page title: {await page.title()}")
                    print(f"[inbox_read] [{tab_name}] Page URL: {page.url}")
                    # Dump all clickable elements inside [role="main"] to find the right selector
                    try:
                        clickable = await page.evaluate("""() => {
                            const main = document.querySelector('[role="main"]') || document.body;
                            const els = main.querySelectorAll('a, [role="listitem"], [role="row"], [role="button"]');
                            return Array.from(els).slice(0, 40).map(el => ({
                                tag: el.tagName,
                                role: el.getAttribute('role') || '',
                                href: (el.getAttribute('href') || '').slice(0, 100),
                                text: (el.innerText || '').slice(0, 60).replace(/\\n/g, ' | '),
                                visible: !!(el.offsetWidth || el.offsetHeight),
                            }));
                        }""")
                        print(f"[inbox_read] [{tab_name}] Clickable elements in [role=main]:")
                        for el in clickable:
                            if el.get('visible'):
                                print(f"  <{el['tag']} role={el['role']!r} href={el['href']!r}> {el['text'][:60]}")
                    except Exception as de:
                        print(f"[inbox_read] [{tab_name}] DOM dump failed: {de}")
                    try:
                        html = await page.content()
                        with open(
                            f"debug_inbox_{account_id[:8]}_{tab_name.lower()}_zero.html",
                            "w", encoding="utf-8",
                        ) as f:
                            f.write(html)
                    except Exception:
                        pass
                    return

                for i, thread in enumerate(threads):
                    thread_id = thread["thread_id"]
                    if thread_id in processed_thread_ids:
                        continue
                    processed_thread_ids.add(thread_id)
                    sender_name = thread.get("sender_name", "")
                    preview_text = thread.get("preview_text", "")

                    try:
                        print(f"[inbox_read] [{tab_name}] Thread {i + 1}/{len(threads)} "
                              f"id={thread_id} sender={sender_name!r}")

                        row_selectors = [
                            '[role="main"] [role="button"]',
                            'a[href*="/marketplace/inbox/t/"]',
                            'a[href*="/marketplace/t/"]',
                            'a[href*="/messages/t/"]',
                            'a[href*="/t/"]',
                            '[role="main"] [role="row"]',
                            '[role="main"] [role="listitem"]',
                            '[role="main"] [role="link"]',
                        ]
                        combined_sel = ", ".join(row_selectors)
                        row_idx = thread.get("row_index", i)

                        # SPA UI thread selection: click conversation row in UI instead of page.goto()
                        await _click_conversation_row(page, combined_sel, index=row_idx, expected_thread_id=thread_id)
                        await _wait_for_conversation_pane_loaded(page, thread_id=thread_id)

                        sender_name, last_msg_text = await _get_latest_incoming_message(
                            page, db, account_id, thread_id, preview_text,
                        )
                        if not sender_name:
                            sender_name = thread.get("sender_name") or "Unknown"

                        print(f"[inbox_read] [{tab_name}] Thread {i + 1} "
                              f"sender={sender_name!r} msg={last_msg_text[:80]!r}")

                        if not last_msg_text:
                            print(f"[inbox_read] [{tab_name}] Thread {i + 1} no message text — skip")
                            continue

                        if await _message_already_stored(db, account_id, thread_id, last_msg_text):
                            print(f"[inbox_read] [{tab_name}] Thread {i + 1} duplicate — skip")
                            continue

                        if not account_id:
                            raise ValueError("account_id is required to store inbox messages")

                        insert_row = {
                            "account_id": account_id,
                            "thread_id": thread_id or None,
                            "sender_name": sender_name,
                            "message_text": last_msg_text,
                            "reply_status": "pending",
                            "read_at": datetime.now(timezone.utc).isoformat(),
                        }
                        inserted = db.table("inbox_messages").insert(insert_row).execute()
                        if not inserted.data or not inserted.data[0].get("account_id"):
                            raise RuntimeError(
                                f"Failed to persist account_id for inbox message (account={account_id})"
                            )
                        messages_found += 1
                        saved_msg = inserted.data[0]
                        try:
                            await broadcast_event("NEW_MESSAGE", saved_msg)
                        except Exception as b_err:
                            print(f"[inbox_read] WebSocket broadcast error: {b_err}")
                        print(f"[inbox_read] [{tab_name}] Thread {i + 1} ✓ saved from '{sender_name}'")
                        await write_log(
                            "inbox_read_message",
                            task_id=task_id,
                            account_id=account_id,
                            details={
                                "tab": tab_name,
                                "thread_id": thread_id,
                                "sender": sender_name,
                                "preview": last_msg_text[:80],
                            },
                        )

                    except Exception as te:
                        print(f"[inbox_read] [{tab_name}] Thread {i + 1} error: {te}")
                        await write_log(
                            "inbox_read_message",
                            task_id=task_id,
                            account_id=account_id,
                            status="failed",
                            error=str(te),
                        )

                    await update_task(
                        task_id,
                        completed_steps=messages_found,
                        progress=min(int(messages_found / max(max_messages, 1) * 100), 99),
                    )
                    await asyncio.sleep(random.uniform(1.5, 3))

                print(f"[inbox_read] [{tab_name}] done. total saved so far: {messages_found}")

            for tab_name in INBOX_TABS:
                await _scrape_tab(tab_name)

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(task_id, status="completed", finished_at=True,
            result={"messages_read": messages_found})
        print(f"[inbox_read] ===== DONE messages_found={messages_found} =====")

    run_background_task(_run(), task_id=task_id)
    return task_id


async def auto_reply_messages(
    account_id: str,
    message_ids: Optional[list[str]] = None,
    max_replies: int = 20,
    tone: str = "friendly",
    custom_instructions: str = "",
    delay_seconds: int = 15,
) -> str:
    """Generate AI replies for pending inbox messages and send them."""
    task_id = await create_task(
        "inbox_auto_reply",
        {"account_id": account_id, "max_replies": max_replies, "tone": tone},
    )
    await update_task(task_id, status="running", total_steps=max_replies, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        db = get_supabase()

        if message_ids:
            result = db.table("inbox_messages").select("*").in_("id", message_ids).execute()
        else:
            result = (
                db.table("inbox_messages")
                .select("*")
                .eq("account_id", account_id)
                .eq("reply_status", "pending")
                .limit(max_replies)
                .execute()
            )

        pending = result.data[:max_replies]
        await update_task(task_id, total_steps=len(pending))

        async with _inbox_browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _ensure_logged_in(
                session, account, account_id, db, "inbox_auto_reply",
            )
            if not logged_in:
                await update_task(task_id, status="failed",
                    error="Login failed. Please verify this account.", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            # Save cookies after successful login (in case credential login was used)
            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            replied = 0
            for i, msg in enumerate(pending):
                try:
                    reply_text = await _ai_service.generate_reply(
                        original_message=msg["message_text"],
                        sender_name=msg.get("sender_name", ""),
                        tone=tone,
                        custom_instructions=custom_instructions,
                        session_id=f"{task_id}_reply_{i}",
                    )

                    sent = await _send_reply_via_browser(
                        session, account_id, msg, reply_text
                    )

                    status = "sent" if sent else "failed"
                    updated = db.table("inbox_messages").update(
                        {
                            "reply_text": reply_text,
                            "reply_status": status,
                            "replied_at": datetime.now(timezone.utc).isoformat() if sent else None,
                        }
                    ).eq("id", msg["id"]).execute()
                    if updated.data:
                        try:
                            await broadcast_event("MESSAGE_UPDATED", updated.data[0])
                        except Exception as b_err:
                            print(f"[inbox_auto_reply] WebSocket broadcast error: {b_err}")

                    if sent:
                        replied += 1

                    await write_log(
                        "inbox_auto_reply",
                        task_id=task_id,
                        account_id=account_id,
                        status=status if sent else "failed",
                        details={
                            "message_id": msg["id"],
                            "sender": msg.get("sender_name"),
                            "reply_preview": reply_text[:100],
                        },
                    )
                except Exception as e:
                    await write_log(
                        "inbox_auto_reply",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / len(pending) * 100) if pending else 100,
                )
                await asyncio.sleep(delay_seconds + random.uniform(-3, 5))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"replied": replied, "total": len(pending)},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def _send_reply_via_browser(
    session: BrowserSession,
    account_id: str,
    message: dict,
    reply_text: str,
) -> bool:
    """Navigate to the conversation thread and send the reply text."""
    page = session.page
    try:
        thread_id = message.get("thread_id")
        opened = False

        if thread_id and thread_id.isdigit() and len(thread_id) > 8:
            try:
                await page.goto(_thread_url(thread_id), timeout=15000)
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                await session.human_delay(1500, 3000)
                opened = "marketplace" in page.url or "messages" in page.url
            except Exception as goto_err:
                print(f"[inbox_auto_reply] Direct URL navigation failed ({goto_err}), falling back to UI search...")
                opened = False

        if not opened:
            if not await _goto_marketplace_inbox(page):
                return False
            await session.human_delay(1000, 2000)

            sender_name = message.get("sender_name", "")
            for tab_name in INBOX_TABS:
                await _click_inbox_tab(page, tab_name)
                await session.human_delay(800, 1500)

                if sender_name:
                    search_box = page.locator(
                        'input[placeholder*="Search" i], input[aria-label*="Search" i]'
                    )
                    if await search_box.count() > 0:
                        await search_box.first.fill(sender_name)
                        await session.human_delay(1000, 2000)

                    thread_item = page.locator(
                        f'{THREAD_LINK_SELECTOR}, '
                        f'[role="listitem"]:has-text("{sender_name}")'
                    )
                    if await thread_item.count() > 0:
                        await thread_item.first.click()
                        await session.human_delay(1500, 3000)
                        opened = True
                        break

                threads = await _collect_marketplace_threads(page, 50)
                for thread in threads:
                    if sender_name and thread.get("sender_name") == sender_name:
                        await page.goto(_thread_url(thread["thread_id"]), timeout=20000)
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                        await session.human_delay(1500, 3000)
                        opened = True
                        break
                if opened:
                    break

        if not opened:
            return False

        reply_input = page.locator(
            'div[contenteditable="true"][data-lexical-editor="true"], '
            'div[contenteditable="true"][role="textbox"], '
            'div[role="textbox"], '
            'textarea[placeholder*="Message" i], '
            'input[placeholder*="Message" i]'
        )

        if await reply_input.count() == 0:
            return False

        await reply_input.first.click()
        await session.human_delay(300, 800)
        await reply_input.first.fill(reply_text)

        await session.human_delay(500, 1000)

        send_btn = page.locator(
            'div[role="button"][aria-label*="Send" i], '
            'button[aria-label*="Send" i], '
            'div[role="button"]:has-text("Send")'
        )
        if await send_btn.count() > 0:
            await send_btn.first.click()
        else:
            await page.keyboard.press("Enter")

        await session.human_delay(1500, 2500)

        # Verify the reply appears in the thread
        bubbles = await _extract_message_bubbles(page)
        return reply_text.strip() in bubbles
    except Exception as e:
        print(f"[inbox_auto_reply] Send failed: {e}")
        return False


async def send_manual_reply(message_id: str, reply_text: str) -> dict:
    """Store a manual reply and attempt to send it via browser."""
    db = get_supabase()
    result = (
        db.table("inbox_messages")
        .select("*")
        .eq("id", message_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise ValueError("Message not found")

    msg = result.data[0]
    account = _get_account(msg["account_id"])

    async with _inbox_browser_manager.new_session(
        proxy=account.get("proxy"),
        cookies_json=account.get("cookies"),
    ) as session:
        db = get_supabase()
        logged_in = await _ensure_logged_in(
            session, account, msg["account_id"], db, "inbox_manual_reply",
        )
        if not logged_in:
            raise ValueError("Login failed")

        cookies = await session.save_cookies()
        _save_cookies(msg["account_id"], cookies)

        sent = await _send_reply_via_browser(session, msg["account_id"], msg, reply_text)

    status = "sent" if sent else "failed"
    updated = db.table("inbox_messages").update(
        {
            "reply_text": reply_text,
            "reply_status": status,
            "replied_at": datetime.now(timezone.utc).isoformat() if sent else None,
        }
    ).eq("id", message_id).execute()
    if updated.data:
        try:
            await broadcast_event("MESSAGE_UPDATED", updated.data[0])
        except Exception as b_err:
            print(f"[send_manual_reply] WebSocket broadcast error: {b_err}")

    return {"message_id": message_id, "reply_status": status}
