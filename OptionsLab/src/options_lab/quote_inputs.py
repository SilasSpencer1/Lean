"""Normalize untrusted JSON-style option quote body records."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ._input_parsing import (
    RejectionCode,
    _InvalidInput,
    _fail,
    _parse_decimal,
    _parse_timestamp,
    _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .contracts import ContractId
from .observations import ObservationMeta
from .quotes import QuoteObservation

_QUOTE_FIELDS = ("bid", "ask", "bid_at", "ask_at", "bid_size", "ask_size")


@dataclass(frozen=True)
class QuoteInputRejection:
    """This class represents one safe quote parsing failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the fixed quote field/code vocabulary.

        :returns:             None.
        :raises   TypeError:  If an identity, timestamp, field, or code has the wrong type.
        :raises   ValueError: If an identity is empty or the field/code pair is unsupported.
        """
        _require_nonempty_string("event_id", self.event_id)
        _require_nonempty_string("raw_ref", self.raw_ref)
        object.__setattr__(
            self, "received_at", _trusted_datetime("received_at", self.received_at)
        )
        _require_nonempty_string("field", self.field)
        _require_nonempty_string("code", self.code)
        if self.code not in _allowed_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["quote_normalization"]:
        """Return the fixed quote-normalization stage.

        :returns: The quote-normalization stage.
        """
        return "quote_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class QuoteValidation:
    """This class represents exactly one normalized quote or safe rejection."""

    value: QuoteObservation | None = None
    rejection: QuoteInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete quote-normalization outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not QuoteObservation:
            raise TypeError("validation result value must be a QuoteObservation")
        if self.rejection is not None and type(self.rejection) is not QuoteInputRejection:
            raise TypeError("validation result rejection must be a QuoteInputRejection")


def normalize_quote_observation(
    raw: object,
    *,
    contract: ContractId,
    meta: ObservationMeta,
    event_id: str,
) -> QuoteValidation:
    """
    Normalize one exact JSON-style option quote body dictionary.

    ``raw`` contains exactly the mandatory keys ``bid``, ``ask``, ``bid_at``,
    ``ask_at``, ``bid_size``, and ``ask_size``. Every value may explicitly be
    null. Non-null prices are nonnegative ASCII fixed-point strings, side times
    are aware ISO strings, and sizes are exact nonnegative integer option-contract
    counts. Contract identity comes from ``contract``; rejection ``raw_ref`` and
    ``received_at`` come from the trusted ``meta`` envelope.

    :param    raw:      Untrusted dictionary containing only the quote-body fields.
    :param    contract: Trusted exact contract identity for the quote.
    :param    meta:     Trusted exact source identity and receipt metadata.
    :param    event_id: Trusted nonempty ingestion-event identifier.
    :returns:           Exactly one normalized quote or safe rejection.
    :raises   TypeError: If a trusted argument has the wrong exact type.
    :raises   ValueError: If the trusted event identifier is empty.
    """
    if type(contract) is not ContractId:
        raise TypeError("contract must be a ContractId")
    if type(meta) is not ObservationMeta:
        raise TypeError("meta must be an ObservationMeta")
    _require_nonempty_string("event_id", event_id)
    try:
        _require_shape(raw, "$", _QUOTE_FIELDS)
        root = raw
        bid = _parse_price(root["bid"], "bid")
        ask = _parse_price(root["ask"], "ask")
        bid_at = _parse_timestamp(root["bid_at"], "bid_at", nullable=True)
        ask_at = _parse_timestamp(root["ask_at"], "ask_at", nullable=True)
        bid_size = _parse_size(root["bid_size"], "bid_size")
        ask_size = _parse_size(root["ask_size"], "ask_size")
        quote = QuoteObservation(
            contract, meta, bid, ask, bid_at, ask_at, bid_size, ask_size,
        )
    except _InvalidInput as failure:
        field, code = failure.args
        return QuoteValidation(
            rejection=QuoteInputRejection(
                event_id, meta.received_at, meta.raw_ref, field, code
            )
        )
    return QuoteValidation(value=quote)


def _parse_price(value: object, field: str) -> Decimal | None:
    """Parse one nullable, finite, nonnegative fixed-point price."""
    if value is None:
        return None
    parsed = _parse_decimal(value, field)
    if parsed < 0:
        _fail(field, "invalid_value")
    return parsed


def _parse_size(value: object, field: str) -> int | None:
    """Parse one nullable, exact, nonnegative option-contract count."""
    if value is None:
        return None
    if type(value) is not int:
        _fail(field, "invalid_type")
    if value < 0:
        _fail(field, "invalid_value")
    return value


def _allowed_codes(field: str) -> tuple[RejectionCode, ...]:
    """Return the bounded rejection codes compatible with one quote field."""
    if field == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field in ("bid", "ask"):
        return ("missing", "invalid_type", "invalid_decimal", "invalid_value")
    if field in ("bid_at", "ask_at"):
        return ("missing", "invalid_type", "invalid_timestamp")
    if field in ("bid_size", "ask_size"):
        return ("missing", "invalid_type", "invalid_value")
    return ()
