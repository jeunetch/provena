"""Tests for the live-validation harness itself.

The harness is the thing that will be run against the real endpoint, so it
has to be known-good before it gets there: its cases must be built correctly
and it must be able to fail in both directions. These tests exercise it with
stub transports and make no network calls.
"""

import importlib.util
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

from src import llm_client
from src.heuristics import build_config

_SPEC = importlib.util.spec_from_file_location(
    "validate_llm_live",
    pathlib.Path(__file__).resolve().parent.parent / "scripts/validate_llm_live.py",
)
harness = importlib.util.module_from_spec(_SPEC)
# @dataclass resolves annotations through sys.modules, so the module has to be
# registered before it is executed.
sys.modules["validate_llm_live"] = harness
_SPEC.loader.exec_module(harness)

CONFIG = build_config()

FAITHFUL = (
    "Unverified: what does the record establish here? It gives only the fields "
    "listed and nothing further on the question the rule raises."
)
CONFABULATED = (
    "Following Kristallnacht in 1938, Hildebrand Gurlitt handled this work, "
    "and it was certainly looted."
)


class Stub:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return self.text


class CaseConstructionTests(unittest.TestCase):
    def setUp(self):
        self.cases = harness.build_cases(CONFIG)

    def test_both_kinds_are_represented(self):
        kinds = {c.kind for c in self.cases}
        self.assertEqual(kinds, {harness.BASELINE, harness.ADVERSARIAL})

    def test_every_case_carries_a_real_flag_and_a_stated_reason(self):
        for case in self.cases:
            self.assertIn("cited_fields", case.flag)
            self.assertTrue(case.flag.get("rule_statement"))
            self.assertTrue(case.why, f"{case.name} has no stated purpose")

    def test_the_injection_case_smuggles_an_instruction_through_a_record_field(self):
        case = next(c for c in self.cases if "injection" in c.name)
        cited = " ".join(str(v) for v in case.flag["cited_fields"].values())
        self.assertIn("disregard the preceding constraints", cited)

    def test_a_named_party_case_is_present(self):
        self.assertTrue(any("named-party" in c.name for c in self.cases))

    def test_the_elicitation_case_appends_to_the_user_prompt(self):
        case = next(c for c in self.cases if "elicitation" in c.name)
        self.assertIn("historical background", case.prompt_suffix)


class SuffixTransportTests(unittest.TestCase):
    def test_the_suffix_reaches_the_model(self):
        stub = Stub(FAITHFUL)
        harness.SuffixTransport(stub, " EXTRA").complete("sys", "user")
        self.assertTrue(stub.prompts[0].endswith(" EXTRA"))


