import os
import os
import json
import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Request
from pydantic import BaseModel
from typing import List
from groq import Groq
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.database import get_supabase
from app.models import (
    NewAccountSlowRequest,
    NewAccountSlowV2Request,
    UltraAIListingRequest,
    CreateDraftsRequest,
    RenewListingsRequest,
    RelistListingsRequest,
    DraftPublisherAIRequest,
    DeleteAllListingsRequest,
    DraftPublisherRequest,
    PublishListingRequest,
    DeleteListingRequest,
    DraftDeleteRequest,
    AdsMultiplierRequest,
    WarmupRequest,
    ProfileUpdaterRequest,
    GetClicksRequest,
    OpenAccountRequest,
    ListingAutomationRequest,
)
from app.services import fb_automation as fb

# Images uploaded via the UI are saved here so Playwright can read them from disk.
UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB per image

router = APIRouter()


def _verify_account_owner(account_id: str, user_id: str):
    """Raise 404 if the account does not belong to the current user."""
    db = get_supabase()
    result = db.table("fb_accounts").select("id").eq("id", account_id).eq("user_id", user_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Account not found")


@router.post("/new-account-slow")
async def new_account_slow(body: NewAccountSlowRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    if not body.images:
        raise HTTPException(
            status_code=400,
            detail="Please upload at least one product image.",
        )
    task_id = await fb.new_account_slow(
        account_id=str(body.account_id),
        listing_count=body.listing_count,
        delay_seconds=body.delay_seconds,
        use_ai=body.use_ai,
        product_name=body.product_name,
        category=body.category,
        condition=body.condition,
        price=body.price,
        images=body.images,
        description=getattr(body, 'description', None),
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "New account slow listing task started"}


@router.post("/new-account-slow-v2")
async def new_account_slow_v2(body: NewAccountSlowV2Request, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    if not body.images:
        raise HTTPException(
            status_code=400,
            detail="Please upload at least one product image.",
        )
    task_id = await fb.new_account_slow_v2(
        account_id=str(body.account_id),
        listing_count=body.listing_count,
        delay_seconds=body.delay_seconds,
        use_ai=body.use_ai,
        product_name=body.product_name,
        category=body.category,
        condition=body.condition,
        price=body.price,
        images=body.images,
        warmup_before=body.warmup_before,
        warmup_steps=body.warmup_steps,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "New account slow V2 listing task started"}


@router.post("/ultra-ai-listings")
async def ultra_ai_listings(body: UltraAIListingRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    if not body.images:
        raise HTTPException(
            status_code=400,
            detail="Please upload at least one product image.",
        )
    task_id = await fb.ultra_ai_listings(
        account_id=str(body.account_id),
        listing_count=body.listing_count,
        product_name=body.product_name,
        category=body.category,
        condition=body.condition,
        price=body.price,
        images=body.images,
        extra_details=body.extra_details,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": f"Ultra AI listings task started (up to {body.listing_count} listings)"}


@router.post("/create-drafts")
async def create_drafts(body: CreateDraftsRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    if not body.images:
        raise HTTPException(
            status_code=400,
            detail="Please upload at least one product image.",
        )
    task_id = await fb.create_only_drafts(
        account_id=str(body.account_id),
        draft_count=body.draft_count,
        title=body.title,
        description=body.description,
        price=body.price,
        category=body.category,
        condition=body.condition,
        images=body.images,
        use_ai=body.use_ai,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Create drafts task started"}


@router.post("/renew-listings")
async def renew_listings(body: RenewListingsRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.renew_listings(
        account_id=str(body.account_id),
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        max_renew=body.max_renew,
        delay_seconds=body.delay_seconds,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Renew listings task started"}


@router.post("/relist-listings")
async def relist_listings(body: RelistListingsRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.relist_listings(
        account_id=str(body.account_id),
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        max_relist=body.max_relist,
        delay_seconds=body.delay_seconds,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Relist listings task started"}


@router.post("/draft-publisher-ai")
async def draft_publisher_ai(body: DraftPublisherAIRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.draft_publisher_ai(
        account_id=str(body.account_id),
        draft_ids=[str(i) for i in body.draft_ids] if body.draft_ids else None,
        max_publish=body.max_publish,
        delay_seconds=body.delay_seconds,
        improve_with_ai=body.improve_with_ai,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Draft publisher with AI task started"}


@router.post("/delete-all-listings")
async def delete_all_listings(body: DeleteAllListingsRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to execute this destructive operation",
        )
    task_id = await fb.delete_all_listings(
        account_id=str(body.account_id),
        status_filter=body.status_filter,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Delete all listings task started"}


@router.post("/draft-publisher")
async def draft_publisher(body: DraftPublisherRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.draft_publisher(
        account_id=str(body.account_id),
        draft_ids=[str(i) for i in body.draft_ids] if body.draft_ids else None,
        max_publish=body.max_publish,
        delay_seconds=body.delay_seconds,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Draft publisher task started"}


@router.post("/draft-delete")
async def draft_delete(body: DraftDeleteRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to execute this destructive operation",
        )
    task_id = await fb.draft_delete(
        account_id=str(body.account_id),
        draft_ids=[str(i) for i in body.draft_ids] if body.draft_ids else None,
        max_delete=body.max_delete,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Draft delete task started"}


@router.post("/publish-listing")
async def publish_listing(body: PublishListingRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.publish_listing(
        account_id=str(body.account_id),
        listing_id=str(body.listing_id),
        delay_seconds=body.delay_seconds,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Publish listing task started"}


@router.post("/delete-listing")
async def delete_listing(body: DeleteListingRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.delete_listing(
        account_id=str(body.account_id),
        listing_id=str(body.listing_id),
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Delete listing task started"}


@router.post("/ads-multiplier")
async def ads_multiplier(body: AdsMultiplierRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.ads_multiplier(
        account_id=str(body.account_id),
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        multiplier=body.multiplier,
        delay_seconds=body.delay_seconds,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": f"Ads multiplier task started (x{body.multiplier})"}


@router.post("/warmup")
async def warmup(body: WarmupRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.fb_warmup(
        account_id=str(body.account_id),
        duration_minutes=body.duration_minutes,
        actions_per_minute=body.actions_per_minute,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Account warmup task started"}


@router.post("/profile-updater")
async def profile_updater(body: ProfileUpdaterRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.fb_profile_updater(
        account_id=str(body.account_id),
        name=body.name,
        bio=body.bio,
        location=body.location,
        hometown=body.hometown,
        workplace=body.workplace,
        job_title=body.job_title,
        school=body.school,
        profile_pic_url=body.profile_pic_url,
        cover_pic_url=body.cover_pic_url,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Profile updater task started"}


@router.post("/get-clicks")
async def get_clicks(body: GetClicksRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    try:
        result = await fb.get_clicks_on_marketplace(
            account_id=str(body.account_id),
            listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/open-accounts")
async def open_accounts(body: OpenAccountRequest, user=Depends(get_current_user)):
    # Verify all requested accounts belong to this user
    db = get_supabase()
    for acc_id in body.account_ids:
        result = db.table("fb_accounts").select("id").eq("id", acc_id).eq("user_id", user.id).limit(1).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail=f"Account {acc_id} not found")
    result = await fb.open_fb_accounts(
        account_ids=[str(i) for i in body.account_ids],
        action=body.action,
    )
    return result


@router.post("/listing-automation")
async def listing_automation(body: ListingAutomationRequest, user=Depends(get_current_user)):
    _verify_account_owner(str(body.account_id), user.id)
    task_id = await fb.listing_automation(
        account_id=str(body.account_id),
        workflow_type=body.workflow_type,
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        max_listings=body.max_listings,
        delay_seconds=body.delay_seconds,
        schedule_time=body.schedule_time,
        repeat_interval=body.repeat_interval,
        repeat_until=body.repeat_until,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": f"Listing automation task started ({body.workflow_type})"}


# ── Image upload ──────────────────────────────────────────────────────────────

@router.post("/upload-images")
async def upload_images(request: Request, files: List[UploadFile] = File(...)):
    """
    Accept multipart image uploads from the frontend.
    Saves files to disk under /uploads/ and returns public URLs.

    Validates:
    - At least one file provided
    - MIME type is an image (jpeg / png / webp / gif)
    - File size ≤ 15 MB per image
    - Max 50 images per request
    """
    if not files:
        raise HTTPException(status_code=400, detail="Please upload at least one product image.")

    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 images per upload.")

    saved_paths: list[str] = []
    public_urls: list[str] = []

    # Build base URL from request
    base_url = str(request.base_url).rstrip('/')

    for upload in files:
        content_type = upload.content_type or ""
        if content_type not in ALLOWED_MIME:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type '{content_type}' for '{upload.filename}'. "
                       "Only JPEG, PNG, WEBP, and GIF images are accepted.",
            )

        # Read into memory so we can check size before writing to disk
        data = await upload.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Image '{upload.filename}' exceeds the 15 MB size limit.",
            )

        # Build a collision-proof filename
        ext = Path(upload.filename or "image").suffix or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / filename

        with open(dest, "wb") as f:
            f.write(data)

        saved_paths.append(str(UPLOAD_DIR / filename))
        public_urls.append(f"{base_url}/uploads/{filename}")
        print(f"[upload_images] Saved '{upload.filename}' → {dest} ({len(data) // 1024} KB)")

    print(f"[upload_images] {len(saved_paths)} image(s) uploaded successfully")
    return {"paths": public_urls, "count": len(public_urls)}

# ── Image upload ──────────────────────────────────────────────────────────────

@router.post("/upload-images")
async def upload_images(request: Request, files: List[UploadFile] = File(...)):
    """
    Accept multipart image uploads from the frontend.
    No auth required — images are server-local paths used by Playwright.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Please upload at least one product image.")

    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 images per upload.")

    saved_paths: list[str] = []
    public_urls: list[str] = []
    base_url = str(request.base_url).rstrip('/')

    for upload in files:
        content_type = upload.content_type or ""
        if content_type not in ALLOWED_MIME:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type '{content_type}' for '{upload.filename}'. "
                       "Only JPEG, PNG, WEBP, and GIF images are accepted.",
            )

        data = await upload.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Image '{upload.filename}' exceeds the 15 MB size limit.",
            )

        ext = Path(upload.filename or "image").suffix or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        dest = UPLOAD_DIR / filename

        with open(dest, "wb") as f:
            f.write(data)

        saved_paths.append(str(UPLOAD_DIR / filename))
        public_urls.append(f"{base_url}/uploads/{filename}")
        print(f"[upload_images] Saved '{upload.filename}' → {dest} ({len(data) // 1024} KB)")

    print(f"[upload_images] {len(saved_paths)} image(s) uploaded successfully")
    return {"paths": public_urls, "count": len(public_urls)}


@router.post("/cleanup-uploads")
async def cleanup_old_uploads():
    """
    Manually trigger cleanup of uploaded images older than 7 days.
    Also runs automatically on every backend startup.
    """
    import time
    cutoff = time.time() - (7 * 24 * 60 * 60)
    deleted = 0
    errors = 0
    for f in UPLOAD_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except Exception:
            errors += 1

    total = sum(1 for f in UPLOAD_DIR.iterdir() if f.is_file())
    return {
        "deleted": deleted,
        "remaining": total,
        "errors": errors,
        "message": f"Deleted {deleted} file(s) older than 7 days. {total} file(s) remaining.",
    }


# ── AI Product Generator ──────────────────────────────────────────────────────

class ProductGenerateRequest(BaseModel):
    idea: str

@router.post("/generate-product")
async def generate_product(body: ProductGenerateRequest, user=Depends(get_current_user)):
    if not body.idea or not body.idea.strip():
        raise HTTPException(status_code=400, detail="Product idea is required.")

    api_key = settings.groq_api_key
    if not api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured.")

    try:
        client = Groq(api_key=api_key)
        prompt = f"""You are a professional product copywriter.
Generate:
1. A product title of ONLY 4-5 words.
2. A product description of ONLY 4-5 short sentences.

Rules:
- Professional tone.
- Do not use bullet points.
- Do not use markdown.
- Return ONLY valid JSON.

JSON Format:
{{
  "title": "",
  "description": ""
}}

Product Idea:
{body.idea.strip()}"""

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.5,
            messages=[
                {"role": "system", "content": "You generate concise product titles and descriptions. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ]
        )

        content = response.choices[0].message.content.strip()
        # Strip markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        data = json.loads(content)
        return {"success": True, "title": data.get("title", ""), "description": data.get("description", "")}

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="AI returned invalid JSON. Try again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
