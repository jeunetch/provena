"""LIDO-XML input adapter.

One `lido:event` becomes one internal record; the events under one
`lido:lido` form that object's chain. As with the CSV adapter, the whole file
is validated before anything is returned, so a file with problems fails with
a complete list rather than half-loading.

WHAT THIS ADAPTER WILL NOT DO. It never infers a REAO `transaction_state`
from free text, a `displayDate` or an event description. LIDO has no native
term for a forced sale or a confiscation, and reading one out of prose is the
keyword inference the methodology rejects — see
`data/lido_event_type_map.json`, which maps only the terms whose REAO
equivalent is unambiguous and records, with reasons, the ones it deliberately
leaves alone. An unmapped event enters as `unknown` and an ingest note names
the term, so the omission is visible rather than silent. `unknown` is still
screened; nothing is dropped from triage by not being mapped.

THE PROVENA_EXT CONVENTION. Several internal fields have no faithful LIDO
carrier. They are read from `lido:eventDescriptionSet/lido:descriptiveNoteValue`
as lines of the form:

    PROVENA_EXT:field_name=value

This is a documented free-text convention, **not** a standards-compliant LIDO
application profile. A real profile — an XSD extension plus Schematron rules —
is future work this prototype does not claim to have done. The gap is specific
to LIDO input: every one of these fields works natively in CSV and JSON, so no
functionality is blocked by it. Any internal field name is accepted and always
overrides the native mapping; an unrecognised name is a validation error, the
same posture the CSV adapter takes towards an unrecognised column.

The fields with no native carrier at all, and therefore no other route in:
`is_institution_acquisition`, `catalogue_reference`,
`owner_stated_in_catalogue`, `restitution_recipient_type`,
`export_licence_present`. (CLAUDE.md named the first four; the fifth was
overlooked there and is recorded here.) `date_precision` has a native route
for exact/month/year and needs the convention only for circa/before/after,
which an ISO date string cannot express.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from .reao_taxonomy import TransactionState
from .schema import (
    DEFAULT_CIRCA_MARGIN_YEARS,
    FIELD_NAMES,
    InputValidationError,
    ObjectChain,
    ProvenanceRecord,
    RecordValidationError,
    build_record,
    group_into_chains,
)

DEFAULT_EVENT_TYPE_MAP_PATH = (
    Path(__file__).resolve().parent.parent / "data/lido_event_type_map.json"
)

EXT_PREFIX = "PROVENA_EXT:"

# Roles naming the party who holds the object AFTER the event — the party an
# internal record is about. A transfer's other side is not lost: in a
# well-formed chain the transferring party is the owner of the preceding
# event.
RECEIVING_ROLES = frozenset(
    {
        "owner", "new owner", "buyer", "purchaser", "recipient", "acquirer",
        "consignee", "holder",
        "eigentumer", "eigentuemer", "besitzer", "kaufer", "kaeufer",
        "erwerber", "empfanger", "empfaenger", "neuer eigentumer",
    }
)

# Roles naming the party who parted with the object. An event whose ONLY actor
# holds one of these is rejected rather than recorded: entering a seller as the
# record's owner would name the wrong party, silently.
TRANSFERRING_ROLES = frozenset(
    {
        "seller", "vendor", "previous owner", "former owner", "consignor",
        "donor", "lender",
        "verkaufer", "verkaufer", "vorbesitzer", "einlieferer", "schenker",
        "veräußerer", "verausserer",
    }
)

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


@dataclass(frozen=True)
class IngestNote:
    """Something the adapter could not carry over, stated rather than dropped."""

    source_ref: str
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"source_ref": self.source_ref, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class EventTypeMap:
    """LIDO Event Type -> transaction_state, plus the terms left unmapped."""

    version: str
    review_status: str
    source: str
    by_id: dict[str, TransactionState] = field(default_factory=dict)
    by_term: dict[str, TransactionState] = field(default_factory=dict)
    unmapped_by_id: dict[str, str] = field(default_factory=dict)
    unmapped_by_term: dict[str, str] = field(default_factory=dict)

    def lookup(self, concept_id: str | None, term: str | None):
        """Return (state, reason_if_deliberately_unmapped)."""
        folded = _fold(term) if term else None
        if concept_id and concept_id in self.by_id:
            return self.by_id[concept_id], None
        if folded and folded in self.by_term:
            return self.by_term[folded], None
        if concept_id and concept_id in self.unmapped_by_id:
            return None, self.unmapped_by_id[concept_id]
        if folded and folded in self.unmapped_by_term:
            return None, self.unmapped_by_term[folded]
        return None, None


def load_event_type_map(
    path: str | Path = DEFAULT_EVENT_TYPE_MAP_PATH,
) -> EventTypeMap:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    by_id: dict[str, TransactionState] = {}
    by_term: dict[str, TransactionState] = {}
    for entry in raw["mapped"]:
        # Parsed through the enum, so a typo in the data file fails at load
        # rather than producing a record with a state nothing recognises.
        state = TransactionState(entry["transaction_state"])
        if entry.get("lido_id"):
            by_id[entry["lido_id"]] = state
        by_term[_fold(entry["term"])] = state
    unmapped_by_id = {
        e["lido_id"]: e["reason"] for e in raw["deliberately_unmapped"] if e.get("lido_id")
    }
    unmapped_by_term = {
        _fold(e["term"]): e["reason"] for e in raw["deliberately_unmapped"]
    }
    return EventTypeMap(
        version=raw["version"],
        review_status=raw["review_status"],
        source=raw["source"],
        by_id=by_id,
        by_term=by_term,
        unmapped_by_id=unmapped_by_id,
        unmapped_by_term=unmapped_by_term,
    )


def _fold(text: str) -> str:
    """Casefold and strip accents, so 'Käufer' and 'Kaeufer' meet."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _local(tag: str) -> str:
    """Element local name. Matching ignores the namespace prefix and URI, so a
    file using a different prefix — or none — reads identically."""
    return tag.rsplit("}", 1)[-1]


