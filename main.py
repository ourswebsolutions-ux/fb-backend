import asyncio
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# ── Windows event-loop safety ─────────────────────────────────────────────────
# Playwright requires ProactorEventLoop on Windows for create_subprocess_exec().
# Set the policy BEFORE any async code runs (module-level).
if sys.platform == "win32":
    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, asyncio.WindowsProactorEventLoopPolicy):
        print(
            "[main] Setting Windows event loop policy to "
            "WindowsProactorEventLoopPolicy (was {})".format(
                type(current_policy).__name__
            )
        )
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.routers import accounts, listings, automation, tasks, auth, inbox, websocket


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup: nothing to initialise yet ────────────────────────────────
    yield
    # ── Shutdown: clean up long-lived resources ───────────────────────────
    print("[main] Shutting down — cleaning up resources...")
    try:
        from app.routers.accounts import _stop_headless_bm
        await _stop_headless_bm()
    except Exception as e:
        print(f"[main] Cleanup error: {e}")


app = FastAPI(
    title="FB Automation Backend",
    description="Facebook Marketplace automation API with AI content generation",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
app.include_router(automation.router, prefix="/api/automation", tags=["automation"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(inbox.router, prefix="/api/inbox", tags=["inbox"])
app.include_router(websocket.router, prefix="/api", tags=["websocket"])



@app.get("/health")
async def health():
    return {"status": "ok"}
