"""Exercise actual assembly checks and source-bound report consumption."""

from copy import copy
from datetime import datetime, timezone

import pytest

from options_lab.bundle_inputs import normalize_model_bytes
from options_lab.config import StrategyConfig
from options_lab.runtime import measure_runtime
from test_bundle_schedule import admitted


def report_bundle(record):
    """Verify one actual registered assembly bundle and all its source owners."""
    from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
    from options_lab.bundles import verify_bundle
    from options_lab.feature_vector import EXACT_VWAP_SPEC
    from options_lab.volume_normalization import normalize_feature_normalization

    fixture = admitted("p14b1b-assembly-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], event_id="assembly-test",
        raw_ref="synthetic://assembly-test", received_at=datetime.now(timezone.utc)).value
    assert manifest is not None
    fixed = record == "fixed-validation"
    names = ("p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1",
             "p14b1b-assembly-v1") if not fixed else (
        "p11-feature-vector-v1", "p14c-partition-samples-v1", "p14c-partition-memberships-v1",
        "p14c2-sources-v1", "p14c2-calendar-v1", "p14c2-partition-v1", "p14c2-record-v1",
        "p14b-schedule-v1", "p14b1b-assembly-v1")
    norm = None
    if fixed:
        training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
        member = next(m for m in artifact.members if m.record_id == "good")
        norm = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact,
            record_id="good", training_manifest=training,
            decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
        assert norm is not None
    return verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC if fixed else None, normalization=norm, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=tuple(admitted(name) for name in names))


def test_checked_inputs_bind_real_cash_model_config_and_runtime():
    """Assembly must derive identities after inspecting actual source owners."""
    from options_lab.bundle_validation import _checked_inputs

    model = normalize_model_bytes(b'{"schema_version":1,"format_id":"options_lab.cash_json.v1"}',
        event_id="assembly-test", raw_ref="synthetic://assembly-test",
        received_at=datetime.now(timezone.utc)).value
    runtime = measure_runtime().value
    assert model is not None and runtime is not None
    fields, _ = _checked_inputs(model, None, None, None, None, StrategyConfig(), runtime, ())
    assert fields["model_hash"] == model.model_hash
    assert fields["implementation_digest"] == runtime.implementation_digest
    assert fields["feature_schema_id"] is fields["calibration_record_ref"] is None


def test_registered_report_is_rechecked_by_bundle_consumer():
    """A2 must retain a real source report after rerunning its owner checks."""
    for record in ("cash-validation", "fixed-validation"):
        result = report_bundle(record)
        assert result.value is not None, result.rejection
        report = result.value.validation_report
        assert report is not None
        assert report.performed_at <= report.fixture.assembled_at
        assert report.fixture.assembled_at <= result.value.manifest.provenance.built_at
        assert result.value.manifest.provenance.built_at <= result.value.fixture.assembled_at
        assert report.model_hash == result.value.model.model_hash
        assert report.runtime is not result.value.supplied_runtime
        assert (report.metadata is None) == (record == "cash-validation")


def test_c2_fresh_support_traverses_retained_report():
    """C2 must reverify the nested B1b report with the fixed bundle graph."""
    from options_lab.calibration_support import assess_calibration

    bundle = report_bundle("fixed-validation").value
    assert bundle is not None
    result = assess_calibration(bundle, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.supported is True, result.reasons


@pytest.mark.parametrize("record,code", (("bad-method", "unsupported_method"),
    ("bad-binding", "counterpart_mismatch"), ("bad-model", "counterpart_mismatch"),
    ("late-validation", "performed_after_assembly"), ("bad-ref", "raw_hash_mismatch"),
    ("missing-owner", "undeclared_reference")))
def test_registered_adverse_report_cannot_authorize_bundle(record, code):
    """A2 rejects changed method, owner, chronology and exact source references."""
    result = report_bundle(record)
    assert result.value is None
    if code in ("raw_hash_mismatch", "undeclared_reference"):
        assert result.rejection.code == code
    else:
        assert result.rejection.code == "normalization_failed"
        assert result.rejection.cause.code == code


def test_damaged_retained_model_and_runtime_are_bounded():
    """Copied owner fields cannot replace rederived bytes or invoke hostile equality."""
    from options_lab.bundle_validation import _checked_inputs
    from options_lab.calibration_inputs import _Failure

    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality was called")

    inspected = normalize_model_bytes(b'{"schema_version":1,"format_id":"options_lab.cash_json.v1"}',
        event_id="assembly-test", raw_ref="synthetic://assembly-test",
        received_at=datetime.now(timezone.utc))
    assert inspected.value is not None
    config, actual_runtime = StrategyConfig(), measure_runtime().value
    assert actual_runtime is not None
    model = copy(inspected.value)
    object.__setattr__(model, "model_hash", "0" * 64)
    with pytest.raises(_Failure) as failure:
        _checked_inputs(model, None, None, None, None, config, actual_runtime, ())
    assert failure.value.args[1] == "retained_content_mismatch"
    runtime = copy(actual_runtime)
    object.__setattr__(runtime, "implementation_digest", Hostile())
    with pytest.raises(_Failure) as failure:
        _checked_inputs(inspected.value, None, None, None, None, config, runtime, ())
    assert failure.value.args[1] == "retained_content_mismatch"
    fixed = report_bundle("fixed-validation").value
    assert fixed is not None
    metadata = copy(fixed.calibration_metadata)
    member = copy(metadata.member)
    object.__setattr__(member, "record_id", Hostile())
    object.__setattr__(metadata, "member", member)
    with pytest.raises(_Failure) as failure:
        _checked_inputs(fixed.model, fixed.spec, fixed.supplied_normalization,
            fixed.calibration_partition, metadata, fixed.config, fixed.runtime,
            fixed.upstream_fixtures)
    assert failure.value.args[1] == "retained_content_mismatch"
