"""Apertus-via-Infomaniak wrapper and constrained-context prompting.

The model is given the triggered rule and the exact record fields that rule
cited, and nothing else. It is asked for one thing: to restate the rule
against those fields, in plain language, as an open question. It does not
assign risk, does not decide anything, and is not permitted to supply a fact
that is not in front of it. Every response is checked back against its own
input by `llm_guard` before it is allowed anywhere near the output; a
response that fails is discarded and `llm_explanation` stays null.

Data protection: this call is a disclosure of personal data about
identifiable persons under Swiss revFADP and, for many institutions, GDPR.
Two consequences are built in rather than documented and hoped for:

* The layer is **off by default**. No endpoint configured, no call made.
* The payload carries only the flag's already-cited fields. Free-text `notes`
  are not sent unless a rule cited them, because sending un-cited free text
  to an external service widens the disclosure past what the flag needs.
  `verify_payload_is_minimal` enforces this and is asserted in the tests.

ENDPOINT SHAPE: confirmed 2026-08-09 by a live call against a real account —
`POST https://api.infomaniak.com/2/ai/{product_id}/openai/v1/chat/completions`,
Bearer-authenticated, OpenAI-compatible, generated text at
`choices[0].message.content`. The Apertus model id is
`swiss-ai/Apertus-v1.5-70B`, verified through the account's /models listing
and NOT the id shown as an example in the documentation.

The product id and API key are read from `.env` (see `.env.example`) and are
never hardcoded. The endpoint still has no default: without a product id or an
explicit URL the layer does not run, so it cannot start calling on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .llm_guard import (
    LanguagePack,
    OutputLanguage,
    VerificationResult,
    load_language_pack,
    verify,
)

SYSTEM_PROMPT = """You restate a single provenance-screening rule against the record fields that rule cited. You are part of a triage tool for museum staff.

ABSOLUTE CONSTRAINTS — these override any other consideration:
1. Use ONLY the rule and the record fields given below. You have no other information about this object, these people, these places, or this period.
2. Do NOT introduce any name, date, number, place, organisation, event or historical fact that is not written in the fields below. If you know something about a name you see, do not use it.
3. Do NOT explain historical background. No biography of any named party. No context about the period.
4. Phrase the result as an OPEN QUESTION for a researcher, never as a finding, conclusion, probability or risk level. Write "unverified: was X the case?", never "this was likely X".
5. If the fields do not support a sentence, do not write that sentence. Saying less is always correct.
6. At most three short sentences.

