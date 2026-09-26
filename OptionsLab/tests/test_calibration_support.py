"""Actual calibration support consumes admitted bundle evidence."""

from datetime import datetime, timedelta, timezone, tzinfo
from copy import copy
from zoneinfo import ZoneInfo
import pytest

from test_calibration_metadata import bundle_result, admitted
from test_bundles import verify as base_bundle_result
from options_lab.calibration_support import assess_calibration
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import verify_bundle
from options_lab.config import StrategyConfig
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.runtime import measure_runtime
from options_lab.volume_normalization import normalize_feature_normalization


def support_bundle_result(record="fixed-c2"):
    fixture = admitted("p14c2-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    receipt = dict(event_id="c2-test", raw_ref="synthetic://c2-test",
                   received_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    manifest = normalize_bundle_manifest(body["manifest"], **receipt).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    member = next(m for m in artifact.members if m.record_id == "good")
    norm = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact,
        record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    roots = tuple(admitted(name) for name in ("p11-feature-vector-v1", "p14c-partition-samples-v1",
        "p14c-partition-memberships-v1", "p14c2-sources-v1", "p14c2-calendar-v1",
        "p14c2-partition-v1", "p14c2-record-v1"))
    return verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC, normalization=norm, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=roots)


def test_causal_fixed_fixture_meets_exact_mechanical_boundaries():
    result = support_bundle_result()
    assert result.value is not None, result.rejection
    call = assess_calibration(result.value, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    put = assess_calibration(result.value, "put", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    for assessment in (call, put):
        assert assessment.supported is True, assessment.reasons
        assert assessment.bucket.bucket_id == assessment.bucket_id
        assert assessment.complete_pairs == 10
        assert assessment.covered_sessions == 21
        assert (assessment.covered_attempts, assessment.observed_attempts,
                assessment.supported_attempts, assessment.supported_pairs,
                assessment.contributing_regular_sessions) == (51, 50, 50, 10, 20)
        assert assessment.statistical_readiness is False
        assert assessment.operational_allowed is False and assessment.economic_allowed is False


def test_supported_bundle_accepts_standard_named_timezone_and_unknown_bucket_is_bounded():
    bundle = support_bundle_result().value
    assert bundle is not None
    when = datetime(2026, 9, 1, 2, tzinfo=ZoneInfo("UTC"))
    assert assess_calibration(bundle, "put", now=when).supported is True
    unknown = assess_calibration(bundle, "other", now=when)
    assert unknown.reasons == ("unknown_bucket",)
    assert unknown.covered_attempts is None and unknown.penalty is None


def test_cash_and_fixed_without_metadata_do_not_claim_calibration_counts():
    cash = base_bundle_result("cash").value
    fixed = base_bundle_result("fixed").value
    assert cash is not None and fixed is not None
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for bundle, reason in ((cash, "calibration_not_applicable"),
                           (fixed, "calibration_metadata_missing")):
        result = assess_calibration(bundle, "call", now=now)
        assert result.reasons == (reason,)
        assert result.covered_attempts is None and result.supported_attempts is None
        assert result.penalty is None and result.statistical_readiness is False


def test_changed_retained_runtime_is_not_used_as_current_a0_authority():
    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    runtime = copy(bundle.supplied_runtime)
    object.__setattr__(runtime, "implementation_digest", "0" * 64)
    object.__setattr__(altered, "supplied_runtime", runtime)
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)


@pytest.mark.parametrize("field", ("original_fixture", "member", "supplied_upstream_fixtures",
                                   "config", "supplied_normalization", "runtime", "model"))
def test_missing_retained_owner_field_has_bounded_result(field):
    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    object.__delattr__(altered, field)
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)


def test_missing_nested_retained_member_field_has_bounded_result():
    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    member = copy(bundle.member)
    object.__delattr__(member, "record_id")
    object.__setattr__(altered, "member", member)
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)


