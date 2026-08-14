"""Deterministic heuristic layer — v1 rules 1-4.

Two axes, never merged: `persecution_context_flags` and
`documentation_quality_flags`. A missing citation is a records problem; a
1936 Vienna transfer is a research problem. Combining them would produce
exactly the single number this tool must not produce.

Nothing here scores, ranks or clears an object. Each rule emits a citation to
the fields it read, a named methodological source, and a statement phrased as
an open question for a researcher.

Implemented in this module (the full v1 rule set):
    PC-001  no pre-1945 provenance at all                        (spec rule 1)
    PC-002  chain begins at institutional acquisition            (spec rule 2)
    PC-003  persecution-window engagement, REAO tier             (spec rule 3)
    DQ-001  missing source_citation                              (spec rule 4)
    PC-004  object-class channel rule, class + date alone        (spec rule 5)
    PC-005  confiscation-channel actor match                     (spec rule 6)
    PC-006  anonymous provenance entry inside the band           (spec rule 7)
    PC-007  deleted owner across two catalogue records           (spec rule 8)
    DQ-002  post-commitment acquisition, thin pre-1945 chain     (spec rule 9)
    PC-008  restitution recorded to a state, not to heirs        (spec rule 10)
    PC-009  missing export licence on a cross-border movement    (spec rule 11)
    NM-001  ALIU list match, documented-concern entry            (spec rule 12)
    NM-002  ALIU list match, exonerating entry — a citation, not a flag

NM-002 deliberately leaves this module on its own channel
(`name_list_citations`) rather than as a flag. An ALIU entry marked
exonerating records that a name was investigated and cleared, or that the
person acted protectively; rendering that as a flag would inverts its meaning
and defame the subject. See name_matching.py.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .name_matching import (
    ACTOR_CITATION_DISCLAIMER,
    ALIU_CITATION_DISCLAIMER,
    ActorList,
    AliuList,
    ReferenceListError,
    load_actor_list,
    load_aliu_list,
    normalise as normalise_name,
)
from .reao_taxonomy import (
    STATE_GLOSSES,
    PresumptionTier,
    RestitutionRecipientType,
    TransactionState,
    intrinsic_tier,
    is_resolved_state,
)
from .schema import CERTAIN, PRE_1945_THRESHOLD, ObjectChain, ProvenanceRecord

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_ONSET_TABLE_PATH = DATA_DIR / "persecution_onset_table.json"
DEFAULT_ANONYMOUS_PATTERNS_PATH = DATA_DIR / "anonymous_owner_patterns.json"

# Washington Conference Principles on Nazi-Confiscated Art, released
# 3 December 1998. Used only when the institution has not configured its own
# commitment date; every report states which date was applied and why.
WASHINGTON_PRINCIPLES_DATE = date(1998, 12, 3)
WASHINGTON_PRINCIPLES_SOURCE = (
    "Washington Conference Principles on Nazi-Confiscated Art, released "
    "3 December 1998 — the default where the institution has not recorded its "
    "own commitment date. An institution that adopted the Principles on a "
    "different date should configure that date instead."
)

# Spec rule 6 scopes confiscation-channel matching to the 1938-1945 window.
ACTOR_WINDOW = (date(1938, 1, 1), date(1945, 12, 31))

# Spec rule 11 scopes the export-licence rule to 1933-1945.
EXPORT_LICENCE_WINDOW = (date(1933, 1, 1), date(1945, 12, 31))

# Spec rule 8: "published in a pre-1933 catalogue raisonné ... reappearing
# post-1950 with that owner removed".
DELETED_OWNER_EARLY_BEFORE = date(1933, 1, 1)
DELETED_OWNER_LATE_AFTER = date(1950, 12, 31)

# Spec rule 5. These classes moved through channels that left no ownership
# chain, so a chain-based rule under-flags them to near zero. Match terms are
# name variants of the same class, not additional classes.
OBJECT_CLASS_RULE_TERMS = {
    "silver": ("silver", "silberzeug", "silber", "argenterie", "argento"),
    "Judaica": ("judaica", "judaika", "judaisme"),
    "coins": (
        "coin",
        "coins",
        "munze",
        "munzen",
        "numismatic",
        "numismatics",
        "medal",
        "medals",
    ),
    "decorative art": (
        "decorative art",
        "decorative arts",
        "kunstgewerbe",
        "arts decoratifs",
        "arti decorative",
    ),
}

AXIS_PERSECUTION = "persecution_context"
AXIS_DOCUMENTATION = "documentation_quality"

RULE_PC_001 = "PC-001"
RULE_PC_002 = "PC-002"
RULE_PC_003 = "PC-003"
RULE_PC_004 = "PC-004"
RULE_PC_005 = "PC-005"
RULE_PC_006 = "PC-006"
RULE_PC_007 = "PC-007"
RULE_PC_008 = "PC-008"
RULE_PC_009 = "PC-009"
RULE_PC_010 = "PC-010"
RULE_DQ_001 = "DQ-001"
RULE_DQ_002 = "DQ-002"
RULE_NM_001 = "NM-001"
RULE_NM_002 = "NM-002"

METHODOLOGY = {
    RULE_PC_001: (
        "Washington Principles (1998) §§1-2 and Terezin Declaration (2009): "
        "identifying objects that carry no pre-1945 ownership documentation is "
        "the precondition for provenance research. The absence of a pre-1945 "
        "chain is a research gap, not a finding about the object."
    ),
    RULE_PC_002: (
        "Washington Principles (1998) §§1-2: a chain whose earliest entry is the "
        "institution's own accession documents the institution's holding, not the "
        "object's history before it."
    ),
    RULE_PC_003: (
        "REAO (Rückerstattungsanordnung) Art. 3 presumption of a "
        "persecution-related loss; the presumption is heightened "
        "(verschärfte Vermutung) for transfers on or after 15 September 1935 "
        "(Nuremberg Laws). Territory onset dates: see the cited persecution-onset "
        "table entry."
    ),
    RULE_PC_004: (
        "Object-class channel rule. Objects in these classes moved through "
        "confiscation channels that produced no ownership chain — forced surrender "
        "to municipal pawn offices under the 1939 precious-metals decree, and "
        "property-stripping via the tax authorities under the 1941 11th Decree to "
        "the Reich Citizenship Law. Screening them on chain evidence under-flags "
        "them to near zero, so this rule triggers on class and date alone."
    ),
    RULE_PC_005: (
        "Confiscation-channel actor list (bundled, each entry carrying its own "
        "documented basis and sources). A match is a citation to that documented "
        "basis and carries no finding about this transaction. Where the matched "
        "basis does not itself bear on persecution of a private former owner, the "
        "flag says so rather than implying otherwise."
    ),
    RULE_PC_006: (
        "Anonymous-entry rule. An anonymous or generic owner entry is unremarkable "
        "across most of a collection's history; inside the persecution band it is "
        "the standard form an interrupted ownership history takes once a name has "
        "dropped out of the record. The rule is scoped to that band for that reason."
    ),
    RULE_PC_007: (
        "Deleted-owner rule. An owner named in a pre-1933 published catalogue who "
        "is absent from a post-1950 entry for the same object is stronger evidence "
        "than a merely missing owner, because the earlier record establishes that a "
        "name was once held and published."
    ),
    RULE_PC_008: (
        "Restitution-recipient distinction. External restitution to a state "
        "frequently succeeded where internal restitution to the dispossessed family "
        "did not, so a settlement recorded to a state or institution leaves open "
        "whether the individuals or heirs ever received anything."
    ),
    RULE_PC_010: (
        "REAO Art. 3 / Swiss restitution practice. `fluchtgut` names property "
        "sold by a refugee who had already reached a safe haven. It is NOT a "
        "presumption of persecution-related loss: Swiss practice has "
        "historically distinguished it from property taken in occupied "
        "territory, and whether — and on what terms — a fluchtgut sale should "
        "found a claim is contested rather than settled. This criterion "
        "therefore asserts no presumption tier and states the question. It is "
        "among the parts of this tool most in need of review by a provenance "
        "researcher and an admitted lawyer."
    ),
    RULE_PC_009: (
        "Export-licence rule. A cross-border movement dated 1933-1945 without a "
        "recorded export licence leaves the lawfulness of the movement unevidenced "
        "in the record supplied."
    ),
    RULE_NM_001: (
        "ALIU Red Flag Names List (Office of Strategic Services Art Looting "
        "Investigation Unit, 1946), a public government-produced investigative "
        "record. Matching against it is standard field practice. A match is a "
        "citation to that record and is not a characterization of any person."
    ),
    RULE_NM_002: (
        "ALIU Red Flag Names List, exonerating entry. Recorded as context so that "
        "the appearance of the name is not later mistaken for an unexamined "
        "concern. This is not a flag and does not place the object in the queue."
    ),
    RULE_DQ_001: (
        "Documentation-completeness axis. A missing source citation is a records-"
        "quality signal only and carries no implication about persecution context; "
        "it is reported on a separate axis for that reason."
    ),
    RULE_DQ_002: (
        "Institutional acquisition practice measured against the institution's own "
        "stated standard (Washington Principles, 1998). This concerns how the "
        "institution acquired the object, not the object's own history, and is "
        "reported on the documentation axis for that reason — it asserts nothing "
        "about persecution context."
    ),
}

CRITERIA = [
    {
        "rule_id": RULE_PC_001,
        "title": "No pre-1945 provenance recorded",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_002,
        "title": "Chain begins at institutional acquisition",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_003,
        "title": "Transfer engages a persecution window (REAO presumption tier)",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_004,
        "title": "Object class moved through a channel that left no chain",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_005,
        "title": "Party matches the confiscation-channel actor list",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_006,
        "title": "Anonymous owner entry inside the 1933-1950 band",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_007,
        "title": "Owner named pre-1933 absent from a post-1950 catalogue entry",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_008,
        "title": "Restitution recorded to a state rather than to individuals or heirs",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_009,
        "title": "Cross-border movement 1933-1945 without a recorded export licence",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_PC_010,
        "title": "Record states a fluchtgut transfer (contested category)",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_NM_001,
        "title": "Name matches an ALIU Red Flag Names List documented-concern entry",
        "axis": AXIS_PERSECUTION,
    },
    {
        "rule_id": RULE_DQ_001,
        "title": "Transaction lacks a source citation",
        "axis": AXIS_DOCUMENTATION,
    },
    {
        "rule_id": RULE_DQ_002,
        "title": "Acquired after the commitment date with a thin pre-1945 chain",
        "axis": AXIS_DOCUMENTATION,
    },
]

# NM-002 is deliberately absent from CRITERIA: an exonerating citation is not
# a criterion an object can fail, and counting it in "screened against N
# criteria" would misdescribe what ran.

SCREENED_NO_FLAGS_STATEMENT = (
    "Screened against {n} criteria; no criteria triggered. Absence of flags is "
    "not evidence of unproblematic provenance and does not constitute "
    "provenance research."
)

SCREENED_WITH_FLAGS_STATEMENT = (
    "Screened against {n} criteria; {triggered} triggered. A triggered criterion "
    "is a question for a human researcher, not a finding about this object."
)


def _normalise(text: str) -> str:
    """Casefold and strip diacritics so 'Österreich' matches 'osterreich'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


