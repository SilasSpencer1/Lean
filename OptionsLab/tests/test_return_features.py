"""Literal numerical and causal tests for the completed-close component."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import Decimal, Inexact, ROUND_DOWN, Rounded, localcontext

import pytest

from options_lab import features
from options_lab.bar_inputs import identify_underlying_bar
from factories import OPEN, bar, session


GOLDEN = tuple(map(Decimal, (
    "0.002306806097915013731464615471169539",
    "0.01158761517238788894107293299073990",
    "0.03517361417826712941881526775822312",
    "0.07162965613925479073308667961437122",
    "0.005182166877311561399250942314051412",
    "0.009082254214374406240937276548535604",
    "0.01308051902088299805000259276580310",
)))
IDENTITY = tuple(map(Decimal, (
    "0.6931471805599453094172321214581766",
    "3.465735902799726547086160607290883",
    "10.39720770839917964125848182187265",
    "20.79441541679835928251696364374530",
    "1.549924214144358454198159029579121",
    "2.684547486779293567206290690104572",
    "3.796523464637884039112866094279591",
)))


def calculate(state, *, endpoint):
    """Exercise the module once the requested component exists."""
    from options_lab import feature_math
    return feature_math.calculate_return_features(state, endpoint=endpoint)


def history(prices=None, *, minutes=None, **changes):
    """Build real reducer state using explicitly available synthetic intervals."""
    if prices is None:
        prices = tuple(Decimal(400 + i) for i in range(35))
    if minutes is None:
        minutes = range(len(prices))
    current = features.FeatureState(session(), as_of=OPEN)
    for minute, price in zip(minutes, prices, strict=True):
        observed = bar(
            minute, close_price=price,
            meta_changes={"available_at": OPEN + timedelta(minutes=minute + 1)},
            **changes,
        )
        update = features.update_features(
            current, observed, current.session, as_of=observed.available_at
        )
        assert update.outcome == "accepted", update.update_reasons
        current = update.next_state
    return current


def test_literal_integration_suffix_uses_all_seven_formulas_and_actual_inputs():
    current = history()
    result = calculate(current, endpoint=current.as_of)
    assert result.values == GOLDEN
    assert result.reasons == ()
    assert result.state is current
    assert result.bars == current.bars[-31:]
    assert result.bar_hashes == tuple(
        identify_underlying_bar(item).economic_content_hash for item in result.bars
    )
    assert result.available_at == OPEN + timedelta(minutes=35)
    assert len(current.bars) == 35
    assert all(type(value) is Decimal for value in result.values)


@pytest.mark.parametrize("prices,expected", [
    (tuple(Decimal(100 * 2**i) for i in range(31)), IDENTITY),
    ((Decimal("100"),) * 31, (Decimal(0),) * 7),
])
def test_literal_geometric_and_constant_identities(prices, expected):
    current = history(prices)
    result = calculate(current, endpoint=current.as_of)
    assert result.values == expected
    if prices[0] == prices[-1]:
        assert tuple(str(value) for value in result.values) == ("0",) * 7


def test_reciprocal_steps_have_zero_endpoint_return_and_positive_realized_vol():
    current = history(tuple(Decimal(100 if i % 2 == 0 else 200) for i in range(31)))
    result = calculate(current, endpoint=current.as_of)
    assert result.values[3] == 0
    assert result.values[4:] == IDENTITY[4:]
    assert result.values[:3] == tuple(value.copy_negate() for value in (IDENTITY[0],) * 3)


@pytest.mark.parametrize("count", [0, 1, 30])
def test_missing_warmup_does_not_emit_partial_values(count):
    current = history((Decimal(100),) * count)
    result = calculate(current, endpoint=OPEN + timedelta(minutes=count))
    assert result.values is None
    assert "insufficient_close_history" in result.reasons


@pytest.mark.parametrize("offset", [timedelta(microseconds=1), timedelta(minutes=1)])
def test_original_endpoint_never_lags_to_latest_complete_window(offset):
    current = history()
    result = calculate(current, endpoint=current.as_of + offset)
    assert result.values is None
    assert result.reasons == ("required_bar_endpoint_missing",)


def test_gap_blocks_required_suffix_but_earlier_gap_does_not_block_recovery():
    gapped = history((Decimal(100),) * 31, minutes=(0, *range(2, 32)))
    failed = calculate(gapped, endpoint=gapped.as_of)
    assert failed.values is None
    assert failed.reasons == ("close_history_not_consecutive",)
    recovered = history((Decimal(100),) * 32, minutes=(0, *range(2, 33)))
    assert calculate(recovered, endpoint=recovered.as_of).values == (Decimal(0),) * 7
    assert len(recovered.bars) == 32


def test_later_revision_cannot_rewrite_original_feature_snapshot():
    original = history()
    before = calculate(original, endpoint=original.as_of)
    selected = original.bars[-1]
    correction = replace(
        selected, close_price=Decimal(435), revision_id="r2",
        supersedes_revision_id="r1", receive_sequence=100,
        meta=replace(selected.meta, available_at=original.as_of + timedelta(microseconds=1)),
    )
    updated = features.update_features(
        original, correction, original.session, as_of=correction.available_at
    )
    assert updated.outcome == "corrected"
    failed = calculate(updated.next_state, endpoint=original.as_of)
    assert failed.values is None
    assert failed.reasons == ("state_after_endpoint",)
    assert failed.input_hash != before.input_hash
    assert before.values == GOLDEN
    assert calculate(original, endpoint=original.as_of) == before


def test_missing_volume_and_vwap_do_not_block_returns():
    current = history(volume=None, volume_definition_id=None, vwap_numerator=None,
                      vwap_denominator=None, vwap_definition_id=None)
    assert calculate(current, endpoint=current.as_of).values == GOLDEN


@pytest.mark.parametrize("precision", [2, 120])
def test_ambient_precision_rounding_exponents_flags_and_traps_do_not_leak(precision):
    current = history()
    expected = calculate(current, endpoint=current.as_of)
    with localcontext() as ambient:
        ambient.prec = precision
        ambient.rounding = ROUND_DOWN
        ambient.Emin, ambient.Emax = -2, 2
        ambient.clamp = 1
        ambient.traps[Inexact] = True
        ambient.traps[Rounded] = True
        ambient.flags[Inexact] = True
        previous = ambient.copy()
        result = calculate(current, endpoint=current.as_of)
        assert result == expected
        assert ambient.flags == previous.flags
        assert ambient.traps == previous.traps


def test_unequal_exact_inputs_collapsing_to_one_are_unavailable():
    prices = (Decimal(1),) * 30 + (Decimal("1." + "0" * 90 + "1"),)
    current = history(prices)
    with localcontext() as ambient:
        ambient.prec = 2
        ambient.traps[Inexact] = True
        result = calculate(current, endpoint=current.as_of)
    assert result.values is None
    assert result.reasons == ("arithmetic_precision_unsupported",)
    assert result.bars[-1].close_price == prices[-1]


@pytest.mark.parametrize("price", [Decimal("1e999"), Decimal("1e-1000")])
def test_supported_fixed_point_extremes_remain_exact(price):
    current = history((price,) * 31)
    result = calculate(current, endpoint=current.as_of)
    assert result.values == (Decimal(0),) * 7


def test_equal_values_with_long_trailing_zeros_preserve_numeric_identity():
    ordinary = history((Decimal(1),) * 31)
    padded = history((Decimal("1." + "0" * 1200),) * 31)
    assert calculate(ordinary, endpoint=ordinary.as_of) == calculate(padded, endpoint=padded.as_of)


def test_result_normalizes_timezone_and_cannot_accept_caller_values_or_hashes():
    from options_lab.feature_math import ReturnFeatureResult
    current = history()
    result = ReturnFeatureResult(current, endpoint=current.as_of)
    local_endpoint = current.as_of.astimezone(timezone(timedelta(hours=-4)))
    assert ReturnFeatureResult(current, endpoint=local_endpoint) == result
    assert result.endpoint.tzinfo is timezone.utc
    with pytest.raises(FrozenInstanceError):
        result.values = ()
    for field, value in (("values", GOLDEN), ("input_hash", "0" * 64), ("reasons", ())):
        with pytest.raises(TypeError):
            ReturnFeatureResult(current, endpoint=current.as_of, **{field: value})
    with pytest.raises(TypeError):
        calculate(None, endpoint=current.as_of)
    with pytest.raises(ValueError):
        calculate(current, endpoint=current.as_of.replace(tzinfo=None))


def test_ratio_intermediate_exceeding_fixed_point_budget_is_unavailable():
    current = history((Decimal("1e999"),) * 30 + (Decimal("1e-1000"),))
    result = calculate(current, endpoint=current.as_of)
    assert result.values is None
    assert result.reasons == ("arithmetic_precision_unsupported",)


@pytest.mark.parametrize("change,reason", [
    ({"close_price": Decimal(0)}, "close_price_nonpositive"),
    ({"close_price": None}, "close_price_missing"),
    ({"price_basis": "split_adjusted"}, "price_basis_not_raw"),
    ({"close_price": Decimal("1e1000")}, "decimal_representation_unsupported"),
    ({"close_price": Decimal("1e-1001")}, "decimal_representation_unsupported"),
    ({"meta_changes": {"source": "other-bars"}}, "price_stream_changed"),
    ({"meta_changes": {"feed_class": "realtime"}}, "price_stream_changed"),
    ({"meta_changes": {"is_fill_forward": True}}, "fill_forward"),
    ({"meta_changes": {"interval_start": OPEN + timedelta(minutes=35, seconds=1)}},
     "interval_duration_not_one_minute"),
    ({"meta_changes": {"interval_start": OPEN - timedelta(days=1),
                       "interval_end": OPEN - timedelta(days=1) + timedelta(minutes=1)}},
     "session_wrong_date"),
])
def test_bad_new_close_cannot_repair_original_endpoint(change, reason):
    current = history()
    endpoint = OPEN + timedelta(minutes=36)
    values = dict(change)
    metadata = {"available_at": endpoint, **values.pop("meta_changes", {})}
    observed = bar(35, meta_changes=metadata, **values)
    update = features.update_features(current, observed, current.session, as_of=endpoint)
    assert update.outcome == "rejected"
    assert reason in update.update_reasons
    assert update.next_state is current
    result = calculate(update.next_state, endpoint=endpoint)
    assert result.values is None
    assert result.reasons == ("required_bar_endpoint_missing",)


def test_one_microsecond_late_publication_does_not_become_available_at_original_endpoint():
    current = history()
    endpoint = OPEN + timedelta(minutes=36)
    observed = bar(35, meta_changes={"available_at": endpoint + timedelta(microseconds=1)})
    update = features.update_features(current, observed, current.session, as_of=endpoint)
    assert update.outcome == "rejected"
    assert "available_after_decision" in update.update_reasons
    assert calculate(update.next_state, endpoint=endpoint).values is None


def test_transform_snapshot_is_complete_fresh_evidence_of_actual_hashes():
    import hashlib
    import json
    current = history()
    result = calculate(current, endpoint=current.as_of)
    snapshot = result.transform_snapshot
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert result.transform_id == hashlib.sha256(encoded).hexdigest()
    numeric = snapshot["numeric_convention"]
    encoded_numeric = json.dumps(numeric, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert result.numeric_id == hashlib.sha256(encoded_numeric).hexdigest()
    assert snapshot["fields"] == [
        "log_return_1m", "log_return_5m", "log_return_15m", "log_return_30m",
        "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    ]
    snapshot["numeric_convention"]["working_precision"] = 2
    assert result.transform_snapshot["numeric_convention"]["working_precision"] == 80
    assert calculate(current, endpoint=current.as_of).transform_id == result.transform_id


def test_same_values_with_changed_revision_or_calendar_have_different_input_hashes():
    current = history()
    original = calculate(current, endpoint=current.as_of)
    alternate = history(revision_id="different-original-revision")
    changed = calculate(alternate, endpoint=alternate.as_of)
    assert changed.values == original.values
    assert changed.bar_hashes != original.bar_hashes
    assert changed.input_hash != original.input_hash
    calendar = session(source_version="synthetic-calendar-v2")
    new_state = features.FeatureState(calendar, as_of=OPEN)
    for observed in current.bars:
        new_state = features.update_features(
            new_state, observed, calendar, as_of=observed.available_at
        ).next_state
    changed = calculate(new_state, endpoint=new_state.as_of)
    assert changed.values == original.values
    assert changed.bar_hashes == original.bar_hashes
    assert changed.input_hash != original.input_hash


def test_trusted_state_and_endpoint_subclasses_do_not_cross_exact_type_boundary():
    from datetime import datetime

    class StateSubclass(features.FeatureState):
        """This class represents an unsupported substitute for trusted state."""

    class DateTimeSubclass(datetime):
        """This class represents an unsupported substitute for an exact instant."""

    with pytest.raises(TypeError):
        calculate(StateSubclass(session(), as_of=OPEN), endpoint=OPEN)
    with pytest.raises(TypeError):
        calculate(history(), endpoint=DateTimeSubclass(2026, 9, 4, 14, 5, tzinfo=timezone.utc))
