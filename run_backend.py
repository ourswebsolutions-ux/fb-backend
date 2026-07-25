import asyncio
import sys

if sys.platform == "win32":
    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, asyncio.WindowsProactorEventLoopPolicy):
        print(
            "[run_backend] Setting Windows event loop policy to "
            "WindowsProactorEventLoopPolicy (was {})".format(
                type(current_policy).__name__
            )
        )
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        # reload=True,
        reload_dirs=["app"],
        reload_excludes=["*.png", "*.jpg", "*.jpeg", "*.html", "*.log", "debug_*", "uploads/*", "scratch/*", "*.json"],
    )
