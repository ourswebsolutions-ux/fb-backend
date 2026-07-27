from fastapi import APIRouter, HTTPException, Query, Depends
from typing import List, Optional
from pydantic import BaseModel
from app.core.database import get_supabase
from app.core.deps import get_current_user

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────

class BulkDeleteLogsRequest(BaseModel):
    log_ids: List[str]


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_tasks(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(get_current_user),
):
    db = get_supabase()
    query = (
        db.table("tasks")
        .select("*")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status:
        query = query.eq("status", status)
    if type:
        query = query.eq("type", type)
    result = query.execute()
    return result.data


@router.post("/cleanup-stuck")
async def cleanup_stuck_tasks(user=Depends(get_current_user)):
    """Mark all tasks stuck in running/pending as failed (only for current user)."""
    db = get_supabase()
    result = (
        db.table("tasks")
        .update({"status": "failed", "error": "Task was orphaned after backend restart"})
        .eq("user_id", user.id)
        .in_("status", ["running", "pending"])
        .execute()
    )
    count = len(result.data) if result.data else 0
    return {"cleaned": count, "message": f"{count} stuck tasks marked as failed"}


@router.get("/{task_id}")
async def get_task(task_id: str, user=Depends(get_current_user)):
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("*")
        .eq("id", task_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    return result.data[0]


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, user=Depends(get_current_user)):
    db = get_supabase()
    existing = (
        db.table("tasks")
        .select("id")
        .eq("id", task_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Task not found")
    result = db.table("tasks").update({"status": "cancelled"}).eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    return result.data[0]


@router.get("/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    user=Depends(get_current_user),
):
    db = get_supabase()
    task_check = (
        db.table("tasks")
        .select("id")
        .eq("id", task_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not task_check.data:
        raise HTTPException(status_code=404, detail="Task not found")

    result = (
        db.table("automation_logs")
        .select("*")
        .eq("task_id", task_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


# ── Automation Logs ───────────────────────────────────────────────────────────

@router.get("/logs/all")
async def get_all_logs(
    account_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(default=500, ge=1, le=1000),
    user=Depends(get_current_user),
):
    db = get_supabase()
    query = (
        db.table("automation_logs")
        .select("*")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if account_id:
        query = query.eq("account_id", account_id)
    if action:
        query = query.eq("action", action)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data


@router.delete("/logs/all")
async def delete_all_logs(user=Depends(get_current_user)):
    """Delete ALL automation logs for the current user."""
    db = get_supabase()
    db.table("automation_logs").delete().eq("user_id", user.id).execute()
    return {"deleted": True, "message": "All logs deleted"}


@router.delete("/logs/bulk")
async def delete_logs_bulk(body: BulkDeleteLogsRequest, user=Depends(get_current_user)):
    """Delete specific log entries by ID (only logs belonging to current user)."""
    db = get_supabase()
    if not body.log_ids:
        raise HTTPException(status_code=400, detail="No log IDs provided")

    db.table("automation_logs").delete().in_("id", body.log_ids).eq("user_id", user.id).execute()
    return {"deleted": True, "count": len(body.log_ids)}


@router.delete("/logs/{log_id}")
async def delete_log(log_id: str, user=Depends(get_current_user)):
    """Delete a single automation log entry."""
    db = get_supabase()
    existing = (
        db.table("automation_logs")
        .select("id")
        .eq("id", log_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Log entry not found")
    db.table("automation_logs").delete().eq("id", log_id).execute()
    return {"deleted": True}
