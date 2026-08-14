"""Verification of generated explanations against their own input.

The LLM layer is allowed to do exactly one thing: restate a rule against the
record fields that rule cited. It may not introduce a name, a date, a place,
an organisation, an event, or any other fact that is not already in front of
it, and it may not phrase anything as a finding.

Instructing a model to obey that is not the same as it obeying. This module
is the enforcement: every generated sentence is checked back against the
exact context it was given, and anything that introduces new material is
rejected. Rejection is cheap — the deterministic `rule_statement` is always
present, so a rejected explanation costs a bit of plain language, nothing
more. A confabulated collector biography that reaches a curator costs the
institution's trust in the whole tool, and is uncheckable by the non-expert
user this is built for. The asymmetry is the entire design rationale, and
every threshold here is set to over-reject.

What is checked:

1. **Numbers.** Every number in the output must appear in the input. Catches
   invented dates, lot numbers, prices and counts.
2. **Historical elaboration.** A curated multilingual list of Nazi-era events,
   institutions and actors, matched regardless of output language. None may
   appear unless it was in the cited fields.
3. **Finding language.** Per-language assertion phrases ("was looted",
   "proves", "certainly") that turn a question into a conclusion.
4. **Ungrounded named material.** Where capitalisation marks proper nouns
   (en/fr/it), a capitalised mid-sentence token that does not trace back to
   the input is an invented entity. Where it does not (de), per-token
   grounding is impossible — the cited fields are English, so an ordinary
   German noun never matches them — and a RUN of adjacent capitalised tokens
   containing an ungrounded one is used instead, that being characteristically
   a name rather than prose. In EVERY language, words capitalised only for
   position (clause-initial verbs, interrogatives, articles) are excluded by
   identity rather than by position, so a clause may still *begin* with a
   detected name. This was true of German alone until 2026-08-14: the other
   three skipped the first token of each sentence blindly, which granted one
   free fabricated entity per sentence.
   Translating the input is not adding to it, so a token also grounds as a
   cross-language form of something in the fields — including the written-out
   name of a month the fields actually date.
5. **Length.** Elaboration beyond the input is the risk; a long answer is
   evidence of it.
6. **Question framing.** The output must read as an open question.

LIMITATIONS, stated because a guard whose gaps are undocumented is worse than
no guard. In German a *single* ungrounded noun mid-sentence is not flagged;
only runs are. That is the accepted cost of the layer being able to emit
German at all — requiring every capitalised token to be grounded rejected
100% of live German output (0/12 baseline, 2026-08-09). Uncited historical
narrative is caught by check 2 instead, which is its proper home. And none of
this detects a fluent paraphrase that subtly changes the meaning of a field it
was given. This is a mitigation, not a proof of faithfulness, and the layer
stays optional and off by default for that reason.

The en/fr/it `positional_capitals` lists are NEW and have not been through a
live run. They carry the same risk German's did: a legitimate sentence-initial
word missing from the list rejects faithful output. Two were found and added
while writing the change ("Cita", "Quella"). Re-run the harness and read its
frequency-ranked rejected-token list before trusting the baseline.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LANGUAGE_PATH = (
    Path(__file__).resolve().parent.parent / "data/llm_output_language.json"
)

# An explanation restates one rule against a handful of fields. Anything much
# longer is elaborating, which is what this layer exists to prevent.
#
# The sentence cap mirrors the prompt's own instruction ("at most three short
# sentences"), so exceeding it is not a stylistic judgement — it is the model
# having ignored the constraint, which is the moment to stop trusting the rest
# of the output. Tightened from 5 after live validation, where an Italian
# elicitation response added two sentences of period background and passed.
MAX_CHARACTERS = 700
MAX_SENTENCES = 3

# Below this length a capitalised token is too likely to be an ordinary word.
# Two, not three: at three, "The buyer was Ky and the seller Ab" passed with two
# invented parties. A two-letter capitalised token mid-sentence is rare enough
# in ordinary prose that the trade is worth it.
MIN_PROPER_NOUN_LENGTH = 2

VIOLATION_NUMBER = "introduced_number"
VIOLATION_HISTORY = "introduced_historical_context"
VIOLATION_ASSERTION = "asserted_a_finding"
VIOLATION_PROPER_NOUN = "introduced_proper_noun"
VIOLATION_LENGTH = "too_long"
VIOLATION_FRAMING = "not_phrased_as_a_question"
VIOLATION_EMPTY = "empty_output"


class LanguageError(Exception):
    """Raised for an unknown or malformed output language."""


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# Endings stripped when matching a German noun back to the fields. German
# inflects, so "Angaben" must reach "Angabe" or the check rejects ordinary
# prose. Longest first; only ever used to find a match, so an over-strip
# produces a non-match and rejects, never a false pass.
_DE_ENDINGS = ("ungen", "chen", "lein", "innen", "en", "er", "es", "em", "e", "n", "s")

# A compound's head is its last element and carries its meaning, so
# "Verkaufsdatum" is grounded by "Datum". Kept long to stop a fabricated name
# being grounded by a short generic tail.
_MIN_COMPOUND_HEAD = 5


@dataclass(frozen=True)
class OutputLanguage:
    code: str
    label: str
    instruction: str
    capitalises_common_nouns: bool
    question_markers: tuple[str, ...]
    forbidden_assertions: tuple[str, ...]
    allowed_vocabulary: frozenset[str]
    # Folded task vocabulary. Populated for languages where capitalisation
    # cannot separate an ordinary noun from an invented entity.
    restatement_vocabulary: frozenset[str] = frozenset()
    # Words capitalised for position rather than because they name anything:
    # clause-initial verbs, interrogatives, articles. None is ever part of a
    # proper name, so none may contribute to a name-run.
    positional_capitals: frozenset[str] = frozenset()
    # Per-language limits. German needs more characters for the same content
    # (compound nouns, verb-final clauses), so the character allowance is
    # language-aware. The SENTENCE cap deliberately is not: it mirrors the
    # prompt's own "at most three short sentences", which is the same
    # instruction in every language, and raising it for German would re-open
    # the elaboration route the Italian leak came through.
    max_characters: int = MAX_CHARACTERS
    max_sentences: int = MAX_SENTENCES


def _de_stems(token: str) -> set[str]:
    """The token plus plausible uninflected forms."""
    forms = {token}
    for ending in _DE_ENDINGS:
        if len(token) > len(ending) + 3 and token.endswith(ending):
            forms.add(token[: -len(ending)])
    return forms


@dataclass(frozen=True)
class LanguagePack:
    languages: dict[str, OutputLanguage]
    historical_terms: tuple[str, ...]
    version: str
    # folded token -> index of the equivalence group it belongs to
    equivalence_index: dict[str, int] = field(default_factory=dict)
    equivalence_groups: tuple[tuple[str, ...], ...] = ()
    # month number (1-12) -> folded written-out forms in every output language
    month_names: dict[int, frozenset[str]] = field(default_factory=dict)

    def equivalents(self, token: str) -> tuple[str, ...]:
        """Cross-language forms of the same referent, including the token."""
        index = self.equivalence_index.get(token)
        return self.equivalence_groups[index] if index is not None else (token,)

    def month_tokens_for(self, text: str) -> set[str]:
        """Written-out forms of the months named by dates present in `text`.

        A date in a cited field *is* its written-out form: for a record whose
        date reads "1934-06", writing the sixth month's name in any output
        language translates the input rather than adding to it, exactly as
        "Allemagne" does for "Germany". So the allowance is derived from the
        dates actually present — never granted as a blanket one. With
        "1934-06" in the fields the sixth month's name grounds and no other
        month's does. The names themselves live in the language pack; nothing
        here knows any of them.
        """
        tokens: set[str] = set()
        for match in _ISO_DATE_RE.finditer(text):
            names = self.month_names.get(int(match.group("month")))
            if names:
                tokens |= names
        return tokens

    def get(self, code: str) -> OutputLanguage:
        try:
            return self.languages[code]
        except KeyError:
            permitted = ", ".join(sorted(self.languages))
            raise LanguageError(
                f"unknown output language {code!r}; available: {permitted}"
            ) from None


def load_language_pack(path: str | Path = DEFAULT_LANGUAGE_PATH) -> LanguagePack:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    languages = {
        code: OutputLanguage(
            code=code,
            label=entry["label"],
            instruction=entry["instruction"],
            capitalises_common_nouns=bool(entry.get("capitalises_common_nouns")),
            question_markers=tuple(_fold(m) for m in entry.get("question_markers", ())),
            forbidden_assertions=tuple(
                _fold(a) for a in entry.get("forbidden_assertions", ())
            ),
            # Folded like every neighbouring comparison. Unfolded, this was
            # the one set in the guard that was case- and diacritic-sensitive.
            allowed_vocabulary=frozenset(
                _fold(w) for w in entry.get("allowed_vocabulary", ())
            ),
            restatement_vocabulary=frozenset(
                _fold(w)
                for w in (entry.get("restatement_vocabulary") or {}).get("words", ())
            ),
            positional_capitals=frozenset(
                _fold(w)
                for w in (entry.get("positional_capitals") or {}).get("words", ())
            ),
            max_characters=int((entry.get("limits") or {}).get(
                "max_characters", MAX_CHARACTERS)),
            max_sentences=int((entry.get("limits") or {}).get(
                "max_sentences", MAX_SENTENCES)),
        )
        for code, entry in raw["languages"].items()
    }

    groups = tuple(
        tuple(_fold(member) for member in group)
        for group in (raw.get("equivalence_groups") or {}).get("groups", ())
    )
    # A month's names across the four output languages are also a cross-language
    # equivalence group in their own right: where a cited field spells a month
    # out, the other languages' forms of it are translations. That route still
    # requires a group member to be present in the fields, so it grounds
    # nothing on its own.
    months: dict[int, frozenset[str]] = {}
    month_groups: list[tuple[str, ...]] = []
    for entry in (raw.get("date_equivalence") or {}).get("months", ()):
        folded = tuple(_fold(name) for name in entry["names"])
        months[int(entry["n"])] = frozenset(folded)
        month_groups.append(folded)

    groups = groups + tuple(month_groups)
    index = {member: position for position, group in enumerate(groups) for member in group}

    return LanguagePack(
        languages=languages,
        historical_terms=tuple(
            _fold(t) for t in raw["historical_elaboration_terms"]["terms"]
        ),
        version=raw.get("version", "unversioned"),
        equivalence_index=index,
        equivalence_groups=groups,
        month_names=months,
    )


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str
    # The exact token that tripped the check, where there is one. Carried
    # structurally rather than parsed back out of `detail`, so tooling can
    # tally which words are being rejected across a whole run.
    token: str | None = None

    def describe(self) -> str:
        return f"{self.kind}: {self.detail}"


@dataclass(frozen=True)
class VerificationResult:
    accepted: bool
    violations: tuple[Violation, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(v.describe() for v in self.violations)


_NUMBER_RE = re.compile(r"\d[\d.,/-]*")
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_SENTENCE_RE = re.compile(r"[.!?]+")

# Periods that end an abbreviation, not a sentence. Left unmasked they inflate
# the sentence count: an output citing an owner recorded as "Cordes, H."
# measured three sentences when it had two, and was rejected as too long.
# German output took the worst of this because it names the party more often,
# but the defect is language-neutral.
_INITIAL_PERIOD_RE = re.compile(r"(?<!\w)([^\W\d_])\.", re.UNICODE)
_ABBREVIATIONS = (
    "nr", "bd", "hrsg", "vgl", "bzw", "ca", "ggf", "usw", "etc", "abb", "jh",
    "fol", "no", "pp", "cf", "ecc", "art", "cat", "inv", "ed", "edn", "vol",
)
_ABBREVIATION_RE = re.compile(
    r"(?<!\w)(" + "|".join(_ABBREVIATIONS) + r")\.", re.IGNORECASE
)
# Stands in for a masked period: not sentence punctuation, so it neither ends
# a sentence nor starts a clause.
_MASK = "․"


def mask_non_sentence_periods(text: str) -> str:
    """Neutralise periods that end an initial or a known abbreviation."""
    masked = _INITIAL_PERIOD_RE.sub(rf"\1{_MASK}", text)
    return _ABBREVIATION_RE.sub(rf"\1{_MASK}", masked)


def count_sentences(text: str) -> int:
    return len([s for s in _SENTENCE_RE.split(mask_non_sentence_periods(text)) if s.strip()])

# Clause boundaries for the name-run scan. Wider than sentence boundaries,
# because German capitalises the first word after a colon, a dash or an
# opening quote as well as after a full stop — and a capital that is there for
# position carries no information about names.
_CLAUSE_RE = re.compile("[.!?:;\n–—\"«»“”„‘]+")


# ISO dates only. This is the form the schema validates to and the form the
# cited fields therefore carry, and a narrow pattern is the point: a looser one
# ("3.5.1938", "5/38") would read month numbers out of catalogue and inventory
# references and ground month names the record never dated.
#
# The boundaries reject anything with more word or hyphen characters attached,
# which is not pedantry: the flags cite an onset-table version of the form
# "2026-08-09b", and without the trailing guard that string grounded "August"
# on every flag that carried it.
_ISO_DATE_RE = re.compile(
    r"(?<!\w)\d{4}-(?P<month>0[1-9]|1[0-2])(?:-\d{2})?(?![\w-])"
)


def _numbers_in(text: str) -> set[str]:
    """Digit runs, with separators stripped, so '1938' matches '1938-11-04'."""
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        for part in re.split(r"[.,/-]", match.group()):
            if part:
                found.add(part.lstrip("0") or "0")
    return found


def build_context_text(context: dict[str, object]) -> str:
    """Flatten the constrained context into the text the output is checked against."""
    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                parts.append(str(key))
                walk(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                walk(inner)
        elif value is not None:
            parts.append(str(value))

    walk(context)
    return " ".join(parts)


def _is_grounded(
    folded: str,
    source_tokens: set[str],
    language: OutputLanguage,
    pack: LanguagePack,
) -> bool:
    """Whether a named token traces back to the cited fields.

    Three routes, in order of directness:

    1. it appears in the fields;
    2. it is a cross-language form of something in the fields — writing
       "Allemagne" for a record that says "Germany" translates the input
       rather than adding to it. Note this never grounds a token on its own:
       another member of its group has to be present;
    3. for a language where capitalisation cannot separate a common noun from
       a proper one, it is ordinary task vocabulary. That list is scoped to
       restatement only, so narrative nouns fall through and are rejected.
    """
    if folded in source_tokens:
        return True
    if any(form in source_tokens for form in pack.equivalents(folded)):
        return True
    if not language.capitalises_common_nouns:
        return False

    vocabulary = language.restatement_vocabulary
    stems = _de_stems(folded)
    if stems & vocabulary or stems & source_tokens:
        return True
    # Grounded through the head of a compound: "Verkaufsdatum" by "Datum".
    for known in vocabulary | source_tokens:
        if len(known) >= _MIN_COMPOUND_HEAD and folded.endswith(known):
            return True
    return False


# A proper name in German characteristically shows up as adjacent capitalised
# content words, because ordinary nouns are separated by articles,
# prepositions and verbs.
_MIN_CAPITALISED_RUN = 2


def _capitalised_run_violations(
    tokens: list[str],
    source_tokens: set[str],
    language: OutputLanguage,
    pack: LanguagePack,
) -> list[Violation]:
    """Entity detection for languages that capitalise common nouns.

    Per-token grounding cannot work here: the cited fields are English, so an
    ordinary German noun never matches them however faithful the sentence is.
    Requiring it rejected every live German output. What survives as a signal
    is the RUN — two or more adjacent capitalised tokens, at least one of them
    ungrounded, which is characteristically a name rather than prose.

    A single ungrounded noun mid-sentence is deliberately not flagged. That is
    the accepted cost of the layer being able to emit German at all; uncited
    narrative is caught by the historical-term check instead.
    """
    violations: list[Violation] = []
    run: list[str] = []

    def flush(run: list[str]) -> None:
        if len(run) < _MIN_CAPITALISED_RUN:
            return
        ungrounded = [
            token
            for token in run
            if not _is_grounded(_fold(token), source_tokens, language, pack)
        ]
        if not ungrounded:
            return
        phrase = " ".join(run)
        violations.append(
            Violation(
                VIOLATION_PROPER_NOUN,
                f"{phrase!r} reads as a name; "
                f"{', '.join(repr(t) for t in ungrounded)} not in the cited fields",
                token=ungrounded[0],
            )
        )

    # Which clause-initial capitals to discard. Where the language supplies a
    # list of words that are only ever capitalised for position, use it: it is
    # strictly better than skipping the first token blindly, because a clause
    # can legitimately *begin* with a name ("Ungeklärt: Galerie Morgenstern
    # trat auf") and blind skipping loses exactly that case.
    use_positional = bool(language.positional_capitals)

    for index, token in enumerate(tokens):
        capitalised = token[:1].isupper() and len(token) >= MIN_PROPER_NOUN_LENGTH
        # A word capitalised only for position names nothing, and breaks the
        # run: a proper name contains no bare article, verb or interrogative.
        if use_positional:
            skip = _fold(token) in language.positional_capitals
        else:
            skip = index == 0
        if capitalised and not skip:
            run.append(token)
        else:
            flush(run)
            run = []
    flush(run)
    return violations


def verify(
    output: str, context: dict[str, object], language: OutputLanguage, pack: LanguagePack
) -> VerificationResult:
    """Check generated text against the exact context it was given."""
    violations: list[Violation] = []
    text = (output or "").strip()
    if not text:
        return VerificationResult(False, (Violation(VIOLATION_EMPTY, "no text returned"),))

    source = build_context_text(context)
    folded_source = _fold(source)
    folded_output = _fold(text)

    # 1. Numbers must come from the input.
    allowed_numbers = _numbers_in(source)
    for number in sorted(_numbers_in(text) - allowed_numbers):
        violations.append(
            Violation(
                VIOLATION_NUMBER,
                f"{number!r} does not appear in the cited fields",
                token=number,
            )
        )

    # 2. Historical context the model supplied from its own knowledge. A narrow
    # inflection suffix set is appended so "enteignung" also catches
    # "Enteignungen", without letting "nazi" match the Italian "nazionale".
    for term in pack.historical_terms:
        pattern = rf"(?<!\w){re.escape(term)}(?:en|er|es|em|em|e|n|s|i)?(?!\w)"
        if re.search(pattern, folded_output) and not re.search(pattern, folded_source):
            violations.append(
                Violation(
                    VIOLATION_HISTORY,
                    f"{term!r} does not appear in the cited fields",
                    token=term,
                )
            )

    # 3. Finding language. Word-boundary matched, not substring: German
    # "ungeklärt" ("unresolved") contains "geklärt" ("resolved"), and a
    # substring test would reject the exact framing the layer requires.
    for phrase in language.forbidden_assertions:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded_output):
            violations.append(
                Violation(
                    VIOLATION_ASSERTION, f"output contains {phrase!r}", token=phrase
                )
            )

    # 4. Named material must be grounded in the cited fields.
    source_tokens = {_fold(t) for t in _TOKEN_RE.findall(source)}
    source_tokens |= pack.month_tokens_for(source)
    scan_text = mask_non_sentence_periods(text)
    if language.capitalises_common_nouns:
        for clause in _CLAUSE_RE.split(scan_text):
            violations.extend(
                _capitalised_run_violations(
                    _TOKEN_RE.findall(clause), source_tokens, language, pack
                )
            )
    for sentence in _SENTENCE_RE.split(scan_text):
        tokens = _TOKEN_RE.findall(sentence)
        if language.capitalises_common_nouns:
            continue
        for index, token in enumerate(tokens):
            if not token[:1].isupper():
                continue
            # The sentence-initial token is excluded by IDENTITY, not by
            # position — the same correction already made for German, which
            # had only ever been applied to German. Skipping index 0 blindly
            # granted one free fabricated entity per sentence: "Morgenstern is
            # named here; was fair value received?" passed the guard entirely.
            if index == 0 and _fold(token) in language.positional_capitals:
                continue
            if len(token) < MIN_PROPER_NOUN_LENGTH:
                continue
            if _fold(token) in language.allowed_vocabulary:
                continue
            if _is_grounded(_fold(token), source_tokens, language, pack):
                continue
            violations.append(
                Violation(
                    VIOLATION_PROPER_NOUN,
                    f"{token!r} does not appear in the cited fields",
                    token=token,
                )
            )

    # 5. Length. Counted on text whose abbreviation periods are masked, so an
    # owner recorded as "Cordes, H." does not read as an extra sentence.
    if len(text) > language.max_characters:
        violations.append(
            Violation(
                VIOLATION_LENGTH,
                f"{len(text)} characters (max {language.max_characters} for "
                f"{language.code})",
            )
        )
    sentence_count = count_sentences(text)
    if sentence_count > language.max_sentences:
        violations.append(
            Violation(
                VIOLATION_LENGTH,
                f"{sentence_count} sentences (max {language.max_sentences})",
            )
        )

    # 6. Question framing.
    if not any(marker in folded_output for marker in language.question_markers):
        violations.append(
            Violation(
                VIOLATION_FRAMING,
                "output reads as a statement; it must be posed as an open question",
            )
        )

    # De-duplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    unique: list[Violation] = []
    for violation in violations:
        key = (violation.kind, violation.detail)
        if key not in seen:
            seen.add(key)
            unique.append(violation)

    return VerificationResult(accepted=not unique, violations=tuple(unique))
