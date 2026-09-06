"""One-contract premium-budget policy."""

from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, localcontext

_CONTRACT_MULTIPLIER = Decimal(100)
_MINIMUM_FEE = Decimal("1")
_MAXIMUM_PREMIUM_FRACTION = Decimal("0.005")
_MAXIMUM_ARITHMETIC_PRECISION = 1000


@dataclass(frozen=True)
class PremiumBudget:
    """This class represents evidence from a premium-budget assessment."""

    premium: Decimal
    fees: Decimal
    adverse_reserve: Decimal
    required_cash: Decimal
    equity_limit: Decimal | None
    reason: str | None

    @property
    def affordable(self) -> bool:
        """
        Return whether every premium-budget condition passed.

        :returns:  True only when the assessment has no rejection reason.
        """
        return self.reason is None


def assess_premium_budget(
    *,
    ask: Decimal,
    bid: Decimal,
    virtual_equity: Decimal | None,
    available_cash: Decimal | None,
    quantity: int = 1,
    round_trip_fees: Decimal = Decimal("1"),
    premium_fraction: Decimal = Decimal("0.005"),
) -> PremiumBudget:
    """
    Assess whether one contract fits the supplied premium and cash limits.

    A successful result is budget evidence only. It neither authorizes an
    order nor reserves money.

    :param    ask:              Decision quote ask price per share.
    :param    bid:              Decision quote bid price per share.
    :param    virtual_equity:   Declared virtual capital, or None when absent.
    :param    available_cash:   Unencumbered settled cash, or None when absent.
    :param    quantity:         Requested contract quantity, which must be one.
    :param    round_trip_fees:  Estimated fees, subject to a one-dollar floor.
    :param    premium_fraction: Maximum share of virtual equity, at most 0.005.
    :returns:                   Immutable budget evidence and one rejection reason.
    :raises   ValueError:       If an input is invalid or needs more than 1000
                               digits of working precision.
    """
    values = {
        "ask": ask,
        "bid": bid,
        "round_trip_fees": round_trip_fees,
        "premium_fraction": premium_fraction,
    }
    for name, value in values.items():
        _require_decimal(name, value)
    for name, value in (
        ("virtual_equity", virtual_equity),
        ("available_cash", available_cash),
    ):
        if value is not None:
            _require_decimal(name, value, nullable=True)
    if type(quantity) is not int:
        raise ValueError("quantity must be an integer")
    if ask <= 0:
        raise ValueError("ask must be positive")
    if bid <= 0:
        raise ValueError("bid must be positive")
    if bid > ask:
        raise ValueError("bid cannot exceed ask")
    if round_trip_fees < 0:
        raise ValueError("round_trip_fees cannot be negative")
    if premium_fraction <= 0:
        raise ValueError("premium_fraction must be greater than zero")
    if premium_fraction > _MAXIMUM_PREMIUM_FRACTION:
        raise ValueError("premium_fraction cannot exceed 0.005")

    decimal_values = [*values.values()]
    if virtual_equity is not None:
        decimal_values.append(virtual_equity)
    if available_cash is not None:
        decimal_values.append(available_cash)
    arithmetic_context = Context(
        prec=_exact_precision(decimal_values),
        Emax=MAX_EMAX,
        Emin=MIN_EMIN,
    )
    with localcontext(arithmetic_context):
        premium = _CONTRACT_MULTIPLIER * ask
        fees = max(_MINIMUM_FEE, round_trip_fees)
        reserve = max(
            _MAXIMUM_PREMIUM_FRACTION * premium,
            _CONTRACT_MULTIPLIER * (ask - bid),
        )
        required_cash = premium + fees + reserve
        equity_limit = (
            None if virtual_equity is None else premium_fraction * virtual_equity
        )

    if quantity != 1:
        reason = "quantity must be exactly one contract"
    elif virtual_equity is None:
        reason = "virtual equity is undeclared"
    elif virtual_equity <= 0:
        reason = "virtual equity must be positive"
    elif required_cash > equity_limit:
        reason = "premium cap exceeded"
    elif available_cash is None:
        reason = "available cash is unavailable"
    elif required_cash > available_cash:
        reason = "insufficient available cash"
    else:
        reason = None

    return PremiumBudget(
        premium=premium,
        fees=fees,
        adverse_reserve=reserve,
        required_cash=required_cash,
        equity_limit=equity_limit,
        reason=reason,
    )


def _require_decimal(name: str, value: object, *, nullable: bool = False) -> None:
    """Reject values that are not finite Decimal instances."""
    if not isinstance(value, Decimal):
        suffix = " or None" if nullable else ""
        raise ValueError(f"{name} must be a Decimal{suffix}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _exact_precision(values: list[Decimal]) -> int:
    """Return bounded local precision sufficient for exact products and sums."""
    nonzero_values = [value for value in values if value]
    highest_place = max(value.adjusted() for value in nonzero_values)
    fractional_places = -sum(
        min(value.as_tuple().exponent, 0) for value in nonzero_values
    )
    significant_digits = sum(
        len(value.as_tuple().digits) for value in nonzero_values
    )
    required_precision = max(
        28,
        highest_place + fractional_places + significant_digits + 20,
    )
    if required_precision > _MAXIMUM_ARITHMETIC_PRECISION:
        raise ValueError("arithmetic precision exceeds 1000 digits")
    return required_precision
