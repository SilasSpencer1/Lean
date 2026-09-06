from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from options_lab.bars import UnderlyingBar, assess_underlying_bar
from options_lab.observations import ObservationMeta
from options_lab.sessions import ExchangeSession


UTC = timezone.utc
SESSION_DATE = date(2026, 9, 4)
OPEN = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
CLOSE = datetime(2026, 9, 4, 20, tzinfo=UTC)
BAR_END = OPEN + timedelta(minutes=1)


def session(**changes: object) -> ExchangeSession:
    """Return synthetic-calendar-factory-v1 evidence, never source admission."""
    values = {
        "calendar": "XNYS",
        "session_date": SESSION_DATE,
        "kind": "regular",
        "opens_at": OPEN,
        "closes_at": CLOSE,
        "source": "synthetic-calendar",
        "provider_record_id": "synthetic-session-2026-09-04",
        "source_version": "synthetic-calendar-v1",
        "available_at": OPEN - timedelta(minutes=30),
        "availability_basis": "measured",
        "received_at": OPEN - timedelta(minutes=29),
        "raw_ref": "synthetic://calendar/2026-09-04",
        "fidelity": "synthetic",
    }
    values.update(changes)
    return ExchangeSession(**values)


def meta(**changes: object) -> ObservationMeta:
    """Return synthetic-bar-meta-factory-v1 evidence, never source admission."""
    values = {
        "source": "synthetic-bars",
        "provider_record_id": "synthetic-SPY-2026-09-04T09:30",
        "raw_ref": "synthetic://bars/SPY/2026-09-04/09:30/rev-1",
        "feed_class": "delayed",
        "fidelity": "synthetic",
        "kind": "interval",
        "event_at": None,
        "available_at": BAR_END,
        "received_at": datetime(2026, 9, 5, 14, tzinfo=UTC),
        "availability_basis": "assumed",
        "availability_evidence_ref": "synthetic-bar-clock-v1",
        "interval_start": OPEN,
        "interval_end": BAR_END,
        "is_fill_forward": False,
        "quality_flags": (),
    }
    values.update(changes)
    return ObservationMeta(**values)


def bar(**changes: object) -> UnderlyingBar:
    """Return synthetic-underlying-bar-factory-v1 evidence, never admission."""
    values = {
        "symbol": "SPY",
        "meta": meta(),
        "close_price": Decimal("100.25"),
        "volume": Decimal("12.5"),
        "vwap_numerator": Decimal("1250.00"),
        "vwap_denominator": Decimal("12.5"),
        "price_basis": "raw",
        "volume_definition_id": "synthetic-sip-eligible-shares-v1",
        "vwap_definition_id": "synthetic-sip-exact-vwap-v1",
        "revision_id": "rev-1",
        "supersedes_revision_id": None,
        "receive_sequence": 1,
    }
    values.update(changes)
    return UnderlyingBar(**values)


def assess(observed: UnderlyingBar | None = None, **changes: object):
    """Assess synthetic bar evidence at an explicit availability cutoff."""
    values = {"session": session(), "as_of": BAR_END}
    values.update(changes)
    return assess_underlying_bar(bar() if observed is None else observed, **values)


def test_completed_bar_retains_claims_and_independent_component_evidence() -> None:
    observed = bar()
    result = assess(observed)

    assert result.bar is observed and result.session == session()
    assert result.as_of == BAR_END
    assert result.observation.meta is observed.meta
    assert result.availability_reasons == ()
    assert result.price_history_reasons == ()
    assert result.volume_reasons == ()
    assert result.vwap_reasons == ()
    assert not hasattr(result, "calendar_reasons")
    assert result.price_history_suitable
    assert result.volume_available
    assert result.exact_vwap_available
    assert observed.interval_start == OPEN
    assert observed.interval_end == observed.available_at == BAR_END
    assert observed.meta.source == "synthetic-bars"
    for unsupported_claim in ("source_admitted", "feature_ready", "entry_ready"):
        assert not hasattr(result, unsupported_claim)


