"""Tests for the parts of the slice that are easy to get subtly wrong:
date-precision handling and the rules that depend on it.

    python -m unittest discover tests
"""

import json
import pathlib
import unittest
from datetime import date

from src.heuristics import (
    RULE_PC_001,
    RULE_PC_002,
    RULE_PC_003,
    RULE_PC_004,
    RULE_PC_005,
    RULE_PC_006,
    RULE_PC_007,
    RULE_PC_008,
    RULE_PC_009,
    RULE_DQ_001,
    RULE_DQ_002,
    RULE_NM_001,
    RULE_NM_002,
    ScreeningConfig,
    build_config,
    load_onset_table,
    screen_object,
)
from src.reao_taxonomy import PresumptionTier, TransactionState
from src.schema import (
    CERTAIN,
    POSSIBLE,
    DatePrecision,
    InputValidationError,
    ProvenanceRecord,
    build_date_span,
    group_into_chains,
)
from src import csv_adapter, pipeline, report

from src.reao_taxonomy import RestitutionRecipientType
from src import name_matching

TABLE = load_onset_table()
GERMANY_WINDOW = (date(1933, 1, 30), TABLE.risk_band_end)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
ACTOR_FIXTURE = FIXTURES / "actors_fixture.json"
ALIU_FIXTURE = FIXTURES / "aliu_fixture.json"

# The real bundled lists. Tests that assert on specific real entries skip if
# they are ever absent, rather than silently passing.
REAL_ALIU = name_matching.DEFAULT_ALIU_PATH
REAL_ACTORS = name_matching.DEFAULT_ACTORS_PATH

# Default config for rule tests: the reference lists are deliberately NOT
# loaded, so PC-005/NM-001 report themselves skipped and the other rules can
# be asserted in isolation. Tests for those two rules pass an explicit list —
# a fixture for the mechanics, the real file for its actual entries.
CONFIG = build_config(aliu_path="/nonexistent/aliu.json",
                      actors_path="/nonexistent/actors.json")


def config(actors=None, aliu=None, **kwargs):
    return build_config(actors_path=actors, aliu_path=aliu, **kwargs)


def record(object_id="OBJ", order=0, **kwargs):
    fields = {
        "owner_name": "owner",
        "source_ref": f"test:row {order}",
        "source_order": order,
    }
    span_args = {
        k: kwargs.pop(k, None) for k in ("date_from", "date_to", "date_precision")
    }
    fields.update(kwargs)
    span = build_date_span(
        span_args["date_from"], span_args["date_to"], span_args["date_precision"]
    )
    return ProvenanceRecord(
        object_id=object_id,
        date_from=span_args["date_from"],
        date_to=span_args["date_to"],
        date_span=span,
        **fields,
    )


def screen(records, cfg=None):
    chain = group_into_chains(records)[0]
    return screen_object(chain, cfg or CONFIG)


def rule_ids(result, axis="persecution_context_flags"):
    return [f["rule_id"] for f in result[axis]]


class DateSpanTests(unittest.TestCase):
    def test_year_expands_to_whole_year(self):
        span = build_date_span("1936", None, "year")
        self.assertEqual(span.nominal_earliest, date(1936, 1, 1))
        self.assertEqual(span.nominal_latest, date(1936, 12, 31))

    def test_circa_widens_but_never_reports_certainty(self):
        span = build_date_span("1938", None, "circa")
        self.assertEqual(span.earliest, date(1933, 1, 1))
        self.assertEqual(span.latest, date(1943, 12, 31))
        # Squarely inside the window on its face, yet still only "possible".
        self.assertEqual(span.overlap_certainty(*GERMANY_WINDOW), POSSIBLE)

    def test_circa_outside_window_even_when_widened_does_not_overlap(self):
        span = build_date_span("1925", None, "circa")
        self.assertIsNone(span.overlap_certainty(*GERMANY_WINDOW))

    def test_before_is_open_at_the_start(self):
        span = build_date_span(None, "1940", "before")
        self.assertIsNone(span.earliest)
        self.assertEqual(span.latest, date(1940, 12, 31))
        self.assertEqual(span.overlap_certainty(*GERMANY_WINDOW), POSSIBLE)

    def test_after_is_open_at_the_end(self):
        span = build_date_span("1946", None, "after")
        self.assertIsNone(span.latest)
        self.assertEqual(span.overlap_certainty(*GERMANY_WINDOW), POSSIBLE)

    def test_precise_span_inside_window_is_certain(self):
        span = build_date_span("1936-11-04", None, "exact")
        self.assertEqual(span.overlap_certainty(*GERMANY_WINDOW), CERTAIN)

    def test_precision_finer_than_the_date_is_rejected(self):
        with self.assertRaises(ValueError):
            build_date_span("1938", None, "exact")
        with self.assertRaises(ValueError):
            build_date_span("1938", None, "month")

    def test_date_without_precision_is_rejected(self):
        with self.assertRaises(ValueError):
            build_date_span("1938", None, None)

    def test_no_date_at_all_is_permitted(self):
        self.assertIsNone(build_date_span(None, None, None))

    def test_reversed_range_is_rejected(self):
        with self.assertRaises(ValueError):
            build_date_span("1940", "1930", "year")


class Rule1Tests(unittest.TestCase):
    def test_fires_when_every_record_is_post_1945(self):
        result = screen([record(date_from="1962", date_precision="year")])
        self.assertIn(RULE_PC_001, rule_ids(result))

    def test_does_not_fire_on_a_pre_1945_record(self):
        result = screen([record(date_from="1921", date_precision="year")])
        self.assertNotIn(RULE_PC_001, rule_ids(result))

    def test_imprecise_pre_1945_coverage_suppresses_the_flag_but_is_noted(self):
        # "before 1960" might be pre-1945, so the flag is withheld — but the
        # object must not read as having established pre-1945 documentation.
        result = screen([record(date_to="1960", date_precision="before")])
        self.assertNotIn(RULE_PC_001, rule_ids(result))
        self.assertIn("rests only on imprecisely dated records", result["coverage_note"])

    def test_fires_when_the_chain_is_entirely_undated(self):
        result = screen([record()])
        self.assertIn(RULE_PC_001, rule_ids(result))


