"""Pipeline: CSV or LIDO-XML input -> validation -> heuristics -> JSON + HTML.

The LLM explanation layer is optional and off unless an endpoint is
configured; without it every `llm_explanation` stays null and no record data
leaves the machine. The heuristic layer is complete on its own — the LLM only
ever restates what a rule already said.

Usage:
    python -m src.pipeline examples/example_input.csv
    python -m src.pipeline examples/example_input.xml --html report.html
    python -m src.pipeline examples/example_input.csv --out report.json --html report.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import csv_adapter, lido_adapter, llm_client, report
from .llm_guard import LanguageError, load_language_pack
from .heuristics import (
    CRITERIA,
    DEFAULT_ANONYMOUS_PATTERNS_PATH,
    DEFAULT_ONSET_TABLE_PATH,
    build_config,
    coverage_map,
    screen_object,
)
from .name_matching import DEFAULT_ACTORS_PATH, DEFAULT_ALIU_PATH
from .schema import (
    CIRCA_MARGIN_NOTE,
    DEFAULT_CIRCA_MARGIN_YEARS,
    InputValidationError,
    ObjectChain,
    group_into_chains,
)

# Input formats, in the priority order the methodology sets: LIDO-XML is the
# vendor-neutral event-structured case, CSV the fallback.
FORMAT_LIDO = "lido"
FORMAT_CSV = "csv"
FORMATS = (FORMAT_LIDO, FORMAT_CSV)

_EXTENSIONS = {".xml": FORMAT_LIDO, ".lido": FORMAT_LIDO, ".csv": FORMAT_CSV}


def detect_format(path: str | Path) -> str:
    """Pick an adapter from the file extension. Raises on anything else.

    Sniffing the contents would be the friendlier behaviour and the wrong one:
    a file that is neither is more likely a mistake than a format to guess at.
    """
    suffix = Path(path).suffix.lower()
    try:
        return _EXTENSIONS[suffix]
    except KeyError:
        known = ", ".join(sorted(_EXTENSIONS))
        raise InputValidationError(
            [
                f"{path}: cannot tell the input format from the extension "
                f"{suffix or '(none)'!r}. Known: {known}. Use --format to say."
            ]
        ) from None


def load_input(
    path: str | Path,
    input_format: str | None = None,
    circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS,
) -> tuple[list[ObjectChain], list[dict[str, str]]]:
    """Read an input file into chains, plus whatever the adapter had to report.

    The second element is empty for CSV, which needs no conversion and so has
    nothing to say about one. The LIDO adapter uses it to name anything it
    could not carry over — an unmapped event type, a date it would not parse —
    so a lossy conversion is visible in the output rather than silent.
    """
    resolved = input_format or detect_format(path)
    if resolved == FORMAT_CSV:
        return csv_adapter.load_chains(path, circa_margin_years), []
    ingest = lido_adapter.load(path, circa_margin_years)
    return (
        group_into_chains(ingest.records),
        [note.as_dict() for note in ingest.notes],
    )

DISCLAIMER = (
    "This output is machine-generated triage. It is not a provenance report, "
    "not provenance research, and not a legal determination. It has not been "
    "reviewed by a provenance researcher or by an admitted lawyer. It records "
    "only what the supplied records do and do not say, measured against the "
    "criteria listed in this file; it verifies nothing against any external "
    "source, and no external source was consulted. A triggered criterion is a "
    "question for a human researcher. An untriggered criterion is not a "
    "clearance: absence of flags is not evidence of unproblematic provenance. "
    "Nothing here should be represented, internally or externally, as a "
    "finding about any object, transaction or named person."
)


def run(
    input_path: str | Path,
    onset_table_path: str | Path = DEFAULT_ONSET_TABLE_PATH,
    circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS,
    anonymous_patterns_path: str | Path = DEFAULT_ANONYMOUS_PATTERNS_PATH,
    aliu_path: str | Path | None = None,
    actors_path: str | Path | None = None,
    commitment_date: date | None = None,
    explanation_generator: "llm_client.ExplanationGenerator | None" = None,
    input_format: str | None = None,
) -> dict[str, object]:
    """Screen an input file and return the full report structure.

    `explanation_generator` is None unless the LLM layer was explicitly
    configured. With it None every `llm_explanation` stays null and no data
    leaves the machine.
    """
    resolved_format = input_format or detect_format(input_path)
    chains, ingest_notes = load_input(input_path, resolved_format, circa_margin_years)
    config = build_config(
        onset_table_path=onset_table_path,
        anonymous_patterns_path=anonymous_patterns_path,
        aliu_path=aliu_path,
        actors_path=actors_path,
        commitment_date=commitment_date,
    )
    results = [screen_object(chain, config) for chain in chains]
    table = config.onset_table

    if explanation_generator is not None:
        for result in results:
            llm_client.annotate(result, explanation_generator)

    return {
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_file": str(input_path),
        "input_format": resolved_format,
        "ingest_notes": ingest_notes,
        "onset_table_version": table.version,
        "onset_table_review_status": table.review_status,
        "date_precision_handling": {
            "circa_margin_years": circa_margin_years,
            "note": CIRCA_MARGIN_NOTE,
        },
        "commitment_date": {
            "applied": config.commitment_date.isoformat(),
            "is_default": config.commitment_date_is_default,
            "source": config.commitment_date_source,
        },
        "reference_lists": _reference_list_status(config),
        "llm_layer": _llm_status(explanation_generator),
        "criteria_screened": CRITERIA,
        "criteria_not_run": [
            {"rule_id": rule_id, "reason": reason}
            for rule_id, reason in sorted(config.unavailable.items())
        ],
        "coverage_map": coverage_map(chains, results),
        "objects": results,
    }


def _llm_status(generator) -> dict[str, object]:
    """What the LLM layer did, so its absence is visible rather than assumed."""
    if generator is None:
        return {
            "enabled": False,
            "note": (
                "The LLM explanation layer did not run. Every llm_explanation is "
                "null and no record data left this machine. The deterministic "
                "rule_statement on each flag carries the finding regardless — the "
                "LLM only ever restates it in plainer words."
            ),
        }
    return {
        "enabled": True,
        "output_language": generator.language.code,
        "constraint": (
            "The model received only the triggered rule and the record fields that "
            "rule cited. Every generated sentence was checked back against that "
            "exact input; anything introducing a name, date, number or historical "
            "fact not present in it was discarded and left null. See "
            "llm_explanation_status on each flag."
        ),
        "data_protection": (
            "Generating explanations disclosed the cited record fields to the "
            "configured endpoint. That is a disclosure of personal data under "
            "Swiss revFADP and, for many institutions, GDPR."
        ),
    }


def _reference_list_status(config) -> dict[str, object]:
    """What each bundled list contributed, so absence is visible in the output."""
    status: dict[str, object] = {}
    if config.aliu_list is not None:
        aliu = config.aliu_list
        status["aliu_red_flag_names"] = {
            "loaded_from": aliu.source_path,
            "entries": len(aliu.entries),
            "exonerating_entries": sum(1 for e in aliu.entries if e.is_exonerating),
            "list_status": aliu.status,
            "is_incomplete": aliu.is_seed,
            "non_match_caveat": (
                "This list declares itself incomplete. A non-match is not evidence "
                "that a name is absent from the full ALIU Red Flag Names List."
            )
            if aliu.is_seed
            else "",
            "critical_scholarly_context": aliu.critical_scholarly_context,
        }
    if config.actor_list is not None:
        actors = config.actor_list
        status["confiscation_channel_actors"] = {
            "loaded_from": actors.source_path,
            "entries": len(actors.actors),
            "list_status": actors.status,
            "is_incomplete": actors.is_seed,
            "non_match_caveat": (
                "This list declares itself non-exhaustive. A non-match is not "
                "evidence that no confiscation-channel actor was involved."
            )
            if actors.is_seed
            else "",
            "critical_context": actors.critical_context,
        }
    if config.anonymous_patterns is not None:
        status["anonymous_owner_patterns"] = {
            "version": config.anonymous_patterns.version,
            "patterns": len(config.anonymous_patterns.patterns),
            "band": (
                f"{config.anonymous_patterns.band_from.isoformat()} to "
                f"{config.anonymous_patterns.band_to.isoformat()}"
            ),
        }
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Triage provenance records for Nazi-era research priority. Emits "
            "qualitative flags with cited evidence — never a score or a verdict."
        )
    )
    parser.add_argument(
        "input", help="path to a LIDO-XML or CSV file of provenance records"
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        default=None,
        help=(
            "input format; inferred from the file extension (.xml/.lido -> lido, "
            ".csv -> csv) when not given"
        ),
    )
    parser.add_argument(
        "--out", help="write JSON here instead of stdout", default=None
    )
    parser.add_argument(
        "--html", help="also write a self-contained HTML report here", default=None
    )
    parser.add_argument(
        "--onset-table",
        default=str(DEFAULT_ONSET_TABLE_PATH),
        help="path to the persecution-onset table JSON",
    )
    parser.add_argument(
        "--circa-margin-years",
        type=int,
        default=DEFAULT_CIRCA_MARGIN_YEARS,
        help=(
            "years a `circa` date is widened by for overlap questions "
            f"(default {DEFAULT_CIRCA_MARGIN_YEARS}; a placeholder, not a "
            "validated field-practice figure)"
        ),
    )
    parser.add_argument(
        "--anonymous-patterns",
        default=str(DEFAULT_ANONYMOUS_PATTERNS_PATH),
        help="path to the configurable anonymous-owner pattern list",
    )
    parser.add_argument(
        "--aliu-list",
        default=str(DEFAULT_ALIU_PATH),
        help="path to the bundled ALIU Red Flag Names List",
    )
    parser.add_argument(
        "--actor-list",
        default=str(DEFAULT_ACTORS_PATH),
        help="path to the confiscation-channel actor list",
    )
    parser.add_argument(
        "--institution-commitment-date",
        default=None,
        help=(
            "the institution's own Washington Principles commitment date "
            "(YYYY-MM-DD). Defaults to 1998-12-03; the date applied is stated "
            "in the output either way."
        ),
    )
    llm = parser.add_argument_group(
        "LLM explanation layer (off unless --llm-endpoint is given)",
        "Generating explanations discloses the cited record fields to the "
        "configured endpoint — a disclosure of personal data under revFADP/GDPR. "
        "The endpoint is not defaulted: this tool does not assume the Infomaniak "
        "request shape.",
    )
    llm.add_argument(
        "--llm-endpoint",
        default=None,
        help=(
            "full chat-completions URL. Overrides --llm-product-id. Supplying "
            "either one enables the layer."
        ),
    )
    llm.add_argument(
        "--llm-product-id",
        default=None,
        help=(
            "Infomaniak AI Tools product id; the confirmed endpoint URL is built "
            f"from it. Defaults to {llm_client.ENV_PRODUCT_ID} in --env-file."
        ),
    )
    llm.add_argument(
        "--llm-model",
        default=None,
        help=f"model to request (default {llm_client.APERTUS_MODEL_ID})",
    )
    llm.add_argument(
        "--env-file",
        default=".env",
        help=(
            "file holding INFOMANIAK_PRODUCT_ID and INFOMANIAK_API_KEY "
            "(default .env; see .env.example). Never commit it."
        ),
    )
    llm.add_argument(
        "--llm-api-key-env",
        default="INFOMANIAK_API_KEY",
        help=(
            "name of the environment variable holding the API key (default "
            "INFOMANIAK_API_KEY). The key is never read from a flag or a file."
        ),
    )
    llm.add_argument(
        "--llm-language",
        default="en",
        help="output language for explanations: de / fr / it / en",
    )
    llm.add_argument(
        "--llm-max-retries",
        type=int,
        default=1,
        help="retries after a failed verification before leaving the explanation null",
    )
    args = parser.parse_args(argv)

    commitment_date = None
    if args.institution_commitment_date:
        try:
            commitment_date = date.fromisoformat(args.institution_commitment_date)
        except ValueError:
            print(
                "--institution-commitment-date must be YYYY-MM-DD, got "
                f"{args.institution_commitment_date!r}",
                file=sys.stderr,
            )
            return 2

    # Credentials come from the env file first, then the process environment.
    # Neither is ever written into the output.
    env = llm_client.load_env_file(args.env_file)
    product_id = args.llm_product_id or env.get(llm_client.ENV_PRODUCT_ID) or ""
    endpoint = args.llm_endpoint or env.get("LLM_ENDPOINT") or ""
    api_key = env.get(args.llm_api_key_env) or os.environ.get(args.llm_api_key_env, "")
    model = args.llm_model or env.get("LLM_MODEL") or llm_client.APERTUS_MODEL_ID

    generator = None
    if endpoint or product_id:
        try:
            generator = llm_client.ExplanationGenerator(
                transport=llm_client.HttpTransport(
                    llm_client.LlmSettings(
                        endpoint=endpoint or llm_client.endpoint_for(product_id),
                        api_key=api_key,
                        model=model,
                        language=args.llm_language,
                    )
                ),
                language_code=args.llm_language,
                max_retries=args.llm_max_retries,
            )
        except (llm_client.LlmConfigurationError, LanguageError) as exc:
            print(f"LLM layer not configured: {exc}", file=sys.stderr)
            return 2
    elif args.llm_model or args.llm_language != "en":
        print(
            "LLM options given without a product id or endpoint; the explanation "
            "layer stays off and every llm_explanation will be null.",
            file=sys.stderr,
        )

    try:
        result = run(
            args.input,
            args.onset_table,
            args.circa_margin_years,
            anonymous_patterns_path=args.anonymous_patterns,
            aliu_path=args.aliu_list,
            actors_path=args.actor_list,
            commitment_date=commitment_date,
            explanation_generator=generator,
            input_format=args.format,
        )
    except InputValidationError as exc:
        print(f"Input rejected. {exc}", file=sys.stderr)
        return 2

    text = json.dumps(result, indent=2, ensure_ascii=False)
    written = []
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        written.append(args.out)
    if args.html:
        Path(args.html).write_text(report.render(result), encoding="utf-8")
        written.append(args.html)
    if not written:
        print(text)
        return 0

    coverage = result["coverage_map"]
    print(
        f"Screened {coverage['objects_processed']} object(s) -> "
        f"{', '.join(written)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
