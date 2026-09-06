from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, Inexact, localcontext

import pytest

import options_lab.quotes as option_api
import options_lab.underlying as api
from options_lab.contracts import ContractId
from options_lab.observations import ObservationMeta


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def meta(**changes: object) -> ObservationMeta:
    values = {
        "source": "synthetic-sip-fixture",
        "provider_record_id": "underlying-42",
        "raw_ref": "raw://underlying/42",
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": DECISION - timedelta(seconds=5),
        "available_at": DECISION,
        "received_at": DECISION + timedelta(days=1),
        "availability_basis": "measured",
        "availability_evidence_ref": "synthetic://capture/underlying/42",
    }
    values.update(changes)
    return ObservationMeta(**values)


def quote(**changes: object) -> api.UnderlyingQuote:
    values = {
        "symbol": "SPY",
        "meta": meta(),
        "bid": Decimal("100.00"),
        "ask": Decimal("100.02"),
        "bid_at": DECISION - timedelta(seconds=5),
        "ask_at": DECISION - timedelta(seconds=5),
        "bid_size": 1,
        "ask_size": 2,
    }
    values.update(changes)
    return api.UnderlyingQuote(**values)


def assess(observed: api.UnderlyingQuote | None = None, **changes: object):
    values = {"decision_at": DECISION}
    values.update(changes)
    return api.assess_underlying_quote(quote() if observed is None else observed, **values)


def test_normal_spy_quote_retains_share_evidence_and_derives_exact_values() -> None:
    observed = quote()
    result = assess(observed)

    assert result.quote is observed
    assert result.observation.meta is observed.meta
    assert result.decision_at == DECISION
    assert result.max_quote_age == timedelta(seconds=5)
    assert result.quote_reasons == ()
    assert result.midpoint == Decimal("100.01")
    assert result.spread == Decimal("0.02")
    assert result.underlying_quote_suitable is True
    assert observed.meta.source == "synthetic-sip-fixture"
    assert (observed.bid_size, observed.ask_size) == (1, 2)


@pytest.mark.parametrize(
    ("changes", "reasons", "midpoint", "spread"),
    [
        ({"symbol": "AAPL"}, ("unsupported_symbol",), "100.01", "0.02"),
        ({"symbol": "spy"}, ("unsupported_symbol",), "100.01", "0.02"),
        ({"bid": None}, ("bid_missing",), None, None),
        ({"bid": Decimal("0")}, ("bid_nonpositive",), None, None),
        ({"ask": None}, ("ask_missing",), None, None),
        ({"ask": Decimal("0")}, ("ask_nonpositive",), None, None),
        ({"bid": Decimal("100.03")}, ("quote_crossed",), None, None),
        ({"ask": Decimal("100.00")}, (), "100.00", "0.00"),
    ],
)
def test_adverse_price_and_symbol_facts_are_retained(
    changes, reasons, midpoint, spread
) -> None:
    observed = quote(**changes)
    result = assess(observed)

    assert result.quote is observed
    assert result.quote_reasons == reasons
    assert result.midpoint == (None if midpoint is None else Decimal(midpoint))
    assert result.spread == (None if spread is None else Decimal(spread))
    assert result.underlying_quote_suitable is (not reasons)


def test_underlying_lock_is_observed_while_option_lock_remains_unsuitable() -> None:
    locked = assess(quote(ask=Decimal("100.00")))
    option_quote = option_api.QuoteObservation(
        ContractId("SPY", date(2026, 9, 18), "call", Decimal("650"), 100, "standard"),
        meta(), Decimal("5"), Decimal("5"), DECISION, DECISION, 1, 1,
    )
    option = option_api.assess_quote_premium_budget(
        option_quote,
        decision_at=DECISION,
        virtual_equity=Decimal("1000000"),
        available_cash=Decimal("1000000"),
    )

    assert locked.spread == Decimal("0")
    assert locked.underlying_quote_suitable is True
    assert option.quote_reasons == ("quote_locked",)
    assert option.quote_budget_suitable is False


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"bid_size": None}, ("bid_size_missing",)),
        ({"bid_size": 0}, ("bid_size_nonpositive",)),
        ({"ask_size": None}, ("ask_size_missing",)),
        ({"ask_size": 0}, ("ask_size_nonpositive",)),
    ],
)
def test_unknown_or_empty_displayed_share_size_is_unsuitable(changes, expected) -> None:
    result = assess(quote(**changes))
    assert result.quote_reasons == expected
    assert result.midpoint == Decimal("100.01")
    assert result.spread == Decimal("0.02")
    assert result.underlying_quote_suitable is False


