from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from options_lab.config import StrategyConfig
from options_lab.contracts import ContractId
from options_lab.sessions import (
    ExchangeSession,
    InstrumentTradability,
    SessionAssessment,
    assess_session,
)


UTC = timezone.utc
NY = ZoneInfo("America/New_York")
SESSION_DATE = date(2026, 9, 4)
NOW = datetime(2026, 9, 4, 14, tzinfo=UTC)


def contract(**changes: object) -> ContractId:
    """Return synthetic-factory-v1 option identity evidence for P04A tests."""
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


def session(**changes: object) -> ExchangeSession:
    """Return synthetic-calendar-factory-v1 evidence, never source admission."""
    values = {
        "calendar": "XNYS",
        "session_date": SESSION_DATE,
        "kind": "regular",
        "opens_at": datetime(2026, 9, 4, 13, 30, tzinfo=UTC),
        "closes_at": datetime(2026, 9, 4, 20, tzinfo=UTC),
        "source": "synthetic-calendar",
        "provider_record_id": "synthetic-session-2026-09-04",
        "source_version": "synthetic-calendar-v1",
        "available_at": datetime(2026, 9, 4, 13, tzinfo=UTC),
        "availability_basis": "measured",
        "received_at": datetime(2026, 9, 4, 13, 1, tzinfo=UTC),
        "raw_ref": "synthetic://calendar/2026-09-04",
        "fidelity": "synthetic",
    }
    values.update(changes)
    return ExchangeSession(**values)


def tradability(**changes: object) -> InstrumentTradability:
    """Return synthetic-instrument-factory-v1 evidence, never source admission."""
    values = {
        "contract": contract(),
        "instrument_ref": "synthetic-option-2026-09-18-C-650",
        "session_date": SESSION_DATE,
        "opens_at": datetime(2026, 9, 4, 13, 30, tzinfo=UTC),
        "closes_at": datetime(2026, 9, 4, 20, 15, tzinfo=UTC),
        "status": "tradable",
        "effective_from": datetime(2026, 9, 4, 13, 30, tzinfo=UTC),
        "effective_until": datetime(2026, 9, 4, 20, 15, tzinfo=UTC),
        "source": "synthetic-instrument",
        "provider_record_id": "synthetic-instrument-2026-09-04",
        "source_version": "synthetic-instrument-v1",
        "available_at": datetime(2026, 9, 4, 13, tzinfo=UTC),
        "availability_basis": "measured",
        "received_at": datetime(2026, 9, 4, 13, 2, tzinfo=UTC),
        "raw_ref": "synthetic://instrument/2026-09-04",
        "fidelity": "synthetic",
    }
    values.update(changes)
    return InstrumentTradability(**values)


def assess(at: datetime = NOW, **changes: object) -> SessionAssessment:
    """Assess synthetic-factory-v1 session evidence at an explicit time."""
    values = {
        "session": session(),
        "tradability": tradability(),
        "config": StrategyConfig(),
        "now": at,
    }
    values.update(changes)
    return assess_session(**values)


def test_regular_session_derives_common_hours_policy_and_actual_config_hash() -> None:
    result = assess()

    assert result.session_reasons == ()
    assert result.instrument_hours_reasons == ()
    assert result.operability_reasons == ()
    assert result.entry_reasons == ()
    assert result.entry_timing_suitable
    assert result.common_opens_at == datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
    assert result.common_closes_at == datetime(2026, 9, 4, 20, tzinfo=UTC)
    assert result.configured_entry_start == datetime(2026, 9, 4, 14, tzinfo=UTC)
    assert result.configured_entry_end == datetime(2026, 9, 4, 19, tzinfo=UTC)
    assert result.effective_liquidation_start == datetime(2026, 9, 4, 19, 35, tzinfo=UTC)
    assert result.effective_liquidation_escalation == datetime(2026, 9, 4, 19, 39, tzinfo=UTC)
    assert result.effective_liquidation_deadline == datetime(2026, 9, 4, 19, 40, tzinfo=UTC)
    assert result.recovery_basis == "common_hours"
    assert result.config_hash == "8ef0583fa6e6ec352140691e78b38172f1801b56be2e22f02423300b241542a8"
    assert not result.liquidation_due
    assert not result.escalation_due
    assert not result.liquidation_deadline_reached


