import asyncio
import sys


def apply_windows_event_loop_policy() -> None:
    """psycopg3's async mode cannot run under Windows' default
    ProactorEventLoop; it requires the SelectorEventLoop policy. This is a
    no-op on non-Windows platforms (i.e. the Linux containers this app is
    deployed in), and only matters when running the backend directly on a
    Windows host (e.g. local `uvicorn` outside Docker, or the test suite).
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