@pytest.mark.parametrize("field", ("member", "supplied_upstream_fixtures", "config"))
def test_wrong_nested_owner_type_is_bounded_for_shared_c2_recheck(field):
    """C2 must share B2's bounded retained-owner preflight without hostile hooks."""
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError("hostile attribute hook invoked")

        def __eq__(self, other):
            raise AssertionError("hostile equality hook invoked")

    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    object.__setattr__(altered, field, (Hostile(),) if field == "supplied_upstream_fixtures" else Hostile())
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)
    assert result.bundle is None and result.rejection is None


def test_missing_nested_fixture_leaf_is_bounded_and_wrong_public_type_still_raises():
    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    fixture = copy(bundle.original_fixture)
    object.__delattr__(fixture, "payload_bytes")
    object.__setattr__(altered, "original_fixture", fixture)
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)
    with pytest.raises(TypeError):
        assess_calibration(object(), "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))


def test_retained_bucket_with_hostile_leaf_is_rejected_without_invoking_equality():
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("unexpected hostile equality")

    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    metadata = copy(bundle.calibration_metadata)
    bucket = copy(metadata.buckets[0])
    object.__setattr__(bucket, "penalty", Hostile())
    object.__setattr__(metadata, "buckets", (bucket, metadata.buckets[1]))
    object.__setattr__(altered, "calibration_metadata", metadata)
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)


def test_retained_datetime_with_hostile_timezone_is_rejected_without_invoking_it():
    class HostileTimezone(tzinfo):
        def utcoffset(self, dt):
            raise AssertionError("unexpected hostile timezone call")

    bundle = support_bundle_result().value
    assert bundle is not None
    altered = copy(bundle)
    metadata = copy(bundle.calibration_metadata)
    object.__setattr__(metadata, "available_at", datetime(2026, 9, 1, 1, tzinfo=HostileTimezone()))
    object.__setattr__(altered, "calibration_metadata", metadata)
    result = assess_calibration(altered, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.reasons == ("retained_content_mismatch",)


@pytest.mark.parametrize("record,reason", (
    ("late-status", "row_unavailable"),
    ("cross-role-leak", "identity_conflict"),
    ("late-odd-label", "label_after_evaluation"),
    ("forty-nine-call", "insufficient_distinct_observed_attempts"),
    ("unsupported-pair", "insufficient_supported_pairs"),
    ("early-close", "insufficient_supported_pairs"),
    ("censored-partial", "partial_time_after_row"),
    ("status-unknown", "row_unavailable"),
    ("late-tune-status", "fit_status_after_freeze"),
    ("late-record", "record_unavailable"),
    ("record-before-fit", "record_before_fit"),
    ("wrong-training-max", "training_feature_cutoff_mismatch"),
    ("wrong-label-max", "last_label_available_at_mismatch"),
))
def test_admitted_adverse_bundle_never_gains_support(record, reason):
    result = support_bundle_result(record)
    assert result.value is not None, result.rejection
    assessment = assess_calibration(result.value, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert assessment.supported is False
    assert reason in assessment.reasons
    if record == "late-odd-label":
        assert assessment.last_label_available_at == datetime(2026, 9, 1, 5, tzinfo=timezone.utc)
    if record == "forty-nine-call":
        assert (assessment.supported_pairs, assessment.contributing_regular_sessions,
                assessment.supported_attempts) == (10, 20, 49)
    if record == "unsupported-pair":
        assert (assessment.observed_attempts, assessment.supported_pairs,
                assessment.contributing_regular_sessions, assessment.supported_attempts) == (47, 9, 18, 45)
    if record == "early-close":
        assert assessment.observed_attempts == 50
        assert (assessment.supported_pairs, assessment.contributing_regular_sessions,
                assessment.supported_attempts) == (9, 18, 45)


def test_duplicate_sample_identity_fails_actual_a2_before_it_can_inflate_counts():
    result = support_bundle_result("duplicate-sample")
    assert result.value is None
    assert result.rejection is not None


def test_old_content_fixture_cannot_claim_causal_support():
    bundle = bundle_result("fixed-metadata").value
    assert bundle is not None
    assessment = assess_calibration(bundle, "call", now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert assessment.supported is False
    assert "calendar_availability_unknown" in assessment.reasons
