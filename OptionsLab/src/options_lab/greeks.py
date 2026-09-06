"""Typed Greek observations and bounded readiness evidence."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .bar_inputs import _MAX_INTEGER_EXCLUSIVE, _UnsupportedIdentity, _identity_decimal
from .config import _duration_snapshot, _snapshot_hash
from .contracts import ContractId
from .quote_content import QuoteContentIdentity, identify_quote_content
from .quotes import QuoteObservation, _assess_quote_only
from .underlying import UnderlyingQuote, assess_underlying_quote


GreekStatus = Literal["READY", "MISSING", "STALE", "INCOMPATIBLE"]
AvailabilityBasis = Literal["measured", "assumed"]
_MAX_GREEK_AGE = timedelta(seconds=5)
_HASH_DIGITS = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class GreekInputs:
    """This class represents exact dependency and rate claims for one Greek pair."""

    option_quote_hash: str | None
    underlying_quote_hash: str | None
    rate: Decimal | None
    dividend_yield: Decimal | None
    rate_unit: str
    dividend_unit: str
    assumptions_id: str

    def __post_init__(self) -> None:
        """
        Validate exact retained dependency and assumption claims.

        :returns:             None.
        :raises   TypeError:  If a trusted field has the wrong exact type.
        :raises   ValueError: If a hash, amount, or identity string is invalid.
        """
        _require_hash("option_quote_hash", self.option_quote_hash)
        _require_hash("underlying_quote_hash", self.underlying_quote_hash)
        _require_optional_decimal("rate", self.rate)
        _require_optional_decimal("dividend_yield", self.dividend_yield)
        for name in ("rate_unit", "dividend_unit", "assumptions_id"):
            _require_nonempty_string(name, getattr(self, name))


@dataclass(frozen=True)
class GreekObservation:
    """
    This class represents one co-valued delta and implied-volatility observation.

    The single as-of timestamp applies to both values and all declared inputs.
    Missing or physically adverse finite claims remain available for assessment.
    """

    contract: ContractId
    delta: Decimal | None
    iv: Decimal | None
    as_of: datetime | None
    available_at: datetime | None
    delta_unit: str
    iv_unit: str
    method_id: str
    method_version: str
    inputs: GreekInputs | None
    input_hash: str | None
    source: str
    provider_record_id: str
    availability_basis: AvailabilityBasis
    received_at: datetime
    raw_ref: str

    def __post_init__(self) -> None:
        """
        Validate and normalize trusted Greek observation facts.

        :returns:             None.
        :raises   TypeError:  If a trusted field has the wrong exact type.
        :raises   ValueError: If a scalar, timestamp, or vocabulary value is invalid.
        """
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        for name in ("delta", "iv"):
            _require_optional_decimal(name, getattr(self, name))
        for name in ("as_of", "available_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))
        for name in (
            "delta_unit",
            "iv_unit",
            "method_id",
            "method_version",
            "source",
            "provider_record_id",
            "raw_ref",
        ):
            _require_nonempty_string(name, getattr(self, name))
        if self.inputs is not None and type(self.inputs) is not GreekInputs:
            raise TypeError("inputs must be a GreekInputs or None")
        _require_hash("input_hash", self.input_hash)
        _require_token(
            "availability_basis", self.availability_basis, ("measured", "assumed")
        )
        object.__setattr__(
            self, "received_at", _trusted_datetime("received_at", self.received_at)
        )


@dataclass(frozen=True)
class GreekMethodSpec:
    """This class represents bounded semantics for one supplied Greek method."""

    method_id: str
    method_version: str
    assumptions_id: str
    delta_unit: str = "signed_option_price_per_underlying_price"
    iv_unit: str = "annualized_volatility_fraction"
    rate_unit: str = "continuous_annual_fraction"
    dividend_unit: str = "continuous_annual_fraction"
    option_price_basis: str = "option_midpoint"
    underlying_price_basis: str = "underlying_midpoint"
    max_age: timedelta = _MAX_GREEK_AGE
    method_spec_hash: str = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate method semantics and derive their canonical identity.

        :returns:             None.
        :raises   TypeError:  If a trusted field has the wrong exact type.
        :raises   ValueError: If an identity is empty or max age is out of bounds.
        """
        for name in (
            "method_id",
            "method_version",
            "assumptions_id",
            "delta_unit",
            "iv_unit",
            "rate_unit",
            "dividend_unit",
            "option_price_basis",
            "underlying_price_basis",
        ):
            _require_nonempty_string(name, getattr(self, name))
        if type(self.max_age) is not timedelta:
            raise TypeError("max_age must be a timedelta")
        if not timedelta(0) < self.max_age <= _MAX_GREEK_AGE:
            raise ValueError("max_age must be positive and at most five seconds")
        object.__setattr__(self, "method_spec_hash", _method_hash(self))


