"""Tests for the status collector.

`python3 status/tests.py` — stdlib unittest on purpose. homelab is a
shell-script repo with no Python packaging, and a status tool whose tests need
an install is a status tool nobody runs.

Every case here is a way the portal reported something untrue. A dashboard that
cries wolf is worse than none: it trains you to ignore it, and then it is
useless on the day it is right. So each test names the false reading it exists
to prevent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect  # noqa: E402
import registry  # noqa: E402
import render  # noqa: E402


def row(doc_id: str, *, deleted: bool = False, conflicts: bool = False) -> dict:
    doc: dict = {"path": doc_id}
    if deleted:
        doc["deleted"] = True
    if conflicts:
        doc["_conflicts"] = ["2-abc"]
    return {"id": doc_id, "doc": doc}


class TestTombstonesAreNotNotes(unittest.TestCase):
    """LiveSync deletes by writing `deleted: true` into a document it keeps.

    `_all_docs` therefore lists deleted notes exactly like present ones. The
    first version of this collector trusted the row list and reported 50
    duplicate notes in a vault that had none — 57 of the 58 it found were
    tombstones the reaper had already cleaned.
    """

    def test_a_deleted_note_is_not_live(self) -> None:
        stats = collect.vault_stats([row("a.md"), row("b.md", deleted=True)])
        self.assertEqual(stats["live"], 1)
        self.assertEqual(stats["tombstones"], 1)

    def test_a_reaped_duplicate_is_not_reported(self) -> None:
        """The exact false alarm: the copy is gone, the tombstone remains."""
        stats = collect.vault_stats([row("note.md"), row("note 2.md", deleted=True)])
        self.assertEqual(stats["duplicate_suffixed"], 0)

    def test_a_live_duplicate_is_still_reported(self) -> None:
        stats = collect.vault_stats([row("note.md"), row("note 2.md")])
        self.assertEqual(stats["duplicate_suffixed"], 1)

    def test_a_suffixed_note_with_no_original_is_not_a_duplicate(self) -> None:
        """`part 2.md` on its own is a document, not a copy of anything."""
        stats = collect.vault_stats([row("part 2.md")])
        self.assertEqual(stats["duplicate_suffixed"], 0)

    def test_the_web_clippers_own_naming_is_not_a_duplicate(self) -> None:
        """`10 raw/` is full of legitimate " 2" files straight from the Web
        Clipper, which is why the janitor never reaps there either."""
        stats = collect.vault_stats([row("10 raw/x.md"), row("10 raw/x 2.md")])
        self.assertEqual(stats["duplicate_suffixed"], 0)

    def test_conflicts_on_a_deleted_document_are_not_counted(self) -> None:
        stats = collect.vault_stats([row("gone.md", deleted=True, conflicts=True)])
        self.assertEqual(stats["conflicts"], 0)


class TestFeedAlertsNeedPersistence(unittest.TestCase):
    """One bad poll is weather; a feed that keeps failing is a fault.

    The run that reported 8 of 23 failures took twenty minutes against a normal
    eighty-five seconds, and the next poll was clean with every feed's
    `consecutive_failures` still 0. Alerting on the last run's count cannot tell
    those apart.
    """

    def _extra(self, feeds: list[dict], failed: int = 0) -> dict:
        return {
            "status": {"feeds": feeds},
            "runs": {"jobs": {"ingest": {"summary": {"feeds_failed": failed, "feeds_polled": 23}}}},
        }

    def test_a_single_bad_poll_is_context_not_an_alarm(self) -> None:
        alerts = render._feed_alerts(self._extra([{"slug": "a", "consecutive_failures": 0}], failed=8))
        self.assertEqual([t for t, _ in alerts], ["info"])

    def test_a_persistently_failing_feed_is_a_warning(self) -> None:
        alerts = render._feed_alerts(self._extra([{"slug": "flaky", "consecutive_failures": 4}]))
        self.assertEqual([t for t, _ in alerts], ["warn"])
        self.assertIn("flaky", alerts[0][1])

    def test_a_feed_given_up_on_is_an_error(self) -> None:
        alerts = render._feed_alerts(
            self._extra([{"slug": "dead", "consecutive_failures": 9, "circuit_open": True}])
        )
        self.assertEqual([t for t, _ in alerts], ["bad"])

    def test_healthy_feeds_say_nothing(self) -> None:
        self.assertEqual(render._feed_alerts(self._extra([{"slug": "a", "consecutive_failures": 0}])), [])


class TestTheVaultDestinationIsChecked(unittest.TestCase):
    """security-digest kept writing to `tastings` for three days after the
    rename — healthy, up, jobs succeeding, notes landing in a database no device
    reads. Every other signal was green. Only the destination was wrong."""

    def _app(self, expect="the_brain"):
        return registry.App(name="x", repo="x", container="x", expect_vault_db=expect)

    def test_the_right_database_passes(self) -> None:
        r = collect.vault_db_check(self._app(), ["VAULT_DB=the_brain"])
        self.assertEqual(r["state"], "ok")

    def test_the_old_database_is_an_error(self) -> None:
        r = collect.vault_db_check(self._app(), ["VAULT_DB=tastings"])
        self.assertEqual(r["state"], "wrong")
        self.assertEqual(r["found"], {"VAULT_DB": "tastings"})

    def test_a_config_file_setting_counts_too(self) -> None:
        """Two apps take it from config.yaml, not the environment."""
        r = collect.vault_db_check(self._app(), [], config_db="the_brain")
        self.assertEqual(r["state"], "ok")

    def test_no_setting_anywhere_is_unknown_not_ok(self) -> None:
        r = collect.vault_db_check(self._app(), [])
        self.assertEqual(r["state"], "unknown")

    def test_an_app_with_no_expectation_is_not_checked(self) -> None:
        self.assertIsNone(collect.vault_db_check(self._app(expect=None), ["VAULT_DB=whatever"]))


class TestHealthVocabulary(unittest.TestCase):
    """Six services, several vocabularies. Normalising is the collector's job."""

    def test_each_dialect_reads_as_healthy(self) -> None:
        for body in ({"status": "ok"}, {"status": "success"}, {"ok": True}, {"status": "healthy"}):
            with self.subTest(body=body):
                self.assertTrue(render._age is not None)  # module imported
                status = str(body.get("status", "")).lower()
                healthy = status in ("ok", "healthy", "up", "success") or body.get("ok") is True
                self.assertTrue(healthy)

    def test_a_failing_sub_check_beats_an_ok_status(self) -> None:
        body = {"status": "ok", "checks": {"db": "ok", "vault": "unreachable"}}
        bad = [k for k, v in body["checks"].items() if str(v).lower() not in ("ok", "true", "healthy")]
        self.assertEqual(bad, ["vault"])


