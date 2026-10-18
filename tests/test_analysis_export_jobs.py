"""Large-export lifecycle and native downloads use isolated local fixtures."""
from __future__ import annotations

import asyncio
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware
from starlette.background import BackgroundTask

from astra_backend import analysis_service as service
from astra_backend.analysis_export import BundleWriter, write_bundle
from astra_backend.analysis_export_jobs import ExportJobs, ExportBusy
from astra_backend.analysis_routes import ExportFileResponse, install_routes
from astra_backend.analysis_store import Archive, sanitize


class ExportJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "archive.db"
        self.archive = Archive(self.path)
        self.env = patch.dict(os.environ, {"ASTRA_ANALYSIS_DB": str(self.path), "ASTRA_TESTING": "1"})
        self.env.start()
        self.saved = patch.object(service, "saved_configuration", return_value={"prompt": "测试配置"})
        self.saved.start()
        self.jobs = ExportJobs()
        self.query = {"start_ms": 1788825600000, "end_ms": 1788825660000, "status": "closed"}
        self.archive.event("account", "llm.request", {"prompt": "历史证据"}, occurred_ms=self.query["start_ms"])

    def tearDown(self):
        for job in list(self.jobs.jobs.values()):
            job["cancel"].set()
            job["thread"].join(5)
        self.saved.stop()
        self.env.stop()
        self.temp.cleanup()

    def finished(self, job):
        self.jobs.jobs[job["id"]]["thread"].join(10)
        self.assertFalse(self.jobs.jobs[job["id"]]["thread"].is_alive())
        return self.jobs.get("session", job["id"])

    def test_disk_export_uses_incremental_readers_and_valid_checksums(self):
        for i in range(260):
            self.archive.event("account", "llm.response", {"text": "正文" * 50, "index": i},
                id=f"e{i}", occurred_ms=self.query["start_ms"] + i)
        self.archive.raw("account", "fills", [
            {"billId": str(i), "instId": "BTC-USDT-SWAP", "ts": str(self.query["start_ms"] + i)}
            for i in range(520)])
        out = Path(self.temp.name) / "bundle.zip"
        progress = []
        with patch.object(self.archive, "raw_rows", side_effect=AssertionError("eager raw read")), \
             patch.object(service, "export_bundle", side_effect=AssertionError("in-memory ZIP")):
            write_bundle("account", self.query, out, self.archive, lambda *args: progress.append(args))
        with zipfile.ZipFile(out) as z:
            self.assertIsNone(z.testzip())
            manifest = json.loads(z.read("manifest.json"))
            self.assertEqual(manifest["counts"]["events"], 261)
            self.assertEqual(len(json.loads(z.read("exchange_facts.json"))["fills"]), 520)
            for name, info in manifest["files"].items():
                data = z.read(name)
                self.assertEqual(len(data), info["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"])
        self.assertIn(("events", 128, 261), progress)
        self.assertIn(("facts.fills", 512), progress)

    def test_text_cache_preserves_redaction_and_is_export_scoped(self):
        secret = "fixture-secret-value"
        body = {
            "prompt": ("市场提示 " + secret + " Bearer abcdef password=test ") * 20,
            "api_key": "must be redacted",
            "nested": ["ſecret_key=example", "https://user:pass@example.test", float("nan")],
        }
        for secrets in ({secret}, set()):
            writer = BundleWriter(io.BytesIO(), secrets)
            try:
                expected = sanitize(body, secrets)
                self.assertEqual(json.loads(writer.encode(body)), expected)
                self.assertEqual(json.loads(writer.encode(body)), expected)
                self.assertGreater(writer._cached_text.cache_info().hits, 0)
                for index in range(200):
                    writer.encode(f"unique text {index}")
                self.assertLessEqual(writer._cached_text.cache_info().currsize, 128)
                before = writer._cached_text.cache_info()
                large = secret + "x" * 65537
                self.assertEqual(json.loads(writer.encode(large)), sanitize(large, secrets))
                self.assertEqual(writer._cached_text.cache_info(), before)
            finally:
                writer.close()
            self.assertEqual(writer._cached_text.cache_info().currsize, 0)

    def test_single_export_deduplication_cancellation_and_partial_cleanup(self):
        entered, proceed = threading.Event(), threading.Event()
        def slow_export(account, query, target, archive, progress):
            target.write(b"partial")
            entered.set()
            proceed.wait(5)
            progress("events", 1, 10)
        with patch("astra_backend.analysis_export_jobs.write_bundle", side_effect=slow_export):
            job = self.jobs.start("session", "account", self.query, self.archive)
            self.assertTrue(entered.wait(5))
            duplicate = self.jobs.start("session", "account", self.query, self.archive)
            self.assertEqual(job["id"], duplicate["id"])
            with self.assertRaises(ExportBusy):
                self.jobs.start("session", "account", {**self.query, "inst": "ETH"}, self.archive)
            with self.assertRaises(KeyError):
                self.jobs.get("other-session", job["id"])
            with self.assertRaises(KeyError):
                self.jobs.cancel("other-session", job["id"])
            self.assertEqual(self.jobs.cancel("session", job["id"])["state"], "cancelling")
            proceed.set()
            self.assertEqual(self.finished(job)["state"], "cancelled")
            self.assertFalse(self.jobs.jobs[job["id"]]["path"].exists())

    def test_disk_full_failure_is_reported_and_removes_partial_file(self):
        def full(account, query, target, archive, progress):
            target.write(b"partial")
            raise OSError(errno.ENOSPC, "fixture")
        with patch("astra_backend.analysis_export_jobs.write_bundle", side_effect=full), \
             self.assertLogs("astra_backend.analysis_export_jobs", level="ERROR"):
            job = self.jobs.start("session", "account", self.query, self.archive)
            result = self.finished(job)
        self.assertEqual((result["state"], result["error"]), ("failed", "disk_full"))
        self.assertFalse(self.jobs.jobs[job["id"]]["path"].exists())

    def test_expired_artifact_cleanup_waits_for_active_download(self):
        job = self.jobs.start("session", "account", self.query, self.archive)
        self.assertEqual(self.finished(job)["state"], "ready")
        path = self.jobs.acquire_file("session", job["id"])
        self.jobs.jobs[job["id"]]["expires_at"] = time.time() - 1
        old_time = time.time() - 7200
        os.utime(path, (old_time, old_time))
        self.jobs.cleanup()
        self.assertTrue(path.exists())
        # Starting another export must not classify an active old download as abandoned.
        next_job = self.jobs.start("session", "account", self.query, self.archive)
        self.assertEqual(self.finished(next_job)["state"], "ready")
        self.assertTrue(path.exists())
        self.jobs.release_file("session", job["id"])
        self.assertFalse(path.exists())
        with self.assertRaises(KeyError):
            self.jobs.get("session", job["id"])

    def test_disconnected_download_releases_lease_and_expired_file(self):
        job = self.jobs.start("session", "account", self.query, self.archive)
        self.assertEqual(self.finished(job)["state"], "ready")
        path = self.jobs.acquire_file("session", job["id"])
        self.jobs.jobs[job["id"]]["expires_at"] = time.time() - 1
        response = ExportFileResponse(path, background=BackgroundTask(self.jobs.release_file, "session", job["id"]))
        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("fixture disconnect")
        async def receive():
            return {"type": "http.disconnect"}
        with self.assertRaisesRegex(OSError, "fixture disconnect"):
            asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
        self.assertFalse(path.exists())
        self.assertNotIn(job["id"], self.jobs.jobs)

    def test_async_api_cookie_download_authentication_retry_and_ranges(self):
        app = FastAPI()
        app.add_middleware(GZipMiddleware, minimum_size=1)
        def auth(x_astra_session=None):
            if x_astra_session not in ("session", "other-session"):
                raise HTTPException(status_code=401)
        install_routes(app, auth)
        self.jobs = app.state.analysis_export_jobs
        client = TestClient(app)
        prefix = "/api/v1/admin/analysis/exports"
        headers = {"X-Astra-Session": "session"}
        query = "?account=account&start=2026-09-01&end=2026-10-01"
        self.assertEqual(client.post(prefix + query).status_code, 401)
        with patch.object(service, "export_bundle", side_effect=AssertionError("in-memory ZIP")):
            response = client.post(prefix + query, headers=headers)
            self.assertEqual(response.status_code, 202)
            job = response.json()
            self.assertEqual(self.finished(job)["state"], "ready")
        base = prefix + "/" + job["id"]
        for method, path in (("get", base), ("delete", base), ("post", base + "/download"), ("get", base + "/file")):
            self.assertEqual(getattr(client, method)(path).status_code, 401)
            self.assertEqual(getattr(client, method)(path, headers={"X-Astra-Session": "other-session"}).status_code, 404)
        prepared = client.post(base + "/download", headers=headers)
        self.assertEqual(prepared.status_code, 200)
        cookie = prepared.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=" + base + "/file", cookie)
        self.assertNotIn("session", prepared.json()["url"])
        # The scoped cookie permits native download but cannot authenticate status APIs.
        self.assertEqual(client.get(base).status_code, 401)
        response = client.get(prepared.json()["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-encoding"], "identity")
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            self.assertIsNone(z.testzip())
        repeated = client.get(prepared.json()["url"])
        self.assertEqual(repeated.content, response.content)
        ranged = client.get(prepared.json()["url"], headers={"Range": "bytes=0-31"})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, response.content[:32])
        self.assertEqual(self.jobs.jobs[job["id"]]["downloads"], 0)
        client.cookies.clear()
        self.assertEqual(client.get(prepared.json()["url"]).status_code, 401)


if __name__ == "__main__":
    unittest.main()
