"""Regression for linear event indexing without dropping historical evidence."""
from unittest.mock import patch
from tests.test_analysis_archive import AnalysisTests, ACCOUNT, START, position
from astra_backend.analysis_store import lifecycle_id
from astra_backend.analysis_sync import reconcile


class ReconcileIndexTests(AnalysisTests):
    def test_event_index_keeps_latest_exit_and_all_extrema(self):
        rows = [position(i) for i in range(1, 21)]
        self.archive.raw(ACCOUNT, "positions-history", rows)
        tid = lifecycle_id(rows[0])
        for i, reason in enumerate(("old", "latest")):
            self.archive.event(ACCOUNT, "position.exit_reason",
                {"reason": reason, "confirmed": True}, id=f"exit-{i}",
                occurred_ms=START+i, trade_id=tid)
        for i, kind in enumerate(("position.manage.end", "position.sample")):
            self.archive.event(ACCOUNT, kind,
                {"tracker": {"highWaterMark": 110+i, "lowWaterMark": 90-i}},
                id=f"sample-{i}", occurred_ms=START+i, trade_id=tid)
        self.archive.event("other-account", "position.exit_reason",
            {"reason": "foreign"}, id="foreign-exit", occurred_ms=START+99, trade_id=tid)
        events = self.archive.events(ACCOUNT)
        class CountedEvents(list):
            scans = 0
            def __iter__(self):
                self.scans += 1
                return super().__iter__()
        counted = CountedEvents(events)
        with patch.object(self.archive, "reconciliation_events", return_value=counted):
            result = reconcile(self.archive, ACCOUNT, [])
        trade = next(t for t in result if t["id"] == tid)
        self.assertEqual(trade["exit_reason"], "latest")
        self.assertEqual(trade["exit_evidence"], "confirmed")
        self.assertAlmostEqual(trade["sampled_mfe_pct"], 11)
        self.assertAlmostEqual(trade["sampled_mae_pct"], 11)
        self.assertLessEqual(counted.scans, 3)

    def test_reconcile_does_not_load_unrelated_events(self):
        self.archive.raw(ACCOUNT, "positions-history", [position()])
        self.archive.event(ACCOUNT, "risk.rule", {}, id="noise")
        self.archive.event(ACCOUNT, "position.sample", {}, id="evidence")
        self.archive.event("foreign", "position.sample", {}, id="foreign")
        self.assertEqual([e["id"] for e in self.archive.reconciliation_events(ACCOUNT)], ["evidence"])
        with patch.object(self.archive, "events", side_effect=AssertionError("full scan")):
            self.assertEqual(len(reconcile(self.archive, ACCOUNT, [])), 1)

    def test_event_id_lookup_batches_and_isolates_accounts(self):
        self.archive.event(ACCOUNT, "order.fill", {}, id="mine")
        self.archive.event("foreign", "order.fill", {}, id="theirs")
        keys = [str(i) for i in range(901)] + ["mine", "mine", "theirs"]
        self.assertEqual(self.archive.existing_event_ids(ACCOUNT, keys), {"mine"})
        self.assertEqual(self.archive.existing_event_ids(ACCOUNT, []), set())
