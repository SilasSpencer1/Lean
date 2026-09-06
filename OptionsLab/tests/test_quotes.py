from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, Inexact, localcontext

import pytest

import options_lab.quotes as api
from options_lab.contracts import ContractId
from options_lab.observations import ObservationMeta


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def contract(**changes: object) -> ContractId:
    values = {
        "underlying": "SPY",
        "expiry": date(2026, 9, 18),
        "right": "call",
        "strike": Decimal("650"),
        "multiplier": 100,
        "deliverable_id": "standard-spy-100",
    }
    values.update(changes)
    return ContractId(**values)


def meta(**changes: object) -> ObservationMeta:
    values = {
        "source": "sip",
        "provider_record_id": "quote-42",
        "raw_ref": "raw://quote/42",
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": DECISION - timedelta(seconds=5),
        "available_at": DECISION,
        "received_at": DECISION + timedelta(days=1),
        "availability_basis": "measured",
        "availability_evidence_ref": "capture://quote/42",
    }
    values.update(changes)
    return ObservationMeta(**values)


def quote(**changes: object) -> api.QuoteObservation:
    values = {
        "contract": contract(),
        "meta": meta(),
        "bid": Decimal("5.00"),
        "ask": Decimal("5.10"),
        "bid_at": DECISION - timedelta(seconds=5),
        "ask_at": DECISION - timedelta(seconds=5),
        "bid_size": 1,
        "ask_size": 1,
    }
    values.update(changes)
    return api.QuoteObservation(**values)


def assess(observed: api.QuoteObservation | None = None, **changes: object):
    values = {
        "decision_at": DECISION,
        "virtual_equity": Decimal("104200"),
        "available_cash": Decimal("521"),
    }
    values.update(changes)
    return api.assess_quote_premium_budget(
        quote() if observed is None else observed,
        **values,
    )


@pytest.mark.parametrize("right", ["call", "put"])
def test_standard_quote_retains_evidence_and_exact_budget(right) -> None:
    observed = quote(contract=contract(right=right))

    result = assess(observed)

    assert result.quote is observed
    assert result.observation.meta is observed.meta
    assert result.decision_at == DECISION
    assert result.virtual_equity == Decimal("104200")
    assert result.available_cash == Decimal("521")
    assert result.round_trip_fees == Decimal("1")
    assert result.premium_fraction == Decimal("0.005")
    assert result.max_quote_age == timedelta(seconds=5)
    assert result.max_spread_fraction == Decimal("0.08")
    assert result.spread_floor == Decimal("0.05")
    assert result.quote_reasons == ()
    assert result.budget is not None
    assert result.budget.premium == Decimal("510.00")
    assert result.budget.adverse_reserve == Decimal("10.00")
    assert result.budget.required_cash == Decimal("521.00")
    assert result.quote_budget_suitable is True


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"virtual_equity": Decimal("104199.99")}, "premium cap exceeded"),
        ({"available_cash": Decimal("520.99")}, "insufficient available cash"),
        ({"virtual_equity": None}, "virtual equity is undeclared"),
        ({"available_cash": None}, "available cash is unavailable"),
        ({"premium_fraction": Decimal("0.004")}, "premium cap exceeded"),
    ],
)
def test_budget_policy_failures_are_preserved(changes, reason) -> None:
    result = assess(**changes)

    assert result.quote_reasons == ()
    assert result.budget is not None
    assert result.budget.reason == reason
    assert result.quote_budget_suitable is False


