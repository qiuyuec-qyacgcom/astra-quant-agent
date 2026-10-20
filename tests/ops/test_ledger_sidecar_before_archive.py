"""Offline regression for ledger commit / sidecar order."""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.ops.test_ledger_sync_sidecar import sfl


class SidecarBeforeArchiveTests(unittest.TestCase):
    def run_commit(self, fail_commit=False):
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
            with patch.object(sfl, "DATA_DIR", tmp), patch.object(
                sfl, "_FETCH_STATUS", {"okx": {"status": "failed", "reason": "fixture"}}
            ):
                if fail_commit:
                    with patch.object(sfl.os, "replace", side_effect=OSError("disk error")):
                        with self.assertRaises(OSError):
                            exec(code, scope)
                    self.assertEqual(json.loads(sidecar.read_text())["generated_at"], "old")
                else:
                    exec(code, scope)

    def test_failed_ledger_commit_does_not_refresh_sidecar(self):
        self.run_commit(fail_commit=True)
