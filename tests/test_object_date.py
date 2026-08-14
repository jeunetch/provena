"""PC-001 against the object's own creation date.

Written before the implementation, deliberately. The docs were wrong three
times in a row because behavioural claims had no tests; prose that is merely
shorter can be wrong just as confidently. So every claim this feature makes is
stated here first, as something that can fail, and the implementation is
whatever makes these pass.

The claim under test: PC-001 ("no pre-1945 provenance at all") is described as
the modal, highest-value rule, and it fires whenever nothing in the chain
predates 1945. For an object *created* in 1962 that is correct by the rule and
useless in practice — and it lands on the coverage map, which is the first
screen and the thing the README calls more diagnostic than any per-object
output. A mixed collection reports "N objects with no pre-1945 provenance"
where a large share could not have any.

What this feature does NOT do, also stated as tests: it does not silently
switch the rule off where the creation date is absent. A criterion that could
not run and a criterion that ran and found nothing are different states, and
an absent creation date is the former.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from src.csv_adapter import load_chains
from src.heuristics import build_config, coverage_map, screen_object
from src.schema import InputValidationError

HEADER = (
    "object_id,object_title,object_class,object_date,object_date_to,"
    "object_date_precision,owner_name,"
    "owner_name_variants,date_from,date_to,date_precision,transaction_state,"
    "location,source_citation,export_licence_present,is_institution_acquisition,"
    "catalogue_reference,owner_stated_in_catalogue,restitution_recipient_type,notes"
)


def row(object_id, object_date, owner, date_from, acquisition="",
        object_date_to="", precision=None):
    # Creation dates carry their own precision, exactly as transfer dates do.
    if precision is None:
        precision = "year" if object_date else ""
    return (
        f'{object_id},Work,painting,{object_date},{object_date_to},{precision},'
        f'"{owner}",,{date_from},,year,'
        f'purchase,"Bern, Switzerland",Reg,,{acquisition},,,'
    )


def screen(rows):
    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8"
    ) as handle:
        handle.write("\n".join([HEADER, *rows]) + "\n")
        path = pathlib.Path(handle.name)
    chains = load_chains(path)
    config = build_config()
    results = [screen_object(chain, config) for chain in chains]
    return chains, results, {r["object_id"]: r for r in results}


def rule_ids(result):
    return {f["rule_id"] for f in result["persecution_context_flags"]}


class PostwarObjectTests(unittest.TestCase):
    def test_an_object_created_after_1945_does_not_raise_pc_001(self):
        _, _, by_id = screen(
            [
                row("POST", "1962", "Meier, S.", "1965"),
                row("POST", "1962", "Municipal Art Collection", "1966", "true"),
            ]
        )
        self.assertNotIn(
            "PC-001",
            rule_ids(by_id["POST"]),
            "an object created in 1962 cannot have pre-1945 provenance; "
            "reporting its absence is noise on the first screen",
        )

    def test_the_reason_is_stated_rather_than_the_rule_silently_vanishing(self):
        _, _, by_id = screen([row("POST", "1962", "Meier, S.", "1965")])
        self.assertIn("PC-001", by_id["POST"]["coverage_note"])
        self.assertIn("1962", by_id["POST"]["coverage_note"])

    def test_an_object_created_before_1945_still_raises_pc_001(self):
        _, _, by_id = screen(
            [
                row("PRE", "1930", "Meier, S.", "1965"),
                row("PRE", "1930", "Municipal Art Collection", "1966", "true"),
            ]
        )
        self.assertIn("PC-001", rule_ids(by_id["PRE"]))

    def test_the_1945_boundary_falls_on_the_side_the_threshold_says(self):
        # Written first as "a 1945 work still raises PC-001", which was wrong:
        # a work created some time in 1945 cannot have had an owner in 1944, so
        # its earliest possible creation day IS the threshold and the rule does
        # not apply. Recording the correction rather than quietly flipping the
        # assertion — the boundary is the part of this feature most likely to
        # be re-argued.
        _, _, on_boundary = screen([row("EDGE", "1945", "Meier, S.", "1965")])
        self.assertNotIn("PC-001", rule_ids(on_boundary["EDGE"]))

        _, _, before = screen([row("EDGE", "1944", "Meier, S.", "1965")])
        self.assertIn("PC-001", rule_ids(before["EDGE"]))

    def test_a_year_precise_creation_date_is_not_read_as_day_precise(self):
        # "1946" means some time in 1946, so the object certainly postdates
        # 1945 only because its EARLIEST possible day does.
        _, _, by_id = screen([row("Y", "1946", "Meier, S.", "1965")])
        self.assertNotIn("PC-001", rule_ids(by_id["Y"]))


class AbsentCreationDateTests(unittest.TestCase):
    """An unrecorded creation date must not switch the rule off."""

    def test_pc_001_still_fires_when_no_creation_date_is_recorded(self):
        _, _, by_id = screen([row("BLANK", "", "Meier, S.", "1965")])
        self.assertIn(
            "PC-001",
            rule_ids(by_id["BLANK"]),
            "a blank creation date must not disable the modal rule",
        )

    def test_the_flag_says_the_finding_could_not_be_distinguished(self):
        _, _, by_id = screen([row("BLANK", "", "Meier, S.", "1965")])
        flag = next(
            f
            for f in by_id["BLANK"]["persecution_context_flags"]
            if f["rule_id"] == "PC-001"
        )
        cited = flag["cited_fields"]
        self.assertIn("object_date", cited)
        self.assertIsNone(cited["object_date"])
        # The distinction the rule cannot make without it.
        self.assertIn("created", flag["rule_statement"].lower())

    def test_a_recorded_pre_1945_creation_date_is_cited_on_the_flag(self):
        _, _, by_id = screen([row("PRE", "1930", "Meier, S.", "1965")])
        flag = next(
            f
            for f in by_id["PRE"]["persecution_context_flags"]
            if f["rule_id"] == "PC-001"
        )
        # The cited value carries its precision, like every other date in
        # the output — "1930 (year)", not a bare "1930".
        self.assertEqual(flag["cited_fields"]["object_date"], "1930 (year)")


class CoverageMapTests(unittest.TestCase):
    """The first screen is where this rule's noise actually lands."""

    def test_postwar_objects_are_counted_separately_not_as_undocumented(self):
        chains, results, _ = screen(
            [
                row("A", "1962", "Meier, S.", "1965"),
                row("B", "1930", "Meier, S.", "1965"),
                row("C", "", "Meier, S.", "1965"),
            ]
        )
        counts = coverage_map(chains, results)
        self.assertEqual(counts["objects_processed"], 3)
        self.assertEqual(counts["created_after_the_risk_band"], 1)
        # B and C are the genuine "no pre-1945 provenance" cases; A is not.
        self.assertEqual(counts["with_no_pre_1945_provenance"], 2)

    def test_the_map_says_how_many_objects_have_no_creation_date(self):
        chains, results, _ = screen(
            [row("A", "1962", "Meier, S.", "1965"), row("C", "", "Meier, S.", "1965")]
        )
        counts = coverage_map(chains, results)
        self.assertEqual(counts["creation_date_not_recorded"], 1)