def test_records_and_assessment_are_frozen_and_normalize_offsets_to_utc() -> None:
    opened = datetime(2026, 9, 4, 9, 30, tzinfo=NY)
    calendar = session(opens_at=opened)
    instrument = tradability(opens_at=opened)
    result = assess(session=calendar, tradability=instrument)
    offset_result = assess_session(
        calendar, instrument, config=result.config,
        now=datetime(2026, 9, 4, 10, tzinfo=NY),
    )

    assert calendar.opens_at == instrument.opens_at == datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
    assert calendar.source_version == "synthetic-calendar-v1"
    assert instrument.instrument_ref == "synthetic-option-2026-09-18-C-650"
    assert result.session is calendar and result.tradability is instrument
    assert result.config is not None and result.now == NOW
    assert offset_result == result
    with pytest.raises(FrozenInstanceError):
        calendar.kind = "closed"
    with pytest.raises(FrozenInstanceError):
        result.entry_reasons = ()


@pytest.mark.parametrize(
    ("at", "reason"),
    [
        (datetime(2026, 9, 4, 13, 30, tzinfo=UTC), "outside_configured_entry_window"),
        (datetime(2026, 9, 4, 13, 59, 59, tzinfo=UTC), "outside_configured_entry_window"),
        (datetime(2026, 9, 4, 19, 0, 0, 1, tzinfo=UTC), "outside_configured_entry_window"),
        (datetime(2026, 9, 4, 20, tzinfo=UTC), "now_outside_common_interval"),
    ],
)
def test_entry_boundaries_fail_for_the_specific_bounded_reason(at, reason) -> None:
    result = assess(at)
    assert reason in result.entry_reasons
    if at == datetime(2026, 9, 4, 13, 30, tzinfo=UTC):
        assert "now_outside_common_interval" not in result.entry_reasons
    assert not result.entry_timing_suitable


@pytest.mark.parametrize("at", [NOW, datetime(2026, 9, 4, 19, tzinfo=UTC)])
def test_configured_entry_window_is_inclusive(at) -> None:
    assert assess(at).entry_timing_suitable


def test_early_close_blocks_entry_and_derives_conservative_recovery_clocks() -> None:
    day = date(2026, 11, 27)
    calendar = session(
        session_date=day,
        kind="early_close",
        opens_at=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        closes_at=datetime(2026, 11, 27, 18, tzinfo=UTC),
        available_at=datetime(2026, 11, 27, 14, tzinfo=UTC),
    )
    instrument = tradability(
        session_date=day,
        opens_at=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        closes_at=datetime(2026, 11, 27, 18, tzinfo=UTC),
        effective_from=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        effective_until=datetime(2026, 11, 27, 18, tzinfo=UTC),
        available_at=datetime(2026, 11, 27, 14, tzinfo=UTC),
    )
    result = assess_session(
        calendar, instrument, config=StrategyConfig(),
        now=datetime(2026, 11, 27, 17, 30, tzinfo=UTC),
    )

    assert result.session_reasons == ("session_not_regular",)
    assert not result.entry_timing_suitable
    assert result.recovery_basis == "common_hours"
    assert result.effective_liquidation_start == datetime(2026, 11, 27, 17, 35, tzinfo=UTC)
    assert result.effective_liquidation_escalation == datetime(2026, 11, 27, 17, 39, tzinfo=UTC)
    assert result.effective_liquidation_deadline == datetime(2026, 11, 27, 17, 40, tzinfo=UTC)


