"""Typed option quote and joined premium-budget evidence."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, localcontext

from ._validation import _trusted_datetime
from .contracts import ContractId
from .observations import _MAX_QUOTE_AGE, ObservationAssessment, ObservationMeta, assess_observation
from .premium import (
    _CONTRACT_MULTIPLIER,
    _MAXIMUM_PREMIUM_FRACTION,
    _MINIMUM_FEE,
    PremiumBudget,
    _exact_precision,
    assess_premium_budget,
)

_MINIMUM_SPREAD = Decimal("0.05")
_MAXIMUM_SPREAD_FRACTION = Decimal("0.08")
_MIDPOINT_DIVISOR = Decimal(2)
_ARITHMETIC_PRECISION_ERROR = "arithmetic precision exceeds 1000 digits"


@dataclass(frozen=True)
class QuoteObservation:
    """
    This class represents one trusted typed option quote observation.

    It is not a raw-data normalizer; malformed trusted inputs raise errors.
    """

    contract: ContractId
    meta: ObservationMeta
    bid: Decimal | None
    ask: Decimal | None
    bid_at: datetime | None
    ask_at: datetime | None
    bid_size: int | None
    ask_size: int | None

    def __post_init__(self) -> None:
        """
        Validate and normalize the retained quote facts.

        Zero, missing, locked, crossed, stale, and future facts remain
        representable for later assessment. Sizes count option contracts.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact trusted type.
        :raises   ValueError: If a price, size, or timestamp is invalid.
        """
        if type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId")
        if type(self.meta) is not ObservationMeta:
            raise TypeError("meta must be an ObservationMeta")
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None:
                _require_exact_decimal(name, value, nullable=True)
                if value < 0:
                    raise ValueError(f"{name} cannot be negative")
        for name in ("bid_at", "ask_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _trusted_datetime(name, value))
        for name in ("bid_size", "ask_size"):
            value = getattr(self, name)
            if value is not None:
                if type(value) is not int:
                    raise TypeError(f"{name} must be an integer or None")
                if value < 0:
                    raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class QuotePremiumAssessment:
    """This class represents derived option quote and premium-budget evidence."""

    quote: QuoteObservation
    decision_at: datetime
    virtual_equity: Decimal | None
    available_cash: Decimal | None
    round_trip_fees: Decimal = Decimal("1")
    premium_fraction: Decimal = Decimal("0.005")
    max_quote_age: timedelta = _MAX_QUOTE_AGE
    max_spread_fraction: Decimal = _MAXIMUM_SPREAD_FRACTION
    spread_floor: Decimal = _MINIMUM_SPREAD
    original_ask_cap: Decimal | None = None
    observation: ObservationAssessment = field(init=False)
    quote_reasons: tuple[str, ...] = field(init=False)
    budget: PremiumBudget | None = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive all assessment evidence once.

        :returns:             None.
        :raises   TypeError:  If a trusted input has the wrong exact type.
        :raises   ValueError: If a trusted timestamp, money value, fee, or
                              premium fraction is invalid.
        """
        if type(self.quote) is not QuoteObservation:
            raise TypeError("quote must be a QuoteObservation")
        decision_at = _trusted_datetime("decision_at", self.decision_at)
        object.__setattr__(self, "decision_at", decision_at)
        for name in ("virtual_equity", "available_cash", "original_ask_cap"):
            value = getattr(self, name)
            if value is not None:
                _require_exact_decimal(name, value, nullable=True)
        _require_exact_decimal("round_trip_fees", self.round_trip_fees)
        _require_exact_decimal("premium_fraction", self.premium_fraction)
        if type(self.max_quote_age) is not timedelta:
            raise TypeError("max_quote_age must be a timedelta")
        _require_exact_decimal("max_spread_fraction", self.max_spread_fraction)
        _require_exact_decimal("spread_floor", self.spread_floor)
        if self.round_trip_fees < 0:
            raise ValueError("round_trip_fees cannot be negative")
        if self.premium_fraction <= 0:
            raise ValueError("premium_fraction must be greater than zero")
        if self.premium_fraction > _MAXIMUM_PREMIUM_FRACTION:
            raise ValueError("premium_fraction cannot exceed 0.005")
        if self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        if self.max_quote_age > _MAX_QUOTE_AGE:
            raise ValueError("max_quote_age cannot exceed five seconds")
        if self.max_spread_fraction <= 0:
            raise ValueError("max_spread_fraction must be greater than zero")
        if self.max_spread_fraction > _MAXIMUM_SPREAD_FRACTION:
            raise ValueError("max_spread_fraction cannot exceed 0.08")
        if self.spread_floor <= 0:
            raise ValueError("spread_floor must be greater than zero")
        if self.spread_floor > _MINIMUM_SPREAD:
            raise ValueError("spread_floor cannot exceed 0.05")

        observation, quote_reasons, budget = _derive_evidence(self, decision_at)
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "quote_reasons", quote_reasons)
        object.__setattr__(self, "budget", budget)

    @property
    def quote_budget_suitable(self) -> bool:
        """
        Return whether the bounded quote checks and premium budget passed.

        This result assesses independent source ages only. It does not prove
        simultaneous validity, source authenticity, reference suitability,
        Greeks, session or risk eligibility, a fill, or entry authorization.

        :returns: True when metadata and quote checks pass and the budget is
                  affordable.
        """
        return self.observation.live_quote_time_suitable and not self.quote_reasons and (
            self.budget is not None and self.budget.affordable
        )


