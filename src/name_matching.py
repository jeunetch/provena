"""Name matching against bundled public reference lists.

Two lists, one mechanism:

* the **ALIU Red Flag Names List** (OSS Art Looting Investigation Unit, 1946)
  — rule NM-001 / NM-002;
* the **confiscation-channel actor list** — rule PC-005.

Everything here renders as a *citation*, never as a characterization of a
person. A match says a name appears in a named historical record and nothing
else; it carries no finding as to any transaction, and the rendering says so
in terms. This is a legal-exposure mitigation, not house style: personality
rights under Swiss ZGB Art. 28 and potentially StGB Art. 173 attach to living
heirs and descendants, and Germany recognises a post-mortem personality
right. Do not soften the disclaimer wording or drop the source fields.

Three distinctions the matching logic itself must carry, rather than leaving
to prose a reader may not reach:

1. **Exonerating entries are not flags.** A list entry marked
   `entry_type: "exonerating"` records that a name was investigated and
   cleared, or that the person acted protectively. Surfacing that as a
   "flag" would invert its meaning and defame the subject. Exonerating
   matches leave this module as citations on their own channel and never
   enter the flag lists.
2. **Per-entry basis gating.** An actor entry may carry several
   `documented_basis` limbs of differing strength and differing implication.
   A limb whose `implies_persecution_of_former_owner` is false must not
   produce a flag that reads as persecution of an individual unless the
   record's own `transaction_state` independently supports it.
3. **Source strength travels with the match.** An entry carrying a
   `verification_note` renders that note next to every match, so a
   weaker-sourced entry never reads with the confidence of a
   well-documented one.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_ALIU_PATH = DATA_DIR / "aliu_red_flag_names.json"
DEFAULT_ACTORS_PATH = DATA_DIR / "confiscation_channel_actors.json"

ENTRY_DOCUMENTED_CONCERN = "documented_concern"
ENTRY_EXONERATING = "exonerating"

ALIU_CITATION_DISCLAIMER = (
    "This indicates only that the name appears in a 1946 Allied investigative "
    "record. It carries no finding as to any specific transaction, and no "
    "characterization of the named person."
)

ACTOR_CITATION_DISCLAIMER = (
    "This indicates only that the name matches an entry in the bundled "
    "confiscation-channel actor list. It carries no finding as to this "
    "transaction, and no characterization of the named business or person."
)


class ReferenceListError(Exception):
    """Raised when a bundled list is missing or does not match its schema."""


def normalise(text: str) -> str:
    """Casefold, strip diacritics, and collapse whitespace and punctuation."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    punctuation_to_space = re.sub(r"[^\w\s]+", " ", lowered)
    # Collapsing runs is not cosmetic: "Lange, Hans W." leaves a double space
    # where the comma was, so "hans w lange" would never match "lange  hans w".
    return re.sub(r"\s+", " ", punctuation_to_space).strip()


# Tokens too generic to identify an entry on their own. A record naming only
# "Galerie" or only a city must not match an entry.
GENERIC_TOKENS = frozenset(
    {
        "galerie", "gallery", "galeries", "kunsthaus", "kunsthandlung",
        "kunstversteigerungshaus", "auktionshaus", "auction", "house", "haus",
        "maison", "casa", "the", "and", "und", "von", "van", "der", "die", "de",
        "munich", "munchen", "munchener", "vienna", "wien", "berlin", "cologne",
        "koln", "lucerne", "luzern", "paris", "linz", "prague", "prag",
        "special", "commission", "taskforce", "estate", "heirs", "collection",
    }
)


# --------------------------------------------------------------------------
# Personal-name identity
#
# A shared surname is not a shared identity. Matching on the surname alone
# prints a 1946 investigative annotation about one person beside the name of
# a different person who happens to share it — "Lange, Elisabeth" against
# "Lange, Hans W." — and Fischer, Lange and Wolff are not exotic surnames in a
# Swiss collection, they are the modal case.
#
# The asymmetry that justifies over-flagging elsewhere does NOT hold here, and
# this is the one place in the tool where that must be said explicitly. A
# false positive on a persecution-window rule costs a researcher an hour. A
# false positive here is an assertion about a named third party, who may have
# living heirs, inside a document the institution circulates. Those are not
# the same cost and must not share a threshold.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonName:
    """A personal name split into the parts identity actually depends on."""

    surname: str
    given: tuple[str, ...]

    def compatible_with(self, other: "PersonName") -> bool:
        """Whether two given-name sets can denote the same person.

        Only the FIRST given name is compared, treating a single letter as an
        initial: "Lange, H." is compatible with "Lange, Hans W.", and
        "Lange, Elisabeth" is not.

        Comparing any token against any token is too loose, and loose in the
        expensive direction: the entry's middle initial "W." would otherwise
        make "Lange, Werner" a match for "Lange, Hans W.", which is a
        different person. A middle initial cannot carry an identification on
        its own.
        """
        if not self.given or not other.given:
            return False
        mine, theirs = self.given[0], other.given[0]
        if mine == theirs:
            return True
        if len(mine) == 1:
            return theirs.startswith(mine)
        if len(theirs) == 1:
            return mine.startswith(theirs)
        return False


