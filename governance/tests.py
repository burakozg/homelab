"""Tests for the governance site build — `python3 governance/tests.py`.

Each case is a way the site could tell a reader something untrue: an age frozen
at build time, a model that is configured but missing from the hardware table
and says nothing, a template token left in a published page.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402

SNAP = {
    "fast": {"collected_at": "2026-01-01T00:00:00+00:00", "apps": {}},
    "slow": {"collected_at": "2026-01-01T00:00:00+00:00"},
}


class StatusBody(unittest.TestCase):
    def test_ages_are_recomputed_in_the_browser(self):
        # Rendered ages freeze at build time; a page opened tomorrow would still
        # say "12 min ago" unless every age carries its timestamp.
        _, body = build.render_body(SNAP)
        self.assertGreaterEqual(body.count('data-ts="2026-01-01T00:00:00+00:00"'), 5)

    def test_verdict_colour_is_a_class_not_baked_css(self):
        verdict, body = build.render_body(SNAP)
        self.assertIn(f'class="mark {verdict}"', body)


class Models(unittest.TestCase):
    def _scan(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app").mkdir()
            (root / "app" / "config.yaml").write_text(text)
            return build.configured_models(root)

    def test_finds_model_ids_and_skips_comments_and_nulls(self):
        found = self._scan(
            "models:\n  generation: openrouter/google/gemini-2.5-flash\n"
            "  embedding_base_url: null\n  # model: not/used\n  - model: kokoro\n"
        )
        self.assertEqual(
            found,
            [("app", "generation", "google/gemini-2.5-flash"), ("app", "model", "kokoro")],
        )

    def test_unknown_model_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "app").mkdir()
            (root / "app" / "config.yaml").write_text("model: nobody/never-heard-of-it\n")
            orig = build.registry.PROJECTS
            build.registry.PROJECTS = root
            try:
                self.assertIn("not in table", build.models_page())
            finally:
                build.registry.PROJECTS = orig


class Architecture(unittest.TestCase):
    def test_no_template_token_survives(self):
        self.assertNotIn("@@", build.architecture_page())


if __name__ == "__main__":
    unittest.main()
