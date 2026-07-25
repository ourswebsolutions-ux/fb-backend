from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.models import ListingCreate, ListingUpdate
from app.core.database import get_supabase

router = APIRouter()


@router.get("/")
async def list_listings(
    account_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    db = get_supabase()
    query = db.table("listings").select("*").order("created_at", desc=True).range(offset, offset + limit - 1)
    if account_id:
        query = query.eq("account_id", account_id)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data


@router.post("/")
async def create_listing(body: ListingCreate):
    db = get_supabase()
    data = body.model_dump()
    if data.get("account_id"):
        data["account_id"] = str(data["account_id"])
    result = db.table("listings").insert(data).execute()
    return result.data[0]


@router.get("/{listing_id}")
async def get_listing(listing_id: str):
    db = get_supabase()
    result = db.table("listings").select("*").eq("id", listing_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Listing not found")
    return result.data[0]


@router.patch("/{listing_id}")
async def update_listing(listing_id: str, body: ListingUpdate):
    db = get_supabase()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = db.table("listings").update(patch).eq("id", listing_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Listing not found")
    return result.data[0]


@router.delete("/{listing_id}")
async def delete_listing(listing_id: str):
    db = get_supabase()
    db.table("listings").update({"status": "deleted"}).eq("id", listing_id).execute()
    return {"deleted": True}
