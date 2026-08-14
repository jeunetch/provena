"""Every persecution state in the enum must be able to reach the queue.

The defect this file exists for: `transaction_state` — the REAO-grounded
field CLAUDE.md calls the correction that grounds the whole taxonomy — drove
no rule of its own. It was read in exactly three places: to pick a tier
*inside* the territory-window gate, to suppress settled records, and as the
independent-establishment check on the actor gate. So a curator who had
already done the research and recorded `zwangsverkauf` got an empty queue
entry, while an ordinary purchase in Munich flagged.

Two consequences make this worse than an ordinary miss:

* The terminal state read "Screened against N criteria; no criteria
  triggered" over a record that says persecution on its face, and the
  coverage note listed PC-003 under *evaluated* rather than skipped. That is
  the failure the no-score design exists to prevent, reached by another route.
* Switzerland has no persecution window and correctly never will — it was not
  occupied. `fluchtgut` is defined in the spec as the Swiss-specific category
  and is named as directly relevant to this project's home jurisdiction. It
  could not fire at all, because the only rule that could fire it required an
  occupation window Switzerland by definition does not have.

The last test in this file is the audit generalised: it walks the whole enum
and asserts that every state which evidences persecution reaches a flag. That
is the test that would have caught this, and it is written so the same class
of defect cannot return when a state is added.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from src.csv_adapter import load_chains
from src.heuristics import (
    RULE_PC_003,
    RULE_PC_010,
    build_config,
    screen_object,
)
from src.reao_taxonomy import PresumptionTier, TransactionState, is_resolved_state

HEADER = (
    "object_id,object_title,object_class,object_date,object_date_to,"
    "object_date_precision,owner_name,owner_name_variants,date_from,date_to,"
    "date_precision,transaction_state,location,source_citation,"
    "export_licence_present,is_institution_acquisition,catalogue_reference,"
    "owner_stated_in_catalogue,restitution_recipient_type,notes"
)


def row(object_id, state, location="Zurich, Switzerland", date_from="1940",
        precision="year", recipient=""):
    return (
        f'{object_id},W,painting,,,,"Meier, S.",,{date_from},,{precision},'
        f'{state},"{location}",Src,,,,,{recipient},'
    )


def screen(rows):
    with tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8"
    ) as handle:
        handle.write("\n".join([HEADER, *rows]) + "\n")
        path = pathlib.Path(handle.name)
    config = build_config()
    return {
        r["object_id"]: r
        for r in (screen_object(c, config) for c in load_chains(path))
    }


def flags(result, rule_id=None):
    return [
        f
        for f in result["persecution_context_flags"]
        if rule_id is None or f["rule_id"] == rule_id
    ]


class RecordedPersecutionStateTests(unittest.TestCase):
    """A record that states a persecution transfer engages the rule directly."""

    def test_a_forced_sale_in_switzerland_reaches_the_queue(self):
        result = screen([row("CH", "zwangsverkauf", "Geneva, Switzerland")])["CH"]
        self.assertTrue(
            flags(result, RULE_PC_003),
            "a recorded forced sale produced an empty queue entry",
        )

    def test_a_seizure_by_state_act_reaches_the_queue(self):
        result = screen([row("CH", "entziehung")])["CH"]
        self.assertTrue(flags(result, RULE_PC_003))

    def test_the_tier_basis_says_it_came_from_the_record_not_a_window(self):
        flag = flags(screen([row("CH", "zwangsverkauf")])["CH"], RULE_PC_003)[0]
        basis = flag["cited_fields"]["presumption_tier_basis"]
        self.assertIn("no territory window", basis.lower())
        self.assertIn(
            "transaction_state", flag["cited_fields"]["basis"]
        )
        # And it must not claim a window it never matched.
        self.assertNotIn("territory", flag["cited_fields"])

    def test_the_two_presumption_states_carry_their_own_tiers(self):
        for state, tier in (
            ("vermutung_der_entziehung", PresumptionTier.ORDINARY),
            ("verschaerfte_vermutung", PresumptionTier.HEIGHTENED),
        ):
            with self.subTest(state=state):
                flag = flags(screen([row("S", state)])["S"], RULE_PC_003)[0]
                self.assertEqual(flag["presumption_tier"], tier.value)

    def test_an_undated_record_still_engages_on_its_stated_type(self):
        # The state says what happened regardless of when. The tier cannot be
        # derived without a date, and the flag says so rather than guessing.
        result = screen([row("U", "zwangsverkauf", date_from="", precision="")])["U"]
        found = flags(result, RULE_PC_003)
        self.assertTrue(found)
        self.assertEqual(found[0]["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)

    def test_a_neutral_state_does_not_engage(self):
        for state in ("purchase", "gift", "exchange", "unknown"):
            with self.subTest(state=state):
                self.assertEqual(flags(screen([row("N", state)])["N"], RULE_PC_003), [])

    def test_a_settled_record_is_still_suppressed(self):
        # resolved_status must keep winning over the new limb.
        result = screen(
            [row("R", "restitution_erfolgt_or_vergleich", recipient="individual_or_heirs")]
        )["R"]
        self.assertEqual(flags(result, RULE_PC_003), [])
        self.assertEqual(result["resolved_status"], "previously_resolved")

    def test_a_window_match_does_not_produce_a_duplicate(self):
        # Munich 1940 matches a territory window AND states a forced sale.
        result = screen([row("DE", "zwangsverkauf", "Munich, Germany")])["DE"]
        self.assertEqual(
            len(flags(result, RULE_PC_003)),
            1,
            "the window limb and the state limb both fired for one record",
        )
        cited = flags(result, RULE_PC_003)[0]["cited_fields"]
        self.assertIn("territory", cited, "the window limb should be the one kept")


class TierDerivationTests(unittest.TestCase):
    """Where the date cannot be placed against the threshold, decline.

    Two bugs with opposite signs, both from picking a tier instead of
    declining to. A 1970 record was clipped to the risk band, produced an
    empty intersection, and fell through to the MILDER tier — with a basis
    string claiming its position was undetermined when it plainly was not. A
    `circa 1935` record was assigned the HARSHER tier while its own basis
    string said the position was undetermined.

    In both, the machine-readable field contradicted the text a reader
    consults to check it, which is the placement problem `identity_caveat`
    had before it moved into `rule_statement`.
    """

    def tier_and_basis(self, date_from, precision="year", state="zwangsverkauf"):
        result = screen([row("T", state, date_from=date_from, precision=precision)])["T"]
        flag = flags(result, RULE_PC_003)[0]
        return flag["presumption_tier"], flag["cited_fields"]["presumption_tier_basis"]

    def test_a_date_wholly_after_the_threshold_is_heightened(self):
        for year in ("1940", "1954", "1955"):
            with self.subTest(year=year):
                tier, basis = self.tier_and_basis(year)
                self.assertEqual(tier, PresumptionTier.HEIGHTENED.value)
                self.assertIn("wholly on or after", basis)

    def test_a_date_wholly_before_the_threshold_is_ordinary(self):
        # In-band but pre-threshold: the band opens 1933-01-30 and the
        # Nuremberg threshold is 1935-09-15.
        tier, basis = self.tier_and_basis("1934")
        self.assertEqual(tier, PresumptionTier.ORDINARY.value)
        self.assertIn("wholly before", basis)

    def test_a_date_before_the_band_opens_is_also_out_of_band(self):
        # Written expecting "ordinary" and corrected: 1930 predates the
        # earliest onset in the table, so it is outside the band by exactly
        # the same rule as 1970, and the state and the date disagree in the
        # same way. Recording the correction rather than flipping it.
        tier, basis = self.tier_and_basis("1930")
        self.assertEqual(tier, PresumptionTier.NOT_APPLICABLE.value)
        self.assertIn("outside the screening band", basis)

    def test_a_date_outside_the_band_asserts_no_tier(self):
        # Was: ordinary — the milder tier, on data the code could not place,
        # for a date further past the threshold than any heightened case.
        for year in ("1956", "1970", "2001"):
            with self.subTest(year=year):
                tier, basis = self.tier_and_basis(year)
                self.assertEqual(tier, PresumptionTier.NOT_APPLICABLE.value)
                self.assertIn("outside the screening band", basis)
                self.assertIn("do not agree", basis)

    def test_a_record_outside_the_band_still_reaches_the_queue(self):
        # The state still says a persecution transfer occurred. Only the tier
        # is withheld.
        result = screen([row("T", "zwangsverkauf", date_from="2001")])["T"]
        self.assertTrue(flags(result, RULE_PC_003))

    def test_a_circa_date_straddling_the_threshold_asserts_no_tier(self):
        # Was: heightened, while the basis said the position was undetermined.
        tier, basis = self.tier_and_basis("1935", precision="circa")
        self.assertEqual(tier, PresumptionTier.NOT_APPLICABLE.value)
        self.assertIn("cannot be placed wholly on either side", basis)

    def test_a_circa_date_clear_of_the_threshold_still_resolves(self):
        # Declining must not become declining always: circa 1950 widens to
        # 1945-1955, which is wholly after the threshold.
        tier, _ = self.tier_and_basis("1950", precision="circa")
        self.assertEqual(tier, PresumptionTier.HEIGHTENED.value)

    def test_an_open_ended_date_resolves_only_when_it_can(self):
        before, _ = self.tier_and_basis("1934", precision="before")
        self.assertEqual(before, PresumptionTier.ORDINARY.value)
        after, _ = self.tier_and_basis("1940", precision="after")
        self.assertEqual(after, PresumptionTier.HEIGHTENED.value)

    def test_the_basis_never_contradicts_the_tier_it_explains(self):
        # The invariant behind both bugs, stated so neither can return.
        for date_from, precision in (
            ("1940", "year"), ("1955", "year"), ("1956", "year"), ("1970", "year"),
            ("2001", "year"), ("1935", "circa"), ("1950", "circa"),
            ("1934", "year"), ("1930", "year"), ("1934", "before"),
            ("1940", "after"),
        ):
            with self.subTest(date=date_from, precision=precision):
                tier, basis = self.tier_and_basis(date_from, precision)
                undetermined = (
                    "cannot be placed" in basis or "no tier asserted" in basis
                )
                self.assertEqual(
                    undetermined,
                    tier == PresumptionTier.NOT_APPLICABLE.value,
                    f"basis says undetermined={undetermined} but tier is {tier}",
                )

    def test_the_state_limb_does_not_point_at_an_onset_table_entry(self):
        # The window methodology ends "see the cited persecution-onset table
        # entry". A state-based flag cites none, and said so in cited_fields
        # while the methodology string still pointed at one.
        flag = flags(screen([row("T", "zwangsverkauf")])["T"], RULE_PC_003)[0]
        self.assertNotIn(
            "see the cited persecution-onset table entry", flag["methodology"]
        )
        self.assertIn("none was matched", flag["methodology"])

    def test_the_window_limb_keeps_its_own_methodology(self):
        flag = flags(
            screen([row("D", "purchase", "Munich, Germany")])["D"], RULE_PC_003
        )[0]
        self.assertIn("persecution-onset table entry", flag["methodology"])


class FluchtgutTests(unittest.TestCase):
    """`fluchtgut` is a contested category, not a presumption."""

    def test_it_reaches_the_queue_on_its_own(self):
        result = screen([row("CH", "fluchtgut", "Zurich, Switzerland", "1938")])["CH"]
        self.assertTrue(
            flags(result, RULE_PC_010),
            "the category named as most relevant to the tool's home "
            "jurisdiction could not fire at all",
        )

    def test_it_asserts_no_presumption_tier(self):
        flag = flags(screen([row("CH", "fluchtgut")])["CH"], RULE_PC_010)[0]
        self.assertEqual(flag["presumption_tier"], PresumptionTier.NOT_APPLICABLE.value)

    def test_it_is_phrased_as_a_contested_question_not_a_finding(self):
        flag = flags(screen([row("CH", "fluchtgut")])["CH"], RULE_PC_010)[0]
        statement = flag["rule_statement"].lower()
        self.assertIn("contested", statement)
        self.assertIn("unverified", statement)
        for banned in ("was looted", "proves", "establishes that"):
            self.assertNotIn(banned, statement)

    def test_it_does_not_also_raise_the_presumption_rule(self):
        result = screen([row("CH", "fluchtgut")])["CH"]
        self.assertEqual(flags(result, RULE_PC_003), [])


class EnumReachabilityAudit(unittest.TestCase):
    """The audit that would have caught the original defect, generalised.

    Every state that evidences persecution must be able to produce a flag from
    a record that states it and nothing else. A state in the enum that no rule
    consumes is a field a curator can fill in correctly and get nothing for.
    """

    NEUTRAL = {
        TransactionState.PURCHASE,
        TransactionState.GIFT,
        TransactionState.EXCHANGE,
        TransactionState.UNKNOWN,
    }

    def test_every_persecution_state_reaches_a_flag(self):
        unreachable = []
        for state in TransactionState:
            if state in self.NEUTRAL or is_resolved_state(state):
                continue
            # Switzerland deliberately: no territory window exists or ever
            # will, so anything that fires here fires on the state alone.
            result = screen([row("A", state.value, "Zurich, Switzerland")])["A"]
            if not result["persecution_context_flags"]:
                unreachable.append(state.value)
        self.assertEqual(
            unreachable,
            [],
            f"states in the enum that no rule consumes: {unreachable}",
        )

    def test_a_neutral_state_alone_still_reaches_nothing(self):
        # The other direction, so the fix cannot be "flag everything".
        for state in self.NEUTRAL:
            with self.subTest(state=state.value):
                result = screen([row("A", state.value, "Zurich, Switzerland")])["A"]
                self.assertEqual(result["persecution_context_flags"], [])

    def test_the_terminal_state_is_no_longer_reached_by_a_persecution_record(self):
        result = screen([row("CH", "zwangsverkauf", "Geneva, Switzerland")])["CH"]
        self.assertNotIn("no criteria triggered", result["screening_statement"])


if __name__ == "__main__":
    unittest.main()
