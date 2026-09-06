"""Typed contract-reference reconciliation and suitability evidence."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from ._validation import _require_nonempty_string, _require_token, _trusted_datetime

ContractRight = Literal["call", "put"]
AvailabilityBasis = Literal["measured", "assumed"]
DeliverableKind = Literal["shares", "cash", "other"]
ListingStatus = Literal["listed", "inactive", "unknown"]

_RIGHTS = ("call", "put")
_AVAILABILITY_BASES = ("measured", "assumed")
_DELIVERABLE_KINDS = ("shares", "cash", "other")
_LISTING_STATUSES = ("listed", "inactive", "unknown")
_STANDARD_QUANTITY = Decimal("100")


@dataclass(frozen=True)
class ContractId:
    """This class represents an exact, provider-independent option identity."""

    underlying: str
    expiry: date
    right: ContractRight
    strike: Decimal
    multiplier: int
    deliverable_id: str

    def __post_init__(self) -> None:
        """
        Validate the exact identity fields while retaining adverse finite facts.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact scalar type.
        :raises   ValueError: If a string, token, or decimal value is invalid.
        """
        _require_nonempty_string("underlying", self.underlying)
        if type(self.expiry) is not date:
            raise TypeError("expiry must be a date")
        _require_token("right", self.right, _RIGHTS)
        _require_finite_decimal("strike", self.strike)
        if type(self.multiplier) is not int:
            raise TypeError("multiplier must be an integer")
        _require_nonempty_string("deliverable_id", self.deliverable_id)


@dataclass(frozen=True)
class ProviderContractMapping:
    """This class represents a provider's supplied mapping to a contract identity."""

    provider: str
    symbol: str
    contract: ContractId
    available_at: datetime
    raw_ref: str
    availability_basis: AvailabilityBasis
    availability_evidence_ref: str

    def __post_init__(self) -> None:
        """
        Validate mapping claims and normalize their availability time to UTC.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact type.
        :raises   ValueError: If a string, token, or timestamp is invalid.
        """
        _require_nonempty_string("provider", self.provider)
        _require_nonempty_string("symbol", self.symbol)
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        object.__setattr__(
            self, "available_at", _trusted_datetime("available_at", self.available_at)
        )
        _require_nonempty_string("raw_ref", self.raw_ref)
        _require_token(
            "availability_basis", self.availability_basis, _AVAILABILITY_BASES
        )
        _require_nonempty_string(
            "availability_evidence_ref", self.availability_evidence_ref
        )


@dataclass(frozen=True)
class DeliverableComponent:
    """This class represents one exact component of a complete deliverable."""

    kind: DeliverableKind
    asset: str
    quantity: Decimal

    def __post_init__(self) -> None:
        """
        Validate a component while retaining finite nonpositive quantities.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact scalar type.
        :raises   ValueError: If a token, string, or decimal value is invalid.
        """
        _require_token("kind", self.kind, _DELIVERABLE_KINDS)
        _require_nonempty_string("asset", self.asset)
        _require_finite_decimal("quantity", self.quantity)


@dataclass(frozen=True)
class ContractReference:
    """This class represents supplied identity, listing, and deliverable evidence."""

    contract: ContractId
    components: tuple[DeliverableComponent, ...] | None
    source: str
    provider_record_id: str
    raw_ref: str
    availability_basis: AvailabilityBasis
    availability_evidence_ref: str
    available_at: datetime
    listed_at: datetime | None
    listing_status: ListingStatus
    effective_from: datetime
    effective_until: datetime | None

    def __post_init__(self) -> None:
        """
        Validate reference claims and normalize all supplied times to UTC.

        :returns:             None.
        :raises   TypeError:  If a field or component collection has the wrong type.
        :raises   ValueError: If a string, token, or timestamp is invalid.
        """
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        if self.components is not None and (
            type(self.components) is not tuple
            or any(type(item) is not DeliverableComponent for item in self.components)
        ):
            raise TypeError(
                "components must be a tuple of DeliverableComponent values or None"
            )
        for name in (
            "source",
            "provider_record_id",
            "raw_ref",
            "availability_evidence_ref",
        ):
            _require_nonempty_string(name, getattr(self, name))
        _require_token(
            "availability_basis", self.availability_basis, _AVAILABILITY_BASES
        )
        _require_token("listing_status", self.listing_status, _LISTING_STATUSES)
        object.__setattr__(
            self, "available_at", _trusted_datetime("available_at", self.available_at)
        )
        object.__setattr__(
            self,
            "effective_from",
            _trusted_datetime("effective_from", self.effective_from),
        )
        for name in ("listed_at", "effective_until"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))


