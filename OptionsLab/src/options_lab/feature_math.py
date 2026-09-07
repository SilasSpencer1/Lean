"""Deterministic raw-close returns and unannualized realized volatility."""

from dataclasses import dataclass
from datetime import datetime
from decimal import (
    Clamped, Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, Subnormal, Underflow,
)

from ._validation import _trusted_datetime
from .bar_inputs import _identity_decimal, _UnsupportedIdentity, identify_underlying_bar
from .bars import UnderlyingBar
from .config import _MAX_DECIMAL_DIGITS, _snapshot_hash
from .features import FeatureState


_RETURN_HORIZONS = (1, 5, 15, 30)
_RV_HORIZONS = (5, 15, 30)


def _numeric_snapshot() -> dict[str, object]:
    """Return the complete code-owned NUM80_34_V1 convention dictionary."""
    return {
        "name": "NUM80_34_V1",
        "working_precision": 80,
        "derived_precision": 34,
        "rounding": "ROUND_HALF_EVEN",
        "Emin": -9999,
        "Emax": 9999,
        "capitals": 1,
        "clamp": 0,
        "initial_flags": [],
        "traps": ["InvalidOperation", "DivisionByZero", "Overflow"],
        "unsupported_signals": ["Underflow", "Subnormal", "Clamped"],
        "maximum_fixed_point_digits": _MAX_DECIMAL_DIGITS,
        "inputs": "exact_bounded_coefficients_without_working_rounding",
        "log_ratio": "Decimal.ln(working_divide(numerator,denominator))",
        "unequal_ratio_rounding_to_one": "arithmetic_precision_unsupported",
        "derived_rounding": "once_after_complete_feature",
        "representation": "canonical_fixed_point_no_trailing_zeros_unsigned_zero",
    }


def _transform_snapshot() -> dict[str, object]:
    """Return the explicit ordered close-component definition for hashing."""
    return {
        "record_kind": "options_lab.return_feature_transform",
        "schema_version": 1,
        "numeric_convention": _numeric_snapshot(),
        "symbol": "SPY",
        "price_basis": "raw_completed_minute_close",
        "fields": [
            *[f"log_return_{h}m" for h in _RETURN_HORIZONS],
            *[f"realized_vol_{h}m" for h in _RV_HORIZONS],
        ],
        "return_horizons": list(_RETURN_HORIZONS),
        "return_formula": "ln(C_t/C_(t-h))",
        "rv_horizons": list(_RV_HORIZONS),
        "rv_formula": "sqrt(sum(ln(C_j/C_(j-1))^2))",
        "rv_order": "unrounded_80_digit_1m_logs_squared_summed_oldest_to_newest",
        "units": "dimensionless_natural_log_unannualized_no_mean_subtraction_no_RMS",
        "history": "31_consecutive_same_session_closes_same_source_feed_fidelity_basis",
        "endpoint": "original_decision_exact_completed_minute_end_no_lag",
        "availability": "state_as_of_and_all_dependencies_no_later_than_endpoint",
        "missingness": "all_seven_or_unavailable_no_imputation",
        "admission": "bounded_typed_state_only_no_verified_source_or_context_claim",
    }


NUMERIC_CONVENTION_ID = _snapshot_hash(_numeric_snapshot())
RETURN_TRANSFORM_ID = _snapshot_hash(_transform_snapshot())