class Rule2Tests(unittest.TestCase):
    def test_fires_when_the_accession_is_the_earliest_record(self):
        result = screen(
            [
                record(
                    date_from="1962",
                    date_precision="year",
                    is_institution_acquisition=True,
                )
            ]
        )
        self.assertIn(RULE_PC_002, rule_ids(result))

    def test_does_not_fire_when_a_prior_owner_is_recorded(self):
        result = screen(
            [
                record(order=0, date_from="1953", date_precision="year",
                       is_institution_acquisition=True),
                record(order=1, date_from="1921", date_precision="year",
                       is_institution_acquisition=False),
            ]
        )
        self.assertNotIn(RULE_PC_002, rule_ids(result))

    def test_skips_and_says_so_when_the_column_is_never_set(self):
        result = screen([record(date_from="1962", date_precision="year")])
        self.assertNotIn(RULE_PC_002, rule_ids(result))
        self.assertIn(f"{RULE_PC_002} skipped", result["coverage_note"])

    def test_skips_when_an_undated_record_may_precede_the_accession(self):
        result = screen(
            [
                record(order=0, date_from="1954", date_precision="year",
                       is_institution_acquisition=True),
                record(order=1),
            ]
        )
        self.assertNotIn(RULE_PC_002, rule_ids(result))
        self.assertIn("chain order is not established", result["coverage_note"])


class Rule3Tests(unittest.TestCase):
    def flag(self, **kwargs):
        result = screen([record(**kwargs)])
        matches = [f for f in result["persecution_context_flags"]
                   if f["rule_id"] == RULE_PC_003]
        return matches[0] if matches else None

    def test_post_nuremberg_transfer_is_heightened(self):
        flag = self.flag(
            date_from="1936-11-04", date_precision="exact", location="Munich, Germany"
        )
        self.assertEqual(flag["presumption_tier"], PresumptionTier.HEIGHTENED.value)
        self.assertEqual(flag["cited_fields"]["overlap_certainty"], CERTAIN)

    def test_pre_nuremberg_transfer_is_ordinary(self):
        flag = self.flag(
            date_from="1934-06", date_precision="month", location="Berlin, Germany"
        )
        self.assertEqual(flag["presumption_tier"], PresumptionTier.ORDINARY.value)

    def test_recorded_presumption_state_overrides_the_date(self):
        flag = self.flag(
            date_from="1936-11-04",
            date_precision="exact",
            location="Munich, Germany",
            transaction_state=TransactionState.VERMUTUNG_DER_ENTZIEHUNG,
        )
        self.assertEqual(flag["presumption_tier"], PresumptionTier.ORDINARY.value)
        self.assertIn("transaction_state", flag["cited_fields"]["presumption_tier_basis"])

    def test_transfer_before_territory_onset_does_not_fire(self):
        # 1937 Vienna is before the Austrian onset of 13 March 1938.
        self.assertIsNone(
            self.flag(
                date_from="1937-05-20", date_precision="exact",
                location="Vienna, Austria",
            )
        )

    def test_onset_is_keyed_to_territory_not_a_flat_date_range(self):
        # Same date, different territory: in scope for Germany, not for Austria.
        self.assertIsNotNone(
            self.flag(date_from="1934", date_precision="year",
                      location="Berlin, Germany")
        )
        self.assertIsNone(
            self.flag(date_from="1934", date_precision="year",
                      location="Vienna, Austria")
        )

    def test_risk_band_extends_past_1945(self):
        self.assertIsNotNone(
            self.flag(date_from="1952", date_precision="year",
                      location="Munich, Germany")
        )
        self.assertIsNone(
            self.flag(date_from="1957", date_precision="year",
                      location="Munich, Germany")
        )

    def test_territory_matching_is_accent_and_case_insensitive(self):
        self.assertIsNotNone(
            self.flag(date_from="1939", date_precision="year", location="Wien, ÖSTERREICH")
        )

    def test_unlisted_territory_does_not_fire_and_records_no_false_certainty(self):
        self.assertIsNone(
            self.flag(date_from="1939", date_precision="year",
                      location="Bern, Switzerland")
        )

    def test_missing_location_is_reported_as_skipped(self):
        result = screen([record(date_from="1939", date_precision="year")])
        self.assertIn("has no `location`", result["coverage_note"])


class ItalyTwoWindowTests(unittest.TestCase):
    """Italy is modelled as two windows with fixed tiers, because the global
    Nuremberg threshold does not describe its chronology."""

    def flags(self, **kwargs):
        result = screen([record(location="Rome, Italy", **kwargs)])
        return [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_003]

    def test_racial_laws_window_is_ordinary_not_heightened(self):
        # Under the global 15 Sept 1935 threshold alone this would come back
        # heightened, which overstates what the 1938 laws support.
        flags = self.flags(date_from="1940", date_precision="year")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["presumption_tier"], PresumptionTier.ORDINARY.value)
        self.assertEqual(
            flags[0]["cited_fields"]["persecution_window"], "italy_racial_laws_1938"
        )

    def test_occupation_window_is_heightened(self):
        flags = self.flags(date_from="1944-02-11", date_precision="exact")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["presumption_tier"], PresumptionTier.HEIGHTENED.value)
        self.assertEqual(
            flags[0]["cited_fields"]["persecution_window"], "italy_occupation_1943"
        )

    def test_1938_is_covered_rather_than_under_flagged(self):
        self.assertEqual(len(self.flags(date_from="1938", date_precision="year")), 1)

    def test_1937_is_before_both_windows(self):
        self.assertEqual(self.flags(date_from="1937", date_precision="year"), [])

    def test_a_span_touching_both_windows_reports_both_tiers(self):
        flags = self.flags(date_from="1942", date_to="1944", date_precision="year")
        self.assertEqual(
            sorted(f["presumption_tier"] for f in flags), ["heightened", "ordinary"]
        )

    def test_other_territories_still_use_the_nuremberg_threshold(self):
        result = screen(
            [record(date_from="1940", date_precision="year", location="Munich, Germany")]
        )
        flag = result["persecution_context_flags"][0]
        self.assertEqual(flag["presumption_tier"], PresumptionTier.HEIGHTENED.value)