# A window whose tier is derived from the transfer's position relative to the
# global Nuremberg threshold, rather than fixed by the territory's own
# chronology. This is the normal case; see the Italy entry for the exception.
TIER_BY_NUREMBERG = "by_nuremberg_threshold"


@dataclass(frozen=True)
class PersecutionWindow:
    """One persecution period for a territory, with the tier it engages."""

    id: str
    onset_earliest: date
    onset_latest: date
    onset_label: str
    ends: date
    tier: PresumptionTier | None  # None = derive from the Nuremberg threshold
    tier_basis: str | None
    source: str

    @property
    def onset_is_a_range(self) -> bool:
        return self.onset_earliest != self.onset_latest

    @property
    def bounds(self) -> tuple[date, date]:
        return self.onset_earliest, self.ends


@dataclass(frozen=True)
class Territory:
    id: str
    territory: str
    match_terms: tuple[str, ...]
    windows: tuple[PersecutionWindow, ...]
    note: str | None = None

    def cite(self, window: PersecutionWindow) -> dict[str, object]:
        cited: dict[str, object] = {
            "territory": self.territory,
            "persecution_window": window.id,
            "persecution_onset": window.onset_label,
            "onset_earliest": window.onset_earliest.isoformat(),
            "onset_latest": window.onset_latest.isoformat(),
            "window_ends": window.ends.isoformat(),
            "onset_table_source": window.source,
        }
        if self.note:
            cited["onset_table_note"] = self.note
        return cited


@dataclass(frozen=True)
class PersecutionOnsetTable:
    territories: tuple[Territory, ...]
    risk_band_end: date
    risk_band_end_rationale: str
    heightened_from: date
    heightened_from_label: str
    version: str
    review_status: str

    @property
    def global_band(self) -> tuple[date, date]:
        """Earliest onset in the table through the risk-band end.

        Used by rules that are not keyed to a territory (the object-class
        rule), where requiring a location match would under-flag exactly the
        records the rule exists to catch — those with no chain and no place.
        """
        earliest = min(w.onset_earliest for t in self.territories for w in t.windows)
        return earliest, self.risk_band_end

    def match(self, location: str | None) -> list[Territory]:
        """Territories whose name variants appear in a location string."""
        if not location:
            return []
        haystack = _normalise(location)
        matched = []
        for territory in self.territories:
            for term in territory.match_terms:
                if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack):
                    matched.append(territory)
                    break
        return matched


def _load_window(raw: dict, risk_band_end: date) -> PersecutionWindow:
    tier_text = raw.get("presumption_tier", TIER_BY_NUREMBERG)
    if tier_text == TIER_BY_NUREMBERG:
        tier = None
    else:
        tier = PresumptionTier(tier_text)  # raises on an unrecognised value
    return PersecutionWindow(
        id=raw["id"],
        onset_earliest=date.fromisoformat(raw["onset_earliest"]),
        onset_latest=date.fromisoformat(raw["onset_latest"]),
        onset_label=raw["onset_label"],
        ends=date.fromisoformat(raw["ends"]) if raw.get("ends") else risk_band_end,
        tier=tier,
        tier_basis=raw.get("tier_basis"),
        source=raw["source"],
    )