def parse_person_name(raw: str) -> PersonName | None:
    """Parse a value into surname + given names, or None if it is not a person.

    Three accepted shapes, all conservative — anything else returns None and
    is treated as an organisation, because guessing that a business name is a
    person is how a company's town ends up compared against a given name.
    """
    if "," in raw:
        surname, _, given = raw.partition(",")
        surname_n = normalise(surname)
        given_n = tuple(t for t in normalise(given).split() if t)
        if surname_n and given_n:
            return PersonName(surname_n, given_n)
        return None

    tokens = normalise(raw).split()
    if any(token in GENERIC_TOKENS for token in tokens):
        return None
    if len(tokens) == 2:
        return PersonName(tokens[1], (tokens[0],))
    # "Hans W. Lange" — exactly one initial, so the shape is unambiguous.
    if len(tokens) == 3 and sum(len(t) == 1 for t in tokens) == 1:
        return PersonName(tokens[2], tuple(tokens[:2]))
    return None


def _surname_first_swap(name: str) -> str:
    """'Dettwiler, R.' -> 'r dettwiler', so both orderings match."""
    if "," not in name:
        return ""
    tail, head = name.split(",", 1)
    return normalise(f"{head} {tail}")


def candidate_forms(name: str) -> set[str]:
    """Normalised forms a single supplied name should be matched under."""
    forms = {normalise(name)}
    swapped = _surname_first_swap(name)
    if swapped:
        forms.add(swapped)
    # A dotted acronym normalises to single letters — "E.R.R." becomes
    # "e r r" — and would otherwise never reach the "err" entry, because
    # every token is below the single-token length floor.
    tokens = normalise(name).split()
    if len(tokens) > 1 and all(len(token) == 1 for token in tokens):
        forms.add("".join(tokens))
    return {f for f in forms if f}


def _contains_term(haystack: str, term: str) -> bool:
    """Word-boundary containment on already-normalised strings."""
    if not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack) is not None


@dataclass(frozen=True)
class MatchTerm:
    """One normalised form an entry can be recognised by.

    `allow_single_token` marks a term a *shorter* supplied name may match into
    — a surname, or an organisation's distinctive word. A full personal name
    is not one: without this, a record owned by any "Franz" would match an
    entry for "Wolff Metternich, Franz".
    """

    text: str
    allow_single_token: bool


def _build_terms(name: str, variants: tuple[str, ...]) -> tuple[MatchTerm, ...]:
    terms: dict[str, bool] = {}

    def add(raw: str, single_ok: bool) -> None:
        text = normalise(raw)
        if not text:
            return
        terms[text] = terms.get(text, False) or single_ok

    for raw in (name, *variants):
        add(raw, len(normalise(raw).split()) == 1)

    if "," in name:
        # "Surname, First" — the surname alone is a legitimate short form.
        add(name.split(",", 1)[0], True)
    else:
        # Organisation: its first distinctive word is a legitimate short form.
        for token in normalise(name).split():
            if token not in GENERIC_TOKENS:
                add(token, True)
                break

    return tuple(MatchTerm(text=t, allow_single_token=ok) for t, ok in terms.items())


MATCH_FULL_NAME = "full_name"
MATCH_GIVEN_NAME_COMPATIBLE = "given_name_compatible"
MATCH_ORGANISATION_NAME = "organisation_name"
MATCH_SURNAME_ONLY = "surname_only"


@dataclass(frozen=True)
class MatchOutcome:
    """How a supplied name met an entry, and whether that identifies anyone.

    `identity_confirmed` is false when the record gives only a surname and the
    entry names a person: the record may be about a different individual of the
    same name, and the flag has to say so rather than imply otherwise.
    """

    basis: str
    identity_confirmed: bool

    @property
    def note(self) -> str:
        if self.identity_confirmed:
            return ""
        return (
            "The record gives a surname with no given name, and the list entry "
            "names an individual. This match therefore does NOT establish that "
            "the record's party is the person the entry describes — a different "
            "individual of the same surname is equally consistent with the "
            "record. Confirm identity before treating this as anything."
        )