def test_historical_bar_uses_later_cutoff_without_changing_its_session_date() -> None:
    next_day = datetime(2026, 9, 5, 14, tzinfo=UTC)
    result = assess(as_of=next_day)
    assert result.availability_reasons == ()
    assert result.price_history_suitable

    wrong_calendar = assess(session=replace(session(), session_date=date(2026, 9, 5)), as_of=next_day)
    wrong_interval = assess(
        bar(meta=meta(
            interval_start=OPEN + timedelta(days=1),
            interval_end=BAR_END + timedelta(days=1),
            available_at=BAR_END + timedelta(days=1),
        )),
        as_of=BAR_END + timedelta(days=1),
    )
    assert "session_wrong_date" in wrong_calendar.availability_reasons
    assert "session_wrong_date" in wrong_interval.availability_reasons
    assert not wrong_calendar.price_history_suitable
    assert not wrong_interval.price_history_suitable


@pytest.mark.parametrize(
    ("meta_changes", "as_of_delta", "reason"),
    [
        ({}, timedelta(0), None),
        ({"available_at": BAR_END + timedelta(microseconds=1)}, timedelta(0), "available_after_decision"),
        ({"available_at": BAR_END - timedelta(microseconds=1)}, timedelta(0), "interval_published_before_end"),
        ({"event_at": BAR_END + timedelta(microseconds=1)}, timedelta(0), "event_after_decision"),
        ({"event_at": None}, timedelta(0), None),
    ],
)
def test_completion_publication_and_event_boundaries_are_causal(
    meta_changes, as_of_delta, reason
) -> None:
    result = assess(bar(meta=meta(**meta_changes)), as_of=BAR_END + as_of_delta)
    assert (reason in result.availability_reasons) if reason else not result.availability_reasons
    assert result.price_history_suitable is (reason is None)


@pytest.mark.parametrize(
    ("start", "end", "kind", "reason"),
    [
        (OPEN, OPEN + timedelta(seconds=59), "interval", "interval_duration_not_one_minute"),
        (OPEN, OPEN + timedelta(seconds=61), "interval", "interval_duration_not_one_minute"),
        (
            OPEN + timedelta(seconds=30),
            OPEN + timedelta(minutes=1, seconds=30),
            "interval",
            "interval_not_minute_aligned",
        ),
        (OPEN - timedelta(minutes=1), OPEN, "interval", "interval_outside_session"),
        (CLOSE - timedelta(seconds=30), CLOSE + timedelta(seconds=30), "interval", "interval_outside_session"),
        (OPEN, BAR_END, "quote", "not_interval"),
    ],
)
def test_only_aligned_one_minute_intervals_wholly_inside_session_are_usable(
    start, end, kind, reason
) -> None:
    observed = bar(meta=meta(
        kind=kind,
        interval_start=start,
        interval_end=end,
        available_at=end,
    ))
    result = assess(observed, as_of=max(BAR_END, end))
    assert reason in result.availability_reasons
    assert not result.price_history_suitable


@pytest.mark.parametrize(
    ("start", "end", "reason"),
    [
        (None, BAR_END, "interval_bounds_missing"),
        (BAR_END, BAR_END, "interval_order_invalid"),
        (BAR_END, OPEN, "interval_order_invalid"),
    ],
)
def test_missing_and_reversed_bounds_remain_bounded_evidence(start, end, reason) -> None:
    result = assess(bar(meta=meta(interval_start=start, interval_end=end)))
    assert reason in result.availability_reasons
    assert not result.price_history_suitable


@pytest.mark.parametrize(
    ("day", "opened"),
    [
        (date(2026, 3, 6), datetime(2026, 3, 6, 14, 30, tzinfo=UTC)),
        (date(2026, 3, 9), datetime(2026, 3, 9, 13, 30, tzinfo=UTC)),
    ],
)
def test_new_york_dst_rules_bind_bar_and_session_dates(day, opened) -> None:
    ended = opened + timedelta(minutes=1)
    calendar = session(
        session_date=day,
        opens_at=opened,
        closes_at=opened + timedelta(hours=6, minutes=30),
        available_at=ended,
    )
    observed = bar(meta=meta(
        interval_start=opened,
        interval_end=ended,
        available_at=ended,
    ))
    result = assess(observed, session=calendar, as_of=ended)
    assert result.availability_reasons == ()
    assert result.price_history_suitable


def test_last_session_minute_and_calendar_publication_equality_are_inclusive() -> None:
    observed = bar(meta=meta(
        interval_start=CLOSE - timedelta(minutes=1),
        interval_end=CLOSE,
        available_at=CLOSE,
    ))
    result = assess(observed, session=session(available_at=CLOSE), as_of=CLOSE)
    assert result.availability_reasons == ()
    assert result.price_history_suitable