def test_higher_fee_and_stricter_fraction_are_forwarded_to_budget_policy() -> None:
    result = assess(
        virtual_equity=Decimal("130500"),
        available_cash=Decimal("522"),
        round_trip_fees=Decimal("2"),
        premium_fraction=Decimal("0.004"),
    )

    assert result.budget is not None
    assert result.budget.fees == Decimal("2")
    assert result.budget.equity_limit == Decimal("522.000")
    assert result.budget.required_cash == Decimal("522.00")
    assert result.quote_budget_suitable is True


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"bid": None}, ("bid_missing",)),
        ({"bid": Decimal("0")}, ("bid_nonpositive",)),
        ({"ask": None}, ("ask_missing",)),
        ({"ask": Decimal("0")}, ("ask_nonpositive",)),
        ({"bid": Decimal("5.10")}, ("quote_locked",)),
        ({"bid": Decimal("5.11")}, ("quote_crossed",)),
    ],
)
def test_unusable_prices_are_retained_without_a_budget(changes, expected) -> None:
    observed = quote(**changes)

    result = assess(observed)

    assert result.quote is observed
    assert result.quote_reasons == expected
    assert result.budget is None
    assert result.quote_budget_suitable is False


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"bid_size": None}, ("bid_size_missing",)),
        ({"bid_size": 0}, ("bid_size_nonpositive",)),
        ({"ask_size": None}, ("ask_size_missing",)),
        ({"ask_size": 0}, ("ask_size_nonpositive",)),
    ],
)
def test_unknown_or_empty_displayed_size_is_retained_without_budget(
    changes, expected
) -> None:
    observed = quote(**changes)

    result = assess(observed)

    assert result.quote is observed
    assert result.quote_reasons == expected
    assert result.budget is None


@pytest.mark.parametrize(
    ("contract_changes", "expected"),
    [
        ({"underlying": "QQQ"}, ("unsupported_underlying",)),
        ({"multiplier": 10}, ("unsupported_multiplier",)),
        (
            {"underlying": "QQQ", "multiplier": 10},
            ("unsupported_underlying", "unsupported_multiplier"),
        ),
    ],
)
def test_local_contract_applicability_is_checked_without_reference_claims(
    contract_changes, expected
) -> None:
    observed = quote(contract=contract(**contract_changes))

    result = assess(observed)

    assert result.quote.contract is observed.contract
    assert result.quote_reasons == expected
    assert result.budget is None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"bid_at": None}, ("bid_time_missing",)),
        ({"bid_at": DECISION + timedelta(microseconds=1)}, ("bid_time_after_decision", "bid_time_after_available")),
        ({"bid_at": DECISION - timedelta(seconds=5, microseconds=1)}, ("bid_too_old",)),
        ({"ask_at": None}, ("ask_time_missing",)),
        ({"ask_at": DECISION + timedelta(microseconds=1)}, ("ask_time_after_decision", "ask_time_after_available")),
        ({"ask_at": DECISION - timedelta(seconds=5, microseconds=1)}, ("ask_too_old",)),
    ],
)
def test_each_side_time_failure_has_a_canonical_reason(changes, expected) -> None:
    result = assess(quote(**changes))

    assert result.quote_reasons == expected
    assert result.budget is None


def test_side_after_declared_availability_fails_even_before_decision() -> None:
    available_at = DECISION - timedelta(seconds=1)
    observed = quote(
        meta=meta(event_at=available_at, available_at=available_at),
        bid_at=available_at + timedelta(microseconds=1),
        ask_at=available_at,
    )

    result = assess(observed)

    assert result.quote_reasons == ("bid_time_after_available",)
    assert result.budget is None


def test_equal_availability_and_utc_equivalent_side_times_pass() -> None:
    eastern = timezone(timedelta(hours=-4))
    observed = quote(
        bid_at=datetime(2026, 9, 5, 10, 30, tzinfo=eastern),
        ask_at=datetime(2026, 9, 5, 10, 30, tzinfo=eastern),
    )

    result = assess(observed, decision_at=datetime(2026, 9, 5, 10, 30, tzinfo=eastern))

    assert observed.bid_at == DECISION
    assert observed.ask_at == DECISION
    assert result.decision_at == DECISION
    assert result.quote_budget_suitable is True