def load_onset_table(path: str | Path = DEFAULT_ONSET_TABLE_PATH) -> PersecutionOnsetTable:
    """Load the configurable territory/date table. Fails loudly if malformed."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    risk_band_end = date.fromisoformat(raw["risk_band_end"]["date"])
    territories = tuple(
        Territory(
            id=entry["id"],
            territory=entry["territory"],
            match_terms=tuple(_normalise(t) for t in entry["match_terms"]),
            windows=tuple(_load_window(w, risk_band_end) for w in entry["windows"]),
            note=entry.get("_note"),
        )
        for entry in raw["territories"]
    )
    return PersecutionOnsetTable(
        territories=territories,
        risk_band_end=risk_band_end,
        risk_band_end_rationale=raw["risk_band_end"]["rationale"],
        heightened_from=date.fromisoformat(raw["heightened_presumption_from"]["date"]),
        heightened_from_label=raw["heightened_presumption_from"]["label"],
        version=raw.get("version", "unversioned"),
        review_status=raw.get("_review_status", ""),
    )


@dataclass(frozen=True)
class AnonymousPatternList:
    patterns: tuple[tuple[str, str], ...]  # (normalised pattern, raw pattern)
    band_from: date
    band_to: date
    source: str
    version: str

    def match(self, names: list[str]) -> list[tuple[str, str]]:
        """(raw pattern, the supplied name it matched) for each hit."""
        hits = []
        for supplied in names:
            if not supplied:
                continue
            haystack = normalise_name(supplied)
            for normalised, raw in self.patterns:
                if re.search(rf"(?<!\w){re.escape(normalised)}(?!\w)", haystack):
                    hits.append((raw, supplied))
                    break
        return hits


def load_anonymous_patterns(
    path: str | Path = DEFAULT_ANONYMOUS_PATTERNS_PATH,
) -> AnonymousPatternList:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    band = raw["band"]
    return AnonymousPatternList(
        patterns=tuple(
            (normalise_name(p["pattern"]), p["pattern"]) for p in raw["patterns"]
        ),
        band_from=date.fromisoformat(band["from"]),
        band_to=date.fromisoformat(band["to"]),
        source=band["source"],
        version=raw.get("version", "unversioned"),
    )


@dataclass(frozen=True)
class ScreeningConfig:
    """Everything the rule set needs beyond the records themselves.

    `unavailable` maps a rule_id to the reason its inputs are missing. A rule
    listed there reports itself *skipped* with that reason rather than
    running and finding nothing — the two are different states and the output
    must not collapse them.
    """

    onset_table: PersecutionOnsetTable
    anonymous_patterns: AnonymousPatternList | None = None
    aliu_list: AliuList | None = None
    actor_list: ActorList | None = None
    commitment_date: date = WASHINGTON_PRINCIPLES_DATE
    commitment_date_source: str = WASHINGTON_PRINCIPLES_SOURCE
    commitment_date_is_default: bool = True
    unavailable: dict[str, str] = field(default_factory=dict)


def build_config(
    onset_table_path: str | Path = DEFAULT_ONSET_TABLE_PATH,
    anonymous_patterns_path: str | Path = DEFAULT_ANONYMOUS_PATTERNS_PATH,
    aliu_path: str | Path | None = None,
    actors_path: str | Path | None = None,
    commitment_date: date | None = None,
    commitment_date_source: str | None = None,
) -> ScreeningConfig:
    """Assemble the screening config, recording any unavailable reference list.

    A missing bundled list is not fatal: the dependent rule reports itself
    skipped and every other rule still runs. It is never silently ignored.
    """
    unavailable: dict[str, str] = {}

    try:
        anonymous_patterns = load_anonymous_patterns(anonymous_patterns_path)
    except (OSError, KeyError, ValueError) as exc:
        anonymous_patterns = None
        unavailable[RULE_PC_006] = f"anonymous-owner pattern list unavailable: {exc}"

    aliu_list = None
    try:
        aliu_list = load_aliu_list(aliu_path) if aliu_path else load_aliu_list()
    except ReferenceListError as exc:
        unavailable[RULE_NM_001] = str(exc)

    actor_list = None
    try:
        actor_list = load_actor_list(actors_path) if actors_path else load_actor_list()
    except ReferenceListError as exc:
        unavailable[RULE_PC_005] = str(exc)

    return ScreeningConfig(
        onset_table=load_onset_table(onset_table_path),
        anonymous_patterns=anonymous_patterns,
        aliu_list=aliu_list,
        actor_list=actor_list,
        commitment_date=commitment_date or WASHINGTON_PRINCIPLES_DATE,
        commitment_date_source=commitment_date_source
        or (
            WASHINGTON_PRINCIPLES_SOURCE
            if commitment_date is None
            else "configured by the institution"
        ),
        commitment_date_is_default=commitment_date is None,
        unavailable=unavailable,
    )


def _flag(
    rule_id: str,
    statement: str,
    cited_fields: dict[str, object],
    tier: PresumptionTier | None = None,
    methodology: str | None = None,
) -> dict[str, object]:
    flag: dict[str, object] = {
        "rule_id": rule_id,
        "rule_statement": statement,
        # A rule with more than one limb may need to state a different
        # grounding per limb: PC-003's window methodology ends by pointing at
        # a cited onset-table entry, which a state-based flag does not have.
        "methodology": methodology or METHODOLOGY[rule_id],
        "cited_fields": cited_fields,
    }
    if tier is not None:
        flag["presumption_tier"] = tier.value
    # Filled by the LLM layer when it runs. Explicitly null rather than absent
    # so a consumer can tell "not generated" from "generated empty" — on both
    # axes, since the documentation axis needs plain language just as much.
    flag["llm_explanation"] = None
    return flag


# --------------------------------------------------------------------------
# Rule PC-001 — no pre-1945 provenance at all
# --------------------------------------------------------------------------


def _pre_1945_status(chain: ObjectChain) -> tuple[bool, bool]:
    """(any record possibly pre-1945, any record certainly pre-1945).

    A record is *certainly* pre-1945 only when its precision is exact/month/
    year and its whole interval falls before the threshold. A `circa 1944` or
    `before 1960` record is only *possibly* pre-1945, and the difference is
    reported rather than resolved.
    """
    possibly = False
    certainly = False
    for record in chain.dated_records:
        span = record.date_span
        if span.earliest is None or span.earliest < PRE_1945_THRESHOLD:
            possibly = True
        bound = span.certainly_before
        if bound is not None and bound < PRE_1945_THRESHOLD:
            certainly = True
    return possibly, certainly


def rule_no_pre_1945_provenance(
    chain: ObjectChain,
) -> tuple[list[dict[str, object]], str | None]:
    """Returns (flags, note). The note explains a non-application.

    An object created after the risk band cannot have pre-1945 provenance, so
    reporting its absence says nothing about documentation — and it says it on
    the coverage map, which is the first screen. Where the creation date is
    absent the rule still runs: an unrecorded date is not evidence the object
    is modern, and disabling the modal rule on a blank column would be the
    silent false negative this project is built to avoid.
    """
    possibly, _ = _pre_1945_status(chain)
    if possibly:
        return [], None
    if chain.certainly_created_after(PRE_1945_THRESHOLD):
        return [], (
            f"{RULE_PC_001} not applicable: the object's recorded creation date "
            f"({chain.object_date}) is on or after "
            f"{PRE_1945_THRESHOLD.isoformat()}, so it cannot have provenance "
            f"before then. This is not a finding about documentation."
        )
    dated = chain.dated_records
    cited = {
        "records_in_chain": len(chain.records),
        "dated_records_in_chain": len(dated),
        "recorded_dates": [
            {"source_ref": r.source_ref, "date": r.date_span.describe()} for r in dated
        ],
        "undated_records": [
            r.source_ref for r in chain.records if not r.is_dated
        ],
        "threshold": PRE_1945_THRESHOLD.isoformat(),
        "object_date": chain.object_date,
    }
    # Two different findings, and the difference is exactly what the creation
    # date settles. Without it the rule cannot separate "the documentation is
    # missing" from "the object was created later and never had any".
    if chain.object_date:
        qualifier = (
            f" The object's recorded creation date is {chain.object_date}, so it "
            f"could have had an owner before then."
        )
    else:
        qualifier = (
            " No creation date is recorded for the object, so this finding "
            "cannot distinguish an object whose early provenance is "
            "undocumented from one that was created after 1945 and never had "
            "any. Record `object_date` to separate the two."
        )
    statement = (
        "Unverified: is there any documentation of ownership before 1945? No "
        "record in the supplied chain is dated, even allowing for its stated "
        "date precision, before 1 January 1945." + qualifier
    )
    return [_flag(RULE_PC_001, statement, cited, PresumptionTier.NOT_APPLICABLE)], None


# --------------------------------------------------------------------------
# Rule PC-002 — chain begins at institutional acquisition
# --------------------------------------------------------------------------


def rule_chain_begins_at_acquisition(
    chain: ObjectChain,
) -> tuple[list[dict[str, object]], str | None]:
    """Returns (flags, skip_reason). Skipped when the column is never set."""
    if all(r.is_institution_acquisition is None for r in chain.records):
        return [], (
            f"{RULE_PC_002} skipped: no record for this object sets "
            "`is_institution_acquisition`, so the institution's own accession "
            "cannot be identified in the chain."
        )

    first = chain.records[0] if chain.records else None
    if first is None or not first.is_institution_acquisition:
        return [], None

    # An undated record elsewhere in the chain may well precede the accession;
    # chain order is not established, so the rule cannot answer its question.
    undated_others = [r.source_ref for r in chain.records[1:] if not r.is_dated]
    if undated_others:
        return [], (
            f"{RULE_PC_002} skipped: the institution's accession is the earliest "
            f"*dated* record, but undated record(s) ({', '.join(undated_others)}) "
            "may precede it, so chain order is not established."
        )

    cited = first.cite(
        "owner_name", "date_span", "transaction_state", "is_institution_acquisition"
    )
    cited["records_earlier_in_chain"] = 0
    cited["records_in_chain"] = len(chain.records)
    statement = (
        "Unverified: who held this object before the institution acquired it? "
        "The earliest record in the supplied chain is the institution's own "
        "acquisition, and no prior owner is recorded."
    )
    return [_flag(RULE_PC_002, statement, cited, PresumptionTier.NOT_APPLICABLE)], None


# --------------------------------------------------------------------------
# Rule PC-003 — persecution-window engagement
# --------------------------------------------------------------------------


def _tier_for_window(
    record: ProvenanceRecord,
    table: PersecutionOnsetTable,
    window: PersecutionWindow,
) -> tuple[PresumptionTier, str]:
    """Presumption tier engaged by the overlap, plus the basis for that tier.

    Precedence: a transaction_state that is itself a presumption definition
    governs; then a tier fixed by the window (a territory whose chronology the
    Nuremberg threshold does not describe); otherwise the tier is derived from
    the transfer's position relative to that threshold.
    """
    recorded = intrinsic_tier(record.transaction_state)
    if recorded is not None:
        return recorded, "recorded in transaction_state"

    if window.tier is not None:
        return window.tier, (
            window.tier_basis or "the tier is set explicitly for this window"
        )

    return _tier_by_nuremberg(record, table, window.bounds)


def _tier_by_nuremberg(
    record: ProvenanceRecord,
    table: PersecutionOnsetTable,
    bounds: tuple[date, date],
) -> tuple[PresumptionTier, str]:
    """Tier from the transfer's position relative to the 15 Sept 1935 threshold."""
    span = record.date_span
    overlap = span.clipped_to(*bounds)
    tier = (
        PresumptionTier.HEIGHTENED
        if overlap and overlap[1] >= table.heightened_from
        else PresumptionTier.ORDINARY
    )

    nominal = span.clipped_to(*bounds, nominal=True)
    if span.is_imprecise or nominal is None:
        return tier, (
            "date-precision dependent: the stated precision "
            f"'{span.precision.value}' leaves the transfer's position relative to "
            f"{table.heightened_from.isoformat()} ({table.heightened_from_label}) "
            "undetermined"
        )
    if nominal[0] >= table.heightened_from:
        return tier, "the recorded date range lies wholly on or after the threshold"
    if nominal[1] < table.heightened_from:
        return tier, "the recorded date range lies wholly before the threshold"
    return tier, (
        "the recorded date range straddles "
        f"{table.heightened_from.isoformat()} ({table.heightened_from_label}); "
        "the heightened tier is reported because the transfer may fall on or "
        "after it"
    )