@pytest.mark.parametrize(
    ("at", "due", "escalation", "deadline"),
    [
        (datetime(2026, 11, 27, 17, 34, 59, tzinfo=UTC), False, False, False),
        (datetime(2026, 11, 27, 17, 35, tzinfo=UTC), True, False, False),
        (datetime(2026, 11, 27, 17, 39, tzinfo=UTC), True, True, False),
        (datetime(2026, 11, 27, 17, 40, tzinfo=UTC), True, True, True),
    ],
)
def test_recovery_predicates_are_explicit_inclusive_clock_comparisons(
    at, due, escalation, deadline
) -> None:
    day = date(2026, 11, 27)
    calendar = session(
        session_date=day, kind="early_close",
        opens_at=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        closes_at=datetime(2026, 11, 27, 18, tzinfo=UTC),
        available_at=datetime(2026, 11, 27, 14, tzinfo=UTC),
    )
    instrument = tradability(
        session_date=day,
        opens_at=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        closes_at=datetime(2026, 11, 27, 18, tzinfo=UTC),
        effective_from=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
        effective_until=datetime(2026, 11, 27, 18, tzinfo=UTC),
        available_at=datetime(2026, 11, 27, 14, tzinfo=UTC),
    )
    result = assess_session(calendar, instrument, config=StrategyConfig(), now=at)
    assert (result.liquidation_due, result.escalation_due, result.liquidation_deadline_reached) == (
        due, escalation, deadline
    )
    assert not hasattr(result, "flat") and not hasattr(result, "quantity")


def test_each_configured_clock_minimum_is_applied_independently() -> None:
    config = StrategyConfig(
        entry_end=time(14, 55),
        liquidation_start=time(15, 30),
        liquidation_deadline=time(15, 38),
    )
    result = assess(config=config)
    assert result.effective_liquidation_start == datetime(2026, 9, 4, 19, 30, tzinfo=UTC)
    assert result.effective_liquidation_deadline == datetime(2026, 9, 4, 19, 38, tzinfo=UTC)
    assert result.effective_liquidation_escalation == datetime(2026, 9, 4, 19, 37, tzinfo=UTC)


@pytest.mark.parametrize(
    ("instrument_close", "common_close", "effective_start"),
    [
        (datetime(2026, 9, 4, 20, 15, tzinfo=UTC), datetime(2026, 9, 4, 20, tzinfo=UTC), datetime(2026, 9, 4, 19, 35, tzinfo=UTC)),
        (datetime(2026, 9, 4, 19, 50, tzinfo=UTC), datetime(2026, 9, 4, 19, 50, tzinfo=UTC), datetime(2026, 9, 4, 19, 25, tzinfo=UTC)),
    ],
)
def test_instrument_close_constrains_but_never_extends_calendar_hours(
    instrument_close, common_close, effective_start
) -> None:
    result = assess(tradability=tradability(closes_at=instrument_close, effective_until=instrument_close))
    assert result.common_closes_at == common_close
    assert result.effective_liquidation_start == effective_start


def test_instrument_only_recovery_survives_missing_or_closed_calendar() -> None:
    for calendar in (
        None,
        session(kind="closed", opens_at=None, closes_at=None),
        session(session_date=date(2026, 9, 3)),
    ):
        result = assess(session=calendar)
        assert result.recovery_basis == "instrument_hours_only"
        assert result.effective_liquidation_deadline == datetime(2026, 9, 4, 19, 40, tzinfo=UTC)
        assert not result.entry_timing_suitable
        assert result.common_opens_at is None and result.common_closes_at is None


