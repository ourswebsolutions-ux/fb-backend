"""
Facebook automation service.
Each method maps to one of the 15 API features.
Browser interaction uses Playwright via BrowserManager.
"""

import asyncio
import json
import os
import random
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
import re

# Debug screenshots — only save if DEBUG_SCREENSHOTS=true in environment
_DEBUG_SCREENSHOTS = os.getenv("DEBUG_SCREENSHOTS", "false").lower() == "true"

async def _screenshot(page, path: str):
    """Save screenshot only when debug mode is enabled."""
    if not _DEBUG_SCREENSHOTS:
        return
    try:
        await _screenshot(page, path)
    except Exception:
        pass


from app.core.browser import BrowserManager, BrowserSession, do_login
from app.core.database import get_supabase
from app.core.ai import AIService
from app.task_runner import create_task, update_task, write_log, run_background_task

MARKETPLACE_CREATE = "https://www.facebook.com/marketplace/create/item"
MARKETPLACE_LISTINGS = "https://www.facebook.com/marketplace/you/selling"

UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_browser_manager = BrowserManager(headless=True)
_ai_service = AIService()


# ------------------------------------------------------------------ helpers --

def _get_account(account_id: str) -> dict:
    db = get_supabase()
    result = db.table("fb_accounts").select("*").eq("id", account_id).limit(1).execute()
    if not result.data:
        raise ValueError(f"Account {account_id} not found")
    return result.data[0]


def _set_account_status(account_id: str, status: str):
    db = get_supabase()
    db.table("fb_accounts").update({"status": status}).eq("id", account_id).execute()