# States whose own definition records a persecution transfer, independently of
# where it happened. These engage PC-003 on the record's own statement — see
# `rule_persecution_window` for why that second limb has to exist.
#
# `fluchtgut` is deliberately absent: it is a contested category rather than a
# presumption, and gets its own criterion (PC-010) which asserts no tier.
STATES_RECORDING_PERSECUTION = frozenset(
    {
        TransactionState.ENTZIEHUNG,
        TransactionState.ZWANGSVERKAUF,
        TransactionState.VERMUTUNG_DER_ENTZIEHUNG,
        TransactionState.VERSCHAERFTE_VERMUTUNG,
    }
)


def rule_fluchtgut(chain: ObjectChain) -> tuple[list[dict[str, object]], list[str]]:
    """Records that state a `fluchtgut` transfer.

    Kept apart from PC-003 deliberately. `fluchtgut` — property sold by a
    refugee who had already reached a safe haven — is not a presumption of
    persecution-related loss, and Swiss practice has historically treated it
    as distinct from property taken in occupied territory. Whether such a sale
    should found a claim, and on what terms, is contested rather than settled.

    So this criterion asserts no tier and states the question. It also has to
    exist at all: Switzerland has no persecution window and correctly never
    will, so before this rule the one category the spec names as most relevant
    to the tool's home jurisdiction could not fire.
    """
    flags: list[dict[str, object]] = []
    for record in chain.records:
        if record.transaction_state is not TransactionState.FLUCHTGUT:
            continue
        if is_resolved_state(record.transaction_state):
            continue
        cited = record.cite(
            "owner_name", "date_span", "location", "transaction_state"
        )
        cited["transaction_state_gloss"] = STATE_GLOSSES[TransactionState.FLUCHTGUT]
        cited["category_status"] = (
            "contested. Swiss restitution practice has historically "
            "distinguished fluchtgut from property taken in occupied "
            "territory, and no presumption of persecution-related loss "
            "attaches to it in the way REAO Art. 3 attaches one to a transfer "
            "in an occupied territory. This tool asserts no tier here."
        )
        statement = (
            "Unverified: on what terms was this sale made, and what became of "
            "the proceeds? The record states its own transaction type as "
            "'fluchtgut' — property sold by a refugee who had already reached a "
            "safe haven. This is a CONTESTED category rather than a presumption: "
            "no REAO presumption tier is asserted, and this flag makes no "
            "finding that the transfer was persecution-related. It is recorded "
            "because the record itself raises the question."
        )
        flags.append(
            _flag(RULE_PC_010, statement, cited, PresumptionTier.NOT_APPLICABLE)
        )
    return flags, []


def _tier_from_state(
    record: ProvenanceRecord, table: PersecutionOnsetTable
) -> tuple[PresumptionTier, str]:
    """Tier for a state-based engagement, where no window supplies bounds.

    This declines to assert a tier wherever the record's date cannot be placed
    against the threshold, rather than defaulting to one. Two bugs came from
    defaulting, with opposite signs: a 1970 record was clipped to the risk
    band, produced an empty intersection, and fell through to the MILDER tier
    with a basis string claiming its position was undetermined when it was not;
    and a `circa 1935` record was assigned the HARSHER tier while its own basis
    string said the position was undetermined.

    Both are the same defect — picking a tier where the honest answer is that
    there isn't one — and defaulting to the milder tier on unplaceable data is
    the false-negative direction besides. The bounds used are the *widened*
    ones, so an imprecise date can never produce a determinate answer, which is
    the rule the rest of the schema already applies.
    """
    span = record.date_span
    if span is None:
        return PresumptionTier.NOT_APPLICABLE, (
            "no tier asserted: the transaction_state records a persecution "
            "transfer but carries no intrinsic REAO tier, and the record has no "
            "date from which to derive one. No territory window was matched."
        )

    band = table.global_band
    if span.overlap_certainty(*band) is None:
        # Almost certainly a data-entry error rather than a claim. The state
        # still says a persecution transfer occurred, so the flag stands; what
        # cannot stand is a tier derived from a date the band excludes.
        return PresumptionTier.NOT_APPLICABLE, (
            f"no tier asserted: the recorded date ({span.describe()}) lies "
            f"wholly outside the screening band {band[0].isoformat()} to "
            f"{band[1].isoformat()}, so no REAO tier can be derived from it. "
            f"The record's own transaction_state and its date do not agree; "
            f"check the date before reading anything into this flag."
        )

    recorded = intrinsic_tier(record.transaction_state)
    if recorded is not None:
        return recorded, (
            "recorded in transaction_state; no territory window was matched, so "
            "the tier comes from the state's own REAO definition"
        )

    threshold = table.heightened_from
    label = f"{threshold.isoformat()} ({table.heightened_from_label})"
    if span.earliest is not None and span.earliest >= threshold:
        return PresumptionTier.HEIGHTENED, (
            f"the record's date lies wholly on or after {label}, allowing for "
            f"its stated precision. No territory window was matched."
        )
    if span.latest is not None and span.latest < threshold:
        return PresumptionTier.ORDINARY, (
            f"the record's date lies wholly before {label}, allowing for its "
            f"stated precision. No territory window was matched."
        )
    return PresumptionTier.NOT_APPLICABLE, (
        f"no tier asserted: allowing for the stated precision "
        f"'{span.precision.value}', the recorded date ({span.describe()}) "
        f"cannot be placed wholly on either side of {label}. A widened or "
        f"open-ended date never yields a determinate tier, and reporting the "
        f"harsher of two possibilities would assert what the record does not "
        f"establish. No territory window was matched."
    )


METHODOLOGY_PC_003_STATE_LIMB = (
    "REAO (Rückerstattungsanordnung) Art. 3. This flag rests on the record's "
    "OWN `transaction_state`, which names a persecution-related transfer "
    "directly, and not on any territory window — no persecution-onset table "
    "entry is cited for it, because none was matched. Where a tier is given it "
    "is either the state's own REAO definition or the transfer's position "
    "relative to 15 September 1935 (Nuremberg Laws); where the record's date "
    "cannot be placed on one side of that threshold, no tier is asserted."
)


def rule_persecution_window(
    chain: ObjectChain, table: PersecutionOnsetTable
) -> tuple[list[dict[str, object]], list[str]]:
    """Returns (flags, skip_notes). One flag per (record, territory) engagement.

    TWO LIMBS, and the second exists because the first is not reachable from
    every record that states a persecution transfer:

    1. the transfer's date overlaps a territory's persecution window;
    2. the record's OWN `transaction_state` records a persecution transfer,
       whatever the territory.

    Without limb 2, `transaction_state` drove no rule at all: it was consulted
    to pick a tier *inside* limb 1's gate, so a state could only ever select a
    tier it could not itself reach. A recorded `zwangsverkauf` in Geneva
    produced "screened, no criteria triggered" while an ordinary purchase in
    Munich flagged — the terminal state the no-score design exists to prevent,
    reached by another route. And Switzerland has no window and correctly
    never will, so every Swiss record was structurally unreachable.
    """
    flags: list[dict[str, object]] = []
    skips: list[str] = []
    matched_by_window: set[str] = set()
    undated_with_location = []
    unlocated = []
    settled = []

    for record in chain.records:
        # A recorded restitution or settlement is the resolution of a claim,
        # not a transfer to be triaged. Flagging it would contradict
        # resolved_status and reopen a matter the record says is closed.
        if is_resolved_state(record.transaction_state):
            settled.append(record.source_ref)
            continue
        if record.date_span is None:
            if record.location:
                undated_with_location.append(record.source_ref)
            continue
        territories = table.match(record.location)
        if not territories:
            if record.location is None:
                unlocated.append(record.source_ref)
            continue

        for territory in territories:
            for window in territory.windows:
                certainty = record.date_span.overlap_certainty(*window.bounds)
                if certainty is None:
                    continue

                tier, tier_basis = _tier_for_window(record, table, window)
                cited = record.cite(
                    "owner_name", "date_span", "location", "transaction_state"
                )
                cited.update(territory.cite(window))
                cited["overlap_certainty"] = certainty
                cited["presumption_tier_basis"] = tier_basis
                cited["onset_table_version"] = table.version
                if window.onset_is_a_range:
                    cited["onset_caveat"] = (
                        "the onset date for this window is itself a range in the "
                        "source table; no single day is asserted"
                    )
                if record.transaction_state is not None:
                    cited["transaction_state_gloss"] = STATE_GLOSSES[
                        record.transaction_state
                    ]

                hedge = (
                    "overlaps"
                    if certainty == CERTAIN
                    else "may overlap (the stated date precision does not settle it)"
                )
                statement = (
                    f"Unverified: was this transfer made by a persecuted person, and "
                    f"if so was fair value received and the transferor free to "
                    f"dispose of the proceeds? The transfer's recorded date {hedge} "
                    f"the persecution window for {territory.territory} (onset "
                    f"{window.onset_label}, running to {window.ends.isoformat()}). "
                    f"The record contains no evidence either way on those questions."
                )
                flags.append(_flag(RULE_PC_003, statement, cited, tier))
                matched_by_window.add(record.source_ref)

    # Limb 2. Only for records limb 1 did not already flag: a window match
    # already carries the state in its cited fields and, where the state is a
    # presumption definition, already took its tier from it.
    for record in chain.records:
        if is_resolved_state(record.transaction_state):
            continue
        if record.source_ref in matched_by_window:
            continue
        if record.transaction_state not in STATES_RECORDING_PERSECUTION:
            continue

        tier, tier_basis = _tier_from_state(record, table)
        cited = record.cite(
            "owner_name", "date_span", "location", "transaction_state"
        )
        cited["presumption_tier_basis"] = tier_basis
        cited["transaction_state_gloss"] = STATE_GLOSSES[record.transaction_state]
        cited["basis"] = (
            "the record's own transaction_state, independently of territory. No "
            "territory window was matched for this record — either its location "
            "is outside the onset table, or none is recorded."
        )
        statement = (
            f"Unverified: was fair value received for this transfer, and was the "
            f"transferor free to dispose of the proceeds? The record states its "
            f"own transaction type as {record.transaction_state.value!r} "
            f"({STATE_GLOSSES[record.transaction_state]}), which records a "
            f"persecution-related transfer on its face. The record contains no "
            f"evidence either way on those questions."
        )
        flags.append(
            _flag(
                RULE_PC_003,
                statement,
                cited,
                tier,
                methodology=METHODOLOGY_PC_003_STATE_LIMB,
            )
        )

    if settled:
        skips.append(
            f"{RULE_PC_003} not evaluated for {', '.join(settled)}: the record is "
            "itself a restitution or settlement, which resolves a claim rather "
            "than presenting one for triage."
        )
    if undated_with_location:
        skips.append(
            f"{RULE_PC_003} not evaluated for {', '.join(undated_with_location)}: "
            "record has a location but no date, so no window comparison is possible."
        )
    if unlocated:
        skips.append(
            f"{RULE_PC_003} not evaluated for {', '.join(unlocated)}: record has no "
            "`location`, and persecution onset is keyed to territory."
        )
    return flags, skips


