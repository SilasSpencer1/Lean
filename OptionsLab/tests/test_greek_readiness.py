from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext

import pytest

import options_lab.greeks as api
from options_lab.contracts import ContractId
from options_lab.observations import ObservationMeta
from options_lab.quote_content import identify_quote_content
from options_lab.quotes import QuoteObservation
from options_lab.underlying import UnderlyingQuote


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)
AS_OF = DECISION - timedelta(seconds=2)


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


def meta(record_id: str, **changes: object) -> ObservationMeta:
    values = {
        "source": "fixture-sip",
        "provider_record_id": record_id,
        "raw_ref": f"raw://{record_id}",
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": AS_OF,
        "available_at": AS_OF + timedelta(seconds=1),
        "received_at": DECISION,
        "availability_basis": "measured",
        "availability_evidence_ref": f"fixture://capture/{record_id}",
    }
    values.update(changes)
    return ObservationMeta(**values)


def option_quote(**changes: object) -> QuoteObservation:
    values = {
        "contract": contract(),
        "meta": meta("option-1"),
        "bid": Decimal("5.00"),
        "ask": Decimal("5.10"),
        "bid_at": AS_OF,
        "ask_at": AS_OF,
        "bid_size": 7,
        "ask_size": 9,
    }
    values.update(changes)
    return QuoteObservation(**values)


def underlying_quote(**changes: object) -> UnderlyingQuote:
    values = {
        "symbol": "SPY",
        "meta": meta("underlying-1"),
        "bid": Decimal("650.00"),
        "ask": Decimal("650.02"),
        "bid_at": AS_OF,
        "ask_at": AS_OF,
        "bid_size": 700,
        "ask_size": 900,
    }
    values.update(changes)
    return UnderlyingQuote(**values)


def inputs(
    option: QuoteObservation | None = None,
    underlying: UnderlyingQuote | None = None,
    **changes: object,
) -> api.GreekInputs:
    option = option_quote() if option is None else option
    underlying = underlying_quote() if underlying is None else underlying
    values = {
        "option_quote_hash": identify_quote_content(option).content_hash,
        "underlying_quote_hash": identify_quote_content(underlying).content_hash,
        "rate": Decimal("-0.01"),
        "dividend_yield": Decimal("0.0125"),
        "rate_unit": "continuous_annual_fraction",
        "dividend_unit": "continuous_annual_fraction",
        "assumptions_id": "fixture-flat-rates-v1",
    }
    values.update(changes)
    return api.GreekInputs(**values)


def greek(
    option: QuoteObservation | None = None,
    underlying: UnderlyingQuote | None = None,
    **changes: object,
) -> api.GreekObservation:
    option = option_quote() if option is None else option
    underlying = underlying_quote() if underlying is None else underlying
    value_inputs = changes.pop("inputs", inputs(option, underlying))
    as_of = changes.pop("as_of", AS_OF)
    method = api.FIXTURE_GREEK_METHOD
    if "input_hash" in changes:
        value_hash = changes.pop("input_hash")
    elif value_inputs is None or as_of is None:
        value_hash = None
    else:
        value_hash = api.greek_input_hash(
            value_inputs, contract=option.contract, method=method, as_of=as_of
        )
    values = {
        "contract": option.contract,
        "delta": Decimal("0.50"),
        "iv": Decimal("0.20"),
        "as_of": as_of,
        "available_at": AS_OF + timedelta(seconds=1),
        "delta_unit": method.delta_unit,
        "iv_unit": method.iv_unit,
        "method_id": method.method_id,
        "method_version": method.method_version,
        "inputs": value_inputs,
        "input_hash": value_hash,
        "source": "fixture-greeks",
        "provider_record_id": "greek-1",
        "availability_basis": "measured",
        "received_at": DECISION,
        "raw_ref": "raw://greek-1",
    }
    values.update(changes)
    return api.GreekObservation(**values)


def assess(
    observed: api.GreekObservation | None = None,
    option: QuoteObservation | None = None,
    underlying: UnderlyingQuote | None = None,
    **changes: object,
) -> api.GreekReadiness:
    option = option_quote() if option is None else option
    underlying = underlying_quote() if underlying is None else underlying
    if observed is None and "missing" not in changes:
        observed = greek(option, underlying)
    changes.pop("missing", None)
    return api.assess_greek_readiness(
        observed,
        option,
        underlying,
        method=changes.pop("method", api.FIXTURE_GREEK_METHOD),
        decision_at=changes.pop("decision_at", DECISION),
        **changes,
    )


