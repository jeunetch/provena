"""Tests for the committed demo artifact and the pre-publication check.

The demo is the one file in this repository that is meant to be served to the
public. Two things about it are worth enforcing mechanically: it must be
self-contained (the hosting path has no build step and no network), and it
must never have been built from anything but the bundled synthetic example.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts import check_publishable  # noqa: E402

ARTIFACT = pathlib.Path("docs/index.html")
DATA_RE = re.compile(
    r'<script id="triage-data" type="application/json">(.*?)</script>', re.S
)


def embedded(path: pathlib.Path) -> dict:
    match = DATA_RE.search(path.read_text(encoding="utf-8"))
    assert match, "no embedded triage data"
    return json.loads(match.group(1))


class DemoArtifactTests(unittest.TestCase):
    def setUp(self):
        if not ARTIFACT.exists():
            self.skipTest(f"{ARTIFACT} not built")
        self.text = ARTIFACT.read_text(encoding="utf-8")
        self.data = embedded(ARTIFACT)

    def test_it_is_served_from_the_github_pages_docs_convention(self):
        self.assertEqual(ARTIFACT.name, "index.html")
        self.assertEqual(ARTIFACT.parent.name, "docs")

    def test_it_fetches_nothing(self):
        # A strict no-network artifact: no script, stylesheet, image or frame
        # may reference an external host. The hosting path cannot fix this at
        # serve time because there is no build step.
        external = re.findall(
            r"<(?:script|link|img|iframe)[^>]+(?:src|href)=[\"']https?:[^\"']*",
            self.text,
        )
        self.assertEqual(external, [])

    def test_it_was_built_from_a_bundled_synthetic_example(self):
        self.assertTrue(
            str(self.data["input_file"]).startswith("examples/"),
            f"demo built from {self.data['input_file']!r}",
        )

    def test_it_carries_the_disclaimer_and_the_methodology_sources(self):
        self.assertIn("machine-generated triage", self.data["disclaimer"])
        self.assertIn("not a legal determination", self.data["disclaimer"])
        self.assertTrue(self.data["criteria_screened"])
        for flag in self._all_flags():
            self.assertTrue(flag["methodology"], flag["rule_id"])
            self.assertTrue(flag["rule_statement"], flag["rule_id"])

    def test_it_contains_no_score_and_no_clearance_language(self):
        for banned in ("risk_score", "low risk", "cleared", "no risk"):
            self.assertNotIn(banned, json.dumps(self.data).lower(), banned)

    def test_it_states_whether_any_sentence_was_machine_generated(self):
        # A reader must be able to answer that at a glance rather than infer
        # it from the absence of a paragraph.
        self.assertIn("llm_layer", self.data)
        self.assertIn("enabled", self.data["llm_layer"])
        self.assertIn('id="llm-note"', self.text)
        self.assertIn("Explanations", self.text)

    def test_every_generated_sentence_is_shown_beside_its_cited_fields(self):
        # Only meaningful once the layer has run; the point of the assertion is
        # that a generated sentence can never appear without the fields it was
        # checked against.
        for flag in self._all_flags():
            if flag.get("llm_explanation"):
                self.assertTrue(flag["cited_fields"], flag["rule_id"])

    def _all_flags(self):
        for obj in self.data["objects"]:
            yield from obj["persecution_context_flags"]
            yield from obj["documentation_quality_flags"]


class PublishabilityCheckTests(unittest.TestCase):
    # Fixtures use a value that bears no relationship to any real credential.
    # An earlier revision of this file reused the first characters of the token
    # from the 2026-08-09 incident, which put a fragment of a real secret into
    # a file destined to be public — the exact mistake the scanner exists to
    # catch, in the scanner's own tests.
    FAKE_KEY = "NOTAREALKEY-0000000000000000"
    # Assembled rather than written literally, so this file never contains a
    # string shaped like a filled-in assignment that the scanner must then be
    # taught to ignore. Every exemption is a place a real leak could hide.
    KEY_VAR = "INFOMANIAK_API_KEY"

    def scan(self, text: str, name: str = "sample.txt") -> list[str]:
        return check_publishable.scan_text(name, text)

    def test_a_filled_in_key_is_caught(self):
        self.assertTrue(self.scan(f"{self.KEY_VAR}={self.FAKE_KEY}"))

    def test_an_empty_placeholder_is_not_a_finding(self):
        self.assertEqual(
            check_publishable.scan_text(
                ".env.example", "INFOMANIAK_API_KEY=\n\n# next line",
                allow_placeholders=True,
            ),
            [],
        )

    def test_a_bearer_token_is_caught(self):
        self.assertTrue(self.scan("Authorization: Bearer abcdef0123456789abcdef"))

    def test_the_known_safe_list_keys_on_the_value_not_the_file(self):
        # A blanket per-file exemption is how a real leak hides behind a test
        # fixture, so a NEW key in an already-listed file must still fire.
        listed = "tests/test_live_harness.py"
        self.assertEqual(
            check_publishable.scan_text(listed, 'INFOMANIAK_API_KEY="sk-quoted"'), []
        )
        self.assertTrue(
            check_publishable.scan_text(listed, f"{self.KEY_VAR}={self.FAKE_KEY}")
        )

    def test_an_artifact_built_from_real_records_is_refused(self):
        payload = {
            "input_file": "/home/curator/accessions_2026.csv",
            "disclaimer": "x",
            "objects": [],
        }
        findings = self._scan_artifact(payload)
        self.assertTrue(any("not one of the bundled" in f for f in findings))

    def test_an_artifact_built_from_the_example_is_accepted(self):
        payload = {
            "input_file": "examples/example_input.csv",
            "disclaimer": "x",
            "objects": [],
        }
        self.assertEqual(self._scan_artifact(payload), [])

    def test_a_missing_disclaimer_is_a_finding(self):
        payload = {"input_file": "examples/example_input.csv", "objects": []}
        self.assertTrue(any("disclaimer" in f for f in self._scan_artifact(payload)))

    def test_a_file_that_is_not_a_report_is_a_finding(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("<html><body>hello</body></html>")
            path = pathlib.Path(handle.name)
        self.assertTrue(
            any("no embedded triage data" in f for f in check_publishable.scan_artifact(path))
        )

    def test_this_repository_s_history_carries_no_credential(self):
        # The invariant worth keeping true forever. A secret removed from the
        # tip is still reachable in history, and publishing a repository
        # publishes its history — so a commit that puts a credential into a
        # tracked config file must fail the suite, not merely be reverted
        # later. This repository was created as a single initial commit for
        # exactly this reason.
        self.assertEqual(check_publishable.scan_history(), [])

    def test_the_history_scan_would_catch_one(self):
        # The companion to the assertion above: an empty result has to mean
        # "nothing there", not "the scan stopped working". A diff line is what
        # scan_history feeds to the matcher, so this exercises the same path.
        self.assertTrue(
            check_publishable.scan_text(
                "git history", f"{self.KEY_VAR}={self.FAKE_KEY}"
            )
        )

    def test_the_script_runs_as_a_command(self):
        result = subprocess.run(
            [sys.executable, "scripts/check_publishable.py", "--skip-history"],
            capture_output=True,
            text=True,
        )
        self.assertIn("Working tree", result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)

    def _scan_artifact(self, payload: dict) -> list[str]:
        html = (
            '<script id="triage-data" type="application/json">'
            + json.dumps(payload)
            + "</script>"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(html)
            path = pathlib.Path(handle.name)
        return check_publishable.scan_artifact(path)


if __name__ == "__main__":
    unittest.main()
