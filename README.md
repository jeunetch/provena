# provena

Open-source provenance triage tool for cultural institutions — flags Nazi-era
ownership-chain gaps and risk indicators for human review, grounded in real
restitution-practice standards (REAO Art. 3, Washington Principles, Terezin
Declaration).

**Status: early development. Not yet reviewed by an admitted lawyer or
professional provenance researcher. See CLAUDE.md for full methodology,
sources, and design rationale.**

**The licence disclaims liability for this project's author, not for you.**
Apache 2.0's AS IS clause protects the author. It does nothing for an
institution that relies on this output and gets something wrong in an actual
restitution matter — that exposure stays with the institution. This is the
concrete reason the outstanding legal and provenance-research review is a
precondition for treating the output as more than a rough triage aid, rather
than a milestone to reach later. Until that review has happened, treat every
flag as a question to put to a researcher and every non-flag as nothing at
all.

This is a triage aid, not a provenance report generator and not a legal
determination. See the Output & Framing section of CLAUDE.md for why it
never produces a numeric risk score or a "clean" verdict.

## Demo

A pre-generated report is committed at [`docs/index.html`](docs/index.html)
and is intended to be served by GitHub Pages at
`https://jeunetch.github.io/provena/`.

**It is a static file, not a service.** It was generated once, offline, from
the bundled synthetic example and committed as plain HTML. Nothing in the
hosting path executes: no backend, no build step, no API call, no upload
form, and no secret anywhere in the serving chain — there is no live process
that could hold one. It cannot accept records and must not be confused with a
tool that does.

Do not point it at real institutional records. A hosted service accepting
real records would contradict both the data-protection posture and the
discovery-risk reasoning below: it would create exactly the record of
institutional notice that the no-score design exists to avoid, at greater
scale.

To rebuild it — **on a local machine, never in CI**:

```sh
python -m src.pipeline examples/example_input.csv --html docs/index.html \
  --llm-language de                      # add --llm-* only with a local .env
python scripts/check_publishable.py --artifact docs/index.html
```

`scripts/check_publishable.py` scans the working tree, the git history and
the built artifact for credential-shaped strings, and refuses an artifact
built from anything other than a bundled synthetic example. It reports how
many machine-generated sentences the artifact contains and does **not**
review them: every one needs reading against the fields printed beside it
before the file is published.

## Quickstart

Python 3.11+. Screening records needs **no third-party packages at all** — the
heuristic layer, both input adapters, the guard, the report and the test suite
run on the standard library. `requirements.txt` carries `requests`, which is
needed only to make a network call: the optional LLM layer and the LIDO
vocabulary script.

```sh
python -m src.pipeline examples/example_input.xml --out report.json --html report.html
python -m src.pipeline examples/example_input.csv --out report.json --html report.html
python -m unittest discover tests
```

The input format is taken from the file extension (`.xml`/`.lido` → LIDO,
`.csv` → CSV) and can be forced with `--format`.

`report.html` is a single self-contained file — no build step, no external
requests, no backend. Open it directly in a browser.

`examples/example_input.csv` is **synthetic**. Every object, transaction,
date, private owner and citation in it is invented for testing, and none of
it refers to a real object or a real event.

The last few rows are the exception, and a deliberate one: they carry party
names taken from the bundled reference lists (Galerie Fischer, Lempertz,
Dorotheum, Haberstock, Wolff Metternich) so the matching rules can be
demonstrated end to end. Those rows record no conduct — their
`transaction_state` is a neutral `purchase`, and each carries a note saying
the object and transaction are invented and the name appears only to exercise
the rule. The tool renders every such match as a citation to the list's own
annotation, never as a characterization.

### What is implemented so far

| | |
|---|---|
| Input | LIDO-XML (`src/lido_adapter.py`), CSV (`src/csv_adapter.py`) |
| Criteria | 13 criteria, 14 rule IDs — see below |
| Output | JSON + static HTML report: coverage map first, work queue second |

