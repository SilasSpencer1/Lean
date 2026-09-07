"""Exact supplied session VWAP and explicitly separate close-volume proxy."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException, Inexact
from typing import Literal

from ._validation import _trusted_datetime
from .bar_inputs import _identity_decimal, _UnsupportedIdentity, identify_underlying_bar
from .bars import UnderlyingBar, assess_underlying_bar
from .config import _MAX_DECIMAL_DIGITS, _snapshot_hash
from .feature_math import NUMERIC_CONVENTION_ID, _checked, _context, _log_ratio, _numeric_snapshot
from .features import FeatureState
from .quote_content import identify_quote_content
from .underlying import UnderlyingQuote


VwapMode = Literal["exact_trade_dollars", "close_volume_proxy"]
EXACT_VWAP_DEFINITION_ID = "synthetic-vwap-v1"
ELIGIBLE_VOLUME_DEFINITION_ID = "synthetic-volume-v1"
_MODES = ("exact_trade_dollars", "close_volume_proxy")


def _transform_snapshot(mode: VwapMode) -> dict[str, object]:
    """Return the fixed synthetic definition and complete arithmetic contract."""
    exact = mode == "exact_trade_dollars"
    return {
        "record_kind": "options_lab.vwap_feature_transform",
        "schema_version": 1,
        "mode": mode,
        "feature_name": "session_vwap_log_distance" if exact else "session_close_volume_proxy_log_distance",
        "fidelity": "exact_supplied_trade_contributions" if exact else "close_volume_proxy",
        "source_definition": {
            "id": EXACT_VWAP_DEFINITION_ID if exact else ELIGIBLE_VOLUME_DEFINITION_ID,
            "scope": "fixed_synthetic_fixture_contract_no_external_source_authority_from_id",
            "eligibility": "fixture_declared_eligible_trades" if exact else "fixture_declared_eligible_ordinary_share_volume",
            "numerator": "supplied_sum_eligible_raw_trade_price_USD_times_shares" if exact else "raw_minute_close_USD_times_eligible_volume_shares",
            "denominator": "matching_supplied_eligible_trade_shares" if exact else "eligible_ordinary_volume_shares",
            "ordinary_volume_equals_exact_denominator_required": False,
        },
        "numeric_convention": _numeric_snapshot(),
        "exact_arithmetic": "bounded_1000_digit_context_Inexact_trap_checked_canonical_operands_and_intermediates",
        "symbol": "SPY",
        "current_price": "exact_current_underlying_bid_plus_ask_divided_by_two",
        "bar_price_basis": "raw",
        "history": "full_session_open_through_endpoint_consecutive_minutes_same_source_feed_fidelity_basis",
        "endpoint": "original_decision_exact_completed_minute_end_no_lag",
        "availability": "state_as_of_session_bars_and_underlying_no_later_than_endpoint",
        "numerator": "sum_supplied_trade_dollars" if exact else "sum_exact_close_times_eligible_volume",
        "denominator": "sum_supplied_matching_trade_volume" if exact else "sum_eligible_volume",
        "operation_order": "exact_totals_then_N_div_D_80_then_S_div_V_80_then_Decimal_ln_80_then_round_34",
        "zero_distance": "requires_exact_S_times_D_equals_N",
        "units": "dimensionless_natural_log_current_midpoint_over_session_vwap",
        "missingness": "unavailable_no_imputation_no_mode_fallback_known_zero_contribution_valid",
        "admission": "typed_component_only_no_verified_source_freshness_joint_coherence_or_vector_readiness_claim",
    }


EXACT_VWAP_TRANSFORM_ID = _snapshot_hash(_transform_snapshot("exact_trade_dollars"))
CLOSE_VOLUME_PROXY_TRANSFORM_ID = _snapshot_hash(_transform_snapshot("close_volume_proxy"))


@dataclass(frozen=True, init=False)
class VwapFeatureResult:
    """
    This class represents one derived full-session VWAP distance assessment.

    Named exact totals, working VWAP and midpoint retain reached arithmetic
    evidence; distance is available only when all component checks pass.
    Mode fidelity describes the calculation, never verified source origin or
    trading authority. The retained state and quote preserve their own origin.
    """

    state: FeatureState
    endpoint: datetime
    bars: tuple[UnderlyingBar, ...]
    bar_hashes: tuple[str, ...]
    underlying_quote: UnderlyingQuote
    underlying_quote_hash: str | None
    input_hash: str | None
    numeric_id: str
    transform_id: str
    mode: VwapMode
    feature_name: str
    fidelity: str
    numerator: Decimal | None
    denominator: Decimal | None
    vwap: Decimal | None
    midpoint: Decimal | None
    distance: Decimal | None
    reasons: tuple[str, ...]
    available_at: datetime | None

    def __init__(
        self, state: FeatureState, underlying_quote: UnderlyingQuote, *,
        endpoint: datetime, mode: VwapMode,
    ) -> None:
        """
        Derive immutable evidence from exact typed original-cutoff inputs.

        :param    state:             Causal P06 state with selected raw closes.
        :param    underlying_quote:  Actual current underlying quote input.
        :param    endpoint:          Original aware decision and required bar end.
        :param    mode:              Exact contributions or explicit close proxy.
        :returns:                     None.
        :raises   TypeError:         If an input has the wrong exact trusted type.
        :raises   ValueError:        If endpoint or the fixed mode is invalid.
        """
        if type(state) is not FeatureState:
            raise TypeError("state must be a FeatureState")
        if type(underlying_quote) is not UnderlyingQuote:
            raise TypeError("underlying_quote must be an UnderlyingQuote")
        endpoint = _trusted_datetime("endpoint", endpoint)
        if type(mode) is not str:
            raise TypeError("mode must be an exact string")
        if mode not in _MODES:
            raise ValueError("mode must be exact_trade_dollars or close_volume_proxy")
        bars = state.bars
        hashes = tuple(identify_underlying_bar(bar).economic_content_hash for bar in bars)
        quote_identity = identify_quote_content(underlying_quote)
        snapshot = _transform_snapshot(mode)
        transform_id = _snapshot_hash(snapshot)
        reasons = tuple(dict.fromkeys((
            *_history_reasons(state, endpoint),
            *_contribution_reasons(state, endpoint, mode),
            *_quote_reasons(underlying_quote, endpoint),
            *quote_identity.reasons,
        )))
        numerator = denominator = vwap = midpoint = distance = None
        if not reasons:
            numerator, denominator, midpoint, vwap, distance, reasons = _calculate(bars, underlying_quote, mode)
        input_hash = None if quote_identity.content_hash is None else _snapshot_hash({
            "record_kind": "options_lab.vwap_feature_inputs",
            "schema_version": 1,
            "endpoint": endpoint.isoformat(),
            "state_as_of": state.as_of.isoformat(),
            "state_input_hash": state.input_hash,
            "selected_bar_economic_hashes": list(hashes),
            "underlying_quote_hash": quote_identity.content_hash,
            "transform_id": transform_id,
        })
        for name, value in (
            ("state", state), ("endpoint", endpoint), ("bars", bars), ("bar_hashes", hashes),
            ("underlying_quote", underlying_quote), ("underlying_quote_hash", quote_identity.content_hash),
            ("input_hash", input_hash), ("numeric_id", NUMERIC_CONVENTION_ID),
            ("transform_id", transform_id), ("mode", mode),
            ("feature_name", snapshot["feature_name"]), ("fidelity", snapshot["fidelity"]),
            ("numerator", numerator), ("denominator", denominator), ("vwap", vwap),
            ("midpoint", midpoint), ("distance", distance), ("reasons", reasons),
            ("available_at", None if reasons else max(
                state.session.available_at, underlying_quote.meta.available_at,
                *(bar.available_at for bar in bars),
            )),
        ):
            object.__setattr__(self, name, value)

    @property
    def transform_snapshot(self) -> dict[str, object]:
        """
        Return fresh explicit definition evidence underlying the transform hash.

        :returns: A new dictionary containing source and arithmetic semantics.
        """
        return _transform_snapshot(self.mode)


def calculate_vwap_feature(
    state: FeatureState, underlying_quote: UnderlyingQuote, *,
    endpoint: datetime, mode: VwapMode,
) -> VwapFeatureResult:
    """
    Calculate a full-session exact VWAP or explicit close-volume proxy distance.

    Ordinary volume and supplied exact contributions remain independent. This
    component does not establish source admission, freshness, joint coherence,
    full-vector readiness, or permission to trade.

    :param    state:             Exact immutable P06 raw-close history.
    :param    underlying_quote:  Current typed underlying quote to use for spot.
    :param    endpoint:          Original aware decision and required minute end.
    :param    mode:              One of the two fixed calculation definitions.
    :returns:                     Named arithmetic and ordered failure evidence.
    :raises   TypeError:         If a trusted input has the wrong exact type.
    :raises   ValueError:        If endpoint or the fixed mode is invalid.
    """
    return VwapFeatureResult(state, underlying_quote, endpoint=endpoint, mode=mode)


def _history_reasons(state: FeatureState, endpoint: datetime) -> tuple[str, ...]:
    """Check the full prefix and cutoff on already-proven P06 selected bars."""
    if state.as_of > endpoint:
        return ("state_after_endpoint",)
    bars, reasons = state.bars, []
    if not bars or bars[-1].interval_end != endpoint:
        reasons.append("required_bar_endpoint_missing")
    if not bars or bars[0].interval_start != state.session.opens_at:
        reasons.append("session_open_bar_missing")
    if any(left.interval_end != right.interval_start for left, right in zip(bars, bars[1:])):
        reasons.append("session_history_not_consecutive")
    return tuple(reasons)


def _contribution_reasons(state: FeatureState, endpoint: datetime, mode: VwapMode) -> tuple[str, ...]:
    """Check only the selected definition's independent per-minute evidence."""
    reasons = []
    for bar in state.bars:
        assessment = assess_underlying_bar(bar, state.session, as_of=endpoint)
        if mode == "exact_trade_dollars":
            reasons.extend(assessment.vwap_reasons)
            if bar.vwap_definition_id is not None and bar.vwap_definition_id != EXACT_VWAP_DEFINITION_ID:
                reasons.append("vwap_definition_unsupported")
        else:
            reasons.extend(assessment.volume_reasons)
            if bar.volume_definition_id is not None and bar.volume_definition_id != ELIGIBLE_VOLUME_DEFINITION_ID:
                reasons.append("volume_definition_unsupported")
    return tuple(reasons)


