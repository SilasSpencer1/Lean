"""Frozen artifacts must recompute actual upstream inputs before granting availability."""

from datetime import datetime, timezone


def test_normalization_factory_is_a_total_owned_boundary():
    from options_lab.volume_normalization import normalize_feature_normalization
    from test_volume_inputs import admitted
    manifest = admitted()
    result = normalize_feature_normalization(None, manifest=manifest, record_id="training",
                                            training_manifest=manifest,
                                            decision_at=datetime(2026, 9, 4, tzinfo=timezone.utc))
    assert result.value is result.fit is None
    assert result.rejection.field == "$"
    assert result.rejection.code == "expected_exact_dict"


from pathlib import Path
import pytest
from options_lab.admission import verify_fixture_bundle
from options_lab.volume_inputs import normalize_volume_partition, _declared_inputs, bind_volume_training_inputs
from options_lab.volume import fit_volume_baseline

AT = datetime(2026, 9, 3, tzinfo=timezone.utc)
DECISION = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def admitted(fixture_id):
    """Read genuine registered bytes; never manufacture a successful manifest."""
    result = verify_fixture_bundle(fixture_id, (Path(__file__).parent / "fixtures" / (fixture_id + ".json")).read_bytes(),
                                   event_id="p10b-test", received_at=DECISION, raw_ref="synthetic://p10b-test")
    assert result.value is not None, result.rejection
    return result.value


def source_fit(record_id):
    """Use the shared actual member materialization and actual fitter."""
    manifest = admitted("p10b-volume-omissions-v1")
    member = next(m for m in manifest.members if m.record_id == record_id)
    partition = normalize_volume_partition(member.decode_raw_body(), manifest=manifest, record_id=record_id).value
    groups, _ = _declared_inputs(partition, manifest, AT)
    return fit_volume_baseline(groups, AT, partition=partition, manifest=manifest)


@pytest.mark.parametrize("name", ["boolean", "negative", "nonfinite", "unknown-definition"])
def test_actual_registered_invalid_volume_alone_is_an_omitted_hashed_observation(name):
    result = source_fit("case-" + name)
    assert result.reasons == () and result.baseline is not None
    assert result.baseline.buckets == ()
    assert len(result.inputs.selected_inputs) == 1
    row = result.inputs.selected_inputs[0]
    assert row.bar is row.assessment is None and row.observation is not None
    assert row.rejection.field == "volume" and row.omission_reasons[0] == "raw_volume_invalid"
    assert row.economic_hash is not None and result.inputs.training_input_hash is not None


@pytest.mark.parametrize("name,reason", [
    ("case-missing-volume", "normalization_failed"), ("case-hidden-revision", "normalization_failed"),
    ("case-hidden-definition", "normalization_failed"), ("case-hidden-vwap", "normalization_failed"),
    ("case-huge-close", "normalization_failed"), ("case-wrong-definition", "volume_definition_mismatch"),
    ("case-self-revision", "revision_self_supersession"), ("case-adjusted", "price_basis_not_raw"),
    ("case-future", "available_after_decision"), ("case-wrong-source", "profile_mismatch"),
    ("case-fill-forward", "fill_forward"), ("case-quality", "quality_flags_present"),
    ("case-duration", "interval_duration_not_one_minute"), ("case-alignment", "interval_not_minute_aligned"),
    ("case-outside", "interval_outside_session"), ("case-wrong-day", "session_wrong_date"),
    ("heldout", "session_not_training"), ("minute-conflict", "session_minute_conflict"),
    ("source-conflict", "source_identity_conflict"), ("future-calendar", "session_available_after_now"),
])
def test_raw_volume_failure_cannot_mask_nonvolume_causal_or_identity_failures(name, reason):
    result = source_fit(name)
    assert result.baseline is None and result.inputs.training_input_hash is None
    assert reason in result.reasons
    rows = [r for r in result.inputs.member_evidence if r.rejection is not None]
    assert rows and rows[0].bar is rows[0].assessment is None
    if "hidden-" in name or name == "case-huge-close":
        assert rows[0].additional_failure is not None
        assert rows[0].rejection.field == "volume"


