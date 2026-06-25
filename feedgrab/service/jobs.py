# -*- coding: utf-8 -*-
"""In-process job queue primitives for desktop workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Any

from feedgrab.service.fetch import FetchService
from feedgrab.service.models import ProgressEvent, ServiceError, redact_value


_JOB_COUNTER = count(1)


@dataclass
class JobRecord:
    """Serializable job state for GUI/worker clients."""

    job_id: str
    kind: str
    urls: list[str] = field(default_factory=list)
    status: str = "queued"
    results: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    attempts: int = 0
    retry_limit: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "urls": redact_value(list(self.urls)),
            "status": self.status,
            "results": redact_value(list(self.results)),
            "artifacts": redact_value(list(self.artifacts)),
            "error": redact_value(dict(self.error)) if self.error else None,
            "attempts": self.attempts,
            "retry_limit": self.retry_limit,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobService:
    """Minimal serial queue used by the desktop sidecar worker."""

    def __init__(self, fetch_service: FetchService | None = None, *, concurrency_limit: int = 1):
        self.fetch_service = fetch_service or FetchService()
        self.concurrency_limit = max(1, int(concurrency_limit))
        self._jobs: dict[str, JobRecord] = {}
        self._queue: list[str] = []
        self._events: dict[str, list[ProgressEvent]] = {}

    def progress(self, stage: str, message: str, **kwargs) -> ProgressEvent:
        return ProgressEvent(stage=stage, message=message, **kwargs)

    def enqueue_fetch(self, urls: list[str], *, retry_limit: int = 0) -> JobRecord:
        job_id = f"job-{next(_JOB_COUNTER):06d}"
        job = JobRecord(job_id=job_id, kind="fetch", urls=list(urls), retry_limit=max(0, int(retry_limit)))
        self._jobs[job_id] = job
        self._queue.append(job_id)
        self._append_event(job_id, "job_queued", "queued", details={"urls": list(urls)})
        return job

    def retry(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status in {"queued", "running"}:
            return False
        if job.attempts > job.retry_limit:
            return False
        job.status = "queued"
        job.results = []
        job.artifacts = []
        job.error = None
        job.started_at = ""
        job.finished_at = ""
        if job_id not in self._queue:
            self._queue.append(job_id)
        self._append_event(job_id, "job_retry_queued", "retry queued", details={"attempts": job.attempts})
        return True

    def get_job(self, job_id: str) -> JobRecord:
        return self._jobs[job_id]

    def history(self) -> list[JobRecord]:
        return list(self._jobs.values())

    def events(self, job_id: str) -> list[ProgressEvent]:
        return list(self._events.get(job_id, []))

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status in {"succeeded", "failed", "cancelled"}:
            return False
        if job_id in self._queue:
            self._queue.remove(job_id)
        job.status = "cancelled"
        job.finished_at = datetime.now().isoformat()
        self._append_event(job_id, "job_cancelled", "cancelled")
        return True

    async def run_until_idle(self) -> None:
        while self._queue:
            job_id = self._queue.pop(0)
            job = self._jobs[job_id]
            if job.status == "cancelled":
                continue
            await self._run_fetch_job(job)

    async def _run_fetch_job(self, job: JobRecord) -> None:
        job.status = "running"
        job.attempts += 1
        job.started_at = datetime.now().isoformat()
        self._append_event(job.job_id, "job_started", "started")
        try:
            results = await self.fetch_service.fetch_urls(job.urls)
            if job.status == "cancelled":
                return
            job.results = [result.to_dict() for result in results]
            job.artifacts = [
                artifact
                for result in job.results
                for artifact in result.get("artifacts", [])
            ]
            failed = [result for result in job.results if not result.get("success", True)]
            if failed:
                job.status = "failed"
                job.error = failed[0].get("error") or ServiceError("fetch failed").to_dict()
                self._append_event(job.job_id, "job_failed", "failed", details={"error": job.error})
            else:
                job.status = "succeeded"
                self._append_event(job.job_id, "job_done", "done", details={"artifacts": job.artifacts})
        except ServiceError as exc:
            if job.status == "cancelled":
                return
            job.status = "failed"
            job.error = exc.to_dict()
            self._append_event(job.job_id, "job_failed", "failed", details={"error": job.error})
        except Exception as exc:
            if job.status == "cancelled":
                return
            err = ServiceError(str(exc), code="job_error", details={"job_id": job.job_id})
            job.status = "failed"
            job.error = err.to_dict()
            self._append_event(job.job_id, "job_failed", "failed", details={"error": job.error})
        finally:
            job.finished_at = datetime.now().isoformat()

    def _append_event(
        self,
        job_id: str,
        stage: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = ProgressEvent(stage=stage, message=message, details=details or {})
        self._events.setdefault(job_id, []).append(event)
