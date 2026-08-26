"""Local dev server launcher for Windows.

`uvicorn app.main:app` creates its asyncio event loop before importing the
app module, so the Windows event-loop-policy fix in
app.core.windows_compat (needed for psycopg3 async mode) runs too late if
set there alone. This script sets the policy before uvicorn ever starts a
loop. Irrelevant in Docker (Linux containers use the Dockerfile's plain
`uvicorn app.main:app` command directly).

Usage: python scripts/run_dev.py
"""

from app.core.windows_compat import apply_windows_event_loop_policy

apply_windows_event_loop_policy()

import uvicorn  # noqa: E402  (must follow the policy fix above)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
