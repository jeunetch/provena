"""Identity tests for reference-list matching.

These exist because the project had 320 tests and not one of them asserted
that a different person with a matching surname does not pull a list entry.
Everything about how a match is *rendered* was argued over carefully; whether
the right person was matched at all was not tested, and the matcher was
surname-only.

The cost asymmetry that justifies over-flagging elsewhere is inverted here,
and these tests are the place that is enforced rather than asserted in prose:

* a false positive on PC-003 costs a researcher an hour;
* a false positive here prints a 1946 investigative annotation about a named
  individual — who may have living heirs — beside an unrelated person's name,
  inside a document the institution circulates.

So the direction of error for name matching is *under*-matching, and these
tests fix that direction in both directions: the names that must not match,
and the names that must still match, so a future tightening cannot quietly
turn the rule off.
"""

from __future__ import annotations

import unittest

from src import name_matching
from src.name_matching import (
    MATCH_SURNAME_ONLY,
    PersonName,
    load_actor_list,
    load_aliu_list,
    match_entry,
    normalise,
    parse_person_name,
)

REAL_ALIU = "data/aliu_red_flag_names.json"
REAL_ACTORS = "data/confiscation_channel_actors.json"


class MatchingHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = list(load_aliu_list(REAL_ALIU).entries)
        cls.entries += list(load_actor_list(REAL_ACTORS).actors)

    def outcomes(self, supplied: str):
        found = []
        for entry in self.entries:
            outcome = match_entry((supplied,), entry.terms, entry.person_forms)
            if outcome is not None:
                found.append((entry.name, outcome))
        return found

    def assertNoMatch(self, supplied: str):
        found = self.outcomes(supplied)
        self.assertEqual(
            found,
            [],
            f"{supplied!r} matched {[n for n, _ in found]} — a shared surname "
            f"is not a shared identity",
        )

    def assertMatches(self, supplied: str):
        found = self.outcomes(supplied)
        self.assertTrue(found, f"{supplied!r} should still match but did not")
        return found[0][1]


class DifferentPersonSameSurnameTests(MatchingHarness):
    """The reported defect. Each of these is a plausible Swiss record."""

    def test_a_different_given_name_does_not_pull_a_person_entry(self):
        for supplied in (
            "Lange, Elisabeth",
            "Lange, Werner",
            "Elisabeth Lange",
            "Haberstock, Anna",
        ):
            with self.subTest(supplied=supplied):
                self.assertNoMatch(supplied)

    def test_a_person_does_not_pull_an_organisation_on_a_shared_surname(self):
        for supplied in ("Fischer, Ernst", "Lempertz, Maria", "Weinmuller, Josef"):
            with self.subTest(supplied=supplied):
                self.assertNoMatch(supplied)

    def test_a_middle_initial_cannot_carry_an_identification(self):
        # The entry is "Lange, Hans W.". Comparing any token to any token let
        # the middle initial "W." match any given name starting with W, so
        # "Lange, Werner" matched. Only the first given name identifies.
        entry = PersonName("lange", ("hans", "w"))
        self.assertFalse(PersonName("lange", ("werner",)).compatible_with(entry))
        self.assertFalse(PersonName("lange", ("wilhelm",)).compatible_with(entry))
        self.assertTrue(PersonName("lange", ("hans",)).compatible_with(entry))
        self.assertTrue(PersonName("lange", ("h",)).compatible_with(entry))


class TruePositivesStillMatchTests(MatchingHarness):
    """The other direction: tightening must not turn the rule off."""

    def test_the_named_individuals_still_match(self):
        for supplied in (
            "Haberstock, Karl",
            "Karl Haberstock",
            "Lange, Hans W.",
            "Hans W. Lange",
            "Lange, H.",
            "Wolff Metternich, Franz",
            "Fischer, Theodor",
        ):
            with self.subTest(supplied=supplied):
                self.assertTrue(self.assertMatches(supplied).identity_confirmed)

    def test_organisation_forms_still_match(self):
        for supplied in (
            "Kunsthaus Lempertz AG",
            "Galerie Fischer Lucerne",
            "Dorotheum",
            "ERR",
            "E.R.R.",
        ):
            with self.subTest(supplied=supplied):
                self.assertTrue(self.assertMatches(supplied).identity_confirmed)


