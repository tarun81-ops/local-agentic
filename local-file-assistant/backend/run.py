"""Starts the backend. Used by the packaged app (PyInstaller) and scripts\\start-dev.ps1.
The port comes from PORT (Electron picks a free one at launch), else settings.port."""
import ipaddress
import multiprocessing
import os
import sys

import uvicorn

from app.config import settings

LOOPBACK_NAMES = {"localhost"}


def is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    # The API reads private files; it must never listen on the network, whatever HOST says.
    if not is_loopback(settings.host):
        sys.exit(f"Refusing to listen on {settings.host}: the backend only binds to 127.0.0.1.")
    from app.main import app

    uvicorn.run(
        app,
        host=settings.host,
        port=int(os.environ.get("PORT", settings.port)),
        log_level="info",
        # Access logs would write every search query (the user's questions) to the log.
        access_log=False,
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()  # needed by PyInstaller if any library spawns processes
    main()
