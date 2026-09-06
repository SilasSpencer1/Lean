"""Normalize untrusted JSON-style Greek observation body records."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ._input_parsing import (
    RejectionCode,
    _InvalidInput,
    _fail,
    _parse_decimal,
    _parse_string,
    _parse_timestamp,
    _parse_token,
    _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .contracts import ContractId
from .greeks import GreekInputs, GreekObservation


_ROOT_FIELDS = (
    "delta",
    "iv",
    "as_of",
    "available_at",
    "delta_unit",
    "iv_unit",
    "method_id",
    "method_version",
    "inputs",
    "input_hash",
    "source",
    "provider_record_id",
    "availability_basis",
)
_INPUT_FIELDS = (
    "option_quote_hash",
    "underlying_quote_hash",
    "rate",
    "dividend_yield",
    "rate_unit",
    "dividend_unit",
    "assumptions_id",
)
_HASH_DIGITS = frozenset("0123456789abcdef")
_AVAILABILITY_BASES = ("measured", "assumed")


@dataclass(frozen=True)
class GreekInputRejection:
    """This class represents one safe Greek observation parsing failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the fixed Greek field/code vocabulary.

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
    def stage(self) -> Literal["greek_normalization"]:
        """
        Return the fixed Greek-normalization stage.

        :returns: The Greek-normalization stage.
        """
        return "greek_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """
        Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class GreekValidation:
    """This class represents exactly one normalized Greek observation or rejection."""

    value: GreekObservation | None = None
    rejection: GreekInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete Greek-normalization outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not GreekObservation:
            raise TypeError("validation result value must be a GreekObservation")
        if self.rejection is not None and type(self.rejection) is not GreekInputRejection:
            raise TypeError("validation result rejection must be a GreekInputRejection")


def normalize_greek_observation(
    raw: object,
    *,
    contract: ContractId,
    event_id: str,
    raw_ref: str,
    received_at: datetime,
) -> GreekValidation:
    """
    Normalize one exact JSON-style Greek observation body dictionary.

    Structure is checked across the root and present non-null ``inputs`` map
    before mandatory fields, then root mandatory fields precede nested mandatory
    fields. Scalars are parsed in root declaration order, descending into
    ``inputs`` at its declared position. Nullable values must still be present.

    :param    raw:          Untrusted dictionary containing only Greek body fields.
    :param    contract:     Trusted exact option contract identity.
    :param    event_id:     Trusted nonempty ingestion-event identifier.
    :param    raw_ref:      Trusted nonempty raw-record locator.
    :param    received_at:  Trusted aware ingestion receipt timestamp.
    :returns:               Exactly one normalized observation or safe rejection.
    :raises   TypeError:    If a trusted argument has the wrong exact type.
    :raises   ValueError:   If trusted identity or timestamp evidence is invalid.
    """
    if type(contract) is not ContractId:
        raise TypeError("contract must be a ContractId")
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        root = _snapshot_input(raw)
        observation = GreekObservation(
            contract=contract,
            delta=_parse_nullable_decimal(root["delta"], "delta"),
            iv=_parse_nullable_decimal(root["iv"], "iv"),
            as_of=_parse_timestamp(root["as_of"], "as_of", nullable=True),
            available_at=_parse_timestamp(
                root["available_at"], "available_at", nullable=True
            ),
            delta_unit=_parse_string(root["delta_unit"], "delta_unit"),
            iv_unit=_parse_string(root["iv_unit"], "iv_unit"),
            method_id=_parse_string(root["method_id"], "method_id"),
            method_version=_parse_string(
                root["method_version"], "method_version"
            ),
            inputs=_parse_inputs(root["inputs"]),
            input_hash=_parse_nullable_hash(root["input_hash"], "input_hash"),
            source=_parse_string(root["source"], "source"),
            provider_record_id=_parse_string(
                root["provider_record_id"], "provider_record_id"
            ),
            availability_basis=_parse_token(
                root["availability_basis"],
                "availability_basis",
                _AVAILABILITY_BASES,
            ),
            received_at=received_at,
            raw_ref=raw_ref,
        )
    except _InvalidInput as failure:
        field, code = failure.args
        return GreekValidation(
            rejection=GreekInputRejection(
                event_id, received_at, raw_ref, field, code
            )
        )
    return GreekValidation(value=observation)


