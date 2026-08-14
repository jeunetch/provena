"""Internal provenance schema, input validation, and date-precision handling.

The date handling here exists to prevent one specific failure mode: treating
an imprecise date as if it were exact. Every date comparison in the heuristic
layer goes through `DateSpan`, which carries its own precision and reports
whether an overlap is *certain* or merely *possible*. A comparison involving
a `circa`, `before` or `after` date can never come back as certain, and no
part of this module computes a numeric gap length between two imprecise
dates.

Validation fails loudly. A malformed row raises rather than degrading to a
default, because a silently mis-parsed date is worse than a rejected file.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Mapping

from .reao_taxonomy import (
    RestitutionRecipientType,
    TransactionState,
    parse_restitution_recipient_type,
    parse_transaction_state,
)

# Widening applied to a `circa` date purely so that overlap questions can be
# asked of it. It is never used to compute or report an interval length.
#
# PLACEHOLDER. This figure has no sourced basis and is not a validated
# field-practice convention — it is a default pending expert input. Override
# it per run (`--circa-margin-years`) rather than treating it as settled.
DEFAULT_CIRCA_MARGIN_YEARS = 5

CIRCA_MARGIN_NOTE = (
    "The `circa` margin is a placeholder with no sourced basis, not a validated "
    "field-practice figure. It widens a circa date only so that overlap "
    "questions can be asked of it; it is never used to compute or report an "
    "interval length, and a widened date can never yield a 'certain' overlap."
)

# The chain-level threshold for rule PC-001. Note this is a coverage question
# ("is there any pre-1945 documentation at all?"), distinct from the
# persecution windows in data/persecution_onset_table.json.
PRE_1945_THRESHOLD = date(1945, 1, 1)


class DatePrecision(str, Enum):
    EXACT = "exact"
    MONTH = "month"
    YEAR = "year"
    CIRCA = "circa"
    BEFORE = "before"
    AFTER = "after"


PRECISE_PRECISIONS = frozenset(
    {DatePrecision.EXACT, DatePrecision.MONTH, DatePrecision.YEAR}
)
IMPRECISE_PRECISIONS = frozenset(
    {DatePrecision.CIRCA, DatePrecision.BEFORE, DatePrecision.AFTER}
)

CERTAIN = "certain"
POSSIBLE = "possible"

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")


class InputValidationError(Exception):
    """Raised with the full list of problems found in an input file."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        joined = "\n  ".join(problems)
        super().__init__(f"{len(problems)} problem(s) in input:\n  {joined}")


class RecordValidationError(Exception):
    """Raised for a single record; collected by the adapter."""

    def __init__(self, source_ref: str, problems: list[str]):
        self.source_ref = source_ref
        self.problems = problems
        super().__init__("; ".join(problems))


def _parse_partial(text: str) -> tuple[int, int | None, int | None]:
    """Parse YYYY, YYYY-MM or YYYY-MM-DD into its stated components."""
    match = _DATE_RE.match(text.strip())
    if not match:
        raise ValueError(f"date {text!r} is not YYYY, YYYY-MM or YYYY-MM-DD")
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None
    if month is not None and not 1 <= month <= 12:
        raise ValueError(f"date {text!r} has month out of range")
    if day is not None:
        last = calendar.monthrange(year, month)[1]
        if not 1 <= day <= last:
            raise ValueError(f"date {text!r} has day out of range for that month")
    return year, month, day


def _granularity(parts: tuple[int, int | None, int | None]) -> DatePrecision:
    _, month, day = parts
    if day is not None:
        return DatePrecision.EXACT
    if month is not None:
        return DatePrecision.MONTH
    return DatePrecision.YEAR


def _start_of(parts: tuple[int, int | None, int | None]) -> date:
    year, month, day = parts
    return date(year, month or 1, day or 1)


def _end_of(parts: tuple[int, int | None, int | None]) -> date:
    year, month, day = parts
    if day is not None:
        return date(year, month, day)
    if month is not None:
        return date(year, month, calendar.monthrange(year, month)[1])
    return date(year, 12, 31)