def test_raw_economic_redelivery_dedupes_and_never_becomes_calendar_evidence():
    result = source_fit("duplicate")
    assert result.reasons == () and len(result.inputs.selected_inputs) == 1
    rows = [r for r in result.inputs.member_evidence if r.member.kind == "underlying_bar"]
    assert len(rows) == 2 and rows[0].economic_hash == rows[1].economic_hash
    shuffled = bind_volume_training_inputs(tuple(reversed(result.inputs.normalized_groups)) * 2, AT,
                                           partition=result.inputs.partition, manifest=result.inputs.manifest)
    assert shuffled.training_input_hash == result.inputs.training_input_hash


from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Rounded, ROUND_UP, localcontext
import hashlib
import json
from options_lab.volume_normalization import normalize_feature_normalization


def artifact(name="good", *, decision=DECISION):
    """Evaluate an actual B member against its explicitly supplied actual upstream A."""
    manifest = admitted("p10b-volume-normalization-v1")
    member = next(m for m in manifest.members if m.record_id == name)
    raw = member.decode_raw_body()
    fixture_id = raw["training_fixture_id"] if name != "wrong-fixture" else "p10a-volume-training-v1"
    upstream = admitted(fixture_id)
    return normalize_feature_normalization(raw, manifest=manifest, record_id=name,
                                           training_manifest=upstream, decision_at=decision)


def test_registered_frozen_artifact_recomputes_population_and_actual_dependency_chain():
    result = artifact()
    assert result.rejection is None and result.reasons == () and result.value is not None
    baseline = result.value.baseline
    assert result.fit.baseline is baseline
    assert result.manifest.fixture_id == "p10b-volume-normalization-v1"
    assert baseline.manifest is result.training_manifest
    assert baseline.manifest.fixture_id == "p10a-volume-training-v1"
    buckets = {b.minute_index: b for b in baseline.buckets}
    assert (buckets[35].sample_count, buckets[35].mean, buckets[35].population_stddev) == (20, Decimal(1000), Decimal(100))
    assert buckets[34].population_stddev == Decimal("1.118033988749894848204586834365638")
    assert not buckets[33].ready
    assert result.value.available_at == datetime(2026, 9, 3, 1, tzinfo=timezone.utc)
    assert result.manifest.origin == baseline.manifest.origin == "synthetic"
    assert result.manifest.permitted_use == baseline.manifest.permitted_use == "core_fixture"


@pytest.mark.parametrize("name", ["raw-omission", "empty", "all-raw-omitted", "numeric"])
def test_frozen_artifact_uses_same_complete_binder_and_stored_statistics(name):
    result = artifact(name)
    assert result.value is not None and result.fit.reasons == ()
    assert result.fit.inputs.training_input_hash is not None
    if name in ("empty", "all-raw-omitted"):
        assert result.value.baseline.buckets == ()
    if name == "raw-omission":
        buckets = {b.minute_index: b for b in result.value.baseline.buckets}
        assert buckets[35].sample_count == 19 and not buckets[35].ready and buckets[34].ready
    if name == "numeric":
        bucket = next(b for b in result.value.baseline.buckets if b.minute_index == 3)
        assert bucket.mean == Decimal("1.333333333333333333333333333333333")


@pytest.mark.parametrize("name,reason,reached", [
    ("unknown-availability", "availability_unknown", True),
    ("future-availability", "available_after_decision", True),
    ("precutoff", "availability_before_cutoff", True),
    ("wrong-root", "training_manifest_mismatch", False),
    ("wrong-fixture", "training_manifest_mismatch", False),
    ("unknown-partition", "partition_unknown_member", False),
    ("wrong-kind", "partition_member_kind_mismatch", False),
    ("failed-binding", "session_not_training", True),
    ("arithmetic-failure", "arithmetic_precision_unsupported", True),
    ("wrong-hash", "baseline_content_hash_mismatch", True),
    ("wrong-cutoff", "baseline_snapshot_mismatch", True),
    *(("tamper-" + name, "baseline_snapshot_mismatch", True) for name in (
        "mean", "population_stddev", "sample_count", "contributing_sessions", "reasons",
        "normalization_id", "numeric_id", "training_input_hash")),
])
def test_actual_registered_adverse_artifacts_retain_only_reached_fit(name, reason, reached):
    result = artifact(name)
    assert result.value is None and result.rejection is None
    assert reason in result.reasons
    assert (result.fit is not None) == reached
    if name == "arithmetic-failure":
        assert result.fit.baseline is None and result.fit.inputs.training_input_hash is not None
        assert result.fit.buckets and result.fit.buckets[-1].mean is None
    if name == "failed-binding":
        assert result.fit.baseline is None and result.fit.inputs.training_input_hash is None


