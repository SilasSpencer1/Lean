"""Normalize raw underlying bars and derive bounded content identities."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ._input_parsing import (
    RejectionCode,
    _InvalidInput,
    _fail,
    _parse_decimal,
    _parse_string,
    _parse_token,
    _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .bars import UnderlyingBar
from .config import (
    _MAX_DECIMAL_DIGITS,
    _snapshot_hash,
)
from .observations import ObservationMeta


_BAR_FIELDS = (
    "symbol",
    "close_price",
    "volume",
    "vwap_numerator",
    "vwap_denominator",
    "price_basis",
    "volume_definition_id",
    "vwap_definition_id",
    "revision_id",
    "supersedes_revision_id",
)
_AMOUNT_FIELDS = (
    "close_price", "volume", "vwap_numerator", "vwap_denominator",
)
_NULLABLE_ID_FIELDS = (
    "volume_definition_id", "vwap_definition_id", "supersedes_revision_id",
)
_PRICE_BASES = ("raw", "split_adjusted", "total_return_adjusted", "unknown")
_MAX_INTEGER_DIGITS = 1000
_MAX_INTEGER_EXCLUSIVE = 10 ** _MAX_INTEGER_DIGITS

IdentityReason = Literal[
    "decimal_representation_unsupported", "integer_representation_unsupported"
]


class _UnsupportedIdentity(Exception):
    """This class represents bounded private canonicalization control flow."""


@dataclass(frozen=True)
class BarInputRejection:
    """This class represents one safe underlying bar parsing failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the fixed field/code vocabulary.

        :returns:             None.
        :raises   TypeError:  If identity evidence has the wrong exact type.
        :raises   ValueError: If identity is empty or field/code is unsupported.
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
    def stage(self) -> Literal["underlying_bar_normalization"]:
        """
        Return the fixed underlying-bar normalization stage.

        :returns: The underlying-bar normalization stage.
        """
        return "underlying_bar_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """
        Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class BarValidation:
    """This class represents one normalized underlying bar or safe rejection."""

    value: UnderlyingBar | None = None
    rejection: BarInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete underlying-bar normalization outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not UnderlyingBar:
            raise TypeError("validation result value must be an UnderlyingBar")
        if self.rejection is not None and type(self.rejection) is not BarInputRejection:
            raise TypeError(
                "validation result rejection must be a BarInputRejection"
            )