def _shift_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 29 February
        return value.replace(year=value.year + years, day=28)


def _intervals_overlap(
    a_start: date | None,
    a_end: date | None,
    b_start: date | None,
    b_end: date | None,
) -> bool:
    """Overlap test where None means unbounded on that side."""
    if a_end is not None and b_start is not None and a_end < b_start:
        return False
    if a_start is not None and b_end is not None and a_start > b_end:
        return False
    return True


@dataclass(frozen=True)
class DateSpan:
    """A dated event as an interval, with the precision that produced it.

    `nominal_*` is the interval as literally stated. `earliest`/`latest` is
    the interval after accounting for the stated imprecision — widened for
    `circa`, left open on one side for `before`/`after`. None means unbounded.
    """

    precision: DatePrecision
    nominal_earliest: date | None
    nominal_latest: date | None
    earliest: date | None
    latest: date | None
    raw_from: str | None
    raw_to: str | None

    @property
    def is_imprecise(self) -> bool:
        return self.precision in IMPRECISE_PRECISIONS

    @property
    def is_fully_bounded(self) -> bool:
        return self.earliest is not None and self.latest is not None

    def overlap_certainty(self, window_start: date, window_end: date) -> str | None:
        """None if no overlap is possible, else CERTAIN or POSSIBLE.

        Only a precise, fully bounded span can produce CERTAIN. An imprecise
        span that appears to sit inside the window still only yields POSSIBLE,
        because its true date may lie outside it.
        """
        if not _intervals_overlap(self.earliest, self.latest, window_start, window_end):
            return None
        if (
            self.precision in PRECISE_PRECISIONS
            and self.nominal_earliest is not None
            and self.nominal_latest is not None
            and _intervals_overlap(
                self.nominal_earliest, self.nominal_latest, window_start, window_end
            )
        ):
            return CERTAIN
        return POSSIBLE

    def clipped_to(
        self, window_start: date, window_end: date, nominal: bool = False
    ) -> tuple[date, date] | None:
        """The part of this span lying inside a bounded window, or None."""
        start = self.nominal_earliest if nominal else self.earliest
        end = self.nominal_latest if nominal else self.latest
        if not _intervals_overlap(start, end, window_start, window_end):
            return None
        low = window_start if start is None else max(start, window_start)
        high = window_end if end is None else min(end, window_end)
        if low > high:
            return None
        return low, high

    @property
    def certainly_before(self) -> date | None:
        """The date this span is certainly wholly earlier than, if any."""
        if self.precision in PRECISE_PRECISIONS and self.latest is not None:
            return self.latest
        return None

    def describe(self) -> str:
        raw = self.raw_from or ""
        if self.raw_to and self.raw_to != self.raw_from:
            raw = f"{raw}–{self.raw_to}" if raw else self.raw_to
        return f"{raw} ({self.precision.value})"


