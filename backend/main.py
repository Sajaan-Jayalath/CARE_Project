"""Local API: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000."""
import hashlib
import hmac
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import sqlite3
from contextlib import asynccontextmanager
from html import escape
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.config import BackendSettings
from backend.services.job_service import JobManager, analysis_worker
from backend.database.database import BackendError
from backend.api.upload import uploads
from backend.api.analysis import jobs
from backend.api.results import router as results_router
from backend.api.feedback import router as feedback_router
from backend.api.fixes import router as fixes_router
from fastapi.responses import JSONResponse

from backend.api.upload import router as upload_router
from backend.api.analysis import router as analysis_router
from backend.extraction.common import ExtractionError, MAX_BYTES
from backend.ai.llm.client import LLMError


class AccessInput(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class BodyLimit:
    """Bound streaming/multipart bodies before they can fill temporary storage."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        total = 0
        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > MAX_BYTES + 1_000_000:
                # HTTPException is handled by FastAPI/Starlette even while
                # parsing multipart; no raw request body is logged.
                from starlette.exceptions import HTTPException
                raise HTTPException(413, f"Request exceeds the {MAX_BYTES // 1_000_000} MB file limit plus multipart overhead.")
            return message
        await self.app(scope, limited_receive, send)



def create_app(settings=None, worker=analysis_worker, rewriter_factory=None):
    settings = settings or BackendSettings.from_env()

    def default_rewriter():
        from backend.ai.llm.rewriter import LLMRewriter
        return LLMRewriter.from_env()

    @asynccontextmanager
    async def lifespan(application):
        application.state.settings = settings
        application.state.rewriter_factory = rewriter_factory or default_rewriter
        application.state.jobs = JobManager(settings, worker)
        application.state.jobs.start()
        try:
            yield
        finally:
            application.state.jobs.close()

    application = FastAPI(title="CARE local backend", version="0.8.0", lifespan=lifespan,
        description="Text-only local CARE analysis with persistent background jobs. Images/OCR deferred.",
        docs_url=None, redoc_url=None)
    application.add_middleware(BodyLimit)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    application.add_middleware(CORSMiddleware, allow_origins=settings.origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"], allow_credentials=False,
        expose_headers=["Content-Disposition", "X-CARE-Unapplied"])

    @application.middleware("http")
    async def local_origin(request, call_next):
        origin = request.headers.get("origin")
        # Behind a tunnel the scheme differs (https outside, http here): compare host names.
        same_origin = origin and urlparse(origin).netloc == request.headers.get("host")
        if origin and origin not in settings.origins and not same_origin:
            return JSONResponse(status_code=403, content={"error": {"code": "origin_not_allowed", "message": "Browser origin is not allowed."}})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Shared access code for links handed to other people (quick tunnels have no login).
    secret = secrets.token_bytes(32)
    token = hmac.new(secret, (settings.access_code or "").encode(), hashlib.sha256).hexdigest()
    failures = {}

    def granted(request):
        return not settings.access_code or hmac.compare_digest(request.cookies.get("care_access", ""), token)

    @application.middleware("http")
    async def access_gate(request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path != "/api/access" and not granted(request):
            return JSONResponse(status_code=401, content={"status": "failed", "error": {"code": "access_required", "message": "Enter the access code to use CARE."}})
        return await call_next(request)

    @application.get("/api/access")
    def access_status(request: Request):
        return {"required": bool(settings.access_code), "granted": granted(request)}

    @application.post("/api/access")
    def grant_access(body: AccessInput, request: Request):
        client = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
        now = time.monotonic()
        recent = [t for t in failures.get(client, []) if now - t < 600]
        if len(recent) >= 10:
            raise BackendError("too_many_attempts", "Too many incorrect codes. Try again in 10 minutes.", 429)
        if not settings.access_code or not hmac.compare_digest(body.code.strip().encode(), settings.access_code.encode()):
            failures[client] = recent + [now]
            raise BackendError("access_denied", "That access code is not correct.", 403)
        failures.pop(client, None)
        response = JSONResponse({"required": True, "granted": True})
        secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        response.set_cookie("care_access", token, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
        return response

    for router in (upload_router, uploads, analysis_router, jobs, results_router, feedback_router, fixes_router):
        application.include_router(router)

    @application.exception_handler(BackendError)
    async def backend_error(request: Request, error: BackendError):
        return JSONResponse(status_code=error.status, content={"status": "failed", "error": {"code": error.code, "message": str(error)}})

    @application.exception_handler(ExtractionError)
    async def extraction_error(request: Request, error: ExtractionError):
        return JSONResponse(status_code=413 if error.code in {"file_too_large", "content_limit", "page_limit", "archive_limit"} else 422,
            content={"status": "failed", "error": {"code": error.code, "message": str(error)}})

    @application.exception_handler(LLMError)
    async def configuration_error(request: Request, error: LLMError):
        return JSONResponse(status_code=503, content={"status": "failed", "error": {"code": error.code, "message": str(error)}})

    @application.exception_handler(MemoryError)
    async def memory_error(request: Request, error: MemoryError):
        return JSONResponse(status_code=503, content={"status": "failed", "error": {"code": "insufficient_memory", "message": "Insufficient memory to handle this request."}})

    @application.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error: sqlite3.Error):
        return JSONResponse(status_code=503, content={"status": "failed", "error": {"code": "storage_unavailable", "message": "Local storage is unavailable. Check free disk space and restart the backend."}})

    @application.get("/health")
    def health(request: Request):
        supervisor = request.app.state.jobs.thread
        return {"status": "ok" if supervisor and supervisor.is_alive() else "degraded", "scope": "text-only", "inference": "local-ollama"}

    @application.get("/api/models/status")
    def model_status():
        import httpx
        from backend.ai.llm.client import Settings
        config = Settings.from_env()
        try:
            with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
                response = client.get(config.base_url + "/api/tags")
                response.raise_for_status()
                model = next((m for m in response.json()["models"] if m.get("name") == config.model), None)
                available = bool(model and model.get("size") and not model.get("remote_host") and not model.get("remote_model") and "cloud" not in config.model.lower())
                return {"available": available, "model": config.model, "code": None if available else "provider_model_unavailable",
                        "note": "Installation check only; sufficient inference memory is not guaranteed."}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"available": False, "model": config.model, "code": "provider_connection"}

    @application.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def offline_docs():
        rows = "".join("<li><code>" + escape(method.upper() + " " + path) + "</code></li>"
            for path, methods in application.openapi()["paths"].items() for method in methods)
        return '<!doctype html><html lang="en"><meta charset="utf-8"><title>CARE API</title><h1>CARE local API</h1><p>Upload content, submit a job, poll its status, then retrieve results and save feedback.</p><p><a href="/openapi.json">Full OpenAPI schema</a></p><ul>' + rows + '</ul><p>All documentation is served locally. Images and OCR are deferred.</p></html>'

    # Serve the built website from the same address when it exists (npm run build).
    site = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if (site / "index.html").is_file():
        application.mount("/", StaticFiles(directory=site, html=True), name="site")
    return application


app = create_app()
