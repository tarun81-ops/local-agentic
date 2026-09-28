from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.api import routes_chat, routes_files, routes_organize, routes_search
from app.core.watcher import start_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    observer = start_watcher()
    yield
    if observer:
        observer.stop()
        observer.join()


app = FastAPI(title="Local File Assistant", lifespan=lifespan)


@app.middleware("http")
async def require_token(request: Request, call_next):
    if settings.api_token and request.headers.get("Authorization") != f"Bearer {settings.api_token}":
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


app.include_router(routes_search.router)
app.include_router(routes_chat.router)
app.include_router(routes_files.router)
app.include_router(routes_organize.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