def _snapshot_input(raw: object) -> dict[str, object]:
    """Validate both structural levels and detach them before scalar parsing."""
    if type(raw) is not dict:
        _fail("$", "expected_exact_dict")
    root_keys = tuple(raw)
    if any(type(key) is not str for key in root_keys):
        _fail("$", "unknown_fields")
    if any(key not in _ROOT_FIELDS for key in root_keys):
        _fail("$", "unknown_fields")
    root = raw.copy()

    nested = root.get("inputs")
    if nested is not None:
        if type(nested) is not dict:
            _fail("inputs", "expected_exact_dict")
        nested_keys = tuple(nested)
        if any(type(key) is not str for key in nested_keys):
            _fail("inputs", "unknown_fields")
        if any(key not in _INPUT_FIELDS for key in nested_keys):
            _fail("inputs", "unknown_fields")
        root["inputs"] = nested.copy()

    _require_shape(root, "$", _ROOT_FIELDS)
    if root["inputs"] is not None:
        _require_shape(root["inputs"], "inputs", _INPUT_FIELDS)
    return root


def _parse_inputs(raw: object) -> GreekInputs | None:
    """Parse one already-shaped nullable dependency and assumption map."""
    if raw is None:
        return None
    inputs = raw
    return GreekInputs(
        option_quote_hash=_parse_nullable_hash(
            inputs["option_quote_hash"], "inputs.option_quote_hash"
        ),
        underlying_quote_hash=_parse_nullable_hash(
            inputs["underlying_quote_hash"], "inputs.underlying_quote_hash"
        ),
        rate=_parse_nullable_decimal(inputs["rate"], "inputs.rate"),
        dividend_yield=_parse_nullable_decimal(
            inputs["dividend_yield"], "inputs.dividend_yield"
        ),
        rate_unit=_parse_string(inputs["rate_unit"], "inputs.rate_unit"),
        dividend_unit=_parse_string(
            inputs["dividend_unit"], "inputs.dividend_unit"
        ),
        assumptions_id=_parse_string(
            inputs["assumptions_id"], "inputs.assumptions_id"
        ),
    )


def _parse_nullable_decimal(value: object, field: str) -> Decimal | None:
    """Parse one nullable finite signed ASCII fixed-point value."""
    if value is None:
        return None
    return _parse_decimal(value, field)


def _parse_nullable_hash(value: object, field: str) -> str | None:
    """Parse one nullable canonical lowercase SHA-256 string."""
    if value is None:
        return None
    if type(value) is not str:
        _fail(field, "invalid_type")
    if len(value) != 64 or any(character not in _HASH_DIGITS for character in value):
        _fail(field, "invalid_value")
    return value


def _allowed_codes(field: str) -> tuple[RejectionCode, ...]:
    """Return bounded rejection codes compatible with one safe Greek path."""
    if field == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field == "inputs":
        return ("missing", "expected_exact_dict", "unknown_fields")
    if field in ("delta", "iv", "inputs.rate", "inputs.dividend_yield"):
        return ("missing", "invalid_type", "invalid_decimal")
    if field in ("as_of", "available_at"):
        return ("missing", "invalid_type", "invalid_timestamp")
    if field in (
        "input_hash",
        "inputs.option_quote_hash",
        "inputs.underlying_quote_hash",
    ):
        return ("missing", "invalid_type", "invalid_value")
    if field in (
        "delta_unit",
        "iv_unit",
        "method_id",
        "method_version",
        "source",
        "provider_record_id",
        "availability_basis",
        "inputs.rate_unit",
        "inputs.dividend_unit",
        "inputs.assumptions_id",
    ):
        return ("missing", "invalid_type", "invalid_value")
    return ()