def test_declared_closure_is_distinct_from_missing_and_unknown_calendar() -> None:
    day = date(2026, 9, 7)
    at = datetime(2026, 9, 7, 14, tzinfo=UTC)
    closed = session(
        session_date=day, kind="closed", opens_at=None, closes_at=None,
        available_at=datetime(2026, 9, 7, 13, tzinfo=UTC),
    )
    unknown = replace(closed, kind="unknown")
    assert assess_session(closed, None, config=StrategyConfig(), now=at).session_reasons == (
        "session_closed", "session_hours_missing"
    )
    assert assess_session(unknown, None, config=StrategyConfig(), now=at).session_reasons == (
        "session_unknown", "session_hours_missing"
    )
    assert assess_session(None, None, config=StrategyConfig(), now=at).session_reasons == (
        "session_missing",
    )


def test_calendar_only_or_unknown_instrument_never_guesses_recovery_deadline() -> None:
    result = assess(tradability=None)
    assert result.recovery_basis == "unknown"
    assert result.effective_liquidation_start is None
    assert result.effective_liquidation_escalation is None
    assert result.effective_liquidation_deadline is None
    assert not result.liquidation_due
    assert "instrument_missing" in result.entry_reasons


@pytest.mark.parametrize("status", ["halted", "closed"])
def test_nontradable_status_preserves_known_common_recovery_hours(status) -> None:
    evidence = tradability(status=status)
    result = assess(tradability=evidence)
    assert result.tradability is evidence
    assert result.recovery_basis == "common_hours"
    assert result.effective_liquidation_deadline == datetime(2026, 9, 4, 19, 40, tzinfo=UTC)
    assert f"instrument_{status}" in result.operability_reasons
    assert not result.entry_timing_suitable


def test_expired_operability_preserves_hours_and_identity_for_recovery() -> None:
    evidence = tradability(
        contract=contract(underlying="QQQ", deliverable_id="adjusted-basket"),
        instrument_ref="opaque-adjusted-instrument",
        effective_until=NOW,
    )
    result = assess(tradability=evidence)
    assert result.tradability is evidence and result.tradability.instrument_ref == "opaque-adjusted-instrument"
    assert result.recovery_basis == "common_hours"
    assert result.operability_reasons == ("operability_expired",)


def test_unresolved_identity_blocks_entry_without_erasing_recovery_hours() -> None:
    evidence = tradability(contract=None)
    result = assess(tradability=evidence)
    assert result.instrument_hours_reasons == ("instrument_unresolved",)
    assert result.recovery_basis == "common_hours"
    assert result.tradability is evidence


def test_adverse_facts_have_stable_group_and_combined_order() -> None:
    tomorrow = date(2026, 9, 5)
    calendar = session(
        calendar="OTHER",
        session_date=tomorrow,
        kind="unknown",
        opens_at=None,
        closes_at=None,
        available_at=None,
        availability_basis="assumed",
    )
    instrument = tradability(
        contract=None,
        session_date=tomorrow,
        opens_at=None,
        closes_at=None,
        status="unknown",
        effective_from=None,
        effective_until=None,
        available_at=NOW + timedelta(seconds=1),
        availability_basis="assumed",
    )
    result = assess(session=calendar, tradability=instrument)
    assert result.session_reasons == (
        "calendar_unsupported",
        "session_wrong_date",
        "session_availability_unknown",
        "session_availability_not_measured",
        "session_unknown",
        "session_hours_missing",
    )
    assert result.instrument_hours_reasons == (
        "instrument_unresolved",
        "instrument_wrong_date",
        "instrument_available_after_now",
        "instrument_availability_not_measured",
        "instrument_hours_missing",
    )
    assert result.operability_reasons == (
        "operability_interval_unknown",
        "instrument_status_unknown",
    )
    assert result.entry_reasons == (
        *result.session_reasons,
        *result.instrument_hours_reasons,
        *result.operability_reasons,
        "no_common_interval",
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"opens_at": datetime(2026, 9, 4, 20, tzinfo=UTC), "closes_at": datetime(2026, 9, 4, 19, tzinfo=UTC)}, "session_hours_reversed"),
        ({"opens_at": datetime(2026, 9, 3, 13, 30, tzinfo=UTC)}, "session_hours_wrong_date"),
        ({"available_at": NOW + timedelta(microseconds=1)}, "session_available_after_now"),
        ({"availability_basis": "assumed"}, "session_availability_not_measured"),
    ],
)
def test_calendar_contradictions_are_retained_and_assessed(changes, reason) -> None:
    evidence = session(**changes)
    result = assess(session=evidence)
    assert result.session is evidence and reason in result.session_reasons


