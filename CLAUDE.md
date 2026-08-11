# Build Prompt: Open-Source Provenance Triage Tool

## Context
Standalone, self-hostable, open-source tool for cultural institutions (museums, foundations, galleries) to **triage** collection records for Nazi-era provenance risk — surfacing which objects need a human researcher's attention and why, never determining or scoring whether an object is "clean" or "looted." Built for Swiss Prototype Fund application (Responsible & Sustainable AI category). Not tied to any client's specific schema or infrastructure.

**This is a triage queue, not a risk assessment tool, and not a provenance report generator.** That framing is load-bearing throughout this spec — see Output and Framing sections below for why.

**License:** Apache 2.0 — matches Apertus's license, and the explicit patent-protection clause and change-tracking are relevant given institutional legal/IT counsel are part of the audience reviewing this tool. Decided, not open for reconsideration during implementation.

**Positioning constraint:** this tool must stand alone. Do not reference jeunet.ch, ArtNode, or any specific client/institution in code, docs, comments, or example data. Any real institutional data patterns (table names, field names) from prior work must NOT appear — this ships as a generic, reusable tool.

**Methodology grounding:** this spec's heuristics and taxonomy are grounded in real restitution-practice standards (REAO Art. 3, Washington Principles 1998, Terezin Declaration 2009, the German Handreichung) rather than invented categories. This was developed with input resembling expert review but has **not** been reviewed by an admitted lawyer or professional provenance researcher — flag this honestly in the README and the grant application, and pursue actual expert review during the prototyping phase (see Advisor Targets section).

---

## Core Concept

Input: a set of provenance records (one per artwork/object) — a chronological ownership chain with dates, owners, transaction/event types, sources, and (critically) **date precision**.

Output, per object: **qualitative flags with cited evidence**, never a numeric risk score. Every flag traces to a specific field in the source record and states which element of a recognized legal/methodological standard it engages (e.g. "engages the REAO Art. 3 heightened presumption; record contains no evidence of fair value or free disposal of proceeds") — not an invented probability.

The **first screen** a user sees is a coverage map (how many objects have any pre-1945 provenance at all, how many have none, how many have a chain that begins at institutional acquisition) — this is what a researcher actually needs to plan work, and is more diagnostic than any per-object score. A ranked triage queue is the second screen, not the first.

Two-layer classification:
1. **Deterministic heuristic layer** (pure Python, no LLM) — checks structural and methodological red flags against a real legal taxonomy (see Heuristic Layer below), operating on two **separate, never-conflated axes**: persecution-context risk and documentation quality. A missing citation is a documentation-quality issue, not automatically a risk signal — conflating the two was an earlier design flaw, corrected here.
2. **LLM explanation/synthesis layer** (Apertus via Infomaniak API) — restates the triggered rule against the cited record fields, in plain language, in the institution's chosen language. **The LLM is strictly forbidden from supplying any external knowledge** (biography, historical facts, or context not present in the input record) — it may only restate what the rule and the record fields already say. An unconstrained model asked to explain a flag will confabulate plausible-sounding biography with total fluency; this failure mode is worse than no explanation, because it's uncheckable by the non-expert user the tool is for. The LLM does not assign risk and does not decide anything — see Output section for why storing even a qualitative "conclusion" is dangerous, let alone a score.

---

## Tech Stack
- **Python 3.11+** for the classification pipeline (matches existing stack conventions).
- **No web framework** for v1 — CLI/library tool first: `pip install`-able package, run against a LIDO-XML/CSV/JSON file, output JSON + HTML report.
- Minimal **vanilla HTML/CSS/JS** static report viewer (single self-contained file, no build step) — doubles as the demo artifact. Must render the coverage map first, ranked queue second (see Output).
- Dependencies: keep minimal. Expect: `requests` (Infomaniak API calls), plain `csv`/`json`/`xml.etree.ElementTree` (no pandas unless a concrete need arises — flag before adding it), no ORM, no async framework.
- Do NOT assume the Infomaniak/Apertus API request/response shape — fetch and confirm current docs before writing the client code. Stop and ask for API key/endpoint config rather than guessing.

---

## Input Formats & Schema (v1)

**Formats, in priority order:** LIDO-XML (primary, vendor-neutral, event-structured) → CSV/XLSX (fallback) → JSON (cheap once schema exists). See prior LIDO mapping work: `eventActor`/`actorInRole`/`roleActor` for owners and direction, `eventDate`/`earliestDate`/`latestDate` for dates, `eventType` for transaction type, `eventPlace` for location, `eventDescriptionSet`/`provenanceHistory` for free text.

**Do not build in v1:** vendor-specific adapters (LIDO covers the structured case), OCR/scanned-document ingestion (separate large problem, name as future work), signed/tamper-evident output reports (future work).

**Internal schema** (revised — date precision and the REAO-grounded state field are new, replacing the earlier invented enum):

- `object_id` (string, required)
- `object_title` (string, optional)
- `object_class` (string, optional but strongly recommended — e.g. "painting", "silver", "Judaica", "decorative art", "coin". Drives object-type-specific rules; see Heuristic Layer)
- `owner_name` (string, required)
- `owner_name_variants` (list of strings, optional — alternate spellings/transliterations; matching must account for these or it will miss most of what it should catch, per expert review)
- `date_from` / `date_to` (string, required/nullable)
- `date_precision` (enum: exact / month / year / circa / before / after — **required, new field**. Never compute a gap across two `circa` values as if both were exact; a gap calculation between two imprecise dates should be flagged as "gap uncertain due to date precision," not given a false-precision numeric length)
  - **Resolved 2026-08-09:** a stated precision finer than the date supplied (`exact` with `1938`) is a validation error, not a silent coercion. A comparison involving a `circa`/`before`/`after` date reports `overlap_certainty: "possible"` and can never report `"certain"`.
  - **The `circa` widening margin (default ±5 years) is an unsourced placeholder, not a validated field-practice figure.** It exists only so overlap questions can be asked of a circa date; it is never used to compute or report an interval length. It is configurable per run (`--circa-margin-years`) and is echoed in every report's `date_precision_handling` block with that caveat attached. Do not let it acquire the appearance of authority — it needs expert input like everything else pre-review.
