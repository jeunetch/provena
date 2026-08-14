# Decisions

One heading per decision: what was decided, why, and when. **Not a
chronology** — git holds that with better fidelity than prose, and someone
asking "why is Italy two windows?" should not have to scan seven live-run
entries to find out.

Nothing here is required to evaluate the methodology. The README is that
document; this one answers "why is it like that" for a decision you have
already found. The live-validation run record lives in
[`scripts/VALIDATION.md`](scripts/VALIDATION.md), next to the harness that
produces it.

---

## No numeric risk score, ever

**2026-08-09.** Three reasons, and the third is the one that is usually
missed.

*Discovery risk.* A stored score is a record of institutional notice. If an
object is scored "low risk" and later shown to be looted, that score can be
used to establish that the institution assessed and dismissed the risk. A
scoring tool risks manufacturing evidence against its own users.

*False precision.* The Washington Principles and the Terezin Declaration are
non-binding soft law resolved case by case. A decimal implies a determinacy
that does not exist anywhere in the field.

*"Low risk" is the dangerous output, not "high risk".* A false positive costs
a researcher a couple of hours. A false negative carrying an authoritative
badge can end inquiry permanently, with the tool's own output as documentation
that inquiry happened.

Work-queue banding is computed at render time and deliberately **not** written
back into the stored JSON. An ordering is fine; a stored per-object label is
the graded, discoverable record this reasoning exists to prevent.

## The coverage map comes before the queue

**2026-08-09.** How many objects have any pre-1945 provenance, how many have
none, how many begin at institutional accession — that is what a researcher
needs to scope a year of work, and it is more diagnostic than any per-object
output. The ranked queue is the second screen.

## Two axes, never merged

**2026-08-09.** `persecution_context_flags` and `documentation_quality_flags`
are separate lists. A well-documented 1935 Vienna sale is a risk problem; a
1650 painting with an unbroken chain and no citations is a records problem.
Collapsing them into one number reintroduces the score by another route.

Rule DQ-002 (post-commitment acquisition, thin pre-1945 chain) is on the
documentation axis deliberately: it concerns how the institution acquired the
object, measured against its own stated standard, and asserts nothing about the
object's persecution context. Putting it on the persecution axis would make it
assert exactly that.

## A criterion that could not run is not a criterion that found nothing

**2026-08-09, applied throughout.** Every rule that depends on an optional
field reports itself *skipped* in `coverage_note` when the field is blank,
never "not triggered". This is why several schema fields exist as explicit
columns rather than being inferred: `is_institution_acquisition`,
`catalogue_reference`, `owner_stated_in_catalogue`,
`restitution_recipient_type`. Inferring any of them from free text is the guess
that produces silent false negatives.

## Italy is two persecution windows, not one

**2026-08-09. Transcription, not citation — see the README's expert-review
list.** The 1938 racial laws began real civil and property restriction; the
September 1943 occupation is where persecution escalates to deportation level.
So 1938–Aug 1943 engages the ordinary presumption and Sep 1943–1955 the
heightened one, with tiers fixed per window rather than derived from the global
15 September 1935 threshold — that threshold does not describe the Italian
chronology, and applying it would report every 1938–43 Italian transfer as
heightened.

Treating 1938 as mere context and screening only from Sep 1943 was the
alternative and is worse: under-flagging 1938–43 is a false negative, which
this project treats as the more dangerous failure. The onset table therefore
supports multiple windows per territory, each with its own tier or
`by_nuremberg_threshold`.

## The `circa` margin is a placeholder

**2026-08-09.** ±5 years, with no sourced basis. It exists only so overlap
questions can be asked of a circa date; it is never used to compute or report
an interval length, and a widened date can never yield a `certain` overlap. It
is configurable per run (`--circa-margin-years`) and echoed in every report
with that caveat attached, so it cannot acquire the appearance of authority.

## Restitution records are not re-flagged

**2026-08-09.** A record whose own `transaction_state` is
`restitution_erfolgt_or_vergleich` is never itself flagged by the
persecution-window rule, even though a post-war settlement date falls inside a
risk band extended to 1955. A restitution resolves a claim rather than
presenting one for triage. Other records in the same chain are screened
normally and the suppression is stated in `coverage_note`.

Re-flagging a settled claim is a real-cost false positive: it can reopen
matters that were closed, and it destroys curatorial trust in the tool.

**PC-008 is the one exception.** It only fires on chains containing a
restitution record — precisely the chains `resolved_status` holds out of the
active queue — so applying the hold-out there would make it unreachable. The
rule exists to ask whether a recorded settlement actually reached the
dispossessed family, and suppressing it on the strength of that settlement
defeats it. An object carrying PC-008 is shown in the queue. This is a display
decision only; the stored `resolved_status` is unchanged.

## Objects leave the queue only when a human says so

**2026-08-09.** There is no automatic exit. If the system can silently mark
something clean, an institution will eventually let it, and that is the failure
mode the whole design exists to prevent.

## The actor list's persecution gate was added by transcription