def test_early_close_history_is_usable_while_calendar_fact_is_retained() -> None:
    early_close = session(kind="early_close", closes_at=datetime(2026, 9, 4, 17, tzinfo=UTC))
    result = assess(session=early_close)
    assert result.availability_reasons == ("session_not_regular",)
    assert result.price_history_suitable


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"calendar": "OTHER"}, "calendar_unsupported"),
        ({"available_at": BAR_END + timedelta(microseconds=1)}, "session_available_after_now"),
        ({"availability_basis": "assumed"}, "session_availability_not_measured"),
        ({"kind": "closed", "opens_at": None, "closes_at": None}, "session_closed"),
        ({"kind": "unknown"}, "session_unknown"),
        ({"opens_at": CLOSE, "closes_at": OPEN}, "session_hours_reversed"),
        ({"opens_at": datetime(2026, 9, 3, 13, 30, tzinfo=UTC)}, "session_hours_wrong_date"),
    ],
)
def test_calendar_facts_are_retained_and_block_history_when_adverse(changes, reason) -> None:
    result = assess(session=session(**changes))
    assert reason in result.availability_reasons
    assert not result.price_history_suitable


def test_common_availability_failure_blocks_each_component_without_erasing_claims() -> None:
    observed = bar()
    result = assess(observed, session=None)
    assert result.availability_reasons == ("session_missing",)
    assert not result.price_history_suitable
    assert not result.volume_available
    assert not result.exact_vwap_available
    assert result.bar is observed


def test_bar_uses_availability_instead_of_live_quote_freshness() -> None:
    result = assess(as_of=BAR_END + timedelta(days=30))
    assert result.observation.available
    assert "not_quote" in result.observation.live_quote_reasons
    assert "fidelity_not_genuine" in result.observation.live_quote_reasons
    assert "feed_not_realtime" in result.observation.live_quote_reasons
    assert "availability_not_measured" in result.observation.live_quote_reasons
    assert "event_time_missing" in result.observation.live_quote_reasons
    assert "quote_too_old" not in result.observation.live_quote_reasons
    assert result.price_history_suitable


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"symbol": "AAPL"}, "unsupported_symbol"),
        ({"price_basis": "split_adjusted"}, "price_basis_not_raw"),
        ({"price_basis": "total_return_adjusted"}, "price_basis_not_raw"),
        ({"price_basis": "unknown"}, "price_basis_not_raw"),
        ({"close_price": None}, "close_price_missing"),
        ({"close_price": Decimal("0")}, "close_price_nonpositive"),
        ({"meta": meta(is_fill_forward=True)}, "fill_forward"),
        ({"meta": meta(quality_flags=("suspect",))}, "quality_flags_present"),
        ({"supersedes_revision_id": "rev-1"}, "revision_self_supersession"),
    ],
)
def test_price_history_reasons_preserve_adverse_source_claims(changes, reason) -> None:
    observed = bar(**changes)
    result = assess(observed)
    assert reason in result.price_history_reasons
    assert not result.price_history_suitable
    assert result.bar is observed


@pytest.mark.parametrize(
    ("volume", "definition", "reasons", "available"),
    [
        (Decimal("12.5"), "synthetic-sip-eligible-shares-v1", (), True),
        (Decimal("0"), "synthetic-sip-eligible-shares-v1", (), True),
        (None, "synthetic-sip-eligible-shares-v1", ("volume_missing",), False),
        (Decimal("12.5"), None, ("volume_definition_unknown",), False),
        (None, None, ("volume_missing", "volume_definition_unknown"), False),
    ],
)
def test_volume_is_independent_and_preserves_missing_and_zero(
    volume, definition, reasons, available
) -> None:
    result = assess(bar(volume=volume, volume_definition_id=definition))
    assert result.volume_reasons == reasons
    assert result.volume_available is available
    assert result.price_history_suitable
    assert result.bar.volume is volume