@dataclass(frozen=True, init=False)
class ReturnFeatureResult:
    """
    This class represents derived seven-value close-feature evidence.

    Construction consumes only immutable P06 reducer state and the original
    endpoint. Values are four log returns followed by three RVs, or None.
    Retained source claims do not establish verified source admission, quote
    coherence, full-vector readiness, or permission to trade.
    """

    state: FeatureState
    endpoint: datetime
    bars: tuple[UnderlyingBar, ...]
    bar_hashes: tuple[str, ...]
    input_hash: str
    numeric_id: str
    transform_id: str
    values: tuple[Decimal, ...] | None
    reasons: tuple[str, ...]
    available_at: datetime | None

    def __init__(self, state: FeatureState, *, endpoint: datetime) -> None:
        """
        Derive immutable component evidence without caller-supplied outputs.

        :param    state:     Exact causal state produced by the P06 reducer.
        :param    endpoint:  Original aware decision and required bar endpoint.
        :returns:             None.
        :raises   TypeError:  If state or endpoint has the wrong trusted type.
        :raises   ValueError: If endpoint is naive or unrepresentable.
        """
        if type(state) is not FeatureState:
            raise TypeError("state must be a FeatureState")
        endpoint = _trusted_datetime("endpoint", endpoint)
        bars = state.bars[-31:]
        hashes = tuple(identify_underlying_bar(bar).economic_content_hash for bar in bars)
        # P06 only creates states whose selected economic identities are supported.
        reasons = _history_reasons(state, endpoint, bars)
        values = None
        if not reasons:
            try:
                values = _return_values(bars)
            except (DecimalException, _UnsupportedIdentity):
                reasons = ("arithmetic_precision_unsupported",)
        input_hash = _snapshot_hash({
            "record_kind": "options_lab.return_feature_inputs",
            "schema_version": 1,
            "endpoint": endpoint.isoformat(),
            "state_as_of": state.as_of.isoformat(),
            "state_input_hash": state.input_hash,
            "selected_bar_economic_hashes": list(hashes),
        })
        for name, value in (
            ("state", state), ("endpoint", endpoint), ("bars", bars),
            ("bar_hashes", hashes), ("input_hash", input_hash),
            ("numeric_id", NUMERIC_CONVENTION_ID), ("transform_id", RETURN_TRANSFORM_ID),
            ("values", values), ("reasons", reasons),
            ("available_at", None if reasons else max(
                state.session.available_at, *(bar.available_at for bar in bars)
            )),
        ):
            object.__setattr__(self, name, value)

    @property
    def transform_snapshot(self) -> dict[str, object]:
        """
        Return fresh canonical definition evidence underlying the transform ID.

        :returns: An explicit dictionary including the numerical convention.
        """
        return _transform_snapshot()


def calculate_return_features(state: FeatureState, *, endpoint: datetime) -> ReturnFeatureResult:
    """
    Calculate all four raw-close returns and three realized volatilities.

    A complete consecutive 31-close suffix must end at the exact original
    endpoint. A later state cannot be filtered into a fictional older snapshot.
    Volume and VWAP missingness do not invalidate otherwise suitable closes.

    :param    state:     Exact immutable P06 selected-bar state.
    :param    endpoint:  Original aware decision and required bar endpoint.
    :returns:             Derived values or ordered unavailability evidence.
    :raises   TypeError:  If a trusted argument has the wrong exact type.
    :raises   ValueError: If endpoint is naive or unrepresentable.
    """
    return ReturnFeatureResult(state, endpoint=endpoint)


def _history_reasons(
    state: FeatureState, endpoint: datetime, bars: tuple[UnderlyingBar, ...]
) -> tuple[str, ...]:
    """Check the additional suffix and original-cutoff requirements over P06."""
    if state.as_of > endpoint:
        return ("state_after_endpoint",)
    reasons = []
    if not bars or bars[-1].interval_end != endpoint:
        reasons.append("required_bar_endpoint_missing")
    if len(bars) < 31:
        reasons.append("insufficient_close_history")
    if any(left.interval_end != right.interval_start for left, right in zip(bars, bars[1:])):
        reasons.append("close_history_not_consecutive")
    return tuple(reasons)


def _context(precision: int) -> Context:
    """Create a fully specified isolated working or derived-output context."""
    return Context(
        prec=precision, rounding=ROUND_HALF_EVEN,
        Emin=-9999, Emax=9999, capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def _checked(value: Decimal, context: Context) -> Decimal:
    """Reject nonfinite, underflowed or over-budget intermediate representations."""
    if not value.is_finite() or any(context.flags[signal] for signal in (Underflow, Subnormal, Clamped)):
        raise _UnsupportedIdentity
    _identity_decimal(value)
    return value


def _log_ratio(numerator: Decimal, denominator: Decimal, work: Context) -> Decimal:
    """Compute one bounded 80-digit natural log without rounding exact operands."""
    if numerator == denominator:
        return Decimal(0)
    ratio = _checked(work.divide(numerator, denominator), work)
    if ratio == 1 or ratio <= 0:
        raise _UnsupportedIdentity
    return _checked(work.ln(ratio), work)


def _return_values(bars: tuple[UnderlyingBar, ...]) -> tuple[Decimal, ...]:
    """Apply the prescribed operation order and round each feature exactly once."""
    work, output = _context(80), _context(34)
    closes = tuple(Decimal(_identity_decimal(bar.close_price)) for bar in bars)
    values = [_log_ratio(closes[-1], closes[-1 - h], work) for h in _RETURN_HORIZONS]
    minute_returns = tuple(_log_ratio(right, left, work) for left, right in zip(closes, closes[1:]))
    for horizon in _RV_HORIZONS:
        total = Decimal(0)
        for value in minute_returns[-horizon:]:
            squared = _checked(work.multiply(value, value), work)
            total = _checked(work.add(total, squared), work)
        values.append(_checked(work.sqrt(total), work))
    return tuple(
        Decimal(0) if value == 0 else _checked(output.plus(value), output)
        for value in values
    )
