"""Pre-publication check: run this before making the repository public.

This exists because a development credential was once committed to
`.env.example` and restoring the placeholders did not remove it from history.
Removing a secret from the tip does not remove it from history, and making a
repository public publishes its history — including, on GitHub, the commits
retained behind `refs/pull/N/head` for every pull request ever opened, which
survive deleting the branch they came from. A token that is merely rotated is
still a token that was published, so this check looks at the whole reachable
history and not just the working tree.

It also scans a built demo artifact, because the report embeds its input as
JSON and an operator building it from real records rather than the synthetic
example would ship those records to a public URL.

    python scripts/check_publishable.py
    python scripts/check_publishable.py --artifact docs/index.html

Exit codes: 0 clean, 1 findings, 2 could not run. Nothing here is a substitute
for reading the artifact; it catches the mechanical failures only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Credential shapes. Deliberately broad — a false positive costs a glance, a
# miss costs a published secret.
# `[ \t]*` rather than `\s*`: a `\s*` after the `=` walks over the newline and
# matches the next line's first character, so every empty placeholder reads as
# a filled-in secret.
SECRET_PATTERNS = (
    ("infomaniak api key", re.compile(r"INFOMANIAK_API_KEY[ \t]*=[ \t]*\S+")),
    ("infomaniak product id", re.compile(r"INFOMANIAK_PRODUCT_ID[ \t]*=[ \t]*\S+")),
    ("bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{16,}")),
    ("generic api key assignment",
     re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/-]{16,}")),
)

# Files whose whole point is to name the variables, with empty values.
PLACEHOLDER_ALLOWED = {".env.example"}

# Known-safe occurrences, keyed on the fixture VALUE rather than on the file,
# so that a new credential-shaped string in an already-listed file still
# fires. A blanket per-file exemption is how a real leak hides behind a test
# fixture, which is the failure this whole script exists to prevent.
KNOWN_SAFE = {
    ("tests/test_live_harness.py", "12345"),
    ("tests/test_live_harness.py", "sk-quoted"),
    # The scanner's own tests must contain credential-shaped strings in order
    # to test it. They use a sentinel value with no relationship to any real
    # credential, and that sentinel is what is listed here — so a real key
    # pasted into this file would still fire.
    ("tests/test_demo_artifact.py", "sk-quoted"),
    ("tests/test_demo_artifact.py", "abcdef0123456789abcdef"),
    # An escaped newline in a source-level fixture string, not a value.
    ("tests/test_demo_artifact.py", "=\\n"),
}


def _is_known_safe(label: str, found: str) -> bool:
    return any(
        label == where and value in found for where, value in KNOWN_SAFE
    )


def _empty_assignment(text: str) -> bool:
    """True when the match assigns nothing — a placeholder, not a secret."""
    _, _, value = text.partition("=")
    return not value.strip().strip("'\"")


def scan_text(label: str, text: str, allow_placeholders: bool = False) -> list[str]:
    findings = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            found = match.group()
            if allow_placeholders and _empty_assignment(found):
                continue
            if _is_known_safe(label, found):
                continue
            excerpt = found[:24] + ("…" if len(found) > 24 else "")
            findings.append(f"{label}: possible {name} ({excerpt!r})")
    return findings


def scan_working_tree() -> list[str]:
    findings: list[str] = []
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    for name in tracked:
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        findings.extend(
            scan_text(name, text, allow_placeholders=name in PLACEHOLDER_ALLOWED)
        )
    return findings


def scan_history() -> list[str]:
    """The check the tip-only scan cannot make.

    A secret removed from the working tree is still reachable in history, and
    publishing a repository publishes its history.
    """
    diff = subprocess.run(
        ["git", "log", "--all", "-p", "--no-color", "--", ".env.example", ".env",
         "config.yaml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    findings = []
    for line in diff.splitlines():
        # Only added lines matter: a removal line proves the secret was there.
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        for problem in scan_text("git history", line[1:], allow_placeholders=True):
            findings.append(problem)
    return sorted(set(findings))


def scan_artifact(path: Path) -> list[str]:
    """Check a built report: no secrets, and the input it was built from."""
    findings: list[str] = []
    text = path.read_text(encoding="utf-8")
    findings.extend(scan_text(str(path), text))

    match = re.search(
        r'<script id="triage-data" type="application/json">(.*?)</script>',
        text,
        re.S,
    )
    if not match:
        return findings + [f"{path}: no embedded triage data — is this a report?"]
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return findings + [f"{path}: embedded data is not valid JSON ({exc})"]

    source = str(data.get("input_file", ""))
    if not source.startswith("examples/"):
        findings.append(
            f"{path}: built from {source!r}, which is not one of the bundled "
            "synthetic examples. A public demo must never be built from real "
            "institutional records."
        )
    if not data.get("disclaimer"):
        findings.append(f"{path}: no disclaimer in the embedded data")
    return findings


def describe_artifact(path: Path) -> None:
    """State the things a human reviewer has to decide about, and not decide them."""
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="triage-data" type="application/json">(.*?)</script>', text, re.S
    )
    if not match:
        return
    data = json.loads(match.group(1))
    llm = data.get("llm_layer") or {}
    explanations = [
        flag.get("llm_explanation")
        for obj in data.get("objects", [])
        for key in ("persecution_context_flags", "documentation_quality_flags")
        for flag in obj.get(key, [])
        if flag.get("llm_explanation")
    ]
    print(f"  input:        {data.get('input_file')} ({data.get('input_format')})")
    print(f"  objects:      {data.get('coverage_map', {}).get('objects_processed')}")
    print(f"  LLM layer:    {'ON — ' + llm.get('output_language', '?') if llm.get('enabled') else 'OFF'}")
    print(f"  explanations: {len(explanations)} generated")
    if not explanations:
        print(
            "  NOTE: no machine-generated text in this artifact. Nothing to review\n"
            "        for fabrication, and nothing demonstrating the LLM layer."
        )
    else:
        print(
            f"  NOTE: {len(explanations)} generated sentences require a human\n"
            "        fabrication review before this is published. This script\n"
            "        cannot do that review and does not attempt it."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        default=None,
        help="path to a built HTML report to check as well as the repository",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="skip the history scan (only if the history has been rewritten)",
    )
    args = parser.parse_args(argv)

    findings: list[str] = []
    print("Working tree (tracked files):")
    tree = scan_working_tree()
    findings.extend(tree)
    print(f"  {len(tree)} finding(s)")

    if not args.skip_history:
        print("Git history (credential files):")
        history = scan_history()
        findings.extend(history)
        print(f"  {len(history)} finding(s)")

    if args.artifact:
        path = Path(args.artifact)
        if not path.exists():
            print(f"artifact not found: {path}", file=sys.stderr)
            return 2
        print(f"Artifact {path}:")
        describe_artifact(path)
        artifact = scan_artifact(path)
        findings.extend(artifact)
        print(f"  {len(artifact)} finding(s)")

    if findings:
        print("\nFINDINGS — do not publish until each is resolved or dismissed:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print("\nNo mechanical findings. This is not a substitute for reading the report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