class TestBusyIsNotDown(unittest.TestCase):
    """vault-ask stalls for minutes rebuilding its index on every restart,
    answering 200 in its own log while every probe times out. Flagging that red
    each time is how a page teaches you to scroll past red."""

    def _app(self, error, state="running"):
        return {"health": {"probed": True, "ok": False, "error": error}, "container": {"state": state}}

    def test_a_timeout_on_a_running_container_is_a_warning(self) -> None:
        tone, text = render.health_verdict(self._app("TimeoutError"))
        self.assertEqual(tone, "warn")
        self.assertIn("busy", text)

    def test_a_refused_connection_is_an_error(self) -> None:
        tone, _ = render.health_verdict(self._app("URLError"))
        self.assertEqual(tone, "bad")

    def test_a_timeout_on_a_stopped_container_is_an_error(self) -> None:
        tone, _ = render.health_verdict(self._app("TimeoutError", state="exited"))
        self.assertEqual(tone, "bad")

    def test_a_reported_failing_subsystem_is_an_error(self) -> None:
        app = {"health": {"probed": True, "ok": False, "failing_checks": ["vault"]},
               "container": {"state": "running"}}
        tone, text = render.health_verdict(app)
        self.assertEqual(tone, "bad")
        self.assertIn("vault", text)

    def test_a_healthy_app_says_nothing(self) -> None:
        self.assertIsNone(render.health_verdict({"health": {"probed": True, "ok": True}}))


class TestFreshnessIsLoud(unittest.TestCase):
    """The failure mode of a dashboard is being old while looking current."""

    def test_a_missing_timestamp_is_infinitely_stale(self) -> None:
        mins, text = render._age(None)
        self.assertEqual(text, "never")
        self.assertGreater(mins, render.STALE_MINUTES["fast"])

    def test_an_old_timestamp_reads_in_days(self) -> None:
        from datetime import UTC, datetime, timedelta

        _, text = render._age((datetime.now(UTC) - timedelta(days=3)).isoformat())
        self.assertIn("d ago", text)


class TestATimeoutIsNotAPass(unittest.TestCase):
    """A suite that never reported must not read as "0 failures"."""

    def test_a_killed_run_says_so(self) -> None:
        rc, _ = collect._run(["sleep", "5"], timeout=1)
        self.assertEqual(rc, 124)

    def test_a_timeout_leaves_no_survivors(self) -> None:
        """The child gets its own process group, so a hung suite cannot outlive
        its timeout and wedge the next run."""
        import subprocess

        collect._run(["sh", "-c", "sleep 30 & wait"], timeout=1)
        found = subprocess.run(["pgrep", "-f", "sleep 30"], capture_output=True, text=True)
        self.assertEqual(found.stdout.strip(), "")


class TestNothingLeaks(unittest.TestCase):
    def test_the_registry_holds_no_real_addresses(self) -> None:
        """Real values live in each project's git-ignored .deploy.env."""
        source = (Path(__file__).resolve().parent / "registry.py").read_text(encoding="utf-8")
        import re

        self.assertEqual(re.findall(r"\b10\.0\.0\.\d+\b", source), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