@pytest.mark.parametrize(
    ("meta_changes", "reason"),
    [
        ({"event_at": DECISION - timedelta(seconds=5, microseconds=1)}, "quote_too_old"),
        ({"feed_class": "indicative"}, "feed_not_realtime"),
        ({"feed_class": "delayed"}, "feed_not_realtime"),
        ({"fidelity": "synthetic"}, "fidelity_not_genuine"),
        ({"availability_basis": "assumed"}, "availability_not_measured"),
        ({"is_fill_forward": True}, "fill_forward"),
        ({"quality_flags": ("suspect",)}, "quality_flags_present"),
        (
            {
                "kind": "interval",
                "event_at": None,
                "interval_start": DECISION - timedelta(minutes=1),
                "interval_end": DECISION,
            },
            "not_quote",
        ),
    ],
)
def test_observation_failures_are_recomputed_and_preserved(meta_changes, reason) -> None:
    observed = quote(meta=meta(**meta_changes), bid_at=DECISION, ask_at=DECISION)

    result = assess(observed)

    assert reason in result.observation.live_quote_reasons
    assert result.quote_reasons == ()
    assert result.budget is None
    assert result.quote_budget_suitable is False


def test_fresh_metadata_does_not_refresh_stale_quote_sides() -> None:
    observed = quote(
        meta=meta(event_at=DECISION),
        bid_at=DECISION - timedelta(seconds=5, microseconds=1),
        ask_at=DECISION - timedelta(seconds=5, microseconds=1),
    )

    result = assess(observed)

    assert result.observation.live_quote_time_suitable is True
    assert result.quote_reasons == ("bid_too_old", "ask_too_old")
    assert result.budget is None


@pytest.mark.parametrize(
    ("observed", "expected_observation", "expected_quote"),
    [
        (
            quote(
                meta=meta(event_at=DECISION - timedelta(seconds=4)),
                bid_at=DECISION,
                ask_at=DECISION,
            ),
            (),
            (),
        ),
        (
            quote(
                meta=meta(event_at=DECISION - timedelta(seconds=4, microseconds=1)),
                bid_at=DECISION,
                ask_at=DECISION,
            ),
            ("quote_too_old",),
            (),
        ),
        (
            quote(
                meta=meta(event_at=DECISION),
                bid_at=DECISION - timedelta(seconds=4, microseconds=1),
                ask_at=DECISION,
            ),
            (),
            ("bid_too_old",),
        ),
        (
            quote(
                meta=meta(event_at=DECISION),
                bid_at=DECISION,
                ask_at=DECISION - timedelta(seconds=4, microseconds=1),
            ),
            (),
            ("ask_too_old",),
        ),
    ],
)
def test_tighter_quote_age_applies_to_metadata_and_each_side(
    observed, expected_observation, expected_quote
) -> None:
    result = assess(observed, max_quote_age=timedelta(seconds=4))

    assert result.max_quote_age == timedelta(seconds=4)
    assert result.observation.live_quote_reasons == expected_observation
    assert result.quote_reasons == expected_quote


@pytest.mark.parametrize(
    ("observed", "changes"),
    [
        (
            quote(bid=Decimal("0.93"), ask=Decimal("1.00")),
            {"max_spread_fraction": Decimal("0.06")},
        ),
        (
            quote(bid=Decimal("0.04"), ask=Decimal("0.09")),
            {"spread_floor": Decimal("0.04")},
        ),
    ],
)
def test_tighter_spread_settings_can_reject_default_acceptable_quotes(
    observed, changes
) -> None:
    default = assess(
        observed,
        virtual_equity=Decimal("1000000"),
        available_cash=Decimal("1000000"),
    )
    tighter = assess(
        observed,
        virtual_equity=Decimal("1000000"),
        available_cash=Decimal("1000000"),
        **changes,
    )

    assert default.quote_reasons == ()
    assert tighter.quote_reasons == ("spread_too_wide",)
    assert tighter.max_spread_fraction == changes.get(
        "max_spread_fraction", Decimal("0.08")
    )
    assert tighter.spread_floor == changes.get("spread_floor", Decimal("0.05"))