# --------------------------------------------------------------------------
# Rule DQ-001 — missing source citation (documentation-quality axis)
# --------------------------------------------------------------------------


def rule_missing_source_citation(chain: ObjectChain) -> list[dict[str, object]]:
    flags = []
    for record in chain.records:
        if record.source_citation:
            continue
        cited = record.cite("owner_name", "date_span", "transaction_state")
        cited["source_citation"] = None
        statement = (
            "Unverified: what documentary source records this transaction? No "
            "`source_citation` is present. This is a records-completeness "
            "observation and says nothing about the object's persecution context."
        )
        flags.append(_flag(RULE_DQ_001, statement, cited))
    return flags


# --------------------------------------------------------------------------
# Rule PC-004 — object-class channel rule (class + date, not chain analysis)
# --------------------------------------------------------------------------


def matched_object_class(object_class: str | None) -> str | None:
    """The rule class an object_class value falls under, if any."""
    if not object_class:
        return None
    haystack = normalise_name(object_class)
    for label, terms in OBJECT_CLASS_RULE_TERMS.items():
        for term in terms:
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack):
                return label
    return None


def rule_object_class(
    chain: ObjectChain, config: ScreeningConfig
) -> tuple[list[dict[str, object]], list[str]]:
    table = config.onset_table
    if chain.object_class is None:
        return [], [
            f"{RULE_PC_004} skipped: no `object_class` recorded, so the "
            "object-class channel rule could not be evaluated."
        ]

    rule_class = matched_object_class(chain.object_class)
    if rule_class is None:
        return [], []
    notes: list[str] = []

    flags: list[dict[str, object]] = []
    band = table.global_band
    common = {
        "object_class": chain.object_class,
        "object_class_rule_group": rule_class,
        "screening_band": f"{band[0].isoformat()} to {band[1].isoformat()}",
        "basis": (
            "class and date alone; this rule deliberately does not require "
            "chain evidence or a location match"
        ),
    }

    for record in chain.records:
        if record.date_span is None or is_resolved_state(record.transaction_state):
            continue
        certainty = record.date_span.overlap_certainty(*band)
        if certainty is None:
            continue

        territories = table.match(record.location)
        if territories:
            window = territories[0].windows[0]
            for candidate in territories[0].windows:
                if record.date_span.overlap_certainty(*candidate.bounds):
                    window = candidate
                    break
            tier, tier_basis = _tier_for_window(record, table, window)
            tier_basis = f"{tier_basis} (territory: {territories[0].territory})"
        else:
            tier, tier_basis = _tier_by_nuremberg(record, table, band)
            tier_basis = (
                f"{tier_basis}; no territory matched `location`, so the tier "
                "follows the Nuremberg threshold rather than a territory window"
            )

        cited = record.cite("owner_name", "date_span", "location", "transaction_state")
        cited.update(common)
        cited["overlap_certainty"] = certainty
        cited["presumption_tier_basis"] = tier_basis
        statement = (
            f"Unverified: how did this object reach the institution? It is "
            f"recorded as {chain.object_class!r}, a class that moved through "
            f"confiscation channels leaving no ownership chain, and this record "
            f"falls inside the screening band. The record contains no evidence "
            f"either way about the channel it passed through."
        )
        flags.append(_flag(RULE_PC_004, statement, cited, tier))

    # The absent-chain limb makes the same inference PC-001 does, and it
    # inverts for the same reason: for an object created after the band, an
    # absent pre-1945 chain is not the trace of a confiscation channel, it is
    # arithmetic. Contemporary silver, studio ceramics, modern Judaica and
    # commemorative coins are ordinary holdings, and this rule has no location
    # requirement to thin them out.
    _, certainly_pre_1945 = _pre_1945_status(chain)
    postwar = chain.certainly_created_after(PRE_1945_THRESHOLD)
    if postwar and not certainly_pre_1945:
        notes.append(
            f"{RULE_PC_004} absent-chain limb not applicable: the object's "
            f"recorded creation date ({chain.object_date}) is on or after "
            f"{PRE_1945_THRESHOLD.isoformat()}, so the absence of a pre-1945 "
            f"chain is not evidence of a confiscation channel."
        )
    if not certainly_pre_1945 and not postwar:
        cited = dict(common)
        cited["records_in_chain"] = len(chain.records)
        cited["certainly_dated_before_1945"] = False
        cited["object_date"] = chain.object_date
        statement = (
            f"Unverified: what was this object's history before 1945? It is "
            f"recorded as {chain.object_class!r}, a class for which an absent "
            f"chain is the expected trace of a confiscation channel rather than "
            f"a neutral records gap. No record in the chain is certainly dated "
            f"before 1945."
        )
        flags.append(_flag(RULE_PC_004, statement, cited, PresumptionTier.NOT_APPLICABLE))

    return flags, notes


# --------------------------------------------------------------------------
# Rule PC-005 — confiscation-channel actor match
# --------------------------------------------------------------------------

# transaction_states that independently evidence persecution of a former
# owner, without relying on anything the actor list says.
PERSECUTION_EVIDENCING_STATES = frozenset(
    {
        TransactionState.ENTZIEHUNG,
        TransactionState.ZWANGSVERKAUF,
        TransactionState.VERMUTUNG_DER_ENTZIEHUNG,
        TransactionState.VERSCHAERFTE_VERMUTUNG,
        TransactionState.FLUCHTGUT,
    }
)