@pytest.mark.parametrize(
    ("changes", "group", "reason"),
    [
        ({"opens_at": datetime(2026, 9, 4, 20, tzinfo=UTC), "closes_at": datetime(2026, 9, 4, 19, tzinfo=UTC)}, "hours", "instrument_hours_reversed"),
        ({"opens_at": datetime(2026, 9, 3, 13, 30, tzinfo=UTC)}, "hours", "instrument_hours_wrong_date"),
        ({"effective_from": None}, "operability", "operability_interval_unknown"),
        ({"effective_from": NOW, "effective_until": NOW}, "operability", "operability_interval_invalid"),
        ({"effective_from": NOW + timedelta(seconds=1)}, "operability", "operability_not_yet_effective"),
        ({"effective_until": NOW}, "operability", "operability_expired"),
    ],
)
def test_instrument_hours_and_operability_fail_independently(changes, group, reason) -> None:
    evidence = tradability(**changes)
    result = assess(tradability=evidence)
    reasons = result.instrument_hours_reasons if group == "hours" else result.operability_reasons
    assert result.tradability is evidence and reason in reasons


def test_disjoint_intervals_are_exposed_but_not_used_as_recovery_hours() -> None:
    evidence = tradability(
        opens_at=datetime(2026, 9, 4, 20, 1, tzinfo=UTC),
        closes_at=datetime(2026, 9, 4, 21, tzinfo=UTC),
        effective_from=datetime(2026, 9, 4, 20, 1, tzinfo=UTC),
        effective_until=datetime(2026, 9, 4, 21, tzinfo=UTC),
    )
    result = assess(tradability=evidence)
    assert result.common_opens_at == datetime(2026, 9, 4, 20, 1, tzinfo=UTC)
    assert result.common_closes_at == datetime(2026, 9, 4, 20, tzinfo=UTC)
    assert "no_common_interval" in result.entry_reasons
    assert result.recovery_basis == "unknown"


@pytest.mark.parametrize(
    ("day", "open_utc", "now_utc"),
    [
        (date(2026, 3, 6), datetime(2026, 3, 6, 14, 30, tzinfo=UTC), datetime(2026, 3, 6, 15, tzinfo=UTC)),
        (date(2026, 3, 9), datetime(2026, 3, 9, 13, 30, tzinfo=UTC), datetime(2026, 3, 9, 14, tzinfo=UTC)),
    ],
)
def test_new_york_zone_rules_drive_dst_conversion(day, open_utc, now_utc) -> None:
    close_utc = open_utc + timedelta(hours=6, minutes=30)
    calendar = session(session_date=day, opens_at=open_utc, closes_at=close_utc, available_at=open_utc)
    instrument = tradability(
        session_date=day, opens_at=open_utc, closes_at=close_utc,
        effective_from=open_utc, effective_until=close_utc, available_at=open_utc,
    )
    result = assess_session(calendar, instrument, config=StrategyConfig(), now=now_utc)
    assert result.configured_entry_start == now_utc
    assert result.entry_timing_suitable


def test_strategy_date_uses_new_york_date_at_utc_midnight() -> None:
    at = datetime(2026, 9, 5, 0, 30, tzinfo=UTC)
    result = assess(at)
    assert "session_wrong_date" not in result.session_reasons
    assert "instrument_wrong_date" not in result.instrument_hours_reasons
    assert result.configured_entry_start.date() == date(2026, 9, 4)