@pytest.mark.parametrize(
    ("numerator", "denominator", "definition", "reasons", "available"),
    [
        (Decimal("1250"), Decimal("12.5"), "v1", (), True),
        (Decimal("0"), Decimal("0"), "v1", (), True),
        (None, None, "v1", ("vwap_numerator_missing", "vwap_denominator_missing"), False),
        (None, Decimal("12.5"), "v1", ("vwap_numerator_missing",), False),
        (Decimal("1250"), None, "v1", ("vwap_denominator_missing",), False),
        (Decimal("1250"), Decimal("0"), "v1", ("vwap_numerator_without_volume",), False),
        (Decimal("0"), Decimal("12.5"), "v1", ("vwap_numerator_nonpositive",), False),
        (Decimal("1250"), Decimal("12.5"), None, ("vwap_definition_unknown",), False),
    ],
)
def test_exact_vwap_contribution_preserves_partial_zero_and_contradictory_pairs(
    numerator, denominator, definition, reasons, available
) -> None:
    result = assess(bar(
        vwap_numerator=numerator,
        vwap_denominator=denominator,
        vwap_definition_id=definition,
    ))
    assert result.vwap_reasons == reasons
    assert result.exact_vwap_available is available
    assert result.price_history_suitable
    assert result.bar.vwap_numerator is numerator
    assert result.bar.vwap_denominator is denominator


def test_volume_and_vwap_definitions_and_amounts_are_not_forced_equal() -> None:
    result = assess(bar(
        volume=Decimal("99.5"),
        vwap_denominator=Decimal("12.5"),
        volume_definition_id="consolidated-shares-v1",
        vwap_definition_id="venue-a-vwap-v2",
    ))
    assert result.volume_available and result.exact_vwap_available
    assert result.bar.volume != result.bar.vwap_denominator
    assert result.bar.volume_definition_id != result.bar.vwap_definition_id


def test_revision_lineage_and_unknown_receive_order_are_retained() -> None:
    observed = bar(supersedes_revision_id="rev-0", receive_sequence=None)
    result = assess(observed)
    assert result.bar.supersedes_revision_id == "rev-0"
    assert result.bar.receive_sequence is None
    assert result.price_history_suitable


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"symbol": 1}, TypeError),
        ({"symbol": ""}, ValueError),
        ({"meta": object()}, TypeError),
        ({"close_price": 1}, TypeError),
        ({"volume": Decimal("NaN")}, ValueError),
        ({"vwap_numerator": Decimal("-1")}, ValueError),
        ({"vwap_denominator": "1"}, TypeError),
        ({"price_basis": True}, TypeError),
        ({"price_basis": "adjusted"}, ValueError),
        ({"volume_definition_id": ""}, ValueError),
        ({"vwap_definition_id": 1}, TypeError),
        ({"revision_id": ""}, ValueError),
        ({"supersedes_revision_id": ""}, ValueError),
        ({"receive_sequence": True}, TypeError),
        ({"receive_sequence": -1}, ValueError),
    ],
)
def test_typed_bar_rejects_malformed_representation_only(changes, error) -> None:
    with pytest.raises(error):
        bar(**changes)


def test_exact_types_utc_normalization_and_immutability() -> None:
    eastern = timezone(timedelta(hours=-4))
    observed = bar(meta=meta(
        interval_start=datetime(2026, 9, 4, 9, 30, tzinfo=eastern),
        interval_end=datetime(2026, 9, 4, 9, 31, tzinfo=eastern),
        available_at=datetime(2026, 9, 4, 9, 31, tzinfo=eastern),
    ))
    result = assess(observed, as_of=datetime(2026, 9, 4, 9, 31, tzinfo=eastern))
    assert observed.interval_start == OPEN and result.as_of == BAR_END
    assert result.price_history_suitable
    with pytest.raises(FrozenInstanceError):
        observed.close_price = Decimal("9")
    with pytest.raises(FrozenInstanceError):
        result.price_history_reasons = ()
    assert replace(result, bar=replace(observed, close_price=Decimal("0"))).price_history_reasons == (
        "close_price_nonpositive",
    )


def test_assessment_rejects_trusted_misuse_and_bounds_extreme_dates() -> None:
    with pytest.raises(TypeError):
        assess_underlying_bar(object(), session(), as_of=BAR_END)
    with pytest.raises(TypeError):
        assess(session=object())
    with pytest.raises(ValueError):
        assess(as_of=BAR_END.replace(tzinfo=None))

    earliest = datetime.min.replace(tzinfo=UTC)
    extreme_session = session(
        session_date=date.min,
        opens_at=earliest,
        closes_at=earliest + timedelta(minutes=1),
        available_at=earliest,
    )
    extreme_bar = bar(meta=meta(
        interval_start=earliest,
        interval_end=earliest + timedelta(minutes=1),
        available_at=earliest + timedelta(minutes=1),
    ))
    result = assess(extreme_bar, session=extreme_session, as_of=datetime.max.replace(tzinfo=UTC))
    assert "time_conversion_unsupported" in result.availability_reasons
    assert not result.price_history_suitable
