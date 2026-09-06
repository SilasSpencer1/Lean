from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from options_lab.observations import (
    FieldDiagnostic,
    InputRejection,
    ObservationAssessment,
    ObservationMeta,
    ObservationValidation,
    assess_observation,
    normalize_observation_meta,
)


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def quote_raw(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "source": "sip",
        "provider_record_id": "quote-42",
        "feed_class": "realtime",
        "fidelity": "genuine",
        "kind": "quote",
        "event_at": DECISION - timedelta(seconds=5),
        "available_at": DECISION,
        "availability_basis": "measured",
        "availability_evidence_ref": "capture-7",
        "is_fill_forward": False,
        "quality_flags": [],
    }
    raw.update(changes)
    return raw


def admitted(**changes: object) -> ObservationMeta:
    result = normalize_observation_meta(
        quote_raw(**changes),
        raw_ref="raw://quote/42",
        event_id="ingest-9",
        received_at=DECISION + timedelta(days=365),
    )
    assert result.rejection is None
    assert result.value is not None
    return result.value


def test_boundary_times_are_inclusive_and_current_ingestion_is_not_historical_availability() -> None:
    meta = admitted()

    assessment = assess_observation(meta, decision_at=DECISION)

    assert meta.received_at == DECISION + timedelta(days=365)
    assert assessment.available is True
    assert assessment.live_quote_time_suitable is True
    assert assessment.availability_reasons == ()
    assert assessment.live_quote_reasons == ()


@pytest.mark.parametrize(
    ("changes", "availability_reason", "live_reason"),
    [
        ({"available_at": DECISION + timedelta(microseconds=1)}, "available_after_decision", "available_after_decision"),
        ({"event_at": DECISION - timedelta(seconds=5, microseconds=1)}, None, "quote_too_old"),
        ({"event_at": DECISION + timedelta(microseconds=1), "available_at": DECISION + timedelta(seconds=1)}, "available_after_decision", "available_after_decision"),
        ({"event_at": DECISION, "available_at": DECISION - timedelta(microseconds=1)}, "event_after_available", "event_after_available"),
    ],
)
def test_temporal_failures_retain_claimed_facts(changes, availability_reason, live_reason) -> None:
    meta = admitted(**changes)

    assessment = assess_observation(meta, decision_at=DECISION)

    assert assessment.meta is meta
    assert availability_reason in assessment.availability_reasons if availability_reason else assessment.available
    assert live_reason in assessment.live_quote_reasons
    assert assessment.live_quote_time_suitable is False


def test_offset_timestamps_normalize_to_utc() -> None:
    meta = admitted(
        event_at="2026-09-05T10:29:55-04:00",
        available_at="2026-09-05T10:30:00-04:00",
    )

    assert meta.event_at == DECISION - timedelta(seconds=5)
    assert meta.available_at == DECISION
    assert assess_observation(meta, decision_at=DECISION).live_quote_time_suitable


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"kind": "interval", "interval_start": DECISION - timedelta(minutes=1), "interval_end": DECISION}, "not_quote"),
        ({"fidelity": "synthetic"}, "fidelity_not_genuine"),
        ({"fidelity": "unknown"}, "fidelity_not_genuine"),
        ({"feed_class": "indicative"}, "feed_not_realtime"),
        ({"feed_class": "delayed"}, "feed_not_realtime"),
        ({"feed_class": "unknown"}, "feed_not_realtime"),
        ({"availability_basis": "assumed"}, "availability_not_measured"),
        ({"event_at": None}, "event_time_missing"),
        ({"is_fill_forward": True}, "fill_forward"),
        ({"quality_flags": ["crossed"]}, "quality_flags_present"),
    ],
)
def test_each_live_provenance_category_is_retained_but_refused(changes, reason) -> None:
    assessment = assess_observation(admitted(**changes), decision_at=DECISION)

    assert assessment.available is True
    assert reason in assessment.live_quote_reasons
    assert assessment.live_quote_time_suitable is False


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"kind": "interval", "event_at": None}, "interval_bounds_missing"),
        ({"kind": "interval", "event_at": None, "interval_start": DECISION, "interval_end": DECISION}, "interval_order_invalid"),
        ({"kind": "interval", "event_at": None, "interval_start": DECISION - timedelta(minutes=1), "interval_end": DECISION + timedelta(microseconds=1), "available_at": DECISION + timedelta(seconds=1)}, "interval_after_decision"),
        ({"kind": "interval", "event_at": None, "interval_start": DECISION - timedelta(minutes=1), "interval_end": DECISION, "available_at": DECISION - timedelta(microseconds=1)}, "interval_published_before_end"),
    ],
)
def test_interval_completion_and_publication_are_assessed_without_fabricating_event_time(changes, reason) -> None:
    meta = admitted(**changes)
    assessment = assess_observation(meta, decision_at=DECISION)

    assert meta.event_at is None
    assert reason in assessment.availability_reasons