### `object_date`, and what PC-001 can and cannot tell you

`object_date` is the object's own creation date (`YYYY`, `YYYY-MM` or
`YYYY-MM-DD`), optional, and it changes what PC-001 means.

PC-001 fires when nothing in the chain predates 1945. For an object *created*
in 1962 that is correct by the rule and useless in practice, and it lands on
the coverage map — the first screen, the thing this README calls more
diagnostic than any per-object output. A mixed collection otherwise reports
"N objects with no pre-1945 provenance" where a large share could not have
had any.

Three states, kept distinct:

| `object_date` | PC-001 | coverage map |
|---|---|---|
| on or after 1945 | not applicable, with the reason in `coverage_note` | counted under `created_after_the_risk_band` |
| before 1945 | fires, and cites the creation date | counted under `with_no_pre_1945_provenance` |
| blank | **still fires** | counted under `with_no_pre_1945_provenance` *and* `creation_date_not_recorded` |

A blank creation date does not switch the rule off. An unrecorded date is not
evidence the object is modern, and disabling the modal rule on an empty column
is the silent false negative this tool exists to avoid — so the flag runs and
says, in its own statement, that it cannot distinguish an object whose early
provenance is undocumented from one created after 1945 that never had any.

A creation date carries its own precision, through the same
`date_precision` vocabulary and the same circa margin as a transfer date —
`object_date`, `object_date_to`, `object_date_precision`. Most of what a
museum's object records hold is imprecise, and a field that accepted only
exact ISO dates would push "ca. 1905" straight back into the bucket it exists
to shrink:

| record says | how to enter it |
|---|---|
| ca. 1948 | `object_date=1948`, precision `circa` |
| 1920s | `object_date=1920`, `object_date_to=1929`, precision `year` |
| 17th century | `object_date=1600`, `object_date_to=1699`, precision `year` |
| before 1930 | `object_date=1930`, precision `before` |

Only the earliest possible day counts, and it is the *widened* one, so the
stated precision governs. `circa 1948` widens to 1943–1953, reaches back
before the threshold, and therefore does **not** step the rule aside — the
same rule the rest of the tool applies, that a widened date can never yield a
certain answer.

**PC-004 consults it too.** That rule's second limb — a covered object class
with no record certainly dated pre-1945 — makes the same inference PC-001
does, and it inverts for the same reason: for an object created after the
band, an absent pre-1945 chain is arithmetic, not the trace of a confiscation
channel. Contemporary silver, studio ceramics, modern Judaica and
commemorative coins are ordinary holdings, and PC-004 has no location
requirement to thin them out.

| Rule | ID | Axis |
|---|---|---|
| No pre-1945 provenance at all | `PC-001` | persecution context |
| Chain begins at institutional acquisition | `PC-002` | persecution context |
| Persecution-window engagement (REAO tier) | `PC-003` | persecution context |
| Object class that moved through chainless channels | `PC-004` | persecution context |
| Confiscation-channel actor match | `PC-005` | persecution context |
| Anonymous owner entry inside the 1933–1950 band | `PC-006` | persecution context |
| Owner named pre-1933 absent from a post-1950 catalogue | `PC-007` | persecution context |
| Restitution recorded to a state, not to heirs | `PC-008` | persecution context |
| Cross-border movement 1933–1945, no export licence | `PC-009` | persecution context |
| Record states a `fluchtgut` transfer (contested category) | `PC-010` | persecution context |
| Missing source citation | `DQ-001` | documentation quality |
| Post-commitment acquisition, thin pre-1945 chain | `DQ-002` | documentation quality |
| ALIU list match, documented-concern entry | `NM-001` | persecution context |
| ALIU list match, exonerating entry | `NM-002` | *not a flag* — see below |

Each object's `coverage_note` states which criteria ran, which were skipped,
and why, so an unflagged object is never mistaken for a
screened-and-cleared one.

### `transaction_state` engages PC-003 directly