class SurnameOnlyTests(MatchingHarness):
    """A record with no given name is ambiguous, and must say so."""

    def test_a_bare_surname_matches_but_does_not_confirm_identity(self):
        for supplied in ("Lange", "Weinmüller", "Wolff Metternich"):
            with self.subTest(supplied=supplied):
                outcome = self.assertMatches(supplied)
                self.assertEqual(outcome.basis, MATCH_SURNAME_ONLY)
                self.assertFalse(outcome.identity_confirmed)
                self.assertIn("different individual", outcome.note)

    def test_an_organisation_s_own_name_does_confirm(self):
        # "Dorotheum" is the entity, not somebody's surname.
        outcome = self.assertMatches("Dorotheum")
        self.assertTrue(outcome.identity_confirmed)
        self.assertEqual(outcome.note, "")


class GenericTokenTests(MatchingHarness):
    def test_two_generic_tokens_identify_nobody(self):
        # Both tokens are in GENERIC_TOKENS; the two-token reverse branch used
        # to return a match without consulting the generic list at all.
        self.assertNoMatch("Münchener Kunstversteigerungshaus")
        self.assertNoMatch("Kunstversteigerungshaus Munich")

    def test_a_single_generic_token_identifies_nobody(self):
        for supplied in ("Galerie", "Auktionshaus", "Vienna", "Collection"):
            with self.subTest(supplied=supplied):
                self.assertNoMatch(supplied)