@pytest.mark.parametrize(
    ("bid", "ask", "suitable"),
    [
        ("0.540", "0.590", True),
        ("0.539999999999", "0.590000000001", False),
        ("0.600", "0.650", True),
        ("0.599999999999", "0.650000000001", False),
        ("0.960", "1.040", True),
        ("0.959999999999", "1.040000000001", False),
        ("0.01", "0.06", True),
    ],
)
def test_spread_limit_uses_exact_floor_and_midpoint_boundaries(
    bid, ask, suitable
) -> None:
    result = assess(
        quote(bid=Decimal(bid), ask=Decimal(ask)),
        virtual_equity=Decimal("1000000"),
        available_cash=Decimal("1000000"),
    )

    assert ("spread_too_wide" not in result.quote_reasons) is suitable
    assert (result.budget is not None) is suitable


def test_decimal_context_does_not_change_results_flags_or_traps() -> None:
    with localcontext() as context:
        context.prec = 3
        context.Emax = 1
        context.Emin = -1
        context.traps[Inexact] = True
        context.flags[Inexact] = True

        result = assess()

        assert context.prec == 3
        assert context.Emax == 1
        assert context.Emin == -1
        assert context.traps[Inexact] is True
        assert context.flags[Inexact] is True

    assert result.quote_budget_suitable is True


@pytest.mark.parametrize(
    "virtual_equity",
    [Decimal("1e941"), Decimal("104200." + "0" * 466)],
)
def test_default_spread_settings_do_not_double_charge_precision_budget(
    virtual_equity,
) -> None:
    result = assess(virtual_equity=virtual_equity)

    assert result.quote_reasons == ()
    assert result.budget is not None
    assert result.budget.required_cash == Decimal("521.00")
    assert result.quote_budget_suitable is True


def test_excessive_finite_arithmetic_range_returns_bounded_reason() -> None:
    observed = quote(
        bid=Decimal("9e999999"),
        ask=Decimal("1e1000000"),
    )

    result = assess(
        observed,
        virtual_equity=Decimal("1e1000010"),
        available_cash=Decimal("1e1000010"),
    )

    assert result.quote is observed
    assert result.quote_reasons == ("arithmetic_precision_unsupported",)
    assert result.budget is None


def test_unrelated_precision_planning_error_is_not_swallowed(monkeypatch) -> None:
    def broken_precision(values):
        raise ValueError("programming defect")

    monkeypatch.setattr(api, "_exact_precision", broken_precision)

    with pytest.raises(ValueError, match="programming defect"):
        assess()


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"contract": "SPY"}, TypeError, "contract must be a ContractId"),
        ({"meta": "meta"}, TypeError, "meta must be an ObservationMeta"),
        ({"bid": 5}, TypeError, "bid must be a Decimal or None"),
        ({"ask": True}, TypeError, "ask must be a Decimal or None"),
        ({"bid": Decimal("NaN")}, ValueError, "bid must be finite"),
        ({"ask": Decimal("Infinity")}, ValueError, "ask must be finite"),
        ({"bid": Decimal("-0.01")}, ValueError, "bid cannot be negative"),
        ({"ask": Decimal("-0.01")}, ValueError, "ask cannot be negative"),
        ({"bid_at": datetime(2026, 9, 5, 14, 30)}, ValueError, "bid_at must be timezone-aware"),
        ({"ask_at": "2026-09-05T14:30:00Z"}, TypeError, "ask_at must be a datetime"),
        ({"bid_size": True}, TypeError, "bid_size must be an integer or None"),
        ({"ask_size": Decimal("1")}, TypeError, "ask_size must be an integer or None"),
        ({"bid_size": -1}, ValueError, "bid_size cannot be negative"),
        ({"ask_size": -1}, ValueError, "ask_size cannot be negative"),
    ],
)
def test_quote_record_rejects_malformed_trusted_values(changes, error, message) -> None:
    with pytest.raises(error, match=message):
        quote(**changes)


