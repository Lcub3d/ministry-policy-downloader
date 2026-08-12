from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .pipeline import Pipeline
from .sites import ADAPTERS, get_adapter
from .storage import Storage


WEB_ROOT = Path(__file__).with_name("web")
SOURCE_NAMES = {
    "ndrc": "国家发展改革委",
    "mee": "生态环境部",
    "mnr": "自然资源部",
    "mof": "财政部",
}
TERMINAL_STATES = {"success", "failed", "cancelled"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _loopback_authority(value: str, *, origin: bool) -> tuple[str, int] | None:
    try:
        parts = urlsplit(value if origin else f"//{value}")
        port = parts.port or 80
    except ValueError:
        return None
    if origin:
        if parts.scheme.casefold() != "http" or parts.path not in {"", "/"}:
            return None
    elif parts.scheme or parts.path:
        return None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return None
    host = (parts.hostname or "").casefold().rstrip(".")
    if host not in LOOPBACK_HOSTS:
        return None
    return host, port


def _is_local_request(host: str, origin: str | None, server_port: int) -> bool:
    authority = _loopback_authority(host, origin=False)
    if authority is None or authority[1] != server_port:
        return False
    if not origin:
        return True
    return _loopback_authority(origin, origin=True) == authority


@dataclass
class Job:
    id: str
    sources: list[str]
    since: str
    output: str
    mode: str
    delay: float
    timeout: float = 30.0
    status: str = "queued"
    progress: int = 0
    message: str = "任务已排队"
    logs: list[str] = field(default_factory=list)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    cancel_requested: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "job_id": self.id,
                "status": self.status,
                "progress": self.progress,
                "message": self.message,
                "logs": list(self.logs[-300:]),
                "results": {key: dict(value) for key, value in self.results.items()},
            }

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"{stamp}  {message}")


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.active_job: str | None = None

    def create(self, payload: dict[str, Any]) -> Job:
        sources = payload.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("请至少选择一个部委")
        sources = list(dict.fromkeys(str(value).casefold() for value in sources))
        unknown = [source for source in sources if source not in ADAPTERS]
        if unknown:
            raise ValueError(f"不支持的来源：{', '.join(unknown)}")

        since = str(payload.get("since", ""))
        try:
            datetime.strptime(since, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("起始日期必须为 YYYY-MM-DD") from exc

        output = str(payload.get("output", "")).strip()
        if not output or "\x00" in output:
            raise ValueError("请填写有效的输出目录")
        mode = str(payload.get("mode", "update"))
        if mode not in {"update", "scan"}:
            raise ValueError("任务模式必须为 update 或 scan")
        delay = float(payload.get("delay", 1.0))
        if not 0.2 <= delay <= 30:
            raise ValueError("请求间隔应在 0.2 到 30 秒之间")

        with self.lock:
            if self.active_job:
                active = self.jobs.get(self.active_job)
                if active and active.status not in TERMINAL_STATES:
                    raise RuntimeError("已有任务正在运行，请等待或先停止当前任务")
            job = Job(
                id=datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
                sources=sources,
                since=since,
                output=output,
                mode=mode,
                delay=delay,
                results={
                    source: {
                        "discovered": 0,
                        "saved": 0,
                        "skipped": 0,
                        "failed": 0,
                        "status": "waiting",
                    }
                    for source in sources
                },
            )
            self.jobs[job.id] = job
            self.active_job = job.id
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        with job.lock:
            if job.status not in TERMINAL_STATES:
                job.cancel_requested = True
                job.message = "将在当前来源完成后停止"
        job.log("已收到停止请求；为避免不完整写入，当前来源完成后停止。")
        return job

    def _run(self, job: Job) -> None:
        with job.lock:
            job.status = "running"
            job.message = "正在准备本地归档"
        job.log(f"任务 {job.id} 开始；输出目录：{Path(job.output).expanduser()}")
        total = len(job.sources)
        try:
            if job.mode == "scan":
                for index, source in enumerate(job.sources):
                    if job.cancel_requested:
                        self._finish_cancelled(job)
                        return
                    result = job.results[source]
                    with job.lock:
                        result["status"] = "running"
                        job.progress = round(index / total * 100)
                        job.message = f"正在处理 {SOURCE_NAMES[source]}"
                    job.log(f"[{source}] 扫描已配置的官方栏目…")
                    pipeline = Pipeline(
                        None,
                        get_adapter(source),
                        delay=job.delay,
                        timeout=job.timeout,
                    )
                    policies = pipeline.preview(job.since)
                    with job.lock:
                        result.update(discovered=len(policies), status="success")
                        job.progress = round((index + 1) / total * 100)
                    job.log(f"[{source}] 扫描完成：发现 {len(policies)} 条；未写入归档。")
            else:
                with Storage(job.output) as storage:
                    for index, source in enumerate(job.sources):
                        if job.cancel_requested:
                            self._finish_cancelled(job)
                            return
                        result = job.results[source]
                        with job.lock:
                            result["status"] = "running"
                            job.progress = round(index / total * 100)
                            job.message = f"正在处理 {SOURCE_NAMES[source]}"
                        job.log(f"[{source}] 扫描已配置的官方栏目…")
                        pipeline = Pipeline(storage, get_adapter(source), delay=job.delay, timeout=job.timeout)
                        stats = pipeline.update(job.since)
                        with job.lock:
                            result.update(
                                discovered=stats.indexed,
                                saved=stats.documents_downloaded + stats.attachments_downloaded,
                                skipped=stats.documents_skipped + stats.attachments_skipped,
                                status="success",
                            )
                        job.log(
                            f"[{source}] 完成：发现 {stats.indexed}；正文新增 "
                            f"{stats.documents_downloaded}；附件新增 {stats.attachments_downloaded}；"
                            f"跳过 {stats.documents_skipped + stats.attachments_skipped}。"
                        )
                        with job.lock:
                            job.progress = round((index + 1) / total * 100)

                    audit = storage.audit()
                    gaps = sum(
                        audit[key]
                        for key in (
                            "policies_pending",
                            "policies_failed",
                            "attachments_pending",
                            "attachments_failed",
                            "missing_content_files",
                            "missing_attachment_files",
                        )
                    )
                    job.log(f"离线审计完成：当前缺口 {gaps}。")
            with job.lock:
                job.status = "success"
                job.progress = 100
                job.message = "索引扫描完成" if job.mode == "scan" else "下载与离线审计完成"
        except Exception as exc:
            current = next(
                (source for source in job.sources if job.results[source]["status"] == "running"),
                None,
            )
            with job.lock:
                if current:
                    job.results[current]["status"] = "failed"
                    job.results[current]["failed"] = 1
                job.status = "failed"
                job.message = f"任务失败：{exc}"
            job.log(f"失败：{exc}")
        finally:
            with self.lock:
                if self.active_job == job.id:
                    self.active_job = None

    def _finish_cancelled(self, job: Job) -> None:
        with job.lock:
            job.status = "cancelled"
            job.message = "任务已安全停止"
        job.log("任务已停止；已完成来源的安全写入保留。")


MANAGER = JobManager()


class Handler(BaseHTTPRequestHandler):
    server_version = "PolicyHarvester/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_is_local():
            return
        path = unquote(urlsplit(self.path).path)
        if path in {"/", "/index.html"}:
            self._file("index.html", "text/html; charset=utf-8")
            return
        if path in {"/app.css", "/static/app.css"}:
            self._file("app.css", "text/css; charset=utf-8")
            return
        if path in {"/app.js", "/static/app.js"}:
            self._file("app.js", "text/javascript; charset=utf-8")
            return
        if path.startswith("/api/jobs/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3:
                job = MANAGER.get(parts[2])
                if job is None:
                    self._json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(job.snapshot())
                return
        self._json({"error": "页面不存在"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._request_is_local():
            return
        path = unquote(urlsplit(self.path).path)
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "请求必须使用 application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        if path == "/api/jobs":
            try:
                job = MANAGER.create(self._body_json())
            except (ValueError, RuntimeError, TypeError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            else:
                self._json({"job_id": job.id}, HTTPStatus.CREATED)
            return
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                try:
                    job = MANAGER.cancel(parts[2])
                except KeyError:
                    self._json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
                else:
                    self._json(job.snapshot())
                return
        self._json({"error": "页面不存在"}, HTTPStatus.NOT_FOUND)

    def _request_is_local(self) -> bool:
        port = int(self.server.server_address[1])
        if _is_local_request(self.headers.get("Host", ""), self.headers.get("Origin"), port):
            return True
        self._json({"error": "只接受当前本机地址的请求"}, HTTPStatus.FORBIDDEN)
        return False

    def _body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length < 2 or length > 64 * 1024:
            raise ValueError("请求内容大小无效")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求必须是 UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象")
        return value

    def _file(self, name: str, content_type: str) -> None:
        path = WEB_ROOT / name
        try:
            data = path.read_bytes()
        except OSError:
            self._json({"error": "界面资源缺失"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {format % args}")


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True) -> int:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("网页界面只允许监听本机地址 127.0.0.1、localhost 或 ::1")
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_port}/"
    print(f"部委政策下载器已启动：{url}")
    print("按 Ctrl+C 停止服务。")
    if open_browser:
        threading.Timer(0.35, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("正在停止…")
    finally:
        server.server_close()
    return 0
