"""Tests for the LLM explanation layer.

The point of these is not that the prompt is well written — it is that a model
which ignores the prompt cannot get bad output into the report. Every case
below feeds the layer text a real model plausibly might produce and asserts
the guard catches it.

    python -m unittest discover tests
"""

import unittest

from src import llm_client, llm_guard, pipeline
from src.heuristics import build_config, screen_object
from src.llm_guard import (
    VIOLATION_ASSERTION,
    VIOLATION_FRAMING,
    VIOLATION_HISTORY,
    VIOLATION_LENGTH,
    VIOLATION_NUMBER,
    VIOLATION_PROPER_NOUN,
    load_language_pack,
    verify,
)
from src.schema import ProvenanceRecord, build_date_span, group_into_chains
from src.reao_taxonomy import TransactionState

PACK = load_language_pack()
CONFIG = build_config(aliu_path="/nonexistent", actors_path="/nonexistent")


def _flag():
    """A real PC-003 flag, so the guard is tested against realistic context."""
    record = ProvenanceRecord(
        object_id="OBJ-1",
        owner_name="Auktionshaus Steinbach",
        source_ref="test:row 1",
        source_order=0,
        date_from="1936-11-04",
        date_span=build_date_span("1936-11-04", None, "exact"),
        transaction_state=TransactionState.ZWANGSVERKAUF,
        location="Munich, Germany",
        source_citation="Auction catalogue, 4 Nov 1936, lot 210",
    )
    result = screen_object(group_into_chains([record])[0], CONFIG)
    return next(
        f for f in result["persecution_context_flags"] if f["rule_id"] == "PC-003"
    )


FLAG = _flag()
CONTEXT = llm_client.build_context(FLAG)
EN = PACK.get("en")

# Faithful: every name, number and term below appears in the cited fields.
COMPLIANT = (
    "Unverified: was this transfer made by a persecuted person? The record dates "
    "it to 1936-11-04 in Munich, Germany, and gives the transaction state as "
    "zwangsverkauf. It says nothing either way about fair value or the proceeds."
)