def rule_confiscation_actors(
    chain: ObjectChain, config: ScreeningConfig
) -> tuple[list[dict[str, object]], list[str]]:
    if config.actor_list is None:
        return [], [
            f"{RULE_PC_005} skipped: "
            f"{config.unavailable.get(RULE_PC_005, 'actor list unavailable')}"
        ]

    flags: list[dict[str, object]] = []
    skips: list[str] = []
    undated: list[str] = []

    for record in chain.records:
        if is_resolved_state(record.transaction_state):
            continue
        hits = config.actor_list.match(record.owner_names)
        if not hits:
            continue
        if record.date_span is None:
            undated.append(record.source_ref)
            continue
        certainty = record.date_span.overlap_certainty(*ACTOR_WINDOW)
        if certainty is None:
            continue

        for actor, matched_name, outcome in hits:
            cited = record.cite("owner_name", "date_span", "location", "transaction_state")
            cited["matched_on"] = matched_name
            cited["match_basis"] = outcome.basis
            # Stated on every match, not only when a caveat fires: whether the
            # entry names a person or an organisation is what tells a reader
            # which sentence they are being told, and a confident match
            # otherwise leaves it invisible.
            cited["entry_kind"] = outcome.entry_kind
            cited["identity_confirmed"] = outcome.identity_confirmed
            if outcome.note:
                cited["identity_caveat"] = outcome.note
            cited.update(actor.cite())
            cited["actor_window"] = (
                f"{ACTOR_WINDOW[0].isoformat()} to {ACTOR_WINDOW[1].isoformat()}"
            )
            cited["overlap_certainty"] = certainty
            cited["citation_disclaimer"] = ACTOR_CITATION_DISCLAIMER

            # The gate. A list entry whose documented basis does not itself
            # bear on persecution of a private former owner may not be
            # rendered as though it did, unless this record's own
            # transaction_state establishes it independently.
            entry_implies = actor.has_persecution_implying_basis
            state_implies = record.transaction_state in PERSECUTION_EVIDENCING_STATES

            if entry_implies:
                tier, tier_basis = _tier_by_nuremberg(record, config.onset_table, ACTOR_WINDOW)
                cited["persecution_inference_basis"] = (
                    "a documented basis on the matched entry bears on persecution "
                    "of a former owner"
                )
                statement = (
                    f"Unverified: what were the circumstances of this transaction? "
                    f"A party to it matches the confiscation-channel entry for "
                    f"{actor.name}, whose documented basis is set out in the cited "
                    f"fields. The match cites that record and makes no finding "
                    f"about this transaction."
                )
            elif state_implies:
                tier, tier_basis = _tier_by_nuremberg(record, config.onset_table, ACTOR_WINDOW)
                cited["persecution_inference_basis"] = (
                    "the matched entry's documented basis does NOT bear on "
                    "persecution of a private former owner; the persecution "
                    "question here rests solely on this record's own "
                    f"transaction_state ({record.transaction_state.value})"
                )
                statement = (
                    f"Unverified: what were the circumstances of this transaction? "
                    f"A party matches the confiscation-channel entry for "
                    f"{actor.name}. That entry's documented basis does not itself "
                    f"bear on persecution of a private former owner — the "
                    f"persecution question arises from this record's own "
                    f"transaction_state, independently of the match."
                )
            else:
                tier = PresumptionTier.NOT_APPLICABLE
                tier_basis = "not asserted — see persecution_inference_basis"
                cited["persecution_inference_basis"] = (
                    "the matched entry's documented basis does not bear on "
                    "persecution of a private former owner, and this record's "
                    "transaction_state does not independently establish it"
                )
                statement = (
                    f"Recorded for context, not as a persecution indicator: a party "
                    f"to this transaction matches the confiscation-channel entry for "
                    f"{actor.name}. On the documented basis cited below, this match "
                    f"does NOT indicate persecution of an individual former owner, "
                    f"and nothing in this record independently suggests it. The "
                    f"open question is what the cited basis covers, not who this "
                    f"object was taken from."
                )
            cited["presumption_tier_basis"] = tier_basis
            if not outcome.identity_confirmed:
                # Same reasoning as NM-001: the qualifier goes in the sentence
                # a reader actually reads, not only in a key beneath it.
                statement += (
                    " IDENTITY NOT ESTABLISHED: the record gives a surname with "
                    "no given name, and the entry names an individual, so a "
                    "different person of the same surname is equally consistent "
                    "with this record."
                )
            flags.append(_flag(RULE_PC_005, statement, cited, tier))

    if undated:
        skips.append(
            f"{RULE_PC_005} not evaluated for {', '.join(undated)}: a party matches "
            "the actor list but the record carries no date, and the rule is scoped "
            f"to {ACTOR_WINDOW[0].year}-{ACTOR_WINDOW[1].year}."
        )
    return flags, skips


# --------------------------------------------------------------------------
# Rule PC-006 — anonymous provenance entry inside the band
# --------------------------------------------------------------------------


def rule_anonymous_entry(
    chain: ObjectChain, config: ScreeningConfig
) -> tuple[list[dict[str, object]], list[str]]:
    patterns = config.anonymous_patterns
    if patterns is None:
        return [], [
            f"{RULE_PC_006} skipped: "
            f"{config.unavailable.get(RULE_PC_006, 'pattern list unavailable')}"
        ]

    band = (patterns.band_from, patterns.band_to)
    flags: list[dict[str, object]] = []
    undated: list[str] = []

    for record in chain.records:
        if is_resolved_state(record.transaction_state):
            continue
        hits = patterns.match(record.owner_names)
        if not hits:
            continue
        if record.date_span is None:
            undated.append(record.source_ref)
            continue
        certainty = record.date_span.overlap_certainty(*band)
        if certainty is None:
            continue

        matched_pattern, matched_name = hits[0]
        cited = record.cite("owner_name", "owner_name_variants", "date_span", "location")
        cited["matched_pattern"] = matched_pattern
        cited["matched_on"] = matched_name
        cited["band"] = f"{band[0].isoformat()} to {band[1].isoformat()}"
        cited["band_source"] = patterns.source
        cited["pattern_list_version"] = patterns.version
        cited["overlap_certainty"] = certainty
        tier, tier_basis = _tier_by_nuremberg(record, config.onset_table, band)
        cited["presumption_tier_basis"] = tier_basis
        statement = (
            f"Unverified: who actually held this object at this point? The owner "
            f"entry is anonymous or generic ({matched_name!r}, matching the "
            f"configured pattern {matched_pattern!r}) and falls inside the "
            f"{band[0].year}-{band[1].year} band. The record does not name a party."
        )
        flags.append(_flag(RULE_PC_006, statement, cited, tier))

    skips = []
    if undated:
        skips.append(
            f"{RULE_PC_006} not evaluated for {', '.join(undated)}: the owner entry "
            "is anonymous but the record carries no date, and the rule is scoped to "
            f"{band[0].year}-{band[1].year}."
        )
    return flags, skips


# --------------------------------------------------------------------------
# Rule PC-007 — deleted owner across two catalogue records
# --------------------------------------------------------------------------


def rule_deleted_owner(chain: ObjectChain) -> tuple[list[dict[str, object]], list[str]]:
    catalogue_records = [r for r in chain.records if r.catalogue_reference]
    if not catalogue_records:
        return [], [
            f"{RULE_PC_007} skipped: no record carries a `catalogue_reference`, so "
            "there is no published entry to compare across time."
        ]

    unstated = [
        r.source_ref for r in catalogue_records if r.owner_stated_in_catalogue is None
    ]

    early = [
        r
        for r in catalogue_records
        if r.owner_stated_in_catalogue
        and r.date_span is not None
        and r.date_span.certainly_before is not None
        and r.date_span.certainly_before < DELETED_OWNER_EARLY_BEFORE
    ]
    late = [
        r
        for r in catalogue_records
        if r.date_span is not None
        and r.date_span.earliest is not None
        and r.date_span.earliest > DELETED_OWNER_LATE_AFTER
    ]
    late_without_owner = [r for r in late if r.owner_stated_in_catalogue is False]

    skips: list[str] = []
    if unstated:
        skips.append(
            f"{RULE_PC_007} partially skipped: catalogue record(s) "
            f"{', '.join(unstated)} do not set `owner_stated_in_catalogue`, so "
            "whether the published entry named an owner is unrecorded."
        )
    if not early:
        return [], skips

    # A later entry that still names the owner is a completed comparison with
    # a negative result, not an impossible one — the "unverifiable" limb is
    # only for the case where no post-1950 published entry exists at all.
    if late and not late_without_owner:
        return [], skips

    if not late_without_owner:
        cited = {
            "pre_1933_catalogue_entries": [
                {
                    "source_ref": r.source_ref,
                    "owner_name": r.owner_name,
                    "catalogue_reference": r.catalogue_reference,
                    "date": r.date_span.describe(),
                }
                for r in early
            ],
            "post_1950_catalogue_entry_present": False,
            "comparison_thresholds": (
                f"published before {DELETED_OWNER_EARLY_BEFORE.isoformat()}, "
                f"compared against an entry after {DELETED_OWNER_LATE_AFTER.isoformat()}"
            ),
        }
        statement = (
            "Unverifiable, requires catalogue raisonné cross-check: an owner is "
            "named in a pre-1933 published entry for this object, but the records "
            "supplied contain no post-1950 published entry to compare against. "
            "Whether the name was later dropped cannot be determined here."
        )
        return [
            _flag(RULE_PC_007, statement, cited, PresumptionTier.NOT_APPLICABLE)
        ], skips

    cited = {
        "pre_1933_catalogue_entries": [
            {
                "source_ref": r.source_ref,
                "owner_name": r.owner_name,
                "catalogue_reference": r.catalogue_reference,
                "date": r.date_span.describe(),
            }
            for r in early
        ],
        "post_1950_catalogue_entries_without_owner": [
            {
                "source_ref": r.source_ref,
                "catalogue_reference": r.catalogue_reference,
                "date": r.date_span.describe(),
                "owner_stated_in_catalogue": False,
            }
            for r in late_without_owner
        ],
        "comparison_thresholds": (
            f"published before {DELETED_OWNER_EARLY_BEFORE.isoformat()}, "
            f"compared against an entry after {DELETED_OWNER_LATE_AFTER.isoformat()}"
        ),
    }
    named = ", ".join(sorted({r.owner_name for r in early}))
    statement = (
        f"Unverified: why is an owner named in a pre-1933 published entry "
        f"({named}) absent from the post-1950 published entry for the same "
        f"object? The record establishes that a name was once published and is "
        f"no longer carried; it does not establish why."
    )
    return [_flag(RULE_PC_007, statement, cited, PresumptionTier.NOT_APPLICABLE)], skips