def build_date_span(
    date_from: str | None,
    date_to: str | None,
    precision_text: str | None,
    circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS,
) -> DateSpan | None:
    """Build a DateSpan, or None when the record carries no date at all.

    Raises ValueError with a message suitable for the validation report.
    """
    raw_from = (date_from or "").strip() or None
    raw_to = (date_to or "").strip() or None
    raw_precision = (precision_text or "").strip().lower() or None

    if raw_from is None and raw_to is None:
        if raw_precision:
            raise ValueError(
                "date_precision is set but neither date_from nor date_to is"
            )
        return None

    if raw_precision is None:
        raise ValueError("date_precision is required whenever a date is given")
    try:
        precision = DatePrecision(raw_precision)
    except ValueError:
        permitted = ", ".join(p.value for p in DatePrecision)
        raise ValueError(
            f"unrecognised date_precision {raw_precision!r}; permitted: {permitted}"
        ) from None

    parts_from = _parse_partial(raw_from) if raw_from else None
    parts_to = _parse_partial(raw_to) if raw_to else None

    # A stated precision finer than the date actually carries is the
    # false-precision failure this module exists to prevent.
    if precision in PRECISE_PRECISIONS:
        for parts, label in ((parts_from, "date_from"), (parts_to, "date_to")):
            if parts is None:
                continue
            stated = _granularity(parts)
            if precision is DatePrecision.EXACT and stated is not DatePrecision.EXACT:
                raise ValueError(
                    f"date_precision 'exact' but {label} gives no day component"
                )
            if precision is DatePrecision.MONTH and stated is DatePrecision.YEAR:
                raise ValueError(
                    f"date_precision 'month' but {label} gives no month component"
                )

    if precision is DatePrecision.BEFORE:
        # The bound is the latest date supplied; the start is open.
        bound = parts_to or parts_from
        nominal_earliest, nominal_latest = None, _end_of(bound)
        earliest, latest = None, nominal_latest
    elif precision is DatePrecision.AFTER:
        bound = parts_from or parts_to
        nominal_earliest, nominal_latest = _start_of(bound), None
        earliest, latest = nominal_earliest, None
    else:
        low_parts = parts_from or parts_to
        high_parts = parts_to or parts_from
        nominal_earliest = _start_of(low_parts)
        nominal_latest = _end_of(high_parts)
        if nominal_earliest > nominal_latest:
            raise ValueError("date_from is later than date_to")
        if precision is DatePrecision.CIRCA:
            earliest = _shift_years(nominal_earliest, -circa_margin_years)
            latest = _shift_years(nominal_latest, circa_margin_years)
        else:
            earliest, latest = nominal_earliest, nominal_latest

    return DateSpan(
        precision=precision,
        nominal_earliest=nominal_earliest,
        nominal_latest=nominal_latest,
        earliest=earliest,
        latest=latest,
        raw_from=raw_from,
        raw_to=raw_to,
    )


def parse_bool(text: str | None, field_name: str) -> bool | None:
    """Parse an optional boolean cell. Empty means 'not recorded', not False.

    The distinction matters: a rule that cannot run because a column is blank
    must report itself as skipped, not as not-triggered.
    """
    if text is None:
        return None
    cleaned = text.strip().lower()
    if not cleaned:
        return None
    if cleaned in {"true", "yes", "y", "1"}:
        return True
    if cleaned in {"false", "no", "n", "0"}:
        return False
    raise ValueError(f"{field_name} must be true/false (or blank), got {text!r}")


def split_variants(text: str | None) -> list[str]:
    """Split the pipe-separated owner_name_variants cell."""
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


@dataclass(frozen=True)
class ProvenanceRecord:
    """One event in an object's ownership chain."""

    object_id: str
    owner_name: str
    source_ref: str
    source_order: int
    object_title: str | None = None
    object_class: str | None = None
    # The object's own creation/production date. Carries its own precision
    # through the same DateSpan machinery as a transfer date, because most of
    # what a museum's object records actually hold is imprecise — "ca. 1905",
    # "1920s", "17th century" — and a field that only accepts exact ISO dates
    # pushes those records back into the unrecorded bucket it exists to shrink.
    object_date: str | None = None
    object_date_to: str | None = None
    object_date_precision: str | None = None
    object_date_span: DateSpan | None = None
    owner_name_variants: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    date_span: DateSpan | None = None
    transaction_state: TransactionState | None = None
    location: str | None = None
    source_citation: str | None = None
    export_licence_present: bool | None = None
    is_institution_acquisition: bool | None = None
    catalogue_reference: str | None = None
    owner_stated_in_catalogue: bool | None = None
    restitution_recipient_type: RestitutionRecipientType | None = None
    notes: str | None = None

    @property
    def is_dated(self) -> bool:
        return self.date_span is not None

    @property
    def country(self) -> str | None:
        """Country taken as the final comma-separated part of `location`.

        A documented convention rather than a hidden guess: "Munich, Germany"
        yields "Germany", and a value with no comma is used whole. Rules that
        rely on it cite `country_derivation` so a reader can see the basis and
        correct the input if the convention does not hold for their records.
        """
        if not self.location:
            return None
        return self.location.rsplit(",", 1)[-1].strip() or None

    @property
    def owner_names(self) -> list[str]:
        """The recorded owner name together with its supplied variants."""
        return [self.owner_name, *self.owner_name_variants]

    def cite(self, *field_names: str) -> dict[str, object]:
        """Field-level citation for a flag: the exact values a rule relied on.

        Always carries source_ref so a user can find the row in their file.
        """
        cited: dict[str, object] = {"source_ref": self.source_ref}
        for name in field_names:
            value = getattr(self, name)
            if isinstance(value, (TransactionState, RestitutionRecipientType)):
                value = value.value
            elif isinstance(value, DateSpan):
                value = value.describe()
            cited[name] = value
        return cited


