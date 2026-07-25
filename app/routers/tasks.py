from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.core.database import get_supabase

router = APIRouter()


@router.get("/")
async def list_tasks(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(default=50, ge=1, le=200),
):
    db = get_supabase()
    query = db.table("tasks").select("*").order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    if type:
        query = query.eq("type", type)
    result = query.execute()
    return result.data


@router.get("/logs/all")
async def get_all_logs(
    account_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    db = get_supabase()
    query = db.table("automation_logs").select("*").order("created_at", desc=True).limit(limit)
    if account_id:
        query = query.eq("account_id", account_id)
    if action:
        query = query.eq("action", action)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data


@router.post("/cleanup-stuck")
async def cleanup_stuck_tasks():
    """Mark all tasks stuck in running/pending as failed.
    Call this after a backend restart to clear orphaned tasks."""
    db = get_supabase()
    result = (
        db.table("tasks")
        .update({"status": "failed", "error": "Task was orphaned after backend restart"})
        .in_("status", ["running", "pending"])
        .execute()
    )
    count = len(result.data) if result.data else 0
    return {"cleaned": count, "message": f"{count} stuck tasks marked as failed"}


@router.get("/{task_id}")
async def get_task(task_id: str):
    db = get_supabase()
    result = db.table("tasks").select("*").eq("id", task_id).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    return result.data[0]


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    db = get_supabase()
    result = db.table("tasks").update({"status": "cancelled"}).eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    return result.data[0]


@router.get("/{task_id}/logs")
async def get_task_logs(task_id: str, limit: int = Query(default=100, ge=1, le=1000)):
    db = get_supabase()
    result = (
        db.table("automation_logs")
        .select("*")
        .eq("task_id", task_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