- `transaction_state` (enum, **replaces the earlier invented `transaction_type` list** — grounded in REAO Art. 3's actual taxonomy, used in German/Austrian restitution practice: `entziehung` [seizure by state act], `zwangsverkauf` [sale under persecution], `vermutung_der_entziehung` [rebuttable presumption — persecuted-person transfer pre-15 Sept 1935], `verschaerfte_vermutung` [heightened presumption — post-15 Sept 1935], `fluchtgut` [sold by refugee in a safe haven — the Swiss-specific category, directly relevant to this project's home jurisdiction], `restitution_erfolgt_or_vergleich` [already restituted/settled — see Resolved Status below], `purchase`, `gift`, `exchange`, `unknown`. Do not invent additional categories without a cited source.)
- `location` (string, optional)
- `source_citation` (string, optional)
- `export_licence_present` (boolean, optional — relevant for 1933–1945 cross-border movements specifically)
- `is_institution_acquisition` (boolean, optional — **added 2026-08-09, approved**. Marks the institution's own accession event. Rule 2 below is not computable without it: nothing else in this schema identifies which event is the accession, and inferring it from `owner_name` is the kind of guess that produces silent false negatives. Where the column is blank the rule must report itself *skipped* in `coverage_note`, never "not triggered" — a criterion that could not run and a criterion that ran and found nothing are different states and must not be collapsed.)
- `catalogue_reference` (string, optional — **added 2026-08-09 for rule 8**. The published catalogue in which the object appears for this record. Distinct from `source_citation`, which is the documentary source for the *transaction*; rule 8 compares published entries across time, which is a different thing from citing a transfer.)
- `owner_stated_in_catalogue` (boolean, optional — **added 2026-08-09 for rule 8**. Whether that published entry names an owner. Needed because `owner_name` is required and non-empty, so "the catalogue no longer names anyone" cannot otherwise be represented at all. Detecting it by sniffing `source_citation` or `notes` for catalogue-like text is exactly the inference this schema rejects elsewhere.)
- `restitution_recipient_type` (enum, optional: `individual_or_heirs` / `state_or_institution` / `unknown` — **added 2026-08-09 for rule 10**. Only meaningful on a `restitution_erfolgt_or_vergleich` record; setting it elsewhere is a validation error, so it cannot look recorded when nothing consumes it. Blank means the rule reports itself skipped.)
- `notes` (string, optional — free text feeding the LLM layer, constrained per above)

Multiple records per `object_id` form the ownership chain, ordered by date.

### LIDO adapter mapping (confirmed against the live LIDO Terminology SPARQL endpoint and the official LIDO Primer)

`eventType` native terms relevant here: Purchase (lido01016), Gift (lido00996), Exchange (lido01015), Loan (lido01128), Looting (lido01011), Theft (lido01010), Loss (lido00009), Restitution (lido00724), Repatriation (lido01153), Change of legal title (lido00001), Transfer of ownership/custody (lido01134), Unknown provenance event (lido01130). **No native term exists for "forced sale" or "confiscation."** Per expert review, this is not actually a gap to patch with keywords — REAO-based transaction_state above supersedes free-text keyword detection entirely; map `zwangsverkauf`/`entziehung`/the presumption states onto LIDO's generic `Change of legal title` event type, and store the REAO state as a separate internal field, not inferred from LIDO text.

**The four added schema fields are carried in LIDO as a documented free-text convention, not an application profile (resolved 2026-08-09).** `is_institution_acquisition`, `catalogue_reference`, `owner_stated_in_catalogue` and `restitution_recipient_type` have no native LIDO equivalent. Building a formal application profile for them — XML schema extension plus Schematron rules — is real and disproportionate scope for four fields in a solo four-month build. Instead the LIDO adapter reads them from `eventDescriptionSet`/`descriptiveNoteValue` using a single parseable prefix convention, `PROVENA_EXT:field_name=value`, specified once in the README. **Built 2026-08-11, with two corrections to this paragraph.** There is no `eventNote` element in LIDO v1.1 — the note lives in `eventDescriptionSet/descriptiveNoteValue`, confirmed against the XSD. And the count is **five**, not four: `export_licence_present` has no native carrier either and was overlooked here. `date_precision` is a sixth partial case — native for exact/month/year, and needing the convention only for circa/before/after, which an ISO date string cannot express. The adapter accepts any internal field name and always lets it override the native mapping, so there is one rule to document rather than a list to keep in sync.

State this plainly as a **limitation, not a standards-compliant extension**: a real LIDO application profile is future work and this prototype does not claim to have solved it. Say equally plainly that the gap is specific to LIDO input — all four fields work natively and cleanly in CSV and JSON, so no functionality is blocked by it. Do not let this read as a bigger problem than it is.

Reference sources (in order of authority): [LIDO Primer](https://lido-schema.org/documents/primer/latest/lido-primer.html) (official, CC BY 4.0) → [`nfdi4objects/lido-schema`](https://github.com/nfdi4objects/lido-schema) (XSD, validation) → [`nfdi4objects/lido-examples`](https://github.com/nfdi4objects/lido-examples) (real/realistic examples, structure only — construct a synthetic populated example by hand for testing). Parser: hand-rolled `xml.etree.ElementTree`, no third-party LIDO library (evaluated `pieterdp/LidoParser`: GPL-2.0, copyleft-incompatible with Apache 2.0, and unmaintained — rejected).

---

## Heuristic Layer — Rules to Implement (v1)

**Fundamental correction from expert review: date/geography are not independent variables, and gap *position* matters far more than gap *length*.** Replace the flat "1933–1945 in a geography list" rule with a keyed persecution-onset table:

| Territory | Persecution onset |
|---|---|
| Germany (Altreich) | 30 Jan 1933 |
| Austria | 13 Mar 1938 |
| Sudetenland | Oct 1938 |
| Bohemia & Moravia | 15 Mar 1939 |
| Poland | 1 Sep 1939 |
| Netherlands/Belgium/Luxembourg/France | May–Jun 1940 |
| Greece/Yugoslavia | Apr 1941 |
| Italy | 1938 (racial laws) **and** Sep 1943 — two windows, see below |
| Hungary | 19 Mar 1944 |

**Italy is modelled as two windows with fixed tiers (resolved 2026-08-09).** The 1938 racial laws (*leggi razziali*) began real civil and property restriction; the September 1943 occupation is where persecution escalates to deportation level. So: 1938 – Aug 1943 engages the **ordinary** presumption, Sep 1943 – 1955 the **heightened** presumption. The tiers are fixed per window rather than derived from the global 15 Sept 1935 threshold, because that threshold does not describe the Italian chronology — applying it would report every 1938–43 Italian transfer as heightened, overstating what the 1938 laws support. Treating 1938 as mere context and screening only from Sep 1943 was the alternative and is worse: under-flagging 1938–43 is a false negative, which this spec's own reasoning identifies as the more dangerous failure. This is an implementation-time modelling decision and is flagged as such in the table file and the README; it needs confirmation from a provenance researcher or an admitted lawyer, like everything else pre-review.

Consequently the table's schema supports **multiple windows per territory**, each with its own onset, end, and either an explicit tier or `by_nuremberg_threshold` (the default, used by every other territory). Where the source table gives a month or a month range rather than a day, the window stores that range rather than inventing a day.

Territory matching runs against `location` on word boundaries, case- and accent-insensitively, using per-territory `match_terms`. **Resolved 2026-08-09:** carrying English/German/local name variants of a territory already in the table (`austria` / `österreich` / `oesterreich`) is a faithful extension, not new scope. Adding a *territory* still requires a cited onset date.

This table is **configurable, not hardcoded** — institutions may need to extend it. Two additional dates matter more than 1933 as a threshold in themselves:
- **15 September 1935** (Nuremberg Laws): under REAO Art. 3, this is where the presumption of persecution-related loss becomes the *heightened* presumption (`verschaerfte_vermutung`), effectively irrebuttable absent proof of fair value, free disposal of proceeds, and that the sale would have happened anyway.
- **Extend the window to ~1955**, not 1945 — post-war Collecting Point restitutions, internal-restitution failures in France/Netherlands, and 1948–1954 "heirless property" dispersal are a distinct, heavily contested risk band.

**Rules, in priority order by expected diagnostic value (not the order originally assumed):**

1. **No pre-1945 provenance at all** — the chain contains zero records dating before 1945. Per expert review, this is the *modal* real-world case, more common and more diagnostic than gap detection, and was missing from the original design.
2. **Chain begins at institutional acquisition** — the earliest recorded event is the institution's own accession, with no prior owner recorded.
3. **Persecution-window engagement** — a transfer's date range overlaps a persecution-onset window (per the table above, using `date_precision` to avoid false-precision comparisons against `circa`/imprecise dates) — output as *engagement with a REAO presumption tier*, not a generic date flag. Weight by degree of overlap and which presumption tier is engaged (ordinary vs. heightened), not by raw gap length.
4. **Documentation-quality flag (separate axis from risk)** — missing `source_citation` on any transaction. This is a completeness signal, not a risk signal, and must be labeled and displayed separately from persecution-context flags. Do not combine into one score.
5. **Object-type-specific rule** — for `object_class` values in (silver, Judaica, coins, decorative art): flag by **class + date alone**, not chain analysis. These categories typically have no ownership chain at all (moved through confiscation channels like forced surrender to municipal pawn offices under the 1939 precious-metals decree, or property-stripping via tax authorities under the 1941 11th Decree to the Reich Citizenship Law) — a chain-based rule under-flags them to near zero, which is backwards.
6. **Known confiscation-channel actors** — transaction involves a named auction house or agency associated with forced/looted transactions in the 1938–1945 window (configurable list; seed with publicly documented examples such as Weinmüller, Hans W. Lange, Lempertz, Dorotheum, Galerie Fischer Lucerne, Sonderauftrag Linz, ERR — cite sources for each in the config file, don't just hardcode names without provenance).
7. **Anonymous provenance entry in the risk band** — "private collection, Switzerland" or similarly anonymous entries specifically within the 1933–1950 range.
8. **Deleted owner** — object published in a pre-1933 catalogue raisonné with a named owner, reappearing post-1950 with that owner removed from the record. Stronger evidence than a merely missing owner; requires cross-referencing two records if available, otherwise flag as "unverifiable, requires catalogue raisonné cross-check."
9. **Post-1998 acquisition with thin pre-1945 provenance** — acquired after the institution's own Washington Principles commitment date (configurable), with a materially incomplete pre-1945 chain. This is a different category (institutional practice against its own stated standard) from the underlying object's history.
10. **Restitution/settlement mismatch** — restitution recorded as made to a *state* rather than to an *individual or named heirs* (external restitution frequently succeeded; internal restitution to the actual dispossessed family frequently did not).
11. **Missing export licence** for a cross-border movement dated 1933–1945.
12. **Name match against the ALIU Red Flag Names List** (see Name Matching section — this is now a bundled public resource, not an opt-in institution-supplied list, per expert review).

**Rule-by-rule decisions taken while implementing 5–12 (resolved 2026-08-09):**

- **Rule 5 does not require a location match.** Requiring one would under-flag exactly the records the rule exists to catch — objects with no chain and no recorded place. It therefore screens against the table's *global* band (earliest onset to risk-band end) and takes its tier from a territory window where `location` matches one, otherwise from the Nuremberg threshold, stating which in `presumption_tier_basis`. The rule has two limbs: a dated record inside the band, and (separately) an object of a covered class with no record certainly dated pre-1945 — for these classes an absent chain is the expected trace of a confiscation channel, not a neutral records gap. Where `object_class` is blank the rule reports itself skipped.
- **Rule 6 gating is per-basis-limb, in the matching logic.** Each entry in the actor list carries one or more `documented_basis` limbs, each with its own `sources` and an explicit `implies_persecution_of_former_owner`. The loader *refuses to default that field* — an entry that omits it fails to load, because a silent default here is precisely the unsourced accusation the tool must not make. Where no limb implies persecution of a private former owner, a match may only be rendered as bearing on an individual if the record's own `transaction_state` establishes it independently; otherwise the flag says in terms that it does **not** indicate persecution of an individual former owner, and carries `presumption_tier: n/a`. An entry-level `verification_note` is surfaced on every match so a weaker-sourced entry never renders with the confidence of a well-documented one.
- **Rule 7's pattern list is a configurable data file** (`data/anonymous_owner_patterns.json`), matched case- and accent-insensitively on word boundaries against `owner_name` and its variants. Nothing in the rule body knows any particular pattern string — there is a test asserting the heuristics module contains no hardcoded pattern. Its band is **1933-01-01 to 1950-12-31**, deliberately narrower than the persecution risk band (which runs to 1955) and deliberately *not* keyed to territory: anonymity is a property of the record, not of a place.
- **Rule 9 lands on the documentation-quality axis, not persecution context.** It concerns how the institution acquired the object, measured against its own stated standard — it asserts nothing about the object's persecution context, and putting it on the persecution axis would make it assert exactly that. The commitment date defaults to **1998-12-03** (Washington Conference Principles) where the institution has not set its own, is configurable per run (`--institution-commitment-date`), and the date applied plus whether it was the default is stated in both the flag's cited fields and the report.
- **Rule 11 derives country as the final comma-separated component of `location`** ("Munich, Germany" → "Germany"; a value with no comma is used whole). This is a documented convention rather than a hidden guess: every flag carries a `country_derivation` field stating it, so a reader can see the basis and correct their input if it does not hold. The flag also distinguishes `export_licence_present: false` ("recorded as absent") from a blank ("not recorded either way").
- **Rule 12 splits by entry type into two different output structures, not one list with a type field.** A `documented_concern` match is a flag (`NM-001`). An `exonerating` match is **not a flag**: it leaves the heuristic layer on a separate per-object channel, `name_list_citations` (`NM-002`), and is rendered on its own panel marked "not a flag · does not place this object in the queue". An exonerating entry records that a name was investigated and cleared, or that the person acted protectively; rendering it as a flag would invert the record's meaning and defame the subject. Keeping the two in one list with a discriminator would leave it one careless `.length` away from being counted as a concern. `NM-002` is deliberately absent from the criteria list, since an exonerating citation is not a criterion an object can fail.

**Rule 10 is the one exception to the resolved-status hold-out (resolved 2026-08-09).** PC-008 only fires on chains that contain a restitution record — which are precisely the chains `resolved_status` marks previously resolved and holds out of the active queue. Applying the hold-out there would make rule 10 unreachable: the rule exists to ask whether a recorded settlement actually reached the dispossessed family, and suppressing it on the strength of the settlement it questions defeats it. So an object carrying a PC-008 flag is shown in the queue rather than held out. This is a *display* decision only — the stored `resolved_status` is unchanged, and objects settled with named heirs (or with no recipient type recorded) are held out as before.

**Two independent output axes, never merged into one number:** `persecution_context_flags` (list) and `documentation_quality_flags` (list). A well-documented 1935 Vienna sale is a risk problem. A 1650 painting with an unbroken chain and zero citations is a records problem. These must never be collapsed into a single score — see Output section for why.

---

## LLM Layer (Apertus via Infomaniak)

- Input to the model per flagged object: the triggered rule ID(s), the exact cited record fields, and nothing else. **The model must be explicitly instructed and constrained not to introduce any fact, name, date, or context not present in those fields** — no biographical elaboration on named actors, no historical color, no inference beyond restating the rule against the cited data.
- Output: a plain-language restatement, e.g. "This 1938 Vienna transfer engages the REAO Art. 3 heightened presumption (post-15 Sept 1935); the record contains no evidence of fair value received or free disposal of proceeds." Show the source fields adjacent to every generated sentence so a non-expert user can verify against the record directly.
- Every output must be phrased as **a research question, not a finding** — "unverified: was fair value received?" not "likely forced sale."
- Output language: configurable (DE/FR/IT/EN minimum), externalized via a simple JSON dict, not a full i18n framework.
- One hallucinated detail (a fabricated collector biography, an invented date) is enough to make an institution distrust and discard the tool entirely — this constraint is not optional polish, it's the core reliability requirement.

**The constraint is enforced, not merely requested (resolved 2026-08-09).** Instructing a model to stay inside its input is not the same as it doing so, so `src/llm_guard.py` checks every generated sentence back against the exact context it was given and discards anything that introduces new material. Six checks: numbers absent from the input; a curated multilingual list of Nazi-era events/institutions/actors absent from the input; per-language finding phrases ("was looted", "proves", "certainly", "low risk"); capitalised mid-sentence tokens absent from the input; length; and a required question framing. A failure retries once with a tightened instruction, then leaves `llm_explanation` null and records why in `llm_explanation_status`. **Rejection is the cheap outcome** — the deterministic `rule_statement` already carries the finding — so every threshold is set to over-reject.

**Named material must be grounded (revised 2026-08-09 after live validation).** The original check treated a capitalised mid-sentence token absent from the input as an invented entity, and skipped German entirely because capitalisation there marks every noun. Live validation found the consequence: German output received *no* check on named material, so an entire uncited historical narrative passed. The check now runs for every language and asks whether a named token is **grounded** — it appears in the cited fields, or it is a cross-language form of something that does (`equivalence_groups`), or, for a language that capitalises common nouns, it is ordinary task vocabulary (`restatement_vocabulary`).

**German needed a second correction (2026-08-09, second live run).** Requiring every capitalised German token to be grounded rejected **100% of live German output — 0/12 baseline**. The cause is structural, not a list that was too short: the cited fields are English (rule text, methodology, field names), so an ordinary German noun can never match them however faithful the sentence is. No task-vocabulary list can fix that, and treating it as one was the design error.

German now uses a **run** signal instead: two or more adjacent capitalised tokens where at least one is ungrounded. German separates ordinary nouns with articles, prepositions and verbs, so adjacent capitalised content words are characteristically a name — "Sammlung Rosenfeld", "Kunsthändler Viktor Bauer". A single ungrounded noun mid-sentence is deliberately not flagged; that is the accepted cost of German being emittable at all, and it is recorded as a test so the trade-off cannot be quietly forgotten.

**Positional capitalisation is excluded by identity, not by position (resolved 2026-08-09, third live run).** German capitalises the first word of a clause after a colon as well as after a full stop, so ordinary verbs and interrogatives appear capitalised for purely positional reasons. The run scan therefore splits on clause boundaries (`: ; — " «`, newline) as well as sentence boundaries, and discards words drawn from a `positional_capitals` list rather than simply skipping each clause's first token. Skipping by position was the obvious fix and is wrong: a clause can legitimately *begin* with a fabricated name ("Ungeklärt: Galerie Morgenstern trat auf"), and blind skipping loses exactly that case. Both directions are covered by tests.

Uncited historical narrative moved to `historical_elaboration_terms`, which is its proper home — those terms name Nazi-era practices (Enteignung, Zwangsversteigerung, Raubkunst, Beschlagnahmung) rather than ordinary vocabulary. Matching gained a narrow inflection suffix set so "enteignung" catches "Enteignungen", chosen tightly enough that "nazi" cannot match the Italian "nazionale".

Equivalence grouping was added at the same time, for a different failure: French and Italian were rejecting nearly everything because the model correctly translated place names present in the record ("Allemagne" for "Germany"). Translating the input is not adding to it. Crucially the grouping never grounds a token on its own — another member of its group has to be present in the fields — so it cannot be used to smuggle a place the record never mentions.

**Italian needed the same treatment (2026-08-09, fourth live run).** Italian `direct-elicitation` carried uncited period background in 2/3 runs — "durante un periodo di forte pressione antisemita", and a characterisation of a cited party as "un'istituzione commerciale attiva in quel contesto storico". Same class as the German failure, in phrasing the term list did not cover. Two fixes, either of which catches it alone: period-characterisation vocabulary (antisemit\*, contesto storico, clima politico and their FR/DE/EN forms) added to `historical_elaboration_terms`, since naming a historical *climate* is background by definition — no record field states one; and `MAX_SENTENCES` tightened from 5 to **3**, which mirrors the prompt's own instruction, so exceeding it is the model having ignored the constraint rather than a stylistic judgement.

**The sentence cap then exposed a counting defect (2026-08-09, fifth live run).** German baseline fell from 75% to 42% on new `too_long` rejections. The cause was not German verbosity: a period ending an initial was counted as a sentence end, so an output citing an owner recorded as `Cordes, H.` measured three sentences when it had two. German took the worst of it because it names the party more often, but the defect was language-neutral — English measured identically wrong. Abbreviation and initial periods are now masked before counting, and before the clause and sentence scans.

Limits are also language-aware now: German gets a higher **character** allowance (900 vs 700), since it genuinely needs more characters per concept. The **sentence** cap is deliberately not raised for any language — it mirrors an instruction that is identical in every language, and raising it for German would re-open the elaboration route the Italian leak came through. Diagnose a length rejection before loosening a limit: this one was a bug wearing a tuning problem's clothes.

Romance place-name forms (Berlino, Norimberga, Milano, Firenze, Varsavia…) were also missing from `equivalence_groups` and showing up as ungrounded. Separate issue from the leak, same fix as the earlier French/Italian one: a token is permitted only when another member of its group is present in the fields.

**A date in a cited field IS its written-out form (2026-08-09, sixth live run).** English baseline lost a whole set (OBJ-004, 0/3) to `introduced_proper_noun` on "June" and "November", against records whose `date_from` reads `1934-06` and `1936-11-04`. Writing the month out is a format translation of the input, the same category as writing "Allemagne" for "Germany" — not a fabrication, and rejecting it penalises the model for producing readable prose.

The fix keeps the equivalence principle intact rather than granting a blanket allowance: `date_equivalence.months` in the language pack maps each month number to its forms in every output language, and a month name grounds **only** where a date in the cited fields actually names that month. With `1934-06` in front of the model, "June"/"Juni"/"juin"/"giugno" ground and "August" does not. Month *numbers* needed nothing new — the number check already splits an ISO date into its components. The month groups double as ordinary equivalence groups, so a field that spells a month out can be translated into another language, still subject to the rule that a group member must be present.

Implementing it surfaced a second, smaller defect: matching ISO dates loosely read `onset_table_version: "2026-08-09b"` — a version string every PC-003 flag carries — as a date, grounding "August" everywhere. The pattern now rejects a date with further word or hyphen characters attached, so a version string is not a date. This is the recurring shape of these fixes: the loosening is safe only because what feeds it is narrow, so check what feeds it.

Remaining gap, stated because an undocumented gap is worse than none: nothing here detects a fluent paraphrase that subtly alters a field's meaning. Month grounding is per-month, not per-date, so an output pairing a grounded month with a grounded year that never occurred together ("December 1934" where the fields carry `1934-06` and `1955-12-31`) passes — the same paraphrase-drift class, not a new one. This is a mitigation, not a proof of faithfulness, which is why the layer is **off by default**.

**An accepted adversarial output is not a failure, and the harness no longer reports it as one (resolved 2026-08-09).** The adversarial cases bait the model so the guard has something to catch. When the model declines the bait the guard is never exercised and there is nothing to catch; when the model takes it and the guard misses, that is a real failure. The two are indistinguishable from outside, and no automated check can separate them — if one could, the guard would already be applying it. So acceptance is reported as REVIEW REQUIRED with the text printed, and the run exits 0; `--strict` restores a non-zero exit for CI, where an unreviewed acceptance should block. Baseline collapse remains an automatic failure, because that one needs no human to confirm. The first design cried FAIL on 12 healthy cases, which trains a reader to ignore the exit code — the opposite of what a validation harness is for.

**Data protection is structural, not documentary.** The layer makes no call unless an endpoint is configured, and the payload carries only the flag's already-cited fields — free-text `notes` are never sent unless a rule cited them, because widening the disclosure past what the flag needs is exactly the exposure the revFADP/GDPR section warns about. `verify_payload_is_minimal` enforces this and is asserted in the tests.

**The Infomaniak request shape is confirmed (2026-08-09, live call against a real account):** `POST https://api.infomaniak.com/2/ai/{product_id}/openai/v1/chat/completions`, Bearer-authenticated, OpenAI-compatible, generated text at `choices[0].message.content`. The Apertus model id is **`swiss-ai/Apertus-v1.5-70B`**, verified through the account's `/models` listing — it is *not* the id used as an example in the documentation, so do not substitute that one. `product_id` and the API key come from `.env` (see `.env.example`, gitignored) and are never hardcoded. The endpoint still has no default: without a product id the layer does not run.

**Live validation, first run (2026-08-09): 108 calls, reviewed per case.** Baseline acceptance 62% — healthy, so the guard is not so strict that the layer produces nothing. One real failure: `adversarial/direct-elicitation` in German, 3/3 accepted, carrying uncited historical narrative. That is the German capitalisation gap, demonstrated rather than theoretical, and it is fixed above.

One diagnostic correction worth keeping: the entity flagged in review as fabricated, "Auktionshaus Steinbach", **is** the `owner_name` on that flag (`example_input.csv:row 3`), so the guard was right to accept that token. The failure was the surrounding narrative, not the name. The fix targets the narrative.

**The layer is validated live (run 7, 2026-08-11).** The month-grounding fix holds and `direct-elicitation` is still rejected in every language. Mocked cases prove the guard rejects the confabulations the tests anticipated; they cannot prove it rejects what a real model produces, which is why the harness exists. It puts real flags in front of the live model — including a bundled-list name, a sparse flag, an instruction smuggled through a record field, and a direct request for background — and can fail in both directions: exit 2 if an adversarial output passed the guard under `--strict` (too lax), exit 3 if every baseline output was rejected (too strict, so the layer is dead weight). Accepted outputs are printed for human review, since the guard checks that nothing new was introduced but cannot check that a faithful-looking paraphrase preserved a field's meaning. Re-run it after any change to the guard, the language pack or the prompt: every entry in the run table is a regression a plausible-looking change introduced.

The layer nonetheless stays **off by default**. That is the data-protection posture, not a hedge about the guard: a run that makes no call discloses nothing. Validation says the guard rejects what it was built to reject — not that a generated sentence can be trusted without reading it against the fields printed beside it.

---

## Output & Framing — do not deviate from this without re-reading the reasoning

**No numeric risk score. This was the original design and it is now known to be actively harmful, for three concrete reasons:**
1. **Discovery risk.** A stored score is a record of institutional notice. If an object is scored "low risk" and later shown to be looted, the existence of that score can be used to establish that the institution assessed and dismissed the risk — potentially affecting claim-accrual analysis under laws like the US HEAR Act. Storing a score risks manufacturing evidence against the tool's own users.
2. **False precision.** The underlying standards (Washington Principles, Terezin Declaration) are non-binding soft law resolved case-by-case, not statute with a formula. A decimal score implies a determinacy that doesn't exist anywhere in the actual field.
3. **The "low risk" label is the dangerous output, not "high risk."** A false positive costs a researcher a couple of hours checking a clean object. A false negative that carries an authoritative-looking "low risk" badge can terminate inquiry permanently, with the tool's own output as documentation that inquiry happened and concluded nothing was wrong.

**What to output instead:**
- Qualitative flags with cited evidence (see Heuristic Layer), never a score or a "clean"/"cleared"/"low risk" label.
- The terminal state for an object with no triggered rules is: *"Screened against N criteria; no criteria triggered. Absence of flags is not evidence of unproblematic provenance and does not constitute provenance research."* This exact framing (or equivalent) must appear — the tool must never imply an unflagged object has been cleared.
- A **work-queue order** (which objects to look at first), not a grade.
- **Resolved status, distinct from "no flags."** If an object's chain shows `restitution_erfolgt_or_vergleich`, mark it "previously resolved — do not re-flag as open" rather than surfacing it in the active triage queue. **Resolved 2026-08-09:** this suppression operates at the *record* level too, not only the object level — a record whose own `transaction_state` is `restitution_erfolgt_or_vergleich` is never itself flagged by the persecution-window rule, even though a post-war settlement date falls inside the risk band extended to 1955. A restitution event resolves a claim rather than presenting one for triage; flagging it as a risky transfer contradicts the entire point of `resolved_status`. Other records in the same chain are still screened normally, and the suppression is stated in `coverage_note` rather than being silent. Re-flagging a settled claim is a real-cost false positive: it can reopen matters that were closed and it destroys curatorial trust in the tool. This requires a human-settable "researched/resolved" status per object, distinct from the automated flags.
- **Human-in-the-loop exit only.** Objects exit the triage queue only when a named human marks them researched, with a note — never automatically. If the system can silently mark something "clean," an institution will eventually let it, and that is the failure mode the whole design exists to prevent.

**First screen: the coverage map, not the ranked list.** Before any per-object triage view, the report must show: total objects processed; how many have any pre-1945 provenance; how many have none; how many have a chain beginning at institutional accession; how many are already in `restitution_erfolgt_or_vergleich` status. This is what a researcher actually needs to scope a year of work, and it's presented first.

**Per-object output shape:**
```json
{
  "object_id": "...",
  "screening_statement": "Screened against N criteria; ...",
  "persecution_context_flags": [
    {"rule_id": "...", "rule_statement": "...", "methodology": "...", "cited_fields": {...}, "presumption_tier": "ordinary|heightened|n/a", "llm_explanation": "..."}
  ],
  "documentation_quality_flags": [
    {"rule_id": "...", "rule_statement": "...", "methodology": "...", "cited_fields": {...}}
  ],
  "name_list_citations": [
    {"rule_id": "NM-002", "rule_statement": "...", "methodology": "...", "cited_fields": {...}, "entry_type": "exonerating"}
  ],
  "resolved_status": "unresolved | previously_resolved",
  "coverage_note": "which rules ran vs skipped and why"
}
```

`rule_statement` and `methodology` on **every** flag are required, not optional (resolved 2026-08-09). The Definition of Done requires every flag to name a methodological source, and `presumption_tier` alone cannot carry that for the documentation-quality axis, which has no tier. `rule_statement` is the deterministic, rule-generated phrasing of the finding as an open question; it is produced by the heuristic layer and is present whether or not the LLM layer has run. `llm_explanation` is `null` until the LLM layer runs — explicitly null rather than absent, so a consumer can distinguish "not generated" from "generated empty".

**Work-queue banding is display-only.** The HTML report groups objects by which criteria triggered, in order to answer "what do I look at first". That grouping is computed at render time and is deliberately **not** written back into the stored JSON: a persisted per-object band is the graded, discoverable record the no-score reasoning above exists to prevent. An ordering is fine; a stored label is not.

**Disclaimer, required on every report, adapted from real precedent (German Lost Art Foundation / Getty Provenance Index use-conditions) rather than drafted from scratch:** state plainly that the output is machine-generated triage, has not been reviewed by a provenance researcher, and is not a provenance report or legal determination.

---

## Name Matching

**Revise from the original opt-in-only design.** The **ALIU Red Flag Names List** (Office of Strategic Services Art Looting Investigation Unit, 1946) is a public, government-produced historical record — matching against it is standard field practice, not an accusation, and it is defensible to bundle directly with the tool rather than requiring each institution to separately source it. The published dealer research of the German Lost Art Foundation is a similarly citable public source.

**Critical constraint on how a match is rendered — this is a real legal-exposure mitigation, not a nicety:**
- Never render a name match as a characterization of the person.
- Render it strictly as a citation: *"Name matches ALIU Red Flag Names List entry #N; see [source]. This indicates the name appears in a 1946 Allied investigative record and carries no finding as to any specific transaction."*
- This matters because of real, specific legal exposure: under Swiss law, personality-rights claims (ZGB Art. 28) and potentially defamation (StGB Art. 173) for living heirs/descendants; Germany recognizes a post-mortem personality right. This is not hypothetical caution — flag it explicitly in the README and get it reviewed by an admitted lawyer before publishing the name-matching feature.
- Name matching requires accounting for **name variants and transliteration** (see `owner_name_variants` in the schema) — without this, the matcher will miss most of what it should catch.
- Institution-supplied additional risk lists remain optional and separate, never bundled or hardcoded — same reasoning as before, now applying to any list beyond the publicly-documented ALIU/DZK sources.

---

## Data Protection

The pipeline processes personal data about identifiable persons (collectors, heirs, claimants) — this is in scope of Swiss revFADP and, depending on the institution, GDPR. The LLM API call is a data disclosure under these frameworks. Keep the LLM call in-jurisdiction (Apertus via Infomaniak, Geneva) and state this explicitly in the README's data-handling section — this is a real compliance point that will matter more to museum legal/IT counsel than the classification logic itself. Do not add a general-purpose compliance/consent framework beyond stating this plainly; this is a documentation requirement, not a new subsystem.

---

## Repo Structure & Deliverables

```
/provenance-triage-tool
  /src
    heuristics.py         # persecution-onset table, two-axis flags, object-class rules
    reao_taxonomy.py       # transaction_state enum and presumption-tier logic
    llm_client.py          # Apertus/Infomaniak wrapper, constrained-context prompting
    pipeline.py             # orchestrates heuristic -> LLM -> output
    schema.py               # input validation, date_precision handling
    name_matching.py        # ALIU list matching, variant/transliteration handling, citation-only rendering
    csv_adapter.py          # CSV input, rejects unknown columns, reports every row problem at once
    lido_adapter.py         # LIDO-XML input, PROVENA_EXT convention, ingest notes
    llm_guard.py            # post-generation verification of every LLM sentence
    report.py               # embeds the JSON into the static template
  /data
    aliu_red_flag_names.json   # bundled public 1946 ALIU list, with source citation
    persecution_onset_table.json  # configurable, editable territory/date table
    confiscation_channel_actors.json  # auction houses/agencies, each with a cited source
    anonymous_owner_patterns.json  # rule 7's patterns and its 1933-1950 band
    lido_event_type_map.json   # LIDO Event Type -> REAO state, and the terms left unmapped, with reasons
    llm_output_language.json   # output languages, guard vocabulary, equivalence and date groups
  /report
    report_template.html   # coverage map FIRST, ranked queue SECOND, vanilla HTML/CSS/JS
  /scripts
    fetch_lido_event_types.py  # re-verify LIDO Event Type vocabulary if revised upstream
    validate_llm_live.py       # live guard validation; fails in both directions
  /examples
    example_input.csv
    example_input.xml      # synthetic, schema-valid LIDO with populated eventWrap
  README.md                 # install, usage, schema docs, methodology sources, limitations, disclaimer, license
  LICENSE
  requirements.txt
  config.example.yaml       # API key placeholder, persecution-onset table, confiscation-actor list, thresholds
```

README must include, beyond the standard quickstart/schema docs:
- Explicit methodology citations (REAO Art. 3, Washington Principles, Terezin Declaration, German Handreichung) — this tool's rules are not invented, and the README should say so with sources.
- The three reasons for no numeric score (discovery risk, false precision, low-risk-is-the-dangerous-output), so a user understands why the output looks the way it does.
- A clear statement this tool has not been reviewed by an admitted lawyer or professional provenance researcher as of initial release, and what review is planned/sought.
- Full disclaimer text (adapted from German Lost Art Foundation / Getty Provenance Index precedent).
- Data protection statement (revFADP/GDPR, in-jurisdiction LLM processing).

---

## Explicit Non-Goals / Constraints
- Do not autonomously fetch or scrape any external database, archive, or provenance registry — the tool only processes data the user supplies.
- Do not hardcode any real institution's data, table names, or schema from prior projects.
- No numeric risk score, ever, under any framing — see Output section.
- No auth layer, no database, no hosting/deployment config in v1 — local/self-hosted CLI tool + static report.
- No OCR/scanned-document ingestion in v1 (future work).
- No signed/tamper-evident output reports in v1 (future work).
- No colonial-context date-range extension treated as a simple parameter change — per expert review, colonial-context provenance is a structurally different tool (no burden-shifting legal instrument equivalent to MG Law 59, claimants are often states/communities not individuals, usually no ownership chain exists at all, human-remains/sacred-object handling has nothing to do with risk scoring). If pursued later, scope it as a separate rule module sharing only ingest/UI — do not claim "same tool, different dates" in the grant application, reviewers with field knowledge will read that as naive.
- Don't add logging/error-handling scaffolding beyond what's needed to fail loudly on bad input.
- Ask before assuming: Infomaniak API request format, and any extension to the persecution-onset table or confiscation-actor list beyond what's cited here.

---

## Future Vision (application context, not v1 scope)

The long-term direction points toward standardized ontologies (CIDOC-CRM, Linked Art) and structured cross-referencing against archival/loss-registry sources, but that requires institutional data-sharing agreements and specialist domains well beyond a four-month solo prototype. This project focuses on the achievable first layer: transparent, methodologically-grounded triage from records institutions already hold, with clear documentation of what it does and does not verify. A colonial-context version is explicitly named as a **separate future tool**, not a parameter extension (see Non-Goals). OCR ingestion and signed output reports are similarly named as later, separate phases.

---

## Deliverable & Hosting (Prototype Fund requirement)

Unchanged from prior scoping: public Apache 2.0 GitHub repo (code) + static demo (pre-run against synthetic data, no live upload, no backend) satisfies the fund's "testable prototype" requirement without any real institutional data touching a hosted service. Do not build a live web app accepting real records — this would contradict both the data-protection posture above and the discovery-risk reasoning in the Output section (a hosted service creates exactly the kind of institutional-notice record that reasoning warns against, at greater scale).

---

## Advisor Targets (for the grant application's team/feasibility section)

Per expert review, approach (in rough priority order, Swiss-first since reviewers will weight a Swiss institutional reviewer disproportionately):
- Swiss Federal Office of Culture's provenance research contact point
- University provenance research chairs in Zurich and Bern
- Arbeitskreis Provenienzforschung e.V. (active digital/methods interest)
- Deutsches Zentrum Kulturgutverluste, Magdeburg
- Commission for Looted Art in Europe, London
- IFAR, New York

Note: initial informal review input (reflected throughout this spec) explicitly declined to be named as an advisor and is not legal advice — an admitted lawyer must review the disclaimer language and personality-rights exposure (ZGB Art. 28, StGB Art. 173, GDPR/revFADP) before the name-matching feature is published, and a professional provenance researcher should validate the heuristic rule set against current field practice before this is presented as anything more than a documented-methodology prototype.

---

## Implementation Status

This file is the living source of truth. Decisions made mid-build land here.

**Built:** demo artifact (`docs/index.html`) with its pre-publication gate (`scripts/check_publishable.py`), internal schema and validation (`src/schema.py`), REAO taxonomy (`src/reao_taxonomy.py`), CSV adapter (`src/csv_adapter.py`), LIDO-XML adapter (`src/lido_adapter.py`, `data/lido_event_type_map.json`, `examples/example_input.xml`), the full v1 rule set 1–12 (`src/heuristics.py`), name/actor matching (`src/name_matching.py`), persecution-onset table (`data/persecution_onset_table.json`), anonymous-owner patterns (`data/anonymous_owner_patterns.json`), LLM explanation layer with output verification (`src/llm_client.py`, `src/llm_guard.py`, `data/llm_output_language.json`), pipeline CLI (`src/pipeline.py`), static HTML report (`report/report_template.html` + `src/report.py`), synthetic example (`examples/example_input.csv`), tests (`tests/`).

**Everything in the v1 scope is built.** The Infomaniak endpoint path, product id and Apertus model id are confirmed against a real account; the layer still defaults no endpoint, so it cannot run on a guess.

**Bundled reference lists (added 2026-08-09, both declare themselves incomplete):** `data/aliu_red_flag_names.json` (3 seed entries) and `data/confiscation_channel_actors.json` (6 seed entries) are loaded and drive NM-001/NM-002 and PC-005. Both carry a `_meta.status` marking them as seeds, and **that status is load-bearing for how a non-match reads**: with 3 ALIU entries, "no name match" carries almost no information. Every object's `coverage_note` therefore states that a non-match is *not* evidence a name is absent from the full list, and the report renders each list's `status` and `critical_scholarly_context`/`critical_context` above the queue. Do not let a non-match render as a clear result.

**The actor list's persecution gate was added by transcription (resolved 2026-08-09).** The file as supplied carries `documented_basis` as prose with entry-level `sources` and no machine-readable gate, while the Galerie Fischer entry states its crucial distinction *in prose* ("IMPORTANT DISTINCTION — do not conflate… GERMAN STATE MUSEUMS (not privately-owned Jewish collections)… Separately, [the broader claim] needs its own case-level verification"). Deriving the gate by keyword-sniffing that prose at match time is exactly the inference this project rejects, so `implies_persecution_of_former_owner` was added per basis limb in the data file, each with a `gate_basis` quoting the entry's own words for why. Galerie Fischer was split into its two limbs precisely where the entry says "Separately,"; every original `documented_basis` string is preserved verbatim (a test asserts the limbs rejoin to the original text). No new claim is made about any entity. Like the Italy windows, this is a transcription/modelling step that needs confirmation from a provenance researcher.

**Decisions resolved 2026-08-09, first pass** (each also recorded inline above): `is_institution_acquisition` added to the schema as explicit/optional with skip-reporting; `rule_statement` + `methodology` required on every flag; onset-table `match_terms` name variants approved; Italy split into two windows with fixed tiers; the `circa` margin documented as an unsourced placeholder and made configurable; persecution-window flags suppressed on restitution/settlement records.

**Decisions resolved 2026-08-09, LLM layer:** the no-outside-knowledge constraint is enforced by a post-generation guard rather than trusted to the prompt, with rejection as the cheap outcome and every threshold set to over-reject; a failed verification retries once then leaves `llm_explanation` null with the reason in a new `llm_explanation_status` field; the layer is off by default and the payload carries only the flag's already-cited fields (un-cited `notes` are never sent); the German capitalised-noun gap is documented rather than papered over; the Infomaniak request shape is left unconfirmed and undefaulted rather than guessed.

**Decisions resolved 2026-08-09, bundled lists:** the actor list's per-limb `implies_persecution_of_former_owner` gate added by transcription from each entry's own basis text, with a `gate_basis` quoting why and all original prose preserved verbatim; both lists' seed status surfaced in every `coverage_note` and in the report, because a non-match against a seed list is not a clear result; ALIU matches cite the entry **by name** rather than by a positional index, since the supplied file carries no entry numbering and a positional "entry #N" would imply a list position that does not exist; matching is bidirectional with a guard so a bare given name ("Franz") cannot pull in a person entry while a bare surname or distinctive organisation word still can.

**Decisions resolved 2026-08-11, the public demo deliverable.** The demo is a single pre-generated file committed at `docs/index.html`, served by GitHub Pages' default `/docs` convention. It is generated once, offline, from the bundled synthetic example and committed as plain HTML: nothing in the hosting path executes, so nothing in the hosting path can leak a credential. **No CI workflow calls the Infomaniak API**, and none should be added — the repository has already had one real credential exposure this session, and the safest architecture is the one with no live process to leak from.

`scripts/check_publishable.py` is the pre-publication gate. It scans the working tree, the **git history** and the built artifact for credential shapes, and refuses an artifact built from anything but a bundled synthetic example. The history scan is the one that matters: removing a secret from the tip does not remove it from history, and making a repository public publishes its history. Its known-safe list keys on the fixture *value* rather than the file, because a per-file exemption is how a real leak hides behind a test fixture. It reports how many machine-generated sentences an artifact carries and explicitly does **not** review them — guard acceptance is not sufficient for a public artifact, and the script says so rather than implying otherwise by passing.

The report now states whether any sentence in it was machine-generated, as a meta chip and a footer note. A reader must be able to answer that at a glance rather than infer it from the absence of a paragraph, and it also means a heuristics-only build can never be mistaken for the intended one.

**Two blockers stand between this and publication, neither resolvable from the build environment:**

1. **The demo has no LLM explanations yet.** The build environment has no credentials and its egress proxy blocks `api.infomaniak.com` (403 on CONNECT), so the committed artifact is heuristics-only and says so. It must be rebuilt locally with `--llm-language` and a rotated key in `.env`, then every generated sentence read against its cited fields before publication.
2. **The demo has not been published yet.** This repository is a clean tree: it was created from the development repository's working state as a single initial commit, with no prior history, because a development credential had been committed there and a rotated token that was published is still a published token. That credential was rotated at the time; the rewrite is defence in depth on top of rotation, not the reason the project is safe to publish. Nothing in this repository's history has ever contained a credential. Enabling GitHub Pages and making the repository public remain deliberate manual steps, taken after the demo carries reviewed explanations.

**Decisions resolved 2026-08-11, LIDO adapter (the last unbuilt v1 component).** One `lido:event` becomes one internal record; element matching is on local names, so the namespace prefix a file uses does not matter. The decisions worth recording, each taken in the same direction — refuse rather than guess:

- **Event types are mapped conservatively, in a data file** (`data/lido_event_type_map.json`), which also records with reasons the terms it deliberately leaves alone. Only Purchase, Gift, Exchange, Restitution and Unknown provenance event have unambiguous REAO equivalents. **Looting is NOT mapped to `entziehung`**, which under REAO Art. 3 means a seizure by *state* act: LIDO's term does not separate that from a private theft, so the mapping would assert what the record does not establish. **Repatriation is NOT mapped to a settled claim**, because that mapping would hold an object out of the *active* queue on a term that commonly names a state-to-state return in the colonial context this tool explicitly does not cover. Under-mapping is the safe direction and over-mapping is not: `unknown` is still screened, so a dated looting event inside a persecution window is still flagged, whereas a wrong resolved-status suppresses triage. That asymmetry decided every borderline case. Like the Italy windows and the actor-list gate, this is transcription and needs a provenance researcher's confirmation.
- **A lossy conversion is reported, not dropped.** Anything the adapter cannot carry over — an unmapped event type, a `displayDate` with no parseable date, a second `eventPlace` — becomes an `IngestNote` carried in the report's new `ingest_notes` block and rendered *above* the coverage map, because it governs how everything below it reads. CSV carries an empty list rather than no key, so a consumer can tell "none" from "not recorded". This is the same distinction as skip-reporting on a rule.
- **Precision is never manufactured.** LIDO practice routinely expands a year to `1921-01-01`/`1921-12-31` and a month to `1934-06-01`/`1934-06-30`; read literally those are day-precise dates, which is exactly the false precision `schema.py` exists to prevent. A span covering a whole calendar year or month is rewritten to that year or month — the interval is unchanged, only the claimed precision, and only downwards. A `displayDate` of `um 1938` is not read for its "um": `circa`/`before`/`after` must be stated through the convention.
- **An ambiguous actor set is rejected, not resolved by picking.** A record names the party holding the object *after* the event. One actor is that party; several are disambiguated by `roleActor/term` against a receiving-role list; anything else fails the file. An event whose only actor is recorded as a seller also fails, because entering it would name the wrong party silently. Both are settleable with `PROVENA_EXT:owner_name=`. Writing that escape hatch into the error message before implementing it was a real bug, caught by the adapter's own tests.
- **The convention accepts any internal field name and always overrides the native mapping** — an explicit statement beats a derivation — and rejects an unrecognised name, the same posture the CSV adapter takes towards an unrecognised column.

**Decisions resolved 2026-08-09, rules 5–12** (each also recorded inline above): three new optional schema fields (`catalogue_reference`, `owner_stated_in_catalogue` for rule 8; `restitution_recipient_type` for rule 10), all skip-reporting when blank; rule 5 screens on class and date without requiring a location; rule 6 gates on a per-limb `implies_persecution_of_former_owner` the loader refuses to default; rule 7's patterns externalised to a configurable data file with a 1933–1950 band narrower than the risk band; rule 9 placed on the documentation axis with a 1998-12-03 default commitment date, configurable and always stated; rule 11 derives country as the final comma-separated component of `location` and says so in every flag; rule 12 routes exonerating entries to a separate `name_list_citations` channel that is not a flag list; rule 10 exempted from the resolved-status hold-out at display time.

---

## Definition of Done
- Runs end-to-end against `example_input.xml` (LIDO) and `example_input.csv`, producing JSON output and a viewable HTML report with the coverage map as the first screen.
- Every flag traces to specific cited record fields and a named methodological source (REAO tier, documentation-quality axis, or object-class rule) — no unexplained or numeric outputs anywhere.
- `resolved_status` correctly suppresses previously-settled objects from the active triage queue.
- README states the methodology sources, the no-score reasoning, the current lack of formal legal/professional review, and the full disclaimer.
- Confirm before finalizing: does an object with zero triggered rules correctly show the "screened, absence of flags is not evidence of unproblematic provenance" terminal state rather than any form of "clean" language?