def assess_quote_premium_budget(
    quote: QuoteObservation,
    *,
    decision_at: datetime,
    virtual_equity: Decimal | None,
    available_cash: Decimal | None,
    round_trip_fees: Decimal = Decimal("1"),
    premium_fraction: Decimal = Decimal("0.005"),
    max_quote_age: timedelta = _MAX_QUOTE_AGE,
    max_spread_fraction: Decimal = _MAXIMUM_SPREAD_FRACTION,
    spread_floor: Decimal = _MINIMUM_SPREAD,
    original_ask_cap: Decimal | None = None,
) -> QuotePremiumAssessment:
    """
    Assess a typed quote and one-contract premium budget at a decision time.

    The result retains all supplied facts. It is bounded evidence and does
    not establish synchronized prices, contract reference proof, economic
    readiness, or permission to place an order. A finite incompatible original
    cap remains evidence with no budget; current quote prices stay unchanged.

    :param    quote:             Typed option quote and observation metadata.
    :param    decision_at:       Decision timestamp used without reading a clock.
    :param    virtual_equity:    Declared virtual capital, or None when absent.
    :param    available_cash:    Unencumbered settled cash, or None when absent.
    :param    round_trip_fees:   Estimated fees, subject to a one-dollar floor.
    :param    premium_fraction:  Maximum share of virtual equity, at most 0.005.
    :param    max_quote_age:     Positive quote age limit, at most five seconds.
    :param    max_spread_fraction: Positive midpoint spread fraction, at most 0.08.
    :param    spread_floor:      Positive absolute spread floor, at most 0.05.
    :param    original_ask_cap:  Retained original cap, or None for current ask.
    :returns:                    Immutable quote and premium-budget evidence.
    :raises   TypeError:         If a trusted input has the wrong exact type.
    :raises   ValueError:        If a trusted timestamp, money value, fee, or
                                 premium fraction is invalid.
    """
    return QuotePremiumAssessment(
        quote,
        decision_at,
        virtual_equity,
        available_cash,
        round_trip_fees,
        premium_fraction,
        max_quote_age,
        max_spread_fraction,
        spread_floor,
        original_ask_cap=original_ask_cap,
    )


def _assess_quote_only(
    quote: QuoteObservation,
    *,
    decision_at: datetime,
    max_quote_age: timedelta = _MAX_QUOTE_AGE,
    max_spread_fraction: Decimal = _MAXIMUM_SPREAD_FRACTION,
    spread_floor: Decimal = _MINIMUM_SPREAD,
) -> tuple[ObservationAssessment, tuple[str, ...]]:
    """Assess current option quote evidence without account or premium inputs.

    :param quote: Exact already-trusted option observation.
    :param decision_at: Explicit aware current assessment time.
    :param max_quote_age: Trusted owner/configured maximum age.
    :param max_spread_fraction: Trusted configured spread fraction; defaults unchanged.
    :param spread_floor: Trusted configured absolute spread floor.
    :returns: Independent observation assessment and current quote reasons.
    :raises TypeError: If the trusted assessment time has an incorrect type.
    :raises ValueError: If the trusted assessment time is invalid.
    """
    decision_at = _trusted_datetime("decision_at", decision_at)
    observation = assess_observation(
        quote.meta,
        decision_at=decision_at,
        max_quote_age=max_quote_age,
    )
    arithmetic_context = _bounded_context(
        value
        for value in (
            quote.bid,
            quote.ask,
            max_spread_fraction,
            spread_floor,
            _MIDPOINT_DIVISOR,
        )
        if value is not None
    )
    return observation, _quote_reasons(
        quote,
        decision_at,
        max_quote_age,
        max_spread_fraction,
        spread_floor,
        arithmetic_context,
    )