class ImpreciseCreationDateTests(unittest.TestCase):
    """Most of what a museum's object records hold is imprecise.

    "ca. 1905", "1920s", "17th century". A field that accepts only exact ISO
    dates forces a curator either to drop the value into
    `creation_date_not_recorded` or to assert a precision the record does not
    support — and shrinking that bucket is the whole point of the field. The
    machinery already existed: the `date_precision` vocabulary, the documented
    circa margin, and the rule that a widened date can never yield a certain
    answer. This is reuse, not a new concept.
    """

    def test_a_circa_date_inside_the_widened_band_still_raises_pc_001(self):
        # circa 1948 widens to 1943-1953, which reaches back before the
        # threshold, so the object is not CERTAINLY post-war.
        _, _, by_id = screen([row("C", "1948", "Meier, S.", "1965", precision="circa")])
        self.assertIn("PC-001", rule_ids(by_id["C"]))

    def test_a_circa_date_clear_of_the_band_does_not(self):
        _, _, by_id = screen([row("C", "1962", "Meier, S.", "1965", precision="circa")])
        self.assertNotIn("PC-001", rule_ids(by_id["C"]))

    def test_a_decade_is_expressible_as_a_range(self):
        _, _, by_id = screen(
            [row("D", "1920", "Meier, S.", "1965", object_date_to="1929")]
        )
        self.assertIn("PC-001", rule_ids(by_id["D"]))

    def test_a_century_is_expressible_as_a_range(self):
        _, _, by_id = screen(
            [row("E", "1600", "Meier, S.", "1965", object_date_to="1699")]
        )
        self.assertIn("PC-001", rule_ids(by_id["E"]))

    def test_before_and_after_are_accepted(self):
        _, _, by_id = screen(
            [
                row("B1", "1930", "Meier, S.", "1965", precision="before"),
                row("A1", "1960", "Meier, S.", "1965", precision="after"),
            ]
        )
        self.assertIn("PC-001", rule_ids(by_id["B1"]))
        self.assertNotIn("PC-001", rule_ids(by_id["A1"]))

    def test_an_imprecise_date_still_counts_as_recorded(self):
        chains, results, _ = screen(
            [row("C", "1905", "Meier, S.", "1965", precision="circa")]
        )
        counts = coverage_map(chains, results)
        self.assertEqual(counts["creation_date_not_recorded"], 0)


class ValidationTests(unittest.TestCase):
    def test_a_malformed_creation_date_is_rejected(self):
        with self.assertRaises(InputValidationError) as caught:
            screen([row("BAD", "circa 1962", "Meier, S.", "1965")])
        problems = "\n".join(caught.exception.problems)
        self.assertIn("object_date", problems)
        # The message names the column to fix, not the transfer-date column
        # that shares the parsing code.
        self.assertNotIn("date_from", problems)

    def test_a_precision_is_required_whenever_a_creation_date_is_given(self):
        with self.assertRaises(InputValidationError) as caught:
            screen([row("BAD", "1962", "Meier, S.", "1965", precision="")])
        self.assertIn("object_date_precision", "\n".join(caught.exception.problems))

    def test_a_precision_finer_than_the_value_is_rejected(self):
        with self.assertRaises(InputValidationError):
            screen([row("BAD", "1962", "Meier, S.", "1965", precision="exact")])

    def test_an_impossible_creation_date_is_rejected(self):
        with self.assertRaises(InputValidationError):
            screen([row("BAD", "1962-13", "Meier, S.", "1965")])

    def test_the_column_is_optional(self):
        # Every existing file omits it, and must keep loading unchanged.
        legacy_header = HEADER.replace(
            "object_date,object_date_to,object_date_precision,", ""
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(
                legacy_header
                + "\n"
                + 'L,Work,painting,"Meier, S.",,1965,,year,purchase,'
                + '"Bern, Switzerland",Reg,,,,,\n'
            )
            path = pathlib.Path(handle.name)
        chains = load_chains(path)
        self.assertEqual(len(chains), 1)
        self.assertIsNone(chains[0].records[0].object_date)


if __name__ == "__main__":
    unittest.main()