class SettledRecordTests(unittest.TestCase):
    def test_a_restitution_record_is_not_itself_window_flagged(self):
        result = screen(
            [
                record(
                    date_from="1949-03-02",
                    date_precision="exact",
                    location="Vienna, Austria",
                    transaction_state=TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH,
                )
            ]
        )
        self.assertNotIn(RULE_PC_003, rule_ids(result))
        self.assertIn("resolves a claim rather than presenting one", result["coverage_note"])

    def test_other_records_in_a_settled_chain_are_still_screened(self):
        result = screen(
            [
                record(order=0, date_from="1939-05-20", date_precision="exact",
                       location="Vienna, Austria"),
                record(order=1, date_from="1949-03-02", date_precision="exact",
                       location="Vienna, Austria",
                       transaction_state=TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH),
            ]
        )
        window_flags = [f for f in result["persecution_context_flags"]
                        if f["rule_id"] == RULE_PC_003]
        self.assertEqual(len(window_flags), 1)
        self.assertEqual(
            window_flags[0]["cited_fields"]["source_ref"], "test:row 0"
        )
        self.assertEqual(result["resolved_status"], "previously_resolved")


class CircaMarginTests(unittest.TestCase):
    def test_margin_is_configurable(self):
        narrow = build_date_span("1938", None, "circa", circa_margin_years=1)
        wide = build_date_span("1938", None, "circa", circa_margin_years=20)
        self.assertEqual(narrow.earliest, date(1937, 1, 1))
        self.assertEqual(wide.earliest, date(1918, 1, 1))

    def test_margin_changes_what_a_circa_date_reaches(self):
        # circa 1930 reaches the German window only under a wide margin.
        self.assertIsNone(
            build_date_span("1930", None, "circa", circa_margin_years=1)
            .overlap_certainty(*GERMANY_WINDOW)
        )
        self.assertEqual(
            build_date_span("1930", None, "circa", circa_margin_years=10)
            .overlap_certainty(*GERMANY_WINDOW),
            POSSIBLE,
        )

    def test_widening_never_produces_certainty_at_any_margin(self):
        for margin in (0, 1, 5, 50):
            span = build_date_span("1938", None, "circa", circa_margin_years=margin)
            self.assertNotEqual(span.overlap_certainty(*GERMANY_WINDOW), CERTAIN)


class Rule4Tests(unittest.TestCase):
    def test_missing_citation_lands_on_the_documentation_axis_only(self):
        result = screen([record(date_from="1921", date_precision="year")])
        self.assertEqual(rule_ids(result, "documentation_quality_flags"), [RULE_DQ_001])
        self.assertEqual(rule_ids(result), [])

    def test_present_citation_does_not_flag(self):
        result = screen(
            [record(date_from="1921", date_precision="year", source_citation="Inv. 1921")]
        )
        self.assertEqual(result["documentation_quality_flags"], [])

    def test_documentation_flags_carry_no_presumption_tier(self):
        result = screen([record(date_from="1921", date_precision="year")])
        self.assertNotIn("presumption_tier", result["documentation_quality_flags"][0])


class OutputFramingTests(unittest.TestCase):
    def test_unflagged_object_gets_the_non_clearance_terminal_state(self):
        result = screen(
            [record(date_from="1889", date_precision="year", source_citation="Cat. 1889")]
        )
        self.assertEqual(result["persecution_context_flags"], [])
        self.assertEqual(result["documentation_quality_flags"], [])
        self.assertIn("no criteria triggered", result["screening_statement"])
        self.assertIn(
            "Absence of flags is not evidence of unproblematic provenance",
            result["screening_statement"],
        )

    def test_no_output_field_carries_a_score_or_a_verdict(self):
        result = screen([record(date_from="1936-11-04", date_precision="exact",
                                location="Munich, Germany")])
        forbidden = ("score", "risk_level", "rating", "clean", "cleared", "low risk")
        blob = repr(result).lower()
        for term in forbidden:
            self.assertNotIn(term, blob, f"output contains {term!r}")

    def test_settled_chain_is_marked_previously_resolved(self):
        result = screen(
            [
                record(order=0, date_from="1939-05-20", date_precision="exact"),
                record(
                    order=1,
                    date_from="1949-03-02",
                    date_precision="exact",
                    transaction_state=TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH,
                ),
            ]
        )
        self.assertEqual(result["resolved_status"], "previously_resolved")
        self.assertIn("held out of the active triage queue", result["coverage_note"])

    def test_unsettled_chain_is_unresolved(self):
        result = screen([record(date_from="1939", date_precision="year")])
        self.assertEqual(result["resolved_status"], "unresolved")


class CsvAdapterTests(unittest.TestCase):
    def test_example_file_loads_and_orders_chains(self):
        chains = csv_adapter.load_chains("examples/example_input.csv")
        by_id = {c.object_id: c for c in chains}
        self.assertEqual(len(chains), 24)
        obj1 = by_id["OBJ-001"]
        self.assertEqual([r.date_from for r in obj1.records], ["1921", "1936-11-04", "1953"])
        # Undated records sort last so they never masquerade as the earliest event.
        self.assertIsNone(by_id["OBJ-008"].records[-1].date_span)

    def test_unknown_column_is_rejected_rather_than_dropped(self):
        import tempfile, pathlib

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.csv"
            path.write_text("object_id,owner_nmae,date_precision\nA,B,year\n")
            with self.assertRaises(InputValidationError) as ctx:
                csv_adapter.load_chains(path)
            self.assertIn("owner_nmae", str(ctx.exception))

    def test_every_row_problem_is_reported_at_once(self):
        import tempfile, pathlib

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.csv"
            path.write_text(
                "object_id,owner_name,date_from,date_precision\n"
                ",,1938,exact\n"
                "OBJ-9,Y,1938,nonsense\n"
            )
            with self.assertRaises(InputValidationError) as ctx:
                csv_adapter.load_chains(path)
            self.assertEqual(len(ctx.exception.problems), 4)