def test_exact_nested_types_and_hostile_scalar_are_rejected_without_callbacks() -> None:
    class DerivedContract(ContractId):
        pass

    class DerivedDecimal(Decimal):
        pass

    class DerivedDatetime(datetime):
        pass

    class HostileDecimal:
        def __getattribute__(self, name):
            raise AssertionError("hostile scalar callback")

    derived = DerivedContract(
        "SPY", date(2026, 9, 18), "call", Decimal("650"), 100, "standard"
    )

    with pytest.raises(TypeError, match="contract must be a ContractId"):
        quote(contract=derived)
    with pytest.raises(TypeError, match="ask must be a Decimal or None"):
        quote(ask=DerivedDecimal("5.10"))
    with pytest.raises(TypeError, match="ask_at must be a datetime"):
        quote(ask_at=DerivedDatetime(2026, 9, 5, 14, 30, tzinfo=UTC))
    with pytest.raises(TypeError, match="bid must be a Decimal or None"):
        quote(bid=HostileDecimal())


class CallbackTimezone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("secret")

    def dst(self, dt):
        return timedelta(0)


def test_timezone_callback_failure_is_a_safe_validation_error() -> None:
    bad_time = datetime(2026, 9, 5, tzinfo=CallbackTimezone())

    with pytest.raises(ValueError, match="bid_at timezone evaluation failed") as error:
        quote(bid_at=bad_time)

    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"decision_at": datetime(2026, 9, 5, 14, 30)}, ValueError, "decision_at must be timezone-aware"),
        ({"virtual_equity": 1000}, TypeError, "virtual_equity must be a Decimal or None"),
        ({"available_cash": False}, TypeError, "available_cash must be a Decimal or None"),
        ({"round_trip_fees": 1}, TypeError, "round_trip_fees must be a Decimal"),
        ({"premium_fraction": 0.005}, TypeError, "premium_fraction must be a Decimal"),
        ({"virtual_equity": Decimal("NaN")}, ValueError, "virtual_equity must be finite"),
        ({"available_cash": Decimal("Infinity")}, ValueError, "available_cash must be finite"),
        ({"round_trip_fees": Decimal("-0.01")}, ValueError, "round_trip_fees cannot be negative"),
        ({"premium_fraction": Decimal("0")}, ValueError, "premium_fraction must be greater than zero"),
        ({"premium_fraction": Decimal("0.0051")}, ValueError, "premium_fraction cannot exceed 0.005"),
        ({"max_quote_age": 5}, TypeError, "max_quote_age must be a timedelta"),
        ({"max_quote_age": timedelta(0)}, ValueError, "max_quote_age must be positive"),
        ({"max_quote_age": timedelta(seconds=5, microseconds=1)}, ValueError, "max_quote_age cannot exceed five seconds"),
        ({"max_spread_fraction": 0.08}, TypeError, "max_spread_fraction must be a Decimal"),
        ({"max_spread_fraction": Decimal("0")}, ValueError, "max_spread_fraction must be greater than zero"),
        ({"max_spread_fraction": Decimal("0.081")}, ValueError, "max_spread_fraction cannot exceed 0.08"),
        ({"spread_floor": Decimal("NaN")}, ValueError, "spread_floor must be finite"),
        ({"spread_floor": Decimal("0")}, ValueError, "spread_floor must be greater than zero"),
        ({"spread_floor": Decimal("0.051")}, ValueError, "spread_floor cannot exceed 0.05"),
    ],
)
def test_trusted_assessment_misuse_raises_even_when_quote_is_bad(
    changes, error, message
) -> None:
    bad_quote = quote(bid=None)

    with pytest.raises(error, match=message):
        assess(bad_quote, **changes)


def test_derived_evidence_cannot_be_supplied_and_replace_recomputes_it() -> None:
    first = assess()
    bad_quote = replace(first.quote, ask=first.quote.bid)
    second = replace(first, quote=bad_quote)

    assert first.quote_budget_suitable is True
    assert second.quote_reasons == ("quote_locked",)
    assert second.budget is None
    with pytest.raises(FrozenInstanceError):
        first.quote.bid = Decimal("4")
    with pytest.raises(FrozenInstanceError):
        first.budget = None
    with pytest.raises(TypeError):
        api.QuotePremiumAssessment(
            first.quote,
            first.decision_at,
            first.virtual_equity,
            first.available_cash,
            first.round_trip_fees,
            first.premium_fraction,
            quote_reasons=(),
        )