def _kids(element, name: str) -> list:
    return [child for child in element if _local(child.tag) == name]


def _kid(element, name: str):
    found = _kids(element, name)
    return found[0] if found else None


def _descend(element, *names):
    """First element down a chain of local names, or None."""
    current = element
    for name in names:
        if current is None:
            return None
        current = _kid(current, name)
    return current


def _text(element) -> str | None:
    if element is None:
        return None
    return (element.text or "").strip() or None


def _attr(element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _appellations(holder) -> list[str]:
    """Appellation values under a holder, preferred ones first.

    LIDO marks a preferred name with lido:pref="preferred"; where nothing is
    marked, document order stands.
    """
    preferred: list[str] = []
    others: list[str] = []
    for wrapper in holder:
        for value in _kids(wrapper, "appellationValue"):
            text = _text(value)
            if not text:
                continue
            if (_attr(value, "pref") or "").lower() == "preferred":
                preferred.append(text)
            else:
                others.append(text)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in preferred + others:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _place_text(place) -> str | None:
    """Flatten a place and its partOfPlace ancestors, innermost first.

    "Munich" nested inside "Germany" yields "Munich, Germany", which is also
    what the internal schema's country convention (final comma-separated
    component) expects.
    """
    parts: list[str] = []
    current = place
    while current is not None:
        names = _appellations(_kids(current, "namePlaceSet"))
        if names:
            parts.append(names[0])
        current = _kid(current, "partOfPlace")
    return ", ".join(parts) or None


def _normalise_date(raw: str) -> str:
    """A LIDO date string reduced to YYYY, YYYY-MM or YYYY-MM-DD.

    A time component is dropped — the internal schema has no time — and
    anything that is not a plain ISO calendar date is rejected rather than
    guessed at.
    """
    value = raw.strip()
    if "T" in value:
        value = value.split("T", 1)[0]
    if not _DATE_RE.match(value):
        raise ValueError(
            f"date {raw!r} is not YYYY, YYYY-MM or YYYY-MM-DD; the adapter does "
            "not interpret free-form or non-Gregorian date text"
        )
    return value


def _coarsen(earliest: str | None, latest: str | None) -> tuple[str | None, str | None]:
    """Collapse a fully-expanded span back to the precision it actually has.

    LIDO practice commonly expands a year to 1921-01-01/1921-12-31 and a month
    to 1934-06-01/1934-06-30. Read literally those are day-precise dates, which
    is exactly the false precision the schema exists to prevent. A span that
    covers a whole calendar year or a whole calendar month is therefore
    rewritten to that year or month. The interval is unchanged; only the
    claimed precision is, and only ever downwards.
    """
    if not earliest or not latest:
        return earliest, latest
    start = _DATE_RE.match(earliest)
    end = _DATE_RE.match(latest)
    if not start or not end:
        return earliest, latest
    y1, m1, d1 = start.groups()
    y2, m2, d2 = end.groups()
    if not (d1 and d2 and m1 and m2):
        return earliest, latest
    if y1 == y2 and m1 == m2 and d1 == "01":
        if int(d2) == calendar.monthrange(int(y1), int(m1))[1]:
            return f"{y1}-{m1}", f"{y2}-{m2}"
    if y1 == y2 and (m1, d1) == ("01", "01") and (m2, d2) == ("12", "31"):
        return y1, y2
    return earliest, latest


def _granularity(value: str) -> str:
    parts = value.count("-")
    return "exact" if parts == 2 else ("month" if parts == 1 else "year")


def _parse_extensions(text: str, source_ref: str) -> tuple[dict[str, str], list[str]]:
    """Pull PROVENA_EXT lines out of a descriptive note.

    Returns the extension values and the note's remaining prose.
    """
    values: dict[str, str] = {}
    prose: list[str] = []
    problems: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(EXT_PREFIX):
            prose.append(line)
            continue
        body = stripped[len(EXT_PREFIX) :]
        if "=" not in body:
            problems.append(
                f"{stripped!r} is not of the form {EXT_PREFIX}field_name=value"
            )
            continue
        name, _, value = body.partition("=")
        name = name.strip()
        if name not in FIELD_NAMES:
            problems.append(
                f"{EXT_PREFIX}{name} is not an internal schema field. "
                f"Permitted: {', '.join(FIELD_NAMES)}"
            )
            continue
        values[name] = value.strip()
    if problems:
        raise RecordValidationError(source_ref, problems)
    return values, prose


def _owner_from_event(event, source_ref: str) -> tuple[str, list[str]]:
    """The name of the party this record is about, plus its variants.

    Where an event carries several actors the receiving one is chosen by its
    roleActor term. Ambiguity is an error, never a pick: naming the wrong
    party in a provenance record is the kind of silent mistake this project
    treats as worse than a rejected file.
    """
    candidates: list[tuple[str | None, list[str]]] = []
    for actor_wrapper in _kids(event, "eventActor"):
        in_role = _kid(actor_wrapper, "actorInRole")
        if in_role is None:
            continue
        actor = _kid(in_role, "actor")
        if actor is None:
            continue
        names = _appellations(_kids(actor, "nameActorSet"))
        if not names:
            continue
        role = None
        role_element = _kid(in_role, "roleActor")
        if role_element is not None:
            role = _text(_kid(role_element, "term"))
        candidates.append((role, names))

    if not candidates:
        raise RecordValidationError(
            source_ref,
            [
                "event names no actor with a nameActorSet/appellationValue; "
                "owner_name is required"
            ],
        )

    if len(candidates) == 1:
        role, names = candidates[0]
        if role and _fold(role) in TRANSFERRING_ROLES:
            raise RecordValidationError(
                source_ref,
                [
                    f"the event's only actor is recorded in the role {role!r}, which "
                    "names the party that parted with the object. An internal record "
                    "names the party holding it after the event; entering this actor "
                    "would name the wrong party. Add the receiving actor, or state the "
                    "owner with " + EXT_PREFIX + "owner_name=..."
                ],
            )
        return names[0], names[1:]

    receiving = [c for c in candidates if c[0] and _fold(c[0]) in RECEIVING_ROLES]
    if len(receiving) == 1:
        role, names = receiving[0]
        return names[0], names[1:]

    roles = ", ".join(repr(role) if role else "(no roleActor)" for role, _ in candidates)
    reason = "none" if not receiving else f"{len(receiving)}"
    raise RecordValidationError(
        source_ref,
        [
            f"event carries {len(candidates)} actors and {reason} of them is "
            f"identifiable as the receiving party (roles: {roles}). Give the "
            "receiving actor a roleActor term such as 'owner', or state the owner "
            "with " + EXT_PREFIX + "owner_name=..."
        ],
    )


def _event_to_raw(
    event,
    object_fields: dict[str, str | None],
    source_ref: str,
    event_map: EventTypeMap,
    notes: list[IngestNote],
) -> dict[str, str | None]:
    """One lido:event flattened to the internal schema's raw row shape."""
    extensions: dict[str, str] = {}
    prose_parts: list[str] = []
    citation: str | None = None
    for description in _kids(event, "eventDescriptionSet"):
        for value in _kids(description, "descriptiveNoteValue"):
            text = value.text or ""
            found, prose = _parse_extensions(text, source_ref)
            extensions.update(found)
            prose_parts.extend(p for p in prose if p.strip())
        if citation is None:
            citation = _text(_kid(description, "sourceDescriptiveNote"))

    # The convention is consulted first, not as a fallback: where the record
    # states the owner outright there is no ambiguity left to reject, and the
    # error raised below tells a user to do exactly this.
    if extensions.get("owner_name"):
        owner_name, variants = extensions["owner_name"], []
    else:
        owner_name, variants = _owner_from_event(event, source_ref)

    date_from = date_to = precision = None
    date_element = _descend(event, "eventDate", "date")
    if date_element is not None:
        earliest = _text(_kid(date_element, "earliestDate"))
        latest = _text(_kid(date_element, "latestDate"))
        problems: list[str] = []
        for raw, label in ((earliest, "earliestDate"), (latest, "latestDate")):
            if raw is None:
                continue
            try:
                _normalise_date(raw)
            except ValueError as exc:
                problems.append(f"{label}: {exc}")
        if problems:
            raise RecordValidationError(source_ref, problems)
        earliest = _normalise_date(earliest) if earliest else None
        latest = _normalise_date(latest) if latest else None
        date_from, date_to = _coarsen(earliest, latest)
        stated = [_granularity(v) for v in (date_from, date_to) if v]
        if stated:
            # The coarsest component wins: a span is only as precise as its
            # vaguer end.
            order = ("year", "month", "exact")
            precision = min(stated, key=order.index)
        if date_from and date_to and date_from == date_to:
            date_to = None

    if _kid(event, "eventDate") is not None and date_element is None:
        display = _text(_kid(_kid(event, "eventDate"), "displayDate"))
        if display:
            notes.append(
                IngestNote(
                    source_ref,
                    "display_date_not_parsed",
                    f"eventDate carries only the free-text displayDate {display!r} and "
                    "no earliestDate/latestDate. The adapter does not parse date prose "
                    "— reading 'circa' or 'before' out of it is the inference this "
                    "project rejects. The record is treated as undated; supply "
                    f"{EXT_PREFIX}date_from / {EXT_PREFIX}date_precision to date it.",
                )
            )

    state: str | None = None
    event_type = _kid(event, "eventType")
    if event_type is not None:
        concept_ids = [_text(c) for c in _kids(event_type, "conceptID")]
        local_ids = [c.rstrip("/").rsplit("/", 1)[-1] for c in concept_ids if c]
        term = _text(_kid(event_type, "term"))
        mapped, unmapped_reason = None, None
        for local_id in local_ids or [None]:
            mapped, unmapped_reason = event_map.lookup(local_id, term)
            if mapped or unmapped_reason:
                break
        if mapped is not None:
            state = mapped.value
        elif "transaction_state" in extensions:
            # The record said which REAO state applies, which is exactly what
            # an unmapped term is supposed to prompt. Nothing to report.
            pass
        else:
            label = term or (local_ids[0] if local_ids else "(no term)")
            detail = (
                f"LIDO eventType {label!r} was not mapped to a REAO transaction_state. "
                + (
                    unmapped_reason
                    or "It is not in data/lido_event_type_map.json; the adapter does "
                    "not guess a REAO state from a term it does not know."
                )
                + f" The record is screened as 'unknown'. State it explicitly with "
                f"{EXT_PREFIX}transaction_state=... if the record supports one."
            )
            notes.append(IngestNote(source_ref, "event_type_not_mapped", detail))

    place = _descend(event, "eventPlace", "place")
    places = _kids(event, "eventPlace")
    if len(places) > 1:
        notes.append(
            IngestNote(
                source_ref,
                "multiple_event_places",
                f"event carries {len(places)} eventPlace elements; the first was used "
                "for `location`. The internal schema records one place per event.",
            )
        )

    row: dict[str, str | None] = {
        "object_id": object_fields["object_id"],
        "object_title": object_fields["object_title"],
        "object_class": object_fields["object_class"],
        "owner_name": owner_name,
        "owner_name_variants": "|".join(variants) or None,
        "date_from": date_from,
        "date_to": date_to,
        "date_precision": precision,
        "transaction_state": state,
        "location": _place_text(place) if place is not None else None,
        "source_citation": citation,
        "export_licence_present": None,
        "is_institution_acquisition": None,
        "catalogue_reference": None,
        "owner_stated_in_catalogue": None,
        "restitution_recipient_type": None,
        "notes": "\n".join(prose_parts).strip() or None,
    }
    # The convention always wins over the native mapping: it is the explicit
    # statement, and the native value is at best a derivation.
    row.update({name: (value or None) for name, value in extensions.items()})
    return row


def _object_fields(lido_record, source_ref: str) -> dict[str, str | None]:
    object_id = _text(_kid(lido_record, "lidoRecID"))
    if not object_id:
        object_id = _text(
            _descend(lido_record, "administrativeMetadata", "recordWrap", "recordID")
        )
    if not object_id:
        raise RecordValidationError(
            source_ref,
            [
                "no lido:lidoRecID and no administrativeMetadata/recordWrap/recordID; "
                "object_id is required"
            ],
        )

    descriptive = _kid(lido_record, "descriptiveMetadata")
    title = None
    class_term = None
    if descriptive is not None:
        title_wrap = _descend(descriptive, "objectIdentificationWrap", "titleWrap")
        if title_wrap is not None:
            names = _appellations(_kids(title_wrap, "titleSet"))
            title = names[0] if names else None
        work_type_wrap = _descend(
            descriptive, "objectClassificationWrap", "objectWorkTypeWrap"
        )
        if work_type_wrap is not None:
            work_type = _kid(work_type_wrap, "objectWorkType")
            if work_type is not None:
                class_term = _text(_kid(work_type, "term"))

    return {"object_id": object_id, "object_title": title, "object_class": class_term}


@dataclass(frozen=True)
class LidoIngest:
    records: list[ProvenanceRecord]
    notes: list[IngestNote]
    event_type_map_version: str


def load(
    path: str | Path,
    circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS,
    event_type_map_path: str | Path = DEFAULT_EVENT_TYPE_MAP_PATH,
) -> LidoIngest:
    """Read and validate a LIDO-XML file. Raises InputValidationError."""
    path = Path(path)
    event_map = load_event_type_map(event_type_map_path)
    try:
        tree = ElementTree.parse(path)
    except ElementTree.ParseError as exc:
        raise InputValidationError([f"{path}: not well-formed XML: {exc}"]) from None

    root = tree.getroot()
    lido_records = (
        [root] if _local(root.tag) == "lido" else _kids(root, "lido")
    )
    if not lido_records:
        raise InputValidationError(
            [
                f"{path}: no lido:lido elements found (root is {_local(root.tag)!r}). "
                "Expected a lidoWrap containing one lido element per object."
            ]
        )

    problems: list[str] = []
    notes: list[IngestNote] = []
    records: list[ProvenanceRecord] = []
    order = 0

    for index, lido_record in enumerate(lido_records, start=1):
        record_ref = f"{path.name}:lido[{index}]"
        try:
            fields = _object_fields(lido_record, record_ref)
        except RecordValidationError as exc:
            problems.extend(f"{exc.source_ref}: {p}" for p in exc.problems)
            continue

        descriptive = _kid(lido_record, "descriptiveMetadata")
        events = []
        if descriptive is not None:
            for wrap in _kids(descriptive, "eventWrap"):
                for event_set in _kids(wrap, "eventSet"):
                    event = _kid(event_set, "event")
                    if event is not None:
                        events.append(event)
        if not events:
            problems.append(
                f"{record_ref}: object {fields['object_id']!r} contains no "
                "descriptiveMetadata/eventWrap/eventSet/event; there is nothing to "
                "screen. An object with no recorded events cannot form a chain."
            )
            continue

        for position, event in enumerate(events, start=1):
            source_ref = f"{path.name}:{fields['object_id']} event {position}"
            try:
                raw = _event_to_raw(event, fields, source_ref, event_map, notes)
                records.append(
                    build_record(raw, source_ref, order, circa_margin_years)
                )
            except RecordValidationError as exc:
                problems.extend(f"{exc.source_ref}: {p}" for p in exc.problems)
            order += 1

    if problems:
        raise InputValidationError(problems)
    if not records:
        raise InputValidationError([f"{path}: no events"])
    return LidoIngest(
        records=records, notes=notes, event_type_map_version=event_map.version
    )


def load_records(
    path: str | Path, circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS
) -> list[ProvenanceRecord]:
    return load(path, circa_margin_years).records


def load_chains(
    path: str | Path, circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS
) -> list[ObjectChain]:
    return group_into_chains(load_records(path, circa_margin_years))