@pytest.mark.parametrize("name,field,code", [
    ("missing-availability", "available_at", "missing"),
    ("malformed-time", "available_at", "invalid_timestamp"),
    ("assumed-basis", "availability_basis", "profile_mismatch"),
    ("invalid-basis", "availability_basis", "invalid_value"),
    ("profile-mismatch", "record_id", "profile_mismatch"),
    ("superseded", "record_id", "artifact_not_immutable"),
])
def test_registered_raw_or_member_failures_retain_safe_envelope_without_a_fit(name, field, code):
    result = artifact(name)
    assert result.value is result.fit is None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    assert result.rejection.event_id == "event-" + name
    assert result.rejection.raw_ref == "synthetic://normalization/" + name
    assert result.rejection.manifest is result.manifest
    assert result.rejection.stage == "feature_normalization"


@pytest.mark.parametrize("name", ["at-cutoff", "at-decision"])
def test_exact_availability_boundaries_pass(name):
    result = artifact(name)
    assert result.value is not None and result.reasons == ()


def test_assessment_decision_never_enters_frozen_artifact_semantic_identity():
    original = artifact()
    later = artifact(decision=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert original.value.content_hash == later.value.content_hash
    assert original.value.snapshot == later.value.snapshot
    early = artifact(decision=datetime(2026, 9, 3, 0, 59, 59, tzinfo=timezone.utc))
    assert early.value is None and early.fit.baseline is not None
    assert early.reasons == ("available_after_decision",)
    snapshot = original.value.snapshot
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == original.value.content_hash
    assert original.manifest.payload_sha256 not in encoded.decode()
    assert "content_hash" not in snapshot and "decision_at" not in snapshot
    snapshot["available_at"] = "changed"
    assert original.value.snapshot != snapshot
    with pytest.raises(FrozenInstanceError):
        original.value.available_at = DECISION


class Hostile:
    """This class raises if a raw leaf is compared or stringified before validation."""

    def __eq__(self, other):
        raise AssertionError("raw equality executed")

    def __str__(self):
        raise AssertionError("raw string conversion executed")

    def __repr__(self):
        raise AssertionError("raw repr executed")


class ListSubclass(list):
    """This class represents a non-exact container that must be rejected."""


class DictSubclass(dict):
    """This class represents a non-exact mapping that must be rejected."""


def _raw_call(raw, *, record_id="good", use_b_as_a=False):
    """Submit hostile caller body while retaining genuine trusted A/B evidence."""
    manifest = admitted("p10b-volume-normalization-v1")
    upstream = manifest if use_b_as_a else admitted("p10a-volume-training-v1")
    return normalize_feature_normalization(raw, manifest=manifest, record_id=record_id,
                                           training_manifest=upstream, decision_at=DECISION)


def _good_raw():
    return next(m for m in admitted("p10b-volume-normalization-v1").members if m.record_id == "good").decode_raw_body()


@pytest.mark.parametrize("path,value,code", [
    (("schema_version",), True, "invalid_type"),
    (("schema_version",), 2, "invalid_value"),
    (("training_fixture_id",), Hostile(), "invalid_type"),
    (("training_payload_sha256",), "X" * 64, "invalid_value"),
    (("cutoff",), "2026-09-03T00:00:00", "invalid_timestamp"),
    (("available_at",), Hostile(), "invalid_type"),
    (("baseline_snapshot",), DictSubclass(), "expected_exact_dict"),
    (("baseline_snapshot", "record_kind"), Hostile(), "invalid_type"),
    (("baseline_snapshot", "schema_version"), True, "invalid_type"),
    (("baseline_snapshot", "normalization_id"), Hostile(), "invalid_type"),
    (("baseline_snapshot", "partition"), ListSubclass(), "expected_exact_dict"),
    (("baseline_snapshot", "partition", "training_sessions"), ListSubclass(), "invalid_type"),
    (("baseline_snapshot", "partition", "training_sessions", 0), Hostile(), "invalid_type"),
    (("baseline_snapshot", "buckets"), (), "invalid_type"),
    (("baseline_snapshot", "buckets", 0), DictSubclass(), "expected_exact_dict"),
    (("baseline_snapshot", "buckets", 0, "minute_index"), True, "invalid_type"),
    (("baseline_snapshot", "buckets", 0, "sample_count"), Hostile(), "invalid_type"),
    (("baseline_snapshot", "buckets", 0, "sample_count"), 10 ** 1000, "invalid_value"),
    (("baseline_snapshot", "buckets", 0, "mean"), Hostile(), "invalid_type"),
    (("baseline_snapshot", "buckets", 0, "mean"), "NaN", "invalid_decimal"),
    (("baseline_snapshot", "buckets", 0, "population_stddev"), "-1", "invalid_value"),
    (("baseline_snapshot", "buckets", 0, "mean"), "1" + "0" * 1001, "decimal_representation_unsupported"),
    (("baseline_snapshot", "buckets", 0, "contributing_sessions", 0), Hostile(), "invalid_type"),
    (("baseline_snapshot", "buckets", 0, "reasons", 0), Hostile(), "invalid_type"),
])
def test_nested_raw_schema_is_total_before_hostile_leaf_equality(path, value, code):
    raw = _good_raw()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = _raw_call(raw)
    assert result.value is result.fit is None
    assert result.rejection.code == code


@pytest.mark.parametrize("path", [(), ("baseline_snapshot",), ("baseline_snapshot", "partition"),
                                    ("baseline_snapshot", "buckets", 0)])
def test_all_nested_shapes_reject_missing_and_unknown_fields_before_comparison(path):
    raw = _good_raw()
    target = raw
    for key in path:
        target = target[key]
    target["unexpected"] = Hostile()
    result = _raw_call(raw)
    assert result.rejection.code == "unknown_fields" and result.fit is None
    del target["unexpected"]
    del target[next(iter(target))]
    result = _raw_call(raw)
    assert result.rejection.code == "missing" and result.fit is None


def test_caller_substitution_unknown_member_wrong_kind_and_containing_root_fail_closed():
    raw = _good_raw()
    raw["baseline_snapshot"]["buckets"][-1]["mean"] = "1001"
    result = _raw_call(raw)
    assert result.rejection.code == "member_content_mismatch" and result.fit is None
    result = _raw_call(_good_raw(), record_id="absent")
    assert result.rejection.code == "unknown_member" and result.rejection.event_id == "p10b-test"
    result = _raw_call(_good_raw(), use_b_as_a=True)
    assert "training_manifest_is_artifact" in result.reasons and result.fit is None
    from test_volume_inputs import admitted as training_admitted
    upstream = training_admitted()
    result = normalize_feature_normalization(_good_raw(), manifest=upstream, record_id="training",
                                             training_manifest=upstream, decision_at=DECISION)
    assert result.rejection.code == "member_kind_mismatch" and result.fit is None


def test_ambient_decimal_context_cannot_change_recomputed_artifact_identity():
    expected = artifact().value.content_hash
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_UP
        context.traps[Inexact] = context.traps[Rounded] = True
        result = artifact()
        assert result.value.content_hash == expected
        assert context.prec == 4 and not context.flags[Inexact] and not context.flags[Rounded]


def test_all_artifact_evidence_is_factory_derived():
    from options_lab import FeatureNormalization, FeatureNormalizationInputRejection, FeatureNormalizationResult
    for cls in (FeatureNormalization, FeatureNormalizationInputRejection, FeatureNormalizationResult):
        with pytest.raises(TypeError):
            cls()


def test_frozen_artifact_is_explicitly_inappropriate_as_market_context_input():
    from test_context import build
    result = build(["good"], at=DECISION, bundle=admitted("p10b-volume-normalization-v1"))
    assert "artifact_not_market_input" in {reason for row in result.components for reason in row.reasons}


@pytest.mark.parametrize("field,value,error", [
    ("manifest", object(), TypeError), ("training_manifest", object(), TypeError),
    ("record_id", True, TypeError), ("record_id", "", ValueError),
    ("decision_at", "now", TypeError), ("decision_at", datetime(2026, 9, 4), ValueError),
])
def test_trusted_artifact_arguments_remain_explicit_exact_inputs(field, value, error):
    kwargs = dict(manifest=admitted("p10b-volume-normalization-v1"), record_id="good",
                  training_manifest=admitted("p10a-volume-training-v1"), decision_at=DECISION)
    kwargs[field] = value
    with pytest.raises(error):
        normalize_feature_normalization(_good_raw(), **kwargs)