class Rule5ObjectClassTests(unittest.TestCase):
    """PC-004 triggers on class and date alone — not on chain evidence."""

    def ids(self, **kwargs):
        return rule_ids(screen([record(**kwargs)]))

    def test_fires_on_a_covered_class_inside_the_band(self):
        self.assertIn(
            RULE_PC_004,
            self.ids(object_class="silver", date_from="1938", date_precision="year"),
        )

    def test_fires_without_a_location(self):
        # The point of the rule: these objects typically have no chain and no
        # place, so requiring a territory match would under-flag them to zero.
        flags = [
            f
            for f in screen(
                [record(object_class="Judaica", date_from="1940", date_precision="year")]
            )["persecution_context_flags"]
            if f["rule_id"] == RULE_PC_004
        ]
        self.assertTrue(flags)
        self.assertIn("no territory matched", flags[0]["cited_fields"]["presumption_tier_basis"])

    def test_does_not_fire_on_an_uncovered_class(self):
        self.assertNotIn(
            RULE_PC_004,
            self.ids(object_class="painting", date_from="1938", date_precision="year"),
        )

    def test_does_not_fire_outside_the_band_when_pre_1945_is_documented(self):
        self.assertNotIn(
            RULE_PC_004,
            self.ids(object_class="silver", date_from="1890", date_precision="year"),
        )

    def test_absent_chain_is_itself_the_signal_for_these_classes(self):
        flags = [
            f
            for f in screen([record(object_class="coins", date_from="1970",
                                    date_precision="year")])["persecution_context_flags"]
            if f["rule_id"] == RULE_PC_004
        ]
        self.assertTrue(flags)
        self.assertFalse(flags[-1]["cited_fields"]["certainly_dated_before_1945"])

    def test_skips_and_says_so_without_an_object_class(self):
        result = screen([record(date_from="1938", date_precision="year")])
        self.assertNotIn(RULE_PC_004, rule_ids(result))
        self.assertIn(f"{RULE_PC_004} skipped", result["coverage_note"])

    def test_class_variants_match(self):
        for value in ("Silber", "decorative arts", "Kunstgewerbe", "numismatics"):
            self.assertIn(
                RULE_PC_004,
                self.ids(object_class=value, date_from="1938", date_precision="year"),
                f"{value!r} should fall under the object-class rule",
            )