@dataclass(frozen=True)
class GreekReadiness:
    """This class represents derived bounded Greek readiness at one decision time."""

    greek: GreekObservation | None
    option_quote: QuoteObservation
    underlying_quote: UnderlyingQuote
    method: GreekMethodSpec
    decision_at: datetime
    option_quote_identity: QuoteContentIdentity = field(init=False)
    underlying_quote_identity: QuoteContentIdentity = field(init=False)
    observed_input_hash: str | None = field(init=False)
    expected_input_hash: str | None = field(init=False)
    expected_method_identity: tuple[str, str, str] = field(init=False)
    reasons: tuple[str, ...] = field(init=False)
    status: GreekStatus = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive identities, reasons, and status.

        :returns:             None.
        :raises   TypeError:  If a trusted input has the wrong exact type.
        :raises   ValueError: If the decision timestamp is invalid.
        """
        if self.greek is not None and type(self.greek) is not GreekObservation:
            raise TypeError("greek must be a GreekObservation or None")
        if type(self.option_quote) is not QuoteObservation:
            raise TypeError("option_quote must be a QuoteObservation")
        if type(self.underlying_quote) is not UnderlyingQuote:
            raise TypeError("underlying_quote must be an UnderlyingQuote")
        if type(self.method) is not GreekMethodSpec:
            raise TypeError("method must be a GreekMethodSpec")
        decision_at = _trusted_datetime("decision_at", self.decision_at)
        object.__setattr__(self, "decision_at", decision_at)

        option_identity = identify_quote_content(self.option_quote)
        underlying_identity = identify_quote_content(self.underlying_quote)
        object.__setattr__(self, "option_quote_identity", option_identity)
        object.__setattr__(self, "underlying_quote_identity", underlying_identity)
        object.__setattr__(
            self,
            "expected_method_identity",
            (self.method.method_id, self.method.method_version, self.method.assumptions_id),
        )
        observed_hash = None if self.greek is None else self.greek.input_hash
        object.__setattr__(self, "observed_input_hash", observed_hash)

        expected_hash = _expected_input_hash(
            self.greek, option_identity, underlying_identity, self.method
        )
        object.__setattr__(self, "expected_input_hash", expected_hash)
        reasons = _readiness_reasons(self, decision_at)
        object.__setattr__(self, "reasons", reasons)
        if any(_reason_class(reason) == "INCOMPATIBLE" for reason in reasons):
            status: GreekStatus = "INCOMPATIBLE"
        elif any(_reason_class(reason) == "MISSING" for reason in reasons):
            status = "MISSING"
        elif any(_reason_class(reason) == "STALE" for reason in reasons):
            status = "STALE"
        else:
            status = "READY"
        object.__setattr__(self, "status", status)

    @property
    def ready(self) -> bool:
        """
        Return whether all bounded Greek checks passed.

        This does not prove source admission, joint coherence, or authorization.

        :returns: True only for the derived READY status.
        """
        return self.status == "READY"


def assess_greek_readiness(
    greek: GreekObservation | None,
    option_quote: QuoteObservation,
    underlying_quote: UnderlyingQuote,
    *,
    method: GreekMethodSpec,
    decision_at: datetime,
) -> GreekReadiness:
    """
    Assess one Greek observation against current quote contents and semantics.

    :param    greek:             Supplied Greek pair, or None when unavailable.
    :param    option_quote:      Current typed option quote dependency.
    :param    underlying_quote:  Current typed underlying quote dependency.
    :param    method:            Expected bounded method semantics.
    :param    decision_at:       Decision timestamp used without reading a clock.
    :returns:                    Immutable derived readiness evidence.
    :raises   TypeError:         If a trusted input has the wrong exact type.
    :raises   ValueError:        If the decision timestamp is invalid.
    """
    return GreekReadiness(greek, option_quote, underlying_quote, method, decision_at)


def greek_input_hash(
    inputs: GreekInputs,
    *,
    contract: ContractId,
    method: GreekMethodSpec,
    as_of: datetime,
) -> str:
    """
    Hash exact Greek dependencies, assumptions, method semantics, and valuation time.

    :param    inputs:    Exact dependency and rate assumption claims.
    :param    contract:  Exact option contract valued by the Greek pair.
    :param    method:    Exact method semantics applied to the supplied values.
    :param    as_of:     Shared valuation timestamp for delta and IV.
    :returns:            Canonical lowercase SHA-256 identity.
    :raises   TypeError: If a trusted input has the wrong exact type.
    :raises   ValueError: If a timestamp is invalid or a scalar representation
                         exceeds the bounded identity format.
    """
    if type(inputs) is not GreekInputs:
        raise TypeError("inputs must be a GreekInputs")
    if type(contract) is not ContractId:
        raise TypeError("contract must be a ContractId")
    if type(method) is not GreekMethodSpec:
        raise TypeError("method must be a GreekMethodSpec")
    as_of = _trusted_datetime("as_of", as_of)
    try:
        if not -_MAX_INTEGER_EXCLUSIVE < contract.multiplier < _MAX_INTEGER_EXCLUSIVE:
            raise _UnsupportedIdentity
        snapshot = {
            "record_kind": "options_lab.greek_inputs",
            "greek_input_schema_version": 1,
            "contract": {
                "underlying": contract.underlying,
                "expiry": contract.expiry.isoformat(),
                "right": contract.right,
                "strike": _identity_decimal(contract.strike),
                "multiplier": {
                    "encoding": "base10",
                    "value": str(Decimal(contract.multiplier)),
                },
                "deliverable_id": contract.deliverable_id,
            },
            "as_of": as_of.isoformat(),
            "option_quote_hash": inputs.option_quote_hash,
            "underlying_quote_hash": inputs.underlying_quote_hash,
            "rate": _identity_decimal(inputs.rate),
            "dividend_yield": _identity_decimal(inputs.dividend_yield),
            "rate_unit": inputs.rate_unit,
            "dividend_unit": inputs.dividend_unit,
            "assumptions_id": inputs.assumptions_id,
            "method_spec_hash": method.method_spec_hash,
        }
    except _UnsupportedIdentity:
        raise ValueError("Greek input identity exceeds representation limits") from None
    return _snapshot_hash(snapshot)


def _method_hash(method: GreekMethodSpec) -> str:
    """Hash one validated method specification through its explicit semantics."""
    return _snapshot_hash(
        {
            "record_kind": "options_lab.greek_method_spec",
            "greek_method_schema_version": 1,
            "method_id": method.method_id,
            "method_version": method.method_version,
            "assumptions_id": method.assumptions_id,
            "delta_unit": method.delta_unit,
            "iv_unit": method.iv_unit,
            "rate_unit": method.rate_unit,
            "dividend_unit": method.dividend_unit,
            "option_price_basis": method.option_price_basis,
            "underlying_price_basis": method.underlying_price_basis,
            "max_age": _duration_snapshot(method.max_age),
        }
    )


def _expected_input_hash(
    greek: GreekObservation | None,
    option_identity: QuoteContentIdentity,
    underlying_identity: QuoteContentIdentity,
    method: GreekMethodSpec,
) -> str | None:
    """Recompute the aggregate identity from actual current quote identities."""
    if (
        greek is None
        or greek.inputs is None
        or greek.as_of is None
        or option_identity.content_hash is None
        or underlying_identity.content_hash is None
    ):
        return None
    actual_inputs = replace(
        greek.inputs,
        option_quote_hash=option_identity.content_hash,
        underlying_quote_hash=underlying_identity.content_hash,
    )
    try:
        return greek_input_hash(
            actual_inputs, contract=greek.contract, method=method, as_of=greek.as_of
        )
    except ValueError as error:
        if str(error) != "Greek input identity exceeds representation limits":
            raise
        return None


def _readiness_reasons(
    readiness: GreekReadiness, decision_at: datetime
) -> tuple[str, ...]:
    """Return all readiness failures in a stable evidence order."""
    greek = readiness.greek
    reasons: list[str] = []
    if greek is None:
        reasons.append("greek_missing")
    else:
        _greek_reasons(readiness, greek, decision_at, reasons)

    for reason in readiness.option_quote_identity.reasons:
        reasons.append(f"option_quote_{reason}")
    option_observation, option_quote_reasons = _assess_quote_only(
        readiness.option_quote, decision_at=decision_at
    )
    reasons.extend(
        f"option_observation_{reason}"
        for reason in option_observation.live_quote_reasons
    )
    reasons.extend(f"option_{reason}" for reason in option_quote_reasons)

    underlying_assessment = assess_underlying_quote(
        readiness.underlying_quote, decision_at=decision_at
    )
    for reason in readiness.underlying_quote_identity.reasons:
        reasons.append(f"underlying_quote_{reason}")
    reasons.extend(
        f"underlying_observation_{reason}"
        for reason in underlying_assessment.observation.live_quote_reasons
    )
    reasons.extend(
        f"underlying_{reason}" for reason in underlying_assessment.quote_reasons
    )
    return tuple(dict.fromkeys(reasons))


def _greek_reasons(
    readiness: GreekReadiness,
    greek: GreekObservation,
    decision_at: datetime,
    reasons: list[str],
) -> None:
    """Append observation, binding, timing, and physical-value failures."""
    method = readiness.method
    if greek.contract != readiness.option_quote.contract:
        reasons.append("contract_mismatch")
    for name in ("method_id", "method_version", "delta_unit", "iv_unit"):
        if getattr(greek, name) != getattr(method, name):
            reasons.append(f"{name}_mismatch")
    if greek.availability_basis != "measured":
        reasons.append("availability_not_measured")

    if greek.delta is None:
        reasons.append("delta_missing")
    elif not Decimal("-1") <= greek.delta <= Decimal("1"):
        reasons.append("delta_out_of_range")
    if greek.iv is None:
        reasons.append("iv_missing")
    elif greek.iv <= 0:
        reasons.append("iv_nonpositive")

    if greek.as_of is None:
        reasons.append("as_of_missing")
    else:
        if greek.as_of > decision_at:
            reasons.append("as_of_after_decision")
        if decision_at - greek.as_of > method.max_age:
            reasons.append("greek_too_old")
    if greek.available_at is None:
        reasons.append("available_at_missing")
    else:
        if greek.available_at > decision_at:
            reasons.append("available_after_decision")
        if greek.as_of is not None and greek.as_of > greek.available_at:
            reasons.append("as_of_after_available")

    if greek.inputs is None:
        reasons.append("inputs_missing")
    else:
        _input_reasons(readiness, greek, reasons)
    if greek.input_hash is None:
        reasons.append("input_hash_missing")
    elif (
        readiness.expected_input_hash is not None
        and greek.input_hash != readiness.expected_input_hash
    ):
        reasons.append("input_hash_mismatch")
    if greek.inputs is not None and readiness.expected_input_hash is None:
        identities_supported = (
            readiness.option_quote_identity.content_hash is not None
            and readiness.underlying_quote_identity.content_hash is not None
        )
        if identities_supported and greek.as_of is not None:
            reasons.append("input_identity_unsupported")

    if greek.as_of is not None:
        _causality_reasons("option", readiness.option_quote, greek.as_of, reasons)
        _causality_reasons(
            "underlying", readiness.underlying_quote, greek.as_of, reasons
        )


def _input_reasons(
    readiness: GreekReadiness,
    greek: GreekObservation,
    reasons: list[str],
) -> None:
    """Append declared assumption and actual quote-binding failures."""
    inputs = greek.inputs
    assert inputs is not None
    method = readiness.method
    if inputs.option_quote_hash is None:
        reasons.append("option_quote_hash_missing")
    elif inputs.option_quote_hash != readiness.option_quote_identity.content_hash:
        reasons.append("option_quote_hash_mismatch")
    if inputs.underlying_quote_hash is None:
        reasons.append("underlying_quote_hash_missing")
    elif inputs.underlying_quote_hash != readiness.underlying_quote_identity.content_hash:
        reasons.append("underlying_quote_hash_mismatch")
    if inputs.rate is None:
        reasons.append("rate_missing")
    if inputs.dividend_yield is None:
        reasons.append("dividend_yield_missing")
    for name in ("rate_unit", "dividend_unit", "assumptions_id"):
        if getattr(inputs, name) != getattr(method, name):
            reasons.append(f"{name}_mismatch")


def _causality_reasons(
    prefix: str,
    quote: QuoteObservation | UnderlyingQuote,
    as_of: datetime,
    reasons: list[str],
) -> None:
    """Append source and side event claims later than the Greek valuation."""
    if quote.meta.event_at is not None and quote.meta.event_at > as_of:
        reasons.append(f"{prefix}_event_after_greek_as_of")
    for side in ("bid", "ask"):
        side_at = getattr(quote, f"{side}_at")
        if side_at is not None and side_at > as_of:
            reasons.append(f"{prefix}_{side}_after_greek_as_of")


def _reason_class(reason: str) -> GreekStatus:
    """Classify one reason for fixed readiness-status precedence."""
    if reason == "greek_too_old" or reason.endswith("_too_old"):
        return "STALE"
    if reason == "greek_missing" or reason.endswith("_missing"):
        return "MISSING"
    return "INCOMPATIBLE"


def _require_optional_decimal(name: str, value: object) -> None:
    """Validate one optional exact finite Decimal without restricting its sign."""
    if value is None:
        return
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal or None")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _require_hash(name: str, value: object) -> None:
    """Validate one optional canonical lowercase SHA-256 text claim."""
    if value is None:
        return
    if type(value) is not str:
        raise TypeError(f"{name} must be a string or None")
    if len(value) != 64 or any(character not in _HASH_DIGITS for character in value):
        raise ValueError(f"{name} must be a canonical lowercase SHA-256 hash")


FIXTURE_GREEK_METHOD = GreekMethodSpec(
    method_id="fixture-greek-values",
    method_version="1",
    assumptions_id="fixture-flat-rates-v1",
)
