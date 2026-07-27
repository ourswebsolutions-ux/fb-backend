from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from uuid import UUID
from app.models import (
    InboxReadRequest,
    InboxAutoReplyRequest,
    InboxMessageUpdate,
)
from app.services.inbox_automation import (
    read_inbox_messages,
    auto_reply_messages,
    get_inbox_messages,
    send_manual_reply,
)
from app.core.database import get_supabase
from app.core.deps import get_current_user

router = APIRouter()


@router.get("/")
async def list_messages(
    account_id: Optional[str] = Query(None),
    reply_status: Optional[str] = Query(None),
    limit: int = Query(default=50, ge=1, le=500),
    include_unassigned: bool = Query(
        False,
        description="When filtering by account_id, also return messages with a null account_id",
    ),
    user=Depends(get_current_user),
):
    normalized_account_id: Optional[str] = None
    if account_id:
        try:
            normalized_account_id = str(UUID(account_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid account_id UUID") from exc

    return await get_inbox_messages(
        account_id=normalized_account_id,
        reply_status=reply_status,
        limit=limit,
        include_unassigned=include_unassigned,
        user_id=user.id,
    )


@router.post("/read")
async def read_inbox(body: InboxReadRequest, user=Depends(get_current_user)):
    # Verify the account belongs to this user
    db = get_supabase()
    account_id = str(UUID(str(body.account_id)))
    acc = db.table("fb_accounts").select("id").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not acc.data:
        raise HTTPException(status_code=404, detail="Account not found")

    task_id = await read_inbox_messages(
        account_id=account_id,
        max_messages=body.max_messages,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Inbox read task started"}


@router.post("/auto-reply")
async def auto_reply(body: InboxAutoReplyRequest, user=Depends(get_current_user)):
    # Verify the account belongs to this user
    db = get_supabase()
    account_id = str(UUID(str(body.account_id)))
    acc = db.table("fb_accounts").select("id").eq("id", account_id).eq("user_id", user.id).limit(1).execute()
    if not acc.data:
        raise HTTPException(status_code=404, detail="Account not found")

    task_id = await auto_reply_messages(
        account_id=account_id,
        message_ids=[str(m) for m in body.message_ids] if body.message_ids else None,
        max_replies=body.max_replies,
        tone=body.tone,
        custom_instructions=body.custom_instructions,
        delay_seconds=body.delay_seconds,
        user_id=user.id,
    )
    return {"task_id": task_id, "message": "Auto-reply task started"}


@router.get("/{message_id}")
async def get_message(message_id: str, user=Depends(get_current_user)):
    db = get_supabase()
    result = (
        db.table("inbox_messages")
        .select("*")
        .eq("id", message_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Message not found")
    return result.data[0]


@router.post("/{message_id}/reply")
async def manual_reply(message_id: str, body: InboxMessageUpdate, user=Depends(get_current_user)):
    # Verify ownership
    db = get_supabase()
    existing = (
        db.table("inbox_messages")
        .select("id")
        .eq("id", message_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Message not found")

    result = await send_manual_reply(
        message_id=message_id,
        reply_text=body.reply_text,
    )
    return result


@router.patch("/{message_id}")
async def update_message(message_id: str, body: dict, user=Depends(get_current_user)):
    db = get_supabase()
    existing = (
        db.table("inbox_messages")
        .select("id")
        .eq("id", message_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Message not found")

    update_data = {}
    if "reply_status" in body:
        update_data["reply_status"] = body["reply_status"]
    if "reply_text" in body:
        update_data["reply_text"] = body["reply_text"]
    if "replied_at" in body:
        update_data["replied_at"] = body["replied_at"]

    if update_data:
        db.table("inbox_messages").update(update_data).eq("id", message_id).eq("user_id", user.id).execute()

    return {"updated": True}


@router.delete("/{message_id}")
async def delete_message(message_id: str, user=Depends(get_current_user)):
    db = get_supabase()
    existing = (
        db.table("inbox_messages")
        .select("id")
        .eq("id", message_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Message not found")
    db.table("inbox_messages").delete().eq("id", message_id).eq("user_id", user.id).execute()
    return {"deleted": True}