def _person_forms(name: str, variants: tuple[str, ...]) -> tuple[PersonName, ...]:
    parsed = [parse_person_name(raw) for raw in (name, *variants)]
    return tuple(p for p in parsed if p is not None)


def match_entry(
    supplied_names: tuple[str, ...],
    terms: tuple[MatchTerm, ...],
    person_forms: tuple[PersonName, ...],
) -> MatchOutcome | None:
    """Match a record's owner names against one reference-list entry.

    The rule that matters: **a supplied value that names a person is matched
    as a person.** Sharing a surname with a list entry is not sharing an
    identity, so a supplied given name that conflicts with every given name
    the entry knows is a non-match, not a weak match. Only where the record
    records no given name at all does a surname stand alone, and that result
    is marked identity-unconfirmed rather than silently equated.

    Organisation values keep the previous bidirectional containment, which is
    what catches "Kunsthaus Lempertz AG" and a record that says only
    "Dorotheum".
    """
    for supplied in supplied_names:
        # A bare surname, checked before the person parse because a compound
        # surname ("Wolff Metternich") is indistinguishable from Given+Surname
        # by shape alone — only the entry knows which it is.
        if any(known.surname == normalise(supplied) for known in person_forms):
            return MatchOutcome(MATCH_SURNAME_ONLY, False)

        person = parse_person_name(supplied)
        if person is not None:
            for known in person_forms:
                if known.surname == person.surname and known.compatible_with(person):
                    return MatchOutcome(MATCH_GIVEN_NAME_COMPATIBLE, True)
            # A named individual that does not match any individual the entry
            # knows. Deliberately no fallback to token containment: that
            # fallback is exactly what printed a dealer's annotation beside an
            # unrelated person's name.
            continue

        for form in candidate_forms(supplied):
            tokens = form.split()
            for term in terms:
                term_tokens = term.text.split()
                if len(term_tokens) >= 2 and _contains_term(form, term.text):
                    return MatchOutcome(MATCH_FULL_NAME, True)
                if len(term_tokens) == 1 and _contains_term(form, term.text):
                    if term.text in GENERIC_TOKENS:
                        continue
                    return _single_token_outcome(term.text, person_forms)
                if not _contains_term(term.text, form):
                    continue
                if len(tokens) >= 2:
                    # A multi-token value inside the entry's name. At least one
                    # token must carry information: every token of
                    # "Münchener Kunstversteigerungshaus" is generic, and two
                    # generic words identify no one.
                    if all(token in GENERIC_TOKENS for token in tokens):
                        continue
                    return MatchOutcome(MATCH_FULL_NAME, True)
                if (
                    term.allow_single_token
                    and len(form) >= 4
                    and form not in GENERIC_TOKENS
                    and form in term_tokens
                ):
                    return _single_token_outcome(form, person_forms)
    return None


def _single_token_outcome(
    token: str, person_forms: tuple[PersonName, ...]
) -> MatchOutcome:
    """A lone token: a person's surname, or an organisation's own name."""
    if any(known.surname == token for known in person_forms):
        return MatchOutcome(MATCH_SURNAME_ONLY, False)
    return MatchOutcome(MATCH_ORGANISATION_NAME, True)


# --------------------------------------------------------------------------
# ALIU Red Flag Names List
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AliuEntry:
    entry_id: str
    name: str
    entry_type: str
    annotation: str
    source_url: str
    terms: tuple[MatchTerm, ...]
    person_forms: tuple[PersonName, ...] = ()
    verification_note: str | None = None
    cross_referenced_sources: tuple[str, ...] = ()

    @property
    def is_exonerating(self) -> bool:
        return self.entry_type == ENTRY_EXONERATING

    def cite(self) -> dict[str, object]:
        cited: dict[str, object] = {
            "aliu_entry_name": self.name,
            "aliu_entry_type": self.entry_type,
            # The list's own wording. Required on every match: a bare
            # "flagged" label is exactly what must never be rendered.
            "aliu_annotation": self.annotation,
            "source_url": self.source_url,
        }
        if self.cross_referenced_sources:
            cited["cross_referenced_sources"] = list(self.cross_referenced_sources)
        if self.verification_note:
            cited["verification_note"] = self.verification_note
        return cited


