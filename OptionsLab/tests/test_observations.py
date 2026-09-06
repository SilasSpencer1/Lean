from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo

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


def test_unknown_key_cannot_run_equality_during_schema_rejection() -> None:
    class CollidingKey:
        def __init__(self) -> None:
            self.comparisons = 0
            self.explode = False

        def __hash__(self) -> int:
            return hash("source")

        def __eq__(self, other: object) -> bool:
            self.comparisons += 1
            if self.explode:
                raise RuntimeError("secret")
            return False

        def __repr__(self) -> str:
            raise AssertionError("untrusted key was represented")

        def __str__(self) -> str:
            raise AssertionError("untrusted key was stringified")

    key = CollidingKey()
    raw = quote_raw()
    del raw["source"]
    raw[key] = "secret"
    key.explode = True
    comparisons = key.comparisons

    result = normalize_observation_meta(raw, raw_ref="raw://bad", event_id="safe", received_at=DECISION)

    assert key.comparisons == comparisons
    assert result.rejection is not None
    assert result.rejection.diagnostics == (
        FieldDiagnostic("$", "unknown_fields"),
        FieldDiagnostic("source", "missing"),
    )


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


class CallbackTimezone(tzinfo):
    def __init__(self, fail_after: int, error: BaseException = RuntimeError("secret")):
        self.calls = 0
        self.fail_after = fail_after
        self.error = error

    def utcoffset(self, dt):
        self.calls += 1
        if self.calls > self.fail_after:
            raise self.error
        return timedelta(0)

    def dst(self, dt):
        return timedelta(0)


class MutatingTimezone(tzinfo):
    def __init__(self, callback):
        self.callback = callback

    def utcoffset(self, dt):
        self.callback()
        return timedelta(0)

    def dst(self, dt):
        return timedelta(0)


def test_timezone_callback_cannot_delete_unparsed_fields() -> None:
    raw = quote_raw()
    raw["event_at"] = datetime(
        2026, 9, 5, 14, 29, 55,
        tzinfo=MutatingTimezone(lambda: raw.pop("is_fill_forward", None)),
    )

    result = normalize_observation_meta(raw, raw_ref="raw://ok", event_id="safe", received_at=DECISION)

    assert result.value is not None
    assert result.value.is_fill_forward is False


def test_timezone_callback_cannot_erase_original_quality_flags() -> None:
    flags = ["crossed"]
    raw = quote_raw(quality_flags=flags)
    raw["event_at"] = datetime(
        2026, 9, 5, 14, 29, 55,
        tzinfo=MutatingTimezone(flags.clear),
    )

    result = normalize_observation_meta(raw, raw_ref="raw://ok", event_id="safe", received_at=DECISION)

    assert result.value is not None
    assert result.value.quality_flags == ("crossed",)
    assert "quality_flags_present" in assess_observation(result.value, decision_at=DECISION).live_quote_reasons


@pytest.mark.parametrize("fail_after", [0, 1])
def test_external_timezone_callback_failures_are_safe_rejections(fail_after) -> None:
    raw = quote_raw(event_at=datetime(2026, 9, 5, tzinfo=CallbackTimezone(fail_after)))

    result = normalize_observation_meta(raw, raw_ref="raw://bad", event_id="safe", received_at=DECISION)

    assert result.rejection is not None
    assert result.rejection.diagnostics == (FieldDiagnostic("event_at", "invalid_timestamp"),)
    assert "secret" not in repr(result)


def test_external_timezone_process_control_signal_propagates() -> None:
    raw = quote_raw(event_at=datetime(2026, 9, 5, tzinfo=CallbackTimezone(0, KeyboardInterrupt())))
    with pytest.raises(KeyboardInterrupt):
        normalize_observation_meta(raw, raw_ref="raw://bad", event_id="safe", received_at=DECISION)


def test_trusted_timezone_callback_failure_is_a_descriptive_programming_error() -> None:
    received_at = datetime(2026, 9, 5, tzinfo=CallbackTimezone(0))
    with pytest.raises(ValueError, match="received_at timezone evaluation failed"):
        normalize_observation_meta(quote_raw(), raw_ref="raw", event_id="event", received_at=received_at)