def _derive_evidence(
    assessment: QuotePremiumAssessment, decision_at: datetime
) -> tuple[ObservationAssessment, tuple[str, ...], PremiumBudget | None]:
    """Derive canonical observation, quote, and budget evidence."""
    quote = assessment.quote
    observation = assess_observation(
        quote.meta,
        decision_at=decision_at,
        max_quote_age=assessment.max_quote_age,
    )
    arithmetic_context = _bounded_context(
        value
        for value in (
            quote.bid,
            quote.ask,
            assessment.virtual_equity,
            assessment.available_cash,
            assessment.round_trip_fees,
            assessment.premium_fraction,
            assessment.max_spread_fraction,
            assessment.spread_floor,
            _MIDPOINT_DIVISOR,
            _CONTRACT_MULTIPLIER,
            _MINIMUM_FEE,
            _MAXIMUM_PREMIUM_FRACTION,
            assessment.original_ask_cap,
        )
        if value is not None
    )
    reasons = _quote_reasons(
        quote,
        decision_at,
        assessment.max_quote_age,
        assessment.max_spread_fraction,
        assessment.spread_floor,
        arithmetic_context,
    )

    cap = assessment.original_ask_cap
    if cap is not None:
        if cap <= 0:
            reasons = (*reasons, "original_ask_cap_nonpositive")
        elif quote.ask is not None and quote.ask > cap:
            reasons = (*reasons, "ask_exceeds_original_cap")

    if not observation.live_quote_time_suitable or reasons:
        return observation, reasons, None

    try:
        budget = assess_premium_budget(
            ask=quote.ask,
            bid=quote.bid,
            virtual_equity=assessment.virtual_equity,
            available_cash=assessment.available_cash,
            round_trip_fees=assessment.round_trip_fees,
            premium_fraction=assessment.premium_fraction,
            original_ask_cap=assessment.original_ask_cap,
        )
    except ValueError as error:
        if str(error) != _ARITHMETIC_PRECISION_ERROR:
            raise
        return observation, ("arithmetic_precision_unsupported",), None
    return observation, reasons, budget


def _bounded_context(values) -> Context | None:
    """Build the shared exact context or report the documented precision cap."""
    try:
        precision = _exact_precision(list(values))
    except ValueError as error:
        if str(error) != _ARITHMETIC_PRECISION_ERROR:
            raise
        return None
    return Context(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN)


def _quote_reasons(
    quote: QuoteObservation,
    decision_at: datetime,
    max_quote_age: timedelta,
    max_spread_fraction: Decimal,
    spread_floor: Decimal,
    arithmetic_context: Context | None,
) -> tuple[str, ...]:
    """Return quote failures in fixed policy order."""
    reasons: list[str] = []
    if quote.contract.underlying != "SPY":
        reasons.append("unsupported_underlying")
    if quote.contract.multiplier != 100:
        reasons.append("unsupported_multiplier")

    if quote.bid is None:
        reasons.append("bid_missing")
    elif quote.bid <= 0:
        reasons.append("bid_nonpositive")
    if quote.ask is None:
        reasons.append("ask_missing")
    elif quote.ask <= 0:
        reasons.append("ask_nonpositive")
    if (
        quote.bid is not None
        and quote.ask is not None
        and quote.bid > 0
        and quote.ask > 0
    ):
        if quote.ask == quote.bid:
            reasons.append("quote_locked")
        elif quote.ask < quote.bid:
            reasons.append("quote_crossed")

    for side in ("bid", "ask"):
        side_at = getattr(quote, f"{side}_at")
        if side_at is None:
            reasons.append(f"{side}_time_missing")
        else:
            if side_at > decision_at:
                reasons.append(f"{side}_time_after_decision")
            if side_at > quote.meta.available_at:
                reasons.append(f"{side}_time_after_available")
            if decision_at - side_at > max_quote_age:
                reasons.append(f"{side}_too_old")

    for side in ("bid", "ask"):
        size = getattr(quote, f"{side}_size")
        if size is None:
            reasons.append(f"{side}_size_missing")
        elif size <= 0:
            reasons.append(f"{side}_size_nonpositive")

    if arithmetic_context is None:
        reasons.append("arithmetic_precision_unsupported")
    elif (
        quote.bid is not None
        and quote.ask is not None
        and quote.bid > 0
        and quote.ask > quote.bid
    ):
        with localcontext(arithmetic_context):
            midpoint = (quote.bid + quote.ask) / _MIDPOINT_DIVISOR
            spread_limit = max(
                spread_floor,
                max_spread_fraction * midpoint,
            )
            if quote.ask - quote.bid > spread_limit:
                reasons.append("spread_too_wide")
    return tuple(reasons)


def _require_exact_decimal(name: str, value: object, *, nullable: bool = False) -> None:
    """Validate an exact finite Decimal trusted value."""
    if type(value) is not Decimal:
        suffix = " or None" if nullable else ""
        raise TypeError(f"{name} must be a Decimal{suffix}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
