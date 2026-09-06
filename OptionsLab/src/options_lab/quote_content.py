"""Derive bounded semantic identities for typed market quotes."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .bar_inputs import (
    _MAX_INTEGER_EXCLUSIVE,
    _UnsupportedIdentity,
    _identity_decimal,
)
from .config import _snapshot_hash
from .observations import ObservationMeta
from .quotes import QuoteObservation
from .underlying import UnderlyingQuote


IdentityReason = Literal[
    "decimal_representation_unsupported", "integer_representation_unsupported"
]
QuoteRecord = QuoteObservation | UnderlyingQuote


@dataclass(frozen=True)
class QuoteContentIdentity:
    """
    This class represents one bounded semantic identity for a typed quote.

    Receipt time and the raw locator remain on the retained quote but do not
    affect its semantic content hash.
    """

    quote: QuoteRecord
    content_hash: str | None = field(init=False)
    reasons: tuple[IdentityReason, ...] = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate the exact quote kind and derive its content identity.

        :returns:            None.
        :raises   TypeError: If quote is not an exact supported quote record.
        """
        if type(self.quote) not in (QuoteObservation, UnderlyingQuote):
            raise TypeError("quote must be a QuoteObservation or UnderlyingQuote")
        reasons = _identity_reasons(self.quote)
        object.__setattr__(self, "reasons", reasons)
        if reasons:
            object.__setattr__(self, "content_hash", None)
            return
        snapshot = (
            _option_snapshot(self.quote)
            if type(self.quote) is QuoteObservation
            else _underlying_snapshot(self.quote)
        )
        object.__setattr__(self, "content_hash", _snapshot_hash(snapshot))


def identify_quote_content(quote: QuoteRecord) -> QuoteContentIdentity:
    """
    Derive the bounded semantic content identity for one typed market quote.

    :param    quote:     Exact option or underlying quote to identify.
    :returns:            Immutable content hash or unsupported evidence.
    :raises   TypeError: If quote is not an exact supported quote record.
    """
    return QuoteContentIdentity(quote)


def _identity_reasons(quote: QuoteRecord) -> tuple[IdentityReason, ...]:
    """Return all representation failures in fixed semantic order."""
    decimals = (quote.bid, quote.ask)
    if type(quote) is QuoteObservation:
        decimals += (quote.contract.strike,)
    try:
        for value in decimals:
            _identity_decimal(value)
    except _UnsupportedIdentity:
        decimal_unsupported = True
    else:
        decimal_unsupported = False

    integers = (quote.bid_size, quote.ask_size)
    if type(quote) is QuoteObservation:
        integers += (quote.contract.multiplier,)
    integer_unsupported = any(
        value is not None
        and not -_MAX_INTEGER_EXCLUSIVE < value < _MAX_INTEGER_EXCLUSIVE
        for value in integers
    )
    return (
        *(("decimal_representation_unsupported",) if decimal_unsupported else ()),
        *(("integer_representation_unsupported",) if integer_unsupported else ()),
    )


def _option_snapshot(quote: QuoteObservation) -> dict[str, object]:
    """Build one fresh explicit canonical option-quote dictionary."""
    return {
        "record_kind": "options_lab.option_quote",
        "quote_content_schema_version": 1,
        "contract": {
            "underlying": quote.contract.underlying,
            "expiry": quote.contract.expiry.isoformat(),
            "right": quote.contract.right,
            "strike": _identity_decimal(quote.contract.strike),
            "multiplier": _integer_snapshot(quote.contract.multiplier),
            "deliverable_id": quote.contract.deliverable_id,
        },
        "metadata": _metadata_snapshot(quote.meta),
        **_side_snapshot(quote),
        "price_unit": "option_premium_USD_per_share",
        "size_unit": "contracts",
    }


def _underlying_snapshot(quote: UnderlyingQuote) -> dict[str, object]:
    """Build one fresh explicit canonical underlying-quote dictionary."""
    return {
        "record_kind": "options_lab.underlying_quote",
        "quote_content_schema_version": 1,
        "symbol": quote.symbol,
        "metadata": _metadata_snapshot(quote.meta),
        **_side_snapshot(quote),
        "price_unit": "USD_per_share",
        "size_unit": "shares",
    }


def _metadata_snapshot(meta: ObservationMeta) -> dict[str, object]:
    """Build the shared non-ingestion metadata projection."""
    return {
        "source": meta.source,
        "provider_record_id": meta.provider_record_id,
        "feed_class": meta.feed_class,
        "fidelity": meta.fidelity,
        "kind": meta.kind,
        "event_at": _timestamp_string(meta.event_at),
        "available_at": _timestamp_string(meta.available_at),
        "availability_basis": meta.availability_basis,
        "availability_evidence_ref": meta.availability_evidence_ref,
        "interval_start": _timestamp_string(meta.interval_start),
        "interval_end": _timestamp_string(meta.interval_end),
        "is_fill_forward": meta.is_fill_forward,
        "quality_flags": list(meta.quality_flags),
    }


def _side_snapshot(quote: QuoteRecord) -> dict[str, object]:
    """Build the common exact side-value projection."""
    return {
        "bid": _identity_decimal(quote.bid),
        "ask": _identity_decimal(quote.ask),
        "bid_at": _timestamp_string(quote.bid_at),
        "ask_at": _timestamp_string(quote.ask_at),
        "bid_size": _integer_snapshot(quote.bid_size),
        "ask_size": _integer_snapshot(quote.ask_size),
    }


def _integer_snapshot(value: int | None) -> dict[str, str] | None:
    """Render one exact integer independently of the interpreter digit limit."""
    if value is None:
        return None
    return {"encoding": "base10", "value": str(Decimal(value))}


def _timestamp_string(value: datetime | None) -> str | None:
    """Return one already-normalized UTC timestamp in canonical ISO form."""
    return None if value is None else value.isoformat()