PC-003 has **two limbs**, and the second matters more than it looks:

1. the transfer's date overlaps a territory's persecution window;
2. the record's own `transaction_state` records a persecution transfer —
   `entziehung`, `zwangsverkauf`, `vermutung_der_entziehung`,
   `verschaerfte_vermutung` — whatever the territory.

Limb 2 exists because without it `transaction_state` drove no rule at all. It
was consulted only to pick a tier *inside* limb 1's gate, so a state could
select a tier it could never reach on its own. A recorded `zwangsverkauf` in
Geneva produced "screened, no criteria triggered" while an ordinary purchase
in Munich flagged.

**Switzerland is the case that makes this structural.** The onset table has no
Swiss window and correctly never will — Switzerland was not occupied. Every
Swiss record was therefore unreachable by limb 1, including every
`fluchtgut` record, which the methodology names as the category most directly
relevant to this tool's home jurisdiction.

`fluchtgut` is **not** part of limb 2. It is a contested category rather than
a presumption: Swiss practice has historically distinguished property sold by
a refugee already in a safe haven from property taken in occupied territory,
and whether such a sale should found a claim is unsettled. It has its own
criterion, `PC-010`, which asserts **no presumption tier** and states the
question. It is among the parts of this tool most in need of the outstanding
provenance-research and legal review.

### LIDO-XML input

One `lido:event` becomes one internal record; the events under one
`lido:lido` form that object's chain. Element names are matched on their
local name, so the namespace prefix a file uses does not matter.

| Internal field | LIDO source |
|---|---|
| `object_id` | `lido:lidoRecID`, else `administrativeMetadata/recordWrap/recordID` |
| `object_title` | `objectIdentificationWrap/titleWrap/titleSet/appellationValue` |
| `object_class` | `objectClassificationWrap/objectWorkTypeWrap/objectWorkType/term` |
| `object_date` / `object_date_to` / `object_date_precision` | no native mapping — use `PROVENA_EXT:object_date=` etc. |
| `owner_name` | `eventActor/actorInRole/actor/nameActorSet/appellationValue` |
| `owner_name_variants` | that actor's remaining `appellationValue`s |
| `date_from` / `date_to` | `eventDate/date/earliestDate` and `latestDate` |
| `date_precision` | derived from the granularity of those dates |
| `transaction_state` | `eventType`, via `data/lido_event_type_map.json` |
| `location` | `eventPlace/place`, flattened through its `partOfPlace` chain |
| `source_citation` | `eventDescriptionSet/sourceDescriptiveNote` |
| `notes` | `eventDescriptionSet/descriptiveNoteValue` |

