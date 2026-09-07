"""Exercise admitted history and exact prior-state reuse through real P06."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from decimal import Decimal
import json

import pytest

from options_lab import admission
from options_lab.bar_inputs import BarInputRejection
from options_lab.features import FeatureState, update_features
from test_context import FIXTURES, build, component, manifest
from build_context_history_fixture import FIXTURE_ID


def instant(time):
    """Return the fixture's explicit UTC decision instant."""
    return datetime.fromisoformat("2026-09-08T" + time + "+00:00")


def history(ids, *, at="14:03:00", previous=None):
    """Build from actual registered history records and explicit prior state."""
    return build(ids, at=instant(at) if at != "bad" else at, bundle=manifest(FIXTURE_ID), previous=previous)


def prior(name="history", *, correction=False):
    """Reduce actually normalized records independently to seed incremental tests."""
    result = history(["history-S", name + "-B0"] + ([name + "-B0r2"] if correction else []), at="bad")
    session = component(result, "history-S").value
    state = FeatureState(session, as_of=instant("14:00:00"))
    for label in ["B0"] + (["B0r2"] if correction else []):
        bar = component(result, name + "-" + label).value
        state = update_features(state, bar, session, as_of=max(instant("14:02:00") if correction else instant("14:01:00"), bar.available_at)).next_state
    return state


def test_registered_history_profile_and_real_fresh_reducer():
    result = history(["history-S", "history-B0", "history-B0r2"], at="14:02:00")
    assert [u.outcome for u in result.feature_updates] == ["accepted", "corrected"]
    assert result.context.feature_state.bars[0].close_price == Decimal("651")
    assert result.context.feature_state.retired_bars == (component(result, "history-B0").value,)
    assert result.context.bars == result.context.feature_state.bars
    assert component(result, "history-B0r2").bar_identity.full_content_hash
    assert result.context.input_digest


def test_incremental_rebind_counts_selected_and_retired_history_without_requests():
    previous = prior(correction=True)
    result = history(["history-S", "history-B1"], previous=previous)
    assert [u.outcome for u in result.feature_updates] == ["accepted"]
    assert result.feature_updates[0].previous_state is previous
    assert len(result.context.bars) == 2
    assert result.context.feature_state.retired_bars == previous.retired_bars
    assert result.previous_feature_state is previous
    assert not result.context.input_reasons
    assert result.context.input_digest


@pytest.mark.parametrize("field", ["price", "volume", "vwap_numerator", "vwap_denominator"])
def test_bar_unit_missing_diagnostics_are_closed(field):
    profile = dict(profile_id="bars", kind="underlying_bar", source="bars", stream_id="bars", feed_class="realtime", fidelity="genuine", availability_basis="measured", units=dict(price="USD_per_share", volume="shares", vwap_numerator="USD", vwap_denominator="shares"), record_identity_rule="provider_record_id_and_revision_id")
    del profile["units"][field]
    from options_lab._input_parsing import _InvalidInput
    with pytest.raises((_InvalidInput, admission._AdmissionFailure)) as failure:
        admission._profiles([profile])
    assert failure.value.args == (f"modeled_source_profiles[0].units.{field}", "missing")
    admission.FixtureInputRejection("verify", instant("14:00:00"), "fixture", *failure.value.args)


@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("change", ["close", "sequence", "receipt"])
def test_every_retained_occurrence_requires_full_membership(retired, change):
    original = prior(correction=True)
    first, correction = original.retired_bars[0], original.bars[0]
    target = first if retired else correction
    altered = replace(target, close_price=Decimal("649")) if change == "close" else replace(target, receive_sequence=0 if retired else 20) if change == "sequence" else replace(target, meta=replace(target.meta, raw_ref="not-admitted"))
    first, correction = (altered, correction) if retired else (first, altered)
    state = FeatureState(original.session, as_of=instant("14:00:00"))
    state = update_features(state, first, state.session, as_of=instant("14:01:00")).next_state
    state = update_features(state, correction, state.session, as_of=instant("14:02:00")).next_state
    if retired:
        assert state.input_hash == original.input_hash
    result = history(["history-S", "history-B1"], previous=state)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert result.previous_feature_state is state and result.feature_updates == ()
    assert "previous_state_membership_missing" in result.context.input_reasons