FIELD_NAMES = (
    "object_id",
    "object_title",
    "object_class",
    "object_date",
    "object_date_to",
    "object_date_precision",
    "owner_name",
    "owner_name_variants",
    "date_from",
    "date_to",
    "date_precision",
    "transaction_state",
    "location",
    "source_citation",
    "export_licence_present",
    "is_institution_acquisition",
    "catalogue_reference",
    "owner_stated_in_catalogue",
    "restitution_recipient_type",
    "notes",
)

REQUIRED_FIELD_NAMES = ("object_id", "owner_name", "date_precision")

# Additions to the schema in CLAUDE.md, each for the same reason: the rule
# that needs it is not computable from the listed fields, and the alternative
# is inferring it from free text — the guess that produces silent false
# negatives. Where one of these is absent or blank the dependent rule reports
# itself *skipped* in coverage_note rather than "not triggered".
#
#   is_institution_acquisition  -> PC-002: which event is the accession
#   catalogue_reference         -> PC-007: which records are catalogue entries
#   owner_stated_in_catalogue   -> PC-007: whether that entry names an owner
#   restitution_recipient_type  -> PC-008: state vs. individual/heirs
OPTIONAL_EXTENSIONS = (
    "is_institution_acquisition",
    "catalogue_reference",
    "owner_stated_in_catalogue",
    "restitution_recipient_type",
)


def build_record(
    raw: Mapping[str, str | None],
    source_ref: str,
    source_order: int,
    circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS,
) -> ProvenanceRecord:
    """Validate one raw row into a ProvenanceRecord.

    Collects every problem in the row before raising, so a user fixing a file
    sees all of them at once.
    """
    problems: list[str] = []

    def text(name: str) -> str | None:
        value = raw.get(name)
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    object_id = text("object_id")
    if not object_id:
        problems.append("object_id is required and must not be empty")

    owner_name = text("owner_name")
    if not owner_name:
        problems.append(
            "owner_name is required and must not be empty "
            "(use an explicit placeholder such as 'unknown' for an unrecorded owner)"
        )

    date_span = None
    try:
        date_span = build_date_span(
            text("date_from"),
            text("date_to"),
            text("date_precision"),
            circa_margin_years,
        )
    except ValueError as exc:
        problems.append(str(exc))

    object_date_span = None
    try:
        object_date_span = build_date_span(
            text("object_date"),
            text("object_date_to"),
            text("object_date_precision"),
            circa_margin_years,
        )
    except ValueError as exc:
        # Re-labelled so the message names the column the user has to fix,
        # rather than the transfer-date column that shares the parsing code.
        message = str(exc)
        if "date_precision" in message or "date_from" in message or "date_to" in message:
            message = (
                message.replace("date_precision", "object_date_precision")
                .replace("date_from", "object_date")
                .replace("date_to", "object_date_to")
            )
        else:
            message = f"object_date: {message}"
        problems.append(message)

    transaction_state = None
    try:
        transaction_state = parse_transaction_state(text("transaction_state"))
    except ValueError as exc:
        problems.append(str(exc))

    booleans: dict[str, bool | None] = {}
    for name in (
        "export_licence_present",
        "is_institution_acquisition",
        "owner_stated_in_catalogue",
    ):
        try:
            booleans[name] = parse_bool(raw.get(name), name)
        except ValueError as exc:
            problems.append(str(exc))
            booleans[name] = None

    restitution_recipient_type = None
    try:
        restitution_recipient_type = parse_restitution_recipient_type(
            text("restitution_recipient_type")
        )
    except ValueError as exc:
        problems.append(str(exc))

    # A recipient type only means anything on a restitution record. Accepting
    # it elsewhere would let it look recorded when nothing consumes it.
    if (
        restitution_recipient_type is not None
        and transaction_state is not TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH
    ):
        problems.append(
            "restitution_recipient_type is set on a record whose transaction_state "
            f"is {transaction_state.value if transaction_state else 'unset'}; it is "
            "only meaningful on a restitution_erfolgt_or_vergleich record"
        )

    if problems:
        raise RecordValidationError(source_ref, problems)

    return ProvenanceRecord(
        object_id=object_id,
        owner_name=owner_name,
        source_ref=source_ref,
        source_order=source_order,
        object_title=text("object_title"),
        object_class=text("object_class"),
        object_date=text("object_date"),
        object_date_to=text("object_date_to"),
        object_date_precision=text("object_date_precision"),
        object_date_span=object_date_span,
        owner_name_variants=split_variants(text("owner_name_variants")),
        date_from=text("date_from"),
        date_to=text("date_to"),
        date_span=date_span,
        transaction_state=transaction_state,
        location=text("location"),
        source_citation=text("source_citation"),
        export_licence_present=booleans["export_licence_present"],
        is_institution_acquisition=booleans["is_institution_acquisition"],
        catalogue_reference=text("catalogue_reference"),
        owner_stated_in_catalogue=booleans["owner_stated_in_catalogue"],
        restitution_recipient_type=restitution_recipient_type,
        notes=text("notes"),
    )


