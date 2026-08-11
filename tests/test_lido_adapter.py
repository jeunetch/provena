"""Tests for the LIDO-XML adapter and the PROVENA_EXT convention.

The theme running through these is that the adapter must never quietly turn a
LIDO record into a claim the record does not make: not a REAO state guessed
from an event term, not a day precision manufactured by a fully-expanded year,
and not an owner picked out of an ambiguous set of actors.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from src import lido_adapter, pipeline
from src.heuristics import build_config, screen_object
from src.reao_taxonomy import RestitutionRecipientType, TransactionState
from src.schema import DatePrecision, InputValidationError, group_into_chains

EXAMPLE = "examples/example_input.xml"

NS = 'xmlns:lido="http://www.lido-schema.org"'


def wrap(events: str, object_id: str = "OBJ-1", work_type: str = "painting") -> str:
    """A minimal one-object LIDO file around the given event markup."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<lido:lidoWrap {NS}>
  <lido:lido>
    <lido:lidoRecID lido:type="local">{object_id}</lido:lidoRecID>
    <lido:descriptiveMetadata xml:lang="en">
      <lido:objectClassificationWrap>
        <lido:objectWorkTypeWrap>
          <lido:objectWorkType><lido:term>{work_type}</lido:term></lido:objectWorkType>
        </lido:objectWorkTypeWrap>
      </lido:objectClassificationWrap>
      <lido:objectIdentificationWrap>
        <lido:titleWrap>
          <lido:titleSet><lido:appellationValue>A Title</lido:appellationValue></lido:titleSet>
        </lido:titleWrap>
      </lido:objectIdentificationWrap>
      <lido:eventWrap>{events}</lido:eventWrap>
    </lido:descriptiveMetadata>
  </lido:lido>
