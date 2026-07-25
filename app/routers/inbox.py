from fastapi import APIRouter, HTTPException, Query
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
    )


@router.post("/read")
async def read_inbox(body: InboxReadRequest):
    try:
        account_id = str(UUID(str(body.account_id)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid account_id UUID") from exc

    task_id = await read_inbox_messages(
        account_id=account_id,
        max_messages=body.max_messages,
    )
    return {"task_id": task_id, "message": "Inbox read task started"}


@router.post("/auto-reply")
async def auto_reply(body: InboxAutoReplyRequest):
    task_id = await auto_reply_messages(
        account_id=str(body.account_id),
        message_ids=[str(m) for m in body.message_ids] if body.message_ids else None,
        max_replies=body.max_replies,
        tone=body.tone,
        custom_instructions=body.custom_instructions,
        delay_seconds=body.delay_seconds,
    )
    return {"task_id": task_id, "message": "Auto-reply task started"}


@router.get("/{message_id}")
async def get_message(message_id: str):
    db = get_supabase()
    result = (
        db.table("inbox_messages")
        .select("*")
        .eq("id", message_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Message not found")
    return result.data[0]


@router.post("/{message_id}/reply")
async def manual_reply(message_id: str, body: InboxMessageUpdate):
    result = await send_manual_reply(
        message_id=message_id,
        reply_text=body.reply_text,
    )
    return result


@router.patch("/{message_id}")
async def update_message(message_id: str, body: dict):
    db = get_supabase()
    update_data = {}
    if "reply_status" in body:
        update_data["reply_status"] = body["reply_status"]
    if "reply_text" in body:
        update_data["reply_text"] = body["reply_text"]
    if "replied_at" in body:
        update_data["replied_at"] = body["replied_at"]
    
    if update_data:
        db.table("inbox_messages").update(update_data).eq("id", message_id).execute()
    
    return {"updated": True}


@router.delete("/{message_id}")
async def delete_message(message_id: str):
    db = get_supabase()
    db.table("inbox_messages").delete().eq("id", message_id).execute()
    return {"deleted": True}