def test_ready_retains_inputs_and_derived_identities() -> None:
    option = option_quote()
    underlying = underlying_quote()
    observed = greek(option, underlying)

    result = assess(observed, option, underlying)

    assert result.greek is observed
    assert result.option_quote is option
    assert result.underlying_quote is underlying
    assert result.method is api.FIXTURE_GREEK_METHOD
    assert result.decision_at == DECISION
    assert result.status == "READY"
    assert result.reasons == ()
    assert result.option_quote_identity.content_hash == observed.inputs.option_quote_hash
    assert result.underlying_quote_identity.content_hash == observed.inputs.underlying_quote_hash
    assert result.observed_input_hash == observed.input_hash
    assert result.expected_input_hash == observed.input_hash
    assert result.expected_method_identity == (
        "fixture-greek-values",
        "1",
        "fixture-flat-rates-v1",
    )
    assert result.ready is True


def test_changed_quote_update_identity_invalidates_claim_and_expected_aggregate() -> None:
    option = option_quote()
    underlying = underlying_quote()
    observed = greek(option, underlying)
    changed = option_quote(meta=meta("option-2"))

    result = assess(observed, changed, underlying)

    assert result.status == "INCOMPATIBLE"
    assert "option_quote_hash_mismatch" in result.reasons
    assert "input_hash_mismatch" in result.reasons
    assert result.option_quote_identity.content_hash != observed.inputs.option_quote_hash
    assert result.expected_input_hash != result.observed_input_hash


@pytest.mark.parametrize(
    ("observed", "expected_status", "reason"),
    [
        (None, "MISSING", "greek_missing"),
        (greek(delta=None), "MISSING", "delta_missing"),
        (greek(iv=None), "MISSING", "iv_missing"),
        (greek(as_of=None, input_hash=None), "MISSING", "as_of_missing"),
        (greek(available_at=None), "MISSING", "available_at_missing"),
        (greek(inputs=None, input_hash=None), "MISSING", "inputs_missing"),
        (greek(input_hash=None), "MISSING", "input_hash_missing"),
        (greek(delta=Decimal("1.0001")), "INCOMPATIBLE", "delta_out_of_range"),
        (greek(iv=Decimal("0")), "INCOMPATIBLE", "iv_nonpositive"),
        (greek(method_id="other"), "INCOMPATIBLE", "method_id_mismatch"),
        (greek(delta_unit="percent"), "INCOMPATIBLE", "delta_unit_mismatch"),
        (
            greek(availability_basis="assumed"),
            "INCOMPATIBLE",
            "availability_not_measured",
        ),
    ],
)
def test_status_matrix_preserves_primary_classification(observed, expected_status, reason) -> None:
    result = assess(observed, missing=observed is None)
    assert result.status == expected_status
    assert reason in result.reasons
    assert result.ready is (expected_status == "READY")


def test_incompatible_precedes_missing_and_stale_while_retaining_every_reason() -> None:
    observed = greek(
        delta=Decimal("2"),
        iv=None,
        as_of=DECISION - timedelta(seconds=6),
        input_hash=None,
    )
    result = assess(observed)
    assert result.status == "INCOMPATIBLE"
    assert "delta_out_of_range" in result.reasons
    assert "iv_missing" in result.reasons
    assert "input_hash_missing" in result.reasons
    assert "greek_too_old" in result.reasons


def test_age_boundary_uses_greek_as_of_and_fresh_receipts_do_not_refresh_it() -> None:
    exact_time = DECISION - timedelta(seconds=5)
    exact_option = option_quote(
        meta=meta("option-1", event_at=exact_time),
        bid_at=exact_time,
        ask_at=exact_time,
    )
    exact_underlying = underlying_quote(
        meta=meta("underlying-1", event_at=exact_time),
        bid_at=exact_time,
        ask_at=exact_time,
    )
    exact = greek(exact_option, exact_underlying, as_of=exact_time)
    stale_time = exact_time - timedelta(microseconds=1)
    stale_option = option_quote(
        meta=meta("option-1", event_at=stale_time),
        bid_at=stale_time,
        ask_at=stale_time,
    )
    stale_underlying = underlying_quote(
        meta=meta("underlying-1", event_at=stale_time),
        bid_at=stale_time,
        ask_at=stale_time,
    )
    stale = greek(stale_option, stale_underlying, as_of=stale_time)

    assert assess(exact, exact_option, exact_underlying).status == "READY"
    result = assess(stale, stale_option, stale_underlying)
    assert result.status == "STALE"
    assert result.greek.received_at == DECISION
    assert result.option_quote.meta.received_at == DECISION
    assert result.underlying_quote.meta.received_at == DECISION


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"as_of": DECISION + timedelta(microseconds=1)}, "as_of_after_decision"),
        ({"available_at": DECISION + timedelta(microseconds=1)}, "available_after_decision"),
        ({"available_at": AS_OF - timedelta(microseconds=1)}, "as_of_after_available"),
    ],
)
def test_future_and_reversed_greek_times_are_incompatible(changes, reason) -> None:
    assert reason in assess(greek(**changes)).reasons


