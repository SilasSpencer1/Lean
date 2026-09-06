"""Typed underlying-share quote and bounded market evidence."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, localcontext

from ._validation import _require_nonempty_string, _trusted_datetime
from .observations import (
    _MAX_QUOTE_AGE,
    ObservationAssessment,
    ObservationMeta,
    assess_observation,
)
from .premium import _exact_precision


_MIDPOINT_DIVISOR = Decimal(2)
_ARITHMETIC_PRECISION_ERROR = "arithmetic precision exceeds 1000 digits"


@dataclass(frozen=True)
class UnderlyingQuote:
    """
    This class represents one trusted typed underlying-share quote.

    Prices are source-claimed USD per share and sizes count displayed shares.
    The record preserves adverse market facts without admitting their source.
    """

    symbol: str
    meta: ObservationMeta
    bid: Decimal | None
    ask: Decimal | None
    bid_at: datetime | None
    ask_at: datetime | None
    bid_size: int | None
    ask_size: int | None

    def __post_init__(self) -> None:
        """
        Validate and normalize the retained underlying quote facts.

        Zero, missing, locked, crossed, stale, future, and non-SPY facts remain
        representable for assessment. No trade or bar price is substituted.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact trusted type.
        :raises   ValueError: If a price, size, symbol, or timestamp is invalid.
        """
        _require_nonempty_string("symbol", self.symbol)
        if type(self.meta) is not ObservationMeta:
            raise TypeError("meta must be an ObservationMeta")
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None:
                _require_exact_decimal(name, value)
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
class UnderlyingQuoteAssessment:
    """
    This class represents derived bounded evidence for an underlying quote.

    It retains the quote and assessment inputs while recomputing all outcomes.
    """

    quote: UnderlyingQuote
    decision_at: datetime
    max_quote_age: timedelta = _MAX_QUOTE_AGE
    observation: ObservationAssessment = field(init=False)
    quote_reasons: tuple[str, ...] = field(init=False)
    midpoint: Decimal | None = field(init=False)
    spread: Decimal | None = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive assessment evidence once.

        :returns:             None.
        :raises   TypeError:  If a trusted input has the wrong exact type.
        :raises   ValueError: If the timestamp or quote-age limit is invalid.
        """
        if type(self.quote) is not UnderlyingQuote:
            raise TypeError("quote must be an UnderlyingQuote")
        decision_at = _trusted_datetime("decision_at", self.decision_at)
        object.__setattr__(self, "decision_at", decision_at)
        if type(self.max_quote_age) is not timedelta:
            raise TypeError("max_quote_age must be a timedelta")
        if self.max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        if self.max_quote_age > _MAX_QUOTE_AGE:
            raise ValueError("max_quote_age cannot exceed five seconds")

        observation = assess_observation(
            self.quote.meta,
            decision_at=decision_at,
            max_quote_age=self.max_quote_age,
        )
        reasons, midpoint, spread = _derive_quote_evidence(
            self.quote, decision_at, self.max_quote_age
        )
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "quote_reasons", reasons)
        object.__setattr__(self, "midpoint", midpoint)
        object.__setattr__(self, "spread", spread)

    @property
    def underlying_quote_suitable(self) -> bool:
        """
        Return whether the independent bounded underlying checks passed.

        This result does not prove source admission, synchronized prices,
        coherence, economic readiness, or authorization to trade.

        :returns: True when metadata, market facts, and exact arithmetic pass.
        """
        return (
            self.observation.live_quote_time_suitable
            and not self.quote_reasons
            and self.midpoint is not None
            and self.spread is not None
        )


def assess_underlying_quote(
    quote: UnderlyingQuote,
    *,
    decision_at: datetime,
    max_quote_age: timedelta = _MAX_QUOTE_AGE,
) -> UnderlyingQuoteAssessment:
    """
    Assess one typed underlying-share quote at an explicit decision time.

    The returned evidence is independent and bounded. It does not establish
    external source admission, option/underlying coherence, economic status,
    or permission to place an order.

    :param    quote:          Typed underlying quote and observation metadata.
    :param    decision_at:    Decision timestamp used without reading a clock.
    :param    max_quote_age:  Positive quote age limit, at most five seconds.
    :returns:                 Immutable derived underlying quote evidence.
    :raises   TypeError:      If a trusted input has the wrong exact type.
    :raises   ValueError:     If the timestamp or quote-age limit is invalid.
    """
    return UnderlyingQuoteAssessment(quote, decision_at, max_quote_age)


def _derive_quote_evidence(
    quote: UnderlyingQuote,
    decision_at: datetime,
    max_quote_age: timedelta,
) -> tuple[tuple[str, ...], Decimal | None, Decimal | None]:
    """Derive fixed-order market reasons and exact price values."""
    reasons: list[str] = []
    if quote.symbol != "SPY":
        reasons.append("unsupported_symbol")

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
        and quote.ask < quote.bid
    ):
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

    midpoint = None
    spread = None
    if (
        quote.bid is not None
        and quote.ask is not None
        and quote.bid > 0
        and quote.ask >= quote.bid
    ):
        try:
            context = Context(
                prec=_exact_precision([quote.bid, quote.ask, _MIDPOINT_DIVISOR]),
                Emax=MAX_EMAX,
                Emin=MIN_EMIN,
            )
        except ValueError as error:
            if str(error) != _ARITHMETIC_PRECISION_ERROR:
                raise
            reasons.append("arithmetic_precision_unsupported")
        else:
            with localcontext(context):
                midpoint = (quote.bid + quote.ask) / _MIDPOINT_DIVISOR
                spread = quote.ask - quote.bid
    return tuple(reasons), midpoint, spread


def _require_exact_decimal(name: str, value: object) -> None:
    """Validate one exact finite Decimal trusted value."""
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal or None")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