def test_valid_custom_timezone_is_normalized() -> None:
    raw = quote_raw(event_at=datetime(2026, 9, 5, 14, 29, 55, tzinfo=CallbackTimezone(99)))
    result = normalize_observation_meta(raw, raw_ref="raw://ok", event_id="safe", received_at=DECISION)
    assert result.value is not None
    assert result.value.event_at == DECISION - timedelta(seconds=5)


@pytest.mark.parametrize(
    ("field", "code"),
    [("secret", "invalid_type"), ("$", "missing"), ("event_at", "invalid_type"),
     ("interval_start", "missing"), ("quality_flags", "invalid_value")],
)
def test_diagnostic_rejects_unknown_or_incompatible_field_code_pairs(field, code) -> None:
    with pytest.raises(ValueError):
        FieldDiagnostic(field, code)


@pytest.mark.parametrize(
    ("reasons", "diagnostics"),
    [
        ((), ()),
        (("missing",), (FieldDiagnostic("source", "invalid_type"),)),
        (("invalid_type", "invalid_type"), (FieldDiagnostic("source", "invalid_type"),)),
        (("invalid_type",), (FieldDiagnostic("feed_class", "invalid_type"), FieldDiagnostic("source", "invalid_type"))),
        (("missing", "unknown_fields"), (FieldDiagnostic("source", "missing"), FieldDiagnostic("$", "unknown_fields"))),
        (("missing",), (FieldDiagnostic("source", "missing"), FieldDiagnostic("source", "missing"))),
        (("missing", "invalid_type"), (FieldDiagnostic("source", "missing"), FieldDiagnostic("feed_class", "invalid_type"))),
    ],
)
def test_rejection_requires_canonical_related_reasons_and_diagnostics(reasons, diagnostics) -> None:
    with pytest.raises(ValueError):
        InputRejection("event", DECISION, "raw", "observation_normalization", reasons, diagnostics)


@pytest.mark.parametrize(
    ("availability", "live"),
    [
        (("made_up",), ("made_up",)),
        (("event_after_decision", "available_after_decision"), ("event_after_decision", "available_after_decision")),
        ((), ("made_up",)),
        ((), ("not_quote", "not_quote")),
        ((), ("quote_too_old", "not_quote")),
        ((), ("available_after_decision",)),
        (("available_after_decision",), ("not_quote", "available_after_decision")),
        (("available_after_decision",), ("not_quote",)),
    ],
)
def test_assessment_requires_canonical_reason_vocabulary_order_and_prefix(availability, live) -> None:
    with pytest.raises(ValueError):
        ObservationAssessment(admitted(), availability, live)


def test_emitters_preserve_canonical_multifailure_and_first_occurrence_orders() -> None:
    first = normalize_observation_meta(quote_raw(source="", provider_record_id=1), raw_ref="r", event_id="e", received_at=DECISION)
    second = normalize_observation_meta(quote_raw(source=1, provider_record_id=""), raw_ref="r", event_id="e", received_at=DECISION)
    assert first.rejection is not None and first.rejection.reasons == ("invalid_value", "invalid_type")
    assert second.rejection is not None and second.rejection.reasons == ("invalid_type", "invalid_value")

    assessment = assess_observation(admitted(event_at=DECISION + timedelta(seconds=2), available_at=DECISION + timedelta(seconds=1), fidelity="synthetic", feed_class="delayed", is_fill_forward=True), decision_at=DECISION)
    assert assessment.availability_reasons == ("available_after_decision", "event_after_decision", "event_after_available")
    assert assessment.live_quote_reasons == ("available_after_decision", "event_after_decision", "event_after_available", "fidelity_not_genuine", "feed_not_realtime", "fill_forward")
    assert ObservationAssessment(assessment.meta, assessment.availability_reasons, assessment.live_quote_reasons) == assessment
