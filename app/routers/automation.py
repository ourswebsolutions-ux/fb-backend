import os
import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import List
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


@router.post("/new-account-slow")
async def new_account_slow(body: NewAccountSlowRequest):
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
    )
    return {"task_id": task_id, "message": "New account slow listing task started"}


@router.post("/new-account-slow-v2")
async def new_account_slow_v2(body: NewAccountSlowV2Request):
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
    )
    return {"task_id": task_id, "message": "New account slow V2 listing task started"}


@router.post("/ultra-ai-listings")
async def ultra_ai_listings(body: UltraAIListingRequest):
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
    )
    return {"task_id": task_id, "message": f"Ultra AI listings task started (up to {body.listing_count} listings)"}


@router.post("/create-drafts")
async def create_drafts(body: CreateDraftsRequest):
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
    )
    return {"task_id": task_id, "message": "Create drafts task started"}


@router.post("/renew-listings")
async def renew_listings(body: RenewListingsRequest):
    task_id = await fb.renew_listings(
        account_id=str(body.account_id),
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        max_renew=body.max_renew,
        delay_seconds=body.delay_seconds,
    )
    return {"task_id": task_id, "message": "Renew listings task started"}


@router.post("/relist-listings")
async def relist_listings(body: RelistListingsRequest):
    task_id = await fb.relist_listings(
        account_id=str(body.account_id),
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        max_relist=body.max_relist,
        delay_seconds=body.delay_seconds,
    )
    return {"task_id": task_id, "message": "Relist listings task started"}


@router.post("/draft-publisher-ai")
async def draft_publisher_ai(body: DraftPublisherAIRequest):
    task_id = await fb.draft_publisher_ai(
        account_id=str(body.account_id),
        draft_ids=[str(i) for i in body.draft_ids] if body.draft_ids else None,
        max_publish=body.max_publish,
        delay_seconds=body.delay_seconds,
        improve_with_ai=body.improve_with_ai,
    )
    return {"task_id": task_id, "message": "Draft publisher with AI task started"}


@router.post("/delete-all-listings")
async def delete_all_listings(body: DeleteAllListingsRequest):
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to execute this destructive operation",
        )
    task_id = await fb.delete_all_listings(
        account_id=str(body.account_id),
        status_filter=body.status_filter,
    )
    return {"task_id": task_id, "message": "Delete all listings task started"}


@router.post("/draft-publisher")
async def draft_publisher(body: DraftPublisherRequest):
    task_id = await fb.draft_publisher(
        account_id=str(body.account_id),
        draft_ids=[str(i) for i in body.draft_ids] if body.draft_ids else None,
        max_publish=body.max_publish,
        delay_seconds=body.delay_seconds,
    )
    return {"task_id": task_id, "message": "Draft publisher task started"}


@router.post("/draft-delete")
async def draft_delete(body: DraftDeleteRequest):
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to execute this destructive operation",
        )
    task_id = await fb.draft_delete(
        account_id=str(body.account_id),
        draft_ids=[str(i) for i in body.draft_ids] if body.draft_ids else None,
        max_delete=body.max_delete,
    )
    return {"task_id": task_id, "message": "Draft delete task started"}


@router.post("/publish-listing")
async def publish_listing(body: PublishListingRequest):
    task_id = await fb.publish_listing(
        account_id=str(body.account_id),
        listing_id=str(body.listing_id),
        delay_seconds=body.delay_seconds,
    )
    return {"task_id": task_id, "message": "Publish listing task started"}


@router.post("/delete-listing")
async def delete_listing(body: DeleteListingRequest):
    task_id = await fb.delete_listing(
        account_id=str(body.account_id),
        listing_id=str(body.listing_id),
    )
    return {"task_id": task_id, "message": "Delete listing task started"}


@router.post("/ads-multiplier")
async def ads_multiplier(body: AdsMultiplierRequest):
    task_id = await fb.ads_multiplier(
        account_id=str(body.account_id),
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        multiplier=body.multiplier,
        delay_seconds=body.delay_seconds,
    )
    return {"task_id": task_id, "message": f"Ads multiplier task started (x{body.multiplier})"}


@router.post("/warmup")
async def warmup(body: WarmupRequest):
    task_id = await fb.fb_warmup(
        account_id=str(body.account_id),
        duration_minutes=body.duration_minutes,
        actions_per_minute=body.actions_per_minute,
    )
    return {"task_id": task_id, "message": "Account warmup task started"}


@router.post("/profile-updater")
async def profile_updater(body: ProfileUpdaterRequest):
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
    )
    return {"task_id": task_id, "message": "Profile updater task started"}


@router.post("/get-clicks")
async def get_clicks(body: GetClicksRequest):
    try:
        result = await fb.get_clicks_on_marketplace(
            account_id=str(body.account_id),
            listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/open-accounts")
async def open_accounts(body: OpenAccountRequest):
    result = await fb.open_fb_accounts(
        account_ids=[str(i) for i in body.account_ids],
        action=body.action,
    )
    return result


@router.post("/listing-automation")
async def listing_automation(body: ListingAutomationRequest):
    task_id = await fb.listing_automation(
        account_id=str(body.account_id),
        workflow_type=body.workflow_type,
        listing_ids=[str(i) for i in body.listing_ids] if body.listing_ids else None,
        max_listings=body.max_listings,
        delay_seconds=body.delay_seconds,
        schedule_time=body.schedule_time,
        repeat_interval=body.repeat_interval,
        repeat_until=body.repeat_until,
    )
    return {"task_id": task_id, "message": f"Listing automation task started ({body.workflow_type})"}


# ── Image upload ──────────────────────────────────────────────────────────────

@router.post("/upload-images")
async def upload_images(files: List[UploadFile] = File(...)):
    """
    Accept multipart image uploads from the frontend.
    Saves files to disk under /uploads/ and returns the absolute paths
    that Playwright will use to set_input_files().

    Validates:
    - At least one file provided
    - MIME type is an image (jpeg / png / webp / gif)
    - File size ≤ 15 MB per image
    - Max 10 images per request
    """
    if not files:
        raise HTTPException(status_code=400, detail="Please upload at least one product image.")

    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 images per upload.")

    saved_paths: list[str] = []

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

        saved_paths.append(str(dest.resolve()))
        print(f"[upload_images] Saved '{upload.filename}' → {dest} ({len(data) // 1024} KB)")

    print(f"[upload_images] {len(saved_paths)} image(s) uploaded successfully")
    return {"paths": saved_paths, "count": len(saved_paths)}