def _quote_reasons(quote: UnderlyingQuote, endpoint: datetime) -> tuple[str, ...]:
    """Check positive observed sides and known temporal facts, without freshness."""
    reasons = []
    if quote.symbol != "SPY":
        reasons.append("underlying_symbol_unsupported")
    if quote.meta.kind != "quote":
        reasons.append("underlying_not_quote")
    if quote.meta.available_at > endpoint:
        reasons.append("underlying_available_after_endpoint")
    for name, at in (("event", quote.meta.event_at), ("bid", quote.bid_at), ("ask", quote.ask_at)):
        if at is not None:
            if at > endpoint:
                reasons.append(f"underlying_{name}_after_endpoint")
            if at > quote.meta.available_at:
                reasons.append(f"underlying_{name}_after_available")
    for side in ("bid", "ask"):
        value = getattr(quote, side)
        if value is None:
            reasons.append(f"underlying_{side}_missing")
        elif value <= 0:
            reasons.append(f"underlying_{side}_nonpositive")
    if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
        reasons.append("underlying_quote_crossed")
    return tuple(reasons)


def _calculate(bars: tuple[UnderlyingBar, ...], quote: UnderlyingQuote, mode: VwapMode):
    """Use exact bounded totals and midpoint, then the fixed 80/34 operation order."""
    exact, work, output = _context(_MAX_DECIMAL_DIGITS), _context(80), _context(34)
    exact.traps[Inexact] = True
    numerator = denominator = midpoint = vwap = None
    try:
        total_n, total_d = Decimal(0), Decimal(0)
        for bar in bars:
            if mode == "exact_trade_dollars":
                n = Decimal(_identity_decimal(bar.vwap_numerator))
                d = Decimal(_identity_decimal(bar.vwap_denominator))
            else:
                close = Decimal(_identity_decimal(bar.close_price))
                d = Decimal(_identity_decimal(bar.volume))
                n = _checked(exact.multiply(close, d), exact)
            total_n = _checked(exact.add(total_n, n), exact)
            total_d = _checked(exact.add(total_d, d), exact)
        numerator, denominator = total_n, total_d
        sides = _checked(exact.add(Decimal(_identity_decimal(quote.bid)), Decimal(_identity_decimal(quote.ask))), exact)
        midpoint = _checked(exact.divide(sides, Decimal(2)), exact)
        if denominator == 0:
            return numerator, denominator, midpoint, None, None, ("vwap_denominator_zero",)
        vwap = _checked(work.divide(numerator, denominator), work)
        distance = _checked(output.plus(_log_ratio(midpoint, vwap, work)), output)
        if distance == 0:
            if _checked(exact.multiply(midpoint, denominator), exact) != numerator:
                raise _UnsupportedIdentity
            distance = Decimal(0)
    except (DecimalException, _UnsupportedIdentity):
        return numerator, denominator, midpoint, vwap, None, ("arithmetic_precision_unsupported",)
    return numerator, denominator, midpoint, vwap, distance, ()
