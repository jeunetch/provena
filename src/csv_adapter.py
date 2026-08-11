"""CSV input adapter.

One row per event; rows sharing an object_id form that object's chain. The
adapter validates every row before returning anything, so a file with
problems fails as a whole with a complete list rather than half-loading.

Column names are the internal schema field names (see schema.FIELD_NAMES).
Unknown columns are rejected: a misspelled header would otherwise silently
drop the data it carries.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .schema import (
    DEFAULT_CIRCA_MARGIN_YEARS,
    FIELD_NAMES,
    REQUIRED_FIELD_NAMES,
    InputValidationError,
    ObjectChain,
    ProvenanceRecord,
    RecordValidationError,
    build_record,
    group_into_chains,
)


def load_records(
    path: str | Path, circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS
) -> list[ProvenanceRecord]:
    """Read and validate a CSV file into records. Raises InputValidationError."""
    path = Path(path)
    problems: list[str] = []
    records: list[ProvenanceRecord] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputValidationError([f"{path}: file is empty (no header row)"])

        headers = [name.strip() for name in reader.fieldnames]
        unknown = [name for name in headers if name not in FIELD_NAMES]
        missing = [name for name in REQUIRED_FIELD_NAMES if name not in headers]
        if unknown:
            problems.append(
                f"{path}: unrecognised column(s): {', '.join(unknown)}. "
                f"Permitted columns: {', '.join(FIELD_NAMES)}"
            )
        if missing:
            problems.append(
                f"{path}: required column(s) absent: {', '.join(missing)}"
            )
        if problems:
            raise InputValidationError(problems)

        for order, row in enumerate(reader):
            # DictReader keys off the raw header; re-key to the stripped names.
            cleaned = {
                header: row.get(original)
                for header, original in zip(headers, reader.fieldnames)
            }
            if not any((value or "").strip() for value in cleaned.values()):
                continue  # blank line
            source_ref = f"{path.name}:row {order + 2}"  # +2: header plus 1-indexing
            try:
                records.append(
                    build_record(cleaned, source_ref, order, circa_margin_years)
                )
            except RecordValidationError as exc:
                problems.extend(f"{exc.source_ref}: {p}" for p in exc.problems)

    if problems:
        raise InputValidationError(problems)
    if not records:
        raise InputValidationError([f"{path}: no data rows"])
    return records


def load_chains(
    path: str | Path, circa_margin_years: int = DEFAULT_CIRCA_MARGIN_YEARS
) -> list[ObjectChain]:
    """Read a CSV file into date-ordered object chains."""
    return group_into_chains(load_records(path, circa_margin_years))