</lido:lidoWrap>
"""


def event(
    *,
    term: str = "Purchase",
    concept: str | None = "lido01016",
    actors: str | None = None,
    dates: str = "",
    place: str = "",
    note: str = "",
    citation: str = "",
) -> str:
    if actors is None:
        actors = actor("Somebody, A.", role="owner")
    concept_xml = (
        f'<lido:conceptID lido:type="URI">http://terminology.lido-schema.org/{concept}</lido:conceptID>'
        if concept
        else ""
    )
    description = ""
    if note or citation:
        description = (
            "<lido:eventDescriptionSet>"
            + (f"<lido:descriptiveNoteValue>{note}</lido:descriptiveNoteValue>" if note else "")
            + (f"<lido:sourceDescriptiveNote>{citation}</lido:sourceDescriptiveNote>" if citation else "")
            + "</lido:eventDescriptionSet>"
        )
    return f"""<lido:eventSet><lido:event>
      <lido:eventType>{concept_xml}<lido:term>{term}</lido:term></lido:eventType>
      {actors}{dates}{place}{description}
    </lido:event></lido:eventSet>"""


def actor(*names: str, role: str | None = None, pref: bool = False) -> str:
    def marked(index: int) -> str:
        if not pref:
            return ""
        return ' lido:pref="preferred"' if index == 0 else ' lido:pref="alternate"'

    values = "".join(
        f"<lido:appellationValue{marked(i)}>{n}</lido:appellationValue>"
        for i, n in enumerate(names)
    )
    role_xml = (
        f"<lido:roleActor><lido:term>{role}</lido:term></lido:roleActor>" if role else ""
    )
    return (
        "<lido:eventActor><lido:actorInRole><lido:actor>"
        f"<lido:nameActorSet>{values}</lido:nameActorSet>"
        f"</lido:actor>{role_xml}</lido:actorInRole></lido:eventActor>"
    )


def dates(earliest: str | None = None, latest: str | None = None, display: str = "") -> str:
    inner = ""
    if earliest:
        inner += f"<lido:earliestDate>{earliest}</lido:earliestDate>"
    if latest:
        inner += f"<lido:latestDate>{latest}</lido:latestDate>"
    return (
        "<lido:eventDate>"
        + (f"<lido:displayDate>{display}</lido:displayDate>" if display else "")
        + (f"<lido:date>{inner}</lido:date>" if inner else "")
        + "</lido:eventDate>"
    )


def place(*nested: str) -> str:
    """A place with its partOfPlace ancestors, innermost first."""
    xml = ""
    for name in reversed(nested):
        inner = f"<lido:partOfPlace>{xml}</lido:partOfPlace>" if xml else ""
        xml = (
            f"<lido:namePlaceSet><lido:appellationValue>{name}</lido:appellationValue>"
            f"</lido:namePlaceSet>{inner}"
        )
    return f"<lido:eventPlace><lido:place>{xml}</lido:place></lido:eventPlace>"


class AdapterTestCase(unittest.TestCase):
    def load(self, xml: str):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(xml)
            path = handle.name
        return lido_adapter.load(path)

    def load_one(self, xml: str):
        ingest = self.load(xml)
        self.assertEqual(len(ingest.records), 1, "expected exactly one event")
        return ingest.records[0], ingest

    def expect_rejection(self, xml: str) -> str:
        with self.assertRaises(InputValidationError) as caught:
            self.load(xml)
        return "\n".join(caught.exception.problems)


class ExampleFileTests(AdapterTestCase):
    """The Definition of Done: the bundled LIDO example runs end to end."""

    def setUp(self):
        self.ingest = lido_adapter.load(EXAMPLE)
        self.chains = group_into_chains(self.ingest.records)

    def test_every_object_and_event_loads(self):
        self.assertEqual([c.object_id for c in self.chains],
                         [f"OBJ-10{n}" for n in range(1, 7)])
        self.assertEqual(len(self.ingest.records), 14)

    def test_the_full_rule_set_fires_on_lido_input(self):
        config = build_config()
        triggered = set()
        for chain in self.chains:
            result = screen_object(chain, config)
            for flag in (
                result["persecution_context_flags"]
                + result["documentation_quality_flags"]
            ):
                triggered.add(flag["rule_id"])
        # The example is built to reach each of these; a mapping regression
        # shows up here as a rule that silently stops firing.
        for rule_id in ("PC-001", "PC-002", "PC-003", "PC-004", "PC-007", "PC-008"):
            self.assertIn(rule_id, triggered, rule_id)

    def test_a_restitution_event_marks_the_object_resolved(self):
        config = build_config()
        by_id = {c.object_id: c for c in self.chains}
        result = screen_object(by_id["OBJ-103"], config)
        self.assertEqual(result["resolved_status"], "previously_resolved")

    def test_the_example_produces_exactly_one_ingest_note(self):
        # The Looting event. A note per "Change of legal title" event would be
        # noise: those records state their REAO state through the convention,
        # which is what an unmapped generic term is supposed to prompt.
        self.assertEqual([n.kind for n in self.ingest.notes], ["event_type_not_mapped"])
        self.assertIn("Looting", self.ingest.notes[0].detail)


class EventTypeMappingTests(AdapterTestCase):
    def test_native_terms_map_to_their_reao_equivalents(self):
        for term, concept, expected in (
            ("Purchase", "lido01016", TransactionState.PURCHASE),
            ("Gift", "lido00996", TransactionState.GIFT),
            ("Exchange", "lido01015", TransactionState.EXCHANGE),
            ("Restitution", "lido00724",
             TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH),
            ("Unknown provenance event", "lido01130", TransactionState.UNKNOWN),
        ):
            with self.subTest(term=term):
                record, _ = self.load_one(wrap(event(term=term, concept=concept)))
                self.assertEqual(record.transaction_state, expected)

    def test_looting_is_not_mapped_to_a_seizure_by_state_act(self):
        # The tempting mapping. REAO `entziehung` means a STATE act; LIDO's
        # term covers private theft too, so the mapping would assert something
        # the record does not establish.
        record, ingest = self.load_one(
            wrap(event(term="Looting", concept="lido01011", dates=dates("1942")))
        )
        self.assertIsNone(record.transaction_state)
        self.assertEqual([n.kind for n in ingest.notes], ["event_type_not_mapped"])
        self.assertIn("STATE act", ingest.notes[0].detail)

    def test_repatriation_is_not_mapped_to_a_settled_claim(self):
        # Over-mapping here would hold the object OUT of the active queue.
        record, ingest = self.load_one(
            wrap(event(term="Repatriation", concept="lido01153"))
        )
        self.assertIsNone(record.transaction_state)
        self.assertTrue(ingest.notes)

    def test_an_unmapped_event_is_still_screened(self):
        # Not mapping must not mean not triaging. A dated looting event inside
        # a persecution window still has to reach the queue.
        ingest = self.load(
            wrap(
                event(
                    term="Looting",
                    concept="lido01011",
                    dates=dates("1942-03", "1942-03"),
                    place=place("Paris", "France"),
                )
            )
        )
        chain = group_into_chains(ingest.records)[0]
        result = screen_object(chain, build_config())
        self.assertIn(
            "PC-003", {f["rule_id"] for f in result["persecution_context_flags"]}
        )

    def test_a_term_the_map_has_never_heard_of_is_reported_not_guessed(self):
        record, ingest = self.load_one(
            wrap(event(term="Municipal Reallocation", concept=None))
        )
        self.assertIsNone(record.transaction_state)
        self.assertIn("Municipal Reallocation", ingest.notes[0].detail)

    def test_the_map_is_data_not_code(self):
        source = pathlib.Path(lido_adapter.__file__).read_text(encoding="utf-8")
        for state in ("zwangsverkauf", "vermutung_der_entziehung", "lido01016"):
            self.assertNotIn(state, source, f"{state} is hardcoded in the adapter")

    def test_a_bad_state_in_the_map_fails_at_load(self):
        raw = json.loads(
            pathlib.Path(lido_adapter.DEFAULT_EVENT_TYPE_MAP_PATH).read_text("utf-8")
        )
        raw["mapped"][0]["transaction_state"] = "beschlagnahme"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(raw, handle)
            path = handle.name
        with self.assertRaises(ValueError):
            lido_adapter.load_event_type_map(path)


class ExtensionConventionTests(AdapterTestCase):
    def test_the_five_fields_with_no_native_carrier_arrive(self):
        note = "\n".join(
            [
                "PROVENA_EXT:is_institution_acquisition=true",
                "PROVENA_EXT:export_licence_present=false",
                "PROVENA_EXT:catalogue_reference=Catalogue, 1928 edn, no. 2",
                "PROVENA_EXT:owner_stated_in_catalogue=true",
            ]
        )
        record, _ = self.load_one(wrap(event(note=note)))
        self.assertIs(record.is_institution_acquisition, True)
        self.assertIs(record.export_licence_present, False)
        self.assertEqual(record.catalogue_reference, "Catalogue, 1928 edn, no. 2")
        self.assertIs(record.owner_stated_in_catalogue, True)

    def test_restitution_recipient_type_arrives_on_a_restitution_event(self):
        record, _ = self.load_one(
            wrap(
                event(
                    term="Restitution",
                    concept="lido00724",
                    note="PROVENA_EXT:restitution_recipient_type=state_or_institution",
                )
            )
        )
        self.assertEqual(
            record.restitution_recipient_type,
            RestitutionRecipientType.STATE_OR_INSTITUTION,
        )

    def test_the_convention_overrides_the_native_mapping(self):
        # The explicit statement wins over a derivation, every time.
        record, _ = self.load_one(
            wrap(
                event(
                    term="Purchase",
                    concept="lido01016",
                    note="PROVENA_EXT:transaction_state=zwangsverkauf",
                )
            )
        )
        self.assertEqual(record.transaction_state, TransactionState.ZWANGSVERKAUF)

    def test_an_unrecognised_field_name_is_rejected(self):
        # Same posture as an unrecognised CSV column: a misspelling would
        # otherwise silently drop the data it carries.
        problems = self.expect_rejection(
            wrap(event(note="PROVENA_EXT:transaction_typ=purchase"))
        )
        self.assertIn("transaction_typ", problems)
        self.assertIn("not an internal schema field", problems)

    def test_a_malformed_line_is_rejected(self):
        problems = self.expect_rejection(wrap(event(note="PROVENA_EXT:purchase")))
        self.assertIn("field_name=value", problems)

    def test_prose_around_the_convention_survives_as_notes(self):
        record, _ = self.load_one(
            wrap(
                event(
                    note="Consignor not named in the catalogue.\n"
                    "PROVENA_EXT:transaction_state=zwangsverkauf"
                )
            )
        )
        self.assertEqual(record.notes, "Consignor not named in the catalogue.")
        self.assertNotIn("PROVENA_EXT", record.notes)


class DatePrecisionTests(AdapterTestCase):
    def test_precision_is_derived_from_the_granularity_supplied(self):
        for earliest, latest, expected in (
            ("1921", "1921", DatePrecision.YEAR),
            ("1934-06", "1934-06", DatePrecision.MONTH),
            ("1936-11-04", "1936-11-04", DatePrecision.EXACT),
        ):
            with self.subTest(earliest=earliest):
                record, _ = self.load_one(wrap(event(dates=dates(earliest, latest))))
                self.assertEqual(record.date_span.precision, expected)

    def test_a_fully_expanded_year_does_not_become_a_day_precise_date(self):
        # LIDO practice routinely writes a year as 1 Jan - 31 Dec. Read
        # literally that is two day-precise dates, which is exactly the false
        # precision the schema exists to prevent.
        record, _ = self.load_one(wrap(event(dates=dates("1921-01-01", "1921-12-31"))))
        self.assertEqual(record.date_span.precision, DatePrecision.YEAR)
        self.assertEqual(record.date_from, "1921")

    def test_a_fully_expanded_month_becomes_a_month(self):
        record, _ = self.load_one(wrap(event(dates=dates("1934-06-01", "1934-06-30"))))
        self.assertEqual(record.date_span.precision, DatePrecision.MONTH)
        self.assertEqual(record.date_from, "1934-06")

    def test_a_genuine_part_year_span_keeps_its_days(self):
        record, _ = self.load_one(wrap(event(dates=dates("1938-03-12", "1938-09-14"))))
        self.assertEqual(record.date_span.precision, DatePrecision.EXACT)
        self.assertEqual((record.date_from, record.date_to),
                         ("1938-03-12", "1938-09-14"))

    def test_the_coarser_end_of_a_span_sets_its_precision(self):
        record, _ = self.load_one(wrap(event(dates=dates("1935", "1936-11-04"))))
        self.assertEqual(record.date_span.precision, DatePrecision.YEAR)

    def test_circa_can_only_come_from_the_convention(self):
        record, _ = self.load_one(
            wrap(
                event(
                    dates=dates("1938", "1938", display="um 1938"),
                    note="PROVENA_EXT:date_precision=circa",
                )
            )
        )
        self.assertEqual(record.date_span.precision, DatePrecision.CIRCA)
        self.assertTrue(record.date_span.is_imprecise)

    def test_a_display_date_is_never_parsed_for_precision(self):
        # "um 1938" says circa in German. Reading it is the free-text
        # inference this project rejects, so the date stays year-precise.
        record, _ = self.load_one(
            wrap(event(dates=dates("1938", "1938", display="um 1938")))
        )
        self.assertEqual(record.date_span.precision, DatePrecision.YEAR)

    def test_a_display_date_with_no_parsable_date_is_reported(self):
        record, ingest = self.load_one(
            wrap(event(dates=dates(display="vor dem Krieg")))
        )
        self.assertIsNone(record.date_span)
        self.assertEqual([n.kind for n in ingest.notes], ["display_date_not_parsed"])

    def test_a_time_component_is_dropped(self):
        record, _ = self.load_one(
            wrap(event(dates=dates("1936-11-04T00:00:00", "1936-11-04T00:00:00")))
        )
        self.assertEqual(record.date_from, "1936-11-04")

    def test_a_date_the_adapter_will_not_parse_is_rejected_by_name(self):
        problems = self.expect_rejection(wrap(event(dates=dates("circa 1938"))))
        self.assertIn("earliestDate", problems)
        self.assertIn("circa 1938", problems)


class ActorTests(AdapterTestCase):
    def test_a_single_actor_is_the_record_s_owner(self):
        record, _ = self.load_one(wrap(event(actors=actor("Cordes, H."))))
        self.assertEqual(record.owner_name, "Cordes, H.")

    def test_name_variants_come_from_the_remaining_appellations(self):
        record, _ = self.load_one(
            wrap(event(actors=actor("Almqvist, H.", "Almquist, H.", pref=True)))
        )
        self.assertEqual(record.owner_name, "Almqvist, H.")
        self.assertEqual(record.owner_name_variants, ["Almquist, H."])

    def test_the_receiving_party_is_chosen_from_several_actors(self):
        record, _ = self.load_one(
            wrap(
                event(
                    actors=actor("Almqvist, H.", role="seller")
                    + actor("Auktionshaus Steinbach", role="new owner")
                )
            )
        )
        self.assertEqual(record.owner_name, "Auktionshaus Steinbach")

    def test_an_ambiguous_actor_set_is_rejected_rather_than_picked(self):
        problems = self.expect_rejection(
            wrap(event(actors=actor("One, A.") + actor("Two, B.")))
        )
        self.assertIn("receiving party", problems)

    def test_two_receiving_actors_are_also_ambiguous(self):
        problems = self.expect_rejection(
            wrap(event(actors=actor("One, A.", role="owner")
                       + actor("Two, B.", role="buyer")))
        )
        self.assertIn("receiving party", problems)

    def test_an_event_naming_only_a_seller_is_rejected(self):
        # Entering the seller as the record's owner would name the wrong
        # party, silently — the failure mode this project treats as worse than
        # a rejected file.
        problems = self.expect_rejection(
            wrap(event(actors=actor("Almqvist, H.", role="seller")))
        )
        self.assertIn("parted with the object", problems)

    def test_roles_match_across_language_and_accent(self):
        record, _ = self.load_one(
            wrap(
                event(
                    actors=actor("Verkäufer, V.", role="Verkäufer")
                    + actor("Käufer, K.", role="Käufer")
                )
            )
        )
        self.assertEqual(record.owner_name, "Käufer, K.")

    def test_an_event_with_no_named_actor_is_rejected(self):
        problems = self.expect_rejection(wrap(event(actors="")))
        self.assertIn("owner_name is required", problems)

    def test_the_owner_can_be_stated_through_the_convention(self):
        record, _ = self.load_one(
            wrap(
                event(
                    actors=actor("Almqvist, H.", role="owner") + actor("Two, B.", role="owner"),
                    note="PROVENA_EXT:owner_name=Almqvist, H.",
                )
            )
        )
        self.assertEqual(record.owner_name, "Almqvist, H.")


class PlaceAndSourceTests(AdapterTestCase):
    def test_a_place_hierarchy_flattens_innermost_first(self):
        record, _ = self.load_one(wrap(event(place=place("Munich", "Germany"))))
        self.assertEqual(record.location, "Munich, Germany")
        # And the schema's country convention then reads the right end of it.
        self.assertEqual(record.country, "Germany")

    def test_a_deeper_hierarchy_keeps_its_order(self):
        record, _ = self.load_one(
            wrap(event(place=place("Schwabing", "Munich", "Bavaria", "Germany")))
        )
        self.assertEqual(record.location, "Schwabing, Munich, Bavaria, Germany")
        self.assertEqual(record.country, "Germany")

    def test_a_second_event_place_is_reported_not_merged(self):
        two = place("Munich", "Germany") + place("Bern", "Switzerland")
        record, ingest = self.load_one(wrap(event(place=two)))
        self.assertEqual(record.location, "Munich, Germany")
        self.assertEqual([n.kind for n in ingest.notes], ["multiple_event_places"])

    def test_the_source_citation_comes_from_sourceDescriptiveNote(self):
        record, _ = self.load_one(
            wrap(event(citation="Dealer stockbook 1921, fol. 44"))
        )
        self.assertEqual(record.source_citation, "Dealer stockbook 1921, fol. 44")

    def test_a_missing_citation_reaches_the_documentation_axis(self):
        ingest = self.load(wrap(event(dates=dates("1936", "1936"))))
        result = screen_object(group_into_chains(ingest.records)[0], build_config())
        self.assertIn(
            "DQ-001", {f["rule_id"] for f in result["documentation_quality_flags"]}
        )


class StructureTests(AdapterTestCase):
    def test_object_id_falls_back_to_the_administrative_record_id(self):
        xml = wrap(event()).replace(
            '<lido:lidoRecID lido:type="local">OBJ-1</lido:lidoRecID>', ""
        ).replace(
            "</lido:descriptiveMetadata>",
            "</lido:descriptiveMetadata><lido:administrativeMetadata>"
            "<lido:recordWrap><lido:recordID lido:type=\"local\">OBJ-9</lido:recordID>"
            "</lido:recordWrap></lido:administrativeMetadata>",
        )
        record, _ = self.load_one(xml)
        self.assertEqual(record.object_id, "OBJ-9")

    def test_an_object_with_no_identifier_is_rejected(self):
        xml = wrap(event()).replace(
            '<lido:lidoRecID lido:type="local">OBJ-1</lido:lidoRecID>', ""
        )
        self.assertIn("object_id is required", self.expect_rejection(xml))

    def test_an_object_with_no_events_is_rejected(self):
        self.assertIn("no descriptiveMetadata/eventWrap", self.expect_rejection(wrap("")))

    def test_malformed_xml_is_rejected_as_such(self):
        self.assertIn("not well-formed XML", self.expect_rejection("<lido:lidoWrap>"))

    def test_a_file_that_is_not_lido_is_rejected(self):
        self.assertIn("no lido:lido elements", self.expect_rejection("<records/>"))

    def test_the_namespace_prefix_does_not_matter(self):
        # Matching is on local names, so a file using a different prefix — or
        # none at all — reads identically.
        renamed = wrap(event()).replace("lido:", "l:").replace("xmlns:l", "xmlns:l")
        renamed = renamed.replace(f"<l:lidoWrap {NS}>", '<l:lidoWrap xmlns:l="http://www.lido-schema.org">')
        record, _ = self.load_one(renamed)
        self.assertEqual(record.object_id, "OBJ-1")

    def test_every_problem_in_a_file_is_reported_at_once(self):
        xml = wrap(
            event(note="PROVENA_EXT:nonsense=1")
            + event(dates=dates("not a date"))
            + event(actors="")
        )
        problems = self.expect_rejection(xml)
        self.assertIn("nonsense", problems)
        self.assertIn("not a date", problems)
        self.assertIn("owner_name is required", problems)

    def test_source_refs_name_the_object_and_the_event_position(self):
        ingest = self.load(wrap(event() + event()))
        self.assertTrue(ingest.records[1].source_ref.endswith("OBJ-1 event 2"))


class PipelineDispatchTests(unittest.TestCase):
    def test_the_format_is_inferred_from_the_extension(self):
        self.assertEqual(pipeline.detect_format("a/b.xml"), pipeline.FORMAT_LIDO)
        self.assertEqual(pipeline.detect_format("a/b.LIDO"), pipeline.FORMAT_LIDO)
        self.assertEqual(pipeline.detect_format("a/b.csv"), pipeline.FORMAT_CSV)

    def test_an_unknown_extension_is_refused_rather_than_sniffed(self):
        with self.assertRaises(InputValidationError) as caught:
            pipeline.detect_format("a/b.txt")
        self.assertIn("--format", "\n".join(caught.exception.problems))

    def test_both_example_files_run_end_to_end(self):
        for path, expected in (
            ("examples/example_input.xml", pipeline.FORMAT_LIDO),
            ("examples/example_input.csv", pipeline.FORMAT_CSV),
        ):
            with self.subTest(path=path):
                result = pipeline.run(path)
                self.assertEqual(result["input_format"], expected)
                self.assertTrue(result["coverage_map"]["objects_processed"])
                self.assertTrue(result["objects"])

    def test_csv_input_carries_no_ingest_notes(self):
        # Nothing is converted, so there is nothing to report about a
        # conversion. An empty list rather than an absent key, so a consumer
        # can tell "none" from "not recorded".
        result = pipeline.run("examples/example_input.csv")
        self.assertEqual(result["ingest_notes"], [])

    def test_lido_ingest_notes_reach_the_report_structure(self):
        result = pipeline.run("examples/example_input.xml")
        self.assertTrue(result["ingest_notes"])
        note = result["ingest_notes"][0]
        self.assertEqual(
            sorted(note), ["detail", "kind", "source_ref"]
        )

    def test_the_format_flag_overrides_the_extension(self):
        with self.assertRaises(InputValidationError):
            # A CSV read as LIDO fails as XML rather than being silently
            # re-sniffed back to CSV.
            pipeline.run("examples/example_input.csv", input_format=pipeline.FORMAT_LIDO)


if __name__ == "__main__":
    unittest.main()