**2026-08-09. Transcription, not citation — see the README's expert-review
list.** Each entry carries `documented_basis` limbs with their own sources and
an explicit `implies_persecution_of_former_owner`; the loader refuses to
default that field, because a silent default there is precisely the unsourced
accusation the tool must not make.

The file as supplied states the Galerie Fischer distinction in prose — the 1939
sale concerned works taken from German state museums, "a different legal
category from persecuted-individual forced sales", while the broader claim
"needs its own case-level verification". Deriving that at match time by
keyword-sniffing the prose is exactly the inference this project rejects, so
the gate was written into the data file per limb, each with a `gate_basis`
quoting the entry's own words. The basis was split at the entry's own
"Separately,"; all original prose is preserved verbatim and a test asserts the
limbs rejoin to the original text.

## Exonerating list entries are a separate channel, not a flag with a field

**2026-08-09.** An `exonerating` ALIU entry records that a name was
investigated and cleared, or that the person acted protectively. Rendering it
as a flag would invert the record's meaning and defame the subject.

It leaves the heuristic layer on `name_list_citations` (`NM-002`), a distinct
structure rather than one list with a discriminator — a discriminated field is
one careless `.length` away from being counted as a concern. `NM-002` is
deliberately absent from the criteria list, since an exonerating citation is
not a criterion an object can fail.

## A shared surname is not a shared identity

**2026-08-14.** The renderer was careful and the matcher was not. `Lange,
Elisabeth` — a 1962 Swiss estate record — pulled the ALIU entry for `Lange,
Hans W.` and printed a 1946 investigative annotation beside her name. Same for
Fischer, Lempertz, Haberstock and Weinmüller: the modal Swiss surname, not an
edge case.

**The cost asymmetry that justifies over-flagging elsewhere is inverted here,
and this is the only place in the tool where that is true.** A PC-003 false
positive costs a researcher an hour. A false positive here is an assertion
about a named third party, who may have living heirs, inside a document the
institution circulates. Those are not the same cost and must not share a
threshold.

So: a supplied value that names a person is matched *as* a person, and a
conflicting given name is a non-match with no fallback to token containment.
Only the **first** given name identifies — comparing any token to any token let
the entry's middle initial `W.` match `Lange, Werner`. A bare surname still
matches but carries `identity_confirmed: false`, and the caveat is in
`rule_statement`, not only in `cited_fields`, because the statement is what a
reader reads and what the LLM layer restates.

`NM-001` also gates on the screening band, so a 2019 acquisition cannot raise
a name flag, and an undated record reports the criterion skipped.

## Person or organisation is declared, never inferred

**2026-08-14.** Both bundled lists carry `entry_kind`, and both loaders refuse
to default it. Two separate defects were the same guess failing in opposite
directions: a compound surname (`von Behr`) read as Given+Surname, and an
auction house rendered with "the list entry names an individual" — an untrue
sentence in the one rendering path whose whole justification is legal accuracy.

Nine entries to annotate then; two thousand later, after the guess had been
wrong in public. The kind governs wording only — whether a match is
identity-confirmed still turns on whether the entry knows any individual by
that name — and it is surfaced on every match, not only when a caveat fires,
because a confident match otherwise leaves it invisible.

## Less information never produces more confidence

**2026-08-14.** `_single_token_outcome` tested `known.surname == token`. A
compound surname never equals one of its own tokens, so `Behr` fell past the
surname branch to the organisation default and came back *more* confident than
`von Behr`, with the identity caveat skipped.

The defect was the default, not the equality test — the same shape as the
containment fallback removed the same day: an unmatched case routed to the more
confident outcome. An entry with any `person_forms` can no longer yield an
organisation match, whatever token reached it.

## `transaction_state` engages the persecution rule directly

**2026-08-14.** The REAO-grounded state field — the correction that grounds the
whole taxonomy — drove no rule of its own. It was read to pick a tier *inside*
PC-003's territory-window gate, to suppress settled records, and as the
independent-establishment check on the actor gate, so a state could only ever
select a tier it could not itself reach. A recorded `zwangsverkauf` in Geneva
returned "screened against N criteria; no criteria triggered", with PC-003
listed as *evaluated* rather than skipped, while an ordinary purchase in Munich
flagged. That is the terminal state the no-score reasoning exists to prevent,
reached by a different route.

**Switzerland is what made this structural.** The onset table has no Swiss
window and correctly never will — Switzerland was not occupied — so every Swiss
record was unreachable, including every `fluchtgut` record, the category this
project names as most relevant to its own jurisdiction.

PC-003 now has a second limb: a record whose own state is `entziehung`,
`zwangsverkauf`, `vermutung_der_entziehung` or `verschaerfte_vermutung` engages
it regardless of territory, and a record already flagged by the window limb is
not flagged twice.

## `fluchtgut` gets its own criterion and asserts no tier

**2026-08-14. Transcription, not citation — see the README's expert-review
list.** It is a contested category rather than a presumption, so it sits
outside PC-003's second limb and has its own criterion, PC-010, which states
the question and asserts nothing.

