"""Causal, literal and precision regressions for distinct session VWAP modes."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import Decimal as D, Inexact, Rounded, ROUND_UP, localcontext
import hashlib
import json

import pytest

from options_lab.features import FeatureState, update_features
from options_lab.feature_math import calculate_return_features
from options_lab.observations import ObservationMeta
from options_lab.quote_content import identify_quote_content
from factories import OPEN, bar, session
from options_lab.underlying import UnderlyingQuote


EXACT = "exact_trade_dollars"
PROXY = "close_volume_proxy"
END = OPEN + timedelta(minutes=35)


def history(*, minutes=range(35), changes=None, **common):
    """Build actual reducer history with synthetic available-at-end intervals."""
    state = FeatureState(session(), as_of=OPEN)
    for minute in minutes:
        values = dict(close_price=D(400 + minute), volume=D(1100),
                      vwap_numerator=D(1100 * (400 + minute) - 550),
                      vwap_denominator=D(1100))
        values.update(common)
        values.update((changes or {}).get(minute, {}))
        observed = bar(minute, meta_changes={"available_at": OPEN + timedelta(minutes=minute + 1)}, **values)
        update = update_features(state, observed, state.session, as_of=observed.available_at)
        assert update.outcome == "accepted", update.update_reasons
        state = update.next_state
    return state


def quote(*, endpoint=END, **changes):
    """Supply a separate explicitly synthetic current underlying quote."""
    at = endpoint - timedelta(seconds=2)
    meta = ObservationMeta(
        source="synthetic-underlying", provider_record_id="spot-1",
        raw_ref="synthetic://spot-1", feed_class="delayed", fidelity="synthetic",
        kind="quote", event_at=at, available_at=endpoint, received_at=endpoint,
        availability_basis="assumed", availability_evidence_ref="fixture-clock",
    )
    values = dict(symbol="SPY", meta=meta, bid=D("433.99"), ask=D("434.01"),
                  bid_at=at, ask_at=at, bid_size=1, ask_size=1)
    values.update(changes)
    return UnderlyingQuote(**values)


def calculate(state, observed=None, *, endpoint=None, mode=EXACT):
    """Exercise the concrete component through its planned public function."""
    from options_lab import vwap_features
    endpoint = state.as_of if endpoint is None else endpoint
    observed = quote(endpoint=endpoint) if observed is None else observed
    return vwap_features.calculate_vwap_feature(state, observed, endpoint=endpoint, mode=mode)


@pytest.mark.parametrize("mode,numerator,vwap,distance", [
    (EXACT, "16035250", "416.5", "0.04115807249350747940874120614240408"),
    (PROXY, "16054500", "417", "0.03995831230160341186771903364564402"),
])
def test_literal_totals_distances_and_actual_retained_input_evidence(mode, numerator, vwap, distance):
    state, observed = history(), quote()
    result = calculate(state, observed, mode=mode)
    assert (result.numerator, result.denominator, result.vwap, result.midpoint) == (
        D(numerator), D(38500), D(vwap), D(434))
    assert result.distance == D(distance)
    assert result.reasons == ()
    assert result.state is state and result.bars is state.bars
    assert result.underlying_quote is observed
    assert result.underlying_quote_hash == identify_quote_content(observed).content_hash
    assert result.available_at == END
    assert len(result.bar_hashes) == len(state.bars) == 35


def test_exact_ignores_ordinary_volume_and_proxy_ignores_exact_contributions():
    exact = calculate(history(volume=None, volume_definition_id=None))
    proxy_state = history(vwap_numerator=None, vwap_denominator=None, vwap_definition_id=None)
    assert exact.distance == D("0.04115807249350747940874120614240408")
    assert calculate(proxy_state, mode=PROXY).distance == D("0.03995831230160341186771903364564402")
    assert calculate(proxy_state).distance is None
    assert calculate_return_features(proxy_state, endpoint=END).values is not None
    assert all(item.vwap_numerator is None for item in proxy_state.bars)


@pytest.mark.parametrize("mode,change,reason", [
    (EXACT, {"vwap_numerator": None}, "vwap_numerator_missing"),
    (EXACT, {"vwap_denominator": None}, "vwap_denominator_missing"),
    (EXACT, {"vwap_numerator": D(1), "vwap_denominator": D(0)}, "vwap_numerator_without_volume"),
    (EXACT, {"vwap_numerator": D(0), "vwap_denominator": D(1)}, "vwap_numerator_nonpositive"),
    (EXACT, {"vwap_definition_id": None}, "vwap_definition_unknown"),
    (EXACT, {"vwap_definition_id": "other-positive-pair"}, "vwap_definition_unsupported"),
    (PROXY, {"volume": None}, "volume_missing"),
    (PROXY, {"volume_definition_id": None}, "volume_definition_unknown"),
    (PROXY, {"volume_definition_id": "another-feed-volume"}, "volume_definition_unsupported"),
])
def test_missing_invalid_or_mixed_source_contributions_fail_without_harming_closes(mode, change, reason):
    state = history(changes={5: change})
    result = calculate(state, mode=mode)
    assert result.distance is None and reason in result.reasons
    assert calculate_return_features(state, endpoint=END).values is not None


@pytest.mark.parametrize("mode", [EXACT, PROXY])
def test_known_zero_contribution_is_valid_but_all_zero_denominator_is_unavailable(mode):
    zero = dict(volume=D("-0"), vwap_numerator=D("0.000"), vwap_denominator=D(0))
    one = dict(close_price=D(400), volume=D(2), vwap_numerator=D(800), vwap_denominator=D(2))
    state = history(minutes=range(2), changes={0: zero, 1: one})
    result = calculate(state, mode=mode)
    assert (result.numerator, result.denominator, result.vwap) == (D(800), D(2), D(400))
    assert result.reasons == ()
    empty = calculate(history(minutes=range(2), **zero), mode=mode)
    assert (empty.numerator, empty.denominator) == (D(0), D(0))
    assert empty.distance is None and empty.reasons == ("vwap_denominator_zero",)


@pytest.mark.parametrize("minutes,reason", [
    (range(1, 35), "session_open_bar_missing"),
    ((0, *range(2, 35)), "session_history_not_consecutive"),
])
def test_missing_full_prefix_blocks_vwap_after_return_suffix_recovers(minutes, reason):
    state = history(minutes=minutes)
    assert calculate_return_features(state, endpoint=END).values is not None
    for mode in (EXACT, PROXY):
        result = calculate(state, mode=mode)
        assert result.distance is None and reason in result.reasons


@pytest.mark.parametrize("offset", [timedelta(microseconds=1), timedelta(minutes=1)])
def test_required_endpoint_cannot_lag_or_round(offset):
    result = calculate(history(), endpoint=END + offset)
    assert result.distance is None
    assert result.reasons == ("required_bar_endpoint_missing",)


def test_empty_history_and_later_correction_never_backdate_an_original_result():
    assert calculate(history(minutes=())).distance is None
    state = history()
    original = calculate(state)
    selected = state.bars[-1]
    revised = replace(selected, revision_id="r2", supersedes_revision_id="r1",
                      vwap_numerator=D(500000), receive_sequence=100,
                      meta=replace(selected.meta, available_at=END + timedelta(microseconds=1)))
    rejected = update_features(state, revised, state.session, as_of=END)
    assert rejected.outcome == "rejected"
    updated = update_features(state, revised, state.session, as_of=revised.available_at)
    assert updated.outcome == "corrected"
    result = calculate(updated.next_state, endpoint=END)
    assert result.distance is None and "state_after_endpoint" in result.reasons
    assert result.input_hash != original.input_hash
    assert calculate(state) == original


def test_current_midpoint_is_independent_of_last_close_and_locked_quote_is_observed():
    state = history(minutes=range(1), close_price=D(999), vwap_numerator=D(200), vwap_denominator=D(2))
    result = calculate(state, quote(endpoint=state.as_of, bid=D(200), ask=D(200)))
    assert result.midpoint == D(200) and result.vwap == D(100)
    assert result.distance == D("0.6931471805599453094172321214581766")


@pytest.mark.parametrize("change,reason", [
    ({"bid": None}, "underlying_bid_missing"),
    ({"ask": None}, "underlying_ask_missing"),
    ({"bid": D(0)}, "underlying_bid_nonpositive"),
    ({"ask": D(0)}, "underlying_ask_nonpositive"),
    ({"bid": D(435)}, "underlying_quote_crossed"),
    ({"symbol": "QQQ"}, "underlying_symbol_unsupported"),
    ({"bid_at": END + timedelta(microseconds=1)}, "underlying_bid_after_endpoint"),
    ({"ask_at": END + timedelta(microseconds=1)}, "underlying_ask_after_endpoint"),
])
def test_bounded_underlying_market_and_future_side_facts_fail(change, reason):
    result = calculate(history(), quote(**change))
    assert result.distance is None and reason in result.reasons


def test_future_availability_event_and_known_side_after_availability_fail():
    observed = quote()
    for metadata, sides, reason in (
        ({"available_at": END + timedelta(microseconds=1)}, {}, "underlying_available_after_endpoint"),
        ({"event_at": END + timedelta(microseconds=1)}, {}, "underlying_event_after_endpoint"),
        ({"available_at": END - timedelta(seconds=3)}, {}, "underlying_bid_after_available"),
    ):
        changed = replace(observed, meta=replace(observed.meta, **metadata), **sides)
        result = calculate(history(), changed)
        assert result.distance is None and reason in result.reasons


def test_component_does_not_claim_five_second_freshness_or_context_admission():
    observed = quote(bid_at=None, ask_at=OPEN, bid_size=None, ask_size=0)
    observed = replace(observed, meta=replace(observed.meta, event_at=OPEN))
    assert calculate(history(), observed).distance is not None


@pytest.mark.parametrize("precision", [2, 120])
@pytest.mark.parametrize("mode", [EXACT, PROXY])
def test_ambient_precision_rounding_exponents_flags_and_traps_do_not_change_result(precision, mode):
    state = history()
    expected = calculate(state, mode=mode)
    with localcontext() as ambient:
        ambient.prec, ambient.rounding = precision, ROUND_UP
        ambient.Emin, ambient.Emax, ambient.clamp = -2, 2, 1
        ambient.traps[Inexact] = ambient.traps[Rounded] = True
        ambient.flags[Inexact] = True
        flags = ambient.flags.copy()
        assert calculate(state, mode=mode) == expected
        assert ambient.flags == flags


def test_exact_totals_above_working_precision_are_retained_before_vwap_division():
    amount = D("1" + "0" * 89 + "1")
    state = history(minutes=range(2), vwap_numerator=amount, vwap_denominator=D(1))
    result = calculate(state, quote(endpoint=state.as_of, bid=D("2e90"), ask=D("2e90")))
    assert result.numerator == D("2" + "0" * 89 + "2")
    assert result.distance == D("0.6931471805599453094172321214581766")


def test_proxy_products_are_exact_above_working_precision():
    price, volume = D("1" + "0" * 49 + "1"), D("1" + "0" * 39 + "1")
    state = history(minutes=range(1), close_price=price, volume=volume)
    result = calculate(state, quote(endpoint=state.as_of, bid=price, ask=price), mode=PROXY)
    assert result.numerator == D(10**90 + 10**50 + 10**40 + 1)
    assert result.vwap == price and result.distance == D(0)


def test_vwap_rounded_to_spot_does_not_manufacture_zero_distance():
    state = history(minutes=range(1), vwap_numerator=D("1." + "0" * 90 + "1"), vwap_denominator=D(1))
    result = calculate(state, quote(endpoint=state.as_of, bid=D(1), ask=D(1)))
    assert result.distance is None and result.reasons == ("arithmetic_precision_unsupported",)
    assert result.numerator == D("1." + "0" * 90 + "1")
    assert (result.denominator, result.midpoint, result.vwap) == (D(1), D(1), D(1))


def test_prescribed_working_vwap_before_spot_ratio_differs_from_algebraic_rewrite():
    spot = D("1.16666666666666666666666666666666666666666666666666666666666666667666666666666666666666666666666666666666666666666666666666666666666666666666666666667")
    state = history(minutes=range(1), vwap_numerator=D(7), vwap_denominator=D(6))
    result = calculate(state, quote(endpoint=state.as_of, bid=spot, ask=spot))
    assert result.vwap == D("1.1666666666666666666666666666666666666666666666666666666666666666666666666666667")
    assert result.distance == D("8.571428571428500000000000000000000E-66")
    # Independent direct S*D/N at 80 digits instead gives 8.5714285714286E-66.
    assert result.distance != D("8.571428571428600000000000000000000E-66")


def test_distinct_spot_and_working_vwap_cannot_collapse_ratio_to_one():
    state = history(minutes=range(1), vwap_numerator=D(1), vwap_denominator=D(1))
    spot = D("1." + "0" * 90 + "1")
    result = calculate(state, quote(endpoint=state.as_of, bid=spot, ask=spot))
    assert result.distance is None and result.reasons == ("arithmetic_precision_unsupported",)
    assert result.midpoint == spot and result.vwap == D(1)


@pytest.mark.parametrize("mode,changes", [
    (EXACT, {"vwap_numerator": D("9e999"), "vwap_denominator": D(1)}),
    (PROXY, {"close_price": D("1e600"), "volume": D("1e600")}),
    (EXACT, {"vwap_numerator": D("1e-1000"), "vwap_denominator": D("1e999")}),
])
def test_sum_product_and_ratio_over_representation_bound_are_owned_failures(mode, changes):
    result = calculate(history(minutes=range(2), **changes), mode=mode)
    assert result.distance is None and result.reasons == ("arithmetic_precision_unsupported",)


def test_aggregate_rounding_is_rejected_without_publishing_partial_session_totals():
    state = history(minutes=range(2), changes={
        0: {"vwap_numerator": D("1e999")},
        1: {"vwap_numerator": D("1e-1000")},
    })
    result = calculate(state)
    assert result.numerator is None and result.denominator is None
    assert result.reasons == ("arithmetic_precision_unsupported",)


def test_midpoint_bound_failure_retains_completed_totals_and_original_quote():
    state = history(minutes=range(1))
    observed = quote(endpoint=state.as_of, bid=D("9e999"), ask=D("9e999"))
    result = calculate(state, observed)
    assert (result.numerator, result.denominator) == (D(439450), D(1100))
    assert result.midpoint is None and result.distance is None
    assert result.underlying_quote is observed
    assert result.reasons == ("arithmetic_precision_unsupported",)


def test_source_definitions_are_required_even_when_all_unknown_pairs_are_positive():
    state = history(vwap_definition_id="external-exact-claim", volume_definition_id="external-volume")
    assert calculate(state).reasons == ("vwap_definition_unsupported",)
    assert calculate(state, mode=PROXY).reasons == ("volume_definition_unsupported",)


def test_non_quote_metadata_and_event_after_known_availability_are_bounded_failures():
    observed = quote()
    state = history()
    changed = replace(observed, meta=replace(observed.meta, kind="interval"))
    assert "underlying_not_quote" in calculate(state, changed).reasons
    changed = replace(observed, meta=replace(observed.meta, event_at=END, available_at=END - timedelta(seconds=1)))
    assert "underlying_event_after_available" in calculate(state, changed).reasons


def test_quote_identity_bound_failure_retains_quote_without_a_partial_input_hash():
    observed = quote(bid=D("1e1000"), ask=D("1e1000"))
    result = calculate(history(), observed)
    assert result.underlying_quote is observed
    assert result.underlying_quote_hash is None and result.input_hash is None
    assert result.distance is None and "decimal_representation_unsupported" in result.reasons


def test_equal_values_still_have_distinct_feature_transform_and_fidelity_identities():
    state = history(minutes=range(1), close_price=D(100), volume=D(2), vwap_numerator=D(200), vwap_denominator=D(2))
    exact, proxy = calculate(state), calculate(state, mode=PROXY)
    assert exact.distance == proxy.distance
    assert exact.feature_name == "session_vwap_log_distance"
    assert proxy.feature_name == "session_close_volume_proxy_log_distance"
    assert exact.transform_id != proxy.transform_id and exact.fidelity != proxy.fidelity
    assert exact.input_hash != proxy.input_hash
    for result in (exact, proxy):
        snapshot = result.transform_snapshot
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert result.transform_id == hashlib.sha256(encoded).hexdigest()
        snapshot["numeric_convention"]["working_precision"] = 2
        assert result.transform_snapshot["numeric_convention"]["working_precision"] == 80


def test_identity_binds_quote_revision_calendar_and_normalizes_decimal_spelling():
    state, observed = history(), quote()
    result = calculate(state, observed)
    changed = calculate(state, replace(observed, meta=replace(observed.meta, provider_record_id="spot-2")))
    assert changed.distance == result.distance and changed.input_hash != result.input_hash
    assert calculate(state, replace(observed, bid=D("433.9900"))) == result
    other = history(revision_id="different-revision")
    assert calculate(other).input_hash != result.input_hash


def test_constructor_derives_immutable_evidence_and_enforces_exact_trusted_types():
    from options_lab.vwap_features import VwapFeatureResult
    state = history()
    result = calculate(state)
    with pytest.raises(FrozenInstanceError):
        result.distance = D(0)
    for field, value in (("distance", D(0)), ("input_hash", "0" * 64), ("reasons", ())):
        with pytest.raises(TypeError):
            VwapFeatureResult(state, quote(), endpoint=END, mode=EXACT, **{field: value})
    for mode in (None, True, "fallback", 0):
        with pytest.raises((TypeError, ValueError)):
            calculate(state, mode=mode)
    with pytest.raises(TypeError):
        VwapFeatureResult(None, quote(), endpoint=END, mode=EXACT)
    with pytest.raises(TypeError):
        VwapFeatureResult(state, None, endpoint=END, mode=EXACT)
    with pytest.raises(ValueError):
        calculate(state, endpoint=END.replace(tzinfo=None))
    local = END.astimezone(timezone(timedelta(hours=-4)))
    assert calculate(state, quote(), endpoint=local) == result
