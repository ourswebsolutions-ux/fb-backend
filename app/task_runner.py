"""
Background task runner and helpers.
Tasks are persisted in Supabase `tasks` table so progress survives restarts.
"""

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any, Coroutine

from app.core.database import get_supabase


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_background_task(coro: Coroutine, task_id: str | None = None):
    """
    Schedule a coroutine as a background task with proper error logging.
    Replaces bare asyncio.create_task() calls which silently fail on Windows.
    """
    async def _wrapper():
        try:
            await coro
        except Exception as e:
            print(f"[background_task] UNHANDLED EXCEPTION (task_id={task_id}):")
            traceback.print_exc()
            # Mark task as failed in DB if we have a task_id
            if task_id:
                try:
                    db = get_supabase()
                    db.table("tasks").update({
                        "status": "failed",
                        "error": f"{type(e).__name__}: {str(e)}",
                        "finished_at": _now(),
                    }).eq("id", task_id).execute()
                except Exception:
                    pass

    asyncio.create_task(_wrapper())


async def create_task(task_type: str, input_data: dict, user_id=None) -> str:
    db = get_supabase()
    payload = {"type": task_type, "status": "pending", "input": input_data}
    if user_id:
        payload["user_id"] = user_id
    result = db.table("tasks").insert(payload).execute()
    return result.data[0]["id"]


async def update_task(
    task_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    completed_steps: int | None = None,
    total_steps: int | None = None,
    result: dict | None = None,
    error: str | None = None,
    started_at: bool = False,
    finished_at: bool = False,
):
    db = get_supabase()
    patch: dict[str, Any] = {}
    if status is not None:
        patch["status"] = status
    if progress is not None:
        patch["progress"] = min(100, max(0, progress))
    if completed_steps is not None:
        patch["completed_steps"] = completed_steps
    if total_steps is not None:
        patch["total_steps"] = total_steps
    if result is not None:
        patch["result"] = result
    if error is not None:
        patch["error"] = error
    if started_at:
        patch["started_at"] = _now()
    if finished_at:
        patch["finished_at"] = _now()
    if patch:
        db.table("tasks").update(patch).eq("id", task_id).execute()


async def write_log(
    action: str,
    *,
    task_id: str | None = None,
    account_id: str | None = None,
    status: str = "success",
    details: dict | None = None,
    error: str | None = None,
    user_id=None,
):
    db = get_supabase()
    payload = {
        "task_id": task_id,
        "account_id": account_id,
        "action": action,
        "status": status,
        "details": details or {},
        "error": error,
    }
    if user_id:
        payload["user_id"] = user_id
    db.table("automation_logs").insert(payload).execute()
