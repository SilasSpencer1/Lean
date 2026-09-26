"""Exercise actual calendar schedule content and bundle counterparts."""

from copy import copy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_inputs import normalize_model_bytes
from options_lab.bundle_schedule import normalize_activation_gap, normalize_evaluation_block
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import verify_bundle
from options_lab.calibration_inputs import normalize_calendar_descriptor
from options_lab.calibration_support import assess_calibration
from options_lab.config import StrategyConfig
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.runtime import measure_runtime
from options_lab.volume_normalization import normalize_feature_normalization


FOLDER = Path(__file__).parent / "fixtures"


def admitted(name):
    """Read one exact registered fixture for schedule owner checks."""
    result = verify_fixture_bundle(name, (FOLDER / (name + ".json")).read_bytes(),
        event_id="schedule-test", raw_ref="synthetic://schedule-test",
        received_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert result.value is not None, result.rejection
    return result.value


def owners():
    """Return the actual source calendar and schedule root."""
    source, descriptor, schedule = (admitted(name) for name in
        ("p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1"))
    member = next(m for m in descriptor.members if m.record_id == "calendar")
    result = normalize_calendar_descriptor(member.decode_raw_body(), fixture=descriptor,
        record_id=member.record_id, upstream_fixtures=(source,))
    assert result.value is not None, result.rejection
    return source, descriptor, schedule, result.value


def test_schedule_owner_is_available():
    """The B1a owner must be importable for real gap and block admission."""
    assert callable(normalize_activation_gap) and callable(normalize_evaluation_block)


def test_actual_gap_uses_adjacent_calendar_sessions_including_holiday_and_early_close():
    """Only declared session neighbors determine gap bounds."""
    source, descriptor, fixture, calendar = owners()
    for name in ("gap", "holiday-gap", "nonadjacent"):
        member = next(m for m in fixture.members if m.record_id == name)
        result = normalize_activation_gap(member.decode_raw_body(), fixture=fixture,
            record_id=name, calendar=calendar, upstream_fixtures=(descriptor, source))
        if name == "nonadjacent":
            assert result.value is None and result.rejection.code == "nonadjacent_session"
        else:
            assert result.value is not None, result.rejection
            assert result.value.starts_at == result.value.previous_evidence.session.closes_at
            assert result.value.ends_at == result.value.next_evidence.session.opens_at
    early_member = next(m for m in descriptor.members if m.record_id == "early-close")
    early_calendar = normalize_calendar_descriptor(early_member.decode_raw_body(), fixture=descriptor,
        record_id=early_member.record_id, upstream_fixtures=(source,)).value
    assert early_calendar is not None
    member = next(m for m in fixture.members if m.record_id == "early-close")
    early_gap = normalize_activation_gap(member.decode_raw_body(), fixture=fixture,
        record_id=member.record_id, calendar=early_calendar, upstream_fixtures=(descriptor, source)).value
    assert early_gap is not None and early_gap.starts_at.hour == 17


def test_cash_block_keeps_actual_calendar_and_none_bindings():
    """Cash uses the calendar without fabricated feature or calibration owners."""
    source, descriptor, fixture, calendar = owners()
    model = normalize_model_bytes(b'{"schema_version":1,"format_id":"options_lab.cash_json.v1"}',
        event_id="cash-test", raw_ref="synthetic://cash-test",
        received_at=datetime(2026, 9, 30, tzinfo=timezone.utc)).value
    assert model is not None
    member = next(m for m in fixture.members if m.record_id == "cash-block")
    result = normalize_evaluation_block(member.decode_raw_body(), fixture=fixture,
        record_id=member.record_id, calendar=calendar, model=model, spec=None,
        normalization=None, partition=None, metadata=None, upstream_fixtures=(descriptor, source))
    assert result.value is not None, result.rejection
    assert result.value.calendar.content_hash == calendar.content_hash
    assert result.value.calibration_record_ref is None


def bundle_result(record):
    """Verify one registered bundle with all actual C2 and B1a roots."""
    fixture = admitted("p14b-schedule-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], event_id="schedule-test",
        raw_ref="synthetic://schedule-test",
        received_at=datetime(2026, 9, 30, tzinfo=timezone.utc)).value
    assert manifest is not None
    cash = record == "cash-schedule"
    norm = None
    if not cash:
        training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
        member = next(m for m in artifact.members if m.record_id == "good")
        norm = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact,
            record_id="good", training_manifest=training,
            decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
        assert norm is not None
    names = ("p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1") if cash else (
        "p11-feature-vector-v1", "p14c-partition-samples-v1", "p14c-partition-memberships-v1",
        "p14c2-sources-v1", "p14c2-calendar-v1", "p14c2-partition-v1", "p14c2-record-v1",
        "p14b-schedule-v1")
    return verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=None if cash else EXACT_VWAP_SPEC, normalization=norm, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=tuple(admitted(name) for name in names))