class Transport:
    """Returns scripted responses and remembers what it was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def check(text, language="en"):
    return verify(text, CONTEXT, PACK.get(language), PACK)


def kinds(result):
    return {v.kind for v in result.violations}


class CompliantOutputTests(unittest.TestCase):
    def test_a_faithful_restatement_is_accepted(self):
        result = check(COMPLIANT)
        self.assertTrue(result.accepted, result.reason)

    def test_terms_from_the_cited_fields_are_permitted(self):
        # "Munich" and "Germany" are proper nouns, but they are in the record.
        self.assertTrue(check(
            "Unverified: what happened in Munich, Germany? The record does not say."
        ).accepted)


class HallucinationTests(unittest.TestCase):
    """Each case is written the way a model actually fails, not as a strawman."""

    def test_invented_year_is_rejected(self):
        result = check(
            "Unverified: was this a forced sale? The work had been in the family "
            "since 1912 before the 1936 transfer."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_NUMBER, kinds(result))
        self.assertIn("'1912'", result.reason)

    def test_invented_person_is_rejected(self):
        result = check(
            "Unverified: who consigned this work? The catalogue was produced by "
            "Hildebrand Gurlitt for the sale."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_HISTORY, kinds(result))

    def test_invented_place_is_rejected(self):
        result = check(
            "Unverified: where did this transfer take place? The record points to "
            "a sale handled through Lucerne."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_PROPER_NOUN, kinds(result))
        self.assertIn("'Lucerne'", result.reason)

    def test_supplied_historical_context_is_rejected(self):
        result = check(
            "Unverified: was fair value received? Sales of this kind followed the "
            "Kristallnacht pogrom and were common under the Third Reich."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_HISTORY, kinds(result))

    def test_a_finding_is_rejected_even_when_otherwise_faithful(self):
        result = check(
            "This transfer was certainly a forced sale, and the 1936-11-04 date "
            "proves it. Unverified?"
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_ASSERTION, kinds(result))

    def test_risk_language_is_rejected(self):
        result = check("Unverified: this object is low risk given the record?")
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_ASSERTION, kinds(result))

    def test_a_statement_without_question_framing_is_rejected(self):
        result = check(
            "The record dates the transfer to 1936-11-04 in Munich, Germany."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_FRAMING, kinds(result))

    def test_elaboration_is_rejected_on_length(self):
        result = check("Unverified: was fair value received? " + ("The record is silent. " * 40))
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_LENGTH, kinds(result))

    def test_empty_output_is_rejected(self):
        self.assertFalse(check("   ").accepted)

    def test_a_plausible_sounding_biography_is_rejected(self):
        # The failure mode the layer exists for: fluent, confident, uncheckable.
        result = check(
            "Unverified: who was the consignor? The auction house was founded in "
            "1874 and was among the most prominent in Bavaria under the Nazi "
            "regime, handling many such consignments."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_NUMBER, kinds(result))
        self.assertIn(VIOLATION_HISTORY, kinds(result))


class GermanOutputTests(unittest.TestCase):
    """German cannot use per-token grounding — see GermanEntityGroundingTests.
    Every other check must still hold."""

    def test_ordinary_german_prose_is_not_rejected_wholesale(self):
        self.assertTrue(PACK.get("de").capitalises_common_nouns)
        result = check("Ungeklärt: wurde ein angemessener Gegenwert erzielt?", "de")
        self.assertTrue(result.accepted, result.reason)

    def test_numbers_are_still_checked_in_german(self):
        result = check("Ungeklärt: was geschah 1912 mit dem Werk?", "de")
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_NUMBER, kinds(result))

    def test_historical_terms_are_still_checked_in_german(self):
        result = check("Ungeklärt: erfolgte der Verkauf nach der Kristallnacht?", "de")
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_HISTORY, kinds(result))

    def test_german_findings_are_still_rejected(self):
        result = check("Das Werk wurde eindeutig geraubt. Ungeklärt?", "de")
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_ASSERTION, kinds(result))

    def test_a_negated_term_is_not_read_as_its_own_opposite(self):
        # Regression: "ungeklärt" (unresolved) contains "geklärt" (resolved).
        # Substring matching rejected the exact framing the layer requires.
        self.assertTrue(check("Ungeklärt: wurde ein Gegenwert erzielt?", "de").accepted)
        self.assertFalse(check("Der Fall ist geklärt. Ungeklärt?", "de").accepted)

    def test_an_english_term_in_german_output_is_still_caught(self):
        # A model asked for German may still reach for an English term.
        result = check("Ungeklärt: erfolgte dies nach dem Anschluss?", "de")
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_HISTORY, kinds(result))


class GermanEntityGroundingTests(unittest.TestCase):
    """Regression for the failure found by live validation on 2026-08-09.

    German output was exempt from the named-material check because
    capitalisation cannot separate a common noun from a proper one there. The
    consequence was that an entire uncited historical narrative passed. The
    check now runs for German against the cited fields plus a task-scoped
    restatement vocabulary, so narrative fails by construction.
    """

    NARRATIVE = (
        "Ungeklärt: wurde ein angemessener Gegenwert erzielt? Das Auktionshaus "
        "Steinbach war ein bedeutendes Handelshaus, das in dieser Zeit zahlreiche "
        "Enteignungen und Zwangsversteigerungen aus jüdischem Besitz abwickelte."
    )

    def test_the_uncited_narrative_that_passed_live_is_now_rejected(self):
        self.assertFalse(check(self.NARRATIVE, "de").accepted)

    def test_the_narrative_is_caught_as_historical_context_not_as_vocabulary(self):
        # Nazi-era practice terms belong on the historical-term list. Trying to
        # catch them with a task-vocabulary allowlist rejected 100% of live
        # German output on 2026-08-09, because the cited fields are English and
        # an ordinary German noun can never match them.
        result = check(self.NARRATIVE, "de")
        self.assertIn(VIOLATION_HISTORY, kinds(result))
        self.assertIn("enteignung", result.reason)

    def test_a_party_named_in_the_cited_fields_is_still_permitted(self):
        # "Auktionshaus Steinbach" IS the owner_name on this flag. Grounded
        # material must not be rejected just because it looks like an entity.
        self.assertTrue(
            check(
                "Ungeklärt: was ist über diesen Vorgang belegt? Als Eigentümer "
                "nennt die Aufzeichnung Auktionshaus Steinbach in Munich.",
                "de",
            ).accepted
        )

    def test_a_fabricated_german_name_is_rejected(self):
        result = check(
            "Ungeklärt: wer war beteiligt? Die Sammlung Rosenfeld übergab das Werk.",
            "de",
        )
        self.assertFalse(result.accepted)
        self.assertIn("Rosenfeld", result.reason)

    def test_ordinary_restatement_german_still_passes(self):
        self.assertTrue(check(
            "Ungeklärt: erfolgte diese Übertragung durch eine verfolgte Person? "
            "Die Aufzeichnung nennt nur das Datum und den Ort.", "de",
        ).accepted)

    def test_inflected_and_compound_nouns_are_grounded(self):
        # German inflects and compounds; rejecting "Angaben" for "Angabe" or
        # "Verkaufsdatum" for "Datum" would make the layer unusable.
        self.assertTrue(check(
            "Ungeklärt: welche Angaben enthält der Eintrag? Das Verkaufsdatum "
            "und der Auktionskatalog sind vermerkt, weitere Nachweise fehlen.",
            "de",
        ).accepted)

    def test_a_fabricated_dealer_name_is_caught_as_a_capitalised_run(self):
        result = check(
            "Ungeklärt: wer vermittelte? Der Kunsthändler Viktor Bauer trat auf.",
            "de",
        )
        self.assertFalse(result.accepted)
        self.assertIn("reads as a name", result.reason)

    def test_rich_but_faithful_german_vocabulary_is_not_rejected(self):
        # The regression that mattered: ordinary German nouns are absent from
        # the English cited fields by construction, so requiring each one to be
        # grounded made the layer produce nothing at all.
        self.assertTrue(check(
            "Ungeklärt: konnte der Veräußerer frei über den Erlös verfügen? Die "
            "Unterlagen enthalten dazu keine Angaben, weder zum Kaufpreis noch "
            "zu den Umständen.", "de",
        ).accepted, "faithful German with ordinary vocabulary must not be rejected")

    def test_a_single_ungrounded_noun_is_a_documented_blind_spot(self):
        # Stated as a test so the trade-off cannot be forgotten: a lone
        # ungrounded noun mid-sentence passes. That is the accepted cost of
        # German being emittable at all.
        self.assertTrue(check(
            "Ungeklärt: was ist belegt? Die Aufzeichnung nennt einen Vermerk.", "de"
        ).accepted)

    def test_inflected_historical_terms_are_still_caught(self):
        result = check(
            "Ungeklärt: wie kam es dazu? Es gab damals viele Zwangsversteigerungen.",
            "de",
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_HISTORY, kinds(result))

    def test_the_inflection_suffix_set_does_not_overmatch(self):
        # "nazi" + "onale" must not make the Italian "nazionale" a violation.
        self.assertNotIn(
            VIOLATION_HISTORY,
            kinds(check("Non verificato: il catalogo nazionale lo cita?", "it")),
        )


class PositionalCapitalTests(unittest.TestCase):
    """Regression for the false positives found live on 2026-08-09.

    German capitalises the first word of a clause after a colon as well as
    after a full stop, so ordinary verbs and interrogatives appear capitalised
    for purely positional reasons. Treating them as name material rejected
    faithful sentences: 'Ungeklärt: War Cordes, H. ...' read 'War Cordes' as a
    name, and 'Welche Dokumentation' likewise.
    """

    def test_a_clause_initial_verb_does_not_form_a_name_run(self):
        self.assertTrue(check(
            "Ungeklärt: War Cordes, H. der Eigentümer zum Zeitpunkt der "
            "Übertragung? Die Aufzeichnung nennt nur das Datum.", "de",
        ).accepted)

    def test_a_clause_initial_interrogative_does_not_form_a_name_run(self):
        self.assertTrue(check(
            "Ungeklärt: Welche Dokumentation nennt der Datensatz? Weitere "
            "Angaben fehlen.", "de",
        ).accepted)

    def test_clause_boundaries_beyond_the_full_stop_are_honoured(self):
        for text in (
            "Ungeklärt — Welche Unterlagen liegen vor? Keine weiteren Angaben.",
            "Ungeklärt; Konnte der Erlös frei verwendet werden?",
        ):
            self.assertTrue(check(text, "de").accepted, text)

    def test_a_preposition_before_a_cited_name_is_not_part_of_it(self):
        # The party here IS the owner_name on this flag, so naming it is
        # grounded; only the positional "Als" could wrongly extend the run.
        result = check(
            "Ungeklärt: Als Eigentümer nennt der Datensatz Auktionshaus "
            "Steinbach?", "de",
        )
        self.assertTrue(result.accepted, result.reason)

    def test_a_name_immediately_after_a_colon_is_still_caught(self):
        # The refinement that matters: skipping the first token of every
        # clause would lose exactly this, because a clause can begin with a
        # name. Positional words are skipped by identity, not by position.
        result = check(
            "Ungeklärt: Galerie Morgenstern trat als Vermittler auf?", "de"
        )
        self.assertFalse(result.accepted)
        self.assertIn("Morgenstern", result.reason)

    def test_a_name_at_the_very_start_of_the_output_is_still_caught(self):
        result = check("Sammlung Rosenfeld übergab das Werk. Ungeklärt?", "de")
        self.assertFalse(result.accepted)
        self.assertIn("Rosenfeld", result.reason)

    def test_a_clause_beginning_with_an_ordinary_noun_is_permitted(self):
        self.assertTrue(check(
            "Ungeklärt: Angaben zum Erlös fehlen im Datensatz?", "de"
        ).accepted)

    def test_positional_words_are_data_not_code(self):
        # The list lives in the language pack so it can be extended without
        # touching the guard.
        self.assertTrue(PACK.get("de").positional_capitals)
        self.assertFalse(PACK.get("en").positional_capitals)
        import pathlib

        import src.llm_guard as guard_module

        source = pathlib.Path(guard_module.__file__).read_text()
        self.assertNotIn("Welche", source)


class TranslatedPlaceNameTests(unittest.TestCase):
    """French and Italian were rejecting almost everything for correctly
    translating place names present in the record. Translating the input is
    not adding to it."""

    def test_french_may_translate_a_country_named_in_the_fields(self):
        self.assertTrue(check(
            "Non vérifié : ce transfert a-t-il été effectué par une personne "
            "persécutée ? Le dossier le date du 1936-11-04 à Munich, en Allemagne.",
            "fr",
        ).accepted)

    def test_italian_may_translate_both_city_and_country(self):
        self.assertTrue(check(
            "Non verificato: il trasferimento è stato effettuato da una persona "
            "perseguitata? Il documento lo data al 1936-11-04 a Monaco, in Germania.",
            "it",
        ).accepted)

    def test_a_place_absent_from_the_fields_is_still_rejected(self):
        result = check("Non vérifié : le dossier mentionne un transfert via Lisbonne ?", "fr")
        self.assertFalse(result.accepted)
        self.assertIn("Lisbonne", result.reason)

    def test_equivalence_never_grounds_a_token_on_its_own(self):
        # "Allemagne" is only permitted because "Germany" is in the fields. A
        # context with no German referent must still reject it.
        empty = {"rule_id": "X", "cited_fields": {"owner_name": "Nobody"}}
        from src.llm_guard import verify as verify_fn

        result = verify_fn(
            "Non vérifié : le dossier mentionne Allemagne ?",
            empty, PACK.get("fr"), PACK,
        )
        self.assertFalse(result.accepted)
        self.assertIn("Allemagne", result.reason)


class ItalianNarrativeTests(unittest.TestCase):
    """Regression for the leak found live on 2026-08-09: Italian
    direct-elicitation carried uncited period background 2/3 runs. Same class
    as the German bug, expressed in phrasing the term list did not cover."""

    LEAK = (
        "Non verificato: il trasferimento è stato effettuato da una persona "
        "perseguitata? Il documento data il trasferimento al 1936-11-04 a Munich, "
        "in Germania. Il trasferimento è avvenuto durante un periodo di forte "
        "pressione antisemita in Germania. Auktionshaus Steinbach era "
        "un'istituzione commerciale attiva in quel contesto storico."
    )

    def test_the_italian_leak_is_now_rejected(self):
        self.assertFalse(check(self.LEAK, "it").accepted)

    def test_period_characterisation_is_caught_as_historical_context(self):
        # Caught by two independent terms, so trimming either still rejects.
        reason = check(self.LEAK, "it").reason
        self.assertIn("antisemita", reason)
        self.assertIn("contesto storico", reason)

    def test_characterising_a_cited_party_is_rejected(self):
        # The name is grounded; the claim about it is not.
        result = check(
            "Non verificato: cosa risulta? Auktionshaus Steinbach era "
            "un'istituzione commerciale attiva in quel contesto storico.",
            "it",
        )
        self.assertFalse(result.accepted)

    def test_faithful_italian_is_unaffected(self):
        self.assertTrue(check(
            "Non verificato: il trasferimento è stato effettuato da una persona "
            "perseguitata? Il documento lo data al 1936-11-04 a Monaco, in Germania.",
            "it",
        ).accepted)

    def test_antisemitic_framing_is_caught_in_every_language(self):
        for code, text in (
            ("en", "Unverified: what happened? It occurred amid antisemitic pressure."),
            ("fr", "Non vérifié : que dit le dossier ? Dans un climat antisémite."),
            ("de", "Ungeklärt: was geschah? In einem antisemitischen Klima."),
        ):
            self.assertFalse(check(text, code).accepted, code)


class SentenceCapTests(unittest.TestCase):
    def test_the_cap_matches_the_prompt_s_own_instruction(self):
        # The prompt says "at most three short sentences", so a fourth is the
        # model ignoring the constraint — the point at which to stop trusting
        # the rest of the output.
        self.assertEqual(llm_guard.MAX_SENTENCES, 3)
        self.assertIn("three short sentences", llm_client.SYSTEM_PROMPT)

    def test_a_fourth_sentence_is_rejected(self):
        result = check(
            "Unverified: what does the record say? It gives a date. It gives a "
            "place. It gives a state."
        )
        self.assertFalse(result.accepted)
        self.assertIn(VIOLATION_LENGTH, kinds(result))

    def test_three_sentences_are_permitted(self):
        self.assertTrue(check(
            "Unverified: what does the record say? It gives a date and a place. "
            "It says nothing about the proceeds."
        ).accepted)


class SentenceCountingTests(unittest.TestCase):
    """Regression for the false too_long rejections in live run 5.

    German baseline fell from 75% to 42% when the sentence cap tightened. The
    cause was not German verbosity: a period ending an initial was counted as
    a sentence end, so an output citing an owner recorded as "Cordes, H."
    measured three sentences when it had two. German took the worst of it
    because it names the party more often, but the defect was language-neutral
    and English was affected identically.
    """

    def test_an_initial_does_not_end_a_sentence(self):
        self.assertEqual(llm_guard.count_sentences(
            "Ungeklärt: War Cordes, H. der Eigentümer? Die Aufzeichnung nennt "
            "nur das Datum."), 2)

    def test_the_same_defect_affected_english_identically(self):
        self.assertEqual(llm_guard.count_sentences(
            "Unverified: was Cordes, H. the owner? The record gives the date."), 2)

    def test_known_abbreviations_do_not_end_a_sentence(self):
        self.assertEqual(llm_guard.count_sentences(
            "Unverified: what is cited? It cites Nr. 12 and vol. 3."), 2)

    def test_genuine_sentences_are_still_counted(self):
        self.assertEqual(llm_guard.count_sentences(
            "Unverified: what does it say? It gives a date. It gives a place. "
            "It gives a state."), 4)

    def test_a_faithful_german_output_citing_an_initial_is_accepted(self):
        result = check(
            "Ungeklärt: War Cordes, H. der Eigentümer zum Zeitpunkt der "
            "Übertragung? Die Aufzeichnung nennt nur das Datum und den Ort.",
            "de",
        )
        self.assertTrue(result.accepted, result.reason)

    def test_three_german_sentences_with_an_initial_still_fit(self):
        self.assertTrue(check(
            "Ungeklärt: War Cordes, H. der Eigentümer? Die Aufzeichnung nennt "
            "das Datum. Weitere Nachweise fehlen.", "de",
        ).accepted)


class LanguageAwareLimitTests(unittest.TestCase):
    def test_german_gets_a_higher_character_allowance(self):
        self.assertGreater(
            PACK.get("de").max_characters, PACK.get("en").max_characters
        )

    def test_the_sentence_cap_is_not_raised_for_any_language(self):
        # Raising it for German would re-open the elaboration route the
        # Italian leak came through. German needs more characters per concept,
        # not more sentences.
        for code in ("en", "de", "fr", "it"):
            self.assertEqual(PACK.get(code).max_sentences, 3, code)

    def test_the_character_limit_reported_names_the_language(self):
        over_limit = "Ungeklärt? " + ("Die Aufzeichnung nennt das Datum. " * 40)
        result = check(over_limit, "de")
        self.assertFalse(result.accepted)
        self.assertIn("for de", result.reason)


class PlaceNameEquivalenceTests(unittest.TestCase):
    BERLIN = {
        "rule_id": "PC-003",
        "rule_statement": "Unverified: was this transfer made by a persecuted person?",
        "methodology": "REAO Art. 3; heightened from 15 September 1935 (Nuremberg Laws).",
        "cited_fields": {"owner_name": "Cordes, H.", "location": "Berlin, Germany"},
    }

    def check_berlin(self, text, language):
        return verify(text, self.BERLIN, PACK.get(language), PACK)

    def test_italian_city_forms_ground_on_the_record(self):
        self.assertTrue(self.check_berlin(
            "Non verificato: cosa indica il documento? Cita Berlino come luogo.", "it"
        ).accepted)

    def test_italian_form_of_a_methodology_term_grounds(self):
        self.assertTrue(self.check_berlin(
            "Non verificato: quale soglia si applica? Quella di Norimberga.", "it"
        ).accepted)

    def test_an_unrelated_city_is_still_rejected(self):
        result = self.check_berlin(
            "Non verificato: cosa indica il documento? Cita Lisbona come luogo.", "it"
        )
        self.assertFalse(result.accepted)
        self.assertIn("Lisbona", result.reason)


class DateFormatEquivalenceTests(unittest.TestCase):
    """A date in a cited field IS its written-out form.

    Live validation (2026-08-09, sixth run) rejected an English baseline
    output 0/3 for writing "June" against a record whose `date_from` reads
    "1934-06". That is a faithful translation of the input, the same category
    as writing "Allemagne" for "Germany" — not a fabrication. The allowance is
    derived from the dates actually present, so a month the record does not
    contain is still a violation.
    """

    JUNE = {
        "rule_id": "PC-003",
        "rule_statement": "Unverified: was fair value received?",
        "methodology": "REAO Art. 3.",
        "cited_fields": {"date_from": "1934-06", "location": "Berlin, Germany"},
    }
    NOVEMBER = {
        "rule_id": "PC-003",
        "rule_statement": "Unverified: was fair value received?",
        "methodology": "REAO Art. 3.",
        "cited_fields": {"date_from": "1936-11-04", "location": "Berlin, Germany"},
    }
    YEAR_ONLY = {
        "rule_id": "PC-003",
        "rule_statement": "Unverified: was fair value received?",
        "methodology": "REAO Art. 3.",
        "cited_fields": {"date_from": "1934", "location": "Berlin, Germany"},
    }

    def test_the_month_named_by_the_date_grounds_in_english(self):
        self.assertTrue(verify(
            "Unverified: was fair value received in June 1934?",
            self.JUNE, PACK.get("en"), PACK,
        ).accepted)

    def test_a_month_the_record_does_not_contain_is_still_rejected(self):
        result = verify(
            "Unverified: was fair value received in August 1934?",
            self.JUNE, PACK.get("en"), PACK,
        )
        self.assertFalse(result.accepted)
        self.assertIn("August", result.reason)

    def test_a_day_precise_date_grounds_its_month(self):
        self.assertTrue(verify(
            "Unverified: was fair value received in November 1936?",
            self.NOVEMBER, PACK.get("en"), PACK,
        ).accepted)

    def test_every_output_language_gets_its_own_month_form(self):
        for language, text in (
            ("de", "Ungeklärt: wurde im Juni 1934 ein angemessener Wert gezahlt?"),
            ("fr", "Non vérifié : une contrepartie a-t-elle été versée en juin 1934 ?"),
            ("it", "Non verificato: è stato pagato un valore equo nel giugno 1934?"),
        ):
            with self.subTest(language=language):
                self.assertTrue(
                    verify(text, self.JUNE, PACK.get(language), PACK).accepted
                )

    def test_a_year_only_date_grounds_no_month_at_all(self):
        result = verify(
            "Unverified: was fair value received in June 1934?",
            self.YEAR_ONLY, PACK.get("en"), PACK,
        )
        self.assertFalse(result.accepted)
        self.assertIn("June", result.reason)

    def test_a_month_named_in_the_fields_may_be_translated(self):
        # The other direction: the field spells the month out, the output is
        # in another language. Grounded through the equivalence route, which
        # still requires a group member to be present.
        flag = {
            "rule_id": "PC-003",
            "rule_statement": "Unverified: was fair value received?",
            "methodology": "REAO Art. 3; heightened from 15 September 1935.",
            "cited_fields": {"date_from": "June 1934"},
        }
        self.assertTrue(verify(
            "Ungeklärt: wurde im Juni 1934 ein angemessener Wert gezahlt?",
            flag, PACK.get("de"), PACK,
        ).accepted)

    def test_month_numbers_are_not_read_out_of_a_catalogue_reference(self):
        # The pattern is deliberately ISO-only. A reference like "Kat. 1938-07"
        # would be a date; "no. 3.5.1938" or "inv. 12/34" must not be.
        flag = {
            "rule_id": "DQ-001",
            "rule_statement": "Unverified: is a source recorded?",
            "methodology": "Documentation-quality axis.",
            "cited_fields": {"catalogue_reference": "inv. 12/34"},
        }
        result = verify(
            "Unverified: does the record cite a source for December?",
            flag, PACK.get("en"), PACK,
        )
        self.assertFalse(result.accepted)
        self.assertIn("December", result.reason)

    def test_a_version_string_is_not_read_as_a_date(self):
        # Every PC-003 flag cites `onset_table_version`, which is of the form
        # "2026-08-09b". A pattern without a trailing guard grounded "August"
        # on all of them — an allowance derived from a string that is not a
        # date at all.
        flag = {
            "rule_id": "PC-003",
            "rule_statement": "Unverified: was fair value received?",
            "methodology": "REAO Art. 3.",
            "cited_fields": {
                "date_span": "1934-06 (month)",
                "onset_table_version": "2026-08-09b",
            },
        }
        result = verify(
            "Unverified: was fair value received in August 1934?",
            flag, PACK.get("en"), PACK,
        )
        self.assertFalse(result.accepted)
        self.assertIn("August", result.reason)
        self.assertTrue(verify(
            "Unverified: was fair value received in June 1934?",
            flag, PACK.get("en"), PACK,
        ).accepted)

    def test_the_month_table_is_data_not_code(self):
        # The names live in the language pack so a deployment can correct or
        # extend them. Nothing in the guard knows any particular month name.
        import json
        import pathlib
        import tempfile

        import src.llm_guard as guard_module

        source = pathlib.Path(guard_module.__file__).read_text(encoding="utf-8")
        for name in ("January", "Januar", "June", "giugno", "novembre", "März"):
            self.assertFalse(name in source, f"{name!r} is hardcoded in the guard")
        self.assertEqual(sorted(PACK.month_names), list(range(1, 13)))

        # Drop the table and the grounding goes with it.
        raw = json.loads(
            pathlib.Path(llm_guard.DEFAULT_LANGUAGE_PATH).read_text(encoding="utf-8")
        )
        raw.pop("date_equivalence")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(raw, handle)
            path = handle.name
        stripped = llm_guard.load_language_pack(path)
        self.assertEqual(stripped.month_names, {})
        self.assertFalse(verify(
            "Unverified: was fair value received in June 1934?",
            self.JUNE, stripped.get("en"), stripped,
        ).accepted)


class LanguagePackTests(unittest.TestCase):
    def test_the_four_required_languages_are_present(self):
        for code in ("de", "fr", "it", "en"):
            self.assertIsNotNone(PACK.get(code))

    def test_an_unknown_language_fails_loudly(self):
        with self.assertRaises(llm_guard.LanguageError):
            PACK.get("es")


class GeneratorTests(unittest.TestCase):
    def generator(self, *responses, **kwargs):
        return llm_client.ExplanationGenerator(
            Transport(*responses), pack=PACK, **kwargs
        )

    def test_a_verified_explanation_is_returned(self):
        outcome = self.generator(COMPLIANT).explain(FLAG)
        self.assertEqual(outcome.text, COMPLIANT)
        self.assertEqual(outcome.status, "generated_and_verified")

    def test_a_failing_explanation_is_withheld_not_returned(self):
        outcome = self.generator("The 1912 sale proves it was looted.").explain(FLAG)
        self.assertIsNone(outcome.text)
        self.assertTrue(outcome.status.startswith("withheld:"))
        self.assertIn(VIOLATION_NUMBER, outcome.status)

    def test_a_retry_is_attempted_and_can_succeed(self):
        generator = self.generator("The 1912 sale proves it.", COMPLIANT)
        outcome = generator.explain(FLAG)
        self.assertEqual(outcome.text, COMPLIANT)
        self.assertEqual(outcome.attempts, 2)
        # The retry must actually tighten the instruction.
        self.assertIn("previous answer introduced", generator.transport.calls[1][0])

    def test_retries_are_bounded(self):
        generator = self.generator("Always 1912 bad.", max_retries=1)
        outcome = generator.explain(FLAG)
        self.assertIsNone(outcome.text)
        self.assertEqual(len(generator.transport.calls), 2)

    def test_a_transport_error_leaves_the_explanation_null(self):
        class Broken:
            def complete(self, system, user):
                raise RuntimeError("connection refused")

        outcome = llm_client.ExplanationGenerator(Broken(), pack=PACK).explain(FLAG)
        self.assertIsNone(outcome.text)
        self.assertIn("connection refused", outcome.status)

    def test_annotate_fills_every_flag_on_both_axes(self):
        record = ProvenanceRecord(
            object_id="OBJ-2", owner_name="owner", source_ref="test:row 1",
            source_order=0, date_from="1962",
            date_span=build_date_span("1962", None, "year"),
        )
        result = screen_object(group_into_chains([record])[0], CONFIG)
        llm_client.annotate(result, self.generator(COMPLIANT))
        flags = result["persecution_context_flags"] + result["documentation_quality_flags"]
        self.assertTrue(flags)
        for flag in flags:
            self.assertIn("llm_explanation_status", flag)


class ConstrainedContextTests(unittest.TestCase):
    """What leaves the machine is a data-protection question, not a detail."""

    def test_context_carries_only_the_flag_s_own_material(self):
        self.assertEqual(
            set(CONTEXT) - {"cited_fields"},
            {"rule_id", "rule_statement", "methodology", "presumption_tier"},
        )

    def test_uncited_free_text_is_not_sent(self):
        record = ProvenanceRecord(
            object_id="OBJ-3",
            owner_name="Cordes, H.",
            source_ref="test:row 1",
            source_order=0,
            date_from="1934",
            date_span=build_date_span("1934", None, "year"),
            location="Berlin, Germany",
            notes="Private family correspondence mentioning a health condition.",
        )
        result = screen_object(group_into_chains([record])[0], CONFIG)
        generator = llm_client.ExplanationGenerator(Transport(COMPLIANT), pack=PACK)
        llm_client.annotate(result, generator)
        sent = "\n".join(user for _, user in generator.transport.calls)
        self.assertNotIn("health condition", sent)
        leaked = llm_client.verify_payload_is_minimal(
            sent,
            result["persecution_context_flags"][0],
            {"notes": record.notes},
        )
        self.assertEqual(leaked, [])

    def test_the_prompt_states_the_constraints_verbatim(self):
        prompt = llm_client.SYSTEM_PROMPT.lower()
        for phrase in ("only", "do not introduce", "open question", "not deciding"):
            self.assertIn(phrase, prompt)


class PipelineIntegrationTests(unittest.TestCase):
    def test_layer_is_off_by_default_and_says_so(self):
        result = pipeline.run("examples/example_input.csv")
        self.assertFalse(result["llm_layer"]["enabled"])
        for obj in result["objects"]:
            for flag in obj["persecution_context_flags"] + obj["documentation_quality_flags"]:
                self.assertIsNone(flag["llm_explanation"])

    def test_a_hallucinating_model_produces_no_explanations_at_all(self):
        generator = llm_client.ExplanationGenerator(
            Transport(
                "Following Kristallnacht in 1938, Hildebrand Gurlitt acquired this "
                "work; it was certainly looted."
            ),
            pack=PACK,
        )
        result = pipeline.run(
            "examples/example_input.csv", explanation_generator=generator
        )
        self.assertTrue(result["llm_layer"]["enabled"])
        seen = 0
        for obj in result["objects"]:
            for flag in (
                obj["persecution_context_flags"]
                + obj["documentation_quality_flags"]
                + obj["name_list_citations"]
            ):
                seen += 1
                self.assertIsNone(flag["llm_explanation"])
                self.assertTrue(flag["llm_explanation_status"].startswith("withheld:"))
        self.assertGreater(seen, 20)

    def test_http_transport_refuses_to_guess_an_endpoint(self):
        with self.assertRaises(llm_client.LlmConfigurationError):
            llm_client.HttpTransport(
                llm_client.LlmSettings(endpoint="", api_key="k", model="m")
            )

    def test_http_transport_requires_a_key_and_a_model(self):
        with self.assertRaises(llm_client.LlmConfigurationError):
            llm_client.HttpTransport(
                llm_client.LlmSettings(endpoint="https://x/y", api_key="", model="m")
            )
        with self.assertRaises(llm_client.LlmConfigurationError):
            llm_client.HttpTransport(
                llm_client.LlmSettings(endpoint="https://x/y", api_key="k", model="")
            )


if __name__ == "__main__":
    unittest.main()
