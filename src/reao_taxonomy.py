"""REAO-grounded transaction states and presumption-tier logic.

The transaction_state values are the taxonomy used in German/Austrian
restitution practice, not an invented enum. Do not add members without a
cited source.

Presumption tiers are reported, never scored. A tier says which limb of the
REAO Art. 3 presumption a transfer engages *if* the transferor was a
persecuted person — which the record does not establish and this tool does
not decide.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

# Nuremberg Laws. Under REAO Art. 3 this is where the ordinary presumption of
# a persecution-related loss becomes the heightened presumption.
NUREMBERG_LAWS_DATE = date(1935, 9, 15)


class TransactionState(str, Enum):
    """REAO Art. 3 taxonomy plus the ordinary commercial states."""

    ENTZIEHUNG = "entziehung"
    ZWANGSVERKAUF = "zwangsverkauf"
    VERMUTUNG_DER_ENTZIEHUNG = "vermutung_der_entziehung"
    VERSCHAERFTE_VERMUTUNG = "verschaerfte_vermutung"
    FLUCHTGUT = "fluchtgut"
    RESTITUTION_ERFOLGT_OR_VERGLEICH = "restitution_erfolgt_or_vergleich"
    PURCHASE = "purchase"
    GIFT = "gift"
    EXCHANGE = "exchange"
    UNKNOWN = "unknown"


STATE_GLOSSES = {
    TransactionState.ENTZIEHUNG: "seizure by state act",
    TransactionState.ZWANGSVERKAUF: "sale under persecution",
    TransactionState.VERMUTUNG_DER_ENTZIEHUNG: (
        "rebuttable presumption — persecuted-person transfer before 15 Sept 1935"
    ),
    TransactionState.VERSCHAERFTE_VERMUTUNG: (
        "heightened presumption — persecuted-person transfer on or after 15 Sept 1935"
    ),
    TransactionState.FLUCHTGUT: "sold by a refugee in a safe haven",
    TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH: "already restituted or settled",
    TransactionState.PURCHASE: "purchase",
    TransactionState.GIFT: "gift",
    TransactionState.EXCHANGE: "exchange",
    TransactionState.UNKNOWN: "unknown",
}


class PresumptionTier(str, Enum):
    ORDINARY = "ordinary"
    HEIGHTENED = "heightened"
    NOT_APPLICABLE = "n/a"


class RestitutionRecipientType(str, Enum):
    """Who a recorded restitution or settlement was made to.

    External restitution (to a state) frequently succeeded where internal
    restitution (to the dispossessed family) did not, so the two are a
    material distinction rather than a bookkeeping detail. Nothing in the
    rest of the schema carries it, and it is never inferred from
    `owner_name` — an unset value means the rule cannot run.
    """

    INDIVIDUAL_OR_HEIRS = "individual_or_heirs"
    STATE_OR_INSTITUTION = "state_or_institution"
    UNKNOWN = "unknown"


def parse_restitution_recipient_type(text: str | None) -> RestitutionRecipientType | None:
    """Parse the optional restitution_recipient_type cell. Empty means unset."""
    if text is None:
        return None
    cleaned = text.strip().lower()
    if not cleaned:
        return None
    try:
        return RestitutionRecipientType(cleaned)
    except ValueError:
        permitted = ", ".join(r.value for r in RestitutionRecipientType)
        raise ValueError(
            f"unrecognised restitution_recipient_type {text!r}; permitted: {permitted}"
        ) from None


# Only the two states that are themselves definitions of a presumption tier
# carry one intrinsically. For every other state the tier follows from the
# transfer date relative to 15 Sept 1935 — see heuristics.tier_for_window.
INTRINSIC_TIER = {
    TransactionState.VERMUTUNG_DER_ENTZIEHUNG: PresumptionTier.ORDINARY,
    TransactionState.VERSCHAERFTE_VERMUTUNG: PresumptionTier.HEIGHTENED,
}


def intrinsic_tier(state: TransactionState | None) -> PresumptionTier | None:
    """Tier carried by the transaction_state itself, or None to derive by date."""
    if state is None:
        return None
    return INTRINSIC_TIER.get(state)


def is_resolved_state(state: TransactionState | None) -> bool:
    """True for a state that marks the matter as previously settled."""
    return state is TransactionState.RESTITUTION_ERFOLGT_OR_VERGLEICH


def parse_transaction_state(text: str | None) -> TransactionState | None:
    """Parse a transaction_state cell. Empty means not recorded, not `unknown`.

    Raises ValueError on an unrecognised value — a typo must fail loudly
    rather than silently degrade to `unknown`.
    """
    if text is None:
        return None
    cleaned = text.strip().lower()
    if not cleaned:
        return None
    try:
        return TransactionState(cleaned)
    except ValueError:
        permitted = ", ".join(s.value for s in TransactionState)
        raise ValueError(
            f"unrecognised transaction_state {text!r}; permitted values: {permitted}"
        ) from None
