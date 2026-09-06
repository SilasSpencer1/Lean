"""Normalize untrusted JSON-style contract-reference records."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
import re
from typing import Literal, Never

from ._validation import _require_nonempty_string, _trusted_datetime
from .contracts import ContractId, ContractReference, DeliverableComponent

RejectionCode = Literal[
    "expected_exact_dict", "unknown_fields", "missing", "invalid_type",
    "invalid_value", "invalid_date", "invalid_timestamp", "invalid_decimal",
]

_ROOT_FIELDS = (
    "contract", "components", "source", "provider_record_id",
    "availability_basis", "availability_evidence_ref", "available_at",
    "listed_at", "listing_status", "effective_from", "effective_until",
)
_CONTRACT_FIELDS = (
    "underlying", "expiry", "right", "strike", "multiplier", "deliverable_id",
)
_COMPONENT_FIELDS = ("kind", "asset", "quantity")
_MONEY_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


class _InvalidReference(Exception):
    """This class represents private first-failure parser control flow."""


@dataclass(frozen=True)
class ContractReferenceRejection:
    """This class represents one safe contract-reference parsing failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the fixed field/code vocabulary.

        :returns:             None.
        :raises   TypeError:  If an identity, timestamp, field, or code has the wrong type.
        :raises   ValueError: If an identity is empty or the field/code pair is unsupported.
        """
        _require_nonempty_string("event_id", self.event_id)
        _require_nonempty_string("raw_ref", self.raw_ref)
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        _require_nonempty_string("field", self.field)
        _require_nonempty_string("code", self.code)
        if self.code not in _allowed_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["contract_reference_normalization"]:
        """Return the fixed processing stage.

        :returns: The contract-reference normalization stage.
        """
        return "contract_reference_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class ContractReferenceValidation:
    """This class represents exactly one normalized value or safe rejection."""

    value: ContractReference | None = None
    rejection: ContractReferenceRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not ContractReference:
            raise TypeError("validation result value must be a ContractReference")
        if self.rejection is not None and type(self.rejection) is not ContractReferenceRejection:
            raise TypeError("validation result rejection must be a ContractReferenceRejection")


def normalize_contract_reference(
    raw: object,
    *,
    raw_ref: str,
    event_id: str,
    received_at: datetime,
) -> ContractReferenceValidation:
    """
    Normalize one exact JSON-style contract-reference dictionary.

    Every root, contract, and component key is mandatory. ``components``,
    ``listed_at``, and ``effective_until`` may explicitly be null. Money uses
    ASCII fixed-point strings; expiry uses ``YYYY-MM-DD``; timestamps use aware
    strings accepted by ``datetime.fromisoformat``. Malformed source content
    produces the first deterministic safe rejection, while trusted envelope
    misuse raises.

    Root keys are ``contract``, ``components``, ``source``,
    ``provider_record_id``, ``availability_basis``,
    ``availability_evidence_ref``, ``available_at``, ``listed_at``,
    ``listing_status``, ``effective_from``, and ``effective_until``. Contract
    keys are ``underlying``, ``expiry``, ``right``, ``strike``, ``multiplier``,
    and ``deliverable_id``. Each component has ``kind``, ``asset``, and
    ``quantity``. ``raw_ref`` belongs only to the trusted envelope.

    :param    raw:         Untrusted JSON-style contract reference.
    :param    raw_ref:     Trusted reference to the unmodified source record.
    :param    event_id:    Trusted identifier for the ingestion event.
    :param    received_at: Trusted current-ingestion timestamp.
    :returns:              Exactly one normalized value or safe rejection.
    :raises   TypeError:   If a trusted envelope argument has the wrong exact type.
    :raises   ValueError:  If a trusted envelope string or timestamp is invalid.
    """
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        _require_shape(raw, "$", _ROOT_FIELDS)
        root = raw
        contract = _parse_contract(root["contract"])
        components = _parse_components(root["components"])
        source = _parse_string(root["source"], "source")
        provider_record_id = _parse_string(root["provider_record_id"], "provider_record_id")
        availability_basis = _parse_token(
            root["availability_basis"], "availability_basis", ("measured", "assumed")
        )
        availability_evidence_ref = _parse_string(
            root["availability_evidence_ref"], "availability_evidence_ref"
        )
        available_at = _parse_timestamp(root["available_at"], "available_at")
        listed_at = _parse_timestamp(root["listed_at"], "listed_at", nullable=True)
        listing_status = _parse_token(
            root["listing_status"], "listing_status", ("listed", "inactive", "unknown")
        )
        effective_from = _parse_timestamp(root["effective_from"], "effective_from")
        effective_until = _parse_timestamp(
            root["effective_until"], "effective_until", nullable=True
        )
        reference = ContractReference(
            contract, components, source, provider_record_id, raw_ref,
            availability_basis, availability_evidence_ref, available_at,
            listed_at, listing_status, effective_from, effective_until,
        )
    except _InvalidReference as failure:
        field, code = failure.args
        return _rejected(event_id, received_at, raw_ref, field, code)
    return ContractReferenceValidation(value=reference)


def _parse_contract(raw: object) -> ContractId:
    """Parse one concrete contract identity for this and provider inputs."""
    _require_shape(raw, "contract", _CONTRACT_FIELDS)
    contract = raw
    underlying = _parse_string(contract["underlying"], "contract.underlying")
    expiry = _parse_date(contract["expiry"], "contract.expiry")
    right = _parse_token(contract["right"], "contract.right", ("call", "put"))
    strike = _parse_decimal(contract["strike"], "contract.strike")
    multiplier = contract["multiplier"]
    if type(multiplier) is not int:
        _fail("contract.multiplier", "invalid_type")
    deliverable_id = _parse_string(contract["deliverable_id"], "contract.deliverable_id")
    return ContractId(underlying, expiry, right, strike, multiplier, deliverable_id)


def _parse_components(raw: object) -> tuple[DeliverableComponent, ...] | None:
    """Parse a null or exact list of concrete deliverable components."""
    if raw is None:
        return None
    if type(raw) is not list:
        _fail("components", "invalid_type")
    parsed: list[DeliverableComponent] = []
    for component in raw:
        _require_shape(component, "components[]", _COMPONENT_FIELDS)
        kind = _parse_token(
            component["kind"], "components[].kind", ("shares", "cash", "other")
        )
        asset = _parse_string(component["asset"], "components[].asset")
        quantity = _parse_decimal(component["quantity"], "components[].quantity")
        parsed.append(DeliverableComponent(kind, asset, quantity))
    return tuple(parsed)


def _require_shape(raw: object, path: str, fields: tuple[str, ...]) -> None:
    """Require one exact dictionary with only and all declared string keys."""
    if type(raw) is not dict:
        _fail(path, "expected_exact_dict")
    keys = tuple(raw)
    if any(type(key) is not str for key in keys):
        _fail(path, "unknown_fields")
    if any(key not in fields for key in keys):
        _fail(path, "unknown_fields")
    for field in fields:
        if field not in raw:
            _fail(field if path == "$" else f"{path}.{field}", "missing")


def _parse_string(value: object, field: str) -> str:
    """Parse one exact nonempty external string."""
    if type(value) is not str:
        _fail(field, "invalid_type")
    if not value:
        _fail(field, "invalid_value")
    return value


def _parse_token(value: object, field: str, allowed: tuple[str, ...]) -> str:
    """Parse one exact string from a fixed local vocabulary."""
    parsed = _parse_string(value, field)
    if parsed not in allowed:
        _fail(field, "invalid_value")
    return parsed


def _parse_date(value: object, field: str) -> date:
    """Parse one exact ASCII calendar date string."""
    if type(value) is not str:
        _fail(field, "invalid_type")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        _fail(field, "invalid_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(field, "invalid_date")


def _parse_decimal(value: object, field: str) -> Decimal:
    """Parse one exact ASCII fixed-point string without changing caller context."""
    if type(value) is not str:
        _fail(field, "invalid_type")
    if _MONEY_PATTERN.fullmatch(value) is None:
        _fail(field, "invalid_decimal")
    with localcontext():
        parsed = Decimal(value)
    if not parsed.is_finite():
        _fail(field, "invalid_decimal")
    return parsed


def _parse_timestamp(value: object, field: str, *, nullable: bool = False) -> datetime | None:
    """Parse one exact aware ISO datetime string and normalize it to UTC."""
    if value is None and nullable:
        return None
    if type(value) is not str:
        _fail(field, "invalid_type")
    try:
        return _trusted_datetime("timestamp", datetime.fromisoformat(value))
    except ValueError:
        _fail(field, "invalid_timestamp")


def _fail(field: str, code: RejectionCode) -> Never:
    """Stop parsing at one safe field and code."""
    raise _InvalidReference(field, code)


def _rejected(
    event_id: str, received_at: datetime, raw_ref: str, field: str, code: RejectionCode
) -> ContractReferenceValidation:
    """Build one safe immutable rejection result."""
    return ContractReferenceValidation(
        rejection=ContractReferenceRejection(event_id, received_at, raw_ref, field, code)
    )


def _allowed_codes(field: str) -> tuple[RejectionCode, ...]:
    """Return the bounded rejection codes compatible with one safe path."""
    if field == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field == "contract":
        return ("missing", "expected_exact_dict", "unknown_fields")
    if field == "components":
        return ("missing", "invalid_type")
    if field == "components[]":
        return ("expected_exact_dict", "unknown_fields")
    if field in (
        "source", "provider_record_id", "availability_evidence_ref",
        "contract.underlying", "contract.deliverable_id", "components[].asset",
        "availability_basis", "listing_status", "contract.right", "components[].kind",
    ):
        return ("missing", "invalid_type", "invalid_value")
    if field in ("available_at", "listed_at", "effective_from", "effective_until"):
        return ("missing", "invalid_type", "invalid_timestamp")
    if field == "contract.expiry":
        return ("missing", "invalid_type", "invalid_date")
    if field in ("contract.strike", "components[].quantity"):
        return ("missing", "invalid_type", "invalid_decimal")
    if field == "contract.multiplier":
        return ("missing", "invalid_type")
    return ()
