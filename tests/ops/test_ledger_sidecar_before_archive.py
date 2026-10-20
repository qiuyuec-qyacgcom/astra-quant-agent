"""Offline regression for ledger commit / sidecar / optional archive order."""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.ops.test_ledger_sync_sidecar import sfl


class SidecarBeforeArchiveTests(unittest.TestCase):
    def run_commit(self, archive_effect=None, fail_commit=False):
        tree = ast.parse(Path(sfl.__file__).read_text(encoding="utf-8"))
        body = next(n.body for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "build_lifecycle_ledger")
        start = next(i for i, n in enumerate(body)
                     if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple)
                     and [x.id for x in n.targets[0].elts] == ["fd", "tmp_path"])
        end = next(i for i, n in enumerate(body)
                   if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Name)
                   and n.value.func.id == "notify_newly_closed_trades")
        code = compile(ast.Module(body=body[start:end], type_ignores=[]),
                       sfl.__file__, "exec")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "trading_ledger.json"
            sidecar = Path(tmp) / "ledger_sync_status.json"
            sidecar.write_text('{"generated_at":"old"}', encoding="utf-8")
            scope = dict(vars(sfl), DATA_DIR=tmp, LEDGER_JSON_FILE=str(ledger),
                         combined_trades=[{"id": "fixture"}], pos_data=[],
                         env=SimpleNamespace(configured=True, simulated=True))
            def archive(**kwargs):
                self.assertEqual(json.loads(ledger.read_text())[0]["id"], "fixture")
                payload = json.loads(sidecar.read_text())
                self.assertNotEqual(payload["generated_at"], "old")
                self.assertEqual(payload["venues"]["okx"]["status"], "failed")
                if archive_effect:
                    raise archive_effect
            with patch.object(sfl, "DATA_DIR", tmp), patch.object(
                sfl, "_FETCH_STATUS", {"okx": {"status": "failed", "reason": "fixture"}}
            ), patch("astra_backend.analysis_capture.enabled", return_value=True), patch(
                "astra_backend.analysis_sync.sync_archive", side_effect=archive
            ) as sync:
                if fail_commit:
                    with patch.object(sfl.os, "replace", side_effect=OSError("disk error")):
                        with self.assertRaises(OSError):
                            exec(code, scope)
                    self.assertEqual(json.loads(sidecar.read_text())["generated_at"], "old")
                    sync.assert_not_called()
                elif archive_effect:
                    with self.assertRaises(SystemExit):
                        exec(code, scope)
                    sync.assert_called_once()
                else:
                    exec(code, scope)
                    sync.assert_called_once()

    def test_sidecar_survives_archive_process_interruption(self):
        self.run_commit(archive_effect=SystemExit(124))

    def test_failed_ledger_commit_does_not_refresh_sidecar(self):
        self.run_commit(fail_commit=True)

    def test_archive_observes_committed_ledger_and_honest_failure_status(self):
        self.run_commit()