@pytest.mark.parametrize("side", ["bid", "ask"])
@pytest.mark.parametrize(
    ("offset", "max_age", "available_offset", "suffix"),
    [
        (timedelta(seconds=-5), timedelta(seconds=5), timedelta(0), None),
        (timedelta(seconds=-5, microseconds=-1), timedelta(seconds=5), timedelta(0), "too_old"),
        (timedelta(seconds=-4), timedelta(seconds=4), timedelta(0), None),
        (timedelta(seconds=-4, microseconds=-1), timedelta(seconds=4), timedelta(0), "too_old"),
        (timedelta(microseconds=1), timedelta(seconds=5), timedelta(0), "time_after_decision"),
        (timedelta(seconds=-1), timedelta(seconds=5), timedelta(seconds=-2), "time_after_available"),
    ],
)
def test_each_side_has_independent_inclusive_time_bounds(
    side, offset, max_age, available_offset, suffix
) -> None:
    side_at = DECISION + offset
    other_side_at = DECISION + min(available_offset, timedelta(0))
    side_values = {"bid_at": other_side_at, "ask_at": other_side_at}
    side_values[f"{side}_at"] = side_at
    observed = quote(
        meta=meta(event_at=DECISION, available_at=DECISION + available_offset),
        **side_values,
    )
    result = assess(observed, max_quote_age=max_age)

    expected = () if suffix is None else (f"{side}_{suffix}",)
    if offset > timedelta(0):
        expected += (f"{side}_time_after_available",)
    assert result.quote_reasons == expected


@pytest.mark.parametrize("side", ["bid", "ask"])
def test_missing_side_time_is_never_repaired_from_other_timestamps(side) -> None:
    result = assess(quote(**{f"{side}_at": None}))
    assert result.quote_reasons == (f"{side}_time_missing",)


def test_offset_equivalent_side_times_normalize_to_utc() -> None:
    eastern = timezone(timedelta(hours=-4))
    observed = quote(
        bid_at=datetime(2026, 9, 5, 10, 30, tzinfo=eastern),
        ask_at=datetime(2026, 9, 5, 10, 30, tzinfo=eastern),
    )
    result = assess(observed, decision_at=datetime(2026, 9, 5, 10, 30, tzinfo=eastern))
    assert observed.bid_at == observed.ask_at == result.decision_at == DECISION
    assert result.underlying_quote_suitable is True


@pytest.mark.parametrize(
    ("meta_changes", "reason"),
    [
        ({"event_at": DECISION - timedelta(seconds=5, microseconds=1)}, "quote_too_old"),
        ({"event_at": None}, "event_time_missing"),
        ({"event_at": DECISION + timedelta(microseconds=1)}, "event_after_decision"),
        ({"available_at": DECISION + timedelta(microseconds=1)}, "available_after_decision"),
        ({"feed_class": "indicative"}, "feed_not_realtime"),
        ({"feed_class": "delayed"}, "feed_not_realtime"),
        ({"fidelity": "synthetic"}, "fidelity_not_genuine"),
        ({"availability_basis": "assumed"}, "availability_not_measured"),
        ({"is_fill_forward": True}, "fill_forward"),
        ({"quality_flags": ("suspect",)}, "quality_flags_present"),
    ],
)
def test_metadata_evidence_is_recomputed_without_admission_claims(meta_changes, reason) -> None:
    result = assess(quote(meta=meta(**meta_changes), bid_at=DECISION, ask_at=DECISION))
    assert reason in result.observation.live_quote_reasons
    assert result.quote_reasons == ()
    assert result.underlying_quote_suitable is False