# --------------------------------------------------------------------------
# Rule PC-008 — restitution recorded to a state rather than to heirs
# --------------------------------------------------------------------------


def rule_restitution_mismatch(
    chain: ObjectChain,
) -> tuple[list[dict[str, object]], list[str]]:
    settled = [r for r in chain.records if is_resolved_state(r.transaction_state)]
    if not settled:
        return [], []

    flags: list[dict[str, object]] = []
    skips: list[str] = []
    for record in settled:
        recipient = record.restitution_recipient_type
        if recipient is None:
            skips.append(
                f"{RULE_PC_008} skipped for {record.source_ref}: the record is a "
                "restitution or settlement but does not set "
                "`restitution_recipient_type`, so whether it ran to a state or to "
                "individuals cannot be determined."
            )
            continue
        if recipient is RestitutionRecipientType.INDIVIDUAL_OR_HEIRS:
            continue
        if recipient is RestitutionRecipientType.UNKNOWN:
            skips.append(
                f"{RULE_PC_008} not determinable for {record.source_ref}: "
                "`restitution_recipient_type` is recorded as `unknown`."
            )
            continue

        cited = record.cite(
            "owner_name", "date_span", "location", "transaction_state",
            "restitution_recipient_type",
        )
        statement = (
            "Unverified: did the dispossessed individuals or their heirs ever "
            "receive this object or its value? The settlement is recorded as made "
            "to a state or institution. External restitution to a state frequently "
            "succeeded where internal restitution to the family did not; the record "
            "does not show what followed."
        )
        flags.append(_flag(RULE_PC_008, statement, cited, PresumptionTier.NOT_APPLICABLE))
    return flags, skips


# --------------------------------------------------------------------------
# Rule PC-009 — cross-border movement 1933-1945 without an export licence
# --------------------------------------------------------------------------


def rule_missing_export_licence(
    chain: ObjectChain, config: ScreeningConfig
) -> tuple[list[dict[str, object]], list[str]]:
    dated = [r for r in chain.records if r.is_dated]
    if len(dated) < 2:
        return [], []

    flags: list[dict[str, object]] = []
    unlocated: list[str] = []

    for previous, current in zip(dated, dated[1:]):
        if is_resolved_state(current.transaction_state):
            continue
        origin, destination = previous.country, current.country
        if origin is None or destination is None:
            unlocated.append(f"{previous.source_ref} -> {current.source_ref}")
            continue
        if normalise_name(origin) == normalise_name(destination):
            continue
        certainty = current.date_span.overlap_certainty(*EXPORT_LICENCE_WINDOW)
        if certainty is None:
            continue
        if current.export_licence_present:
            continue

        cited = current.cite(
            "owner_name", "date_span", "location", "transaction_state",
            "export_licence_present",
        )
        cited["moved_from"] = previous.location
        cited["moved_from_source_ref"] = previous.source_ref
        cited["origin_country"] = origin
        cited["destination_country"] = destination
        cited["country_derivation"] = (
            "country taken as the final comma-separated component of `location`; "
            "where the value carries no comma the whole value is used"
        )
        cited["window"] = (
            f"{EXPORT_LICENCE_WINDOW[0].isoformat()} to "
            f"{EXPORT_LICENCE_WINDOW[1].isoformat()}"
        )
        cited["overlap_certainty"] = certainty
        cited["export_licence_state"] = (
            "recorded as absent"
            if current.export_licence_present is False
            else "not recorded either way"
        )
        tier, tier_basis = _tier_by_nuremberg(
            current, config.onset_table, EXPORT_LICENCE_WINDOW
        )
        cited["presumption_tier_basis"] = tier_basis
        statement = (
            f"Unverified: under what authority did this object cross a border? The "
            f"chain moves from {origin} to {destination} on a record dated inside "
            f"{EXPORT_LICENCE_WINDOW[0].year}-{EXPORT_LICENCE_WINDOW[1].year}, and "
            f"no export licence is recorded."
        )
        flags.append(_flag(RULE_PC_009, statement, cited, tier))

    skips = []
    if unlocated:
        skips.append(
            f"{RULE_PC_009} not evaluated for {', '.join(unlocated)}: `location` is "
            "missing on one side of the movement, so no border crossing can be "
            "established."
        )
    return flags, skips


# --------------------------------------------------------------------------
# Rule DQ-002 — post-commitment acquisition with a thin pre-1945 chain
# --------------------------------------------------------------------------


def rule_post_commitment_acquisition(
    chain: ObjectChain, config: ScreeningConfig
) -> tuple[list[dict[str, object]], list[str]]:
    acquisitions = [r for r in chain.records if r.is_institution_acquisition]
    if not acquisitions:
        if all(r.is_institution_acquisition is None for r in chain.records):
            return [], [
                f"{RULE_DQ_002} skipped: no record sets "
                "`is_institution_acquisition`, so the acquisition date is unknown."
            ]
        return [], []

    flags: list[dict[str, object]] = []
    undated: list[str] = []
    for record in acquisitions:
        if record.date_span is None:
            undated.append(record.source_ref)
            continue
        # Only an acquisition certainly after the commitment date counts; an
        # imprecise date that merely might postdate it is reported, not assumed.
        earliest = record.date_span.earliest
        if earliest is None or earliest <= config.commitment_date:
            continue
        _, certainly_pre_1945 = _pre_1945_status(chain)
        if certainly_pre_1945:
            continue

        cited = record.cite("owner_name", "date_span", "transaction_state")
        cited["commitment_date_applied"] = config.commitment_date.isoformat()
        cited["commitment_date_source"] = config.commitment_date_source
        cited["commitment_date_is_default"] = config.commitment_date_is_default
        cited["certainly_dated_before_1945"] = False
        cited["records_in_chain"] = len(chain.records)
        statement = (
            f"Unverified: what pre-1945 provenance was established before this "
            f"object was acquired? The institution acquired it after the "
            f"commitment date applied "
            f"({config.commitment_date.isoformat()}), and no record in the chain "
            f"is certainly dated before 1945. This concerns the acquisition "
            f"process, not the object's own history."
        )
        flags.append(_flag(RULE_DQ_002, statement, cited))

    skips = []
    if undated:
        skips.append(
            f"{RULE_DQ_002} not evaluated for {', '.join(undated)}: the acquisition "
            "record carries no date, so it cannot be placed relative to the "
            "commitment date."
        )
    return flags, skips


# --------------------------------------------------------------------------
# Rules NM-001 / NM-002 — ALIU Red Flag Names List
# --------------------------------------------------------------------------