class NormalisationTests(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        # The docstring always claimed this; the implementation did not do it,
        # leaving a double space where a comma was removed.
        self.assertEqual(normalise("Lange, Hans W."), "lange hans w")
        self.assertNotIn("  ", normalise("Wolff  Metternich ,  Franz"))

    def test_a_compound_surname_is_not_read_as_given_plus_surname(self):
        # Shape alone cannot tell "Wolff Metternich" from "Hans Lange"; only
        # the entry knows, which is why the exact-surname check runs first.
        self.assertEqual(
            parse_person_name("Hans Lange"), PersonName("lange", ("hans",))
        )

    def test_an_organisation_never_parses_as_a_person(self):
        for supplied in ("Galerie Fischer", "Kunsthaus Lempertz", "Dorotheum Wien"):
            with self.subTest(supplied=supplied):
                self.assertIsNone(parse_person_name(supplied))


class DateGateTests(unittest.TestCase):
    """NM-001 gates on the screening band, like every other dated rule."""

    def test_a_modern_record_does_not_raise_a_name_flag(self):
        from src.csv_adapter import load_chains
        from src.heuristics import build_config, screen_object
        import tempfile
        import pathlib

        rows = [
            "object_id,object_title,object_class,owner_name,owner_name_variants,"
            "date_from,date_to,date_precision,transaction_state,location,"
            "source_citation,export_licence_present,is_institution_acquisition,"
            "catalogue_reference,owner_stated_in_catalogue,"
            "restitution_recipient_type,notes",
            'M-1,Portrait,painting,"Lange, Hans W.",,2019,,year,purchase,'
            '"Zurich, Switzerland",Invoice 2019,,,,,,',
            'M-2,Portrait,painting,"Lange, Hans W.",,1941,,year,purchase,'
            '"Berlin, Germany",Invoice 1941,,,,,,',
        ]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("\n".join(rows) + "\n")
            path = pathlib.Path(handle.name)

        config = build_config()
        results = {
            r["object_id"]: r
            for r in (screen_object(c, config) for c in load_chains(path))
        }
        modern = [
            f["rule_id"] for f in results["M-1"]["persecution_context_flags"]
        ]
        in_band = [
            f["rule_id"] for f in results["M-2"]["persecution_context_flags"]
        ]
        self.assertNotIn("NM-001", modern, "a 2019 record raised a name flag")
        self.assertIn("NM-001", in_band, "an in-band record stopped flagging")

    def test_an_undated_record_reports_the_criterion_skipped(self):
        from src.heuristics import build_config, rule_aliu_name_match
        from src.schema import ObjectChain, build_record

        record = build_record(
            {
                "object_id": "U-1",
                "owner_name": "Lange, Hans W.",
                "date_precision": "",
            },
            "test:1",
            0,
        )
        _, _, skips = rule_aliu_name_match(
            ObjectChain("U-1", [record]), build_config()
        )
        self.assertTrue(skips, "an undated record must report the rule skipped")
        self.assertIn("could not run", " ".join(skips))


class CompoundSurnameTests(unittest.TestCase):
    """Less information must never produce more confidence.

    `_single_token_outcome` tested `known.surname == token`. A compound
    surname never equals one of its own tokens, so "Behr" — against an entry
    for "von Behr, Kurt" — fell through to the organisation default and came
    back MORE confident than "von Behr", with the identity caveat skipped: a
    person entry classified as an organisation, on the one branch that omits
    the caveat.

    The seed list masks this: its only compound-surname entry is exonerating.
    It stops being masked as the list grows toward the ~2,000 ALIU names,
    which are dense with von/van/de surnames. Hence a fixture rather than a
    bundled entry.
    """

    def setUp(self):
        import json
        import tempfile

        payload = {
            "_meta": {"status": "test fixture"},
            "entries": [
                {
                    "entry_id": "C1",
                    "name": "von Behr, Kurt",
                    "name_variants": ["Kurt von Behr"],
                    "entry_type": "documented_concern",
                    "entry_kind": "person",
                    "annotation": "Fixture entry.",
                    "source_url": "https://example.invalid/fixture",
                }
            ],
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle)
            path = handle.name
        # Read after the handle closes: an unflushed file loads as empty JSON.
        self.entry = name_matching.load_aliu_list(path).entries[0]

    def outcome(self, supplied: str):
        return match_entry((supplied,), self.entry.terms, self.entry.person_forms)

    def test_a_fragment_of_a_compound_surname_never_reads_as_an_organisation(self):
        for supplied in ("Behr", "von Behr"):
            with self.subTest(supplied=supplied):
                outcome = self.outcome(supplied)
                self.assertIsNotNone(outcome)
                self.assertEqual(outcome.basis, MATCH_SURNAME_ONLY)
                self.assertFalse(outcome.identity_confirmed)

    def test_a_shorter_form_is_never_more_confident_than_a_longer_one(self):
        # The invariant behind the defect, stated so it cannot come back.
        full = self.outcome("von Behr, Kurt")
        partial = self.outcome("von Behr")
        fragment = self.outcome("Behr")
        self.assertTrue(full.identity_confirmed)
        self.assertFalse(partial.identity_confirmed)
        self.assertFalse(fragment.identity_confirmed)

    def test_a_conflicting_given_name_still_does_not_match(self):
        self.assertIsNone(self.outcome("Behr, Anna"))
        self.assertIsNone(self.outcome("von Behr, Anna"))

    def test_an_entry_naming_a_person_can_never_yield_an_organisation_match(self):
        # The fix is the default, not the equality test. Any token that
        # reaches an entry with person_forms is a surname question.
        from src.name_matching import MATCH_ORGANISATION_NAME, _single_token_outcome

        person = name_matching.PersonName("von behr", ("kurt",))
        self.assertEqual(
            _single_token_outcome("anything", (person,)).basis, MATCH_SURNAME_ONLY
        )
        self.assertEqual(
            _single_token_outcome("dorotheum", ()).basis, MATCH_ORGANISATION_NAME
        )

    def test_the_live_list_s_compound_entry_behaves_the_same(self):
        wolff = next(
            e for e in load_aliu_list(REAL_ALIU).entries if "Metternich" in e.name
        )
        for supplied in ("Wolff", "Metternich", "Wolff Metternich"):
            with self.subTest(supplied=supplied):
                outcome = match_entry((supplied,), wolff.terms, wolff.person_forms)
                self.assertFalse(outcome.identity_confirmed)


class StatementCarriesTheCaveatTests(unittest.TestCase):
    """The headline sentence is what a reader reads and what the LLM restates."""

    def _screen(self, owner_name: str, date_from: str = "1939"):
        from src.csv_adapter import load_chains
        from src.heuristics import build_config, screen_object
        import pathlib
        import tempfile

        header = (
            "object_id,object_title,object_class,owner_name,owner_name_variants,"
            "date_from,date_to,date_precision,transaction_state,location,"
            "source_citation,export_licence_present,is_institution_acquisition,"
            "catalogue_reference,owner_stated_in_catalogue,"
            "restitution_recipient_type,notes"
        )
        row = (
            f'S-1,Work,painting,"{owner_name}",,{date_from},,year,purchase,'
            f'"Berlin, Germany",Invoice,,,,,,'
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(header + "\n" + row + "\n")
            path = pathlib.Path(handle.name)
        chain = load_chains(path)[0]
        return screen_object(chain, build_config())

    def test_an_unconfirmed_identity_says_so_in_the_statement(self):
        flags = self._screen("Lange")["persecution_context_flags"]
        nm = [f for f in flags if f["rule_id"] == "NM-001"]
        self.assertTrue(nm, "a bare surname should still reach the queue")
        self.assertIn("IDENTITY NOT ESTABLISHED", nm[0]["rule_statement"])

    def test_a_confirmed_identity_does_not_carry_the_qualifier(self):
        flags = self._screen("Lange, Hans W.")["persecution_context_flags"]
        nm = [f for f in flags if f["rule_id"] == "NM-001"]
        self.assertTrue(nm)
        self.assertNotIn("IDENTITY NOT ESTABLISHED", nm[0]["rule_statement"])

    def test_the_two_statements_actually_differ(self):
        # They were byte-identical before this change.
        bare = self._screen("Lange")["persecution_context_flags"]
        full = self._screen("Lange, Hans W.")["persecution_context_flags"]
        self.assertNotEqual(
            next(f for f in bare if f["rule_id"] == "NM-001")["rule_statement"],
            next(f for f in full if f["rule_id"] == "NM-001")["rule_statement"],
        )


class EntryKindTests(unittest.TestCase):
    """Person-or-organisation is declared, never guessed from the name's shape.

    Both earlier defects were that guess going wrong in different directions:
    a compound surname read as Given+Surname, and an auction house rendered
    with "the list entry names an individual" — an untrue sentence in the one
    rendering path whose whole justification is legal accuracy. Nine entries
    to annotate now; two thousand later, after the guess has been wrong in
    public.
    """

    def _payload(self, **overrides):
        entry = {
            "entry_id": "K1",
            "name": "Testperson, Amalia",
            "entry_type": "documented_concern",
            "entry_kind": "person",
            "annotation": "Fixture.",
            "source_url": "https://example.invalid/fixture",
        }
        entry.update(overrides)
        return {"_meta": {"status": "test fixture"}, "entries": [entry]}

    def _load(self, payload):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle)
            path = handle.name
        return name_matching.load_aliu_list(path)

    def test_the_loader_refuses_to_default_the_kind(self):
        payload = self._payload()
        del payload["entries"][0]["entry_kind"]
        with self.assertRaises(name_matching.ReferenceListError) as caught:
            self._load(payload)
        self.assertIn("entry_kind", str(caught.exception))

    def test_the_loader_rejects_an_unknown_kind(self):
        with self.assertRaises(name_matching.ReferenceListError):
            self._load(self._payload(entry_kind="institution"))

    def test_every_bundled_entry_declares_its_kind(self):
        for entry in load_aliu_list(REAL_ALIU).entries:
            self.assertIn(entry.entry_kind, name_matching.ENTRY_KINDS, entry.name)
        for actor in load_actor_list(REAL_ACTORS).actors:
            self.assertIn(actor.entry_kind, name_matching.ENTRY_KINDS, actor.name)

    def test_an_organisation_is_never_described_as_naming_an_individual(self):
        # The untrue sentence. "Galerie Fischer (Lucerne)" is an auction house.
        fischer = next(
            a for a in load_actor_list(REAL_ACTORS).actors if "Fischer" in a.name
        )
        outcome = match_entry(
            ("Fischer",), fischer.terms, fischer.person_forms, fischer.entry_kind
        )
        self.assertFalse(outcome.identity_confirmed)
        self.assertNotIn("the list entry names an individual", outcome.note)
        self.assertIn("organisation", outcome.note)

    def test_a_person_entry_keeps_the_person_wording(self):
        lange = next(e for e in load_aliu_list(REAL_ALIU).entries if "Lange" in e.name)
        outcome = match_entry(("Lange",), lange.terms, lange.person_forms, lange.entry_kind)
        self.assertIn("names an individual", outcome.note)

    def test_the_kind_does_not_change_whether_something_matches(self):
        # It governs wording only. Confidence is still decided by whether the
        # entry knows any individual by that name.
        fischer = next(
            a for a in load_actor_list(REAL_ACTORS).actors if "Fischer" in a.name
        )
        self.assertIsNone(
            match_entry(
                ("Fischer, Ernst",), fischer.terms, fischer.person_forms,
                fischer.entry_kind,
            )
        )


class SourceDisciplineTests(unittest.TestCase):
    def test_matching_follows_the_data_file_not_the_code(self):
        # Behavioural rather than a grep: a name invented here matches only
        # because it is in the list handed to the loader, and identity rules
        # apply to it exactly as to a bundled entry.
        import json
        import pathlib
        import tempfile

        payload = {
            "_meta": {"status": "test fixture"},
            "entries": [
                {
                    "entry_id": "T1",
                    "name": "Testperson, Amalia",
                    "name_variants": ["Amalia Testperson"],
                    "entry_type": "documented_concern",
                    "entry_kind": "person",
                    "annotation": "Fixture entry.",
                    "source_url": "https://example.invalid/fixture",
                }
            ],
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle)
            path = pathlib.Path(handle.name)

        listing = name_matching.load_aliu_list(path)
        entry = listing.entries[0]
        self.assertIsNotNone(
            match_entry(("Testperson, Amalia",), entry.terms, entry.person_forms)
        )
        self.assertIsNone(
            match_entry(("Testperson, Bruno",), entry.terms, entry.person_forms),
            "the identity rule must apply to any entry, not just bundled ones",
        )


if __name__ == "__main__":
    unittest.main()