def _chain_sort_key(record: ProvenanceRecord) -> tuple[int, date, int]:
    """Order a chain chronologically; undated records sort last, stably."""
    span = record.date_span
    if span is None:
        return (1, date.max, record.source_order)
    anchor = span.nominal_earliest or span.nominal_latest or date.min
    return (0, anchor, record.source_order)


@dataclass(frozen=True)
class ObjectChain:
    """All records sharing an object_id, ordered by date."""

    object_id: str
    records: list[ProvenanceRecord]

    @property
    def object_title(self) -> str | None:
        return next((r.object_title for r in self.records if r.object_title), None)

    @property
    def object_class(self) -> str | None:
        return next((r.object_class for r in self.records if r.object_class), None)

    @property
    def object_date_span(self) -> DateSpan | None:
        """The object's own creation date, first non-empty value in the chain."""
        return next(
            (r.object_date_span for r in self.records if r.object_date_span), None
        )

    @property
    def object_date(self) -> str | None:
        span = self.object_date_span
        return span.describe() if span is not None else None

    def certainly_created_after(self, threshold: date) -> bool:
        """Whether the object certainly did not exist before `threshold`.

        Only the EARLIEST possible day counts, and it is the *widened* one, so
        the stated precision governs: "1946" certainly postdates 1945, and
        "circa 1948" does not, because the circa margin puts its earliest
        plausible creation at 1943. That is the same rule the rest of the tool
        applies — a widened date can never yield a certain answer — reused
        rather than reinvented.

        A blank creation date returns False. An unrecorded date is not evidence
        the object is modern, and treating it as such would switch off the rule
        this feeds for every file that omits the column.
        """
        span = self.object_date_span
        if span is None or span.earliest is None:
            return False
        return span.earliest >= threshold

    @property
    def dated_records(self) -> list[ProvenanceRecord]:
        return [r for r in self.records if r.is_dated]

    @property
    def earliest_record(self) -> ProvenanceRecord | None:
        dated = self.dated_records
        return dated[0] if dated else (self.records[0] if self.records else None)


def group_into_chains(records: list[ProvenanceRecord]) -> list[ObjectChain]:
    """Group validated records by object_id, preserving first-seen order."""
    grouped: dict[str, list[ProvenanceRecord]] = {}
    for record in records:
        grouped.setdefault(record.object_id, []).append(record)
    return [
        ObjectChain(object_id=object_id, records=sorted(rows, key=_chain_sort_key))
        for object_id, rows in grouped.items()
    ]
