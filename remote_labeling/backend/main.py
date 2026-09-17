"""Application factory with one API process and one supervised model slot."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .api.schemas import (
    CommitRequest,
    DraftRequest,
    Exchange,
    ExportRequest,
    InferenceRequest,
    LeaseRequest,
    ShareRequest,
    SlotRequest,
    SampleView,
    SessionView,
    SlotView,
    JobView,
    ModelView,
    OpeningSelectionView,
)
from .application.exports import ExportService
from .application.inference import InferenceService
from .application.service import AnnotationService, digest
from .config import Settings
from .domain.rules import DomainError
from .infrastructure.inference.devices import devices
from .infrastructure.persistence.sqlite import SQLiteStore

logger = logging.getLogger("realisr.remote")


def create_app(settings: Settings) -> FastAPI:
    """Construct the standalone application without importing Qt or ONNX."""
    store = SQLiteStore(settings.state_dir, settings.sqlite_journal_mode)
    service = AnnotationService(settings, store)
    shutdown_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.preflight()
        if not store.path.exists():
            raise RuntimeError("请先运行 init 初始化服务")
        store.acquire_instance()
        inference = exports = None
        try:
            await run_in_threadpool(store.initialize)
            await run_in_threadpool(service.sync_owner_credential)
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            with store.transaction() as db:
                db.execute("DELETE FROM leases")
                db.execute(
                    "UPDATE datasets SET status='invalid',errors='[ {\"message\": \"扫描被服务重启中断，请重新扫描\"} ]' WHERE status='scanning'"
                )
                db.execute(
                    "UPDATE jobs SET state='interrupted',error='服务重启',updated=? WHERE state IN ('queued','running')",
                    (time.time(),),
                )
                db.execute(
                    "UPDATE exports SET state='interrupted',error='服务重启' WHERE state IN ('queued','running')"
                )
            inference = await run_in_threadpool(InferenceService, service)
            exports = ExportService(service)
            app.state.inference = inference
            app.state.exports = exports
            inference.start()
            yield
        finally:
            if inference is not None and inference.thread.ident is not None:
                await run_in_threadpool(inference.close)
            if exports is not None:
                await run_in_threadpool(exports.close)
            store.release_instance()

    app = FastAPI(title="Real-ISR Remote", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.shutdown_event = shutdown_event

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(
        request: Request, exc: sqlite3.Error
    ) -> JSONResponse:
        """Never turn a database failure into a successful save indication."""
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "storage_unavailable",
                    "message": "存储暂不可用，请保留当前内容并重试",
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation",
                    "message": "请求字段格式无效",
                    "details": [
                        {"field": list(e["loc"]), "message": e["msg"]}
                        for e in exc.errors()
                    ],
                    "request_id": request.state.request_id,
                }
            },
        )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        request.state.request_id = secrets.token_hex(12)
        started = time.monotonic()
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") != settings.public_origin.rstrip(
                "/"
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "origin",
                            "message": "写请求来源不匹配",
                            "request_id": request.state.request_id,
                        }
                    },
                )
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "body_too_large",
                                "message": "请求内容过大",
                            }
                        },
                    )
            request._body = bytes(body)
        try:
            response = await call_next(request)
        except OSError:
            logger.exception("storage request=%s", request.state.request_id)
            response = JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "storage_unavailable",
                        "message": "存储操作失败，请保留当前内容并重试",
                        "request_id": request.state.request_id,
                    }
                },
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Server-Timing"] = (
            f"app;dur={(time.monotonic()-started)*1000:.2f}"
        )
        if (
            request.url.path.startswith("/api/")
            and "/images/" not in request.url.path
        ):
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request=%s method=%s status=%s elapsed_ms=%.2f",
            request.state.request_id,
            request.method,
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return response

    def client_ip(request: Request) -> str:
        """Use the direct transport peer, never a client-supplied header."""
        return request.client.host if request.client else ""

    def session(request: Request) -> str:
        """Check the request IP before handing a cookie to a use case."""
        sid = digest(request.cookies.get("realisr_session", ""))
        service.check_session_ip(sid, client_ip(request))
        return sid

    prefix = "/api/v1"

    def set_session_cookie(response: Response, cookie: str) -> None:
        """Use the same cookie policy for token and remembered-IP logins."""
        response.set_cookie(
            "realisr_session",
            cookie,
            httponly=True,
            samesite="strict",
            secure=settings.public_origin.startswith("https://"),
            max_age=settings.session_seconds,
        )

    @app.post(prefix + "/session", response_model=SessionView)
    def exchange(request: Request, body: Exchange, response: Response) -> dict:
        cookie, _ = service.exchange(
            body.token, body.nickname, client_ip(request)
        )
        set_session_cookie(response, cookie)
        return service.whoami(digest(cookie))

    @app.post(prefix + "/session/restore", response_model=SessionView)
    def restore_session(request: Request, response: Response) -> dict:
        """Resume an existing session or log in through a remembered owner IP."""
        cookie, user = service.restore_session(
            digest(request.cookies.get("realisr_session", "")),
            client_ip(request),
        )
        if cookie is not None:
            set_session_cookie(response, cookie)
        return service.whoami(user["session_id"])

    @app.get(prefix + "/session", response_model=SessionView)
    def whoami(request: Request) -> dict:
        return service.whoami(session(request))

    @app.delete(prefix + "/session")
    def logout(request: Request, response: Response) -> dict:
        sid = session(request)
        service.logout(sid)
        response.delete_cookie("realisr_session")
        return {"logged_out": True}

    @app.get(prefix + "/datasets")
    def datasets(request: Request) -> list[dict]:
        return service.datasets(session(request))

    @app.post(prefix + "/datasets/{dataset}/scan", status_code=202)
    def scan(
        request: Request, dataset: str, background: BackgroundTasks
    ) -> dict:
        service.reserve_scan(session(request), dataset)
        background.add_task(service.finish_scan, dataset)
        return {"dataset": dataset, "state": "scanning"}

    @app.get(
        prefix + "/datasets/{dataset}/opening-selection",
        response_model=OpeningSelectionView,
    )
    def opening_selection(
        request: Request, dataset: str, sample: str | None = None
    ) -> dict:
        """Resolve a readable opening target without acquiring a lease."""
        return service.opening_selection(session(request), dataset, sample)

    @app.get(prefix + "/datasets/{dataset}/samples")
    def samples(
        request: Request,
        dataset: str,
        search: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict:
        return service.list_samples(
            session(request), dataset, search, offset, limit
        )

    sample_path = prefix + "/datasets/{dataset}/samples/{sample}"

    @app.get(sample_path, response_model=SampleView)
    def sample(request: Request, dataset: str, sample: str) -> dict:
        return service.get_sample(session(request), dataset, sample)

    @app.get(sample_path + "/images/{variant}")
    def image(
        request: Request, dataset: str, sample: str, variant: str
    ) -> Response:
        path, version = service.image(
            session(request), dataset, sample, variant
        )
        tag = '"' + version + '"'
        headers = {
            "ETag": tag,
            "Cache-Control": "private, no-cache",
            "Vary": "Cookie",
        }
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/png", headers=headers)

    @app.post(sample_path + "/lease")
    def acquire(
        request: Request, dataset: str, sample: str, body: LeaseRequest
    ) -> dict:
        return service.lease(
            session(request),
            dataset,
            sample,
            body.tab_id,
            "acquire",
            body.lease_id,
        )

    @app.put(sample_path + "/lease")
    def renew(
        request: Request, dataset: str, sample: str, body: LeaseRequest
    ) -> dict:
        return service.lease(
            session(request),
            dataset,
            sample,
            body.tab_id,
            "renew",
            body.lease_id,
        )

    @app.delete(sample_path + "/lease")
    def release(
        request: Request, dataset: str, sample: str, body: LeaseRequest
    ) -> dict:
        return service.lease(
            session(request),
            dataset,
            sample,
            body.tab_id,
            "release",
            body.lease_id,
        )

    @app.put(sample_path + "/draft", response_model=SampleView)
    def draft(
        request: Request, dataset: str, sample: str, body: DraftRequest
    ) -> dict:
        return service.save(
            session(request), dataset, sample, body.model_dump()
        )

    @app.post(sample_path + "/commit", response_model=SampleView)
    def commit(
        request: Request, dataset: str, sample: str, body: CommitRequest
    ) -> dict:
        return service.save(
            session(request), dataset, sample, body.model_dump(), commit=True
        )

    @app.get(prefix + "/shares")
    def shares(request: Request) -> list[dict]:
        return service.shares(session(request))

    @app.post(prefix + "/shares")
    def share(request: Request, body: ShareRequest) -> dict:
        return service.create_share(session(request), **body.model_dump())

    @app.delete(prefix + "/shares/{share}")
    def revoke(request: Request, share: str) -> dict:
        return service.revoke(session(request), share)

    @app.get(prefix + "/models", response_model=list[ModelView])
    def models(request: Request) -> list[dict]:
        with store.read() as db:
            service.auth(db, session(request), edit=True)
        return app.state.inference.models

    @app.get(prefix + "/devices")
    def get_devices(request: Request) -> dict:
        with store.read() as db:
            service.auth(db, session(request), edit=True)
        return devices(settings)

    @app.get(prefix + "/model-slot", response_model=SlotView)
    def slot(request: Request) -> dict:
        with store.read() as db:
            service.auth(db, session(request), edit=True)
        return app.state.inference.snapshot()

    @app.put(prefix + "/model-slot", status_code=202, response_model=SlotView)
    def control(request: Request, body: SlotRequest) -> dict:
        return app.state.inference.control(session(request), body.model_dump())

    @app.post(prefix + "/inference-jobs", status_code=202)
    def infer(request: Request, body: InferenceRequest) -> dict:
        return app.state.inference.submit(session(request), body.model_dump())

    @app.get(prefix + "/inference-jobs", response_model=list[JobView])
    def jobs(request: Request) -> list[dict]:
        return app.state.inference.jobs(session(request))

    @app.get(prefix + "/inference-jobs/{job}", response_model=JobView)
    def job(request: Request, job: str) -> dict:
        """Read the requesting session's task and recorded inference timing."""
        return app.state.inference.job(session(request), job)

    @app.delete(prefix + "/inference-jobs/{job}")
    def cancel(request: Request, job: str) -> dict:
        return app.state.inference.cancel(session(request), job)

    @app.post(prefix + "/exports", status_code=202)
    def export(request: Request, body: ExportRequest) -> dict:
        return app.state.exports.create(session(request), body.dataset)

    @app.get(prefix + "/exports")
    def exports(request: Request) -> list[dict]:
        return app.state.exports.list(session(request))

    @app.get(prefix + "/exports/{export_id}/download")
    def download(request: Request, export_id: str) -> Response:
        return FileResponse(
            app.state.exports.download(session(request), export_id),
            filename=f"{export_id}.zip",
            media_type="application/zip",
        )

    def shutdown_message() -> str:
        """Expose only the configured browser countdown in shutdown events."""
        data = {"countdown_seconds": settings.shutdown_countdown_seconds}
        return f"event: shutdown\ndata: {json.dumps(data)}\n\n"

    async def wait_for_shutdown(timeout: float) -> None:
        """Wake promptly on shutdown while retaining stream heartbeats."""
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    @app.get(prefix + "/server-events")
    async def server_events(request: Request) -> Response:
        """Notify every open page, including unauthenticated login pages."""

        async def stream():
            while not await request.is_disconnected():
                if shutdown_event.is_set():
                    yield shutdown_message()
                    return
                yield ": heartbeat\n\n"
                await wait_for_shutdown(15)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    @app.get(prefix + "/events")
    async def events(
        request: Request, after: int = Query(0, ge=0)
    ) -> Response:
        sid = await run_in_threadpool(session, request)
        try:
            last = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            last = after

        async def stream():
            cursor = last
            while not await request.is_disconnected():
                if shutdown_event.is_set():
                    yield shutdown_message()
                    return
                try:
                    rows = await run_in_threadpool(service.events, sid, cursor)
                except DomainError:
                    yield "event: revoked\ndata: {}\n\n"
                    return
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(row)}\n\n"
                yield ": heartbeat\n\n"
                await wait_for_shutdown(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    @app.get(prefix + "/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    if settings.frontend_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=settings.frontend_dir, html=True),
            name="frontend",
        )
    return app