Element names were confirmed against the LIDO v1.1 XSD
([`nfdi4objects/lido-schema`](https://github.com/nfdi4objects/lido-schema)).

**Which actor is the owner.** An internal record names the party holding the
object *after* the event. Where an event carries one actor, that is it. Where
it carries several, the one whose `roleActor/term` names a receiving role
(owner, new owner, buyer, recipient, and German equivalents) is used. An
ambiguous set is **rejected**, not resolved by picking: naming the wrong party
in a provenance record is a silent error, and a rejected file is the cheaper
outcome. An event whose only actor is recorded as a seller is rejected for the
same reason. Either can be settled with `PROVENA_EXT:owner_name=`. The other
side of a transfer is not lost — in a well-formed chain the transferring party
is the owner of the preceding event.

**Dates are never made more precise than the record.** LIDO practice commonly
writes a year as `1921-01-01`/`1921-12-31` and a month as
`1934-06-01`/`1934-06-30`. Read literally those are day-precise dates, which
is exactly the false precision the schema exists to prevent, so a span
covering a whole calendar year or month is rewritten to that year or month.
The interval does not change; only the claimed precision does, and only
downwards. `circa`, `before` and `after` cannot be expressed by an ISO date
string at all and must come through the convention below — a `displayDate` of
`um 1938` is **not** parsed for its "um", because reading precision out of
date prose is the free-text inference this project rejects.

**Event types are mapped conservatively.** `data/lido_event_type_map.json`
maps only the terms whose REAO equivalent is unambiguous (Purchase, Gift,
Exchange, Restitution, Unknown provenance event) and records, with reasons,
the ones it deliberately leaves alone. LIDO's *Looting* is not mapped to REAO
`entziehung`, which means a seizure by **state** act: LIDO's term does not
distinguish that from a private theft, so the mapping would assert something
the record does not establish. *Repatriation* is not mapped to a settled
claim, because that mapping would hold an object out of the active queue on a
term that often means a state-to-state return in a colonial context this tool
does not cover. Under-mapping is the safe direction: an unmapped event enters
as `unknown` and is still screened, so a dated looting event inside a
persecution window is still flagged. Every unmapped term produces an **ingest
note**, listed in the JSON and shown above the coverage map in the HTML
report, so a lossy conversion is visible rather than silent.

#### The `PROVENA_EXT:` convention

Several internal fields have no faithful LIDO carrier. They are written as
lines inside `lido:eventDescriptionSet/lido:descriptiveNoteValue`:

```xml
<lido:eventDescriptionSet>
  <lido:descriptiveNoteValue>Consignor not named in the catalogue.
PROVENA_EXT:transaction_state=zwangsverkauf
PROVENA_EXT:export_licence_present=false</lido:descriptiveNoteValue>
  <lido:sourceDescriptiveNote>Auction catalogue, 4 Nov 1936, lot 210</lido:sourceDescriptiveNote>
</lido:eventDescriptionSet>
```

Any internal field name is accepted and always overrides the native mapping —
an explicit statement beats a derivation. An unrecognised name is a validation
error, the same posture the CSV adapter takes towards an unrecognised column.
The remaining prose becomes `notes`.

Five fields have no native carrier at all and no other route in:
`is_institution_acquisition`, `catalogue_reference`,
`owner_stated_in_catalogue`, `restitution_recipient_type`,
`export_licence_present`. `date_precision` needs the convention only for
`circa`/`before`/`after`.

**This is a documented free-text convention, not a standards-compliant LIDO
application profile.** A real profile — an XSD extension plus Schematron
rules — is future work, and this prototype does not claim to have done it.
The gap is specific to LIDO input: all of these fields work natively in CSV,
so no functionality is blocked by it.

### The LLM explanation layer

Off by default. With no `--llm-endpoint` no call is made, every
`llm_explanation` stays `null`, and no record data leaves the machine. The
deterministic `rule_statement` on each flag carries the finding regardless —
the model only ever restates it in plainer words.

```sh
cp .env.example .env        # set INFOMANIAK_PRODUCT_ID and INFOMANIAK_API_KEY
python -m src.pipeline examples/example_input.csv --html report.html \
  --llm-language de
```

Credentials come from `.env` (gitignored, never committed) and are never
hardcoded or passed as flags. The endpoint is built from the product id;
`--llm-endpoint` overrides it if the path ever changes.

Confirmed against a live account on 2026-08-09:

| | |
|---|---|
| Endpoint | `POST https://api.infomaniak.com/2/ai/{product_id}/openai/v1/chat/completions` |
| Model | `swiss-ai/Apertus-v1.5-70B` — verified via the account's `/models`, **not** the id shown in the docs |
| Response | `choices[0].message.content` |

There is still no default endpoint: without a product id the layer does not
run, so it cannot start calling on its own.

**Generated text is verified, not trusted.** Telling a model to stay inside
its input is not the same as it doing so, so every sentence is checked back
against the exact context it was given:

| Check | Catches |
|---|---|
| Numbers absent from the input | invented dates, lots, prices |
| Curated multilingual historical-term list | supplied period context, named Nazi-era figures |
| Per-language finding phrases | "was looted", "proves", "certainly", "low risk" |
| Named material not grounded in the input | invented people, places, organisations |
| Length | elaboration beyond the fields |
| Required question framing | conclusions stated as fact |

A failure retries once with a tightened instruction, then leaves the
explanation `null` and records why in `llm_explanation_status`. **Rejection is
the cheap outcome**, so every threshold is set to over-reject: a withheld
explanation costs a sentence of plain language, while one confabulated
collector biography is uncheckable by the non-expert this tool is built for
and is enough to make an institution discard it.

Against a deliberately hallucinating model the layer produces no explanations
at all, and the report says so per flag rather than falling silent.

#### Live validation — required before relying on this layer

Mocked cases prove the guard rejects the confabulations *we thought of*. They
cannot prove it rejects the ones a real model actually produces. That second
test is a separate, required step:

```sh
python scripts/validate_llm_live.py                    # all four languages
python scripts/validate_llm_live.py --languages en,de --repeats 3
```

It puts real flags in front of the live model — including a name from the
bundled list, a sparse flag, an instruction smuggled through a record field,
and a direct request for historical background — and reports what the guard
did with each. It can fail in **both** directions, and both are real failures:

| Exit | Meaning |
|---|---|
| `0` | nothing the harness can decide automatically; review any accepted output |
| `2` | an adversarial output was accepted **and** `--strict` was passed |
| `3` | **guard too strict** — every baseline output rejected, so the layer produces nothing |
| `4` | not configured |

**An accepted adversarial output is not by itself a failure.** The adversarial
cases bait the model so the guard has something to catch. If the model
declines the bait, the guard is never exercised and there is nothing to catch.
If the model takes it and the guard misses, that is a real failure. The two
look identical from outside, and no automated check separates them — if one
could, the guard would already apply it. So they are printed under REVIEW
REQUIRED and the run exits 0. Pass `--strict` in CI, where an unreviewed
acceptance should block a merge.

Baseline collapse stays an automatic failure: a guard that rejects everything
is broken in a way no human needs to confirm.

Re-run a single case after a fix, rather than the whole battery, and show
what was rejected when tuning:

```sh
python scripts/validate_llm_live.py --list-cases
python scripts/validate_llm_live.py --only direct-elicitation --languages de
python scripts/validate_llm_live.py --languages de --show-rejected
```

`--show-rejected` prints rejected output in full with every violation detail.
The run always ends with a frequency-ranked list of the exact tokens that were
rejected — if ordinary vocabulary appears there, the guard is over-firing on
it.

**Status: validated. Run 7 confirmed the month-grounding fix, with
`direct-elicitation` still rejected in every language.** Re-run the harness
after any change to the guard, the language pack or the prompt — every entry
below is a regression that a plausible-looking change reintroduced.

| Run | Result |
|---|---|
| 1 | 62% baseline; German `direct-elicitation` accepted uncited narrative |
| 2 | elicitation fixed (5/5 rejected) but German baseline **0/12** — too strict |
| 3 | elicitation holds 5/5; German baseline 50% (6/12), rejections traced to positional capitalisation |
| 4 | German recovered to 75% (9/12), both false positives gone; Italian `direct-elicitation` leaked period background 2/3 |
| 5 | all 44 accepted adversarial outputs read clean; German fell to 42% on false `too_long` rejections |
| 6 | counting defect fixed; English lost a baseline set (0/3) for writing month names against ISO dates |

Run 5's German drop was a counting defect, not a limit that was too tight: a
period ending an initial (`Cordes, H.`) counted as a sentence end, so
two-sentence outputs measured three. Abbreviation periods are now masked
before counting. Character limits are language-aware (German 900, English
700); the sentence cap stays at 3 everywhere, because it mirrors an
instruction that is identical in every language.

Run 6's English rejections were the place-name problem in another form. A
record whose `date_from` reads `1934-06` was described as "June 1934", and
"June" was rejected as an introduced proper noun — but writing a date out is a
format translation of the input, not an addition to it. Month names now ground
through `date_equivalence` in the language pack, and only for months the cited
fields actually date: with `1934-06` in front of the model, "June" grounds and
"August" does not. Month *numbers* already grounded, since the number check
splits an ISO date into its parts.

That fix also had to be narrowed once. Matching ISO dates loosely read the
`onset_table_version` string (`2026-08-09b`) that every PC-003 flag carries as
a date, which grounded "August" on all of them; the pattern now rejects a date
with further word or hyphen characters attached.

The layer stays **off by default** regardless. That is not a statement about
whether the guard works — it is the data-protection posture: a run that makes
no call discloses nothing. Validation says the guard rejects what it was built
to reject, not that a generated sentence can be trusted without reading it
against the fields printed beside it.

**Grounding, not capitalisation.** A named token is permitted only if it
traces back to the cited fields, either by appearing in them or by being a
cross-language form of something in them. Cross-language grouping never
permits a token on its own: "Allemagne" passes only where the record says
"Germany".

German works differently, and the reason is worth knowing. The cited fields
are English, so an ordinary German noun never matches them however faithful
the sentence is — requiring per-token grounding there rejected 100% of live
German output. German therefore uses a **run** signal: two or more adjacent
capitalised tokens with at least one ungrounded, which is characteristically a
name rather than prose. Words capitalised only for position — clause-initial
verbs, interrogatives, articles — are excluded by identity rather than by
position, so a clause may still begin with a name and have it detected. A
single ungrounded noun mid-sentence is not flagged; that is the accepted cost
of German being usable at all. Uncited historical narrative is caught by the
historical-term list instead.

**Remaining gap:** nothing here detects a fluent paraphrase that subtly alters
a field's meaning. Month grounding is per-month rather than per-date, so an
output that pairs a grounded month with a grounded year the record never put
together passes — the same paraphrase-drift class, not a new one. This is a
mitigation, not a proof of faithfulness — which is why the layer is off by
default.

**Data protection.** The payload carries only the flag's already-cited
fields. Free-text `notes` are never sent unless a rule cited them, because
widening the disclosure past what the flag needs is the exposure the
revFADP/GDPR posture exists to limit. A test asserts this.

**The Infomaniak request shape is confirmed**, against a real account:
`POST https://api.infomaniak.com/2/ai/{product_id}/openai/v1/chat/completions`,
Bearer-authenticated, OpenAI-compatible, generated text at
`choices[0].message.content`. The Apertus model id is
`swiss-ai/Apertus-v1.5-70B`, verified through the account's `/models` listing —
it is **not** the id used as an example in the documentation, so do not
substitute that one. The client still defaults no endpoint: without a product
id the layer does not run, so a misconfiguration cannot make a call silently.
`product_id` and the API key come from `.env` (see `.env.example`, gitignored)
and are never hardcoded.

### The bundled reference lists are seeds, and that changes how to read them

`data/aliu_red_flag_names.json` (3 entries) and
`data/confiscation_channel_actors.json` (6 entries) both declare themselves
incomplete in their own `_meta.status`. That is load-bearing: **with three
ALIU entries, "no name match" tells you almost nothing.** Every object's
`coverage_note` says so explicitly, and the report renders each list's status
and critical-context note above the work queue. A non-match must never read
as a clear result.

Both loaders validate strictly rather than silently accepting a malformed
entry — an entry missing its annotation, its sources, or its persecution gate
fails to load rather than matching with a gap in it.

### How a reference-list match is rendered

A match is **a citation, never a characterization of a person**. This is a
legal-exposure mitigation, not house style — personality rights under Swiss
ZGB Art. 28 and potentially StGB Art. 173 attach to living heirs and
descendants, and Germany recognises a post-mortem personality right.

Three distinctions are carried by the matching logic itself, not left to
prose a reader may not reach:

- **Exonerating entries are not flags.** An ALIU entry typed `exonerating`
  records that a name was investigated and cleared, or that the person acted
  protectively. It leaves the heuristic layer on a separate per-object
  channel (`name_list_citations`), never in a flag list, and renders on its
  own panel marked *not a flag · does not place this object in the queue*.
  Rendering it as a concern would invert the record's meaning.
- **Per-limb persecution gating.** Each actor entry carries one or more
  documented bases, each with its own sources and an explicit
  `implies_persecution_of_former_owner`. Where no limb bears on persecution
  of a private former owner, the match may only be rendered as bearing on an
  individual if the record's own `transaction_state` establishes it
  independently — otherwise the flag says in terms that it does **not**
  indicate persecution of an individual former owner. The loader refuses to
  default that field: an entry omitting it fails to load.
- **Source strength travels with the match.** An entry carrying a
  `verification_note` renders it next to every match, so a weaker-sourced
  entry never reads with the confidence of a well-documented one.

### The report

The coverage map comes first — total objects, how many carry any pre-1945
provenance, how many carry none, how many begin at the institution's own
accession, how many are previously resolved. That is what a researcher needs
to scope work, and it is more diagnostic than any per-object output.

The work queue comes second. It groups objects by **which criteria
triggered** — not by severity. There is no score, no grade, and no ranking of
objects within a group. Group markers are shape-coded rather than
colour-coded specifically so that nothing reads as a traffic light, and the
grouping is computed at render time rather than stored in the JSON: a
persisted per-object band would be exactly the graded, discoverable record
that the no-score reasoning exists to prevent.

Objects whose chain records a restitution or settlement are held out of the
active queue rather than re-flagged as open — with one deliberate exception.
`PC-008` (settlement recorded to a state rather than to heirs) only ever
fires on chains containing a restitution record, so applying the hold-out
there would make the rule unreachable: it exists to ask whether a recorded
settlement actually reached the dispossessed family, and suppressing it on
the strength of the settlement it questions defeats it. Objects carrying a
`PC-008` flag are shown in the queue. This is a display decision only — the
stored `resolved_status` is unchanged, and objects settled with named heirs
are held out as before.

### Modelling decisions that need expert confirmation

These were made during implementation and are **not** drawn from a cited
source. They are flagged here, in `CLAUDE.md`, and in the data files
themselves, and they need review by a provenance researcher or an admitted
lawyer before this is presented as more than a documented-methodology
prototype.

- **The characterisation of `fluchtgut` as a contested category is mine, not
  a cited source's.** `PC-010`'s methodology text states that Swiss practice
  has historically distinguished property sold by a refugee already in a safe
  haven from property taken in occupied territory, and that no REAO
  presumption attaches to it. That framing comes from this project's own
  specification, not from a source I can cite, and it is doing real work: it
  is the reason `fluchtgut` asserts no presumption tier while the four other
  persecution states engage one. If the distinction is wrong, or is narrower
  than stated, the rule under-reports the category the methodology names as
  most relevant to this tool's home jurisdiction. This is the newest of the
  entries in this list and the least examined.
- **Italy is modelled as two persecution windows**, not one: 1938–Aug 1943
  (racial laws) engages the ordinary presumption, Sep 1943–1955 the
  heightened presumption. The global 15 Sept 1935 Nuremberg threshold is not
  applied to Italy, because it does not describe that chronology — using it
  would report every 1938–43 Italian transfer at the heightened tier.
  Screening only from Sep 1943 was the alternative, and under-flagging
  1938–43 is the more dangerous error.
- **The actor list's persecution gate was added by transcription.** The file
  as supplied states the Galerie Fischer distinction in prose — the 1939 sale
  concerned works taken from German state museums, "a different legal
  category from persecuted-individual forced sales", while the broader claim
  "needs its own case-level verification". Deriving that at match time by
  keyword-sniffing the prose is exactly the inference this project rejects, so
  `implies_persecution_of_former_owner` was written into the data file per
  basis limb, each carrying a `gate_basis` that quotes the entry's own words.
  Galerie Fischer's basis was split at the entry's own "Separately,"; all
  original prose is preserved verbatim and a test asserts the limbs rejoin to
  the original text. No new claim is made about any entity — but a researcher
  should confirm the transcription.
- **The `circa` margin (default ±5 years) is a placeholder with no sourced
  basis.** It is not a validated field-practice figure. It widens a `circa`
  date only so overlap questions can be asked of it; it never produces a
  numeric interval length, and a widened date can never yield a "certain"
  overlap. Configure it with `--circa-margin-years`; every report echoes the
  value used along with this caveat.

### Input columns

Column names are the internal schema field names. `object_id`, `owner_name`
and a `date_precision` column are required; unknown columns are rejected
rather than ignored. `owner_name_variants` is pipe-separated.

`date_precision` (`exact` / `month` / `year` / `circa` / `before` / `after`)
is load-bearing: a stated precision finer than the date supplied is a
validation error, and a comparison involving a `circa`, `before` or `after`
date is reported as `"overlap_certainty": "possible"`, never `"certain"`. No
numeric interval length is ever computed between two imprecise dates.

Four optional columns are **additions to the schema in CLAUDE.md**, each
because the rule that needs it is not computable from the listed fields and
the alternative is inferring it from free text. Where one is blank the
dependent rule reports itself *skipped*, never "not triggered".

| Column | Needed by |
|---|---|
| `is_institution_acquisition` | `PC-002`, `DQ-002` — which event is the accession |
| `catalogue_reference` | `PC-007` — which records are published catalogue entries |
| `owner_stated_in_catalogue` | `PC-007` — whether that entry names an owner |
| `restitution_recipient_type` | `PC-008` — state vs. individual/heirs |

`restitution_recipient_type` (`individual_or_heirs` / `state_or_institution` /
`unknown`) is only meaningful on a `restitution_erfolgt_or_vergleich` record;
setting it elsewhere is a validation error, so it cannot look recorded when
nothing consumes it.

### Configurable data files

| File | Drives |
|---|---|
| `data/persecution_onset_table.json` | territory windows and presumption tiers (`PC-003`, `PC-004`) |
| `data/anonymous_owner_patterns.json` | anonymous-owner patterns and the 1933–1950 band (`PC-006`) |
| `data/confiscation_channel_actors.json` | actor entries with cited bases (`PC-005`) — seed list, see above |
| `data/aliu_red_flag_names.json` | ALIU entries with annotations and sources (`NM-001`/`NM-002`) — seed list, see above |
| `data/lido_event_type_map.json` | LIDO Event Type → REAO `transaction_state`, and the terms deliberately left unmapped |
| `data/llm_output_language.json` | output languages, guard vocabulary, equivalence and date groups |

`--institution-commitment-date` sets the Washington Principles commitment
date for `DQ-002`. It defaults to 1998-12-03 and the date applied — and
whether it was the default — is stated in the flag and in the report.
`config.example.yaml` is a **reference sheet for these flags, not
configuration**. This build has no YAML interface: editing that file, or
copying it to `config.yaml`, changes nothing. Each setting in it names the
flag that actually controls it, and the pipeline prints a warning if it finds
a `config.yaml`, so the no-op is at least a loud one.

## License

Apache 2.0 — see [LICENSE](LICENSE). Chosen for the explicit patent grant and
change-tracking requirement, both of which matter to the institutional legal
and IT reviewers who are part of this tool's audience.

Note what the licence does and does not do. Its AS IS and limitation-of-
liability clauses disclaim the *author's* liability to you. They do not
allocate any risk away from an institution that acts on this output: if a flag
is wrong, or an absent flag is read as a clearance, the consequences in a real
restitution matter fall on the institution, not on this project or its
licence. No open-source licence changes that, and picking a different one
would not either. What changes it is expert review of the methodology and of
the name-matching exposure, which has not happened yet.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Methodology corrections from
provenance researchers and lawyers are the contributions this project most
needs — more than code.