@dataclass(frozen=True)
class BarContentIdentity:
    """
    This class represents bounded full and economic identities for one bar.

    The economic identity excludes only receipt time, raw locator, and receive
    sequence. Unsupported representations remain explicit and cannot produce
    a partial or rounded hash.
    """

    bar: UnderlyingBar
    full_content_hash: str | None = field(init=False)
    economic_content_hash: str | None = field(init=False)
    full_reasons: tuple[IdentityReason, ...] = field(init=False)
    economic_reasons: tuple[IdentityReason, ...] = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate the exact bar and derive both canonical identities.

        :returns:            None.
        :raises   TypeError: If bar is not an exact UnderlyingBar.
        """
        if type(self.bar) is not UnderlyingBar:
            raise TypeError("bar must be an UnderlyingBar")
        _derive_identities(self)


def normalize_underlying_bar(
    raw: object,
    *,
    meta: ObservationMeta,
    event_id: str,
    receive_sequence: int | None,
) -> BarValidation:
    """
    Normalize one exact JSON-style underlying bar body dictionary.

    Every body field is mandatory and nullable fields must be explicit. The
    metadata and receive sequence are trusted ingestion-envelope facts and
    cannot be supplied or overridden by the provider body.

    :param    raw:               Untrusted dictionary containing bar fields.
    :param    meta:              Trusted exact source and receipt metadata.
    :param    event_id:          Trusted nonempty ingestion-event identifier.
    :param    receive_sequence:  Trusted nonnegative receipt order, or None.
    :returns:                    Exactly one normalized bar or safe rejection.
    :raises   TypeError:         If a trusted argument has the wrong exact type.
    :raises   ValueError:        If trusted identity or sequence is invalid.
    """
    if type(meta) is not ObservationMeta:
        raise TypeError("meta must be an ObservationMeta")
    _require_nonempty_string("event_id", event_id)
    _require_receive_sequence(receive_sequence)

    try:
        _require_shape(raw, "$", _BAR_FIELDS)
        root = dict(raw)
        symbol = _parse_string(root["symbol"], "symbol")
        amounts = {
            name: _parse_amount(root[name], name) for name in _AMOUNT_FIELDS
        }
        price_basis = _parse_token(
            root["price_basis"], "price_basis", _PRICE_BASES
        )
        volume_definition_id = _parse_nullable_string(
            root["volume_definition_id"], "volume_definition_id"
        )
        vwap_definition_id = _parse_nullable_string(
            root["vwap_definition_id"], "vwap_definition_id"
        )
        revision_id = _parse_string(root["revision_id"], "revision_id")
        supersedes_revision_id = _parse_nullable_string(
            root["supersedes_revision_id"], "supersedes_revision_id"
        )
        bar = UnderlyingBar(
            symbol=symbol,
            meta=meta,
            price_basis=price_basis,
            revision_id=revision_id,
            receive_sequence=receive_sequence,
            volume_definition_id=volume_definition_id,
            vwap_definition_id=vwap_definition_id,
            supersedes_revision_id=supersedes_revision_id,
            **amounts,
        )
    except _InvalidInput as failure:
        field_name, code = failure.args
        return BarValidation(
            rejection=BarInputRejection(
                event_id, meta.received_at, meta.raw_ref, field_name, code
            )
        )
    return BarValidation(value=bar)


def identify_underlying_bar(bar: UnderlyingBar) -> BarContentIdentity:
    """
    Derive canonical full and economic content hashes for one bar.

    :param    bar:       Exact trusted underlying bar to identify.
    :returns:            Immutable hashes or bounded unsupported evidence.
    :raises   TypeError: If bar is not an exact UnderlyingBar.
    """
    return BarContentIdentity(bar)


def _parse_amount(value: object, field_name: str) -> Decimal | None:
    """Parse one nullable finite nonnegative exact fixed-point amount."""
    if value is None:
        return None
    parsed = _parse_decimal(value, field_name)
    if parsed < 0:
        _fail(field_name, "invalid_value")
    return parsed


def _parse_nullable_string(value: object, field_name: str) -> str | None:
    """Parse one nullable exact nonempty identifier string."""
    if value is None:
        return None
    return _parse_string(value, field_name)


def _require_receive_sequence(value: object) -> None:
    """Require one trusted exact nonnegative receive sequence or None."""
    if value is None:
        return
    if type(value) is not int:
        raise TypeError("receive_sequence must be an integer or None")
    if value < 0:
        raise ValueError("receive_sequence cannot be negative")


def _derive_identities(result: BarContentIdentity) -> None:
    """Derive economic identity independently from ingestion-only limits."""
    economic_snapshot = None
    try:
        economic_snapshot = _bar_snapshot(result.bar, include_ingestion=False)
    except _UnsupportedIdentity:
        economic_reasons: tuple[IdentityReason, ...] = (
            "decimal_representation_unsupported",
        )
    else:
        economic_reasons = ()

    integer_unsupported = (
        result.bar.receive_sequence is not None
        and result.bar.receive_sequence >= _MAX_INTEGER_EXCLUSIVE
    )
    full_reasons = economic_reasons + (
        ("integer_representation_unsupported",) if integer_unsupported else ()
    )
    object.__setattr__(result, "economic_reasons", economic_reasons)
    object.__setattr__(result, "full_reasons", full_reasons)
    object.__setattr__(
        result,
        "economic_content_hash",
        None if economic_snapshot is None else _snapshot_hash(economic_snapshot),
    )
    object.__setattr__(
        result,
        "full_content_hash",
        None
        if full_reasons
        else _snapshot_hash(_bar_snapshot(result.bar, include_ingestion=True)),
    )


def _bar_snapshot(
    bar: UnderlyingBar, *, include_ingestion: bool
) -> dict[str, object]:
    """Build one fresh explicit allowlisted canonical bar dictionary."""
    metadata = {
        "source": bar.meta.source,
        "provider_record_id": bar.meta.provider_record_id,
        "feed_class": bar.meta.feed_class,
        "fidelity": bar.meta.fidelity,
        "kind": bar.meta.kind,
        "event_at": _timestamp_string(bar.meta.event_at),
        "available_at": _timestamp_string(bar.meta.available_at),
        "availability_basis": bar.meta.availability_basis,
        "availability_evidence_ref": bar.meta.availability_evidence_ref,
        "interval_start": _timestamp_string(bar.meta.interval_start),
        "interval_end": _timestamp_string(bar.meta.interval_end),
        "is_fill_forward": bar.meta.is_fill_forward,
        "quality_flags": list(bar.meta.quality_flags),
    }
    snapshot = {
        "record_kind": "options_lab.underlying_bar",
        "bar_content_schema_version": 1,
        "symbol": bar.symbol,
        "metadata": metadata,
        "close_price": _identity_decimal(bar.close_price),
        "volume": _identity_decimal(bar.volume),
        "vwap_numerator": _identity_decimal(bar.vwap_numerator),
        "vwap_denominator": _identity_decimal(bar.vwap_denominator),
        "price_basis": bar.price_basis,
        "volume_definition_id": bar.volume_definition_id,
        "vwap_definition_id": bar.vwap_definition_id,
        "revision_id": bar.revision_id,
        "supersedes_revision_id": bar.supersedes_revision_id,
    }
    if include_ingestion:
        metadata["raw_ref"] = bar.meta.raw_ref
        metadata["received_at"] = _timestamp_string(bar.meta.received_at)
        snapshot["receive_sequence"] = _sequence_snapshot(bar.receive_sequence)
    return snapshot


def _identity_decimal(value: Decimal | None) -> str | None:
    """Return one bounded canonical fixed-point amount or None."""
    if value is None:
        return None
    if not value:
        return "0"
    sign, digits, exponent = value.as_tuple()
    final_digit = len(digits)
    while digits[final_digit - 1] == 0:
        final_digit -= 1
        exponent += 1
    digit_span = (
        final_digit + exponent
        if exponent >= 0
        else max(final_digit, -exponent)
    )
    if digit_span > _MAX_DECIMAL_DIGITS:
        raise _UnsupportedIdentity
    coefficient = "".join(str(digit) for digit in digits[:final_digit])
    if exponent >= 0:
        rendered = coefficient + ("0" * exponent)
    else:
        split = len(coefficient) + exponent
        rendered = (
            coefficient[:split] + "." + coefficient[split:]
            if split > 0
            else "0." + ("0" * -split) + coefficient
        )
    return ("-" if sign else "") + rendered


def _timestamp_string(value: datetime | None) -> str | None:
    """Return one already-normalized UTC timestamp in canonical ISO form."""
    return None if value is None else value.isoformat()


def _sequence_snapshot(value: int | None) -> dict[str, str] | None:
    """Return an exact base-10 sequence without process digit-limit coupling."""
    if value is None:
        return None
    return {"encoding": "base10", "value": str(Decimal(value))}


def _allowed_codes(field_name: str) -> tuple[RejectionCode, ...]:
    """Return bounded rejection codes compatible with one body field."""
    if field_name == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field_name in ("symbol", "revision_id") + _NULLABLE_ID_FIELDS:
        return ("missing", "invalid_type", "invalid_value")
    if field_name in _AMOUNT_FIELDS:
        return ("missing", "invalid_type", "invalid_decimal", "invalid_value")
    if field_name == "price_basis":
        return ("missing", "invalid_type", "invalid_value")
    return ()