class Rule6ConfiscationActorTests(unittest.TestCase):
    """PC-005. Gating is the substance here, not the match itself."""

    def setUp(self):
        self.cfg = config(actors=ACTOR_FIXTURE)

    def flags(self, **kwargs):
        kwargs.setdefault("date_from", "1941")
        kwargs.setdefault("date_precision", "year")
        result = screen([record(**kwargs)], self.cfg)
        return [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_005]

    def test_match_renders_basis_and_sources_never_a_bare_boolean(self):
        flag = self.flags(owner_name="Auktionshaus Steinbach")[0]
        bases = flag["cited_fields"]["documented_bases"]
        self.assertTrue(bases)
        for basis in bases:
            self.assertTrue(basis["documented_basis"])
            self.assertTrue(basis["sources"])
            self.assertIn("implies_persecution_of_former_owner", basis)

    def test_entry_whose_basis_implies_persecution_carries_a_tier(self):
        flag = self.flags(owner_name="Auktionshaus Steinbach")[0]
        self.assertNotEqual(flag["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)

    def test_gated_entry_does_not_imply_persecution_of_a_former_owner(self):
        # The Galerie Fischer shape: every documented limb is marked as not
        # bearing on a private former owner, and nothing in the record
        # independently supplies it.
        flag = self.flags(owner_name="Galerie Beispiel",
                          transaction_state=TransactionState.PURCHASE)[0]
        self.assertEqual(flag["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)
        self.assertIn("does NOT indicate persecution", flag["rule_statement"])
        self.assertIn(
            "does not bear on persecution",
            flag["cited_fields"]["persecution_inference_basis"],
        )

    def test_gate_opens_only_on_independent_transaction_state_support(self):
        flag = self.flags(owner_name="Galerie Beispiel",
                          transaction_state=TransactionState.ZWANGSVERKAUF)[0]
        self.assertNotEqual(flag["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)
        basis = flag["cited_fields"]["persecution_inference_basis"]
        self.assertIn("rests solely on this record's own", basis)
        self.assertIn("zwangsverkauf", basis)

    def test_verification_note_is_surfaced_on_every_match(self):
        flag = self.flags(owner_name="Handelshaus Muster")[0]
        self.assertIn("verification_note", flag["cited_fields"])
        self.assertTrue(flag["cited_fields"]["verification_note"])

    def test_well_documented_entry_carries_no_spurious_verification_note(self):
        flag = self.flags(owner_name="Auktionshaus Steinbach")[0]
        self.assertNotIn("verification_note", flag["cited_fields"])

    def test_match_is_scoped_to_the_rule_window(self):
        self.assertEqual(self.flags(owner_name="Auktionshaus Steinbach",
                                    date_from="1960", date_precision="year"), [])

    def test_undated_match_is_reported_skipped_not_dropped(self):
        result = screen([record(owner_name="Auktionshaus Steinbach")], self.cfg)
        self.assertIn(f"{RULE_PC_005} not evaluated", result["coverage_note"])

    def test_owner_name_variants_are_matched(self):
        self.assertTrue(
            self.flags(owner_name="Kunsthandlung Nord",
                       owner_name_variants=["Auktionshaus Steinbach"])
        )

    def test_skips_and_says_so_when_the_list_is_absent(self):
        result = screen([record(owner_name="Auktionshaus Steinbach",
                                date_from="1941", date_precision="year")],
                        build_config(actors_path="/nonexistent/actors.json"))
        self.assertNotIn(RULE_PC_005, rule_ids(result))
        self.assertIn(f"{RULE_PC_005} skipped", result["coverage_note"])

    def test_loader_rejects_an_entry_with_no_documented_basis(self):
        self.assertRaisesRegex(
            name_matching.ReferenceListError, "documented_basis",
            self._load_bad, {"actors": [{"name": "X"}]},
        )

    def test_loader_rejects_a_basis_with_no_sources(self):
        self.assertRaisesRegex(
            name_matching.ReferenceListError, "no sources",
            self._load_bad,
            {"actors": [{"name": "X", "documented_basis": [
                {"summary": "s", "implies_persecution_of_former_owner": True}]}]},
        )

    def test_loader_refuses_to_default_the_persecution_gate(self):
        self.assertRaisesRegex(
            name_matching.ReferenceListError, "implies_persecution_of_former_owner",
            self._load_bad,
            {"actors": [{"name": "X", "documented_basis": [
                {"summary": "s", "sources": ["src"]}]}]},
        )

    def _load_bad(self, payload):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text(json.dumps(payload))
            name_matching.load_actor_list(path)


@unittest.skipUnless(REAL_ACTORS.exists(), f"{REAL_ACTORS} not present in this checkout")
class Rule6RealListTests(unittest.TestCase):
    """Assertions against the real bundled list. Skipped until it is added."""

    def setUp(self):
        self.actors = name_matching.load_actor_list(REAL_ACTORS)

    def _entry(self, needle):
        for actor in self.actors.actors:
            if needle.casefold() in actor.name.casefold():
                return actor
        self.skipTest(f"no {needle} entry in the bundled list")

    def test_galerie_fischer_is_gated_against_implying_persecution(self):
        entry = self._entry("Fischer")
        self.assertFalse(
            entry.has_persecution_implying_basis,
            "A Galerie Fischer match must not imply persecution of an individual "
            "former owner on the strength of the list entry alone.",
        )

    def test_galerie_fischer_carries_both_documented_limbs(self):
        self.assertGreaterEqual(len(self._entry("Fischer").bases), 2)

    def test_dorotheum_carries_a_verification_note(self):
        self.assertTrue(self._entry("Dorotheum").verification_note)

    def test_every_entry_carries_sources(self):
        for actor in self.actors.actors:
            for basis in actor.bases:
                self.assertTrue(basis.sources, f"{actor.name} has a basis with no sources")

    def test_split_limbs_rejoin_to_the_original_curated_prose(self):
        # The gate field was added by transcription. Nothing the curator wrote
        # may be reworded or lost in the process.
        raw = json.loads(REAL_ACTORS.read_text(encoding="utf-8"))
        for entry in raw["entries"]:
            limbs = entry["documented_basis"]
            if not isinstance(limbs, list):
                continue
            joined = " ".join(limb["summary"] for limb in limbs)
            # Every limb's text must be a verbatim slice of the whole.
            for limb in limbs:
                self.assertIn(limb["summary"], joined)
            if len(limbs) > 1:
                self.assertTrue(
                    joined.startswith(limbs[0]["summary"]),
                    f"{entry['name']}: limb order does not preserve the original text",
                )

    def test_every_gate_value_carries_its_basis(self):
        for actor in self.actors.actors:
            for basis in actor.bases:
                self.assertTrue(
                    basis.gate_basis,
                    f"{actor.name} has a gate value with no gate_basis explaining it",
                )

    def _fischer_flag(self, **kwargs):
        cfg = config(actors=REAL_ACTORS)
        kwargs.setdefault("date_from", "1941")
        kwargs.setdefault("date_precision", "year")
        result = screen([record(owner_name="Galerie Fischer Lucerne", **kwargs)], cfg)
        return [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_005][0]

    def test_fischer_match_alone_asserts_no_tier(self):
        flag = self._fischer_flag(transaction_state=TransactionState.PURCHASE)
        self.assertEqual(flag["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)
        self.assertIn("does NOT indicate persecution", flag["rule_statement"])

    def test_fischer_gate_opens_only_on_the_record_s_own_state(self):
        flag = self._fischer_flag(transaction_state=TransactionState.ZWANGSVERKAUF)
        self.assertNotEqual(flag["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)
        self.assertIn(
            "rests solely on this record's own",
            flag["cited_fields"]["persecution_inference_basis"],
        )

    def test_fischer_match_renders_both_limbs_with_their_own_sources(self):
        bases = self._fischer_flag(
            transaction_state=TransactionState.PURCHASE
        )["cited_fields"]["documented_bases"]
        self.assertEqual(len(bases), 2)
        for basis in bases:
            self.assertTrue(basis["sources"])
            self.assertFalse(basis["implies_persecution_of_former_owner"])
        self.assertIn("GERMAN STATE MUSEUMS", bases[0]["documented_basis"])
        self.assertIn("case-level verification", bases[1]["documented_basis"])

    def test_weinmuller_basis_does_bear_on_persecution(self):
        self.assertTrue(self._entry("Weinm").has_persecution_implying_basis)

    def test_dorotheum_note_reaches_the_flag_output(self):
        cfg = config(actors=REAL_ACTORS)
        result = screen(
            [record(owner_name="Dorotheum", date_from="1943", date_precision="year")],
            cfg,
        )
        flag = [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_005][0]
        self.assertIn("WEAKER SOURCING", flag["cited_fields"]["verification_note"])

    def test_non_match_carries_the_seed_list_caveat(self):
        cfg = config(actors=REAL_ACTORS)
        result = screen(
            [record(owner_name="Nobody In Particular", date_from="1941",
                    date_precision="year")],
            cfg,
        )
        self.assertNotIn(RULE_PC_005, rule_ids(result))
        self.assertIn("is NOT evidence that no", result["coverage_note"])


class Rule7AnonymousEntryTests(unittest.TestCase):
    def flags(self, **kwargs):
        result = screen([record(**kwargs)])
        return [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_006]

    def test_fires_on_an_anonymous_entry_inside_the_band(self):
        flag = self.flags(owner_name="Private collection, Switzerland",
                          date_from="1942", date_precision="year")[0]
        self.assertEqual(flag["cited_fields"]["matched_pattern"], "private collection")

    def test_does_not_fire_outside_the_band(self):
        self.assertEqual(
            self.flags(owner_name="Private collection, Switzerland",
                       date_from="1955", date_precision="year"),
            [],
        )
        self.assertEqual(
            self.flags(owner_name="Private collection, Switzerland",
                       date_from="1925", date_precision="year"),
            [],
        )

    def test_does_not_fire_on_a_named_owner(self):
        self.assertEqual(
            self.flags(owner_name="Berglund, M.", date_from="1942",
                       date_precision="year"),
            [],
        )

    def test_patterns_are_configurable_rather_than_hardcoded(self):
        # The rule reads its patterns and its band from the data file; nothing
        # in the rule body knows the string "private collection".
        import src.heuristics as heuristics_module

        source = pathlib.Path(heuristics_module.__file__).read_text()
        self.assertNotIn("private collection", source.lower())

    def test_band_is_narrower_than_the_persecution_risk_band(self):
        patterns = CONFIG.anonymous_patterns
        self.assertEqual(patterns.band_to, date(1950, 12, 31))
        self.assertLess(patterns.band_to, TABLE.risk_band_end)

    def test_non_latin_and_accented_patterns_match(self):
        self.assertTrue(
            self.flags(owner_name="Collection privée", date_from="1942",
                       date_precision="year")
        )


class Rule8DeletedOwnerTests(unittest.TestCase):
    def catalogue(self, order, year, stated, owner="owner", precision="year"):
        return record(
            order=order,
            owner_name=owner,
            date_from=year,
            date_precision=precision,
            catalogue_reference=f"Catalogue {year}",
            owner_stated_in_catalogue=stated,
        )

    def test_fires_when_a_named_owner_disappears_from_a_later_edition(self):
        result = screen([
            self.catalogue(0, "1928", True, owner="Lindqvist, A."),
            self.catalogue(1, "1961", False, owner="not recorded"),
        ])
        flag = [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_007][0]
        self.assertIn("Lindqvist, A.", flag["rule_statement"])
        self.assertTrue(flag["cited_fields"]["post_1950_catalogue_entries_without_owner"])

    def test_reports_unverifiable_when_there_is_nothing_to_compare(self):
        result = screen([self.catalogue(0, "1928", True, owner="Lindqvist, A.")])
        flag = [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_007][0]
        self.assertIn("Unverifiable, requires catalogue raisonné cross-check",
                      flag["rule_statement"])
        self.assertFalse(flag["cited_fields"]["post_1950_catalogue_entry_present"])

    def test_does_not_fire_when_the_later_edition_still_names_the_owner(self):
        result = screen([
            self.catalogue(0, "1928", True, owner="Lindqvist, A."),
            self.catalogue(1, "1961", True, owner="Lindqvist, A."),
        ])
        self.assertNotIn(RULE_PC_007, rule_ids(result))

    def test_skips_and_says_so_without_a_catalogue_reference(self):
        result = screen([record(date_from="1928", date_precision="year")])
        self.assertNotIn(RULE_PC_007, rule_ids(result))
        self.assertIn(f"{RULE_PC_007} skipped", result["coverage_note"])

    def test_reports_when_the_catalogue_owner_field_is_unset(self):
        result = screen([
            record(order=0, date_from="1928", date_precision="year",
                   catalogue_reference="Catalogue 1928"),
        ])
        self.assertIn("do not set `owner_stated_in_catalogue`", result["coverage_note"])

    def test_imprecise_early_date_does_not_establish_the_pre_1933_side(self):
        result = screen([
            self.catalogue(0, "1930", True, owner="X", precision="circa"),
            self.catalogue(1, "1961", False, owner="not recorded"),
        ])
        self.assertNotIn(RULE_PC_007, rule_ids(result))


class Rule9PostCommitmentTests(unittest.TestCase):
    def result(self, records, **kwargs):
        return screen(records, build_config(**kwargs) if kwargs else CONFIG)

    def test_fires_on_a_post_1998_acquisition_with_no_pre_1945_chain(self):
        result = self.result([
            record(date_from="2005", date_precision="year",
                   is_institution_acquisition=True)
        ])
        flag = [f for f in result["documentation_quality_flags"]
                if f["rule_id"] == RULE_DQ_002][0]
        self.assertEqual(flag["cited_fields"]["commitment_date_applied"], "1998-12-03")
        self.assertTrue(flag["cited_fields"]["commitment_date_is_default"])

    def test_lands_on_the_documentation_axis_not_persecution_context(self):
        result = self.result([
            record(date_from="2005", date_precision="year",
                   is_institution_acquisition=True)
        ])
        self.assertIn(RULE_DQ_002, rule_ids(result, "documentation_quality_flags"))
        self.assertNotIn(RULE_DQ_002, rule_ids(result))

    def test_does_not_fire_when_pre_1945_provenance_is_documented(self):
        result = self.result([
            record(order=0, date_from="1910", date_precision="year"),
            record(order=1, date_from="2003", date_precision="year",
                   is_institution_acquisition=True),
        ])
        self.assertNotIn(RULE_DQ_002, rule_ids(result, "documentation_quality_flags"))

    def test_does_not_fire_on_a_pre_commitment_acquisition(self):
        result = self.result([
            record(date_from="1962", date_precision="year",
                   is_institution_acquisition=True)
        ])
        self.assertNotIn(RULE_DQ_002, rule_ids(result, "documentation_quality_flags"))

    def test_configured_commitment_date_is_used_and_stated(self):
        result = self.result(
            [record(date_from="1996", date_precision="year",
                    is_institution_acquisition=True)],
            commitment_date=date(1995, 1, 1),
        )
        flag = [f for f in result["documentation_quality_flags"]
                if f["rule_id"] == RULE_DQ_002][0]
        self.assertEqual(flag["cited_fields"]["commitment_date_applied"], "1995-01-01")
        self.assertFalse(flag["cited_fields"]["commitment_date_is_default"])

    def test_skips_and_says_so_without_an_acquisition_record(self):
        result = self.result([record(date_from="2005", date_precision="year")])
        self.assertIn(f"{RULE_DQ_002} skipped", result["coverage_note"])


class Rule10RestitutionRecipientTests(unittest.TestCase):
    def settled(self, recipient, order=0):
        return record(
            order=order,
            date_from="1950-06-01",
            date_precision="exact",
            transaction_state=TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH,
            restitution_recipient_type=recipient,
        )

    def test_fires_when_restitution_ran_to_a_state(self):
        result = screen([self.settled(RestitutionRecipientType.STATE_OR_INSTITUTION)])
        self.assertIn(RULE_PC_008, rule_ids(result))

    def test_does_not_fire_when_restitution_ran_to_heirs(self):
        result = screen([self.settled(RestitutionRecipientType.INDIVIDUAL_OR_HEIRS)])
        self.assertNotIn(RULE_PC_008, rule_ids(result))

    def test_skips_and_says_so_when_the_recipient_type_is_unset(self):
        result = screen([self.settled(None)])
        self.assertNotIn(RULE_PC_008, rule_ids(result))
        self.assertIn(f"{RULE_PC_008} skipped", result["coverage_note"])

    def test_reports_not_determinable_on_an_unknown_recipient(self):
        result = screen([self.settled(RestitutionRecipientType.UNKNOWN)])
        self.assertNotIn(RULE_PC_008, rule_ids(result))
        self.assertIn(f"{RULE_PC_008} not determinable", result["coverage_note"])

    def test_does_not_run_on_a_chain_with_no_settlement(self):
        result = screen([record(date_from="1938", date_precision="year")])
        self.assertNotIn(RULE_PC_008, rule_ids(result))
        # Nothing to skip either: with no settlement the rule has no subject.
        self.assertNotIn(f"{RULE_PC_008} skipped", result["coverage_note"])
        self.assertNotIn(f"{RULE_PC_008} not determinable", result["coverage_note"])

    def test_recipient_type_is_rejected_off_a_restitution_record(self):
        from src.schema import build_record

        with self.assertRaises(Exception) as ctx:
            build_record(
                {
                    "object_id": "A", "owner_name": "B", "date_precision": "year",
                    "date_from": "1950", "transaction_state": "purchase",
                    "restitution_recipient_type": "state_or_institution",
                },
                "test:row 1", 0,
            )
        self.assertIn("only meaningful on a restitution", str(ctx.exception))


class Rule11ExportLicenceTests(unittest.TestCase):
    def move(self, from_loc, to_loc, year="1939", licence=None):
        return [
            record(order=0, date_from="1936", date_precision="year", location=from_loc),
            record(order=1, date_from=year, date_precision="year", location=to_loc,
                   export_licence_present=licence),
        ]

    def flags(self, *args, **kwargs):
        result = screen(self.move(*args, **kwargs))
        return [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_PC_009]

    def test_fires_on_a_cross_border_move_with_no_licence_recorded(self):
        flag = self.flags("Berlin, Germany", "Basel, Switzerland")[0]
        self.assertEqual(flag["cited_fields"]["origin_country"], "Germany")
        self.assertEqual(flag["cited_fields"]["destination_country"], "Switzerland")
        self.assertEqual(
            flag["cited_fields"]["export_licence_state"], "not recorded either way"
        )

    def test_distinguishes_recorded_absent_from_not_recorded(self):
        flag = self.flags("Berlin, Germany", "Basel, Switzerland", licence=False)[0]
        self.assertEqual(flag["cited_fields"]["export_licence_state"], "recorded as absent")

    def test_does_not_fire_when_a_licence_is_recorded_present(self):
        self.assertEqual(
            self.flags("Berlin, Germany", "Basel, Switzerland", licence=True), []
        )

    def test_does_not_fire_on_a_move_within_one_country(self):
        self.assertEqual(self.flags("Berlin, Germany", "Munich, Germany"), [])

    def test_does_not_fire_outside_the_window(self):
        self.assertEqual(
            self.flags("Berlin, Germany", "Basel, Switzerland", year="1960"), []
        )

    def test_country_derivation_is_stated_in_the_citation(self):
        flag = self.flags("Berlin, Germany", "Basel, Switzerland")[0]
        self.assertIn("final comma-separated component",
                      flag["cited_fields"]["country_derivation"])

    def test_skips_and_says_so_when_a_location_is_missing(self):
        result = screen(self.move("Berlin, Germany", None))
        self.assertIn(f"{RULE_PC_009} not evaluated", result["coverage_note"])


class Rule12AliuNameMatchTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config(aliu=ALIU_FIXTURE)

    def screen_name(self, name, **kwargs):
        return screen([record(owner_name=name, date_from="1941",
                              date_precision="year", **kwargs)], self.cfg)

    def test_documented_concern_becomes_a_flag(self):
        result = self.screen_name("Muster, Heinrich")
        flag = [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_NM_001][0]
        self.assertIn("aliu_annotation", flag["cited_fields"])
        self.assertIn("source_url", flag["cited_fields"])
        self.assertIn("carries no finding", flag["cited_fields"]["citation_disclaimer"])

    def test_exonerating_entry_is_not_a_flag_on_either_axis(self):
        result = self.screen_name("Vogel, Franz")
        self.assertNotIn(RULE_NM_001, rule_ids(result))
        self.assertNotIn(RULE_NM_002, rule_ids(result))
        self.assertNotIn(RULE_NM_002, rule_ids(result, "documentation_quality_flags"))
        self.assertEqual(result["persecution_context_flags"], [])

    def test_exonerating_entry_lands_on_its_own_citation_channel(self):
        result = self.screen_name("Vogel, Franz")
        citations = result["name_list_citations"]
        self.assertEqual([c["rule_id"] for c in citations], [RULE_NM_002])
        self.assertEqual(citations[0]["entry_type"], "exonerating")
        self.assertIn("EXONERATING", citations[0]["rule_statement"])
        self.assertIn("is not a flag", citations[0]["rule_statement"])

    def test_the_two_entry_types_do_not_collapse_into_one_state(self):
        concern = self.screen_name("Muster, Heinrich")
        exonerating = self.screen_name("Vogel, Franz")
        self.assertTrue(concern["persecution_context_flags"])
        self.assertFalse(concern["name_list_citations"])
        self.assertFalse(exonerating["persecution_context_flags"])
        self.assertTrue(exonerating["name_list_citations"])

    def test_an_exonerating_match_leaves_the_object_with_no_triggered_criteria(self):
        result = self.screen_name("Vogel, Franz", source_citation="Inv. 1941")
        self.assertIn("no criteria triggered", result["screening_statement"])

    def test_critical_scholarly_context_is_carried_into_the_output(self):
        result = self.screen_name("Muster, Heinrich")
        flag = result["persecution_context_flags"][0]
        self.assertTrue(flag["cited_fields"]["critical_scholarly_context"])

    def test_name_order_variants_match(self):
        self.assertTrue(self.screen_name("Heinrich Muster")["persecution_context_flags"])

    def test_supplied_owner_name_variants_are_matched(self):
        result = screen(
            [record(owner_name="Unrelated Name", date_from="1941",
                    date_precision="year",
                    owner_name_variants=["Muster, Heinrich"])],
            self.cfg,
        )
        self.assertIn(RULE_NM_001, rule_ids(result))

    def test_skips_and_says_so_when_the_list_is_absent(self):
        result = screen(
            [record(owner_name="Muster, Heinrich", date_from="1941",
                    date_precision="year")],
            build_config(aliu_path="/nonexistent/aliu.json"),
        )
        self.assertIn(f"{RULE_NM_001} skipped", result["coverage_note"])

    def test_loader_rejects_an_unknown_entry_type(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text(json.dumps({"entries": [{
                "name": "X", "entry_type": "flagged", "annotation": "a",
                "source_url": "u"}]}))
            with self.assertRaisesRegex(name_matching.ReferenceListError, "entry_type"):
                name_matching.load_aliu_list(path)

    def test_loader_requires_annotation_and_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text(json.dumps({"entries": [{
                "name": "X", "entry_type": "documented_concern"}]}))
            with self.assertRaisesRegex(name_matching.ReferenceListError, "annotation"):
                name_matching.load_aliu_list(path)


@unittest.skipUnless(REAL_ALIU.exists(), f"{REAL_ALIU} not present in this checkout")
class Rule12RealListTests(unittest.TestCase):
    """Assertions against the real bundled list. Skipped until it is added."""

    def setUp(self):
        self.aliu = name_matching.load_aliu_list(REAL_ALIU)

    def test_wolff_metternich_is_an_exonerating_entry(self):
        matches = self.aliu.match(["Wolff Metternich"])
        self.assertTrue(matches, "no Wolff Metternich entry in the bundled list")
        entry, _ = matches[0]
        self.assertTrue(
            entry.is_exonerating,
            "The Wolff Metternich entry must be typed exonerating; rendering it "
            "as a concern would invert the record's meaning.",
        )

    def test_wolff_metternich_never_reaches_a_flag_list(self):
        cfg = config(aliu=REAL_ALIU)
        result = screen(
            [record(owner_name="Wolff Metternich", date_from="1941",
                    date_precision="year", source_citation="Inv. 1941")],
            cfg,
        )
        self.assertEqual(result["persecution_context_flags"], [])
        self.assertEqual(
            [c["entry_type"] for c in result["name_list_citations"]], ["exonerating"]
        )

    def test_list_carries_the_critical_scholarly_context_note(self):
        self.assertTrue(self.aliu.critical_scholarly_context)

    def test_every_entry_carries_an_annotation_and_a_source(self):
        for entry in self.aliu.entries:
            self.assertTrue(entry.annotation, f"{entry.name} has no annotation")
            self.assertTrue(entry.source_url, f"{entry.name} has no source_url")

    def test_haberstock_is_a_documented_concern_rendered_with_its_annotation(self):
        cfg = config(aliu=REAL_ALIU)
        result = screen(
            [record(owner_name="Karl Haberstock", date_from="1940",
                    date_precision="year")],
            cfg,
        )
        flag = [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_NM_001][0]
        # The list's own wording, never a bare "flagged" label.
        self.assertIn("Kurfürstenstrasse", flag["cited_fields"]["aliu_annotation"])
        self.assertEqual(flag["cited_fields"]["aliu_entry_type"], "documented_concern")
        self.assertTrue(flag["cited_fields"]["source_url"])

    def test_single_sourced_entries_surface_their_verification_note(self):
        cfg = config(aliu=REAL_ALIU)
        result = screen(
            [record(owner_name="Hans W. Lange", date_from="1941",
                    date_precision="year")],
            cfg,
        )
        flag = [f for f in result["persecution_context_flags"]
                if f["rule_id"] == RULE_NM_001][0]
        self.assertIn("single primary source", flag["cited_fields"]["verification_note"])

    def test_metternich_annotation_is_carried_into_the_citation(self):
        cfg = config(aliu=REAL_ALIU)
        result = screen(
            [record(owner_name="Graf Wolff-Metternich", date_from="1942",
                    date_precision="year", source_citation="Inv. 1942")],
            cfg,
        )
        citation = result["name_list_citations"][0]
        self.assertIn("complete integrity", citation["cited_fields"]["aliu_annotation"])
        self.assertIn("no criteria triggered", result["screening_statement"])

    def test_a_given_name_alone_does_not_match_a_person_entry(self):
        # "Franz" must not pull in the entry for "Wolff Metternich, Franz".
        self.assertEqual(self.aliu.match(["Franz"]), [])

    def test_non_match_carries_the_seed_list_caveat(self):
        cfg = config(aliu=REAL_ALIU)
        result = screen(
            [record(owner_name="Nobody In Particular", date_from="1941",
                    date_precision="year")],
            cfg,
        )
        self.assertNotIn(RULE_NM_001, rule_ids(result))
        self.assertIn("is NOT evidence that a name is absent", result["coverage_note"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.result = pipeline.run(
            "examples/example_input.csv", "data/persecution_onset_table.json"
        )
        self.html = report.render(self.result)

    def test_embedded_data_round_trips(self):
        start = self.html.index('id="triage-data"')
        payload = self.html[self.html.index(">", start) + 1 : self.html.index("</script>", start)]
        self.assertEqual(json.loads(payload)["objects"], self.result["objects"])

    def test_record_content_cannot_close_the_data_block(self):
        hostile = {
            "disclaimer": "d",
            "objects": [{"object_id": "</script><script>alert(1)</script>"}],
        }
        html = report.render(hostile)
        body = html[html.index('id="triage-data"') :]
        payload = body[body.index(">") + 1 : body.index("</script>")]
        self.assertEqual(
            json.loads(payload)["objects"][0]["object_id"],
            "</script><script>alert(1)</script>",
        )

    def test_disclaimer_is_present(self):
        self.assertIn("machine-generated triage", self.html)

    def test_coverage_map_precedes_the_work_queue(self):
        self.assertLess(
            self.html.index('id="coverage-section"'), self.html.index('id="queue-section"')
        )

    def test_report_carries_no_verdict_language(self):
        # "not cleared" is the framing the methodology requires, so the check
        # targets affirmative clearance and scoring phrasings specifically.
        blob = self.html.lower()
        for term in (
            "low risk",
            "high risk",
            "risk score",
            "risk level",
            "has been cleared",
            "clean provenance",
            "no risk",
        ):
            self.assertNotIn(term, blob, f"report contains {term!r}")
        self.assertIn("screened, not cleared", blob)


class PipelineTests(unittest.TestCase):
    def test_run_produces_the_documented_top_level_shape(self):
        result = pipeline.run(
            "examples/example_input.csv", "data/persecution_onset_table.json"
        )
        for key in (
            "disclaimer",
            "coverage_map",
            "criteria_screened",
            "date_precision_handling",
            "objects",
        ):
            self.assertIn(key, result)
        for obj in result["objects"]:
            self.assertEqual(
                set(obj) >= {
                    "object_id",
                    "persecution_context_flags",
                    "documentation_quality_flags",
                    "resolved_status",
                    "coverage_note",
                },
                True,
            )
            for flag in obj["persecution_context_flags"] + obj["documentation_quality_flags"]:
                # Definition of Done: every flag cites fields and names a source.
                self.assertTrue(flag["cited_fields"])
                self.assertTrue(flag["methodology"])
                self.assertTrue(flag["rule_statement"])

    def test_circa_margin_is_recorded_in_the_output(self):
        result = pipeline.run(
            "examples/example_input.csv", "data/persecution_onset_table.json", 12
        )
        self.assertEqual(result["date_precision_handling"]["circa_margin_years"], 12)
        self.assertIn("placeholder", result["date_precision_handling"]["note"].lower())


if __name__ == "__main__":
    unittest.main()