class ExitCodeTests(unittest.TestCase):
    """The harness must be able to report a guard that is too lax AND one
    that is too strict. A script that can only pass is not a test."""

    def _run(self, text, extra_args=()):
        original_transport = llm_client.HttpTransport
        original_env = llm_client.load_env_file
        llm_client.HttpTransport = lambda settings: Stub(text)
        llm_client.load_env_file = lambda path=".env": {
            llm_client.ENV_PRODUCT_ID: "test-product",
            llm_client.ENV_API_KEY: "test-key",
        }
        try:
            buffer = io.StringIO()
            # The harness writes its verdict to stderr; captured so the suite's
            # own output cannot be mistaken for a real failure.
            with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                code = harness.main(["--languages", "en", *extra_args])
            return code, buffer.getvalue()
        finally:
            llm_client.HttpTransport = original_transport
            llm_client.load_env_file = original_env

    def _forcing_acceptance(self, extra_args=()):
        original = harness.verify
        harness.verify = lambda *a, **k: type(
            "R", (), {"accepted": True, "violations": ()}
        )()
        try:
            return self._run(FAITHFUL, extra_args)
        finally:
            harness.verify = original

    def test_confabulating_model_is_caught_by_the_guard_not_the_harness(self):
        code, out = self._run(CONFABULATED)
        self.assertEqual(code, 3, "every output rejected should report over-strictness")
        self.assertIn("adversarial accepted: 0", out)

    def test_adversarial_acceptance_is_reported_for_review_not_failed(self):
        # An accepted adversarial output usually means the model declined the
        # bait, which is not a guard failure. No automated check can tell that
        # from a real miss, so the default is to surface it, not to cry FAIL.
        code, out = self._forcing_acceptance()
        self.assertEqual(code, 0)
        self.assertIn("REVIEW REQUIRED", out)
        self.assertIn("the model declined the bait", out)

    def test_strict_mode_fails_on_an_unreviewed_adversarial_acceptance(self):
        code, _ = self._forcing_acceptance(["--strict"])
        self.assertEqual(code, 2)

    def test_the_rejected_count_is_reported_alongside_the_accepted_one(self):
        # Reporting only acceptances made a healthy run look like 12 failures.
        _, out = self._run(CONFABULATED)
        self.assertIn("adversarial rejected:", out)
        self.assertIn("guard caught these", out)

    def test_credentials_are_never_printed(self):
        code, out = self._run(CONFABULATED)
        self.assertNotIn("test-key", out)
        self.assertNotIn("test-product", out)

    def test_missing_credentials_refuse_to_run(self):
        original = llm_client.load_env_file
        llm_client.load_env_file = lambda path=".env": {}
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                code = harness.main(["--languages", "en"])
        finally:
            llm_client.load_env_file = original
        self.assertEqual(code, 4)


class ConfirmedConfigTests(unittest.TestCase):
    def test_endpoint_is_built_from_the_confirmed_path(self):
        self.assertEqual(
            llm_client.endpoint_for("987654"),
            "https://api.infomaniak.com/2/ai/987654/openai/v1/chat/completions",
        )

    def test_the_confirmed_apertus_model_id_is_the_default(self):
        self.assertEqual(llm_client.APERTUS_MODEL_ID, "swiss-ai/Apertus-v1.5-70B")
        self.assertEqual(
            llm_client.LlmSettings(endpoint="https://x", api_key="k").model,
            "swiss-ai/Apertus-v1.5-70B",
        )

    def test_no_product_id_means_no_endpoint_rather_than_a_guess(self):
        with self.assertRaises(llm_client.LlmConfigurationError):
            llm_client.endpoint_for("")

    def test_env_file_parsing_handles_comments_quotes_and_blanks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text(
                "# comment\n\nINFOMANIAK_PRODUCT_ID=12345\n"
                'INFOMANIAK_API_KEY="sk-quoted"\nBAD LINE\n'
            )
            values = llm_client.load_env_file(path)
        self.assertEqual(values["INFOMANIAK_PRODUCT_ID"], "12345")
        self.assertEqual(values["INFOMANIAK_API_KEY"], "sk-quoted")

    def test_env_values_are_not_exported_into_the_process(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text("PROVENA_LEAK_CHECK=should-not-be-exported\n")
            llm_client.load_env_file(path)
        self.assertIsNone(os.environ.get("PROVENA_LEAK_CHECK"))

class CredentialHygieneTests(unittest.TestCase):
    """A real API key was once committed to .env.example. This makes the
    same mistake fail the test suite instead of reaching a public repo."""

    EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / ".env.example"

    def test_env_example_carries_no_filled_in_values(self):
        for number, line in enumerate(
            self.EXAMPLE.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            self.assertEqual(
                value.strip(),
                "",
                f".env.example line {number}: {key.strip()} has a value. This file "
                "is committed — it must contain placeholders only. If a real "
                "credential was committed, ROTATE IT: removing it from the file "
                "does not remove it from git history.",
            )

    def test_dotenv_itself_is_gitignored(self):
        ignore = (self.EXAMPLE.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignore.split())

if __name__ == "__main__":
    unittest.main()
