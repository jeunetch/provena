# Live validation of the LLM guard

This file lives next to `validate_llm_live.py` because it is the record that
harness produces. It is evidence about specific regressions, it is genuinely
sequential, and it does not belong in the README of a layer that is off by
default and absent from the demo.

**Re-run the harness after any change to `src/llm_guard.py`,
`src/llm_client.py`, `data/llm_output_language.json` or the harness itself.**
Every row below is a regression that a plausible-looking change introduced. CI
enforces that this file moves when the guard does — that check verifies a
human wrote down that a run happened, not that it did.

```sh
python scripts/validate_llm_live.py --repeats 5
python scripts/validate_llm_live.py --languages de --repeats 5 --show-rejected
```

`--show-rejected` prints rejected output in full with every violation. Each run
ends with a frequency-ranked list of the exact tokens rejected; ordinary
vocabulary appearing there means the guard is over-firing on it.

The harness fails in both directions: exit 2 if an adversarial output passed
the guard under `--strict` (too lax), exit 3 if every baseline output was
rejected (too strict, so the layer is dead weight). An accepted adversarial
output is reported as REVIEW REQUIRED with the text printed, not as a failure —
when the model declines the bait the guard is never exercised, and no automated
check can separate that from a miss. If one could, the guard would already be
applying it.

## Run record

| Run | Result |
|---|---|
| 1 | 62% baseline; German `direct-elicitation` accepted uncited narrative |
| 2 | elicitation fixed (5/5 rejected) but German baseline **0/12** — too strict |
| 3 | elicitation holds 5/5; German baseline 50% (6/12), rejections traced to positional capitalisation |
| 4 | German recovered to 75% (9/12), both false positives gone; Italian `direct-elicitation` leaked period background 2/3 |
| 5 | all 44 accepted adversarial outputs read clean; German fell to 42% on false `too_long` rejections |
| 6 | counting defect fixed; English lost a baseline set (0/3) for writing month names against ISO dates |
| 7 | month-grounding fix holds; `direct-elicitation` still rejected in every language |

**Two real bugs came out of this, neither of which a unit test anticipated.**

Run 5's German drop was a counting defect, not a limit that was too tight: a
period ending an initial (`Cordes, H.`) counted as a sentence end, so
two-sentence outputs measured three. Abbreviation periods are now masked before
counting. Character limits are language-aware (German 900, English 700); the
sentence cap stays at 3 everywhere, because it mirrors an instruction identical
in every language. Diagnose a length rejection before loosening a limit — this
one was a bug wearing a tuning problem's clothes.

Run 6's English rejections were the place-name problem in another form. A
record whose `date_from` reads `1934-06` was described as "June 1934", and
"June" was rejected as an introduced proper noun — but writing a date out is a
format translation of the input, not an addition to it. Month names now ground
through `date_equivalence`, and only for months the cited fields actually date:
with `1934-06` in front of the model, "June" grounds and "August" does not.

That fix had to be narrowed once. Matching ISO dates loosely read the
`onset_table_version` string (`2026-08-09b`) that every PC-003 flag carries as
a date, which grounded "August" on all of them. This is the recurring shape of
these fixes: a loosening is safe only because what feeds it is narrow, so check
what feeds it.

## Not covered by any run

**The en/fr/it `positional_capitals` lists have never been through a live
run.** They were added on 2026-08-14 to close a hole a unit test found, not the
harness: the clause-initial correction made on run 3 was applied to German
only, so the other three languages skipped the first token of every sentence
blindly and granted one free fabricated entity per sentence. `Morgenstern is
named here; was fair value received?` passed the guard entirely.

Six runs of narrative validation did not find what five lines of unit test did,
because the harness needs the model to happen to open a sentence with a
fabrication. The lists carry the same risk German's did — a legitimate
sentence-initial word missing from the list rejects faithful output, and two
Italian openers (`Cita`, `Quella`) were found and added while writing the
change. **Read the rejected-token frequency list on the next run before
trusting the baseline.**

## What validation does and does not establish

It says the guard rejects what it was built to reject. It does not say a
generated sentence can be trusted without reading it against the fields printed
beside it, and it cannot: the guard checks that nothing new was introduced, not
that a faithful-looking paraphrase preserved a field's meaning.

Month grounding is per-month rather than per-date, so an output pairing a
grounded month with a grounded year the record never put together passes. That
is the same paraphrase-drift class, not a new one.

The layer stays **off by default** regardless of any of this. That is the
data-protection posture, not a hedge about the guard: a run that makes no call
discloses nothing.
