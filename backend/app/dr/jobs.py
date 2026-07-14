"""In-memory background-job store for DR operations.

Each DR action (failover / failback / start / stop / sync) runs in a background
thread and appends its :class:`StepResult` items into the job's ``steps`` list
as it progresses, so the dashboard can poll and render live step-by-step
progress. A single-flight lock ensures only one DR job runs at a time (you can
never overlap a failover and a failback).

State is kept in memory - fine for the single-process uvicorn deployment on the
jumpbox. (A multi-worker deployment would need a shared store.)
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import uuid
from datetime import datetime, timezone

from ..config import Settings
from .workflows import (
    DEFAULT_GROUP,
    DrError,
    StepResult,
    failback,
    failover,
    recover,
    restore,
    revert_failover,
    run_link_op,
)

logger = logging.getLogger("dr.jobs")


class JobBusyError(RuntimeError):
    """Raised when a DR job is requested while another is still running."""


# kind -> callable(settings, group, dry_run, sink) -> None
_WORKFLOWS = {
    "failover": lambda s, g, dry, sink: failover(s, g, dry_run=dry, sink=sink),
    "failback": lambda s, g, dry, sink: failback(s, g, dry_run=dry, sink=sink),
    "recover": lambda s, g, dry, sink: recover(s, g, dry_run=dry, sink=sink),
    "restore": lambda s, g, dry, sink: restore(s, g, dry_run=dry, sink=sink),
    "revert": lambda s, g, dry, sink: revert_failover(s, g, dry_run=dry, sink=sink),
    "start": lambda s, g, dry, sink: run_link_op(s, "start", g, dry_run=dry, sink=sink),
    "stop": lambda s, g, dry, sink: run_link_op(s, "stop", g, dry_run=dry, sink=sink),
    "sync": lambda s, g, dry, sink: run_link_op(s, "sync", g, dry_run=dry, sink=sink),
}

JOB_KINDS = tuple(_WORKFLOWS.keys())


class Job:
    def __init__(self, kind: str, group: str, dry_run: bool) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.group = group
        self.dry_run = dry_run
        self.state = "pending"  # pending | running | succeeded | failed
        self.steps: list[StepResult] = []
        self.error: str | None = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "group": self.group,
            "dry_run": self.dry_run,
            "state": self.state,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            # Copy the list so a concurrent append can't disrupt serialization.
            "steps": [dataclasses.asdict(s) for s in list(self.steps)],
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_active_id: str | None = None


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def recent_jobs(limit: int = 10) -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]


def active_job() -> Job | None:
    if _active_id is None:
        return None
    return _jobs.get(_active_id)


def start_job(settings: Settings, kind: str, group: str, dry_run: bool) -> Job:
    """Create and start a DR job in a background thread. Single-flight."""
    global _active_id
    if kind not in _WORKFLOWS:
        raise DrError(f"Unknown DR job kind: {kind}")
    with _lock:
        active = _jobs.get(_active_id) if _active_id else None
        if active and active.state in ("pending", "running"):
            raise JobBusyError(
                f"A DR operation ({active.kind}) is already running. "
                "Wait for it to finish."
            )
        job = Job(kind, group, dry_run)
        _jobs[job.id] = job
        _active_id = job.id

    thread = threading.Thread(target=_run, args=(settings, job), daemon=True)
    thread.start()
    return job


def _run(settings: Settings, job: Job) -> None:
    global _active_id
    job.state = "running"
    logger.info("DR job %s (%s, dry_run=%s) started", job.id, job.kind, job.dry_run)
    try:
        _WORKFLOWS[job.kind](settings, job.group, job.dry_run, job.steps)
        if job.dry_run:
            job.state = "succeeded"  # a preview always "succeeds"
        else:
            job.state = "failed" if any(not s.ok for s in job.steps) else "succeeded"
    except DrError as exc:
        job.error = str(exc)
        job.steps.append(StepResult("error", "", False, str(exc)))
        job.state = "failed"
    except Exception as exc:  # noqa: BLE001 - never leak a thread crash
        logger.exception("DR job %s crashed", job.id)
        job.error = f"{type(exc).__name__}: {exc}"
        job.steps.append(StepResult("error", "", False, job.error))
        job.state = "failed"
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()
        with _lock:
            if _active_id == job.id:
                _active_id = None
        logger.info("DR job %s finished: %s", job.id, job.state)