@pytest.mark.parametrize("change", ["source_version", "provider_record_id", "raw_ref"])
def test_exact_session_rebinding_rejects_same_day_changed_session(change):
    original = prior()
    altered = replace(original.session, **{change: "not-admitted"})
    previous = FeatureState(altered, as_of=instant("14:01:00"))
    result = history(["history-S", "history-B0"], at="14:01:00", previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert result.previous_feature_state is previous


def test_prior_cutoff_regression_is_rejected_and_equality_is_allowed():
    previous = prior()
    regressed = history(["history-S", "history-B0"], at="14:00:59.999999", previous=previous)
    assert regressed.context.feature_state is None and regressed.context.input_digest is None
    equal = history(["history-S"], at="14:01:00", previous=previous)
    assert equal.context.feature_state is previous
    assert equal.feature_updates == ()


@pytest.mark.parametrize("name,extra,reason", [("cross", ["cross-other-B1"], "history_stream_mismatch"), ("ambiguous", [], "history_stream_ambiguous")])
def test_one_actual_registered_stream_is_required(name, extra, reason):
    previous = prior(name)
    result = history(["history-S", *extra], previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert result.feature_updates == ()
    assert reason in result.context.input_reasons


@pytest.mark.parametrize("name,outcome,reason", [("ordered", "accepted", None), ("tie", "rejected", "event_order_ambiguous"), ("unknown-seq", "rejected", "event_order_ambiguous"), ("backward", "rejected", "out_of_order")])
def test_prior_cursor_uses_actual_same_stream_order(name, outcome, reason):
    previous = prior(name)
    result = history(["history-S", name + "-B1"], previous=previous)
    assert len(result.feature_updates) == 1
    update = result.feature_updates[0]
    assert update.outcome == outcome
    if reason:
        assert update.update_reasons == (reason,)
        assert update.next_state is previous
        assert result.context.feature_state is previous
    else:
        assert len(result.context.bars) == 2


@pytest.mark.parametrize("name,outcome,reason", [("duplicate", "duplicate", "duplicate"), ("conflict", "rejected", "source_identity_conflict")])
def test_retired_redelivery_retains_exact_prior_state(name, outcome, reason):
    previous = prior(name, correction=True)
    result = history(["history-S", name + "-redelivery"], previous=previous)
    assert len(result.feature_updates) == 1
    assert result.feature_updates[0].outcome == outcome
    assert result.feature_updates[0].update_reasons == (reason,)
    assert result.feature_updates[0].next_state is previous
    assert result.context.feature_state is previous
    assert result.context.feature_state.as_of == instant("14:02:00")


def test_fresh_r2_does_not_seed_unrequested_inspected_predecessor():
    result = history(["history-S", "history-B0r2"], at="14:02:00")
    assert len(result.feature_updates) == 1
    assert result.feature_updates[0].outcome == "rejected"
    assert result.feature_updates[0].update_reasons == ("revision_lineage_mismatch",)
    assert result.context.bars == ()
    assert not component(result, "history-B0").requested


def test_omitted_causal_correction_invalidates_reuse_without_reset():
    previous = prior()
    result = history(["history-S"], at="14:02:00", previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert result.feature_updates == ()
    assert "incomplete_context_input_set" in component(result, "history-B0r2").reasons
    assert result.previous_feature_state is previous


@pytest.mark.parametrize("name,reason", [("missing", "close_price_missing"), ("zero", "close_price_nonpositive")])
def test_typed_bad_correction_keeps_reducer_price_history(name, reason):
    previous = prior(name)
    result = history(["history-S", name + "-B0r2"], previous=previous)
    update, = result.feature_updates
    assert update.outcome == "rejected" and reason in update.update_reasons
    assert update.next_state is previous and result.context.feature_state is previous
    assert result.context.bars == previous.bars
    assert reason in result.context.input_reasons


def test_raw_bad_correction_has_native_rejection_without_fabricated_update():
    previous = prior("raw")
    result = history(["history-S", "raw-B0r2"], previous=previous)
    assert result.feature_updates == ()
    assert any(type(r) is BarInputRejection and (r.field, r.code) == ("close_price", "invalid_type") for r in result.rejections)
    assert component(result, "raw-B0r2").value is None


@pytest.mark.parametrize("name", ["partial", "zero-vwap", "definitions"])
def test_partial_volume_and_vwap_never_erase_valid_close(name):
    result = history(["history-S", name + "-B0"])
    update, = result.feature_updates
    assert update.outcome == "accepted"
    assert result.context.bars[0].close_price == Decimal("650")
    if name == "partial":
        assert update.assessment.volume_reasons and update.assessment.vwap_reasons
    if name == "zero-vwap":
        assert update.assessment.exact_vwap_available


def test_future_just_ended_minute_does_not_change_causal_state_or_digest():
    previous = prior("future")
    earlier = history(["history-S"], at="14:02:00", previous=previous)
    requested = history(["history-S", "future-B1"], at="14:02:00", previous=previous)
    assert requested.context.feature_state is previous
    assert requested.feature_updates == ()
    assert requested.context.input_digest == earlier.context.input_digest
    assert requested.context.bars[-1].interval_end == instant("14:01:00")
    assert component(requested, "future-B1").disposition == "future"


def test_gap_remains_visible_and_late_backfill_is_rejected():
    result = history(["history-S", "gap-B0", "gap-B2"], at="14:03:00")
    previous = result.context.feature_state
    assert [b.interval_start for b in previous.bars] == [instant("14:00:00"), instant("14:02:00")]
    result = history(["history-S", "gap-B1"], at="14:04:00", previous=previous)
    assert result.feature_updates[0].update_reasons == ("late_backfill",)
    assert result.context.feature_state is previous


@pytest.mark.parametrize("name", ["body-lineage", "shifted", "missing-link", "profile"])
def test_invalid_member_body_correspondence_cannot_authorize_history(name):
    result = history(["history-S", name + "-B0", name + "-B0r2"])
    assert result.context.feature_state is None
    assert ("profile_mismatch" if name == "profile" else "lineage_invalid") in result.context.input_reasons


def test_unknown_availability_omission_is_unresolved_not_future():
    previous = prior("unknown")
    result = history(["history-S"], previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert "availability_unknown" in component(result, "unknown-B0r2").reasons


def test_no_session_or_valid_header_means_no_reducer():
    for ids, at in [(["history-B0"], "14:01:00"), (["history-S", "history-B0"], "bad")]:
        result = history(ids, at=at)
        assert result.context is None or result.context.feature_state is None
        assert result.feature_updates == ()
        assert component(result, "history-B0").value is not None


def test_unsupported_bar_identity_stays_bounded_owner_evidence():
    result = history(["history-S", "huge-B0"])
    update, = result.feature_updates
    assert update.outcome == "rejected"
    assert component(result, "huge-B0").bar_identity.economic_content_hash is None
    assert result.context.bars == ()
    assert update.update_reasons


def test_same_time_retired_redelivery_keeps_declared_original_parent_order():
    previous = prior("same-time-duplicate", correction=True)
    assert previous.last_event_order == (instant("14:03:00"), 5)
    result = history(["history-S", "same-time-duplicate-A-redelivery"], at="14:04:00", previous=previous)
    assert result.context.feature_state is previous
    assert result.feature_updates[0].outcome == "duplicate"
    assert result.feature_updates[0].next_state is previous
    assert "lineage_unorderable" not in result.context.input_reasons
    assert result.context.input_digest


def test_omitted_intermediate_correction_invalidates_prior_reuse():
    previous = prior("chain")
    result = history(["history-S", "chain-B0r3"], previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert result.feature_updates == ()
    assert "incomplete_context_input_set" in component(result, "chain-B0r2").reasons


def test_future_malformed_correction_cannot_change_prior_digest():
    previous = prior("future-correction")
    original = history(["history-S"], previous=previous)
    result = history(["history-S", "future-correction-B0r2"], previous=previous)
    assert result.context.feature_state is previous
    assert result.feature_updates == ()
    assert result.context.input_digest == original.context.input_digest
    assert component(result, "future-correction-B0r2").disposition == "future"
    assert component(result, "future-correction-B0r2").rejection_indexes


def test_unrepresentable_full_bar_identity_cannot_authorize_prior_digest():
    previous = prior("huge-sequence")
    assert previous.bars and previous.input_hash
    result = history(["history-S"], previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    row = component(result, "huge-sequence-B0")
    assert row.bar_identity.full_content_hash is None and row.bar_identity.economic_content_hash
    assert row.bar_identity.full_reasons


def test_prior_cutoff_changes_digest_even_when_selected_input_hash_matches():
    original = prior("partial")
    earlier = FeatureState(original.session, as_of=instant("14:00:00"))
    later = FeatureState(original.session, as_of=instant("14:00:01"))
    assert earlier.input_hash == later.input_hash
    a = history(["history-S"], previous=earlier)
    b = history(["history-S"], previous=later)
    assert a.context.input_digest != b.context.input_digest
    assert a.context.feature_state is earlier and b.context.feature_state is later


def test_invalid_prior_does_not_implicitly_reset_good_requested_history():
    previous = prior()
    invalid = FeatureState(replace(previous.session, raw_ref="not-member"), as_of=instant("14:01:00"))
    ids = ["history-S", "history-B0", "history-B0r2"]
    rejected = history(ids, at="14:02:00", previous=invalid)
    assert rejected.context.feature_state is None and rejected.feature_updates == ()
    fresh = history(ids, at="14:02:00", previous=None)
    assert [u.outcome for u in fresh.feature_updates] == ["accepted", "corrected"]
    assert fresh.context.bars[0].revision_id == "r2"


@pytest.mark.parametrize("name", ["wrong-feed", "wrong-fidelity", "wrong-basis"])
def test_profile_claim_mismatch_never_repairs_actual_bar(name):
    result = history(["history-S", name + "-B0"])
    assert component(result, name + "-B0").value is not None
    assert "profile_mismatch" in result.context.input_reasons
    assert result.context.feature_state is None and result.feature_updates == ()


def test_wrong_observation_kind_remains_actual_reducer_rejection():
    result = history(["history-S", "wrong-meta-B0"])
    update, = result.feature_updates
    assert update.outcome == "rejected"
    assert update.assessment.availability_reasons == ("not_interval",)
    assert result.context.bars == ()


def test_normalized_history_and_updates_are_frozen_and_permutation_stable():
    ids = ["history-S", "history-B0", "history-B0r2", "history-B1"]
    a, b = history(ids), history(ids[::-1])
    assert a.context == b.context
    assert a.feature_updates == b.feature_updates
    with pytest.raises(FrozenInstanceError):
        a.context.feature_state.bars = ()
    with pytest.raises(FrozenInstanceError):
        a.feature_updates[0].outcome = "rejected"
    with pytest.raises(TypeError):
        replace(a.components[0], bar_identity=None)


@pytest.mark.parametrize("field", ["profile_id", "kind", "source", "stream_id", "feed_class", "fidelity", "availability_basis", "units", "record_identity_rule"])
def test_bar_profile_missing_field_diagnostics_are_closed(field):
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    profile = next(p for p in raw["modeled_source_profiles"] if p["kind"] == "underlying_bar")
    del profile[field]
    from options_lab._input_parsing import _InvalidInput
    with pytest.raises((_InvalidInput, admission._AdmissionFailure)) as failure:
        admission._profiles([profile])
    assert failure.value.args == (f"modeled_source_profiles[0].{field}", "missing")
    admission.FixtureInputRejection("verify", instant("14:00:00"), "fixture", *failure.value.args)


@pytest.mark.parametrize("field,value,code", [
    ("feed_class", "delayed", "invalid_value"), ("feed_class", True, "invalid_type"),
    ("fidelity", "synthetic", "invalid_value"), ("fidelity", None, "invalid_type"),
    ("availability_basis", "assumed", "invalid_value"), ("availability_basis", True, "invalid_type"),
    ("record_identity_rule", "new_provider_record_id_per_update", "invalid_value"),
    ("record_identity_rule", True, "invalid_type"),
    ("units", [], "expected_exact_dict"), ("units", {"extra": "x"}, "unknown_fields"),
])
def test_bar_profile_malformed_field_diagnostics_are_closed(field, value, code):
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    profile = next(p for p in raw["modeled_source_profiles"] if p["kind"] == "underlying_bar")
    profile[field] = value
    from options_lab._input_parsing import _InvalidInput
    with pytest.raises((_InvalidInput, admission._AdmissionFailure)) as failure:
        admission._profiles([profile])
    assert failure.value.args == (f"modeled_source_profiles[0].{field}", code)
    admission.FixtureInputRejection("verify", instant("14:00:00"), "fixture", *failure.value.args)


@pytest.mark.parametrize("field", ["price", "volume", "vwap_numerator", "vwap_denominator"])
@pytest.mark.parametrize("value", ["wrong", None, True])
def test_bar_unit_wrong_values_have_owned_diagnostics(field, value):
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    profile = next(p for p in raw["modeled_source_profiles"] if p["kind"] == "underlying_bar")
    profile["units"][field] = value
    with pytest.raises(admission._AdmissionFailure) as failure:
        admission._profiles([profile])
    assert failure.value.args == ("modeled_source_profiles[0].units", "invalid_value")
    admission.FixtureInputRejection("verify", instant("14:00:00"), "fixture", *failure.value.args)


@pytest.mark.parametrize("field,value,code", [("contract", {}, "invalid_value"), ("metadata", None, "expected_exact_dict"), ("metadata", [], "expected_exact_dict")])
def test_bar_envelope_framing_uses_actual_metadata_and_null_contract(field, value, code):
    member = next(m for m in manifest(FIXTURE_ID).members if m.kind == "underlying_bar")
    env = member.decode_envelope()
    env[field] = value
    with pytest.raises(admission._AdmissionFailure) as failure:
        admission._envelope(env, "members[0]", "underlying_bar", env["stream_id"])
    assert failure.value.args == (f"members[0].envelope.{field}", code)
    admission.FixtureInputRejection("verify", instant("14:00:00"), "fixture", *failure.value.args)


def test_same_time_correction_redelivery_cannot_reorder_retained_successor():
    previous = prior("same-time-correction-copy", correction=True)
    result = history(["history-S", "same-time-correction-copy-A-copy"], at="14:04:00", previous=previous)
    assert result.context.feature_state is previous
    assert result.feature_updates[0].outcome == "duplicate"
    assert result.feature_updates[0].next_state is previous
    assert "lineage_unorderable" not in result.context.input_reasons


@pytest.mark.parametrize("name", ["tie", "unknown-seq"])
def test_fresh_unordered_arrivals_cannot_choose_first_bar_by_member_id(name):
    result = history(["history-S", name + "-B0", name + "-B1"])
    assert result.context.feature_state is None and result.context.bars == ()
    assert result.feature_updates == ()
    assert "stream_order_ambiguous" in result.context.input_reasons


def test_rejected_correction_component_cannot_claim_selected_history():
    previous = prior("missing")
    result = history(["history-S", "missing-B0r2"], previous=previous)
    assert component(result, "missing-B0").disposition == "selected"
    assert component(result, "missing-B0r2").disposition == "unresolved"
    assert result.context.bars == (component(result, "missing-B0").value,)


def test_rebound_history_alone_can_supply_its_actual_session_and_retained_scope():
    previous = prior(correction=True)
    result = history([], at="14:02:00", previous=previous)
    assert result.context.session == previous.session
    assert result.context.feature_state is previous and result.context.input_digest
    assert result.feature_updates == ()
    assert all(not c.requested for c in result.components)
    assert "incomplete_context_input_set" not in result.context.input_reasons


def test_retained_session_must_have_been_available_at_prior_cutoff():
    session = component(history(["history-S"], at="bad"), "history-S").value
    previous = FeatureState(session, as_of=instant("12:59:59.999999"))
    result = history(["history-S"], previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert "previous_state_time_invalid" in result.context.input_reasons


def test_other_strategy_session_remains_audit_only():
    result = history(["joined-exchange_session", "history-S"], at="bad")
    old_session = component(result, "joined-exchange_session").value
    previous = FeatureState(old_session, as_of=instant("14:00:00"))
    result = history(["history-S", "partial-B0"], previous=previous)
    assert result.context.session.session_date.isoformat() == "2026-09-08"
    assert result.context.feature_state is None and result.feature_updates == ()
    assert "previous_state_session_mismatch" in result.context.input_reasons
    assert result.previous_feature_state is previous


def test_bar_history_requires_actual_membership_in_supplied_manifest():
    previous = prior(correction=True)
    result = build(["null-end-S"], at=instant("14:03:00"), bundle=manifest("p08d-reference-context-v1"), previous=previous)
    assert result.context.feature_state is None and result.context.input_digest is None
    assert "previous_state_membership_missing" in result.context.input_reasons
    assert result.previous_feature_state is previous


def test_independent_stream_counters_are_never_compared_as_one_arrival_order():
    result = history(["history-S", "ordered-B0", "tie-B0", "ordered-B1", "tie-B1"])
    assert result.context.feature_state is None and result.feature_updates == ()
    assert "history_stream_ambiguous" in result.context.input_reasons
    # The tied pair in its own stream is already diagnosed; independent seq4
    # records must not produce an extra global order rejection.
    assert not any(r.code == "stream_order_ambiguous" and r.event_id == "request" for r in result.rejections)


def test_idempotent_redelivery_retains_native_reason_without_assembly_failure():
    previous = prior("duplicate", correction=True)
    result = history(["history-S", "duplicate-redelivery"], previous=previous)
    assert result.feature_updates[0].update_reasons == ("duplicate",)
    assert result.context.feature_state is previous
    assert not result.entry_input_failed
