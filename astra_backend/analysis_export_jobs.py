"""Bounded background exports for the standalone backend's single web process."""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import threading
import time
import uuid

from astra_backend.analysis_export import write_bundle
from astra_backend.analysis_store import Archive

LOG = logging.getLogger(__name__)
TTL_SECONDS = 3600


class ExportBusy(Exception):
    pass


class ExportCancelled(Exception):
    pass


class ExportJobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.reaper = None

    def _reap(self):
        while True:
            time.sleep(30)
            self.cleanup()
            with self.lock:
                if not self.jobs:
                    self.reaper = None
                    return

    @staticmethod
    def owner(session):
        return hashlib.sha256(session.encode("utf-8")).hexdigest()

    @staticmethod
    def public(job):
        return {key: job[key] for key in (
            "id", "state", "stage", "completed", "total", "bytes", "created_at", "expires_at", "error")}

    def cleanup(self):
        with self.lock:
            for job_id, job in list(self.jobs.items()):
                if job["state"] in ("ready", "failed", "cancelled") and job["expires_at"] < time.time():
                    if job["downloads"]:
                        continue
                    try:
                        job["path"].unlink(missing_ok=True)
                    except OSError:
                        LOG.warning("Could not remove expired export %s", job_id)
                        continue
                    del self.jobs[job_id]

    def start(self, session, account, query, archive=None):
        archive = archive or Archive()
        owner = self.owner(session)
        with self.lock:
            self.cleanup()
            for job in self.jobs.values():
                if job["state"] in ("running", "cancelling"):
                    if job["owner"] == owner and job["account"] == account and job["query"] == query:
                        return self.public(job)
                    raise ExportBusy()
            # Keep artifacts on the data volume rather than /tmp, which may be tmpfs.
            directory = archive.path.parent / "analysis_exports"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            known_paths = {job["path"] for job in self.jobs.values()}
            for old in directory.glob("*.zip"):
                # Clean abandoned artifacts after a restart, only inside this private directory.
                if len(old.stem) == 32 and all(c in "0123456789abcdef" for c in old.stem):
                    if old not in known_paths and not old.is_symlink() and old.stat().st_mtime < time.time() - TTL_SECONDS:
                        old.unlink(missing_ok=True)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id, "owner": owner, "account": account, "query": dict(query),
                "state": "running", "stage": "reading", "completed": 0, "total": 0,
                "bytes": 0, "created_at": time.time(), "expires_at": None, "error": "",
                "path": directory / (job_id + ".zip"), "cancel": threading.Event(), "downloads": 0,
            }
            self.jobs[job_id] = job
            thread = threading.Thread(target=self._run, args=(job, archive), daemon=True,
                                      name="analysis-export")
            job["thread"] = thread
            thread.start()
            if self.reaper is None:
                self.reaper = threading.Thread(target=self._reap, daemon=True, name="analysis-export-cleanup")
                self.reaper.start()
            return self.public(job)

    def _run(self, job, archive):
        last_report = 0
        def progress(stage, completed=0, total=0):
            nonlocal last_report
            if job["cancel"].is_set():
                raise ExportCancelled()
            stamp = time.monotonic()
            if stage != job["stage"] or stamp - last_report >= .25 or (total and completed == total):
                with self.lock:
                    job.update(stage=stage, completed=completed, total=total)
                    if job["path"].exists():
                        job["bytes"] = job["path"].stat().st_size
                last_report = stamp
        try:
            # Exclusive creation, with owner-only permissions on POSIX hosts.
            fd = os.open(job["path"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as target:
                write_bundle(job["account"], job["query"], target, archive, progress)
            with self.lock:
                if job["cancel"].is_set():
                    raise ExportCancelled()
                job.update(state="ready", stage="ready", bytes=job["path"].stat().st_size,
                           expires_at=time.time() + TTL_SECONDS)
        except Exception as exc:
            try:
                job["path"].unlink(missing_ok=True)
            except OSError:
                LOG.warning("Could not remove incomplete export %s", job["id"])
            with self.lock:
                job.update(state="cancelled" if isinstance(exc, ExportCancelled) else "failed",
                           error="" if isinstance(exc, ExportCancelled) else (
                               "disk_full" if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else "failed"),
                           bytes=0, expires_at=time.time() + TTL_SECONDS)
            if not isinstance(exc, ExportCancelled):
                LOG.exception("Analysis export failed: %s", job["id"])

    def _find(self, session, job_id):
        self.cleanup()
        job = self.jobs.get(job_id)
        if job is None or job["owner"] != self.owner(session):
            raise KeyError(job_id)
        return job

    def get(self, session, job_id):
        with self.lock:
            return self.public(self._find(session, job_id))

    def cancel(self, session, job_id):
        with self.lock:
            job = self._find(session, job_id)
            if job["state"] == "running":
                job["cancel"].set()
                job["state"] = "cancelling"
            return self.public(job)

    def acquire_file(self, session, job_id):
        with self.lock:
            job = self._find(session, job_id)
            if job["state"] != "ready":
                raise ExportBusy()
            job["downloads"] += 1
            return job["path"]

    def release_file(self, session, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job and job["owner"] == self.owner(session):
                job["downloads"] = max(0, job["downloads"] - 1)
        self.cleanup()
