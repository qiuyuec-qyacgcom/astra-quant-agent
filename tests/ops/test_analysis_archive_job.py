import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from scripts.sync_analysis_archive import main
from scripts.file_lock import LockContended

class ArchiveJobTests(unittest.TestCase):
    def setUp(self):
        def start(name, **kwargs):
            p = patch(name, **kwargs)
            value = p.start()
            self.addCleanup(p.stop)
            return value
        start('astra_backend.analysis_capture.enabled', return_value=True)
        self.fault = start('astra_backend.analysis_capture.fault')
        self.lock = start('scripts.file_lock.cycle_lock', return_value=nullcontext())
        start('scripts.okx_runtime.current_environment', return_value=SimpleNamespace(configured=True))
        self.positions = start('scripts.okx_rest.positions', return_value=[])
        self.sync = start('astra_backend.analysis_sync.sync_archive')

    def test_success_passes_current_positions(self):
        self.assertEqual(main(), 0)
        self.sync.assert_called_once_with(positions=[])

    def test_contention_skips_without_exchange_request(self):
        self.lock.side_effect = LockContended('fixture')
        self.assertEqual(main(), 0)
        self.positions.assert_not_called()
        self.sync.assert_not_called()

    def test_invalid_positions_fails_without_reconciliation(self):
        self.positions.return_value = None
        self.assertEqual(main(), 1)
        self.sync.assert_not_called()
        self.fault.assert_called_once()

    def test_sync_exception_is_reported(self):
        self.sync.side_effect = RuntimeError('fixture')
        self.assertEqual(main(), 1)
        self.fault.assert_called_once()
