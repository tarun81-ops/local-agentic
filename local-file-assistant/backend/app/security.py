"""The API is bound to 127.0.0.1, but any web page open in the user's browser can still send
requests to localhost. So every request needs the bearer token, and browser requests must
come from an allowed Origin. There is no "no token" mode."""
import logging
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings

log = logging.getLogger(__name__)

TOKEN_FILE = "api_token.txt"
# /health answers "is the backend up yet" for the app while it starts; it reveals nothing.
PUBLIC_PATHS = {"/health"}
# The app always calls http://127.0.0.1:<port>. Checking the Host header blocks DNS-rebinding
# (a web page whose own domain has been re-pointed at 127.0.0.1) before anything else runs.
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def host_allowed(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8756
        host = host.split("]")[0] + "]"
    elif host.count(":") == 1:  # 127.0.0.1:8756
        host = host.split(":")[0]
    return host in ALLOWED_HOSTS


def resolve_token(data_dir: Path) -> str:
    """Uses API_TOKEN when set (Electron passes a fresh one per launch). Otherwise generates
    one and writes it to the data dir, so a backend started on its own is still protected."""
    if settings.api_token:
        return settings.api_token
    token = secrets.token_hex(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / TOKEN_FILE).write_text(token, encoding="utf-8")
    log.warning("No API_TOKEN set. Generated one for this run, saved to %s", data_dir / TOKEN_FILE)
    print(f"API token for this run: {token}", flush=True)
    return token


def make_guard(token: str, allowed_origins: list[str]):
    async def guard(request: Request, call_next):
        if not host_allowed(request.headers.get("host")):
            return JSONResponse(status_code=421, content={"detail": "host not allowed"})
        # CORS preflights carry no Authorization header; CORSMiddleware (outermost) answers them.
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {token}"):
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)

    return guard
