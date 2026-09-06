"""Normalize admitted tick-rule claims and assess exact price-grid applicability."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ._input_parsing import (
    RejectionCode,
    _InvalidInput,
    _parse_contract_id,
    _parse_decimal,
    _parse_string,
    _parse_timestamp,
    _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import _TICK_DEFINITIONS, VerifiedFixtureManifest, VerifiedFixtureMember
from .bar_inputs import _UnsupportedIdentity, _identity_decimal
from .contracts import ContractId, _require_finite_decimal


FIXTURE_TICK_DEFINITION_ID, FIXTURE_TICK_SOURCE_VERSION = _TICK_DEFINITIONS[0]
_ROOT_FIELDS = (
    "contract", "increment", "price_from", "price_until", "effective_from",
    "effective_until", "available_at", "source", "provider_record_id",
    "source_version", "units", "rule_id",
)
_STRING_FIELDS = ("source", "provider_record_id", "source_version", "units", "rule_id")


@dataclass(frozen=True)
class TickRule:
    """This class represents one supplied instrument price-increment claim."""

    contract: ContractId
    increment: Decimal | None
    price_from: Decimal
    price_until: Decimal | None
    effective_from: datetime | None
    effective_until: datetime | None
    available_at: datetime | None
    source: str
    provider_record_id: str
    source_version: str
    raw_ref: str
    units: str
    rule_id: str

    def __post_init__(self) -> None:
        """
        Validate typed claim scalars while retaining adverse finite facts.

        :returns:             None.
        :raises TypeError:     If a retained field has a wrong exact type.
        :raises ValueError:    If a string, decimal, or timestamp is invalid.
        """
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        _require_finite_decimal("price_from", self.price_from)
        for name in ("increment", "price_until"):
            value = getattr(self, name)
            if value is not None:
                _require_finite_decimal(name, value)
        for name in ("effective_from", "effective_until", "available_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))
        for name in (*_STRING_FIELDS, "raw_ref"):
            _require_nonempty_string(name, getattr(self, name))


@dataclass(frozen=True)
class TickInputRejection:
    """This class represents one safe tick-rule normalization failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: RejectionCode

    def __post_init__(self) -> None:
        """
        Validate closed diagnostic evidence.

        :returns:             None.
        :raises TypeError:     If a retained field has a wrong exact type.
        :raises ValueError:    If the diagnostic pair is unsupported.
        """
        for name in ("event_id", "raw_ref", "field"):
            _require_nonempty_string(name, getattr(self, name))
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        if type(self.code) is not str:
            raise TypeError("code must be a string")
        if self.code not in _allowed_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["tick_normalization"]:
        """Return the fixed tick normalization stage.

        :returns: The fixed tick normalization stage.
        """
        return "tick_normalization"

    @property
    def reasons(self) -> tuple[RejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class TickValidation:
    """This class represents exactly one normalized tick rule or safe rejection."""

    value: TickRule | None = None
    rejection: TickInputRejection | None = None

    def __post_init__(self) -> None:
        """Enforce the exclusive normalized result shape.

        :returns:             None.
        :raises TypeError:     If an outcome has a wrong concrete type.
        :raises ValueError:    If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not TickRule:
            raise TypeError("validation value must be a TickRule")
        if self.rejection is not None and type(self.rejection) is not TickInputRejection:
            raise TypeError("validation rejection must be a TickInputRejection")


@dataclass(frozen=True)
class TickAssessment:
    """This class represents derived actual-member tick applicability evidence."""

    rule: TickRule | None
    manifest: VerifiedFixtureManifest
    contract: ContractId
    price: Decimal
    decision_at: datetime
    reasons: tuple[str, ...] = field(init=False)
    matched_member: VerifiedFixtureMember | None = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive bounded tick evidence.

        :returns:             None.
        :raises TypeError:     If a trusted input has a wrong exact type.
        :raises ValueError:    If a trusted decimal or decision time is invalid.
        """
        if self.rule is not None and type(self.rule) is not TickRule:
            raise TypeError("rule must be a TickRule or None")
        if type(self.manifest) is not VerifiedFixtureManifest:
            raise TypeError("manifest must be a VerifiedFixtureManifest")
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        _require_finite_decimal("price", self.price)
        decision_at = _trusted_datetime("decision_at", self.decision_at)
        object.__setattr__(self, "decision_at", decision_at)
        reasons, member = _assessment(self.rule, self.manifest, self.contract, self.price, decision_at)
        object.__setattr__(self, "reasons", tuple(reasons))
        object.__setattr__(self, "matched_member", member)

    @property
    def tick_aligned(self) -> bool:
        """Return whether all bound applicability and alignment checks passed.

        :returns: True only when the retained rule is fully applicable and exact.
        """
        return not self.reasons


def normalize_tick_rule(raw: object, *, event_id: str, raw_ref: str, received_at: datetime) -> TickValidation:
    """
    Normalize one exact JSON-style tick-rule body.

    :param    raw:          Untrusted body containing all declared tick fields.
    :param    event_id:     Trusted nonempty ingestion event identity.
    :param    raw_ref:      Trusted nonempty raw locator.
    :param    received_at:  Trusted aware ingestion receipt time.
    :returns:               Exactly one normalized rule or safe rejection.
    :raises TypeError:       If a trusted argument has a wrong exact type.
    :raises ValueError:      If trusted identity or receipt time is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        _require_shape(raw, "$", _ROOT_FIELDS)
        root = dict(raw)
        contract = _parse_contract_id(root["contract"])
        increment = _nullable_decimal(root["increment"], "increment")
        price_from = _parse_decimal(root["price_from"], "price_from")
        price_until = _nullable_decimal(root["price_until"], "price_until")
        effective_from = _parse_timestamp(root["effective_from"], "effective_from", nullable=True)
        effective_until = _parse_timestamp(root["effective_until"], "effective_until", nullable=True)
        available_at = _parse_timestamp(root["available_at"], "available_at", nullable=True)
        values = {name: _parse_string(root[name], name) for name in _STRING_FIELDS}
        return TickValidation(value=TickRule(contract, increment, price_from, price_until,
            effective_from, effective_until, available_at, values["source"],
            values["provider_record_id"], values["source_version"], raw_ref,
            values["units"], values["rule_id"]))
    except _InvalidInput as failure:
        field_name, code = failure.args
        return TickValidation(rejection=TickInputRejection(event_id, received_at, raw_ref, field_name, code))


def assess_order_tick(rule: TickRule | None, *, manifest: VerifiedFixtureManifest, contract: ContractId, price: Decimal, decision_at: datetime) -> TickAssessment:
    """
    Assess one intended price against an actual verified fixture tick rule.

    :param    rule:        Typed tick rule or explicit absence.
    :param    manifest:    Actual byte-verified fixture manifest.
    :param    contract:    Exact intended option contract.
    :param    price:       Intended unrounded positive price.
    :param    decision_at: Trusted aware decision instant.
    :returns:              Immutable exact tick assessment.
    :raises TypeError:      If a trusted input has a wrong exact type.
    :raises ValueError:     If a trusted decimal or decision time is invalid.
    """
    return TickAssessment(rule, manifest, contract, price, decision_at)


def _nullable_decimal(value: object, field_name: str) -> Decimal | None:
    """Parse one explicit nullable fixed-point decimal.

    :param    value:       Untrusted scalar value.
    :param    field_name:  Safe field path.
    :returns:              Parsed finite decimal or None.
    """
    return None if value is None else _parse_decimal(value, field_name)


def _assessment(rule: TickRule | None, manifest: VerifiedFixtureManifest, contract: ContractId, price: Decimal, decision_at: datetime) -> tuple[list[str], VerifiedFixtureMember | None]:
    """Derive ordered binding, applicability, and exact-grid reasons.

    :returns: Ordered reasons and the unique actual member when available.
    """
    reasons: list[str] = []
    if rule is None:
        return ["rule_missing", *_price_reason(price)], None
    matches = _matching_members(rule, manifest)
    member = matches[0] if len(matches) == 1 else None
    if not matches:
        reasons.append("rule_member_missing")
    elif len(matches) != 1:
        reasons.append("rule_member_ambiguous")
    profile = None
    if member is not None:
        profile = next((item for item in manifest.decode_modeled_source_profiles() if item["profile_id"] == member.profile_id), None)
    if profile is None or not _profile_matches(profile, rule):
        reasons.append("rule_profile_mismatch")
    if rule.rule_id not in manifest.tick_definition_ids:
        reasons.append("definition_not_registered")
    if (rule.rule_id, rule.source_version) != (FIXTURE_TICK_DEFINITION_ID, FIXTURE_TICK_SOURCE_VERSION):
        reasons.append("definition_version_unregistered")
    if rule.units != "USD_per_share":
        reasons.append("units_unsupported")
    if rule.contract != contract:
        reasons.append("contract_mismatch")
    if rule.increment is None:
        reasons.append("increment_missing")
    elif rule.increment <= 0:
        reasons.append("increment_not_positive")
    if rule.price_from < 0:
        reasons.append("price_from_negative")
    if rule.price_until is not None and rule.price_until <= rule.price_from:
        reasons.append("price_band_invalid")
    reasons.extend(_price_reason(price))
    if price > 0 and rule.price_from >= 0 and (rule.price_until is None or rule.price_until > rule.price_from):
        if price < rule.price_from or (rule.price_until is not None and price >= rule.price_until):
            reasons.append("price_outside_band")
    if rule.effective_from is None:
        reasons.append("effective_from_missing")
    if rule.effective_until is None:
        reasons.append("effective_until_missing")
    if rule.effective_from is not None and rule.effective_until is not None:
        if rule.effective_until <= rule.effective_from:
            reasons.append("effective_interval_invalid")
        elif not rule.effective_from <= decision_at < rule.effective_until:
            reasons.append("effective_not_applicable")
    if rule.available_at is None:
        reasons.append("available_at_missing")
    elif rule.available_at > decision_at:
        reasons.append("available_after_decision")
    representation_supported = _bounded_representations(
        price, rule.increment, rule.price_from, rule.price_until
    )
    if not representation_supported:
        reasons.append("decimal_representation_unsupported")
    if representation_supported and rule.increment is not None and rule.increment > 0 and price > 0:
        if not _is_aligned(price, rule.increment):
            reasons.append("price_off_grid")
    return reasons, member


def _price_reason(price: Decimal) -> tuple[str, ...]:
    """Derive the retained intended-price sign reason.

    :returns: A bounded sign reason or no reason.
    """
    return ("price_not_positive",) if price <= 0 else ()


def _bounded_representations(*values: Decimal | None) -> bool:
    """Confirm every supplied arithmetic Decimal has the shared bounded form.

    :returns: True when each non-null value is safe for exact arithmetic.
    """
    try:
        for value in values:
            _identity_decimal(value)
    except _UnsupportedIdentity:
        return False
    return True


def _matching_members(rule: TickRule, manifest: VerifiedFixtureManifest) -> tuple[VerifiedFixtureMember, ...]:
    """Return all actual members whose full normalized rule equals the input.

    :returns: Exact matching immutable admitted members.
    """
    matches: list[VerifiedFixtureMember] = []
    for member in manifest.members:
        if member.kind != "tick_rule":
            continue
        envelope = member.decode_envelope()
        result = normalize_tick_rule(member.decode_raw_body(), event_id=envelope["event_id"], raw_ref=envelope["raw_ref"], received_at=_parse_timestamp(envelope["simulated_received_at"], "received_at"))
        if result.value == rule:
            matches.append(member)
    return tuple(matches)


def _profile_matches(profile: dict[str, object], rule: TickRule) -> bool:
    """Check the actual admitted tick profile against the normalized rule.

    :returns: True when the exact permitted profile values agree.
    """
    return profile == {
        "profile_id": profile["profile_id"], "kind": "tick_rule", "source": rule.source,
        "stream_id": profile["stream_id"], "feed_class": None, "fidelity": None,
        "availability_basis": "measured", "units": {"price": "USD_per_share"},
        "record_identity_rule": "new_provider_record_id_per_update",
    }


def _is_aligned(price: Decimal, increment: Decimal) -> bool:
    """Check zero-origin Decimal alignment with bounded exact integer arithmetic.

    :returns: True when price is an integral multiple of increment.
    """
    canonical_price = Decimal(_identity_decimal(price))
    canonical_increment = Decimal(_identity_decimal(increment))
    numerator, denominator = canonical_price.as_integer_ratio()
    tick_numerator, tick_denominator = canonical_increment.as_integer_ratio()
    return divmod(numerator * tick_denominator, denominator * tick_numerator)[1] == 0


def _allowed_codes(field_name: str) -> tuple[str, ...]:
    """Return the closed safe diagnostic codes for one tick path.

    :returns: Allowed fixed rejection codes.
    """
    if field_name == "$":
        return ("expected_exact_dict", "unknown_fields")
    if field_name == "contract":
        return ("missing", "expected_exact_dict", "unknown_fields")
    if field_name in ("increment", "price_from", "price_until", "contract.strike"):
        return ("missing", "invalid_type", "invalid_decimal")
    if field_name in ("effective_from", "effective_until", "available_at"):
        return ("missing", "invalid_type", "invalid_timestamp")
    if field_name == "contract.expiry":
        return ("missing", "invalid_type", "invalid_date")
    if field_name == "contract.multiplier":
        return ("missing", "invalid_type")
    if field_name in (*_STRING_FIELDS, "contract.underlying", "contract.right", "contract.deliverable_id"):
        return ("missing", "invalid_type", "invalid_value")
    return ()