def rule_aliu_name_match(
    chain: ObjectChain, config: ScreeningConfig
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Returns (concern flags, exonerating citations, skip notes).

    The two outputs are separate structures, not one list with a type field,
    because an exonerating entry must not be capable of being rendered,
    counted or filtered as a flag anywhere downstream.
    """
    if config.aliu_list is None:
        return [], [], [
            f"{RULE_NM_001} skipped: "
            f"{config.unavailable.get(RULE_NM_001, 'ALIU list unavailable')}"
        ]

    aliu = config.aliu_list
    band = config.onset_table.global_band
    flags: list[dict[str, object]] = []
    citations: list[dict[str, object]] = []
    skips: list[str] = []

    for record in chain.records:
        hits = aliu.match(record.owner_names)
        if not hits:
            continue
        # The date gate. Without it a 2019 acquisition from a dealer who
        # happens to share a surname with a 1946 investigative record raises a
        # flag about a named third party. Every other name-bearing rule gates
        # on persecution context; this one is the rule where an ungated match
        # is most costly, so it gates too.
        if record.date_span is None:
            skips.append(
                f"{RULE_NM_001} not applied to {record.source_ref}: the record "
                f"carries no date, so it could not be placed inside or outside "
                f"the screening band {band[0].isoformat()} to {band[1].isoformat()}. "
                f"A name match was NOT evaluated for this record — this is a "
                f"criterion that could not run, not one that ran and found nothing."
            )
            continue
        if record.date_span.overlap_certainty(*band) is None:
            continue

        for entry, matched_name, outcome in hits:
            cited = record.cite("owner_name", "owner_name_variants", "date_span")
            cited["matched_on"] = matched_name
            cited["match_basis"] = outcome.basis
            # Stated on every match, not only when a caveat fires: whether the
            # entry names a person or an organisation is what tells a reader
            # which sentence they are being told, and a confident match
            # otherwise leaves it invisible.
            cited["entry_kind"] = outcome.entry_kind
            cited["identity_confirmed"] = outcome.identity_confirmed
            if outcome.note:
                cited["identity_caveat"] = outcome.note
            cited["screening_band"] = (
                f"{band[0].isoformat()} to {band[1].isoformat()}"
            )
            cited.update(entry.cite())
            cited["citation_disclaimer"] = ALIU_CITATION_DISCLAIMER
            if aliu.critical_scholarly_context:
                cited["critical_scholarly_context"] = aliu.critical_scholarly_context

            if entry.is_exonerating:
                citations.append(
                    {
                        "rule_id": RULE_NM_002,
                        "rule_statement": (
                            f"Name matches the ALIU Red Flag Names List entry for "
                            f"{entry.name}, which is an EXONERATING entry: the "
                            f"list's own annotation records this name in terms that "
                            f"do not raise a concern. Read that annotation, quoted "
                            f"in full below, rather than the fact of the match. "
                            f"This is context only — it is not a flag, it does not "
                            f"place this object in the work queue, and it must not "
                            f"be read or reported as an unexamined concern."
                        ),
                        "methodology": METHODOLOGY[RULE_NM_002],
                        "cited_fields": cited,
                        "entry_type": entry.entry_type,
                        "llm_explanation": None,
                    }
                )
                continue

            # The caveat belongs in the headline sentence, not only in a key
            # below it. This statement is what a reader reads and what the LLM
            # layer restates; an identity qualifier that lives in cited_fields
            # is exactly the "prose a reader may not reach" this project
            # refuses to rely on everywhere else.
            identity = (
                ""
                if outcome.identity_confirmed
                else (
                    " IDENTITY NOT ESTABLISHED: the record gives a surname with "
                    "no given name, so a different individual of the same "
                    "surname is equally consistent with it. This match does not "
                    "say the record's party is the person the entry describes."
                )
            )
            # Entry names often end in an initial ("Lange, Hans W."), so the
            # sentence supplies its own full stop only when one is needed.
            entry_name = entry.name.rstrip(".")
            statement = (
                f"Name matches the ALIU Red Flag Names List entry for "
                f"{entry_name}.{identity} See the cited source and the list's "
                f"own annotation, quoted in full below. "
                f"{ALIU_CITATION_DISCLAIMER} Unverified: what, if anything, "
                f"connects this named party to this object's history?"
            )
            flags.append(
                _flag(RULE_NM_001, statement, cited, PresumptionTier.NOT_APPLICABLE)
            )

    return flags, citations, skips


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _resolved_records(chain: ObjectChain) -> list[ProvenanceRecord]:
    return [r for r in chain.records if is_resolved_state(r.transaction_state)]


def screen_object(chain: ObjectChain, config: ScreeningConfig) -> dict[str, object]:
    """Run the full rule set over one chain and return its output record."""
    coverage: list[str] = []
    table = config.onset_table

    pc_001_flags, pc_001_note = rule_no_pre_1945_provenance(chain)
    persecution_flags = list(pc_001_flags)
    if pc_001_note:
        coverage.append(pc_001_note)

    acquisition_flags, acquisition_skip = rule_chain_begins_at_acquisition(chain)
    persecution_flags.extend(acquisition_flags)
    if acquisition_skip:
        coverage.append(acquisition_skip)

    window_flags, window_skips = rule_persecution_window(chain, table)
    persecution_flags.extend(window_flags)
    coverage.extend(window_skips)

    fluchtgut_flags, fluchtgut_skips = rule_fluchtgut(chain)
    persecution_flags.extend(fluchtgut_flags)
    coverage.extend(fluchtgut_skips)

    for rule in (
        lambda: rule_object_class(chain, config),
        lambda: rule_confiscation_actors(chain, config),
        lambda: rule_anonymous_entry(chain, config),
        lambda: rule_deleted_owner(chain),
        lambda: rule_restitution_mismatch(chain),
        lambda: rule_missing_export_licence(chain, config),
    ):
        rule_flags, rule_skips = rule()
        persecution_flags.extend(rule_flags)
        coverage.extend(rule_skips)

    name_flags, name_citations, name_skips = rule_aliu_name_match(chain, config)
    persecution_flags.extend(name_flags)
    coverage.extend(name_skips)

    documentation_flags = rule_missing_source_citation(chain)
    commitment_flags, commitment_skips = rule_post_commitment_acquisition(chain, config)
    documentation_flags.extend(commitment_flags)
    coverage.extend(commitment_skips)

    possibly_pre_1945, certainly_pre_1945 = _pre_1945_status(chain)
    if possibly_pre_1945 and not certainly_pre_1945:
        coverage.append(
            f"{RULE_PC_001} did not trigger, but the object's pre-1945 coverage "
            "rests only on imprecisely dated records; no record is certainly "
            "dated before 1945."
        )

    resolved = _resolved_records(chain)
    if resolved:
        coverage.append(
            "Chain records `restitution_erfolgt_or_vergleich` at "
            f"{', '.join(r.source_ref for r in resolved)}: this object is marked "
            "previously resolved and is held out of the active triage queue. Flags "
            "below are retained for the record and must not be treated as reopening "
            "a settled matter."
        )

    undated = [r.source_ref for r in chain.records if not r.is_dated]
    if undated:
        coverage.append(
            f"Undated records in chain ({', '.join(undated)}): all date-based "
            "criteria were skipped for these records."
        )

    # A seed list makes a *non*-match nearly uninformative. Saying so on every
    # object is the point: otherwise "no name match" reads as a clear result.
    if config.aliu_list is not None and config.aliu_list.is_seed:
        coverage.append(
            f"{RULE_NM_001} ran against a list that declares itself incomplete "
            f"({config.aliu_list.status}; {len(config.aliu_list.entries)} entries "
            "loaded). The absence of a name match is NOT evidence that a name is "
            "absent from the full ALIU Red Flag Names List."
        )
    if config.actor_list is not None and config.actor_list.is_seed:
        coverage.append(
            f"{RULE_PC_005} ran against a list that declares itself non-exhaustive "
            f"({config.actor_list.status}; {len(config.actor_list.actors)} entries "
            "loaded). The absence of an actor match is NOT evidence that no "
            "confiscation-channel actor was involved."
        )

    ran = [c["rule_id"] for c in CRITERIA if c["rule_id"] not in config.unavailable]
    coverage.append(f"Criteria evaluated: {', '.join(ran)}.")

    triggered = len(persecution_flags) + len(documentation_flags)
    statement = (
        SCREENED_NO_FLAGS_STATEMENT.format(n=len(ran))
        if triggered == 0
        else SCREENED_WITH_FLAGS_STATEMENT.format(n=len(ran), triggered=triggered)
    )

    return {
        "object_id": chain.object_id,
        "object_title": chain.object_title,
        "object_class": chain.object_class,
        "screening_statement": statement,
        "persecution_context_flags": persecution_flags,
        "documentation_quality_flags": documentation_flags,
        # Exonerating name-list matches. Deliberately not a flag list: an
        # exonerating entry must not be countable, filterable or renderable
        # as a concern anywhere downstream.
        "name_list_citations": name_citations,
        "resolved_status": "previously_resolved" if resolved else "unresolved",
        "coverage_note": " ".join(coverage),
    }


def coverage_map(
    chains: list[ObjectChain], results: list[dict[str, object]]
) -> dict[str, object]:
    """The first screen: what proportion of the collection is documented at all.

    Counts only. This is a scoping aid for a researcher, not an assessment of
    any object.
    """
    with_pre_1945 = 0
    certain_pre_1945 = 0
    without_pre_1945 = 0
    begins_at_acquisition = 0
    acquisition_undeterminable = 0
    previously_resolved = 0
    created_after_band = 0
    creation_date_unrecorded = 0

    for chain, result in zip(chains, results):
        possibly, certainly = _pre_1945_status(chain)
        if not chain.object_date:
            creation_date_unrecorded += 1
        if possibly:
            with_pre_1945 += 1
            if certainly:
                certain_pre_1945 += 1
        elif chain.certainly_created_after(PRE_1945_THRESHOLD):
            # Counted apart from `with_no_pre_1945_provenance`: an object that
            # postdates the band has nothing missing, and folding it into the
            # gap count overstates the gap on the first screen.
            created_after_band += 1
        else:
            without_pre_1945 += 1
        if any(
            f["rule_id"] == RULE_PC_002 for f in result["persecution_context_flags"]
        ):
            begins_at_acquisition += 1
        elif all(r.is_institution_acquisition is None for r in chain.records):
            acquisition_undeterminable += 1
        if result["resolved_status"] == "previously_resolved":
            previously_resolved += 1

    return {
        "objects_processed": len(chains),
        "with_any_pre_1945_provenance": with_pre_1945,
        "of_which_certainly_dated_pre_1945": certain_pre_1945,
        "with_no_pre_1945_provenance": without_pre_1945,
        "created_after_the_risk_band": created_after_band,
        "creation_date_not_recorded": creation_date_unrecorded,
        "chain_begins_at_institutional_acquisition": begins_at_acquisition,
        "institutional_acquisition_not_determinable": acquisition_undeterminable,
        "previously_resolved": previously_resolved,
        "note": (
            "Counts describe how much documentation exists, not how much risk. "
            "`institutional_acquisition_not_determinable` counts objects whose "
            "records do not mark which event is the institution's own accession, "
            "so that criterion could not run. `created_after_the_risk_band` "
            "counts objects whose own recorded creation date puts them after "
            "1945 — they are not missing documentation, they could not have "
            "any, and they are excluded from `with_no_pre_1945_provenance` for "
            "that reason. `creation_date_not_recorded` counts the objects for "
            "which that distinction could not be made at all."
        ),
    }