@pytest.mark.parametrize(
    "raw",
    [
        [],
        quote_raw(feed_class="fast"),
        quote_raw(feed_class=["secret-enum-value"]),
        quote_raw(event_at="2026-09-05T14:30:00"),
        quote_raw(event_at=datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))),
        quote_raw(is_fill_forward=1),
        quote_raw(quality_flags=[True]),
        quote_raw(secret_key="do-not-echo"),
    ],
)
def test_malformed_external_data_returns_safe_rejection(raw) -> None:
    result = normalize_observation_meta(
        raw,
        raw_ref="raw://bad/1",
        event_id="event-safe",
        received_at=DECISION,
    )

    assert result.value is None
    assert result.rejection is not None
    assert result.rejection.event_id == "event-safe"
    assert result.rejection.stage == "observation_normalization"
    assert "do-not-echo" not in repr(result)
    assert "secret_key" not in repr(result)


def test_normalized_records_and_collections_are_immutable() -> None:
    meta = admitted(quality_flags=["stale"])
    result = ObservationValidation(value=meta)

    assert meta.quality_flags == ("stale",)
    with pytest.raises(FrozenInstanceError):
        meta.source = "other"
    with pytest.raises(FrozenInstanceError):
        result.value = None


@pytest.mark.parametrize(
    "factory",
    [
        lambda: FieldDiagnostic(field="source", code=[]),
        lambda: InputRejection("event", DECISION, "raw", "wrong_stage", ("bad",), ()),
        lambda: InputRejection("event", DECISION, "raw", "observation_normalization", ["bad"], ()),
        lambda: ObservationAssessment(admitted(), [], ()),
        lambda: ObservationAssessment("not-meta", (), ()),
        lambda: ObservationValidation(value="not-meta"),
        lambda: ObservationValidation(rejection="not-rejection"),
    ],
)
def test_public_records_reject_wrong_or_mutable_nested_fields(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_rejection_stage_requires_an_exact_string() -> None:
    class Stage(str):
        pass

    with pytest.raises(TypeError, match="stage must be a string"):
        InputRejection(
            "event",
            DECISION,
            "raw",
            Stage("observation_normalization"),
            ("bad",),
            (FieldDiagnostic("source", "invalid_type"),),
        )


def test_assessment_cannot_pass_live_time_with_availability_failures() -> None:
    with pytest.raises(ValueError, match="live_quote_reasons must include"):
        ObservationAssessment(admitted(), ("available_after_decision",), ())


def test_interval_completion_equality_is_available_without_source_event_fabrication() -> None:
    meta = admitted(
        kind="interval",
        event_at=None,
        interval_start=DECISION - timedelta(minutes=1),
        interval_end=DECISION,
    )

    assessment = assess_observation(meta, decision_at=DECISION)

    assert meta.event_at is None
    assert assessment.available is True
    assert assessment.live_quote_time_suitable is False


def test_validation_result_requires_exactly_one_outcome() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ObservationValidation()
    with pytest.raises(ValueError, match="exactly one"):
        ObservationValidation(value=admitted(), rejection=normalize_observation_meta([], raw_ref="r", event_id="e", received_at=DECISION).rejection)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"decision_at": datetime(2026, 9, 5)}, ValueError),
        ({"decision_at": "2026-09-05T14:30:00Z"}, TypeError),
        ({"decision_at": DECISION, "max_quote_age": timedelta(0)}, ValueError),
        ({"decision_at": DECISION, "max_quote_age": timedelta(seconds=5, microseconds=1)}, ValueError),
        ({"decision_at": DECISION, "max_quote_age": 5}, TypeError),
    ],
)
def test_invalid_assessment_arguments_are_programming_errors(kwargs, error) -> None:
    with pytest.raises(error):
        assess_observation(admitted(), **kwargs)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"raw_ref": ""}, ValueError),
        ({"event_id": 7}, TypeError),
        ({"received_at": datetime(2026, 9, 5)}, ValueError),
    ],
)
def test_invalid_normalization_envelope_is_a_programming_error(changes, error) -> None:
    kwargs = {"raw_ref": "raw://1", "event_id": "event-1", "received_at": DECISION}
    kwargs.update(changes)
    with pytest.raises(error):
        normalize_observation_meta(quote_raw(), **kwargs)