@dataclass(frozen=True)
class ContractReferenceAssessment:
    """This class represents immutable, derived contract-reference evidence."""

    mapping: ProviderContractMapping
    reference: ContractReference
    decision_at: datetime
    reasons: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive canonical suitability reasons once.

        :returns:             None.
        :raises   TypeError:  If a retained input has the wrong exact type.
        :raises   ValueError: If the decision timestamp is invalid.
        """
        if type(self.mapping) is not ProviderContractMapping:
            raise TypeError("mapping must be a ProviderContractMapping")
        if type(self.reference) is not ContractReference:
            raise TypeError("reference must be a ContractReference")
        decision_at = _trusted_datetime("decision_at", self.decision_at)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "reasons", _assessment_reasons(self, decision_at))

    @property
    def reference_suitable(self) -> bool:
        """
        Return whether all bounded reference checks passed.

        This result does not certify source claims or authorize an order.

        :returns: True when no reconciliation or suitability reason was found.
        """
        return not self.reasons


def assess_contract_reference(
    mapping: ProviderContractMapping,
    reference: ContractReference,
    *,
    decision_at: datetime,
) -> ContractReferenceAssessment:
    """
    Reconcile typed mapping and reference claims at one explicit decision time.

    The provider symbol remains opaque. The result assesses only the supplied
    typed claims and does not establish external authenticity or entry readiness.

    :param    mapping:     Provider-supplied symbol-to-contract mapping.
    :param    reference:   Supplied contract and complete-deliverable evidence.
    :param    decision_at: Decision timestamp used without reading a clock.
    :returns:              Immutable reconciliation and suitability evidence.
    :raises   TypeError:   If a trusted argument has the wrong exact type.
    :raises   ValueError:  If the decision timestamp is invalid.
    """
    return ContractReferenceAssessment(mapping, reference, decision_at)


def _assessment_reasons(
    assessment: ContractReferenceAssessment, decision_at: datetime
) -> tuple[str, ...]:
    """Derive canonical reasons from already validated retained facts."""
    mapping = assessment.mapping
    reference = assessment.reference
    mapped_contract = mapping.contract
    referenced_contract = reference.contract
    reasons: list[str] = []

    for name in (
        "underlying",
        "expiry",
        "right",
        "strike",
        "multiplier",
        "deliverable_id",
    ):
        if getattr(mapped_contract, name) != getattr(referenced_contract, name):
            reasons.append(f"identity_{name}_mismatch")

    checks = (
        (mapping.available_at > decision_at, "mapping_available_after_decision"),
        (reference.available_at > decision_at, "reference_available_after_decision"),
        (
            mapping.availability_basis != "measured",
            "mapping_availability_not_measured",
        ),
        (
            reference.availability_basis != "measured",
            "reference_availability_not_measured",
        ),
        (reference.listed_at is None, "listing_time_unknown"),
        (
            reference.listed_at is not None and reference.listed_at > decision_at,
            "listed_after_decision",
        ),
        (reference.listing_status == "inactive", "listing_status_inactive"),
        (reference.listing_status == "unknown", "listing_status_unknown"),
        (decision_at < reference.effective_from, "reference_not_yet_effective"),
        (
            reference.effective_until is not None
            and decision_at >= reference.effective_until,
            "reference_no_longer_effective",
        ),
        (
            reference.effective_until is not None
            and reference.effective_until <= reference.effective_from,
            "effective_interval_invalid",
        ),
        (
            mapped_contract.underlying != "SPY"
            or referenced_contract.underlying != "SPY",
            "unsupported_underlying",
        ),
        (
            mapped_contract.multiplier != 100
            or referenced_contract.multiplier != 100,
            "unsupported_multiplier",
        ),
        (
            mapped_contract.strike <= 0 or referenced_contract.strike <= 0,
            "nonpositive_strike",
        ),
    )
    reasons.extend(reason for failed, reason in checks if failed)

    if reference.components is None:
        reasons.append("deliverable_unknown")
    elif reference.components != (
        DeliverableComponent("shares", "SPY", _STANDARD_QUANTITY),
    ):
        reasons.append("deliverable_unsupported")
    return tuple(reasons)


def _require_finite_decimal(name: str, value: object) -> None:
    """Validate an exact finite Decimal while allowing any finite sign."""
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