## Where a date cannot be placed, no tier is asserted

**2026-08-14.** PC-003's second limb first clipped the record's span to the
risk band and fell through to a default. That was wrong with both signs: a 1970
record produced an empty intersection and got the *milder* tier with a basis
string claiming its position was undetermined when it was not, while `circa
1935` got the *harsher* tier with a basis string that said undetermined.

Both are the same defect — picking a tier where the honest answer is that there
isn't one — and defaulting to the milder tier on unplaceable data is the
false-negative direction besides. The limb now tests the **widened** span
against the threshold directly: wholly on or after it is heightened, wholly
before it is ordinary, anything else asserts no tier with the reason. A test
asserts the invariant behind both bugs: *the basis never contradicts the tier
it explains.*

**A coupling worth knowing about:** the screening band's lower bound is the
onset table's **earliest onset**, not a fixed 1933. The table is documented as
configurable and extensible, so an institution adding a territory with an
earlier onset silently widens every decline boundary on this limb. That is
probably the correct behaviour — the band should follow the table — but it is
not predictable from reading either file, which is why it is written down here.

## `object_date` changes what PC-001 means

**2026-08-14.** PC-001 fires when nothing in the chain predates 1945. For an
object *created* in 1962 that is correct by the rule and useless in practice,
and it lands on the coverage map — the first screen.

Three states, not two. Recorded and on/after 1945: not applicable, with the
reason. Recorded and before 1945: fires, and cites the creation date. **Blank:
still fires.** An unrecorded date is not evidence the object is modern, and
switching off the modal rule on an empty column is the silent false negative
this project exists to avoid. The flag says in its own statement that it cannot
distinguish undocumented early provenance from an object created later.

The creation date carries its own precision through the same vocabulary and the
same circa margin as a transfer date, because most of what a museum's object
records hold is imprecise, and a field accepting only exact ISO dates would
push "ca. 1905" back into the bucket it exists to shrink.

PC-004's absent-chain limb consults it too: that limb makes the same inference
PC-001 does and inverts for the same reason.

## The LLM constraint is enforced, not requested

**2026-08-09.** Instructing a model to stay inside its input is not the same as
it doing so, so `src/llm_guard.py` checks every generated sentence against the
exact context it was given and discards anything that introduces new material.
Rejection is the cheap outcome — the deterministic `rule_statement` already
carries the finding — so every threshold is set to over-reject.

German cannot use per-token grounding: the cited fields are English, so an
ordinary German noun never matches them however faithful the sentence is, and
requiring it rejected 100% of live German output. German uses a **run** signal
instead — two or more adjacent capitalised tokens with at least one ungrounded.
A single ungrounded German noun mid-sentence is deliberately not flagged; that
is the accepted cost of German being emittable at all.

Positional capitals are excluded **by identity, not by position**, in every
language. A clause can legitimately begin with a fabricated name, and blind
skipping loses exactly that case.

Details, the run record and the known gaps:
[`scripts/VALIDATION.md`](scripts/VALIDATION.md).

## The LIDO adapter under-maps rather than guesses

**2026-08-11.** Only event types with unambiguous REAO equivalents are mapped;
`data/lido_event_type_map.json` records the rest with reasons.

**Looting is not mapped to `entziehung`**, which under REAO Art. 3 means a
seizure by *state* act — LIDO's term does not separate that from a private
theft. **Repatriation is not mapped to a settled claim**, because that would
hold an object out of the *active* queue on a term that commonly names a
state-to-state return in the colonial context this tool does not cover.

Under-mapping is safe and over-mapping is not: `unknown` is still screened, so
a dated looting event inside a persecution window is still flagged, whereas a
wrong resolved-status suppresses triage. That asymmetry decided every
borderline case.

An ambiguous actor set is rejected rather than resolved by picking, and
precision is never manufactured — a span covering a whole calendar year or
month is rewritten to that year or month, and `um 1938` in a `displayDate` is
not read for its "um".

## The demo is a committed static file, and nothing in its path executes

**2026-08-11.** Generated once, offline, from the bundled synthetic example and
committed as plain HTML at `docs/index.html`. No CI workflow calls the
Infomaniak API and none should be added: this repository has had one real
credential exposure, and the safest architecture is the one with no live
process to leak from.

`scripts/check_publishable.py` scans the working tree, the **git history** and
the built artifact. The history scan is the one that matters — removing a
secret from the tip does not remove it from history, and making a repository
public publishes its history, including the commits GitHub retains behind
`refs/pull/N/head` for every pull request ever opened. Its known-safe list keys
on the fixture *value* rather than the file, because a per-file exemption is
how a real leak hides behind a test fixture.

## Apache 2.0, and what it does not do

**2026-08-14.** The licence is right and is not up for reconsideration. But its
AS IS clause disclaims the *author's* liability and does nothing for an
institution that relies on the output and gets something wrong in a real
restitution matter. That converts the outstanding legal and provenance-research
review from a milestone into a precondition, and the README says so.