def _touch_account(account_id: str):
    from datetime import datetime, timezone
    db = get_supabase()
    db.table("fb_accounts").update(
        {"last_used_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", account_id).execute()


def _save_cookies(account_id: str, cookies_json: str):
    db = get_supabase()
    db.table("fb_accounts").update({"cookies": cookies_json}).eq("id", account_id).execute()

def _resolve_url(base_url: str, href: str) -> str:
    if not href:
        return href
    return urljoin(base_url, href)

def _extract_fb_listing_id(url: str) -> Optional[str]:
    if not url:
        return None
    patterns = [
        r"/marketplace/item/(\d+)(?:[/?]|$)",
        r"/item/(\d+)(?:[/?]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

async def _find_published_listing_url(session: BrowserSession) -> Optional[str]:
    page = session.page
    link_selectors = [
        'a:has-text("View listing")',
        'a:has-text("See your listing")',
        'a:has-text("Go to listing")',
        'a[href*="/marketplace/item/"]',
        'a[href*="/item/"]',
    ]

    for sel in link_selectors:
        try:
            locator = page.locator(sel)
            count = await locator.count()
            for index in range(count):
                item = locator.nth(index)
                if await item.is_visible():
                    href = await item.get_attribute("href")
                    if href:
                        resolved = _resolve_url(page.url, href)
                        print(f"[publish] Found candidate listing URL via '{sel}': {resolved}")
                        return resolved
        except Exception:
            continue

    button_selectors = [
        'button:has-text("View listing")',
        'button:has-text("See your listing")',
        'button:has-text("Go to listing")',
    ]
    for sel in button_selectors:
        try:
            button = page.locator(sel).first
            if await button.count() > 0 and await button.is_visible():
                try:
                    await button.click(force=True, timeout=12000)
                except Exception:
                    handle = await button.element_handle()
                    if handle:
                        await page.evaluate("(el) => el.click()", handle)
                await page.wait_for_load_state("domcontentloaded", timeout=60000)
                await asyncio.sleep(2)
                print(f"[publish] Clicked '{sel}' to reveal listing URL")
                return page.url
        except Exception:
            continue

    return None

def _is_create_item_url(url: str) -> bool:
    if not url:
        return False
    return (
        "/marketplace/create/item" in url
        or "/create/item" in url
        or "/create" in url and "/marketplace" in url
    )

async def _open_listing_from_your_listings(session: BrowserSession) -> Optional[str]:
    page = session.page
    print("[publish] Waiting for redirect to Your Listings...")

    heading = page.locator(
        'h1:has-text("Your Listings"), '
        'h2:has-text("Your Listings"), '
        'h3:has-text("Your Listings"), '
        '[role="heading"]:has-text("Your Listings")'
    ).first

    try:
        await heading.wait_for(state="visible", timeout=20000)
    except Exception as err:
        print(f"[publish] 'Your Listings' section not found: {err}")
        return None

    print("[publish] Found 'Your Listings' section.")

    listing_href = None
    try:
        handle = await heading.element_handle()
        if handle:
            listing_href = await page.evaluate("""(heading) => {
                const testVisibility = (el) => {
                    const style = window.getComputedStyle(el);
                    return el.offsetWidth > 0 && el.offsetHeight > 0 && style.visibility !== 'hidden';
                };

                const links = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"], a[href*="/item/"]'));
                for (const link of links) {
                    if (!(heading.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING)) {
                        continue;
                    }
                    if (!testVisibility(link)) {
                        continue;
                    }
                    const section = heading.closest('section, div[role="main"], div[role="region"], div[role="presentation"], div');
                    if (section && section.contains(link)) {
                        return link.href;
                    }
                }
                return null;
            }""", handle)
    except Exception as err:
        print(f"[publish] Error locating first listing card: {err}")

    if not listing_href:
        print("[publish] No listing card link found under 'Your Listings'.")
        return None

    print("[publish] Found first listing under 'Your Listings'.")
    print(f"[publish] Opening listing card: {listing_href}")

    try:
        card = page.locator(f'a[href="{listing_href}"]').first
        if await card.count() > 0 and await card.is_visible():
            await card.click(force=True, timeout=12000)
        else:
            await page.goto(listing_href, timeout=20000)
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
    except Exception as click_err:
        print(f"[publish] Failed to click listing card directly: {click_err}")
        try:
            await page.goto(listing_href, timeout=20000)
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
        except Exception:
            return None

    current = page.url
    if _extract_fb_listing_id(current):
        print("[publish] Listing page opened")
        print(f"[publish] Current URL: {current}")
        return current

    dialog = page.locator('dialog, div[role="dialog"], div[role="presentation"]').first
    if await dialog.count() > 0 and await dialog.is_visible():
        print("[publish] Dialog opened.")
        dialog_actions = [
            'a:has-text("View listing")',
            'a:has-text("See your listing")',
            'a:has-text("Go to listing")',
            'a[href*="/marketplace/item/"]',
            'button:has-text("View listing")',
            'button:has-text("See your listing")',
            'button:has-text("Go to listing")',
        ]
        for sel in dialog_actions:
            try:
                action = dialog.locator(sel).first
                if await action.count() > 0 and await action.is_visible():
                    print(f"[publish] Opening listing page via dialog selector '{sel}'")
                    await action.click(force=True, timeout=12000)
                    await page.wait_for_load_state("domcontentloaded", timeout=20000)
                    await asyncio.sleep(2)
                    current = page.url
                    if _extract_fb_listing_id(current):
                        print("[publish] Listing page opened")
                        print(f"[publish] Current URL: {current}")
                        return current
            except Exception:
                continue

    print("[publish] Dialog selector not found or listing page did not open.")
    return None


async def _open_newly_published_listing(session: BrowserSession) -> Optional[str]:
    page = session.page
    print("[publish] Opening newly created listing...")

    strategies = [
        ('a:has-text("View listing")', False),
        ('a:has-text("See your listing")', False),
        ('a:has-text("Go to listing")', False),
        ('button:has-text("View listing")', True),
        ('button:has-text("See your listing")', True),
        ('button:has-text("Go to listing")', True),
    ]

    for sel, is_button in strategies:
        try:
            locator = page.locator(sel)
            count = await locator.count()
            if count == 0:
                continue
            for index in range(count):
                item = locator.nth(index)
                if not await item.is_visible():
                    continue

                if is_button:
                    try:
                        await item.click(force=True, timeout=12000)
                    except Exception:
                        handle = await item.element_handle()
                        if handle:
                            await page.evaluate("(el) => el.click()", handle)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)
                    print(f"[publish] Clicked '{sel}' to open listing")
                else:
                    href = await item.get_attribute("href")
                    if not href:
                        continue
                    resolved = _resolve_url(page.url, href)
                    print(f"[publish] Opening listing URL via '{sel}': {resolved}")
                    await page.goto(resolved, timeout=20000)
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)

                current = page.url
                if _extract_fb_listing_id(current):
                    print("[publish] Listing page opened")
                    print(f"[publish] Current URL: {current}")
                    return current
        except Exception:
            continue

    print("[publish] Trying the first visible listing card link...")
    try:
        card = page.locator('a[href*="/marketplace/item/"]:visible').first
        if await card.count() > 0 and await card.is_visible():
            href = await card.get_attribute("href")
            if href:
                resolved = _resolve_url(page.url, href)
                print(f"[publish] Opening listing card URL: {resolved}")
                await page.goto(resolved, timeout=20000)
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
                current = page.url
                if _extract_fb_listing_id(current):
                    print("[publish] Listing page opened")
                    print(f"[publish] Current URL: {current}")
                    return current
    except Exception:
        pass

    print("[publish] Falling back to Marketplace Selling page to locate the newest listing...")
    try:
        await page.goto(MARKETPLACE_LISTINGS, timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await asyncio.sleep(2)
        print("[publish] Opened Marketplace Selling page for fallback")
    except Exception as exc:
        print(f"[publish] Failed to open Selling page fallback: {exc}")
        return None

    card_selectors = [
        'a[href*="/marketplace/item/"]:visible',
        'div[role="article"] a[href*="/marketplace/item/"]',
        'a[href*="/item/"]:visible',
    ]
    for sel in card_selectors:
        try:
            card = page.locator(sel).first
            if await card.count() > 0 and await card.is_visible():
                href = await card.get_attribute("href")
                if not href:
                    continue
                resolved = _resolve_url(page.url, href)
                print(f"[publish] Opening fallback listing card URL via '{sel}': {resolved}")
                await page.goto(resolved, timeout=20000)
                await page.wait_for_load_state("domcontentloaded", timeout=60000)
                await asyncio.sleep(2)
                current = page.url
                if _extract_fb_listing_id(current):
                    print("[publish] Listing page opened")
                    print(f"[publish] Current URL: {current}")
                    return current
        except Exception:
            continue

    return None


async def _find_listing_by_title_after_publish(session: BrowserSession, title: str) -> Optional[str]:
    """
    After Publish redirect, wait for the user's listings page to finish loading,
    search inside the "Your Listings" section using the exact saved title,
    click the matched listing, and return the final listing page URL.
    """
    page = session.page

    # If we're already on an item URL, return it immediately.
    current = page.url
    if _extract_fb_listing_id(current):
        print(f"[publish] Already on listing URL: {current}")
        return current

    print('[publish] Waiting for post-publish redirect and page stability...')
    try:
        await page.wait_for_load_state('networkidle', timeout=60000)
    except Exception:
        pass

    heading_selectors = [
        'h1:has-text("All Listings")', 'h2:has-text("All Listings")', 'h3:has-text("All Listings")',
        '[role="heading"]:has-text("All Listings")',
        'h1:has-text("Your Listings")', 'h2:has-text("Your Listings")', 'h3:has-text("Your Listings")',
        '[role="heading"]:has-text("Your Listings")',
    ]

    heading = None
    for sel in heading_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                heading = loc
                break
        except Exception:
            continue

    if heading is None:
        print('[publish] Listings section heading not found')
        return None

    print('[publish] Found listings section heading')

    section = None
    section_selectors = [
        'section',
        'div[role="main"]',
        'div[role="region"]',
        'div[role="presentation"]',
        'div',
    ]
    for sel in section_selectors:
        try:
            sec = page.locator(sel, has=heading).first
            if await sec.count() > 0:
                section = sec
                break
        except Exception:
            continue

    if section is None:
        print('[publish] Unable to determine listings section container')
        return None

    print('[publish] Waiting for search box inside listings section...')
    search_input = section.locator(
        'input[type="search"], '
        'input[aria-label*="Search" i], '
        'input[placeholder*="Search" i], '
        'input[role="searchbox"]'
    ).first

    try:
        await search_input.wait_for(state='visible', timeout=60000)
        print('[publish] Search box found inside listings section')
    except Exception:
        print('[publish] Search box not found inside listings section')
        return None

    print(f'[publish] Searching listing title: "{title}"')
    try:
        await search_input.fill('')
        await search_input.fill(title)
    except Exception as err:
        print(f'[publish] Failed to fill search box: {err}')
        return None

    try:
        await page.wait_for_load_state('networkidle', timeout=60000)
    except Exception:
        pass

    print('[publish] Waiting for filtered listing results...')
    exact_link = section.locator(f'a:has-text("{title}")').first
    try:
        await exact_link.wait_for(state='visible', timeout=60000)
    except Exception:
        print('[publish] Exact title listing not visible in filtered results')
        return None

    print('[publish] Listing found. Opening listing...')
    try:
        await exact_link.click(force=True, timeout=10000)
    except Exception:
        try:
            handle = await exact_link.element_handle()
            if handle:
                await page.evaluate('(el) => el.click()', handle)
        except Exception as err:
            print(f'[publish] Failed to click listing link: {err}')
            return None

    try:
        await page.wait_for_url(re.compile(r'.*/marketplace/item/\d+(?:[/?]|$)|.*/item/\d+(?:[/?]|$)'), timeout=60000)
        print(f'[publish] Listing page opened: {page.url}')
        return page.url
    except Exception:
        pass

    print('[publish] Listing click did not open item URL; checking dialog actions...')
    dialog = page.locator('dialog, div[role="dialog"], div[aria-modal="true"]').first
    if await dialog.count() > 0 and await dialog.is_visible():
        actions = [
            'a:has-text("View listing")', 'a:has-text("See your listing")', 'a:has-text("Go to listing")',
            'button:has-text("View listing")', 'button:has-text("See your listing")',
        ]
        for sel in actions:
            try:
                act = dialog.locator(sel).first
                if await act.count() > 0 and await act.is_visible():
                    print(f'[publish] Clicking dialog action "{sel}"')
                    try:
                        await act.click(force=True, timeout=10000)
                    except Exception:
                        handle = await act.element_handle()
                        if handle:
                            await page.evaluate('(el) => el.click()', handle)
                    try:
                        await page.wait_for_url(re.compile(r'.*/marketplace/item/\d+(?:[/?]|$)|.*/item/\d+(?:[/?]|$)'), timeout=60000)
                        print(f'[publish] Listing page opened: {page.url}')
                        return page.url
                    except Exception:
                        continue
            except Exception:
                continue

    print('[publish] Unable to open listing page from filtered title result')
    return None

def _normalize_category(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    cat = str(category).strip()
    if not cat:
        return None

    normalized = cat.replace("_", " ").strip().lower()
    if normalized == cat.lower():
        return cat

    parts = [w if w == "and" else w.capitalize() for w in normalized.split()]
    return " ".join(parts)


async def _resolve_images(images: list[str]) -> list[str]:
    """
    Accept image paths OR public URLs.
    - If URL (http/https): download to local uploads/ dir and return local path.
    - If local path: verify it exists.
    Returns list of local file paths ready for Playwright set_input_files().
    """
    import httpx
    if not images:
        raise ValueError("No images provided. Please upload at least one product image.")

    resolved = []
    for img in images:
        if img.startswith("http://") or img.startswith("https://"):
            # Download URL to local uploads dir
            filename = img.split("/")[-1].split("?")[0]
            dest = UPLOAD_DIR / filename
            if not dest.exists():
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        r = await client.get(img)
                        r.raise_for_status()
                        dest.write_bytes(r.content)
                    print(f"[resolve_images] Downloaded {img} → {dest}")
                except Exception as e:
                    raise ValueError(f"Failed to download image {img}: {e}")
            resolved.append(str(dest))
        else:
            if not os.path.isfile(img):
                raise ValueError(
                    f"Image file not found on server: {img}. Please re-upload your images."
                )
            resolved.append(img)
    return resolved


def _validate_images(images: list[str]) -> list[str]:
    """
    Verify every image path exists on disk.
    Returns the validated list or raises ValueError with a clear message.
    """
    if not images:
        raise ValueError("No images provided. Please upload at least one product image.")
    missing = [p for p in images if not os.path.isfile(p)]
    if missing:
        raise ValueError(
            f"Image file(s) not found on server: {', '.join(missing)}. "
            "Please re-upload your images."
        )
    return images


def _upsert_listing(data: dict) -> dict:
    db = get_supabase()
    result = db.table("listings").insert(data).execute()
    return result.data[0]


def _update_listing(listing_id: str, patch: dict):
    db = get_supabase()
    result = db.table("listings").update(patch).eq("id", listing_id).execute()
    if getattr(result, "error", None):
        raise RuntimeError(f"Supabase update error for listing {listing_id}: {result.error}")
    return result


async def _do_login(session: BrowserSession, account: dict) -> bool:
    """Delegate to the centralized do_login in browser.py."""
    return await do_login(session, account, _browser_manager)


async def _fill_listing_form(session: BrowserSession, listing: dict) -> bool:
    """
    Fill out the FB Marketplace create-item form including image upload.

    Steps:
    1. Navigate to the create-item page and confirm it loaded.
    2. Handle account/page selection if prompted.
    3. Upload all images — MUST succeed (Photos count > 0) before proceeding.
    4. Fill title, price, category, description.
    5. Take a screenshot on every failure point for debugging.

    Returns True if all required fields were filled, raises RuntimeError otherwise.
    """
    page = session.page
    listing_id_short = listing.get("id", "unknown")[:8]

    print(f"[listing_form] ── START listing={listing_id_short} ──────────────────")
    print(f"[listing_form] Navigating to {MARKETPLACE_CREATE}")
    await page.goto(MARKETPLACE_CREATE, timeout=105000)
    await page.wait_for_load_state("domcontentloaded", timeout=60000)
    await asyncio.sleep(3)  # let React fully render the form

    current_url = page.url
    print(f"[listing_form] Step 1 | Landed on: {current_url}")

    if "login" in current_url or "checkpoint" in current_url:
        raise RuntimeError(f"Redirected to {current_url} — session expired before form")

    # ── Step 1.5: Handle account/page selection if prompted ─────────────────────
    # Facebook sometimes prompts to select which account/page to use for Marketplace
    print(f"[listing_form] Step 1.5 | Checking for account/page selection prompt...")
    
    account_selectors = [
        'div[role="dialog"]:has-text("Choose account")',
        'div[role="dialog"]:has-text("Select account")',
        'div[role="dialog"]:has-text("Choose page")',
        'div[role="dialog"]:has-text("Select page")',
        '[aria-label*="Choose account" i]',
        '[aria-label*="Select account" i]',
        'div:has-text("Continue as")',
    ]
    
    account_dialog_found = False
    for selector in account_selectors:
        try:
            dialog = page.locator(selector).first
            if await dialog.count() > 0 and await dialog.is_visible():
                print(f"[listing_form] Step 1.5 | Account selection dialog found: {selector}")
                account_dialog_found = True
                
                # Try to find and click the first account option
                account_option = page.locator('div[role="radiogroup"] > div, div[role="listitem"], [role="option"]').first
                if await account_option.count() > 0:
                    print(f"[listing_form] Step 1.5 | Clicking first account option...")
                    await account_option.click()
                    await asyncio.sleep(1)
                    
                    # Click continue button
                    continue_btn = page.locator('div[role="button"]:has-text("Continue"), button:has-text("Continue")').first
                    if await continue_btn.count() > 0:
                        print(f"[listing_form] Step 1.5 | Clicking Continue button...")
                        await continue_btn.click()
                        await asyncio.sleep(2)
                    break
        except Exception as e:
            print(f"[listing_form] Step 1.5 | Selector {selector} failed: {e}")
    
    if account_dialog_found:
        print(f"[listing_form] Step 1.5 | Account selection handled, re-checking URL...")
        await asyncio.sleep(2)
        current_url = page.url
        print(f"[listing_form] Step 1.5 | URL after account selection: {current_url}")
        
        # If still not on create page, navigate again
        if "create" not in current_url:
            print(f"[listing_form] Step 1.5 | Not on create page, navigating again...")
            await page.goto(MARKETPLACE_CREATE, timeout=105000)
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            await asyncio.sleep(3)
            current_url = page.url
            print(f"[listing_form] Step 1.5 | URL after re-navigation: {current_url}")
    else:
        print(f"[listing_form] Step 1.5 | No account selection dialog found")

    # Screenshot of fresh form
    try:
        await _screenshot(page, f"debug_form_{listing_id_short}_loaded.png")
        print(f"[listing_form] Step 1 | Screenshot saved: debug_form_{listing_id_short}_loaded.png")
    except Exception as e:
        print(f"[listing_form] Step 1 | Screenshot failed: {e}")

    # ── Step 2: Image upload ──────────────────────────────────────────────────
    images: list[str] = listing.get("images") or []

    # ── 2a. Resolve URLs to local paths ──────────────────────────────────────
    import httpx
    resolved_images = []
    for img in images:
        if img.startswith("http://") or img.startswith("https://"):
            filename = img.split("/")[-1].split("?")[0]
            dest = UPLOAD_DIR / filename
            if not dest.exists():
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        r = await client.get(img)
                        r.raise_for_status()
                        dest.write_bytes(r.content)
                    print(f"[listing_form] Downloaded {img} → {dest}")
                except Exception as e:
                    raise RuntimeError(f"Failed to download image {img}: {e}")
            resolved_images.append(str(dest))
        else:
            resolved_images.append(img)
    images = resolved_images

    # ── 2a. Print & verify every image path ──────────────────────────────────
    print(f"[listing_form] Step 2 | Image paths received ({len(images)}):")
    for idx, p in enumerate(images):
        exists = os.path.exists(p)
        is_file = os.path.isfile(p)
        size = os.path.getsize(p) if is_file else 0
        print(f"[listing_form] Step 2 |   [{idx}] path={p!r}  exists={exists}  isfile={is_file}  size={size}B")

    if not images:
        raise RuntimeError(
            "No images provided for this listing. "
            "Upload at least one image before starting."
        )

    missing = [p for p in images if not p.startswith('http') and not os.path.isfile(p)]
    if missing:
        raise RuntimeError(
            f"Image file(s) not found on disk: {missing}. "
            "Re-upload images before starting."
        )

    # ── 2b. Wait for the file input to appear in the DOM ─────────────────────
    # FB renders a hidden <input type="file"> — we must wait for it before use.
    file_input_sel = 'input[type="file"]'
    print(f"[listing_form] Step 2 | Waiting for file input: {file_input_sel!r}")
    try:
        await page.wait_for_selector(file_input_sel, state="attached", timeout=60000)
        input_count = await page.locator(file_input_sel).count()
        print(f"[listing_form] Step 2 | Found {input_count} file input(s) in DOM")
    except Exception as e:
        await _screenshot(page, f"debug_form_{listing_id_short}_no_file_input.png")
        raise RuntimeError(
            f"No <input type='file'> found on page after 15 s: {e}. "
            f"Screenshot: debug_form_{listing_id_short}_no_file_input.png"
        )

    # ── 2c. Try every known strategy to trigger the file chooser ─────────────
    # Strategy A: click the visible "Add photos" button → intercept file chooser
    photo_btn_selectors = [
        'div[aria-label="Add photos"]',
        'div[aria-label="Add photo"]',
        'div[aria-label*="photo" i]',
        'div[aria-label*="image" i]',
        'button:has-text("Add photos")',
        'div[role="button"]:has-text("Add photos")',
        '[data-testid="media-upload-button"]',
    ]

    file_chooser = None
    used_selector = None
    for sel in photo_btn_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() == 0:
                print(f"[listing_form] Step 2 | Selector not found: {sel!r}")
                continue
            is_vis = await el.is_visible()
            print(f"[listing_form] Step 2 | Trying photo trigger: {sel!r}  visible={is_vis}")
            async with page.expect_file_chooser(timeout=7000) as fc_info:
                try:
                    await el.click(force=True)
                except Exception:
                    handle = await el.element_handle()
                    if handle:
                        await page.evaluate("(e) => e.click()", handle)
            file_chooser = await fc_info.value
            used_selector = sel
            print(f"[listing_form] Step 2 | ✓ File chooser opened via: {sel!r}")
            break
        except Exception as e:
            print(f"[listing_form] Step 2 | Selector {sel!r} failed: {e}")

    if file_chooser is not None:
        # Strategy A succeeded
        await file_chooser.set_files(images)
        print(f"[listing_form] Step 2 | File chooser set with {len(images)} file(s) via {used_selector!r}")
    else:
        # Strategy B: set_input_files directly on every hidden input
        print(f"[listing_form] Step 2 | File chooser strategy failed — trying direct set_input_files")
        all_inputs = page.locator('input[type="file"]')
        n = await all_inputs.count()
        print(f"[listing_form] Step 2 | Found {n} file input(s) for direct injection")
        uploaded = False
        for i in range(n):
            inp = all_inputs.nth(i)
            try:
                # Make input interactable even if hidden
                await page.evaluate(
                    """(el) => {
                        el.style.display = 'block';
                        el.style.visibility = 'visible';
                        el.style.opacity = '1';
                        el.removeAttribute('tabindex');
                    }""",
                    await inp.element_handle(),
                )
                await inp.set_input_files(images)
                print(f"[listing_form] Step 2 | set_input_files on input[{i}] ✓")
                uploaded = True
                used_selector = f'input[type="file"][{i}]'
                break
            except Exception as e:
                print(f"[listing_form] Step 2 | set_input_files on input[{i}] failed: {e}")

        if not uploaded:
            await _screenshot(page, f"debug_form_{listing_id_short}_no_upload.png")
            raise RuntimeError(
                f"All image upload strategies failed. "
                f"Screenshot: debug_form_{listing_id_short}_no_upload.png"
            )

    # ── 2d. Verify Photos count changed from 0 → ≥ 1 ─────────────────────────
    # Wait up to 30 s for FB to accept the file(s) and update the UI counter.
    print(f"[listing_form] Step 2 | Waiting for Photos count to increase...")
    photos_confirmed = False

    # Selectors that indicate at least one photo was accepted by FB
    confirmed_selectors = [
        'img[src^="blob:"]',                          # blob preview thumbnail
        'img[src*="scontent"]',                       # CDN-uploaded thumbnail
        'div[style*="background-image"]',             # CSS background thumbnail
        'span:has-text("1 photo")',
        'span:has-text("photo")',
        '[aria-label*="photo" i][aria-label*="1" i]',
        'div[data-visualcompletion="media-vc-image"]',
    ]

    for attempt in range(20):                        # up to 20 × 1.5 s = 30 s
        await asyncio.sleep(1.5)
        for t_sel in confirmed_selectors:
            try:
                count = await page.locator(t_sel).count()
                if count > 0:
                    photos_confirmed = True
                    print(
                        f"[listing_form] Step 2 | ✓ Photos confirmed ({count} element(s)) "
                        f"via '{t_sel}' after {(attempt+1)*1.5:.1f}s"
                    )
                    break
            except Exception:
                pass
        if photos_confirmed:
            break

    # Screenshot after upload attempt regardless of outcome
    try:
        shot_name = f"debug_form_{listing_id_short}_after_upload.png"
        await _screenshot(page, shot_name)
        print(f"[listing_form] Step 2 | Post-upload screenshot: {shot_name}")
    except Exception:
        pass

    if not photos_confirmed:
        raise RuntimeError(
            "Image upload failed. "
            "Photos count remained 0 after upload attempt. "
            f"Check debug_form_{listing_id_short}_after_upload.png for the current page state. "
            f"Selector used: {used_selector!r}. "
            f"Images attempted: {images}"
        )

    print(f"[listing_form] Step 2 | ✓ Image upload verified — continuing to form fields")

    await session.human_delay(1000, 1500)

    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER: find the first visible element from a list of locators
    # ═══════════════════════════════════════════════════════════════════════════
    async def _find_visible(locators: list, label: str):
        """Try each locator in order; return (element, description) for first visible one."""
        for loc, desc in locators:
            try:
                el = loc.first
                count = await el.count()
                if count == 0:
                    print(f"[form] {label} | not found: {desc}")
                    continue
                vis = await el.is_visible()
                print(f"[form] {label} | found ({count}) visible={vis}: {desc}")
                if vis:
                    return el, desc
            except Exception as exc:
                print(f"[form] {label} | error on {desc}: {exc}")
        return None, None

    # ═══════════════════════════════════════════════════════════════════════════
    # DUMP: print every input/textarea/contenteditable + div[role=combobox]
    # ═══════════════════════════════════════════════════════════════════════════
    async def _dump_fields():
        try:
            info = await page.evaluate("""() => {
                const els = document.querySelectorAll(
                    'input, textarea, [contenteditable="true"], [role="combobox"], [role="spinbutton"]'
                );
                return Array.from(els).slice(0, 60).map(el => ({
                    tag: el.tagName,
                    type: el.getAttribute('type') || '',
                    role: el.getAttribute('role') || '',
                    name: el.getAttribute('name') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    ariaPlaceholder: el.getAttribute('aria-placeholder') || '',
                    ce: el.getAttribute('contenteditable') || '',
                    visible: !!(el.offsetWidth || el.offsetHeight),
                    id: el.id || '',
                    text: (el.innerText || el.value || '').slice(0, 60),
                }));
            }""")
            print(f"[form] DOM dump — {len(info)} editable element(s):")
            for item in info:
                if item.get('visible'):
                    print(f"[form]   VISIBLE  {item}")
                else:
                    print(f"[form]   hidden   {item}")
        except Exception as exc:
            print(f"[form] DOM dump failed: {exc}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 3 — Navigate to the details page (click "Next" after media upload)
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"[form] Step 3 | Advancing past media step to details form...")
    await _dump_fields()

    async def _details_form_visible() -> bool:
        """Return True once any known details field is visible."""
        checks = [
            page.get_by_label("Title"),
            page.get_by_placeholder("Title"),
            page.get_by_label("Price"),
            page.get_by_placeholder("Price"),
            page.locator('[aria-label="Title"]'),
            page.locator('[aria-label*="Title" i]'),
            page.locator('[aria-placeholder*="Title" i]'),
            # FB sometimes uses contenteditable divs
            page.locator('div[contenteditable="true"][aria-label*="Title" i]'),
            page.locator('div[contenteditable="true"][aria-placeholder*="Title" i]'),
        ]
        for loc in checks:
            try:
                if await loc.first.is_visible():
                    return True
            except Exception:
                pass
        return False

    for _n in range(4):
        if await _details_form_visible():
            print(f"[form] Step 3 | Details form is visible (pass {_n})")
            break
        # Look for a Next button
        next_loc = page.get_by_role("button", name="Next")
        next_div = page.locator('div[role="button"]:has-text("Next")')
        for nb in (next_loc, next_div):
            try:
                if await nb.first.is_visible():
                    print(f"[form] Step 3 | Clicking Next (pass {_n + 1})...")
                    await nb.first.click()
                    await asyncio.sleep(2.5)
                    break
            except Exception:
                pass
        else:
            print(f"[form] Step 3 | No Next button on pass {_n + 1}, waiting...")
            await asyncio.sleep(2)

    # Full dump after navigation attempt
    await _dump_fields()
    try:
        await _screenshot(page, f"debug_form_{listing_id_short}_details.png")
        print(f"[form] Step 3 | Screenshot: debug_form_{listing_id_short}_details.png")
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 4 — Title
    # FB Marketplace uses either <input> or <div contenteditable> for Title.
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"[form] Step 4 | Filling Title: '{listing['title']}'")
    title_el, title_desc = await _find_visible([
        (page.get_by_label("Title"),                               "get_by_label('Title')"),
        (page.get_by_placeholder("Title"),                         "get_by_placeholder('Title')"),
        (page.locator('[aria-label="Title"]'),                     "aria-label=Title"),
        (page.locator('[aria-label*="Title" i]'),                  "aria-label*=Title"),
        (page.locator('[aria-placeholder="Title"]'),               "aria-placeholder=Title"),
        (page.locator('[aria-placeholder*="Title" i]'),            "aria-placeholder*=Title"),
        (page.locator('div[contenteditable="true"][aria-label*="Title" i]'),  "ce-div aria-label*Title"),
        (page.locator('div[contenteditable="true"][aria-placeholder*="Title" i]'), "ce-div aria-ph*Title"),
        (page.locator('input[name="title"]'),                      "name=title"),
        (page.locator('input[type="text"]').nth(0),                "first text input fallback"),
    ], "Title")

    if title_el is None:
        await _screenshot(page, f"debug_form_{listing_id_short}_no_title.png")
        html = (await page.content())[:8000]
        print(f"[form] Step 4 | FAIL — HTML:\n{html}")
        raise RuntimeError(
            f"Title field not found. URL={page.url} "
            f"Check debug_form_{listing_id_short}_no_title.png"
        )

    await title_el.wait_for(state="visible", timeout=8000)

    # FB renders an overlay div that intercepts pointer events on the input.
    # Strategy: scroll element into view, then use JS focus+input instead of click.
    print(f"[form] Step 4 | Focusing Title via JS (bypasses overlay)...")
    try:
        handle = await title_el.element_handle()
        await page.evaluate("""(el) => {
            el.scrollIntoView({block: 'center'});
            el.focus();
            el.click();
        }""", handle)
        await asyncio.sleep(0.3)
    except Exception as fe:
        print(f"[form] Step 4 | JS focus failed ({fe}), trying force click...")
        try:
            await title_el.click(force=True)
        except Exception:
            pass

    # Clear + fill using keyboard to work with any input type
    await title_el.fill("")
    await title_el.fill(listing["title"])

    # Verify value landed
    try:
        val = await title_el.input_value()
    except Exception:
        val = await title_el.inner_text()
    print(f"[form] Step 4 | ✓ Title='{val}' via {title_desc}")

    if not val:
        # Last resort: type character by character via keyboard
        print(f"[form] Step 4 | fill() produced empty value — typing via keyboard")
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        for ch in listing["title"]:
            await page.keyboard.type(ch)
            await asyncio.sleep(0.03)
        try:
            val = await title_el.input_value()
        except Exception:
            val = listing["title"]
        print(f"[form] Step 4 | ✓ Title (keyboard) ='{val}'")

    await session.human_delay(400, 700)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 5 — Price
    # ═══════════════════════════════════════════════════════════════════════════
    price_str = str(listing["price"] // 100) if listing.get("price") else "0"
    print(f"[form] Step 5 | Filling Price: '{price_str}'")
    price_el, price_desc = await _find_visible([
        (page.get_by_label("Price"),                               "get_by_label('Price')"),
        (page.get_by_placeholder("Price"),                         "get_by_placeholder('Price')"),
        (page.locator('[aria-label="Price"]'),                     "aria-label=Price"),
        (page.locator('[aria-label*="Price" i]'),                  "aria-label*=Price"),
        (page.locator('[aria-placeholder*="Price" i]'),            "aria-placeholder*=Price"),
        (page.locator('div[contenteditable="true"][aria-label*="Price" i]'), "ce-div Price"),
        (page.locator('input[type="number"]').nth(0),              "first number input"),
        (page.locator('input[name="price"]'),                      "name=price"),
    ], "Price")

    if price_el:
        await price_el.wait_for(state="visible", timeout=5000)
        # Use JS focus to bypass overlay, same as Title
        try:
            handle = await price_el.element_handle()
            await page.evaluate("""(el) => {
                el.scrollIntoView({block: 'center'});
                el.focus();
                el.click();
            }""", handle)
            await asyncio.sleep(0.3)
        except Exception:
            try:
                await price_el.click(force=True)
            except Exception:
                pass
        await price_el.fill("")
        await price_el.fill(price_str)
        try:
            val = await price_el.input_value()
        except Exception:
            val = await price_el.inner_text()
        print(f"[form] Step 5 | ✓ Price='{val}' via {price_desc}")
    else:
        print(f"[form] Step 5 | ⚠ Price field not found — skipping")
    await session.human_delay(400, 700)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 6 — Category
    # FB renders Category as a combobox/listbox — click it then pick option.
    # ═══════════════════════════════════════════════════════════════════════════
    if listing.get("category"):
        cat = listing["category"]
        print(f"[form] Step 6 | Setting Category: '{cat}'")
        cat_el, cat_desc = await _find_visible([
            (page.get_by_label("Category"),                        "get_by_label('Category')"),
            (page.locator('[aria-label="Category"]'),              "aria-label=Category"),
            (page.locator('[aria-label*="Category" i]'),           "aria-label*=Category"),
            (page.locator('[aria-placeholder*="Category" i]'),     "aria-placeholder*=Category"),
            (page.locator('div[role="combobox"][aria-label*="Category" i]'), "combobox Category"),
            (page.locator('select[aria-label*="Category" i]'),     "select Category"),
        ], "Category")

        if cat_el:
            await cat_el.wait_for(state="visible", timeout=5000)
            tag = await cat_el.evaluate("el => el.tagName")
            role = await cat_el.get_attribute("role") or ""
            if tag == "SELECT":
                try:
                    await cat_el.select_option(label=cat, timeout=3000)
                    print(f"[form] Step 6 | ✓ Category select_option via {cat_desc}")
                except Exception as e:
                    print(f"[form] Step 6 | select_option failed: {e}")
            else:
                # Focus/click input to open Category dropdown, then try typeahead if needed
                try:
                    handle = await cat_el.element_handle()
                    await page.evaluate("(el) => { el.scrollIntoView({block:'center'}); el.focus(); el.click(); }", handle)
                except Exception:
                    await cat_el.click(force=True)
                await asyncio.sleep(1.5)

                cat_selectors = [
                    f'[role="option"]:has-text("{cat}")',
                    f'div[role="option"]:has-text("{cat}")',
                    f'span:has-text("{cat}")',
                    f'li:has-text("{cat}")',
                ]
                clicked_cat = False
                for c_sel in cat_selectors:
                    try:
                        opt_c = page.locator(c_sel).first
                        if await opt_c.is_visible():
                            try:
                                await opt_c.click(force=True, timeout=5000)
                            except Exception:
                                handle = await opt_c.element_handle()
                                if handle:
                                    await page.evaluate("(el) => el.click()", handle)
                            print(f"[form] Step 6 | ✓ Category option clicked via '{c_sel}'")
                            clicked_cat = True
                            break
                    except Exception:
                        continue

                if not clicked_cat:
                    # Type category name to trigger FB typeahead filter
                    try:
                        await cat_el.fill(cat)
                        await asyncio.sleep(1.5)
                        opt_any = page.locator('[role="option"]').first
                        if await opt_any.is_visible():
                            try:
                                await opt_any.click(force=True, timeout=5000)
                            except Exception:
                                handle = await opt_any.element_handle()
                                if handle:
                                    await page.evaluate("(el) => el.click()", handle)
                            print(f"[form] Step 6 | ✓ Category option clicked via typeahead fallback")
                            clicked_cat = True
                    except Exception as type_err:
                        print(f"[form] Step 6 | Typeahead fallback error: {type_err}")

                if not clicked_cat:
                    print(f"[form] Step 6 | ⚠ Category option '{cat}' not visible in dropdown")
        else:
            print(f"[form] Step 6 | ⚠ Category field not found — skipping")
        await session.human_delay(1500, 2000)


    # ═══════════════════════════════════════════════════════════════════════════
    # Step 7 — Condition
    # ═══════════════════════════════════════════════════════════════════════════
    if listing.get("condition"):
        condition_map = {
            "new":           "New",
            "used_like_new": "Used - Like New",
            "used_good":     "Used - Good",
            "used_fair":     "Used - Fair",
        }
        cond_label = condition_map.get(listing["condition"], listing["condition"])
        print(f"[form] Step 7 | Setting Condition: '{cond_label}'")
        cond_el, cond_desc = await _find_visible([
            (page.get_by_label("Condition"),                       "get_by_label('Condition')"),
            (page.locator('[aria-label="Condition"]'),             "aria-label=Condition"),
            (page.locator('[aria-label*="Condition" i]'),          "aria-label*=Condition"),
            (page.locator('[aria-placeholder*="Condition" i]'),    "aria-placeholder*=Condition"),
            (page.locator('div[role="combobox"][aria-label*="Condition" i]'), "combobox Condition"),
            (page.locator('select[aria-label*="Condition" i]'),    "select Condition"),
        ], "Condition")

        if cond_el:
            tag = await cond_el.evaluate("el => el.tagName")
            if tag == "SELECT":
                try:
                    await cond_el.select_option(label=cond_label, timeout=3000)
                    print(f"[form] Step 7 | ✓ Condition select_option: '{cond_label}'")
                except Exception as e:
                    print(f"[form] Step 7 | select_option failed: {e}")
            else:
                try:
                    handle = await cond_el.element_handle()
                    await page.evaluate("(el) => { el.scrollIntoView({block:'center'}); el.click(); }", handle)
                except Exception:
                    await cond_el.click(force=True)
                await asyncio.sleep(1.5)
                opt = page.locator(f'[role="option"]:has-text("{cond_label}")').first
                if await opt.is_visible():
                    try:
                        await opt.click(force=True, timeout=5000)
                    except Exception:
                        handle = await opt.element_handle()
                        if handle:
                            await page.evaluate("(el) => el.click()", handle)
                    print(f"[form] Step 7 | ✓ Condition option clicked: '{cond_label}'")
                else:
                    short = cond_label.split(" - ")[-1]
                    opt2 = page.locator(f'[role="option"]:has-text("{short}")').first
                    if await opt2.is_visible():
                        try:
                            await opt2.click(force=True, timeout=5000)
                        except Exception:
                            handle = await opt2.element_handle()
                            if handle:
                                await page.evaluate("(el) => el.click()", handle)
                        print(f"[form] Step 7 | ✓ Condition partial match: '{short}'")
                    else:
                        print(f"[form] Step 7 | ⚠ Condition option not visible — skipping")
        else:
            print(f"[form] Step 7 | ⚠ Condition field not found — skipping")
        await session.human_delay(1500, 2000)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 8 — Description
    # ═══════════════════════════════════════════════════════════════════════════
    desc = listing.get("description") or ""
    if desc:
        print(f"[form] Step 8 | Filling Description ({len(desc)} chars)")
        desc_el, desc_desc = await _find_visible([
            (page.get_by_label("Description"),                     "get_by_label('Description')"),
            (page.get_by_placeholder("Description"),               "get_by_placeholder('Description')"),
            (page.locator('[aria-label="Description"]'),           "aria-label=Description"),
            (page.locator('[aria-label*="Description" i]'),        "aria-label*=Description"),
            (page.locator('[aria-placeholder*="Description" i]'),  "aria-placeholder*=Description"),
            (page.locator('div[contenteditable="true"][aria-label*="Description" i]'), "ce-div Description"),
            (page.locator('textarea'),                             "textarea fallback"),
        ], "Description")

        if desc_el:
            await desc_el.wait_for(state="visible", timeout=5000)
            try:
                handle = await desc_el.element_handle()
                await page.evaluate("(el) => { el.scrollIntoView({block:'center'}); el.focus(); el.click(); }", handle)
                await asyncio.sleep(0.3)
            except Exception:
                await desc_el.click(force=True)
            await desc_el.fill(desc)
            print(f"[form] Step 8 | ✓ Description filled via {desc_desc}")
        else:
            print(f"[form] Step 8 | ⚠ Description field not found — skipping")
        await session.human_delay(400, 700)

    # ═══════════════════════════════════════════════════════════════════════════
    # Step 9 — Availability (optional field, skip silently if absent)
    # ═══════════════════════════════════════════════════════════════════════════
    print(f"[form] Step 9 | Checking for Availability field...")
    avail_el, avail_desc = await _find_visible([
        (page.get_by_label("Availability"),                        "get_by_label('Availability')"),
        (page.locator('[aria-label*="Availability" i]'),           "aria-label*=Availability"),
        (page.locator('div[role="combobox"][aria-label*="Availability" i]'), "combobox Availability"),
    ], "Availability")

    if avail_el:
        print(f"[form] Step 9 | Availability field found via {avail_desc} — leaving default")
    else:
        print(f"[form] Step 9 | Availability field not present — skipping")

    await session.human_delay(800, 1200)

    # Final screenshot before publish
    try:
        await _screenshot(page, f"debug_form_{listing_id_short}_ready.png")
        print(f"[form] Step 9 | Pre-publish screenshot: debug_form_{listing_id_short}_ready.png")
    except Exception:
        pass

    print(f"[form] ── DONE listing={listing_id_short} URL={page.url} ──")
    return True


async def _publish_listing(session: BrowserSession) -> None:
    """
    STEP 1-4: Click through the FB multi-step form and click Publish.
    
    DOES NOT extract the listing ID.
    ID extraction happens AFTER navigating to Your Listings and opening the listing.
    
    Workflow:
    1. Navigate through form steps (Next buttons) until Publish button appears
    2. Click Publish button ONCE
    3. Wait for publish completion (event-based)
    4. Wait for redirected page to fully load
    
    Does NOT navigate or perform any lookups.
    Does NOT extract listing ID from URL.
    
    Raises RuntimeError on failure.
    """
    page = session.page
    print(f"[publish] ── START PUBLISHING LISTING ──────────────────────────────────")
    print(f"[publish] Step 1: Checking current URL: {page.url}")

    try:
        await _screenshot(page, "debug_pre_publish.png")
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 1: Navigate through form until Publish button appears
    # ─────────────────────────────────────────────────────────────────────────────
    print("[publish] Step 1: Finding Publish button...")
    for attempt in range(8):
        current_url = page.url
        print(f"[publish] Step 1.{attempt+1}: Looking for buttons | URL={current_url}")

        # Check for errors
        error_el = page.locator(
            '[role="alert"], '
            'div[data-visualcompletion="error"], '
            'span:has-text("went wrong"), '
            'span:has-text("try again"), '
            'div:has-text("Something went wrong")'
        )
        if await error_el.count() > 0:
            err_text = (await error_el.first.inner_text()).strip()
            try:
                await _screenshot(page, "debug_publish_error.png")
            except Exception:
                pass
            raise RuntimeError(f"Facebook error during publish: {err_text}")

        # Check for buttons
        pub_role  = page.get_by_role("button", name="Publish")
        pub_div   = page.locator('div[role="button"]:has-text("Publish")')
        next_role = page.get_by_role("button", name="Next")
        next_div  = page.locator('div[role="button"]:has-text("Next")')

        pub_vis  = await pub_role.first.is_visible()  if await pub_role.count()  > 0 else False
        pub_vis  = pub_vis or (await pub_div.first.is_visible()  if await pub_div.count()  > 0 else False)
        next_vis = await next_role.first.is_visible() if await next_role.count() > 0 else False
        next_vis = next_vis or (await next_div.first.is_visible() if await next_div.count() > 0 else False)

        print(f"[publish] Step 1.{attempt+1}: Publish visible={pub_vis}, Next visible={next_vis}")

        if pub_vis:
            print(f"[publish] Step 1: ✓ Publish button found on attempt {attempt+1}")
            break

        if next_vis:
            btn = next_role if await next_role.count() > 0 else next_div
            lbl = (await btn.first.inner_text()).strip()
            print(f"[publish] Step 1.{attempt+1}: Clicking Next button: '{lbl}'")
            try:
                await btn.first.click(force=True, timeout=12000)
            except Exception:
                handle = await btn.first.element_handle()
                if handle:
                    await page.evaluate("(el) => el.click()", handle)
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
            continue

        print(f"[publish] Step 1.{attempt+1}: No button found, waiting 2 seconds...")
        await asyncio.sleep(2)

    if not pub_vis:
        try:
            await _screenshot(page, "debug_publish_no_button.png")
        except Exception:
            pass
        raise RuntimeError(
            "Publish button never appeared after 8 attempts. "
            "Check debug_publish_no_button.png"
        )

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 2: Click Publish button ONCE
    # ─────────────────────────────────────────────────────────────────────────────
    print("[publish] Step 2: Click Publish...")
    btn = pub_role if await pub_role.count() > 0 else pub_div
    try:
        await btn.first.click(force=True, timeout=12000)
    except Exception:
        handle = await btn.first.element_handle()
        if handle:
            await page.evaluate("(el) => el.click()", handle)
    print("[publish] Step 2: ✓ Publish button clicked")

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 3: Wait for publish completion (event-based)
    # ─────────────────────────────────────────────────────────────────────────────
    print("[publish] Step 3: Waiting for publish completion...")
    await asyncio.sleep(1)

    # Check for errors after click
    error_el = page.locator(
        '[role="alert"], '
        'span:has-text("went wrong"), '
        'span:has-text("try again"), '
        'div:has-text("Something went wrong")'
    )
    if await error_el.count() > 0:
        err_text = (await error_el.first.inner_text()).strip()
        try:
            await _screenshot(page, "debug_publish_post_error.png")
        except Exception:
            pass
        await _discard_failed_create(session)
        raise RuntimeError(f"Facebook error after Publish click: {err_text}")

    completed = False
    completion_method = None

    # Condition 1: Success message appears
    try:
        print("[publish] Step 3: Waiting for success message...")
        success_selector = (
            'span:has-text("Your listing is now live"), '
            'span:has-text("listing is live"), '
            'span:has-text("published"), '
            'h2:has-text("Listing published"), '
            'h1:has-text("Your Listings"), '
            'h2:has-text("Your Listings"), '
            'h1:has-text("All Listings"), '
            'h2:has-text("All Listings")'
        )
        await page.locator(success_selector).first.wait_for(state="visible", timeout=120000)
        completed = True
        completion_method = "success_message"
    except Exception:
        pass

    # Condition 2: Publish button disappears
    if not completed:
        try:
            print("[publish] Step 3: Waiting for Publish button to disappear...")
            publish_button = page.locator('button:has-text("Publish"), div[role="button"]:has-text("Publish")')
            await publish_button.first.wait_for(state="hidden", timeout=120000)
            completed = True
            completion_method = "publish_button_hidden"
        except Exception:
            pass

    # Condition 3: URL redirect away from create-item
    if not completed:
        try:
            print("[publish] Step 3: Waiting for redirect away from create-item...")
            await page.wait_for_url(
                re.compile(r'^(?!.*(/marketplace/create/item|/create)).*$'),
                timeout=120000
            )
            completed = True
            completion_method = "url_redirect"
        except Exception:
            pass

    # Condition 4: Page load stabilizes
    if not completed:
        try:
            print("[publish] Step 3: Waiting for page load to stabilize...")
            await page.wait_for_load_state("networkidle", timeout=120000)
            completed = True
            completion_method = "page_load_stable"
        except Exception:
            pass

    if not completed:
        try:
            await _screenshot(page, "debug_publish_incomplete.png")
        except Exception:
            pass
        raise RuntimeError(
            "Publish request did not complete within expected time. "
            "Check debug_publish_incomplete.png"
        )

    print(f"[publish] Step 3: ✓ Publish completed (via: {completion_method})")

    # ─────────────────────────────────────────────────────────────────────────────
    # STEP 4: Wait for page to fully load
    # ─────────────────────────────────────────────────────────────────────────────
    print("[publish] Step 4: Waiting for page to fully load...")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        print("[publish] Step 4: ✓ DOM content loaded")
    except Exception as e:
        print(f"[publish] Step 4: DOM load timeout: {e}")

    try:
        await page.wait_for_load_state("networkidle", timeout=60000)
        print("[publish] Step 4: ✓ Page fully loaded")
    except Exception:
        pass

    print(f"[publish] Step 4: Current URL: {page.url}")
    print(f"[publish] ── PUBLISH CLICK COMPLETE ──────────────────────────────────")
    # Note: ID extraction happens in next function, not here


async def _extract_listing_id_after_publish(session: BrowserSession, listing_title: str) -> dict:
    """
    STEPS 5-10: Search the Selling page for the newly published listing,
    open the top result, wait for the listing page, and extract the Listing ID.

    The Listing ID MUST come only from the listing page URL after opening it.
    """
    page = session.page
    print(f"[publish] Step 5: Preparing Selling page search")
    if MARKETPLACE_LISTINGS not in page.url:
        print(f"[publish] Step 5: Navigating to Selling page: {MARKETPLACE_LISTINGS}")
        try:
            await page.goto(MARKETPLACE_LISTINGS, timeout=90000)
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            try:
                await _screenshot(page, "debug_marketplace_selling_navigation_failed.png")
            except Exception:
                pass
            raise RuntimeError(f"Failed to open Selling page: {e}")

    print(f"[publish] Step 5: Searching listing: \"{listing_title}\"")

    search_selectors = [
        'input[type="search"]',
        'input[aria-label*="Search" i]',
        'input[placeholder*="Search" i]',
        'input[role="searchbox"]',
        'input[name*="search" i]',
    ]

    search_input = None
    for sel in search_selectors:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0 and await locator.is_visible():
                search_input = locator
                print(f"[publish] Step 5: Found search input via selector: {sel}")
                break
        except Exception:
            continue

    if not search_input:
        try:
            await _screenshot(page, "debug_marketplace_search_not_found.png")
        except Exception:
            pass
        raise RuntimeError(
            "Marketplace search input not found on Selling page. "
            "Check debug_marketplace_search_not_found.png"
        )

    try:
        await search_input.fill("")
        await search_input.fill(listing_title)
        await search_input.press("Enter")
        print(f"[publish] Step 5: Search submitted")
    except Exception as e:
        raise RuntimeError(f"Failed to submit search query on Selling page: {e}")

    results_selector = 'a[href*="/marketplace/item/"], a[href*="/item/"]'
    try:
        await page.wait_for_selector(results_selector, state="visible", timeout=60000)
        print(f"[publish] Step 5: ✓ Search results visible")
    except Exception:
        try:
            await _screenshot(page, "debug_search_results_not_found.png")
        except Exception:
            pass
        raise RuntimeError(
            "Search results did not appear after searching listing title. "
            "Check debug_search_results_not_found.png"
        )

    print(f"[publish] Step 6: Selecting newest visible listing")
    listing_link = None
    candidates = page.locator(results_selector)
    count = await candidates.count()
    print(f"[publish] Step 6: {count} listing candidate(s) found")
    for index in range(min(count, 20)):
        candidate = candidates.nth(index)
        if await candidate.is_visible():
            listing_link = candidate
            print(f"[publish] Step 6: Using candidate #{index+1}")
            break

    if not listing_link:
        try:
            await _screenshot(page, "debug_first_listing_not_found.png")
        except Exception:
            pass
        raise RuntimeError(
            "No visible listing card found in search results. "
            "Check debug_first_listing_not_found.png"
        )

    try:
        await listing_link.click(force=True, timeout=15000)
    except Exception:
        try:
            handle = await listing_link.element_handle()
            if handle:
                await page.evaluate("(el) => el.click()", handle)
        except Exception as e:
            try:
                await _screenshot(page, "debug_click_listing_failed.png")
            except Exception:
                pass
            raise RuntimeError(f"Failed to click first listing card: {e}")

    print(f"[publish] Step 7: Waiting for listing navigation or dialog")
    dialog_selectors = [
        'dialog',
        'div[role="dialog"]',
        'div[aria-modal="true"]',
        'div[role="presentation"]',
    ]

    dialog = None
    try:
        for sel in dialog_selectors:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                await locator.wait_for(state="visible", timeout=10000)
                dialog = locator
                print(f"[publish] Step 7: Dialog opened via selector: {sel}")
                break
    except Exception:
        dialog = None

    if dialog:
        print(f"[publish] Step 7: Opening listing page from dialog")
        dialog_actions = [
            'a:has-text("View listing")',
            'a:has-text("See your listing")',
            'a:has-text("Go to listing")',
            'button:has-text("View listing")',
            'button:has-text("See your listing")',
            'button:has-text("Go to listing")',
            'a[href*="/marketplace/item/"]',
            'a[href*="/item/"]',
        ]

        clicked = False
        for action in dialog_actions:
            try:
                element = dialog.locator(action).first
                if await element.count() > 0 and await element.is_visible():
                    await element.click(force=True, timeout=15000)
                    clicked = True
                    print(f"[publish] Step 7: Clicked dialog action: {action}")
                    break
            except Exception:
                continue

        if not clicked:
            try:
                maybe_link = dialog.locator('a[href*="/marketplace/item/"], a[href*="/item/"]').first
                if await maybe_link.count() > 0 and await maybe_link.is_visible():
                    await maybe_link.click(force=True, timeout=15000)
                    clicked = True
                    print("[publish] Step 7: Clicked fallback dialog link")
            except Exception:
                pass

        if not clicked:
            try:
                await _screenshot(page, "debug_dialog_open_failed.png")
            except Exception:
                pass
            raise RuntimeError("Could not open listing from dialog popup.")
    else:
        print(f"[publish] Step 7: No listing dialog opened; waiting for direct navigation to listing page")

    print(f"[publish] Step 8: Waiting for listing page URL")
    item_url_pattern = re.compile(r'https?://(?:www|web)\.facebook\.com/marketplace/item/\d+')
    try:
        await page.wait_for_url(item_url_pattern, timeout=60000)
        print("[publish] Step 8: Listing page URL detected")
    except Exception:
        pass

    final_url = page.url
    if not item_url_pattern.search(final_url):
        try:
            await _screenshot(page, "debug_listing_page_not_open.png")
        except Exception:
            pass
        raise RuntimeError(f"Listing page did not open. Current URL: {final_url}")

    print(f"[publish] Step 8: Listing page opened")
    print(f"[publish] Current URL: {final_url}")

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        print(f"[publish] Step 8: Listing page load stable")
    except Exception:
        print(f"[publish] Step 8: Listing page load may still be pending")

    fb_listing_id = _extract_fb_listing_id(final_url)
    if not fb_listing_id:
        try:
            await _screenshot(page, "debug_listing_id_extraction_failed.png")
        except Exception:
            pass
        raise RuntimeError(f"Could not extract listing ID from URL: {final_url}")

    print(f"[publish] Step 9: Listing ID extracted: {fb_listing_id}")
    print(f"[publish] Step 10: Returning listing information")

    return {
        "facebook_listing_id": fb_listing_id,
        "facebook_listing_url": final_url,
    }


async def _discard_failed_create(session: BrowserSession) -> bool:
    page = session.page
    discard_selectors = [
        'button:has-text("Discard")',
        'div[role="button"]:has-text("Discard")',
        'button:has-text("Do not save")',
        'div[role="button"]:has-text("Do not save")',
        'button:has-text("Don\'t save")',
        'div[role="button"]:has-text("Don\'t save")',
        'button:has-text("Leave")',
        'div[role="button"]:has-text("Leave")',
    ]

    for sel in discard_selectors:
        try:
            button = page.locator(sel).first
            if await button.count() > 0 and await button.is_visible():
                try:
                    await button.click(force=True, timeout=10000)
                except Exception:
                    handle = await button.element_handle()
                    if handle:
                        await page.evaluate("(el) => el.click()", handle)
                await asyncio.sleep(1.5)
        except Exception:
            pass

    if MARKETPLACE_CREATE in page.url or "/create" in page.url:
        try:
            await page.goto(MARKETPLACE_LISTINGS, timeout=60000)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await asyncio.sleep(2)
        except Exception:
            pass

    return True


async def _delete_fb_listing(session: BrowserSession, fb_id: str) -> bool:
    page = session.page
    item_url = f"https://www.facebook.com/marketplace/item/{fb_id}"
    print(f"[delete_fb_listing] Navigating to {item_url}")
    await page.goto(item_url, timeout=20000)
    await page.wait_for_load_state("domcontentloaded", timeout=60000)
    await asyncio.sleep(2)

    menu_btn = page.locator(
        '[aria-label*="more options" i], '
        '[aria-label*="More" i], '
        'button[aria-haspopup="menu"], '
        'div[role="button"]:has-text("Actions")'
    )
    if await menu_btn.count() == 0:
        print("[delete_fb_listing] No menu button found")
        return False

    await menu_btn.first.click(force=True)
    await session.human_delay(800, 1400)

    delete_btn = page.locator(
        'div[role="menuitem"]:has-text("Delete"), '
        'span:has-text("Delete listing"), '
        'button:has-text("Delete")'
    )
    if await delete_btn.count() == 0:
        print("[delete_fb_listing] No delete button found")
        return False

    await delete_btn.first.click(force=True)
    await session.human_delay(900, 1600)

    confirm_btn = page.locator('div[role="button"]:has-text("Delete"), button:has-text("Delete")')
    if await confirm_btn.count() > 0:
        await confirm_btn.first.click(force=True)
        await session.human_delay(1000, 1600)

    print("[delete_fb_listing] Delete workflow triggered")
    return True


async def publish_listing(
    account_id: str,
    listing_id: str,
    delay_seconds: int,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "publish_listing",
        {"account_id": account_id, "listing_id": listing_id},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=1, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        db = get_supabase()

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            result = db.table("listings").select("*").eq("id", listing_id).limit(1).execute()
            if not result.data:
                await update_task(
                    task_id,
                    status="failed",
                    error=f"Listing {listing_id} not found",
                    finished_at=True,
                )
                _set_account_status(account_id, "idle")
                return

            listing = result.data[0]
            if listing.get("status") != "draft":
                await update_task(
                    task_id,
                    status="failed",
                    error="Only draft listings can be published",
                    finished_at=True,
                )
                _set_account_status(account_id, "idle")
                return

            try:
                await _fill_listing_form(session, listing)
                
                # Step 1-4: Click Publish and wait for completion
                await _publish_listing(session)
                
                # Step 5-10: Search the Selling page and extract the listing URL/ID
                listing_title = listing.get('title') or listing.get('name') or ''
                if not listing_title:
                    raise RuntimeError("No listing title available for post-publish lookup")

                print(f"[publish] Extracting Listing ID from Selling page using title: '{listing_title}'")
                fb_result = await _extract_listing_id_after_publish(session, listing_title)
                fb_id = fb_result["facebook_listing_id"]
                facebook_listing_url = fb_result["facebook_listing_url"]

                from datetime import datetime, timezone

                _update_listing(
                    listing_id,
                    {
                        "status": "published",
                        "fb_listing_id": fb_id,
                        "facebook_listing_url": facebook_listing_url,
                        "published_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                print("[publish] Saved Listing ID successfully.")

                verify = db.table("listings").select("*").eq("id", listing_id).limit(1).execute()
                if not verify.data:
                    raise RuntimeError(
                        f"Failed to verify listing update for {listing_id}: record missing after update"
                    )
                saved = verify.data[0]
                if str(saved.get("fb_listing_id")) != str(fb_id) or saved.get("facebook_listing_url") != facebook_listing_url:
                    raise RuntimeError(
                        f"Listing verification mismatch after update for {listing_id}: "
                        f"fb_listing_id={saved.get('fb_listing_id')} facebook_listing_url={saved.get('facebook_listing_url')}"
                    )
                print("[publish] Verified listing update in database successfully.")

                await write_log(
                    "publish_listing",
                    task_id=task_id,
                    account_id=account_id,
                    details={
                        "listing_id": listing_id,
                        "facebook_listing_id": fb_id,
                        "facebook_listing_url": facebook_listing_url,
                    },
                )
                await update_task(
                    task_id,
                    completed_steps=1,
                    progress=100,
                    status="completed",
                    finished_at=True,
                    result={"published": 1},
                )
            except Exception as e:
                await write_log(
                    "publish_listing",
                    task_id=task_id,
                    account_id=account_id,
                    status="failed",
                    error=str(e),
                )
                await update_task(
                    task_id,
                    status="failed",
                    error=str(e),
                    finished_at=True,
                )

        _touch_account(account_id)
        _set_account_status(account_id, "idle")

    run_background_task(_run(), task_id=task_id)
    return task_id


async def delete_listing(
    account_id: str,
    listing_id: str,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "delete_listing",
        {"account_id": account_id, "listing_id": listing_id},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=1, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        db = get_supabase()

        result = db.table("listings").select("*").eq("id", listing_id).limit(1).execute()
        if not result.data:
            await update_task(
                task_id,
                status="failed",
                error=f"Listing {listing_id} not found",
                finished_at=True,
            )
            _set_account_status(account_id, "idle")
            return

        listing = result.data[0]
        fb_id = listing.get("fb_listing_id")

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            try:
                deleted = False
                if fb_id:
                    deleted = await _delete_fb_listing(session, fb_id)
                    if not deleted:
                        raise RuntimeError("Facebook delete workflow could not be completed")

                _update_listing(listing_id, {"status": "deleted"})
                await write_log(
                    "delete_listing",
                    task_id=task_id,
                    account_id=account_id,
                    details={"listing_id": listing_id, "fb_id": fb_id},
                )
                await update_task(
                    task_id,
                    completed_steps=1,
                    progress=100,
                    status="completed",
                    finished_at=True,
                    result={"deleted": 1},
                )
            except Exception as e:
                await write_log(
                    "delete_listing",
                    task_id=task_id,
                    account_id=account_id,
                    status="failed",
                    error=str(e),
                )
                await update_task(
                    task_id,
                    status="failed",
                    error=str(e),
                    finished_at=True,
                )

        _touch_account(account_id)
        _set_account_status(account_id, "idle")

    run_background_task(_run(), task_id=task_id)
    return task_id


async def _save_as_draft(session: BrowserSession):
    page = session.page
    try:
        draft_btn = page.locator(
            'div[role="button"]:has-text("Save draft"), button:has-text("Save draft")'
        )
        if await draft_btn.count() > 0:
            await draft_btn.first.click(timeout=8000)
            await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass


# ------------------------------------------------------------------ features --

async def new_account_slow(
    account_id: str,
    listing_count: int,
    delay_seconds: int,
    use_ai: bool,
    product_name: Optional[str],
    category: Optional[str],
    condition: str,
    price: int,
    images: list[str],
    description: Optional[str] = None,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "new_account_slow",
        {"account_id": account_id, "listing_count": listing_count},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=listing_count, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        # Resolve image URLs/paths — download if URL, verify if local path
        try:
            resolved_images = await _resolve_images(images)
        except ValueError as ve:
            await update_task(task_id, status="failed", error=str(ve), finished_at=True)
            _set_account_status(account_id, "idle")
            return

        account = _get_account(account_id)
        success = 0
        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            for i in range(listing_count):
                title = product_name or f"Item for sale {i + 1}"
                description_text = description or None

                # Pick image for this listing — resolved_images[i % len] cycles if fewer images than listings
                listing_image = [resolved_images[i % len(resolved_images)]] if resolved_images else []

                if use_ai and product_name:
                    try:
                        ai_result = await _ai_service.generate_listing(
                            product_name=product_name,
                            category=category or "miscellaneous",
                            condition=condition,
                            price=price,
                            session_id=f"{task_id}_listing_{i}",
                        )
                        title = ai_result["title"]
                        description_text = ai_result.get("description") or description_text
                    except Exception:
                        pass

                listing_data = {
                    "account_id": account_id,
                    "title": title,
                    "description": description_text,
                    "price": price,
                    "category": category,
                    "condition": condition,
                    "images": listing_image,   # one image per listing
                    "status": "draft",
                }
                db_listing = _upsert_listing(listing_data)

                try:
                    form_ok = await _fill_listing_form(session, db_listing)
                    if not form_ok:
                        raise RuntimeError("Form fill returned False")
                    fb_id = await _publish_listing(session)
                    from datetime import datetime, timezone

                    _update_listing(
                        db_listing["id"],
                        {
                            "status": "published",
                            "fb_listing_id": fb_id,
                            "published_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    success += 1
                    print(f"[new_account_slow] Listing {i+1} published successfully. fb_id={fb_id}")
                    await write_log(
                        "publish_listing",
                        task_id=task_id,
                        account_id=account_id,
                        details={"listing_id": db_listing["id"], "fb_id": fb_id},
                    )
                except Exception as e:
                    print(f"[new_account_slow] Listing {i+1} FAILED: {e}")
                    # Keep status as "draft" — "failed" is not a valid DB status
                    await write_log(
                        "publish_listing",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / listing_count * 100),
                )
                if i < listing_count - 1:
                    await asyncio.sleep(delay_seconds + random.uniform(-2, 5))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")

        if success == 0:
            await update_task(
                task_id,
                status="failed",
                finished_at=True,
                error=f"0 out of {listing_count} listings were published. Check task logs for the exact Facebook error.",
                result={"published": 0, "total": listing_count},
            )
        else:
            await update_task(
                task_id,
                status="completed",
                finished_at=True,
                result={"published": success, "total": listing_count},
            )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def new_account_slow_v2(
    account_id: str,
    listing_count: int,
    delay_seconds: int,
    use_ai: bool,
    product_name: Optional[str],
    category: Optional[str],
    condition: str,
    price: int,
    images: list[str],
    warmup_before: bool,
    warmup_steps: int,
    user_id: str | None = None,
) -> str:
    if warmup_before:
        warmup_task_id = await fb_warmup(
            account_id=account_id,
            duration_minutes=warmup_steps,
            actions_per_minute=3,
            user_id=user_id,
        )
        # Give warmup a head start
        await asyncio.sleep(2)

    return await new_account_slow(
        account_id=account_id,
        listing_count=listing_count,
        delay_seconds=delay_seconds,
        use_ai=use_ai,
        product_name=product_name,
        category=category,
        condition=condition,
        price=price,
        images=images,
        user_id=user_id,
    )


async def ultra_ai_listings(
    account_id: str,
    listing_count: int,
    product_name: str,
    category: str,
    condition: str,
    price: int,
    images: list[str],
    extra_details: str,
    user_id: str | None = None,
) -> str:
    """Generates up to 100 AI-powered listings rapidly."""
    task_id = await create_task(
        "ultra_ai_listings",
        {"account_id": account_id, "listing_count": listing_count},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=listing_count, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        # Resolve image URLs/paths — download if URL, verify if local path
        try:
            resolved_images = await _resolve_images(images)
        except ValueError as ve:
            await update_task(task_id, status="failed", error=str(ve), finished_at=True)
            _set_account_status(account_id, "idle")
            return

        account = _get_account(account_id)
        success = 0
        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            ai_tasks = [
                _ai_service.generate_listing(
                    product_name=product_name,
                    category=category,
                    condition=condition,
                    price=price,
                    extra_details=extra_details,
                    session_id=f"{task_id}_ai_{i}",
                )
                for i in range(listing_count)
            ]
            ai_results = await asyncio.gather(*ai_tasks, return_exceptions=True)

            for i, ai_result in enumerate(ai_results):
                if isinstance(ai_result, Exception):
                    title = f"{product_name} - {i + 1}"
                    description = extra_details
                else:
                    title = ai_result["title"]
                    description = ai_result["description"]

                listing_data = {
                    "account_id": account_id,
                    "title": title,
                    "description": description,
                    "price": price,
                    "category": category,
                    "condition": condition,
                    "images": images,
                    "status": "draft",
                }
                db_listing = _upsert_listing(listing_data)

                try:
                    await _fill_listing_form(session, db_listing)
                    fb_id = await _publish_listing(session)
                    from datetime import datetime, timezone

                    _update_listing(
                        db_listing["id"],
                        {
                            "status": "published",
                            "fb_listing_id": fb_id,
                            "published_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    success += 1
                except Exception as e:
                    await write_log(
                        "ultra_ai_publish",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / listing_count * 100),
                )
                await asyncio.sleep(random.uniform(5, 15))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")

        if success == 0:
            await update_task(
                task_id,
                status="failed",
                finished_at=True,
                error=f"0 out of {listing_count} listings were published. Check task logs for the exact Facebook error.",
                result={"published": 0, "total": listing_count},
            )
        else:
            await update_task(
                task_id,
                status="completed",
                finished_at=True,
                result={"published": success, "total": listing_count},
            )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def create_only_drafts(
    account_id: str,
    draft_count: int,
    title: str,
    description: Optional[str],
    price: int,
    category: Optional[str],
    condition: str,
    images: list[str],
    use_ai: bool,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "create_only_drafts",
        {"account_id": account_id, "draft_count": draft_count},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=draft_count, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        created = 0
        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            for i in range(draft_count):
                final_title = title
                final_desc = description

                if use_ai:
                    try:
                        ai = await _ai_service.generate_listing(
                            product_name=title,
                            category=category or "miscellaneous",
                            condition=condition,
                            price=price,
                            session_id=f"{task_id}_draft_{i}",
                        )
                        final_title = ai["title"]
                        final_desc = ai["description"]
                    except Exception:
                        pass

                listing_data = {
                    "account_id": account_id,
                    "title": final_title,
                    "description": final_desc,
                    "price": price,
                    "category": category,
                    "condition": condition,
                    "images": images,
                    "status": "draft",
                }
                db_listing = _upsert_listing(listing_data)

                try:
                    await _fill_listing_form(session, db_listing)
                    await _save_as_draft(session)
                    created += 1
                    await write_log(
                        "create_draft",
                        task_id=task_id,
                        account_id=account_id,
                        details={"listing_id": db_listing["id"]},
                    )
                except Exception as e:
                    await write_log(
                        "create_draft",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / draft_count * 100),
                )
                await asyncio.sleep(random.uniform(5, 15))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"created": created, "total": draft_count},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def renew_listings(
    account_id: str,
    listing_ids: Optional[list[str]],
    max_renew: int,
    delay_seconds: int,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "renew_listings",
        {"account_id": account_id, "max_renew": max_renew},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=max_renew, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        renewed = 0
        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            db = get_supabase()
            if listing_ids:
                result = db.table("listings").select("*").in_("id", listing_ids).execute()
            else:
                result = (
                    db.table("listings")
                    .select("*")
                    .eq("account_id", account_id)
                    .eq("status", "published")
                    .limit(max_renew)
                    .execute()
                )

            listings = result.data[:max_renew]
            await update_task(task_id, total_steps=len(listings))

            for i, listing in enumerate(listings):
                fb_id = listing.get("fb_listing_id")
                if not fb_id:
                    continue
                try:
                    page = session.page
                    await page.goto(
                        f"https://www.facebook.com/marketplace/item/{fb_id}",
                        timeout=60000,
                    )
                    await page.wait_for_load_state("domcontentloaded")
                    renew_btn = page.locator(
                        'div[role="button"]:has-text("Renew"), button:has-text("Renew")'
                    )
                    if await renew_btn.count() > 0:
                        await renew_btn.first.click()
                        await session.human_delay()
                        renewed += 1
                        await write_log(
                            "renew_listing",
                            task_id=task_id,
                            account_id=account_id,
                            details={"fb_id": fb_id},
                        )
                except Exception as e:
                    await write_log(
                        "renew_listing",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / len(listings) * 100),
                )
                await asyncio.sleep(delay_seconds + random.uniform(-1, 3))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"renewed": renewed},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def listing_automation(
    account_id: str,
    workflow_type: str,
    listing_ids: Optional[list[str]],
    max_listings: int,
    delay_seconds: int,
    schedule_time: Optional[str] = None,
    repeat_interval: Optional[str] = None,
    repeat_until: Optional[str] = None,
    user_id: str | None = None,
) -> str:
    """Unified listing automation workflow supporting multiple operation types."""
    task_id = await create_task(
        f"listing_automation_{workflow_type}",
        {
            "account_id": account_id,
            "workflow_type": workflow_type,
            "max_listings": max_listings,
        },
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=max_listings, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        # Route to appropriate workflow
        if workflow_type == "renew":
            result_task_id = await renew_listings(
                account_id=account_id,
                listing_ids=listing_ids,
                max_renew=max_listings,
                delay_seconds=delay_seconds,
                user_id=user_id,
            )
            await update_task(
                task_id,
                status="completed",
                finished_at=True,
                result={"workflow_type": workflow_type, "sub_task_id": result_task_id},
            )
        elif workflow_type == "relist":
            result_task_id = await relist_listings(
                account_id=account_id,
                listing_ids=listing_ids,
                max_relist=max_listings,
                delay_seconds=delay_seconds,
                user_id=user_id,
            )
            await update_task(
                task_id,
                status="completed",
                finished_at=True,
                result={"workflow_type": workflow_type, "sub_task_id": result_task_id},
            )
        elif workflow_type == "delete_and_repost":
            result_task_id = await delete_all_listings(
                account_id=account_id,
                status_filter="published",
                confirm=True,
                user_id=user_id,
            )
            await update_task(
                task_id,
                status="completed",
                finished_at=True,
                result={"workflow_type": workflow_type, "sub_task_id": result_task_id},
            )
        elif workflow_type == "schedule":
            await update_task(
                task_id,
                status="failed",
                finished_at=True,
                error="Scheduled workflows require additional implementation",
            )
        else:
            await update_task(
                task_id,
                status="failed",
                finished_at=True,
                error=f"Unknown workflow type: {workflow_type}",
            )

        _set_account_status(account_id, "idle")

    run_background_task(_run(), task_id=task_id)
    return task_id


async def relist_listings(
    account_id: str,
    listing_ids: Optional[list[str]],
    max_relist: int,
    delay_seconds: int,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "relist_listings",
        {"account_id": account_id, "max_relist": max_relist},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=max_relist, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        relisted = 0
        db = get_supabase()

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            if listing_ids:
                result = db.table("listings").select("*").in_("id", listing_ids).execute()
            else:
                result = (
                    db.table("listings")
                    .select("*")
                    .eq("account_id", account_id)
                    .in_("status", ["published", "deleted"])
                    .limit(max_relist)
                    .execute()
                )

            listings = result.data[:max_relist]
            await update_task(task_id, total_steps=len(listings))

            for i, listing in enumerate(listings):
                new_listing = {
                    "account_id": account_id,
                    "title": listing["title"],
                    "description": listing.get("description"),
                    "price": listing["price"],
                    "category": listing.get("category"),
                    "condition": listing.get("condition", "used_good"),
                    "images": listing.get("images", []),
                    "status": "draft",
                }
                db_listing = _upsert_listing(new_listing)

                try:
                    await _fill_listing_form(session, db_listing)
                    fb_id = await _publish_listing(session)
                    from datetime import datetime, timezone

                    _update_listing(
                        db_listing["id"],
                        {
                            "status": "relisted",
                            "fb_listing_id": fb_id,
                            "published_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    relisted += 1
                    await write_log(
                        "relist_listing",
                        task_id=task_id,
                        account_id=account_id,
                        details={"original_id": listing["id"], "new_id": db_listing["id"]},
                    )
                except Exception as e:
                    await write_log(
                        "relist_listing",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / len(listings) * 100),
                )
                await asyncio.sleep(delay_seconds + random.uniform(-1, 3))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"relisted": relisted},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def draft_publisher_ai(
    account_id: str,
    draft_ids: Optional[list[str]],
    max_publish: int,
    delay_seconds: int,
    improve_with_ai: bool,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "draft_publisher_ai",
        {"account_id": account_id, "max_publish": max_publish},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=max_publish, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        published = 0
        db = get_supabase()

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            if draft_ids:
                result = db.table("listings").select("*").in_("id", draft_ids).execute()
            else:
                result = (
                    db.table("listings")
                    .select("*")
                    .eq("account_id", account_id)
                    .eq("status", "draft")
                    .limit(max_publish)
                    .execute()
                )

            drafts = result.data[:max_publish]
            await update_task(task_id, total_steps=len(drafts))

            for i, draft in enumerate(drafts):
                if improve_with_ai and draft.get("description"):
                    try:
                        improved = await _ai_service.improve_description(
                            title=draft["title"],
                            description=draft["description"],
                            session_id=f"{task_id}_improve_{i}",
                        )
                        _update_listing(draft["id"], {"description": improved})
                        draft["description"] = improved
                    except Exception:
                        pass

                try:
                    await _fill_listing_form(session, draft)
                    fb_id = await _publish_listing(session)
                    from datetime import datetime, timezone

                    _update_listing(
                        draft["id"],
                        {
                            "status": "published",
                            "fb_listing_id": fb_id,
                            "published_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    published += 1
                    await write_log(
                        "draft_publish_ai",
                        task_id=task_id,
                        account_id=account_id,
                        details={"listing_id": draft["id"], "fb_id": fb_id},
                    )
                except Exception as e:
                    await write_log(
                        "draft_publish_ai",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / len(drafts) * 100),
                )
                await asyncio.sleep(delay_seconds + random.uniform(-2, 5))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"published": published},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def delete_all_listings(
    account_id: str,
    status_filter: Optional[str],
    confirm: bool = False,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "delete_all_listings",
        {"account_id": account_id, "status_filter": status_filter},
        user_id=user_id,
    )
    await update_task(task_id, status="running", started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        deleted = 0
        db = get_supabase()

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            query = db.table("listings").select("*").eq("account_id", account_id)
            if status_filter:
                query = query.eq("status", status_filter)
            result = query.execute()
            listings = result.data
            await update_task(task_id, total_steps=len(listings))

            for i, listing in enumerate(listings):
                fb_id = listing.get("fb_listing_id")
                try:
                    if fb_id:
                        page = session.page
                        await page.goto(
                            f"https://www.facebook.com/marketplace/item/{fb_id}",
                            timeout=60000,
                        )
                        await page.wait_for_load_state("domcontentloaded")
                        menu_btn = page.locator('[aria-label*="more options"], [aria-label*="More"]')
                        if await menu_btn.count() > 0:
                            await menu_btn.first.click()
                            await session.human_delay(500, 1000)
                            delete_btn = page.locator(
                                'div[role="menuitem"]:has-text("Delete"), span:has-text("Delete listing")'
                            )
                            if await delete_btn.count() > 0:
                                await delete_btn.first.click()
                                await session.human_delay(800, 1500)
                                confirm_btn = page.locator(
                                    'div[role="button"]:has-text("Delete")'
                                )
                                if await confirm_btn.count() > 0:
                                    await confirm_btn.first.click()
                                    await session.human_delay()

                    _update_listing(listing["id"], {"status": "deleted"})
                    deleted += 1
                    await write_log(
                        "delete_listing",
                        task_id=task_id,
                        account_id=account_id,
                        details={"listing_id": listing["id"], "fb_id": fb_id},
                    )
                except Exception as e:
                    await write_log(
                        "delete_listing",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / len(listings) * 100),
                )
                await asyncio.sleep(random.uniform(3, 8))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"deleted": deleted},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def draft_publisher(
    account_id: str,
    draft_ids: Optional[list[str]],
    max_publish: int,
    delay_seconds: int,
    user_id: str | None = None,
) -> str:
    return await draft_publisher_ai(
        account_id=account_id,
        draft_ids=draft_ids,
        max_publish=max_publish,
        delay_seconds=delay_seconds,
        improve_with_ai=False,
        user_id=user_id,
    )


async def draft_delete(
    account_id: str,
    draft_ids: Optional[list[str]],
    max_delete: int,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "draft_delete",
        {"account_id": account_id, "max_delete": max_delete},
        user_id=user_id,
    )
    await update_task(task_id, status="running", started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        deleted = 0
        db = get_supabase()

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            if draft_ids:
                result = db.table("listings").select("*").in_("id", draft_ids).execute()
            else:
                result = (
                    db.table("listings")
                    .select("*")
                    .eq("account_id", account_id)
                    .eq("status", "draft")
                    .limit(max_delete)
                    .execute()
                )
            drafts = result.data[:max_delete]
            await update_task(task_id, total_steps=len(drafts))

            for i, draft in enumerate(drafts):
                try:
                    _update_listing(draft["id"], {"status": "deleted"})
                    deleted += 1
                    await write_log(
                        "delete_draft",
                        task_id=task_id,
                        account_id=account_id,
                        details={"listing_id": draft["id"]},
                    )
                except Exception as e:
                    await write_log(
                        "delete_draft",
                        task_id=task_id,
                        account_id=account_id,
                        status="failed",
                        error=str(e),
                    )

                await update_task(
                    task_id,
                    completed_steps=i + 1,
                    progress=int((i + 1) / len(drafts) * 100),
                )
                await asyncio.sleep(random.uniform(1, 3))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"deleted": deleted},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def ads_multiplier(
    account_id: str,
    listing_ids: Optional[list[str]],
    multiplier: int,
    delay_seconds: int,
    user_id: str | None = None,
) -> str:
    """Clone each listing N times to multiply marketplace presence."""
    task_id = await create_task(
        "ads_multiplier",
        {"account_id": account_id, "multiplier": multiplier},
        user_id=user_id,
    )
    await update_task(task_id, status="running", started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        created = 0
        db = get_supabase()

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            if listing_ids:
                result = db.table("listings").select("*").in_("id", listing_ids).execute()
            else:
                result = (
                    db.table("listings")
                    .select("*")
                    .eq("account_id", account_id)
                    .eq("status", "published")
                    .limit(20)
                    .execute()
                )

            originals = result.data
            total = len(originals) * (multiplier - 1)
            await update_task(task_id, total_steps=total)

            step = 0
            for listing in originals:
                for _ in range(multiplier - 1):
                    clone = {
                        "account_id": account_id,
                        "title": listing["title"],
                        "description": listing.get("description"),
                        "price": listing["price"],
                        "category": listing.get("category"),
                        "condition": listing.get("condition", "used_good"),
                        "images": listing.get("images", []),
                        "status": "draft",
                    }
                    db_listing = _upsert_listing(clone)
                    try:
                        await _fill_listing_form(session, db_listing)
                        fb_id = await _publish_listing(session)
                        from datetime import datetime, timezone

                        _update_listing(
                            db_listing["id"],
                            {
                                "status": "published",
                                "fb_listing_id": fb_id,
                                "published_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        created += 1
                    except Exception as e:
                        await write_log(
                            "ads_multiplier",
                            task_id=task_id,
                            account_id=account_id,
                            status="failed",
                            error=str(e),
                        )
                    step += 1
                    await update_task(
                        task_id,
                        completed_steps=step,
                        progress=int(step / total * 100) if total else 100,
                    )
                    await asyncio.sleep(delay_seconds + random.uniform(-2, 5))

        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"clones_created": created},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def fb_warmup(
    account_id: str,
    duration_minutes: int,
    actions_per_minute: int,
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "fb_warmup",
        {"account_id": account_id, "duration_minutes": duration_minutes},
        user_id=user_id,
    )
    total_actions = duration_minutes * actions_per_minute
    await update_task(
        task_id, status="running", total_steps=total_actions, started_at=True
    )
    _set_account_status(account_id, "warming")

    async def _run():
        account = _get_account(account_id)
        db = get_supabase()

        warmup_urls = [
            "https://www.facebook.com/",
            "https://www.facebook.com/marketplace/",
            "https://www.facebook.com/marketplace/category/vehicles",
            "https://www.facebook.com/marketplace/category/propertyrentals",
            "https://www.facebook.com/marketplace/category/electronics",
            "https://www.facebook.com/marketplace/category/clothing",
            "https://www.facebook.com/marketplace/category/furniture",
        ]

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)

            completed = 0
            for step in range(total_actions):
                action = random.choice(["scroll", "navigate", "hover"])
                try:
                    if action == "navigate":
                        url = random.choice(warmup_urls)
                        await session.page.goto(url, timeout=60000)
                        await session.page.wait_for_load_state("domcontentloaded", timeout=10000)
                    elif action == "scroll":
                        await session.random_scroll(random.randint(2, 5))
                    elif action == "hover":
                        links = await session.page.locator("a").all()
                        if links:
                            target = random.choice(links[:20])
                            await target.hover()

                    completed += 1
                    await write_log(
                        "warmup_action",
                        task_id=task_id,
                        account_id=account_id,
                        details={"action": action, "step": step},
                    )
                except Exception:
                    pass

                await update_task(
                    task_id,
                    completed_steps=step + 1,
                    progress=int((step + 1) / total_actions * 100),
                )
                interval = 60 / actions_per_minute
                await asyncio.sleep(interval + random.uniform(-2, 5))

        new_level = min(100, account.get("warmup_level", 0) + min(completed, 20))
        db.table("fb_accounts").update({"warmup_level": new_level}).eq("id", account_id).execute()
        _touch_account(account_id)
        _set_account_status(account_id, "idle")
        await update_task(
            task_id,
            status="completed",
            finished_at=True,
            result={"actions_completed": completed, "warmup_level": new_level},
        )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def fb_profile_updater(
    account_id: str,
    name: Optional[str],
    bio: Optional[str],
    location: Optional[str],
    profile_pic_url: Optional[str],
    cover_pic_url: Optional[str],
    user_id: str | None = None,
) -> str:
    task_id = await create_task(
        "fb_profile_updater",
        {"account_id": account_id},
        user_id=user_id,
    )
    await update_task(task_id, status="running", total_steps=3, started_at=True)
    _set_account_status(account_id, "active")

    async def _run():
        account = _get_account(account_id)
        print(f"[profile_updater] Starting for {account.get('email')}")

        async with _browser_manager.new_session(
            proxy=account.get("proxy"),
            cookies_json=account.get("cookies"),
        ) as session:
            logged_in = await _do_login(session, account)
            if not logged_in:
                await update_task(task_id, status="failed", error="Login failed", finished_at=True)
                _set_account_status(account_id, "idle")
                return

            cookies = await session.save_cookies()
            _save_cookies(account_id, cookies)
            page = session.page
            updates_done = 0
            errors = []

            try:
                # Navigate to profile
                print(f"[profile_updater] Navigating to profile...")
                try:
                    await page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=60000)
                except Exception as nav_err:
                    print(f"[profile_updater] page.goto warning: {nav_err}")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                await asyncio.sleep(2)
                profile_url = page.url
                print(f"[profile_updater] Profile URL: {profile_url}")

                # ── Bio update ─────────────────────────────────────────────────
                if bio:
                    print(f"[profile_updater] Updating bio: {bio}")
                    bio_success = False

                    # Strategy 1: Look for direct bio buttons on profile page
                    bio_selectors = [
                        '[aria-label*="Add bio" i]',
                        '[aria-label*="Edit bio" i]',
                        'div[role="button"]:has-text("Add bio")',
                        'div[role="button"]:has-text("Edit bio")',
                        'div[role="button"]:has-text("Add Bio")',
                        'div[role="button"]:has-text("Edit Bio")',
                        'span:has-text("Add bio")',
                        'span:has-text("Edit bio")',
                    ]

                    for sel in bio_selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.count() > 0 and await el.is_visible():
                                await el.click()
                                await asyncio.sleep(1.5)
                                print(f"[profile_updater] Clicked bio button: {sel}")
                                break
                        except Exception:
                            pass

                    # Strategy 2: If no direct bio button worked, open "Edit profile" modal
                    edit_prof_selectors = [
                        'div[role="button"]:has-text("Edit profile")',
                        '[aria-label*="Edit profile" i]',
                        'span:has-text("Edit profile")',
                        'div[role="button"]:has-text("Edit Profile")',
                        'a:has-text("Edit profile")',
                    ]
                    for sel in edit_prof_selectors:
                        try:
                            btn = page.locator(sel).first
                            if await btn.count() > 0 and await btn.is_visible():
                                await btn.click()
                                await asyncio.sleep(2)
                                print(f"[profile_updater] Clicked 'Edit profile' modal button")
                                break
                        except Exception:
                            pass

                    # Look for Bio add/edit inside modal if present
                    modal_bio_btn = page.locator(
                        'div[role="dialog"] div:has-text("Bio") div[role="button"], '
                        'div[role="dialog"] [aria-label*="Bio" i], '
                        'div[role="dialog"] [aria-label*="bio" i]'
                    ).first
                    if await modal_bio_btn.count() > 0 and await modal_bio_btn.is_visible():
                        try:
                            await modal_bio_btn.click()
                            await asyncio.sleep(1.5)
                            print(f"[profile_updater] Clicked modal bio button")
                        except Exception:
                            pass

                    # Search for bio input field (textarea or contenteditable div)
                    bio_input = None
                    for loc_str in [
                        'textarea',
                        'div[role="dialog"] textarea',
                        'div[contenteditable="true"]',
                        '[aria-label*="bio" i]',
                        '[placeholder*="bio" i]',
                        '[placeholder*="Describe" i]',
                    ]:
                        locs = page.locator(loc_str)
                        c = await locs.count()
                        for i in range(c):
                            item = locs.nth(i)
                            if await item.is_visible():
                                bio_input = item
                                print(f"[profile_updater] Found visible bio input field: {loc_str}")
                                break
                        if bio_input:
                            break

                    if bio_input:
                        try:
                            await bio_input.click()
                            await asyncio.sleep(0.5)
                            tag = await bio_input.evaluate("el => el.tagName.toLowerCase()")
                            if tag in ["textarea", "input"]:
                                await bio_input.focus()
                                await bio_input.fill("")
                                await bio_input.fill(bio)
                            else:
                                await bio_input.evaluate("el => el.innerText = ''")
                                await bio_input.type(bio)
                            await asyncio.sleep(1)

                            # Click save button
                            save_btn = page.locator(
                                'div[role="dialog"] div[role="button"]:has-text("Save"), '
                                'div[role="button"]:has-text("Save"), '
                                'button:has-text("Save"), '
                                '[aria-label="Save"]'
                            ).first
                            if await save_btn.count() > 0 and await save_btn.is_visible():
                                await save_btn.click()
                                await asyncio.sleep(2)
                                updates_done += 1
                                bio_success = True
                                print(f"[profile_updater] Bio saved successfully")
                        except Exception as e_bio:
                            errors.append(f"Failed to fill/save bio: {e_bio}")
                    else:
                        errors.append("Bio input field not found")

                await update_task(task_id, completed_steps=1, progress=33)

                # ── Name update ────────────────────────────────────────────────
                if name:
                    print(f"[profile_updater] Attempting Name update: {name}")
                    try:
                        await page.goto("https://accountscenter.facebook.com/personal_details", wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(2)
                        name_entry = page.locator('div:has-text("Name"), a[href*="name"], span:has-text("Name")').first
                        if await name_entry.count() > 0:
                            print(f"[profile_updater] Name section located in Meta Accounts Center")
                            errors.append("Name update opened in Accounts Center — Meta security requires manual password confirmation")
                        else:
                            errors.append("Name update requires manual navigation to Settings — skipped")
                    except Exception as e_name:
                        errors.append(f"Name update navigation: {e_name}")

                await update_task(task_id, completed_steps=2, progress=66)

                # ── Location update ────────────────────────────────────────────
                if location:
                    print(f"[profile_updater] Location update: {location}")
                    # Navigate to About > Places
                    try:
                        if "?" in profile_url:
                            places_url = f"{profile_url}&sk=about_places"
                        else:
                            places_url = f"{profile_url.rstrip('/')}/about_places"
                        await page.goto(places_url, wait_until="domcontentloaded", timeout=20000)
                    except Exception as nav_err:
                        print(f"[profile_updater] Location navigation warning: {nav_err}")
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                    city_btn_selectors = [
                        'div[role="button"]:has-text("Add current city")',
                        'span:has-text("Add current city")',
                        'a:has-text("Add current city")',
                        'div[role="button"]:has-text("Edit current city")',
                        'span:has-text("Edit current city")',
                        'div[role="button"]:has-text("Add hometown")',
                        'span:has-text("Add hometown")',
                        'div[role="button"]:has-text("Add a city")',
                        'span:has-text("Add a city")',
                        'div[role="button"]:has-text("Add city")',
                        'span:has-text("Add city")',
                        '[aria-label*="Add current city" i]',
                        '[aria-label*="Edit current city" i]',
                        '[aria-label*="Add hometown" i]',
                        '[aria-label*="Add a city" i]',
                    ]

                    city_btn = None
                    for sel in city_btn_selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.count() > 0 and await el.is_visible():
                                city_btn = el
                                print(f"[profile_updater] Found city button: {sel}")
                                break
                        except Exception:
                            pass

                    if city_btn is not None:
                        await city_btn.click()
                        await asyncio.sleep(1)
                        city_inputs = page.locator('input[aria-label*="city" i], input[placeholder*="city" i], input[placeholder*="City" i], input[type="text"]')
                        count = await city_inputs.count()
                        city_input = None
                        for i in range(count):
                            item = city_inputs.nth(i)
                            if await item.is_visible():
                                city_input = item
                                break

                        if city_input is not None:
                            await city_input.focus()
                            await city_input.fill("")
                            await city_input.fill(location)
                            await asyncio.sleep(1.5)

                            option = page.locator('div[role="listbox"] [role="option"], ul[role="listbox"] li, [role="option"]').first
                            if await option.count() > 0 and await option.is_visible():
                                await option.click()
                                await asyncio.sleep(1)
                            await asyncio.sleep(0.5)
                            save = page.locator('div[role="button"]:has-text("Save"), button:has-text("Save")').first
                            if await save.count() > 0:
                                await save.click()
                                updates_done += 1
                                print(f"[profile_updater] Location saved")
                    else:
                        errors.append("City/location button not found")

                await update_task(task_id, completed_steps=3, progress=100)

                await write_log(
                    "profile_update",
                    task_id=task_id,
                    account_id=account_id,
                    details={"bio": bool(bio), "name": bool(name), "location": bool(location), "updates_done": updates_done},
                )
                print(f"[profile_updater] Done. updates_done={updates_done} errors={errors}")

            except Exception as e:
                print(f"[profile_updater] EXCEPTION: {e}")
                await write_log(
                    "profile_update",
                    task_id=task_id,
                    account_id=account_id,
                    status="failed",
                    error=str(e),
                )

        _touch_account(account_id)
        _set_account_status(account_id, "idle")

        if updates_done == 0 and errors:
            await update_task(
                task_id,
                status="failed",
                finished_at=True,
                error=f"No updates applied. Issues: {'; '.join(errors)}",
            )
        else:
            await update_task(
                task_id,
                status="completed",
                finished_at=True,
                result={"updates_done": updates_done, "errors": errors},
            )

    run_background_task(_run(), task_id=task_id)
    return task_id


async def get_clicks_on_marketplace(
    account_id: str,
    listing_ids: Optional[list[str]],
) -> dict:
    """Fetch click/view counts for listings from FB Marketplace seller dashboard."""
    account = _get_account(account_id)
    db = get_supabase()

    if listing_ids:
        result = db.table("listings").select("*").in_("id", listing_ids).execute()
    else:
        result = (
            db.table("listings")
            .select("*")
            .eq("account_id", account_id)
            .eq("status", "published")
            .limit(50)
            .execute()
        )

    listings = result.data
    click_data = []

    async with _browser_manager.new_session(
        proxy=account.get("proxy"),
        cookies_json=account.get("cookies"),
    ) as session:
        logged_in = await _do_login(session, account)
        if not logged_in:
            raise ValueError("Login failed")

        cookies = await session.save_cookies()
        _save_cookies(account_id, cookies)
        page = session.page

        for listing in listings:
            fb_id = listing.get("fb_listing_id")
            views = 0
            if fb_id:
                try:
                    await page.goto(
                        f"https://www.facebook.com/marketplace/item/{fb_id}",
                        timeout=60000,
                    )
                    await page.wait_for_load_state("domcontentloaded")
                    view_el = page.locator('span:has-text("view"), span:has-text("people")')
                    if await view_el.count() > 0:
                        text = await view_el.first.inner_text()
                        import re
                        nums = re.findall(r"\d+", text.replace(",", ""))
                        if nums:
                            views = int(nums[0])
                except Exception:
                    pass

            click_data.append(
                {
                    "listing_id": listing["id"],
                    "fb_listing_id": fb_id,
                    "title": listing["title"],
                    "views": views,
                }
            )
            await asyncio.sleep(random.uniform(2, 5))

    _touch_account(account_id)
    return {"account_id": account_id, "listings": click_data}


async def open_fb_accounts(
    account_ids: list[str],
    action: str,
) -> dict:
    """Open/verify one or more FB accounts and return status."""
    results = []
    for account_id in account_ids:
        try:
            account = _get_account(account_id)
            async with _browser_manager.new_session(
                proxy=account.get("proxy"),
                cookies_json=account.get("cookies"),
            ) as session:
                logged_in = await _do_login(session, account)
                if logged_in:
                    cookies = await session.save_cookies()
                    _save_cookies(account_id, cookies)
                    _set_account_status(account_id, "active")
                    results.append(
                        {"account_id": account_id, "email": account["email"], "status": "active"}
                    )
                else:
                    _set_account_status(account_id, "idle")
                    results.append(
                        {"account_id": account_id, "email": account["email"], "status": "login_failed"}
                    )
        except Exception as e:
            results.append({"account_id": account_id, "status": "error", "error": str(e)})

    return {"results": results}