@dataclass(frozen=True)
class AliuList:
    entries: tuple[AliuEntry, ...]
    meta: dict
    source_path: str

    @property
    def critical_scholarly_context(self) -> str:
        return self.meta.get("critical_scholarly_context", "")

    @property
    def status(self) -> str:
        return self.meta.get("status", "")

    @property
    def is_seed(self) -> bool:
        """True when the list declares itself incomplete.

        This governs how a *non*-match must be read: against a seed list,
        the absence of a name match carries almost no information, and the
        report has to say so rather than let it read as a clear result.
        """
        return "seed" in self.status.casefold() or "incomplete" in self.status.casefold()

    def match(self, names: list[str]) -> list[tuple[AliuEntry, str, MatchOutcome]]:
        """(entry, the supplied name that matched, how it matched)."""
        hits: list[tuple[AliuEntry, str, MatchOutcome]] = []
        seen: set[str] = set()
        for supplied in names:
            if not supplied:
                continue
            for entry in self.entries:
                if entry.entry_id in seen:
                    continue
                outcome = match_entry((supplied,), entry.terms, entry.person_forms)
                if outcome is not None:
                    hits.append((entry, supplied, outcome))
                    seen.add(entry.entry_id)
        return hits


def load_aliu_list(path: str | Path = DEFAULT_ALIU_PATH) -> AliuList:
    """Load the bundled ALIU list. Raises ReferenceListError if unusable."""
    path = Path(path)
    if not path.exists():
        raise ReferenceListError(
            f"ALIU Red Flag Names List not found at {path}. This is a bundled "
            "public resource; the name-matching rule cannot run without it."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for index, item in enumerate(raw.get("entries", [])):
        missing = [
            key
            for key in ("name", "entry_type", "annotation", "source_url")
            if not item.get(key)
        ]
        if missing:
            raise ReferenceListError(
                f"{path}: entry {index} is missing required field(s): "
                f"{', '.join(missing)}. Every entry must carry its annotation "
                "and source so a match can render as a citation."
            )
        if item["entry_type"] not in (ENTRY_DOCUMENTED_CONCERN, ENTRY_EXONERATING):
            raise ReferenceListError(
                f"{path}: entry {index} has entry_type {item['entry_type']!r}; "
                f"permitted: {ENTRY_DOCUMENTED_CONCERN}, {ENTRY_EXONERATING}"
            )
        variants = tuple(item.get("name_variants") or item.get("match_terms") or ())
        entries.append(
            AliuEntry(
                entry_id=str(item.get("entry_id", index + 1)),
                name=item["name"],
                entry_type=item["entry_type"],
                annotation=item["annotation"],
                source_url=item["source_url"],
                terms=_build_terms(item["name"], variants),
                person_forms=_person_forms(item["name"], variants),
                verification_note=item.get("verification_note"),
                cross_referenced_sources=tuple(
                    item.get("cross_referenced_sources") or ()
                ),
            )
        )
    if not entries:
        raise ReferenceListError(f"{path}: list contains no entries")
    return AliuList(
        entries=tuple(entries), meta=raw.get("_meta", {}), source_path=str(path)
    )


# --------------------------------------------------------------------------
# Confiscation-channel actors
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActorBasis:
    """One documented limb of an actor entry.

    `implies_persecution_of_former_owner` is the gate. Some documented
    activity — a state-organised disposal of works seized from *public
    museum* collections, for instance — is well evidenced but says nothing
    about persecution of a private former owner. A limb marked false must
    not be rendered as though it did.
    """

    summary: str
    sources: tuple[str, ...]
    implies_persecution_of_former_owner: bool
    period: str | None = None
    gate_basis: str | None = None

    def cite(self) -> dict[str, object]:
        cited: dict[str, object] = {
            "documented_basis": self.summary,
            "sources": list(self.sources),
            "implies_persecution_of_former_owner": (
                self.implies_persecution_of_former_owner
            ),
        }
        if self.gate_basis:
            cited["gate_basis"] = self.gate_basis
        if self.period:
            cited["basis_period"] = self.period
        return cited


@dataclass(frozen=True)
class ConfiscationActor:
    actor_id: str
    name: str
    bases: tuple[ActorBasis, ...]
    terms: tuple[MatchTerm, ...]
    person_forms: tuple[PersonName, ...] = ()
    verification_note: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    location: str | None = None
    active_period: str | None = None

    @property
    def has_persecution_implying_basis(self) -> bool:
        return any(b.implies_persecution_of_former_owner for b in self.bases)

    def cite(self) -> dict[str, object]:
        cited: dict[str, object] = {
            "actor_entry": self.name,
            "documented_bases": [b.cite() for b in self.bases],
        }
        if self.location:
            cited["actor_location"] = self.location
        if self.active_period:
            cited["actor_active_period"] = self.active_period
        if self.verification_note:
            # Surfaced on every match so a weaker-sourced entry never renders
            # with the confidence of a well-documented one.
            cited["verification_note"] = self.verification_note
        return cited


@dataclass(frozen=True)
class ActorList:
    actors: tuple[ConfiscationActor, ...]
    meta: dict
    source_path: str

    @property
    def critical_context(self) -> str:
        return self.meta.get("critical_context", "")

    @property
    def status(self) -> str:
        return self.meta.get("status", "")

    @property
    def is_seed(self) -> bool:
        lowered = self.status.casefold()
        return "seed" in lowered or "not exhaustive" in lowered

    def match(
        self, names: list[str]
    ) -> list[tuple[ConfiscationActor, str, MatchOutcome]]:
        hits: list[tuple[ConfiscationActor, str, MatchOutcome]] = []
        seen: set[str] = set()
        for supplied in names:
            if not supplied:
                continue
            for actor in self.actors:
                if actor.actor_id in seen:
                    continue
                outcome = match_entry((supplied,), actor.terms, actor.person_forms)
                if outcome is not None:
                    hits.append((actor, supplied, outcome))
                    seen.add(actor.actor_id)
        return hits


def load_actor_list(path: str | Path = DEFAULT_ACTORS_PATH) -> ActorList:
    """Load the confiscation-channel actor list.

    Every entry must carry at least one documented basis with sources; an
    entry that merely names a business, with no cited basis, is exactly the
    unsourced accusation this tool must not make.
    """
    path = Path(path)
    if not path.exists():
        raise ReferenceListError(
            f"Confiscation-channel actor list not found at {path}. The "
            "confiscation-channel rule cannot run without it."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    actors = []
    for index, item in enumerate(raw.get("actors", raw.get("entries", []))):
        name = item.get("name")
        if not name:
            raise ReferenceListError(f"{path}: entry {index} has no name")
        bases_raw = item.get("documented_basis") or []
        if isinstance(bases_raw, dict):
            bases_raw = [bases_raw]
        elif isinstance(bases_raw, str):
            # Single prose basis with entry-level sources.
            bases_raw = [
                {
                    "summary": bases_raw,
                    "sources": item.get("sources") or [],
                    "period": item.get("active_period"),
                    **(
                        {
                            "implies_persecution_of_former_owner": item[
                                "implies_persecution_of_former_owner"
                            ]
                        }
                        if "implies_persecution_of_former_owner" in item
                        else {}
                    ),
                }
            ]
        if not bases_raw:
            raise ReferenceListError(
                f"{path}: entry {index} ({name}) has no documented_basis. An "
                "entry without a cited basis cannot be rendered as a citation "
                "and must not be matched against."
            )
        bases = []
        for basis in bases_raw:
            sources = basis.get("sources") or []
            if not sources:
                raise ReferenceListError(
                    f"{path}: entry {index} ({name}) has a documented_basis "
                    "limb with no sources."
                )
            if "implies_persecution_of_former_owner" not in basis:
                raise ReferenceListError(
                    f"{path}: entry {index} ({name}) has a documented_basis "
                    "limb without an explicit "
                    "`implies_persecution_of_former_owner`. This gate decides "
                    "whether a match may be rendered as bearing on an "
                    "individual former owner and must not be defaulted."
                )
            bases.append(
                ActorBasis(
                    summary=basis.get("summary") or basis.get("description") or "",
                    sources=tuple(sources),
                    implies_persecution_of_former_owner=bool(
                        basis["implies_persecution_of_former_owner"]
                    ),
                    period=basis.get("period"),
                    gate_basis=basis.get("gate_basis"),
                )
            )
        aliases = tuple(
            item.get("name_variants") or item.get("aliases") or item.get("match_terms") or ()
        )
        actors.append(
            ConfiscationActor(
                actor_id=str(item.get("actor_id", item.get("id", index + 1))),
                name=name,
                bases=tuple(bases),
                terms=_build_terms(name, aliases),
                person_forms=_person_forms(name, aliases),
                verification_note=item.get("verification_note"),
                aliases=aliases,
                location=item.get("location"),
                active_period=item.get("active_period"),
            )
        )
    if not actors:
        raise ReferenceListError(f"{path}: list contains no entries")
    return ActorList(
        actors=tuple(actors), meta=raw.get("_meta", {}), source_path=str(path)
    )