def test_tighter_age_reaches_metadata_at_its_inclusive_boundary() -> None:
    exact = assess(quote(meta=meta(event_at=DECISION - timedelta(seconds=4))), max_quote_age=timedelta(seconds=4))
    stale = assess(quote(meta=meta(event_at=DECISION - timedelta(seconds=4, microseconds=1))), max_quote_age=timedelta(seconds=4))
    assert exact.observation.live_quote_reasons == ()
    assert stale.observation.live_quote_reasons == ("quote_too_old",)


def test_stock_spread_has_no_option_cap_or_contract_multiplier() -> None:
    result = assess(quote(bid=Decimal("100"), ask=Decimal("120")))
    assert result.midpoint == Decimal("110")
    assert result.spread == Decimal("20")
    assert result.quote_reasons == ()
    assert result.underlying_quote_suitable is True
    for unsupported_claim in ("coherent", "economic_ready", "entry_ready"):
        assert not hasattr(result, unsupported_claim)


def test_decimal_context_cannot_round_or_trap_exact_derived_values() -> None:
    with localcontext() as context:
        context.prec = 3
        context.Emax = 1
        context.Emin = -1
        context.traps[Inexact] = True
        context.flags[Inexact] = True
        result = assess(quote(bid=Decimal("123456789.123456789"), ask=Decimal("123456789.123456791")))
        assert (context.prec, context.Emax, context.Emin) == (3, 1, -1)
        assert context.traps[Inexact] is True and context.flags[Inexact] is True
    assert result.midpoint == Decimal("123456789.123456790")
    assert result.spread == Decimal("0.000000002")


def test_excessive_finite_precision_fails_closed_with_retained_inputs() -> None:
    observed = quote(bid=Decimal("1e-1000"), ask=Decimal("2e-1000"))
    result = assess(observed)
    assert result.quote is observed
    assert result.quote_reasons == ("arithmetic_precision_unsupported",)
    assert result.midpoint is None and result.spread is None
    assert result.underlying_quote_suitable is False


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"symbol": 1}, TypeError), ({"symbol": ""}, ValueError),
        ({"meta": object()}, TypeError), ({"bid": 1}, TypeError),
        ({"ask": Decimal("NaN")}, ValueError), ({"bid": Decimal("-1")}, ValueError),
        ({"bid_at": "now"}, TypeError), ({"ask_at": datetime(2026, 9, 5)}, ValueError),
        ({"bid_size": True}, TypeError), ({"ask_size": Decimal("1")}, TypeError),
        ({"bid_size": -1}, ValueError),
    ],
)
def test_typed_quote_rejects_malformed_trusted_values(changes, error) -> None:
    with pytest.raises(error):
        quote(**changes)


class CallbackTimezone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("secret")

    def dst(self, dt):
        return timedelta(0)


def test_hostile_timezone_failure_is_safe() -> None:
    with pytest.raises(ValueError, match="bid_at timezone evaluation failed") as caught:
        quote(bid_at=datetime(2026, 9, 5, tzinfo=CallbackTimezone()))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"quote": object()}, TypeError),
        ({"decision_at": datetime(2026, 9, 5)}, ValueError),
        ({"max_quote_age": 5}, TypeError),
        ({"max_quote_age": timedelta(0)}, ValueError),
        ({"max_quote_age": timedelta(seconds=5, microseconds=1)}, ValueError),
    ],
)
def test_assessment_rejects_trusted_misuse(changes, error) -> None:
    observed = changes.pop("quote", quote())
    with pytest.raises(error):
        assess(observed, **changes)


def test_assessment_is_immutable_and_replace_recomputes_derived_evidence() -> None:
    first = assess()
    second = replace(first, quote=replace(first.quote, ask=first.quote.bid))
    assert first.spread == Decimal("0.02")
    assert second.spread == Decimal("0")
    with pytest.raises(FrozenInstanceError):
        first.spread = Decimal("9")
    with pytest.raises(TypeError):
        api.UnderlyingQuoteAssessment(first.quote, first.decision_at, quote_reasons=())
