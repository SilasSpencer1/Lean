"""Normalize untrusted JSON-style contract input records."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ._input_parsing import (
    RejectionCode,
    _InvalidInput,
    _fail,
    _parse_contract_id,
    _parse_decimal,
    _parse_string,
    _parse_timestamp,
    _parse_token,
    _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .contracts import (
    ContractReference,
    DeliverableComponent,
    ProviderContractMapping,
)

_ROOT_FIELDS = (
    "contract", "components", "source", "provider_record_id",
    "availability_basis", "availability_evidence_ref", "available_at",
    "listed_at", "listing_status", "effective_from", "effective_until",
)
_MAPPING_ROOT_FIELDS = (
    "provider", "symbol", "contract", "available_at", "availability_basis",
    "availability_evidence_ref",
)
_COMPONENT_FIELDS = ("kind", "asset", "quantity")


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


@dataclass(frozen=True)
class ProviderContractMappingRejection:
    """This class represents one safe provider-contract mapping parsing failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the mapping field/code vocabulary.

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
        if self.code not in _allowed_mapping_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["provider_contract_mapping_normalization"]:
        """Return the fixed provider-contract mapping normalization stage.

        :returns: The provider-contract mapping normalization stage.
        """
        return "provider_contract_mapping_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class ProviderContractMappingValidation:
    """This class represents exactly one normalized mapping or safe rejection."""

    value: ProviderContractMapping | None = None
    rejection: ProviderContractMappingRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete provider-mapping outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not ProviderContractMapping:
            raise TypeError("validation result value must be a ProviderContractMapping")
        if (
            self.rejection is not None
            and type(self.rejection) is not ProviderContractMappingRejection
        ):
            raise TypeError(
                "validation result rejection must be a ProviderContractMappingRejection"
            )


def normalize_provider_contract_mapping(
    raw: object,
    *,
    raw_ref: str,
    event_id: str,
    received_at: datetime,
) -> ProviderContractMappingValidation:
    """
    Normalize one exact JSON-style provider-contract mapping dictionary.

    All root and contract fields are mandatory and null is never accepted.
    Provider, symbol, and evidence strings are exact, nonempty, and preserved
    verbatim. The symbol is opaque and is never decoded. Contract expiry uses
    ``YYYY-MM-DD`` and money uses an ASCII fixed-point string. ``available_at``
    uses an aware string accepted by ``datetime.fromisoformat`` and is
    normalized to UTC. ``availability_basis`` is ``measured`` or ``assumed``.

    Root keys are ``provider``, ``symbol``, ``contract``, ``available_at``,
    ``availability_basis``, and ``availability_evidence_ref``. Contract keys
    are ``underlying``, ``expiry``, ``right``, ``strike``, ``multiplier``, and
    ``deliverable_id``. ``raw_ref`` belongs only to the trusted envelope.
    Normalization retains supplied evidence without inferring identity or
    suitability; malformed source content yields one deterministic rejection.

    :param    raw:         Untrusted JSON-style provider-contract mapping.
    :param    raw_ref:     Trusted reference to the unmodified source record.
    :param    event_id:    Trusted identifier for the ingestion event.
    :param    received_at: Trusted current-ingestion timestamp.
    :returns:              Exactly one normalized mapping or safe rejection.
    :raises   TypeError:   If a trusted envelope argument has the wrong exact type.
    :raises   ValueError:  If a trusted envelope string or timestamp is invalid.
    """
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        _require_shape(raw, "$", _MAPPING_ROOT_FIELDS)
        root = raw
        provider = _parse_string(root["provider"], "provider")
        symbol = _parse_string(root["symbol"], "symbol")
        contract = _parse_contract_id(root["contract"])
        available_at = _parse_timestamp(root["available_at"], "available_at")
        availability_basis = _parse_token(
            root["availability_basis"], "availability_basis", ("measured", "assumed")
        )
        availability_evidence_ref = _parse_string(
            root["availability_evidence_ref"], "availability_evidence_ref"
        )
        mapping = ProviderContractMapping(
            provider, symbol, contract, available_at, raw_ref,
            availability_basis, availability_evidence_ref,
        )
    except _InvalidInput as failure:
        field, code = failure.args
        return _rejected_mapping(event_id, received_at, raw_ref, field, code)
    return ProviderContractMappingValidation(value=mapping)


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
        contract = _parse_contract_id(root["contract"])
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
    except _InvalidInput as failure:
        field, code = failure.args
        return _rejected(event_id, received_at, raw_ref, field, code)
    return ContractReferenceValidation(value=reference)


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


def _rejected(
    event_id: str, received_at: datetime, raw_ref: str, field: str, code: RejectionCode
) -> ContractReferenceValidation:
    """Build one safe immutable rejection result."""
    return ContractReferenceValidation(
        rejection=ContractReferenceRejection(event_id, received_at, raw_ref, field, code)
    )


def _rejected_mapping(
    event_id: str, received_at: datetime, raw_ref: str, field: str, code: RejectionCode
) -> ProviderContractMappingValidation:
    """Build one safe immutable provider-mapping rejection result."""
    return ProviderContractMappingValidation(
        rejection=ProviderContractMappingRejection(
            event_id, received_at, raw_ref, field, code
        )
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


def _allowed_mapping_codes(field: str) -> tuple[RejectionCode, ...]:
    """Return mapping-only rejection codes compatible with one safe path."""
    if field == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field == "contract":
        return ("missing", "expected_exact_dict", "unknown_fields")
    if field in (
        "provider", "symbol", "availability_basis", "availability_evidence_ref",
        "contract.underlying", "contract.right", "contract.deliverable_id",
    ):
        return ("missing", "invalid_type", "invalid_value")
    if field == "available_at":
        return ("missing", "invalid_type", "invalid_timestamp")
    if field == "contract.expiry":
        return ("missing", "invalid_type", "invalid_date")
    if field == "contract.strike":
        return ("missing", "invalid_type", "invalid_decimal")
    if field == "contract.multiplier":
        return ("missing", "invalid_type")
    return ()