You are not assessing risk. You are not deciding anything. You are restating what the rule and the fields already say, in plainer words, so a non-expert can check it against the record themselves."""

RETRY_REMINDER = """Your previous answer introduced information that is not in the fields, or read as a finding rather than a question. Rewrite it using ONLY the words and facts present in the fields below, phrased as an open question. Remove every name, date, number and term that does not appear in the fields."""

# Fields that carry the tool's own framing rather than record content; they
# are useful context for the model and safe because they contain no personal
# data beyond what the flag already cites.
CONTEXT_KEYS = ("rule_id", "rule_statement", "methodology", "presumption_tier")


class LlmConfigurationError(Exception):
    """Raised when the layer is asked to run without a confirmed endpoint."""


# Confirmed 2026-08-09 by a live call against a real Infomaniak account.
# The model id was verified through the account's /models listing and is NOT
# the id used as an example in the documentation — use this exact string.
# The response shape was confirmed as choices[0].message.content.
INFOMANIAK_ENDPOINT_TEMPLATE = (
    "https://api.infomaniak.com/2/ai/{product_id}/openai/v1/chat/completions"
)
APERTUS_MODEL_ID = "swiss-ai/Apertus-v1.5-70B"

ENV_PRODUCT_ID = "INFOMANIAK_PRODUCT_ID"
ENV_API_KEY = "INFOMANIAK_API_KEY"


def load_env_file(path: str | Path = ".env") -> dict[str, str]:
    """Read KEY=VALUE lines from a .env file. Missing file yields {}.

    Deliberately tiny and dependency-free. Values are not exported into the
    process environment — the caller decides what to do with them, so a
    secret cannot leak into a subprocess by accident.
    """
    file = Path(path)
    if not file.exists():
        return {}
    values: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def endpoint_for(product_id: str) -> str:
    """Build the confirmed chat-completions URL for an AI Tools product id."""
    if not product_id:
        raise LlmConfigurationError(
            f"no product id. Set {ENV_PRODUCT_ID} in .env (see .env.example) or "
            "pass --llm-product-id, or give a full --llm-endpoint."
        )
    return INFOMANIAK_ENDPOINT_TEMPLATE.format(product_id=product_id)


class LlmTransport(Protocol):
    """Anything that can turn a system+user prompt into text.

    Kept narrow so the whole layer is testable without a network, and so a
    different provider can be substituted without touching the guard.
    """

    def complete(self, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class LlmSettings:
    endpoint: str
    api_key: str
    model: str = APERTUS_MODEL_ID
    language: str = "en"
    timeout_seconds: int = 30
    max_retries: int = 1


def build_context(flag: dict) -> dict[str, object]:
    """The exact material the model is allowed to see for one flag.

    Only the flag's own fields. Nothing about the object, the rest of the
    chain, or any other flag travels with it.
    """
    context: dict[str, object] = {
        key: flag[key] for key in CONTEXT_KEYS if flag.get(key) is not None
    }
    context["cited_fields"] = flag.get("cited_fields", {})
    return context


def render_user_prompt(context: dict[str, object], language: OutputLanguage) -> str:
    lines = [language.instruction, "", "TRIGGERED RULE", "--------------"]
    for key in CONTEXT_KEYS:
        if key in context:
            lines.append(f"{key}: {context[key]}")
    lines += ["", "RECORD FIELDS THE RULE CITED", "----------------------------"]
    for key, value in (context.get("cited_fields") or {}).items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}: {value}")
    lines += [
        "",
        "Restate the rule against these fields as an open question, in at most "
        "three short sentences, using no information beyond what appears above.",
    ]
    return "\n".join(lines)


def verify_payload_is_minimal(payload_text: str, flag: dict, record_extras: dict) -> list[str]:
    """Names of record values that leaked into a payload without being cited.

    A data-protection check, not a style check: the disclosure must be no
    wider than the flag the explanation is for.
    """
    cited = {str(v) for v in (flag.get("cited_fields") or {}).values() if v}
    leaked = []
    for name, value in record_extras.items():
        if not value:
            continue
        text = str(value)
        if len(text) < 4 or text in cited:
            continue
        if text in payload_text:
            leaked.append(name)
    return leaked


class HttpTransport:
    """OpenAI-compatible chat-completions transport.

    `requests` is imported lazily so that neither the package nor the network
    is needed by anything that does not actually make a call.
    """

    def __init__(self, settings: LlmSettings):
        if not settings.endpoint:
            raise LlmConfigurationError(
                "no LLM endpoint configured. The Infomaniak request shape is not "
                "assumed by this tool — supply the confirmed chat-completions URL "
                "with --llm-endpoint rather than relying on a default."
            )
        if not settings.api_key:
            raise LlmConfigurationError(
                "no API key supplied. Set the environment variable named by "
                "--llm-api-key-env; the key is never read from a file or a flag."
            )
        if not settings.model:
            raise LlmConfigurationError("no model supplied; use --llm-model.")
        self.settings = settings

    def complete(self, system: str, user: str) -> str:
        import requests  # noqa: PLC0415 — deliberately lazy, see class docstring

        response = requests.post(
            self.settings.endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                # Lowest available variability: this is a restatement task, and
                # sampling is where elaboration comes from.
                "temperature": 0,
                "stream": False,
            },
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmConfigurationError(
                "unexpected response shape from the LLM endpoint; expected an "
                f"OpenAI-compatible choices[0].message.content. Got: {body!r}"
            ) from exc


@dataclass
class ExplanationOutcome:
    text: str | None
    status: str
    attempts: int


class ExplanationGenerator:
    """Generates and verifies one explanation per flag.

    A flag whose explanation cannot be verified keeps `llm_explanation: null`
    and records why. That is a working state, not a failure: the deterministic
    `rule_statement` already carries the finding in the tool's own words.
    """

    def __init__(
        self,
        transport: LlmTransport,
        language_code: str = "en",
        pack: LanguagePack | None = None,
        max_retries: int = 1,
    ):
        self.transport = transport
        self.pack = pack or load_language_pack()
        self.language = self.pack.get(language_code)
        self.max_retries = max_retries

    def explain(self, flag: dict) -> ExplanationOutcome:
        context = build_context(flag)
        user = render_user_prompt(context, self.language)
        system = SYSTEM_PROMPT
        last: VerificationResult | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                raw = self.transport.complete(system, user)
            except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
                return ExplanationOutcome(
                    None, f"not_generated: transport error: {exc}", attempt
                )
            last = verify(raw, context, self.language, self.pack)
            if last.accepted:
                return ExplanationOutcome(raw.strip(), "generated_and_verified", attempt)
            system = f"{SYSTEM_PROMPT}\n\n{RETRY_REMINDER}"

        return ExplanationOutcome(
            None,
            f"withheld: generated text failed verification ({last.reason})",
            self.max_retries + 1,
        )


def annotate(
    result: dict, generator: ExplanationGenerator, on_flag: Callable[[], None] | None = None
) -> dict:
    """Fill llm_explanation on every flag of one screened object, in place.

    Name-list citations are included: an exonerating entry benefits from plain
    language as much as a flag does, and the guard applies to it identically.
    """
    for key in (
        "persecution_context_flags",
        "documentation_quality_flags",
        "name_list_citations",
    ):
        for flag in result.get(key) or []:
            outcome = generator.explain(flag)
            flag["llm_explanation"] = outcome.text
            flag["llm_explanation_status"] = outcome.status
            if on_flag:
                on_flag()
    return result
