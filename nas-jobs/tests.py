#!/usr/bin/env python3
"""Tests for the scheduler's catch-up logic and the NAS-side target export."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "status"))
os.environ.setdefault("HOMELAB_STATUS_DIR", tempfile.mkdtemp())

import scheduler  # noqa: E402


def job(name):
    return next(j for j in scheduler.JOBS if j.name == name)


class TestDue(unittest.TestCase):
    def test_a_daily_job_never_run_is_due_after_its_slot(self):
        now = datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc).astimezone()
        self.assertTrue(scheduler.due(job("vault-doctor"), now, None))

    def test_a_daily_job_is_not_due_before_its_slot_has_ever_passed_today_if_it_ran_since_yesterday(self):
        now = datetime(2026, 10, 3, 2, 0).astimezone()
        last = now - timedelta(hours=22)
        self.assertFalse(scheduler.due(job("vault-doctor"), now, last))

    def test_a_job_that_ran_after_todays_slot_is_not_repeated(self):
        now = datetime(2026, 10, 3, 9, 0).astimezone()
        last = datetime(2026, 10, 3, 4, 1).astimezone()
        self.assertFalse(scheduler.due(job("vault-doctor"), now, last))

    def test_a_missed_slot_is_caught_up(self):
        now = datetime(2026, 10, 3, 9, 0).astimezone()
        last = datetime(2026, 10, 2, 4, 1).astimezone()
        self.assertTrue(scheduler.due(job("vault-doctor"), now, last))

    def test_the_live_job_repeats_on_its_interval(self):
        now = datetime(2026, 10, 3, 9, 0).astimezone()
        self.assertTrue(scheduler.due(job("live"), now, now - timedelta(minutes=31)))
        self.assertFalse(scheduler.due(job("live"), now, now - timedelta(minutes=5)))


class TestTargets(unittest.TestCase):
    def test_exported_targets_round_trip_through_load_targets(self):
        import registry

        saved = registry._TARGETS
        try:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "targets.json"
                import json

                p.write_text(json.dumps(registry.export_targets()))
                registry.load_targets(p)
                exported = registry.export_targets()
                self.assertTrue(exported)
                for app in registry.APPS:
                    if app.name in exported:
                        self.assertEqual(registry.base_url(app), exported[app.name]["url"], app.name)
                        want = app.internal_url or exported[app.name]["url"]
                        self.assertEqual(registry.extra_base_url(app), want, app.name)
        finally:
            registry._TARGETS = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