def test_bundle_consumes_actual_gap_and_block_and_rejects_conflicting_direct_refs():
    """A2 retains both source owners and rejects conflicting duplicate provenance."""
    for name in ("fixed-schedule", "cash-schedule"):
        result = bundle_result(name)
        assert result.value is not None, result.rejection
        assert result.value.activation_gap is not None
        assert result.value.evaluation_block is not None
        assert result.value.activation_gap.calendar.content_hash == result.value.evaluation_block.calendar.content_hash
    for name in ("direct-gap-mismatch", "direct-block-mismatch"):
        result = bundle_result(name)
        assert result.value is None and result.rejection.code == "counterpart_mismatch"
    for name, code in (("wrong-gap-kind", "kind_mismatch"),
                       ("wrong-block-hash", "raw_hash_mismatch")):
        result = bundle_result(name)
        assert result.value is None and result.rejection.code == code


def test_c2_fresh_counterpart_includes_new_schedule_owners():
    """Fresh C2 comparison traverses the retained B1a owners too."""
    bundle = bundle_result("fixed-schedule").value
    assert bundle is not None
    result = assess_calibration(bundle, "call", now=datetime(2026, 9, 1, 2, tzinfo=timezone.utc))
    assert result.supported is True, result.reasons


def test_forecast_block_rechecks_model_interval_and_complete_calendar():
    """A block cannot rewrite its actual fixed model or the C fold interval."""
    bundle = bundle_result("fixed-schedule").value
    assert bundle is not None
    block = bundle.evaluation_block
    assert block is not None
    fixture = admitted("p14b-schedule-v1")
    for name in ("block", "wrong-model", "wrong-interval"):
        member = next(m for m in fixture.members if m.record_id == name)
        result = normalize_evaluation_block(member.decode_raw_body(), fixture=fixture,
            record_id=name, calendar=block.calendar, model=bundle.model,
            spec=bundle.spec, normalization=bundle.supplied_normalization,
            partition=bundle.calibration_partition, metadata=bundle.calibration_metadata,
            upstream_fixtures=block.upstream_fixtures)
        if name == "block":
            assert result.value is not None, result.rejection
        else:
            expected = "incomplete_coverage" if name == "wrong-interval" else "counterpart_mismatch"
            assert result.value is None and result.rejection.code == expected


def test_forecast_block_rejects_each_frozen_counterpart_and_wrong_calendar():
    """Source members cannot change frozen feature, calibration, or calendar facts."""
    bundle = bundle_result("fixed-schedule").value
    assert bundle is not None
    block = bundle.evaluation_block
    assert block is not None
    fixture = admitted("p14b-schedule-v1")
    for name, expected in (("wrong-schema", "counterpart_mismatch"),
                           ("wrong-normalization", "counterpart_mismatch"),
                           ("wrong-threshold", "counterpart_mismatch"),
                           ("wrong-bucket-rule", "counterpart_mismatch"),
                           ("wrong-calendar", "retained_content_mismatch"),
                           ("wrong-record-ref", "raw_hash_mismatch")):
        member = next(m for m in fixture.members if m.record_id == name)
        result = normalize_evaluation_block(member.decode_raw_body(), fixture=fixture,
            record_id=name, calendar=block.calendar, model=bundle.model,
            spec=bundle.spec, normalization=bundle.supplied_normalization,
            partition=bundle.calibration_partition, metadata=bundle.calibration_metadata,
            upstream_fixtures=block.upstream_fixtures)
        assert result.value is None and result.rejection.code == expected


