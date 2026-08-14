# provena

Open-source triage tool for Nazi-era provenance risk. It reads a set of
ownership records and reports, per object, **qualitative flags with cited
evidence** — which objects need a researcher's attention, and why.

It never produces a numeric score, a ranking, or a "clean" verdict.

- **[What a reviewer should check first](#what-a-provenance-researcher-should-check-first)** — the modelling decisions not drawn from a cited source
- **[The criteria](#the-criteria)** — all 14 rule IDs and what each asks
- **[How a flag reads](#how-a-flag-reads)** · **[The schema](#the-schema)** · **[Disclaimer](#disclaimer)**
- **[Demo](#demo)** — a pre-generated static report against synthetic data

Why it is built the way it is: **[DECISIONS.md](DECISIONS.md)**. The build
specification: [CLAUDE.md](CLAUDE.md). Neither is needed to evaluate the
methodology.

---

## Status

**Not reviewed by an admitted lawyer or a professional provenance researcher.**
That review is a **precondition**, not a milestone.

Apache 2.0's AS IS clause disclaims liability for *this project's author*. It
does nothing for an institution that relies on the output and gets something
wrong in an actual restitution matter — that exposure stays with the
institution. Until the review has happened, treat every flag as a question to
put to a researcher, and every non-flag as nothing at all.

Bus factor is one. No external methodological review has taken place.

---

## What a provenance researcher should check first

Four modelling decisions are **transcription, not citation**. None is drawn
from a source that can be cited, and three of the four change what the tool
outputs rather than only how it words things. They are the shortest honest
answer to "where would this be wrong?"

### 1. `fluchtgut` is treated as a contested category, so it asserts no tier

`PC-010` states that Swiss practice has historically distinguished property
sold by a refugee already in a safe haven from property taken in occupied
territory, and that no REAO presumption attaches to it. **That framing comes
from this project's own specification, not from a source I can cite**, and it
is load-bearing: it is the sole reason `fluchtgut` asserts no presumption tier
while the other four persecution states engage one. If the distinction is wrong
or narrower than stated, the rule under-reports the category the methodology
names as most relevant to this tool's home jurisdiction.

*Newest of the four and the least examined.* **Changes output.**

### 2. Italy is modelled as two persecution windows, not one

1938–Aug 1943 (racial laws) engages the ordinary presumption; Sep 1943–1955 the
heightened one. The global 15 September 1935 Nuremberg threshold is not applied
to Italy, because it does not describe that chronology — applying it would
report every 1938–43 Italian transfer at the heightened tier. Screening only
from Sep 1943 was the alternative, and under-flagging 1938–43 is the more
dangerous error.

**Changes output.**

### 3. The actor list's persecution gate was written in by transcription

Each entry's `documented_basis` limbs carry an explicit
`implies_persecution_of_former_owner`, and the loader refuses to default it. The
file as supplied states the Galerie Fischer distinction in prose — the 1939 sale
concerned works taken from German state museums, "a different legal category
from persecuted-individual forced sales", while the broader claim "needs its own
case-level verification". Deriving that at match time by keyword-sniffing prose
is the inference this project rejects, so it was transcribed into the data file,
each limb carrying a `gate_basis` quoting the entry's own words. The basis was
split at the entry's own "Separately,"; all original prose is preserved verbatim
and a test asserts the limbs rejoin to it.

No new claim is made about any entity — but the transcription needs confirming.
**Changes output.**

### 4. The `circa` margin (±5 years) is a placeholder

No sourced basis, not a validated field-practice figure. It widens a `circa`
date only so overlap questions can be asked of it; it never computes or reports
an interval length, and a widened date can never yield a `certain` overlap.
Configurable per run (`--circa-margin-years`) and echoed in every report with
that caveat.

*Wording only — it cannot manufacture a flag on its own.*

### Also worth knowing

**The screening band's lower bound is the onset table's earliest onset, not a
fixed 1933.** The table is configurable and extensible, so adding a territory
with an earlier onset silently widens the band — including the boundary at which
`PC-003`'s state limb declines to assert a tier. Probably correct, but not
predictable from reading either file.

**The bundled reference lists are seeds.** See
[below](#the-bundled-lists-are-seeds-and-that-changes-how-to-read-a-non-match).

---

## The criteria

13 criteria, 14 rule IDs. Every flag names its methodological source and cites
the exact record fields it relied on.

| Rule | ID | Axis |
|---|---|---|
| No pre-1945 provenance recorded | `PC-001` | persecution context |
| Chain begins at institutional acquisition | `PC-002` | persecution context |
| Persecution-window engagement, or a state that records one | `PC-003` | persecution context |
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
| ALIU list match, **exonerating** entry | `NM-002` | *not a flag* |

**Two axes, never merged into one number.** A well-documented 1935 Vienna sale
is a risk problem; a 1650 painting with an unbroken chain and no citations is a
records problem. `DQ-002` sits on the documentation axis because it concerns how
the institution acquired the object against its own stated standard, and asserts
nothing about the object's history.

Methodological grounding: REAO (*Rückerstattungsanordnung*) Art. 3 presumption
tiers and the 15 September 1935 Nuremberg threshold; the Washington Principles
(1998); the Terezin Declaration (2009); the German *Handreichung*. The risk band
runs to **1955**, not 1945 — post-war Collecting Point restitutions, internal
restitution failures in France and the Netherlands, and 1948–54
heirless-property dispersal are a distinct, heavily contested band.

### Three rules whose behaviour is easy to misread

**`PC-003` has two limbs.** A transfer engages it when its date overlaps a
territory's persecution window, **or** when the record's own `transaction_state`
names a persecution transfer (`entziehung`, `zwangsverkauf`,
`vermutung_der_entziehung`, `verschaerfte_vermutung`) regardless of territory.
The second limb matters most in Switzerland, which has no persecution window and
correctly never will. Where the record's date cannot be placed on one side of
the Nuremberg threshold — an imprecise date straddling it, or a date outside the
band — **no tier is asserted**, with the reason stated.

**`PC-001` and `PC-004` consult the object's creation date.** An object created
in 1962 cannot have pre-1945 provenance, so reporting its absence is noise on
the first screen. Where `object_date` is blank the rules still run: an
unrecorded date is not evidence the object is modern, and the flag says it
cannot distinguish undocumented early provenance from an object created later.

**`NM-002` is not a flag.** An exonerating ALIU entry records that a name was
investigated and cleared, or that the person acted protectively. It leaves the
heuristic layer on a separate channel, `name_list_citations`, and never enters a
flag list.

---

## How a flag reads

```json
{
  "object_id": "OBJ-001",
  "screening_statement": "Screened against 13 criteria; ...",
  "persecution_context_flags": [
    {
      "rule_id": "PC-003",
      "rule_statement": "Unverified: was this transfer made by a persecuted person, and if so was fair value received ...?",
      "methodology": "REAO Art. 3 presumption ...",
      "cited_fields": { "owner_name": "...", "date_span": "1936-11-04 (exact)" },
      "presumption_tier": "heightened",
      "llm_explanation": null
    }
  ],
  "documentation_quality_flags": [],
  "name_list_citations": [],
  "resolved_status": "unresolved",
  "coverage_note": "which criteria ran, which were skipped, and why"
}
```

Every flag is phrased as **an open question, not a finding**. `rule_statement`
is the tool's own deterministic wording and is always present.

**An object with no triggered criteria** reads: *"Screened against 13 criteria;
no criteria triggered. Absence of flags is not evidence of unproblematic
provenance and does not constitute provenance research."* There is no "clean",
no "cleared", no "low risk".

**`coverage_note` distinguishes a criterion that could not run from one that ran
and found nothing.** Every rule depending on an optional field reports itself
*skipped* when that field is blank. This distinction is load-bearing: an
unflagged object must never be mistaken for a screened-and-cleared one.

**`resolved_status`** holds previously settled objects out of the active queue,
and is human-settable. Objects leave the queue only when a named person marks
them researched — never automatically.

**Work-queue banding is display-only.** The HTML report groups objects to answer
"what do I look at first". That grouping is computed at render time and
deliberately not written into the stored JSON: a persisted per-object band is
the graded, discoverable record the no-score design exists to prevent.

---

## The bundled lists are seeds, and that changes how to read a non-match

`data/aliu_red_flag_names.json` (3 entries) and
`data/confiscation_channel_actors.json` (6 entries) both declare themselves
incomplete. **With three ALIU entries, "no name match" tells you almost
nothing.** Every object's `coverage_note` says so, and the report renders each
list's status above the queue. A non-match must never read as a clear result.

### How a name match is rendered, and why

Never as a characterization of a person — only as a citation: *the name appears
in a named historical record, and this carries no finding as to any specific
transaction.* Every match reproduces the list's own annotation in full.

This is a legal-exposure mitigation, not house style. Personality rights under
Swiss ZGB Art. 28 and potentially StGB Art. 173 attach to living heirs and
descendants, and Germany recognises a post-mortem personality right. **Do not
soften the disclaimer wording or drop the source fields.**

**A shared surname is not a shared identity.** A supplied name that identifies a
person is matched *as* a person: a conflicting given name is a non-match, and
only the first given name identifies. Where a record gives a bare surname the
match carries `identity_confirmed: false` and says so **in the flag's own
statement**, not only in a field beneath it. `entry_kind` states on every match
whether the entry names an individual or an organisation.

`NM-001` is gated to the screening band, so a modern acquisition cannot raise a
name flag.

---

## The schema

One row per event; rows sharing an `object_id` form that object's chain.

| Column | |
|---|---|
| `object_id` | **required** |
| `owner_name` | **required** — use an explicit placeholder for an unrecorded owner |
| `date_precision` | **required whenever a date is given** — `exact` / `month` / `year` / `circa` / `before` / `after` |
| `date_from`, `date_to` | `YYYY`, `YYYY-MM` or `YYYY-MM-DD` |
| `transaction_state` | REAO taxonomy — see below |
| `object_title`, `object_class`, `location`, `source_citation`, `notes` | optional |
| `object_date`, `object_date_to`, `object_date_precision` | the object's own creation date, with its own precision |
| `owner_name_variants` | pipe-separated alternates and transliterations |
| `export_licence_present` | `PC-009` |
| `is_institution_acquisition` | `PC-002`, `DQ-002` — which event is the accession |
| `catalogue_reference`, `owner_stated_in_catalogue` | `PC-007` |
| `restitution_recipient_type` | `PC-008` — `individual_or_heirs` / `state_or_institution` / `unknown` |

Blank optional columns make the dependent rule report itself **skipped**, not
"not triggered". Unknown columns are rejected outright: a misspelled header
would otherwise silently drop the data it carries.

**`transaction_state`** — `entziehung` (seizure by state act), `zwangsverkauf`
(sale under persecution), `vermutung_der_entziehung` (rebuttable presumption,
pre-15 Sept 1935), `verschaerfte_vermutung` (heightened presumption, on or
after), `fluchtgut` (sold by a refugee in a safe haven),
`restitution_erfolgt_or_vergleich` (already restituted or settled), `purchase`,
`gift`, `exchange`, `unknown`.

**Date precision is enforced, not advisory.** A stated precision finer than the
value supplied (`exact` with `1938`) is a validation error, not a silent
coercion. A comparison involving `circa`, `before` or `after` reports
`overlap_certainty: "possible"` and can never report `"certain"`.

**Creation dates are usually imprecise, and the schema expects that:**

| record says | how to enter it |
|---|---|
| ca. 1948 | `object_date=1948`, precision `circa` |
| 1920s | `object_date=1920`, `object_date_to=1929`, precision `year` |
| 17th century | `object_date=1600`, `object_date_to=1699`, precision `year` |
| before 1930 | `object_date=1930`, precision `before` |

### LIDO-XML input

One `lido:event` becomes one internal record; element names are matched on their
local name, so a file's namespace prefix does not matter. Names were confirmed
against the LIDO v1.1 XSD
([`nfdi4objects/lido-schema`](https://github.com/nfdi4objects/lido-schema)).

| Internal field | LIDO source |
|---|---|
| `object_id` | `lido:lidoRecID`, else `recordWrap/recordID` |
| `object_title` | `titleWrap/titleSet/appellationValue` |
| `object_class` | `objectWorkTypeWrap/objectWorkType/term` |
| `owner_name` | `eventActor/actorInRole/actor/nameActorSet/appellationValue` |
| `date_from` / `date_to` | `eventDate/date/earliestDate` and `latestDate` |
| `transaction_state` | `eventType`, via `data/lido_event_type_map.json` |
| `location` | `eventPlace/place`, flattened through `partOfPlace` |
| `source_citation` | `eventDescriptionSet/sourceDescriptiveNote` |

**Fields with no LIDO carrier use a documented free-text convention**, written
inside `lido:eventDescriptionSet/lido:descriptiveNoteValue`:

```xml
<lido:descriptiveNoteValue>Consignor not named in the catalogue.
PROVENA_EXT:transaction_state=zwangsverkauf
PROVENA_EXT:export_licence_present=false</lido:descriptiveNoteValue>
```

Any internal field name is accepted and always overrides the native mapping — an
explicit statement beats a derivation. An unrecognised name is a validation
error. Five fields have no native carrier at all:
`is_institution_acquisition`, `catalogue_reference`,
`owner_stated_in_catalogue`, `restitution_recipient_type`,
`export_licence_present`; `object_date` and `circa`/`before`/`after` precision
need it too.

**This is a convention, not a standards-compliant LIDO application profile.** A
real profile — an XSD extension plus Schematron rules — is future work this
prototype does not claim to have done. The gap is specific to LIDO input: all of
these fields work natively in CSV.

**The adapter under-maps rather than guesses.** *Looting* is not mapped to REAO
`entziehung`, which means a seizure by *state* act; *Repatriation* is not mapped
to a settled claim. An unmapped event still screens as `unknown`, and every
unmapped term produces an **ingest note** rendered above the coverage map, so a
lossy conversion is visible rather than silent.

---

## Disclaimer

> This output is machine-generated triage. It is not a provenance report, not
> provenance research, and not a legal determination. It has not been reviewed
> by a provenance researcher or by an admitted lawyer. It records only what the
> supplied records do and do not say, measured against the criteria listed in
> the file; it verifies nothing against any external source, and no external
> source was consulted. A triggered criterion is a question for a human
> researcher. An untriggered criterion is not a clearance: absence of flags is
> not evidence of unproblematic provenance. Nothing here should be represented,
> internally or externally, as a finding about any object, transaction or named
> person.

Adapted from the use-conditions precedent of the German Lost Art Foundation and
the Getty Provenance Index. It appears on every report.

## Data protection

The pipeline processes personal data about identifiable persons — collectors,
heirs, claimants — which is in scope of the Swiss revFADP and, for many
institutions, the GDPR.

**The optional LLM layer is off by default**, and a run that makes no call
discloses nothing. When enabled it calls Apertus via Infomaniak (Geneva), which
keeps the processing in-jurisdiction, and the payload carries **only the flag's
already-cited fields** — free-text `notes` are never sent unless a rule cited
them. A test asserts this.

Generated sentences are checked back against the exact fields they were given
and discarded if they introduce anything new; the deterministic
`rule_statement` carries the finding regardless. The guard's design, its known
gaps and the live-validation record:
[`scripts/VALIDATION.md`](scripts/VALIDATION.md).

---

## Demo

A pre-generated report is committed at [`docs/index.html`](docs/index.html), to
be served by GitHub Pages at `https://jeunetch.github.io/provena/`.

**It is a static file, not a service.** Generated once, offline, from the
bundled synthetic example and committed as plain HTML. Nothing in the hosting
path executes: no backend, no build step, no API call, no upload form, and no
secret anywhere in the serving chain — there is no live process that could hold
one. It cannot accept records and must not be confused with a tool that does.
Do not point it at real institutional records.

**The committed demo contains zero machine-generated sentences.** The LLM layer
is undemonstrated in the deliverable; the report states this at the top of every
build.

## Quickstart

Python 3.11+. Screening needs **no third-party packages** — the heuristic layer,
both adapters, the guard, the report and the test suite run on the standard
library. `requirements.txt` carries `requests`, needed only to make a network
call.

```sh
python -m src.pipeline examples/example_input.xml --out report.json --html report.html
python -m src.pipeline examples/example_input.csv --out report.json --html report.html
python -m unittest discover tests
```

Format is taken from the extension (`.xml`/`.lido` → LIDO, `.csv` → CSV) and can
be forced with `--format`. `report.html` is self-contained: no build step, no
external requests, no backend.

To rebuild the demo — **locally, never in CI**:

```sh
python -m src.pipeline examples/example_input.csv --html docs/index.html --llm-language de
python scripts/check_publishable.py --artifact docs/index.html
```

`check_publishable.py` scans the working tree, the git history and the artifact
for credential shapes, and refuses an artifact built from anything but a bundled
synthetic example. It reports how many machine-generated sentences the artifact
contains and does **not** review them: every one needs reading against the
fields printed beside it before publication.

`examples/example_input.csv` is **synthetic**. Every object, transaction, date,
private owner and citation is invented. The last rows are a deliberate
exception: they carry party names from the bundled reference lists so the
matching rules can be demonstrated, with a neutral `transaction_state` and a
note saying the name appears only to exercise the rule.

### Configurable data files

| File | Drives |
|---|---|
| `data/persecution_onset_table.json` | territory windows and presumption tiers |
| `data/anonymous_owner_patterns.json` | `PC-006`'s patterns and its 1933–1950 band |
| `data/confiscation_channel_actors.json` | `PC-005` — seed list |
| `data/aliu_red_flag_names.json` | `NM-001`/`NM-002` — seed list |
| `data/lido_event_type_map.json` | LIDO Event Type → REAO state, and the terms left unmapped |
| `data/llm_output_language.json` | output languages and guard vocabulary |

`--institution-commitment-date` sets the Washington Principles commitment date
for `DQ-002`, defaulting to 1998-12-03; the date applied, and whether it was the
default, is stated in the flag and the report. `config.example.yaml` documents
the same settings but **is not read** — the CLI flags are the live interface,
and the pipeline warns if a `config.yaml` exists.

---

## Explicit non-goals

No numeric score under any framing. No autonomous fetching of any external
database or registry — the tool processes only what it is given. No auth layer,
no database, no hosting config. No OCR ingestion, no signed output reports
(both named as later, separate phases).

**Colonial-context provenance is a separate tool, not a date-range parameter.**
There is no burden-shifting instrument equivalent to MG Law 59, claimants are
often states or communities rather than individuals, an ownership chain usually
does not exist, and human-remains and sacred-object handling has nothing to do
with this design. Claiming "same tool, different dates" would be read as naive
by anyone with field knowledge.

## Contributing

What this project most needs is **methodology review, not code** — see
[CONTRIBUTING.md](CONTRIBUTING.md) and the
[four decisions above](#what-a-provenance-researcher-should-check-first).

Bug reports must use synthetic input. A real provenance record in a public
tracker is a disclosure of personal data.

## License

Apache 2.0 — see [LICENSE](LICENSE). Chosen for its explicit patent grant and
change-tracking, both relevant when institutional legal and IT counsel are part
of the audience. See [Status](#status) for what the AS IS clause does *not* do.
