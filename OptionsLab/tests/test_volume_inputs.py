"""Actual registered training membership, causal omission and identity checks."""

from datetime import datetime, timezone
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
import pytest
from options_lab.admission import verify_fixture_bundle
from options_lab.observations import normalize_observation_meta
from options_lab.session_inputs import normalize_exchange_session
from options_lab.bar_inputs import normalize_underlying_bar


AT = datetime(2026, 9, 3, tzinfo=timezone.utc)
FIXTURE_ID = "p10a-volume-training-v1"


def admitted():
    """Use actual retained bytes through the unmodified admission boundary."""
    result = verify_fixture_bundle(
        FIXTURE_ID, (Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")).read_bytes(),
        event_id="volume-input-test", received_at=AT, raw_ref="synthetic://volume-input-test",
    )
    assert result.value is not None, result.rejection
    return result.value


def test_actual_registered_partition_has_bound_identity_and_selected_inputs():
    """A copied root or arbitrary typed partition must not replace admission."""
    from options_lab import volume_inputs
    manifest = admitted()
    member = next(m for m in manifest.members if m.record_id == "training")
    result = volume_inputs.normalize_volume_partition(member.decode_raw_body(), manifest=manifest, record_id="training")
    assert result.rejection is None
    assert result.value.input_manifest_hash == manifest.payload_sha256
    assert result.value.raw_hash == member.raw_hash
    assert len(result.value.training_sessions) == len(result.value.training_inputs) == 20


def inputs(name="training"):
    """Normalize supplied typed groups independently through existing owners."""
    from options_lab.volume_inputs import TrainingSessionBars, normalize_volume_partition
    manifest = admitted()
    members = {m.record_id: m for m in manifest.members}
    partition = normalize_volume_partition(members[name].decode_raw_body(), manifest=manifest, record_id=name).value
    groups = []
    for session_id, bar_ids in partition.training_inputs:
        member = members.get(session_id)
        if member is None or member.kind != "exchange_session":
            continue
        env = member.decode_envelope()
        kwargs = dict(event_id=env["event_id"], received_at=datetime.fromisoformat(env["simulated_received_at"].replace("Z", "+00:00")), raw_ref=env["raw_ref"])
        session = normalize_exchange_session(member.decode_raw_body(), **kwargs).value
        bars = []
        for record in bar_ids:
            member = members.get(record)
            if member is None or member.kind != "underlying_bar":
                continue
            env = member.decode_envelope()
            kwargs = dict(event_id=env["event_id"], received_at=datetime.fromisoformat(env["simulated_received_at"].replace("Z", "+00:00")), raw_ref=env["raw_ref"])
            meta = normalize_observation_meta(env["metadata"], **kwargs).value
            if meta is not None:
                bar = normalize_underlying_bar(member.decode_raw_body(), meta=meta, event_id=env["event_id"], receive_sequence=env["receive_sequence"]).value
                if bar is not None:
                    bars.append(bar)
        groups.append(TrainingSessionBars(session, tuple(bars)))
    return manifest, partition, tuple(groups)


def bound(name="training", *, cutoff=AT):
    """Call the actual public binder on independently normalized declared inputs."""
    from options_lab.volume_inputs import bind_volume_training_inputs
    manifest, partition, groups = inputs(name)
    return bind_volume_training_inputs(groups, cutoff, partition=partition, manifest=manifest)


def test_complete_volume_population_ignores_missing_price_vwap_and_audits_omissions():
    result = bound()
    assert result.reasons == ()
    assert len(result.training_input_hash) == 64
    rows = result.selected_inputs
    assert len(rows) == 60
    assert sum(row.minute_index == 35 and not row.omission_reasons for row in rows) == 20
    assert [row.omission_reasons for row in rows if row.omission_reasons] == [
        ("volume_missing",), ("volume_definition_unknown",),
    ]
    assert sum(row.bar.volume == 0 for row in rows) == 5


@pytest.mark.parametrize("name,reason", [
    ("heldout", "session_not_training"), ("nonmember", "session_not_training"),
    ("wrong-definition", "volume_definition_mismatch"), ("raw-volume", "normalization_failed"),
    ("wrong-source", "profile_mismatch"), ("future-bar", "available_after_decision"),
    ("fill-forward", "fill_forward"), ("quality", "quality_flags_present"),
    ("adjusted", "price_basis_not_raw"), ("self-revision", "revision_self_supersession"),
    ("conflicting-revision", "session_minute_conflict"), ("other-stream", "volume_stream_mismatch"),
    ("future-calendar", "session_available_after_now"), ("missing-calendar", "session_availability_unknown"),
    ("early-close", "session_not_regular"), ("wrong-calendar-profile", "profile_mismatch"),
    ("unknown-session", "unknown_member"), ("wrong-member-kind", "member_kind_mismatch"),
    ("unknown-bar", "unknown_member"), ("wrong-bar-kind", "member_kind_mismatch"),
    ("source-identity", "source_identity_conflict"), ("calendar-conflict", "session_identity_conflict"),
    ("calendar-source-identity", "source_identity_conflict"),
    ("unsupported-amount", "decimal_representation_unsupported"),
])
def test_actual_admitted_adverse_inputs_fail_whole_binding(name, reason):
    result = bound(name)
    assert reason in result.reasons
    assert result.training_input_hash is None


def test_supplied_permutation_duplicates_and_actual_receipt_equivalence_are_canonical():
    from options_lab.volume_inputs import bind_volume_training_inputs
    manifest, partition, groups = inputs("duplicate")
    first = bind_volume_training_inputs(groups, AT, partition=partition, manifest=manifest)
    shuffled = tuple(replace(g, bars=tuple(reversed(g.bars)) + g.bars) for g in reversed(groups))
    second = bind_volume_training_inputs(shuffled + shuffled[:1], AT, partition=partition, manifest=manifest)
    assert first.reasons == second.reasons == ()
    assert first.training_input_hash == second.training_input_hash
    assert len(first.selected_inputs) == len(second.selected_inputs) == 60
    assert len(first.member_evidence) == 81


@pytest.mark.parametrize("mutation", ["drop", "extra", "receipt", "session", "revision"])
def test_caller_substitution_or_silent_selected_member_omission_is_rejected(mutation):
    from options_lab.volume_inputs import bind_volume_training_inputs
    manifest, partition, groups = inputs()
    group = groups[0]
    if mutation == "drop":
        group = replace(group, bars=group.bars[1:])
    elif mutation == "session":
        group = replace(group, session=replace(group.session, source_version="wrong"))
    else:
        bar = group.bars[0]
        if mutation == "receipt":
            bar = replace(bar, meta=replace(bar.meta, raw_ref="unregistered"))
        elif mutation == "revision":
            bar = replace(bar, revision_id="new")
        else:
            bar = replace(bar, volume=Decimal(123))
        group = replace(group, bars=(bar, *group.bars[1:]))
    result = bind_volume_training_inputs((group, *groups[1:]), AT, partition=partition, manifest=manifest)
    assert result.reasons and result.training_input_hash is None


@pytest.mark.parametrize("field,value,code", [
    ("schema_version", True, "invalid_type"), ("schema_version", 2, "invalid_value"),
    ("partition_id", None, "invalid_type"), ("partition_id", "", "invalid_value"),
    ("training_sessions", (), "invalid_type"), ("training_sessions", [True], "invalid_type"),
    ("training_sessions", ["2026-02-30"], "invalid_date"),
    ("training_sessions", ["2026-08-06", "2026-08-06"], "invalid_value"),
    ("training_sessions", ["2026-08-07", "2026-08-06"], "invalid_value"),
    ("validation_sessions", ["2026-08-06"], "invalid_value"),
    ("test_sessions", ["2025-01-01"], "invalid_value"),
    ("training_inputs", None, "invalid_type"),
    ("training_inputs", [{"session_record_id": "s"}], "missing"),
    ("training_inputs", [{"session_record_id": "s", "bar_record_ids": [True]}], "invalid_type"),
    ("volume_definition_id", "wrong", "member_content_mismatch"),
])
def test_total_partition_shape_and_scalar_failures_are_safe(field, value, code):
    from options_lab.volume_inputs import normalize_volume_partition
    manifest = admitted()
    raw = next(m for m in manifest.members if m.record_id == "training").decode_raw_body()
    raw[field] = value
    result = normalize_volume_partition(raw, manifest=manifest, record_id="training")
    assert result.value is None and result.rejection.code == code
    assert result.rejection.record_id == "training"
    assert result.rejection.event_id == "event-training"


class Hostile:
    """This class represents a leaf whose equality or conversion must never execute."""

    def __eq__(self, other):
        """Reject any unexpected equality invocation."""
        raise AssertionError("hostile equality ran")

    def __str__(self):
        """Reject any unexpected string conversion."""
        raise AssertionError("hostile conversion ran")


@pytest.mark.parametrize("location", ["root", "date", "session", "bar", "version", "definition"])
def test_nested_hostile_raw_values_never_reach_equality(location):
    from options_lab.volume_inputs import normalize_volume_partition
    manifest = admitted()
    raw = next(m for m in manifest.members if m.record_id == "training").decode_raw_body()
    if location == "root":
        raw = Hostile()
    elif location == "date":
        raw["training_sessions"][0] = Hostile()
    elif location in ("version", "definition"):
        raw["schema_version" if location == "version" else "volume_definition_id"] = Hostile()
    elif location == "session":
        raw["training_inputs"][0]["session_record_id"] = Hostile()
    else:
        raw["training_inputs"][0]["bar_record_ids"][0] = Hostile()
    result = normalize_volume_partition(raw, manifest=manifest, record_id="training")
    assert result.value is None


def test_unknown_or_wrong_partition_member_retains_actual_manifest_receipt():
    from options_lab.volume_inputs import normalize_volume_partition
    manifest = admitted()
    raw = next(m for m in manifest.members if m.record_id == "training").decode_raw_body()
    for record, code in (("absent", "unknown_member"), ("session-2026-08-06", "member_kind_mismatch")):
        result = normalize_volume_partition(raw, manifest=manifest, record_id=record)
        assert result.rejection.code == code
        assert result.rejection.manifest is manifest


def test_original_calendar_close_boundary_and_cutoff_hash_are_exact():
    from datetime import timedelta
    close = datetime(2026, 9, 2, 20, tzinfo=timezone.utc)
    passed = bound(cutoff=close)
    failed = bound(cutoff=close - timedelta(microseconds=1))
    assert passed.reasons == ()
    assert "training_session_incomplete" in failed.reasons
    assert failed.training_input_hash is None
    assert passed.training_input_hash != bound().training_input_hash


def test_partial_and_empty_training_groups_do_not_invent_volume_samples():
    result = bound("empty-bars")
    assert result.reasons == ()
    assert len(result.normalized_groups) == 1
    assert result.selected_inputs == ()
    assert result.training_input_hash is not None
    omitted = bound("all-omitted")
    assert omitted.reasons == () and omitted.training_input_hash is not None
    assert len(omitted.normalized_groups) == len(omitted.selected_inputs) == 2
    assert all(row.omission_reasons for row in omitted.selected_inputs)


def test_artifact_members_are_explicitly_rejected_as_market_inputs():
    from test_context import build
    result = build(["training"], at=AT, bundle=admitted())
    assert "artifact_not_market_input" in {reason for row in result.components for reason in row.reasons}


def test_pinned_dst_sessions_share_actual_first_completed_minute_index():
    result = bound("dst")
    assert result.reasons == ()
    assert [row.minute_index for row in result.selected_inputs] == [1, 1]
    assert [row.bar.interval_end.hour for row in result.selected_inputs] == [14, 13]


def test_equivalent_admitted_receipt_can_close_declaration_without_unregistered_receipt():
    from options_lab.volume_inputs import bind_volume_training_inputs
    manifest, partition, groups = inputs("duplicate")
    original = bind_volume_training_inputs(groups, AT, partition=partition, manifest=manifest)
    # The duplicate's receipt is genuinely declared; either admitted copy closes
    # the economic observation while both declared receipts remain in evidence.
    reduced = tuple(replace(g, bars=tuple(b for b in g.bars if b.meta.raw_ref != "synthetic://duplicate")) for g in groups)
    result = bind_volume_training_inputs(reduced, AT, partition=partition, manifest=manifest)
    assert result.reasons == ()
    assert result.training_input_hash == original.training_input_hash
    assert result.member_evidence == original.member_evidence


@pytest.mark.parametrize("precision", [2, 120])
def test_ambient_decimal_context_never_changes_economic_input_hash(precision):
    from decimal import Inexact, Rounded, ROUND_UP, localcontext
    expected = bound()
    with localcontext() as context:
        context.prec, context.rounding = precision, ROUND_UP
        context.Emin, context.Emax, context.clamp = -2, 2, 1
        context.traps[Inexact] = context.traps[Rounded] = True
        assert bound() == expected


def test_changed_raw_payload_is_rejected_even_after_rehashing_member_body():
    import hashlib
    import json
    from options_lab.admission import _canonical_bytes
    raw = json.loads((Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")).read_bytes())
    row = next(m for m in raw["members"] if m["record_id"] == "training")
    row["raw_body"]["training_inputs"] = []
    row["raw_hash"] = hashlib.sha256(_canonical_bytes(row["raw_body"])).hexdigest()
    result = verify_fixture_bundle(FIXTURE_ID, json.dumps(raw).encode(), event_id="tampered", received_at=AT, raw_ref="tampered")
    assert result.value is None and result.rejection.code == "hash_mismatch"


def test_foreign_manifest_cannot_bind_the_admitted_partition():
    from test_context import manifest as other_manifest
    from options_lab.volume_inputs import bind_volume_training_inputs
    _, partition, groups = inputs()
    result = bind_volume_training_inputs(groups, AT, partition=partition, manifest=other_manifest())
    assert "partition_manifest_mismatch" in result.reasons
    assert result.training_input_hash is None


def test_factory_outcomes_are_immutable_and_cannot_accept_caller_success_hashes():
    from dataclasses import FrozenInstanceError
    from options_lab import volume_inputs as api
    result = bound()
    with pytest.raises(FrozenInstanceError):
        result.training_input_hash = "0" * 64
    with pytest.raises(FrozenInstanceError):
        result.partition.training_sessions = ()
    for cls in (api.VolumePartition, api.VolumePartitionValidation, api.VolumeInputRejection,
                api.VolumeTrainingInputEvidence, api.VolumeTrainingInputResult):
        with pytest.raises(TypeError):
            cls()
    manifest, partition, groups = inputs()
    for supplied, cutoff, declared, actual in ((list(groups), AT, partition, manifest),
                                               (groups, AT, None, manifest),
                                               (groups, AT, partition, None)):
        with pytest.raises(TypeError):
            api.bind_volume_training_inputs(supplied, cutoff, partition=declared, manifest=actual)
    with pytest.raises(ValueError):
        api.bind_volume_training_inputs(groups, AT.replace(tzinfo=None), partition=partition, manifest=manifest)
    assert result.manifest.origin == "synthetic" and result.manifest.fidelity_tier == 0
    assert result.manifest.permitted_use == "core_fixture"
    assert not result.manifest.operational_allowed and not result.manifest.economic_allowed
