# Contributing

## What this project most needs

Not code. **Methodology review.** This tool makes structured claims about
Nazi-era provenance risk against real institutional collections, and its rule
set has been written by one developer working from published standards, with
no professional provenance researcher or admitted lawyer having reviewed it.
That is stated in the README and it is the honest state of the project.

The most valuable contribution is someone qualified telling us a rule is
wrong. Four areas are flagged in the README as **modelling decisions that
need expert confirmation** — the two Italian persecution windows, the
transcribed `implies_persecution_of_former_owner` gate on the actor list, and
the placeholder `circa` margin. There is an issue template for exactly this.

Corrections to the bundled reference lists are equally welcome. Both ship as
declared-incomplete seeds, and with three ALIU entries a non-match carries
almost no information — a real sharp edge, disclosed but not eliminated.

## Ground rules that are not negotiable

These are design constraints, not preferences. A pull request that crosses one
will be declined regardless of how well it is written.

1. **No numeric risk score, and no "clean" / "cleared" / "low risk" label**,
   under any framing. The reasoning is in DECISIONS.md: discovery risk, false
   precision, and the fact that a false negative wearing an authoritative
   badge is the dangerous output. A stored per-object grade is the thing this
   design exists to prevent.
2. **The two axes never merge.** Persecution context and documentation
   quality stay separate lists. A missing citation is a records problem, not a
   risk signal.
3. **No credential ever reaches CI.** The demo is generated locally and
   committed as static HTML so that nothing in the published path holds a key.
   Do not add a workflow that calls the Infomaniak API, and do not add
   repository secrets.
4. **The LLM layer may not introduce anything.** It restates a rule against
   the fields that rule cited. Any change that lets it supply a fact, a name,
   a date or historical context not in the input is out of scope.
5. **Nothing is fetched or scraped.** The tool processes only what the user
   supplies.
6. **A rule that cannot run reports itself skipped**, never "not triggered".
   Collapsing those two states hides the difference between a criterion that
   found nothing and one that never ran.

## Running the checks

```sh
python -m unittest discover tests          # no third-party packages needed
python scripts/check_publishable.py --artifact docs/index.html
```

Both run in CI on every push and pull request, and both block.

## What CI cannot check

**The live guard validation.** `scripts/validate_llm_live.py` puts real flags
in front of the live Apertus model and can fail in both directions — too lax
(an adversarial output passed the guard) and too strict (every baseline output
was rejected, so the layer is dead weight). It needs credentials, so by the
rule above it cannot run in CI, and it is a local step whose accepted outputs
a human then reads.

This matters more than it sounds. Seven live runs found two real bugs that
every offline test passed: a sentence-counting defect that read the period in
`Cordes, H.` as a sentence end, and an Italian response that carried uncited
period background. Every row of the run table in `scripts/VALIDATION.md` is a regression
that a plausible-looking change introduced.

So: **after changing `src/llm_guard.py`, `src/llm_client.py`,
`data/llm_output_language.json` or the harness itself, re-run it and record
the outcome.**

```sh
cp .env.example .env      # local only; .env is gitignored and must stay that way
python scripts/validate_llm_live.py --repeats 5
python scripts/validate_llm_live.py --languages de --repeats 5 --show-rejected
```

Use `--repeats 5` or more. Earlier runs at `--repeats 3` gave a baseline of
n=12 per language, where a single output moved the number by eight points and
made noise look like regression.

CI enforces a proxy for this, not the thing itself: a pull request touching
those files must also touch `scripts/VALIDATION.md`, so the run table moves with the
guard. It cannot tell whether the run happened — only whether a human wrote
down that it did.

**Reading the generated text.** The guard checks that nothing new was
introduced. It cannot check that a faithful-looking paraphrase preserved a
field's meaning, and one gap is known and documented: month names ground
per-month rather than per-date, so an output pairing a grounded month with a
grounded year the record never put together will pass. Every accepted output
in a published artifact needs reading against the fields printed beside it.

## Configuration

`config.example.yaml` is a reference sheet for the CLI flags. This build has
no YAML interface — editing it changes nothing, and the pipeline warns if it
finds a `config.yaml`. If you add a setting, add the flag and update that
file's entry so the two do not drift.

## Commits

Explain why, not what. The decision log in `CLAUDE.md` is the source of truth
for design decisions; a change that alters one should update it in the same
commit.
