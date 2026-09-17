"""Service-wide model slot and durable, revision-fenced inference queue."""

from __future__ import annotations

import copy
import json
import multiprocessing
import os
import secrets
import threading
import time

from .service import AnnotationService, digest, encode, event
from ..domain.rules import DomainError, edit_group
from ..infrastructure.inference.devices import catalog, select_device
from ..infrastructure.inference.worker import worker_main
from ..infrastructure.inference.downloads import (
    ModelDownloader,
    DownloadCancelled,
)


class InferenceService:
    """Serialize model control and work without blocking HTTP or SQLite."""

    def __init__(self, service: AnnotationService) -> None:
        self.service = service
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.state = "UNLOADED"
        self.generation = 0
        self.model_id = None
        self.model_version = None
        self.device = None
        self.error = None
        self.providers = []
        self.download = None
        self.downloader = ModelDownloader(service.settings)
        self.pending_control = None
        self.deferred_failures = []
        self.pending_slot_failure = None
        self.process = None
        self.connection = None
        self.models = catalog(service.settings)
        self.thread = threading.Thread(
            target=self.loop, name="inference-supervisor", daemon=True
        )

    def start(self) -> None:
        """Start the sole slot supervisor after recovery."""
        self.thread.start()

    def snapshot(self) -> dict:
        """Read slot state and active/queued counts consistently."""
        with self.lock:
            with self.service.store.read() as db:
                queued = db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state='queued'"
                ).fetchone()[0]
                running = db.execute(
                    "SELECT id FROM jobs WHERE state='running'"
                ).fetchone()
            return {
                "state": self.state,
                "generation": self.generation,
                "model_id": self.model_id,
                "model_version": self.model_version,
                "device": self.device,
                "error": self.error,
                "providers": self.providers,
                "queued": queued,
                "running_job": running["id"] if running else None,
                "download": self.download,
            }

    def control(self, session: str, request: dict) -> dict:
        """Confirm one compare-and-swap load, switch or unload operation."""
        with self.lock:
            with self.service.store.read() as db:
                self.service.auth(db, session, edit=True)
                busy = db.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('queued','running')"
                ).fetchone()
            if (
                request["generation"] != self.generation
                or self.state not in ("READY", "UNLOADED")
                or busy
            ):
                raise DomainError(
                    "model_conflict", "共享模型正在加载、运行或有排队任务", 409
                )
            model_id = request.get("model_id")
            if model_id:
                # Re-evaluate registered file fingerprints at load confirmation.
                self.models = catalog(self.service.settings)
                model = next(
                    (m for m in self.models if m["id"] == model_id), None
                )
                if not model or not (
                    model["available"] or model["downloadable"]
                ):
                    raise DomainError(
                        "model_unavailable", "模型文件未准备完成", 409, model
                    )
                # Fresh device eligibility is sampled after network downloads.
                device = request["device"]
            else:
                model, device = None, None
            self.generation += 1
            self.state = (
                ("DOWNLOADING" if not model["available"] else "LOADING")
                if model_id
                else "UNLOADING"
            )
            self.download = None
            self.error = None
            self.pending_control = (model, device, session)
            return self.snapshot()

    def submit(self, session: str, request: dict) -> dict:
        """Persist a task pinned to the saved revision and current model slot."""
        service = self.service
        with self.lock, service.store.transaction() as db:
            service.auth(
                db, session, request["dataset"], request["sample"], edit=True
            )
            signature = digest(encode(["inference", request]))
            old = db.execute(
                "SELECT * FROM operations WHERE session=? AND id=?",
                (session, request["operation_id"]),
            ).fetchone()
            if old:
                if old["digest"] != signature:
                    raise DomainError(
                        "operation_reused", "操作 ID 已用于其他请求", 409
                    )
                return json.loads(old["result"])
            if (
                self.state not in ("READY", "RUNNING")
                or request["slot_generation"] != self.generation
                or request["model_id"] != self.model_id
                or request["model_version"] != self.model_version
            ):
                raise DomainError("model_conflict", "模型槽位版本已变化", 409)
            row = service.check_write(
                db, session, request["dataset"], request["sample"], request
            )
            if row["image_version"] != request["image_version"]:
                raise DomainError("image_version", "图像版本已变化", 409)
            config = service.settings.models[self.model_id]
            if config.attribute != row["attribute"]:
                raise DomainError("model_task", "模型与数据集任务不匹配")
            hr = json.loads(row["draft"])["HR"]
            ids = request["region_ids"]
            if len(set(ids)) != len(ids) or set(ids) - {
                r["region_id"] for r in hr
            }:
                raise DomainError("region_id", "选中区域无效")
            if (ids and config.attribute != "text") or (not ids and hr):
                raise DomainError(
                    "inference_mode",
                    "整图检测仅适用于空 HR；已有文本框须选择区域识别",
                )
            if db.execute(
                "SELECT 1 FROM jobs WHERE session=? AND tab=? AND state IN ('queued','running')",
                (session, request["tab_id"]),
            ).fetchone():
                raise DomainError(
                    "pending_job", "当前编辑会话已有未完成推理", 409
                )
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state='queued'"
            ).fetchone()[0]
            if count >= service.settings.queue_limit:
                raise DomainError(
                    "queue_full", "推理队列已满，请稍后重试", 429
                )
            job = secrets.token_hex(16)
            now = time.time()
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?, ?,?,NULL,NULL,?,?)",
                (
                    job,
                    session,
                    request["tab_id"],
                    request["dataset"],
                    request["sample"],
                    "queued",
                    encode(request),
                    now,
                    now,
                ),
            )
            result = {"id": job, "state": "queued"}
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,?)",
                (session, request["operation_id"], signature, encode(result)),
            )
            event(db, "job", request["dataset"], request["sample"], result)
            return result

    def jobs(self, session: str) -> list[dict]:
        """Only expose task records created by this access session."""
        with self.service.store.read() as db:
            self.service.auth(db, session, edit=True)
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,dataset,sample,state,error,created,updated FROM jobs WHERE session=? ORDER BY created DESC LIMIT 100",
                    (session,),
                )
            ]

    def job(self, session: str, job_id: str) -> dict:
        """Return one authorized task and its measured worker stages."""
        with self.service.store.read() as db:
            self.service.auth(db, session, edit=True)
            row = db.execute(
                "SELECT id,dataset,sample,state,error,created,updated,result FROM jobs WHERE id=? AND session=?",
                (job_id, session),
            ).fetchone()
            if row is None:
                raise DomainError("job", "任务不存在", 404)
            output = dict(row)
            result = json.loads(output.pop("result") or "null")
            output["timings"] = (
                result.get("timings", {}) if isinstance(result, dict) else {}
            )
            return output

    def cancel(self, session: str, job: str) -> dict:
        """Cancel the caller's queued task; do not interrupt running inference."""
        with self.service.store.transaction() as db:
            self.service.auth(db, session, edit=True)
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND session=?", (job, session)
            ).fetchone()
            if not row:
                raise DomainError("job", "任务不存在", 404)
            if row["state"] != "queued":
                raise DomainError("job_running", "仅可取消排队中的任务", 409)
            db.execute(
                "UPDATE jobs SET state='cancelled',updated=? WHERE id=?",
                (time.time(), job),
            )
            return {"cancelled": True}

    def receive(self, timeout: float) -> dict:
        """Bound native inference time and detect worker exit."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connection.poll(0.1):
                result = self.connection.recv()
                if result["state"] == "error":
                    raise RuntimeError(result["error"])
                return result
            if not self.process.is_alive():
                raise RuntimeError("推理进程异常退出")
        raise TimeoutError("模型加载或推理超时")

    def cleanup(self) -> None:
        """Reap only this service's child before reporting UNLOADED."""
        if self.process:
            if self.process.is_alive():
                try:
                    self.connection.send({"action": "stop"})
                except (OSError, EOFError):
                    pass
                self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
            self.process.close()
            self.connection.close()
            self.process = self.connection = None
        self.providers = []

    def load(self, command: tuple) -> None:
        """Switch by exiting the old model process before starting another."""
        model, requested_device, session = command
        with self.service.store.read() as db:
            self.service.auth(db, session, edit=True)
        self.cleanup()
        with self.lock:
            self.model_id = self.model_version = self.device = None
        if model:
            original = self.service.settings.models[model["id"]]
            last_authorized = 0.0

            def check() -> None:
                nonlocal last_authorized
                if self.stop.is_set():
                    raise DownloadCancelled("服务停止，下载已中断")
                if time.monotonic() - last_authorized >= 1:
                    with self.service.store.read() as db:
                        self.service.auth(db, session, edit=True)
                    last_authorized = time.monotonic()

            def progress(value: dict) -> None:
                with self.lock:
                    self.state = "DOWNLOADING"
                    self.download = value

            config = self.downloader.ensure(
                model["id"], original, progress, check
            )
            with self.service.store.read() as db:
                self.service.auth(db, session, edit=True)
            refreshed = catalog(self.service.settings)
            model = next(
                item for item in refreshed if item["id"] == model["id"]
            )
            if not model["available"]:
                raise DomainError(
                    "model_unavailable", "模型文件未准备完成", 409, model
                )
            with self.lock:
                self.models = refreshed
            device = select_device(
                self.service.settings,
                requested_device,
                config.required_memory_mb,
            )
            with self.lock:
                self.state = "LOADING"
                self.download = None
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(
                target=worker_main,
                args=(
                    child,
                    {
                        **config.model_dump(mode="json"),
                        "_expected_version": model["version"],
                    },
                    device,
                    self.service.settings.cpu_threads,
                    str(self.service.settings.cache_dir),
                    os.getpid(),
                ),
                daemon=True,
            )
            self.process.start()
            child.close()
            ready = self.receive(self.service.settings.load_timeout_seconds)
            with self.lock:
                self.model_id, self.model_version, self.device = (
                    model["id"],
                    model["version"],
                    device,
                )
                self.providers = ready["providers"]
                self.state = "READY"
        else:
            with self.lock:
                self.state = "UNLOADED"

    def apply_result(
        self, job: dict, result: list[dict], timings: dict | None = None
    ) -> None:
        """Apply only while original authority, lease and revision still hold."""
        service = self.service
        request = json.loads(job["request"])
        with service.store.transaction() as db:
            service.auth(
                db, job["session"], job["dataset"], job["sample"], edit=True
            )
            row = service.check_write(
                db, job["session"], job["dataset"], job["sample"], request
            )
            if (
                row["image_version"] != request["image_version"]
                or request["slot_generation"] != self.generation
            ):
                raise DomainError("stale_result", "推理结果版本已过期", 409)
            previous = json.loads(row["draft"])
            hr = copy.deepcopy(previous["HR"])
            if request["region_ids"]:
                by_id = {r["region_id"]: r for r in hr}
                if {r["region_id"] for r in result} != set(
                    request["region_ids"]
                ):
                    raise DomainError("inference_result", "识别结果区域不完整")
                for record in result:
                    by_id[record["region_id"]]["description"] = record[
                        "description"
                    ]
            else:
                hr = [
                    dict(record, region_id=secrets.token_hex(16))
                    for record in result
                ]
            group = edit_group(
                hr,
                previous,
                json.loads(row["dimensions"]),
                row["attribute"],
                {},
            )
            revision = row["revision"] + 1
            db.execute(
                "UPDATE samples SET draft=?,revision=?,modified=1,complete=0,updated=? WHERE dataset=? AND id=?",
                (
                    encode(group),
                    revision,
                    time.time(),
                    job["dataset"],
                    job["sample"],
                ),
            )
            db.execute(
                "UPDATE jobs SET state='applied',result=?,updated=? WHERE id=?",
                (
                    encode({"regions": result, "timings": timings or {}}),
                    time.time(),
                    job["id"],
                ),
            )
            event(
                db,
                "sample",
                job["dataset"],
                job["sample"],
                {"revision": revision, "job": job["id"]},
            )

    def run_job(self, job: dict) -> None:
        """Revalidate before costly work, then atomically fence result apply."""
        service = self.service
        request = json.loads(job["request"])
        queue_ms = (time.time() - job["created"]) * 1000
        with service.store.read() as db:
            service.auth(
                db, job["session"], job["dataset"], job["sample"], edit=True
            )
            row = service.check_write(
                db, job["session"], job["dataset"], job["sample"], request
            )
            info = json.loads(row["images"])["HR"]
            regions = [
                r
                for r in json.loads(row["draft"])["HR"]
                if r["region_id"] in request["region_ids"]
            ]
        self.connection.send(
            {
                "action": "predict",
                "image": info["path"],
                "image_sha256": info["sha256"],
                "regions": regions or None,
            }
        )
        result = self.receive(
            service.settings.models[self.model_id].timeout_seconds
        )
        self.apply_result(
            job,
            result["result"],
            {**result.get("timings", {}), "queue_ms": queue_ms},
        )

    def fail_job(self, job: dict, state: str, error: str) -> None:
        """Retain terminal transitions in memory until storage is writable."""
        try:
            with self.service.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET state=?,error=?,updated=? WHERE id=?",
                    (state, error, time.time(), job["id"]),
                )
                event(
                    db,
                    "job",
                    job["dataset"],
                    job["sample"],
                    {"id": job["id"], "state": state},
                )
        except DomainError as exc:
            if exc.status != 503:
                raise
            self.deferred_failures.append((job, state, error))

    def retry_terminal_states(self) -> None:
        """Recover task state after a transient full or busy state disk."""
        pending, self.deferred_failures = self.deferred_failures, []
        for failure in pending:
            self.fail_job(*failure)
        if self.pending_slot_failure is not None:
            try:
                with self.service.store.transaction() as db:
                    db.execute(
                        "UPDATE jobs SET state='failed',error=?,updated=? WHERE state IN ('queued','running')",
                        (self.pending_slot_failure, time.time()),
                    )
                    event(db, "model", None, None, {})
                self.pending_slot_failure = None
            except DomainError as exc:
                if exc.status != 503:
                    raise

    def loop(self) -> None:
        """Run model control and a serial FIFO queue in a dedicated thread."""
        try:
            while not self.stop.wait(0.1):
                job = None
                try:
                    self.retry_terminal_states()
                    if (
                        self.pending_slot_failure is not None
                        or self.deferred_failures
                    ):
                        continue
                    with self.lock:
                        control, self.pending_control = (
                            self.pending_control,
                            None,
                        )
                    if control is not None:
                        self.load(control)
                        with self.service.store.transaction() as db:
                            event(db, "model", None, None, {})
                        continue
                    with self.lock:
                        if self.state != "READY":
                            continue
                        if self.process and not self.process.is_alive():
                            raise RuntimeError("空闲模型进程异常退出")
                        with self.service.store.transaction() as db:
                            row = db.execute(
                                "SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1"
                            ).fetchone()
                            if row:
                                job = dict(row)
                                db.execute(
                                    "UPDATE jobs SET state='running',updated=? WHERE id=?",
                                    (time.time(), job["id"]),
                                )
                        if job:
                            self.state = "RUNNING"
                    if job:
                        try:
                            self.run_job(job)
                        except DomainError as exc:
                            terminal = (
                                "stale"
                                if exc.status in (401, 403, 409)
                                else "failed"
                            )
                            self.fail_job(job, terminal, str(exc))
                        with self.lock:
                            self.state = "READY"
                except Exception as exc:
                    if job:
                        self.fail_job(job, "failed", str(exc))
                    with self.lock:
                        self.state = "ERROR"
                        self.error = str(exc)
                    self.cleanup()
                    with self.lock:
                        self.state = "UNLOADED"
                        self.model_id = self.model_version = self.device = None
                    self.pending_slot_failure = "模型已卸载: " + str(exc)
                    self.retry_terminal_states()
        finally:
            self.cleanup()

    def close(self) -> None:
        """Stop accepting queued work and wait for the bounded running task."""
        self.stop.set()
        self.thread.join()