def test_quote_publication_after_valuation_is_allowed_but_source_events_are_not() -> None:
    published_later = AS_OF + timedelta(seconds=1)
    option = option_quote(meta=meta("option-1", available_at=published_later))
    underlying = underlying_quote(meta=meta("underlying-1", available_at=published_later))
    assert assess(greek(option, underlying), option, underlying).status == "READY"

    future_event = option_quote(
        meta=meta("option-1", event_at=AS_OF + timedelta(microseconds=1)),
        bid_at=AS_OF + timedelta(microseconds=1),
    )
    observed = greek(future_event, underlying)
    result = assess(observed, future_event, underlying)
    assert "option_event_after_greek_as_of" in result.reasons
    assert "option_bid_after_greek_as_of" in result.reasons
    assert result.status == "INCOMPATIBLE"


@pytest.mark.parametrize(
    ("option_changes", "underlying_changes", "status", "reason"),
    [
        ({"bid": Decimal("5.10")}, {}, "INCOMPATIBLE", "option_quote_locked"),
        ({"bid_size": None}, {}, "MISSING", "option_bid_size_missing"),
        (
            {"bid_at": DECISION - timedelta(seconds=5, microseconds=1)},
            {},
            "STALE",
            "option_bid_too_old",
        ),
        ({}, {"bid": Decimal("650.03")}, "INCOMPATIBLE", "underlying_quote_crossed"),
        ({}, {"ask_size": None}, "MISSING", "underlying_ask_size_missing"),
    ],
)
def test_current_owner_quote_failures_keep_their_classification(
    option_changes, underlying_changes, status, reason
) -> None:
    option = option_quote(**option_changes)
    underlying = underlying_quote(**underlying_changes)
    result = assess(greek(option, underlying), option, underlying)
    assert result.status == status
    assert reason in result.reasons


def test_underlying_lock_passes_while_option_lock_fails() -> None:
    locked_underlying = underlying_quote(ask=Decimal("650.00"))
    assert assess(
        greek(underlying=locked_underlying), underlying=locked_underlying
    ).status == "READY"

    locked_option = option_quote(bid=Decimal("5.10"))
    result = assess(greek(locked_option), locked_option)
    assert result.status == "INCOMPATIBLE"
    assert "option_quote_locked" in result.reasons


def test_physical_bounds_do_not_apply_candidate_sign_band_or_iv_cap() -> None:
    for delta, iv in ((Decimal("-1"), Decimal("20")), (Decimal("1"), Decimal("0.0001"))):
        assert assess(greek(delta=delta, iv=iv)).status == "READY"


def test_missing_assumptions_are_missing_and_never_replaced_with_zero() -> None:
    value_inputs = inputs(rate=None, dividend_yield=None)
    observed = greek(inputs=value_inputs)
    result = assess(observed)
    assert result.status == "MISSING"
    assert "rate_missing" in result.reasons
    assert "dividend_yield_missing" in result.reasons
    assert result.greek.inputs.rate is None
    assert result.greek.inputs.dividend_yield is None


def test_extreme_input_identity_fails_closed_without_losing_observation() -> None:
    value_inputs = inputs(rate=Decimal("1E+1000"))
    observed = greek(inputs=value_inputs, input_hash="0" * 64)
    result = assess(observed)
    assert result.status == "INCOMPATIBLE"
    assert "input_identity_unsupported" in result.reasons
    assert result.expected_input_hash is None
    assert result.greek is observed


def test_hashing_and_readiness_ignore_ambient_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        before = (context.prec, context.flags.copy(), context.traps.copy())
        result = assess()
        after = (context.prec, context.flags.copy(), context.traps.copy())
    assert result.status == "READY"
    assert after == before


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"contract": "SPY"}, TypeError),
        ({"delta": 0.5}, TypeError),
        ({"iv": Decimal("Infinity")}, ValueError),
        ({"inputs": object()}, TypeError),
        ({"availability_basis": "unknown"}, ValueError),
        ({"received_at": datetime(2026, 9, 5)}, ValueError),
    ],
)
def test_trusted_observation_rejects_inexact_or_malformed_values(changes, error) -> None:
    with pytest.raises(error):
        greek(**changes)


def test_records_are_frozen_and_derived_readiness_cannot_be_injected() -> None:
    observed = greek()
    result = assess(observed)
    with pytest.raises(FrozenInstanceError):
        observed.delta = Decimal("0")
    with pytest.raises(FrozenInstanceError):
        result.status = "MISSING"
    with pytest.raises(TypeError):
        api.GreekReadiness(
            observed,
            option_quote(),
            underlying_quote(),
            api.FIXTURE_GREEK_METHOD,
            DECISION,
            status="READY",
        )


def test_assessment_rejects_nonexact_trusted_arguments() -> None:
    with pytest.raises(TypeError, match="greek must be a GreekObservation or None"):
        assess(object())
    with pytest.raises(TypeError, match="method must be a GreekMethodSpec"):
        assess(method=object())
    with pytest.raises(ValueError, match="decision_at must be timezone-aware"):
        assess(decision_at=datetime(2026, 9, 5, 14, 30))