def test_unknown_gap_availability_remains_unknown():
    """B1a preserves missing availability for the later B2 decision."""
    source, descriptor, fixture, calendar = owners()
    member = next(m for m in fixture.members if m.record_id == "unknown-gap")
    result = normalize_activation_gap(member.decode_raw_body(), fixture=fixture,
        record_id=member.record_id, calendar=calendar, upstream_fixtures=(descriptor, source))
    assert result.value is not None and result.value.available_at is None


def test_damaged_retained_calendar_is_rejected_without_hostile_equality():
    """Copied nested leaves never replace actual re-admitted source bytes."""
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality was called")

    source, descriptor, fixture, calendar = owners()
    altered = copy(calendar)
    member = copy(calendar.member)
    object.__setattr__(member, "raw_body_bytes", Hostile())
    object.__setattr__(altered, "member", member)
    gap = next(m for m in fixture.members if m.record_id == "gap")
    result = normalize_activation_gap(gap.decode_raw_body(), fixture=fixture,
        record_id="gap", calendar=altered, upstream_fixtures=(descriptor, source))
    assert result.value is None and result.rejection.code == "retained_content_mismatch"
    missing = copy(calendar)
    object.__delattr__(missing, "member")
    result = normalize_activation_gap(gap.decode_raw_body(), fixture=fixture,
        record_id="gap", calendar=missing, upstream_fixtures=(descriptor, source))
    assert result.value is None and result.rejection.code == "retained_content_mismatch"
    nested = copy(calendar)
    object.__setattr__(nested, "sessions", None)
    result = normalize_activation_gap(gap.decode_raw_body(), fixture=fixture,
        record_id="gap", calendar=nested, upstream_fixtures=(descriptor, source))
    assert result.value is None and result.rejection.code == "retained_content_mismatch"


def test_damaged_retained_forecast_owner_is_bounded():
    """The source block cannot inherit authority from a damaged C record."""
    bundle = bundle_result("fixed-schedule").value
    assert bundle is not None
    block = bundle.evaluation_block
    assert block is not None
    altered = copy(bundle.calibration_metadata)
    object.__delattr__(altered, "member")
    fixture = admitted("p14b-schedule-v1")
    member = next(m for m in fixture.members if m.record_id == "block")
    result = normalize_evaluation_block(member.decode_raw_body(), fixture=fixture,
        record_id="block", calendar=block.calendar, model=bundle.model,
        spec=bundle.spec, normalization=bundle.supplied_normalization,
        partition=bundle.calibration_partition, metadata=altered,
        upstream_fixtures=block.upstream_fixtures)
    assert result.value is None and result.rejection.code == "retained_content_mismatch"
    for name, original, field in (("partition", bundle.calibration_partition, "calendar"),
                                  ("metadata", bundle.calibration_metadata, "partition")):
        damaged = copy(original)
        object.__setattr__(damaged, field, None)
        result = normalize_evaluation_block(member.decode_raw_body(), fixture=fixture,
            record_id="block", calendar=block.calendar, model=bundle.model,
            spec=bundle.spec, normalization=bundle.supplied_normalization,
            partition=damaged if name == "partition" else bundle.calibration_partition,
            metadata=damaged if name == "metadata" else bundle.calibration_metadata,
            upstream_fixtures=block.upstream_fixtures)
        assert result.value is None and result.rejection.code == "retained_content_mismatch"
    damaged = copy(bundle.calibration_partition)
    object.__setattr__(damaged, "frozen_threshold_return", Decimal("sNaN"))
    result = normalize_evaluation_block(member.decode_raw_body(), fixture=fixture,
        record_id="block", calendar=block.calendar, model=bundle.model,
        spec=bundle.spec, normalization=bundle.supplied_normalization,
        partition=damaged, metadata=bundle.calibration_metadata,
        upstream_fixtures=block.upstream_fixtures)
    assert result.value is None and result.rejection.code == "retained_content_mismatch"