def test_entry_policy_uses_current_decision_remaining_time() -> None:
    short_close = datetime(2026, 9, 4, 19, 55, tzinfo=UTC)
    result = assess(
        datetime(2026, 9, 4, 19, tzinfo=UTC),
        session=session(closes_at=short_close),
        tradability=tradability(closes_at=short_close, effective_until=short_close),
    )
    assert result.effective_liquidation_start == datetime(2026, 9, 4, 19, 30, tzinfo=UTC)
    assert result.entry_reasons == ("insufficient_time_before_liquidation",)


def test_valid_extreme_utc_time_conversion_failure_is_bounded_evidence() -> None:
    at = datetime.min.replace(tzinfo=UTC)
    result = assess_session(None, None, config=StrategyConfig(), now=at)
    assert result.now == at
    assert result.entry_reasons == (
        "session_missing",
        "instrument_missing",
        "time_conversion_unsupported",
    )
    assert result.configured_entry_start is None
    assert result.recovery_basis == "unknown"


def test_datetime_arithmetic_overflow_is_bounded_evidence() -> None:
    at = datetime(9999, 12, 31, 23, 59, tzinfo=UTC)
    day = date(9999, 12, 31)
    opened = datetime(9999, 12, 31, 14, 30, tzinfo=UTC)
    closed = datetime.max.replace(tzinfo=UTC)
    calendar = session(session_date=day, opens_at=opened, closes_at=closed, available_at=opened)
    instrument = tradability(
        session_date=day, opens_at=opened, closes_at=closed,
        effective_from=opened, effective_until=closed, available_at=opened,
    )
    result = assess_session(calendar, instrument, config=StrategyConfig(), now=at)
    assert "time_arithmetic_unsupported" in result.entry_reasons
    assert not result.entry_timing_suitable


class DateSubclass(date):
    """This class represents an invalid date subclass test value."""


class DatetimeSubclass(datetime):
    """This class represents an invalid datetime subclass test value."""


@pytest.mark.parametrize(
    ("factory", "changes", "error"),
    [
        (session, {"calendar": ""}, ValueError),
        (session, {"session_date": DateSubclass(2026, 9, 4)}, TypeError),
        (session, {"kind": "holiday"}, ValueError),
        (session, {"opens_at": DatetimeSubclass(2026, 9, 4, 13, 30, tzinfo=UTC)}, TypeError),
        (session, {"available_at": datetime(2026, 9, 4, 13)}, ValueError),
        (session, {"fidelity": "modeled"}, ValueError),
        (tradability, {"contract": object()}, TypeError),
        (tradability, {"instrument_ref": ""}, ValueError),
        (tradability, {"status": "open"}, ValueError),
        (tradability, {"availability_basis": True}, TypeError),
    ],
)
def test_typed_records_reject_malformed_representation_only(factory, changes, error) -> None:
    with pytest.raises(error):
        factory(**changes)


def test_assessment_rejects_untrusted_argument_types() -> None:
    with pytest.raises(TypeError):
        assess_session(object(), tradability(), config=StrategyConfig(), now=NOW)
    with pytest.raises(TypeError):
        assess_session(session(), object(), config=StrategyConfig(), now=NOW)
    with pytest.raises(TypeError):
        assess_session(session(), tradability(), config=object(), now=NOW)
    with pytest.raises(ValueError):
        assess_session(session(), tradability(), config=StrategyConfig(), now=NOW.replace(tzinfo=None))


def test_same_inputs_are_deterministic_and_have_no_operational_outputs() -> None:
    calendar = session()
    instrument = tradability()
    config = StrategyConfig()
    first = assess_session(calendar, instrument, config=config, now=NOW)
    second = assess_session(calendar, instrument, config=config, now=NOW)
    assert first == second
    assert not hasattr(first, "order")
    assert not hasattr(first, "sell")
    assert replace(first) == first
