"""Exercise actual joined D2 fixture bytes and native owner evidence end to end."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import json

import pytest

from options_lab import admission
from options_lab.contract_inputs import ContractReferenceRejection, ProviderContractMappingRejection
from options_lab.session_inputs import SessionInputRejection
from test_context import FIXTURES, build, component, manifest

FIXTURE_ID = "p08d-reference-context-v1"
NOW = datetime(2026, 9, 8, 16, tzinfo=timezone.utc)
KINDS = ("exchange_session", "instrument_tradability", "provider_contract_mapping", "contract_reference")


def scenario(name, *, labels=None, at=NOW):
    """Build an actual registered finite trace, optionally omitting its tips."""
    bundle = manifest(FIXTURE_ID)
    ids = [m.record_id for m in bundle.members if m.decode_envelope()["stream_id"] == f"{name}-{m.kind}"]
    if labels is not None:
        ids = [f"{name}-{label}" for label in labels]
    return build(ids, at=at, bundle=bundle)


def test_four_new_kind_profiles_and_envelopes_are_admitted():
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    # Removing any dispatch/profile addition makes genuine owner-shaped rows fail.
    profiles, _ = admission._profiles(raw["modeled_source_profiles"])
    assert {p["kind"] for p in profiles} >= set(KINDS)


def test_joined_nine_kind_context_uses_actual_owner_assessments():
    result = scenario("joined", at=datetime(2026, 9, 5, 14, 30, tzinfo=timezone.utc))
    context = result.context
    assert context.session.calendar == "XNYS"
    assert len(context.tradability) == len(context.provider_mappings) == len(context.contract_references) == 1
    assert context.session_assessments[0].entry_timing_suitable
    assert context.reference_assessments[0].reference_suitable
    assert context.greek_readiness[0].ready
    assert context.coherence_assessments[0].supports_instant(context.greeks[0].as_of)
    assert len(context.tick_rules) == 1
    assert context.timing.session_assessment.session == context.session
    assert context.timing.session_assessment.tradability is None
    assert context.feature_state is None and context.bars == result.feature_updates == ()
    assert context.origin == "synthetic" and context.fidelity_tier == 0
    assert context.operational_allowed is context.economic_allowed is False


def test_session_uses_new_york_strategy_date_and_retains_tomorrow_audit():
    result = scenario("date", at=datetime(2026, 9, 9, 0, 30, tzinfo=timezone.utc))
    assert result.context.session.session_date == date(2026, 9, 8)
    assert component(result, "date-S9").value.session_date == date(2026, 9, 9)
    assert component(result, "date-S9").disposition == "audit"
    assert "session_wrong_date" not in result.context.session_assessments[0].session_reasons


@pytest.mark.parametrize("name,kind,reason", [("revision", "early_close", "session_not_regular"), ("unknown-session", "unknown", "session_unknown")])
def test_session_revision_keeps_adverse_current_facts(name, kind, reason):
    result = scenario(name)
    assert result.context.session.kind == kind
    assert reason in result.context.session_assessments[0].session_reasons
    assert component(result, name + "-S0").disposition == "superseded"
    if kind == "early_close":
        assert result.context.session_assessments[0].recovery_basis != "unknown"


@pytest.mark.parametrize("name", ["unknown-availability", "session-conflict"])
def test_unresolved_session_cannot_fall_back_to_regular(name):
    result = scenario(name)
    assert result.context.session is None
    assert result.context.input_reasons


def test_status_schedule_retains_expired_and_newly_effective_owner_evidence():
    result = scenario("status-schedule")
    assert [value.status for value in result.context.tradability] == ["tradable", "halted"]
    a, b = result.context.session_assessments
    assert "operability_expired" in a.operability_reasons
    assert b.operability_reasons == ("instrument_halted",)
    assert b.instrument_hours_reasons == ()


def test_status_and_reference_null_ends_preserve_different_owner_semantics():
    context = scenario("null-end").context
    assert "operability_interval_unknown" in context.session_assessments[0].operability_reasons
    assert context.reference_assessments[0].reference_suitable
    failed = scenario("null-start")
    assert isinstance(failed.rejections[0], ContractReferenceRejection)
    assert (failed.rejections[0].field, failed.rejections[0].code) == ("effective_from", "invalid_type")


@pytest.mark.parametrize("name,field", [("status-overlap", "tradability"), ("status-unknown", "tradability"), ("reference-overlap", "contract_references"), ("reference-schedule", "contract_references")])
def test_independent_effective_roots_remain_complete_without_success_filtering(name, field):
    context = scenario(name).context
    assert len(getattr(context, field)) == 2
    assessments = context.session_assessments if field == "tradability" else context.reference_assessments
    assert len(assessments) == 2
    if name == "reference-overlap":
        assert assessments[0].reference_suitable
        assert "deliverable_unknown" in assessments[1].reasons
    if name == "status-unknown":
        assert "operability_interval_unknown" in assessments[1].operability_reasons


def test_mapping_update_uses_provider_symbol_target_and_actual_mismatch_assessor():
    result = scenario("mapping-revision")
    mapping = result.context.provider_mappings[0]
    assert mapping.symbol == "opaque-K" and mapping.contract.right == "put"
    assert mapping.provider != result.context.contract_references[0].source
    assert "identity_right_mismatch" in result.context.reference_assessments[0].reasons
    assert component(result, "mapping-revision-M0").disposition == "superseded"


@pytest.mark.parametrize("name", ["mapping-conflict", "mapping-roots"])
def test_mapping_new_member_id_or_sequence_does_not_repair_identity_or_roots(name):
    result = scenario(name)
    assert result.context.provider_mappings == ()
    assert len(result.context.contract_references) == 1
    assert ("source_identity_conflict" if name == "mapping-conflict" else "current_roots_conflict") in result.context.input_reasons


def test_omitted_malformed_mapping_update_retains_native_failure_and_usable_reference():
    result = scenario("mapping-malformed", labels=["M0", "R"])
    assert result.context.provider_mappings == ()
    assert len(result.context.contract_references) == 1
    omitted = component(result, "mapping-malformed-M1")
    assert omitted.disposition == "omitted"
    failures = [result.rejections[i] for i in omitted.rejection_indexes]
    native = next(f for f in failures if type(f) is ProviderContractMappingRejection)
    assert (native.field, native.code) == ("contract.multiplier", "invalid_type")
    assert "incomplete_context_input_set" in omitted.reasons


@pytest.mark.parametrize("kind", ["instrument_tradability", "contract_reference"])
def test_future_malformed_update_does_not_change_causal_selection_or_digest(kind):
    name = "future-" + kind
    earlier, requested = scenario(name, labels=["A"]), scenario(name)
    assert earlier.context.input_digest == requested.context.input_digest
    assert component(requested, name + "-B").disposition == "future"
    assert component(requested, name + "-B").rejection_indexes
    field = "tradability" if kind == "instrument_tradability" else "contract_references"
    assert len(getattr(requested.context, field)) == 1
    later = scenario(name, at=datetime(2026, 9, 8, 16, 0, 1, tzinfo=timezone.utc))
    assert getattr(later.context, field) == ()


def test_future_requested_member_and_cross_stream_link_cannot_introduce_scope():
    earlier, requested = scenario("future-link", labels=["A"]), scenario("future-link")
    assert earlier.context.input_digest == requested.context.input_digest
    assert all(not c.member.record_id.startswith("reference-overlap") for c in requested.components)
    future_only = build(["future-contract_reference-B"], at=NOW, bundle=requested.manifest)
    assert [c.member.record_id for c in future_only.components] == ["future-contract_reference-B"]


@pytest.mark.parametrize("kind", ["instrument_tradability", "contract_reference"])
def test_explicit_scheduled_revision_retires_predecessor_immediately(kind):
    name = "scheduled-revision-" + kind
    result = scenario(name)
    field = "tradability" if kind == "instrument_tradability" else "contract_references"
    assert getattr(result.context, field) == (component(result, name + "-B").value,)
    assert component(result, name + "-A").disposition == "superseded"


@pytest.mark.parametrize("name,field", [("status-overlap", "tradability"), ("reference-overlap", "contract_references")])
def test_omitted_effective_root_invalidates_complete_target_set(name, field):
    result = scenario(name, labels=["A"])
    assert getattr(result.context, field) == ()
    assert "incomplete_context_input_set" in component(result, name + "-B").reasons


@pytest.mark.parametrize("kind", KINDS)
def test_source_redelivery_ignores_ingestion_but_changed_source_content_conflicts(kind):
    duplicate = scenario("duplicate-" + kind)
    assert sum(c.disposition == "selected" for c in duplicate.components) == 1
    assert sum(c.disposition == "duplicate" for c in duplicate.components) == 1
    conflict = scenario("identity-" + kind)
    assert all(c.disposition == "unresolved" for c in conflict.components)
    assert "source_identity_conflict" in conflict.context.input_reasons


def test_native_nested_raw_precedence_and_independent_salvage_are_unchanged():
    result = scenario("raw-precedence")
    failures = [r for r in result.rejections if type(r) in (SessionInputRejection, ProviderContractMappingRejection)]
    assert {(r.stage, r.field, r.code) for r in failures} == {
        ("instrument_tradability_normalization", "contract", "unknown_fields"),
        ("provider_contract_mapping_normalization", "provider", "missing"),
    }
    assert result.context.session is not None and len(result.context.contract_references) == 1


def test_null_contract_remains_unresolved_without_discarding_instrument_hours():
    context = scenario("unresolved").context
    assert context.tradability[0].contract is None
    assert context.tradability[0].instrument_ref == "opaque-K"
    assessment = context.session_assessments[0]
    assert "instrument_unresolved" in assessment.instrument_hours_reasons
    assert assessment.recovery_basis != "unknown"


@pytest.mark.parametrize("kind,prefix", [(kind, prefix) for kind in KINDS for prefix in ("wrong-source", "wrong-basis", "wrong-fidelity") if prefix != "wrong-fidelity" or kind in KINDS[:2]])
def test_source_profile_claims_remain_actual_unrepaired_evidence(kind, prefix):
    result = scenario(prefix + "-" + kind)
    row = result.components[0]
    assert row.value is not None
    assert "profile_mismatch" in row.reasons and row.disposition == "unresolved"
    field = "provider" if kind == "provider_contract_mapping" else "source"
    if prefix == "wrong-basis":
        field = "availability_basis"
    elif prefix == "wrong-fidelity":
        field = "fidelity"
    assert getattr(row.value, field) == row.member.decode_raw_body()[field]


def test_new_frozen_fields_and_digest_are_permutation_stable():
    result = scenario("status-overlap")
    reversed_result = build(list(reversed([c.member.record_id for c in result.components])), at=NOW, bundle=result.manifest)
    assert result.context == reversed_result.context
    with pytest.raises(FrozenInstanceError):
        result.context.tradability = ()
    with pytest.raises(TypeError):
        replace(result.context)


def test_malformed_time_still_preserves_all_four_native_values_without_context():
    result = scenario("joined", at="bad")
    assert result.context is None
    assert len([c for c in result.components if c.member.kind in KINDS and c.value is not None]) == 4
    assert all(c.disposition == "audit" for c in result.components)


@pytest.mark.parametrize("name", ["mapping-ambiguous", "targets-ambiguous"])
def test_no_arbitrary_assessor_pair_when_mapping_or_reference_target_is_ambiguous(name):
    context = scenario(name).context
    assert context.provider_mappings and context.contract_references
    assert context.reference_assessments == ()


@pytest.mark.parametrize("kind", ["instrument_tradability", "contract_reference"])
def test_omitted_malformed_effective_revision_has_native_failure_without_fallback(kind):
    name = "malformed-revision-" + kind
    result = scenario(name, labels=["A"])
    field = "tradability" if kind == "instrument_tradability" else "contract_references"
    assert getattr(result.context, field) == ()
    omitted = component(result, name + "-B")
    assert omitted.disposition == "omitted"
    native_type = SessionInputRejection if kind == "instrument_tradability" else ContractReferenceRejection
    native = next(result.rejections[i] for i in omitted.rejection_indexes if type(result.rejections[i]) is native_type)
    assert (native.field, native.code) == ("contract.multiplier", "invalid_type")
    assert "incomplete_context_input_set" in omitted.reasons



@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field", ["profile_id", "kind", "source", "stream_id", "feed_class", "fidelity", "availability_basis", "units", "record_identity_rule"])
def test_new_profile_missing_fields_emit_constructible_closed_diagnostics(kind, field):
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    profile = next(p for p in raw["modeled_source_profiles"] if p["kind"] == kind)
    del profile[field]
    from options_lab._input_parsing import _InvalidInput
    with pytest.raises((_InvalidInput, admission._AdmissionFailure)) as failure:
        admission._profiles([profile])
    path, code = failure.value.args
    assert (path, code) == (f"modeled_source_profiles[0].{field}", "missing")
    assert admission.FixtureInputRejection("verify", NOW, "fixture", path, code).code == code


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value,code", [
    ("feed_class", "realtime", "invalid_value"),
    ("fidelity", "synthetic", "invalid_value"),
    ("availability_basis", "assumed", "invalid_value"),
    ("availability_basis", True, "invalid_type"),
    ("record_identity_rule", "wrong", "invalid_value"),
    ("record_identity_rule", True, "invalid_type"),
    ("units", [], "expected_exact_dict"),
    ("units", {"price": "USD_per_share"}, "unknown_fields"),
])
def test_new_profile_malformed_fields_emit_constructible_closed_diagnostics(kind, field, value, code):
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    profile = next(p for p in raw["modeled_source_profiles"] if p["kind"] == kind)
    profile[field] = value
    from options_lab._input_parsing import _InvalidInput
    with pytest.raises((_InvalidInput, admission._AdmissionFailure)) as failure:
        admission._profiles([profile])
    path, observed = failure.value.args
    assert (path, observed) == (f"modeled_source_profiles[0].{field}", code)
    assert admission.FixtureInputRejection("verify", NOW, "fixture", path, observed).code == code


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field", ["contract", "metadata"])
def test_new_envelope_owner_fields_require_null_and_closed_diagnostics(kind, field):
    bundle = manifest(FIXTURE_ID)
    member = next(m for m in bundle.members if m.kind == kind)
    env = member.decode_envelope()
    env[field] = {}
    with pytest.raises(admission._AdmissionFailure) as failure:
        admission._envelope(env, "members[0]", kind, env["stream_id"])
    path, code = failure.value.args
    assert (path, code) == (f"members[0].envelope.{field}", "invalid_value")
    assert admission.FixtureInputRejection("verify", NOW, "fixture", path, code).code == code


def test_nonnull_malformed_status_identity_cannot_masquerade_as_nullable_target():
    result = scenario("malformed-status-root")
    assert result.context.tradability == ()
    bad = component(result, "malformed-status-root-B")
    assert "target_unknown" in bad.reasons
    assert bad.value is None
    native = next(result.rejections[i] for i in bad.rejection_indexes if type(result.rejections[i]) is SessionInputRejection)
    assert (native.field, native.code) == ("contract.multiplier", "invalid_type")


def test_empty_prior_state_rebinds_exact_admitted_session_without_inventing_history():
    from options_lab.features import FeatureState
    at = datetime(2026, 9, 5, 14, 30, tzinfo=timezone.utc)
    current = scenario("joined", at=at)
    previous = FeatureState(current.context.session, as_of=at)
    result = build([c.member.record_id for c in current.components], at=at, bundle=current.manifest, previous=previous)
    assert result.previous_feature_state is previous
    assert result.context.feature_state is previous and result.context.input_digest
    assert result.context.bars == result.feature_updates == ()
    assert "previous_state_unverified" not in result.context.input_reasons


def test_off_grid_session_context_remains_representable_with_separate_timing_reasons():
    context = scenario("joined", at=datetime(2026, 9, 5, 14, 30, 1, tzinfo=timezone.utc)).context
    assert context.session is not None and context.input_digest
    assert "decision_off_grid" in context.timing.reasons
    assert "decision_off_grid" not in context.input_reasons


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", [None, True, ""])
def test_new_profile_fidelity_type_and_null_policy_diagnostics_are_closed(kind, value):
    raw = json.loads((FIXTURES / f"{FIXTURE_ID}.json").read_bytes())
    profile = next(p for p in raw["modeled_source_profiles"] if p["kind"] == kind)
    profile["fidelity"] = value
    if kind not in KINDS[:2] and value is None:
        assert admission._profiles([profile])[0][0]["fidelity"] is None
        return
    from options_lab._input_parsing import _InvalidInput
    with pytest.raises((_InvalidInput, admission._AdmissionFailure)) as failure:
        admission._profiles([profile])
    path, code = failure.value.args
    expected = "invalid_type" if kind in KINDS[:2] and value in (None, True) else "invalid_value"
    assert (path, code) == ("modeled_source_profiles[0].fidelity", expected)
    assert admission.FixtureInputRejection("verify", NOW, "fixture", path, code).code == expected


def test_malformed_mapping_omission_leaves_independent_market_and_reference_evidence():
    result = build(["mapping-malformed-M0", "mapping-malformed-R", "joined-underlying_quote"],
                   at=NOW, bundle=manifest(FIXTURE_ID))
    assert result.context.provider_mappings == ()
    assert len(result.context.underlying_quotes) == len(result.context.contract_references) == 1
    assert component(result, "joined-underlying_quote").disposition == "selected"


@pytest.mark.parametrize("field", ["symbol", "provider"])
@pytest.mark.parametrize("malformed", ["missing", "invalid"])
@pytest.mark.parametrize("omit", [False, True])
def test_unknown_mapping_target_cannot_be_narrowed_by_changed_mapped_contract(field, malformed, omit):
    name = f"mapping-target-{field}-{malformed}-causal"
    ids = [name + "-A"] + ([] if omit else [name + "-B"])
    ids += ["null-end-R", "null-end-S", "null-end-H", "joined-underlying_quote"]
    result = build(ids, at=NOW, bundle=manifest(FIXTURE_ID))
    assert result.context.provider_mappings == ()
    bad = component(result, name + "-B")
    assert bad.contract.right == "put"
    assert component(result, name + "-A").contract.right == "call"
    assert "target_unknown" in component(result, name + "-A").reasons
    assert bad.disposition == ("omitted" if omit else "unresolved")
    failure = next(result.rejections[i] for i in bad.rejection_indexes
                   if type(result.rejections[i]) is ProviderContractMappingRejection)
    assert (failure.field, failure.code) == (field, "missing" if malformed == "missing" else "invalid_type")
    if omit:
        assert "incomplete_context_input_set" in bad.reasons
    assert len(result.context.contract_references) == len(result.context.tradability) == len(result.context.underlying_quotes) == 1
    assert result.context.session is not None


@pytest.mark.parametrize("field", ["symbol", "provider"])
@pytest.mark.parametrize("malformed", ["missing", "invalid"])
def test_known_future_unknown_mapping_target_preserves_causal_mapping_and_digest(field, malformed):
    name = f"mapping-target-{field}-{malformed}-future"
    earlier, requested = scenario(name, labels=["A"]), scenario(name)
    assert earlier.context.input_digest == requested.context.input_digest
    assert len(requested.context.provider_mappings) == 1
    assert requested.context.provider_mappings[0].contract.right == "call"
    bad = component(requested, name + "-B")
    assert bad.disposition == "future" and bad.rejection_indexes
    future_only = scenario(name, labels=["B"])
    assert [c.member.record_id for c in future_only.components] == [name + "-B"]
    now_available = scenario(name, at=datetime(2026, 9, 8, 16, 0, 1, tzinfo=timezone.utc))
    assert now_available.context.provider_mappings == ()
