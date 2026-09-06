"""Exercise current-market assembly through actual registered fixture bytes."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path

import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.config import StrategyConfig, config_hash, policy_hash
from options_lab.context_inputs import normalize_context_request

NOW = datetime(2026, 9, 5, 14, 30, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"


def manifest(name="p08a-greek-ready-v1"):
    """Load genuinely registered fixture bytes through admission."""
    result = verify_fixture_bundle(name, (FIXTURES / f"{name}.json").read_bytes(),
                                  event_id="verify", raw_ref="fixture", received_at=NOW)
    assert result.rejection is None
    return result.value


def build(ids, *, at=NOW, bundle=None, previous=None):
    """Exercise the single normalized request-to-context path."""
    api = importlib.import_module("options_lab.context")
    request = normalize_context_request(
        {"decision_id": "decision", "decision_at": at.isoformat() if isinstance(at, datetime) else at,
         "member_record_ids": ids}, event_id="request", raw_ref="request", received_at=NOW)
    return api.build_decision_context(request, bundle or manifest(), config=StrategyConfig(),
                                      previous_feature_state=previous)


def test_existing_actual_members_produce_current_prices_and_owned_identities():
    result = build(["option-member-1", "underlying-member-1", "greek-member-1"])
    context = result.context
    assert len(context.option_quotes) == len(context.underlying_quotes) == len(context.greeks) == 1
    assert context.config_hash == config_hash(StrategyConfig())
    assert context.policy_hash == policy_hash(StrategyConfig())
    assert context.input_manifest_id == (result.manifest.fixture_id, result.manifest.payload_sha256)
    assert context.session is context.feature_state is None
    assert context.bars == context.tradability == context.provider_mappings == context.contract_references == ()
    assert result.feature_updates == ()
    assert len(context.greek_readiness) == 1 and context.greek_readiness[0].ready
    assert result.components[0].member in result.manifest.members


def test_malformed_header_preserves_usable_components_without_time_or_digest():
    result = build(["option-member-1"], at="not a time")
    assert result.context is None
    assert result.components[0].value is not None
    assert result.components[0].disposition == "audit"
    assert result.rejections[0].field == "decision_at"


def scenario(name, *, labels=None, at=NOW):
    """Select one finite fixture stream or an intentionally incomplete subset."""
    bundle = manifest("p08d-current-market-v1")
    ids = [m.record_id for m in bundle.members if m.decode_envelope()["stream_id"].removesuffix("-" + m.kind) == name]
    if labels is not None:
        ids = [f"{name}-{label}" for label in labels]
    return build(ids, at=at, bundle=bundle)


def component(result, label):
    return next(c for c in result.components if c.member.record_id == label)


def test_actual_five_kind_pair_assessments_and_empty_later_inputs():
    result = scenario("market")
    assert len(result.context.tick_rules) == len(result.context.coherence_evidence) == 1
    assert result.context.greek_readiness[0].ready
    assert result.context.coherence_assessments[0].supports_instant(result.context.greeks[0].as_of)
    assert not result.context.input_reasons


@pytest.mark.parametrize("name", ["malformed", "unknown", "both", "source", "profile", "tie",
                                      "missing-sequence", "orphan", "cycle", "fork", "conflict", "unlinked"])
def test_unresolved_current_evidence_never_exposes_old_exit_price(name):
    result = scenario(name)
    assert result.context.option_quotes == ()
    assert component(result, name + "-A").value is not None
    assert result.context.input_reasons


def test_independent_contract_and_metadata_failures_and_metadata_body_salvage():
    result = scenario("both")
    bad = component(result, "both-B")
    failures = [result.rejections[i] for i in bad.rejection_indexes]
    failures = [r for r in failures if getattr(r, "field", None) != "member"]
    assert len(failures) == 2
    assert failures[0].field == "contract.underlying"
    assert failures[1].diagnostics[0].field == "source"
    malformed = component(scenario("malformed"), "malformed-B")
    assert malformed.metadata is not None and malformed.value is None


@pytest.mark.parametrize("name", ["crossed", "event"])
def test_typed_adverse_successor_is_current_without_old_good_substitution(name):
    result = scenario(name)
    assert result.context.option_quotes == (component(result, name + "-B").value,)
    assert component(result, name + "-B").reasons
    assert component(result, name + "-A").disposition == "superseded"


@pytest.mark.parametrize("name", ["crossed", "malformed", "unknown", "linear"])
def test_omitted_successor_is_retained_and_suppresses_old_price(name):
    result = scenario(name, labels=["A"])
    assert result.context.option_quotes == ()
    missing = component(result, name + "-B")
    assert not missing.requested
    assert "incomplete_context_input_set" in missing.reasons
    assert "incomplete_context_input_set" in result.context.input_reasons


def test_future_raw_failure_does_not_change_earlier_causal_digest():
    earlier = scenario("future", labels=["A"])
    also_future = scenario("future")
    assert earlier.context.option_quotes == also_future.context.option_quotes
    assert earlier.context.input_digest == also_future.context.input_digest
    assert component(also_future, "future-B").disposition == "future"
    assert scenario("future", at=NOW + timedelta(seconds=1)).context.option_quotes == ()


@pytest.mark.parametrize("name,expected", [
    ("bands", ["A", "B"]), ("schedule", ["A", "B"]), ("overlap", ["A", "B"]),
    ("bad-band", ["A2", "B"]), ("raw-band", ["B"]), ("tick-future", ["A"]),
    ("tick-fork", []), ("tick-linear", ["A3"]), ("tick-tie", []), ("tick-null", []),
    ("scheduled-revision", ["A2"]),
])
def test_all_tick_lineage_examples_retain_exact_known_tips(name, expected):
    result = scenario(name)
    assert result.context.tick_rules == tuple(component(result, f"{name}-{label}").value for label in expected)


def test_omitted_independent_tick_root_cannot_be_hidden():
    result = scenario("bands", labels=["A"])
    assert result.context.tick_rules == ()
    assert "incomplete_context_input_set" in result.context.input_reasons


def test_duplicate_redelivery_and_request_permutation_are_idempotent():
    result = scenario("duplicate")
    assert len(result.context.option_quotes) == 1
    assert not result.context.input_reasons
    ids = [c.member.record_id for c in result.components]
    reversed_result = build(list(reversed(ids)), bundle=result.manifest)
    assert reversed_result.context == result.context
    assert reversed_result.components == result.components


def test_unrequested_ancestor_can_be_inspected_without_becoming_selected():
    result = scenario("linear", labels=["B"])
    assert result.context.option_quotes == (component(result, "linear-B").value,)
    assert not component(result, "linear-A").requested
    assert not result.context.input_reasons


def test_existing_mixed_assessment_streams_are_not_favorable_current_subsets():
    for fixture, record, field in (("p08b-quote-coherence-v1", "joint", "coherence_evidence"),
                                   ("p08c-tick-evidence-v1", "tick-good", "tick_rules")):
        result = build([record], bundle=manifest(fixture))
        assert getattr(result.context, field) == ()
        assert "incomplete_context_input_set" in result.context.input_reasons


def test_frozen_factories_reject_manual_success_and_replacement():
    result = scenario("market")
    for value in (result, result.context, result.components[0]):
        with pytest.raises(TypeError):
            type(value)()
        with pytest.raises(TypeError):
            replace(value)
        with pytest.raises(FrozenInstanceError):
            value.unsafe = True


def test_valid_off_grid_header_still_produces_context_and_unknown_id_is_retained():
    result = build(["option-member-1", "missing"], at=NOW + timedelta(seconds=1))
    assert result.context is not None and result.context.slot_key is None
    assert len(result.context.option_quotes) == 1
    assert "unknown_member" in result.context.input_reasons


@pytest.mark.parametrize("kind,field", [("option_quote", "option_quotes"), ("underlying_quote", "underlying_quotes"),
    ("greek_observation", "greeks"), ("quote_coherence", "coherence_evidence"), ("tick_rule", "tick_rules")])
def test_complete_source_event_conflicts_for_each_supported_kind(kind, field):
    result = scenario("identity-" + kind)
    assert getattr(result.context, field) == ()
    assert "source_identity_conflict" in result.context.input_reasons
    if kind == "greek_observation":
        assert result.components[0].value.input_hash == result.components[1].value.input_hash
        assert result.components[0].value.delta != result.components[1].value.delta


def test_future_identity_conflict_and_malformed_metadata_are_excluded_before_integrity():
    for name in ("future-identity", "future-meta"):
        earlier = scenario(name, labels=["A"])
        with_future = scenario(name)
        assert len(earlier.context.option_quotes) == len(with_future.context.option_quotes) == 1
        assert earlier.context.input_digest == with_future.context.input_digest
        assert "source_identity_conflict" not in with_future.context.input_reasons
        future = next(c for c in with_future.components if c.disposition == "future")
        assert "available_after_decision" in future.reasons
    assert scenario("future-identity", at=NOW + timedelta(seconds=1)).context.option_quotes == ()


def test_future_rejections_cannot_shift_causal_digest_or_causal_failures():
    bundle = manifest("p08d-current-market-v1")
    ids = ["future-A", "malformed-A", "malformed-B", "market-underlying_quote"]
    without = build(ids, bundle=bundle)
    with_future = build(ids + ["future-B"], bundle=bundle)
    assert without.context.input_digest == with_future.context.input_digest
    assert without.context.rejections == with_future.context.rejections
    for result in (without, with_future):
        bad = component(result, "malformed-B")
        assert any(getattr(result.rejections[i], "field", None) == "ask" for i in bad.rejection_indexes)


def test_unknown_key_and_cross_stream_link_cannot_recover_old_quote():
    assert scenario("unknown-key").context.option_quotes == ()
    result = scenario("cross-stream")
    assert result.context.option_quotes == ()
    assert "lineage_invalid" in result.context.input_reasons
    assert not component(result, "other-stream-B").requested


def test_participating_competing_streams_fail_but_unrelated_streams_are_not_scanned():
    bundle = manifest("p08d-current-market-v1")
    selected = ["linear-B", "market-underlying_quote"]
    good = build(selected, bundle=bundle)
    assert len(good.context.option_quotes) == len(good.context.underlying_quotes) == 1
    assert {c.member.record_id for c in good.components} == {"linear-A", *selected}
    conflict = build(selected + ["market-option_quote"], bundle=bundle)
    assert conflict.context.option_quotes == ()
    assert len(conflict.context.underlying_quotes) == 1
    assert "current_roots_conflict" in conflict.context.input_reasons


def test_bad_method_protocol_and_definition_cannot_regain_predecessor():
    for name, field in (("bad-method", "greeks"), ("bad-protocol", "coherence_evidence"), ("bad-definition", "tick_rules")):
        result = scenario(name)
        assert component(result, name + "-A").value not in getattr(result.context, field)
        assert result.context.input_reasons


def test_actual_greek_asof_requires_returned_coherence_proof_support():
    bundle = manifest("p08d-current-market-v1")
    result = build(["market-option_quote", "market-underlying_quote", "market-quote_coherence", "wrong-asof-B"], bundle=bundle)
    assert result.context.greeks[0].as_of == NOW - timedelta(seconds=1)
    assert not result.context.coherence_assessments[0].supports_instant(result.context.greeks[0].as_of)
    assert "greek_coherence_missing" in result.context.input_reasons


def test_unsupported_quote_identity_is_retained_without_rounding_or_throwing():
    result = scenario("giant")
    assert len(result.context.option_quotes) == 1
    assert result.components[0].quote_identity.content_hash is None
    assert "integer_representation_unsupported" in result.context.input_reasons
    assert len(result.context.input_digest) == 64


def test_malformed_header_keeps_both_prerequisites_but_never_assesses_time():
    result = scenario("both", at="not-a-time")
    assert result.context is None and result.feature_updates == ()
    assert all(c.disposition == "audit" and c.quote_identity is None for c in result.components)
    failures = [result.rejections[i] for i in component(result, "both-B").rejection_indexes]
    assert any(getattr(r, "field", None) == "contract.underlying" for r in failures)
    assert any(getattr(r, "diagnostics", ()) for r in failures)


def test_missing_manifest_and_wrong_trusted_types_are_explicit():
    from options_lab.context import build_decision_context
    request = normalize_context_request({"decision_id": "x", "decision_at": NOW.isoformat(), "member_record_ids": []},
                                       event_id="x", raw_ref="x", received_at=NOW)
    kwargs = dict(request=request, manifest=None, config=StrategyConfig(), previous_feature_state=None)
    result = build_decision_context(**kwargs)
    assert result.context.input_manifest_id is None
    assert "manifest_missing" in result.context.input_reasons
    for field in kwargs:
        with pytest.raises(TypeError):
            build_decision_context(**{**kwargs, field: object()})


def test_supplied_previous_state_is_retained_unverified_without_reset():
    from options_lab.features import FeatureState
    from test_bars import session
    previous = FeatureState(session(), as_of=NOW)
    result = build(["option-member-1"], previous=previous)
    assert result.previous_feature_state is previous
    assert result.context.feature_state is None and result.feature_updates == ()
    assert "previous_state_unverified" in result.context.input_reasons
    assert result.context.input_digest is None


def test_context_rejections_roundtrip_through_closed_native_constructors():
    from options_lab.context import ContextMemberRejection
    result = build([m.record_id for m in manifest("p08d-current-market-v1").members], bundle=manifest("p08d-current-market-v1"))
    for rejection in result.rejections:
        assert replace(rejection) == rejection
    with pytest.raises(ValueError):
        ContextMemberRejection("x", NOW, "x", "contract", "made_up")
    with pytest.raises(ValueError):
        ContextMemberRejection("x", NOW, "x", "unbounded.raw.path", "missing")


def test_immutable_context_serialization_and_digest_bind_header_config_and_manifest():
    import pickle
    from options_lab.context import build_decision_context
    result = scenario("market")
    assert pickle.loads(pickle.dumps(result)) == result
    request = result.request
    changed = build_decision_context(request, result.manifest, config=StrategyConfig(max_entries_per_session=2), previous_feature_state=None)
    assert changed.context.config_hash != result.context.config_hash
    assert changed.context.input_digest != result.context.input_digest
    repeated = build(list(request.member_record_ids) * 2, bundle=result.manifest)
    assert repeated.context.input_digest == result.context.input_digest


def test_future_only_link_cannot_expand_causal_scope_into_other_stream():
    result = scenario("future-link", labels=["A"])
    assert len(result.context.option_quotes) == 1
    assert {c.member.record_id for c in result.components} == {"future-link-A", "future-link-F"}
    assert "current_roots_conflict" not in result.context.input_reasons
    assert scenario("future-link").context.input_digest == result.context.input_digest


def test_unknown_contract_in_participating_stream_blocks_other_option_targets():
    bundle = manifest("p08d-current-market-v1")
    result = build(["market-option_quote", "unknown-contract-A", "market-underlying_quote"], bundle=bundle)
    assert result.context.option_quotes == ()
    assert len(result.context.underlying_quotes) == 1


def test_requested_duplicate_promotion_keeps_source_conflict_across_contracts():
    result = scenario("alias-conflict", labels=["B", "C"])
    assert result.context.option_quotes == ()
    assert "source_identity_conflict" in component(result, "alias-conflict-C").reasons


def test_unknown_request_ids_are_content_bound_without_raw_diagnostic_text():
    first = build(["option-member-1", "unknown-one"])
    second = build(["option-member-1", "unknown-two"])
    assert first.context.input_digest != second.context.input_digest
    assert first.context.input_reasons == second.context.input_reasons


def test_multiple_requested_redeliveries_have_one_representative_without_false_conflict():
    result = scenario("duplicate", labels=["B", "C"])
    assert len(result.context.option_quotes) == 1
    assert not result.context.input_reasons


def test_public_exports_and_all_five_kind_request_permutations():
    import itertools
    import options_lab
    import options_lab.context as api
    for name in ("ContextMemberRejection", "ContextComponent", "DecisionContext", "ContextBuildResult", "build_decision_context"):
        assert getattr(options_lab, name) is getattr(api, name)
    baseline = scenario("market")
    for ids in itertools.permutations(baseline.request.member_record_ids):
        result = build(list(ids), bundle=baseline.manifest)
        assert result.context == baseline.context
        assert result.components == baseline.components


def test_fixture_producer_reproduces_all_committed_members_and_preserves_old_bytes():
    import hashlib
    import json
    from build_context_fixture import build_fixture
    recorded = json.loads((FIXTURES / "p08d-current-market-v1.json").read_bytes())
    generated = json.loads(build_fixture())
    assert datetime.fromisoformat(recorded.pop("assembled_at")) <= datetime.fromisoformat(generated.pop("assembled_at"))
    assert recorded == generated
    expected = (
        ("p08a-greek-ready-v1", "6a40fe628066a6f73c6c0b6599ef9ddaf94c213fdf0e1adf589e5333ac5f7c04"),
        ("p08b-quote-coherence-v1", "3fd4b403dea1e0e234159c8655f820fef194e17ced6e84026e82d4684aafd52f"),
        ("p08c-tick-evidence-v1", "aa2eeace708428c39ad95c13788941c814acdeaf8b0fdaf48df78a5a384ba3da"),
    )
    for name, digest in expected:
        assert hashlib.sha256((FIXTURES / f"{name}.json").read_bytes()).hexdigest() == digest


def test_entry_input_failure_is_derived_without_claiming_trade_readiness():
    assert scenario("malformed").entry_input_failed
    assert scenario("market", at="bad-time").entry_input_failed
    assert not scenario("market").entry_input_failed
    assert scenario("market").context.session is None


@pytest.mark.parametrize("future_id", ["future-B", "future-link-F"])
def test_future_request_cannot_introduce_a_new_participating_stream(future_id):
    bundle = manifest("p08d-current-market-v1")
    ids = ["market-option_quote", "market-underlying_quote"]
    baseline = build(ids, bundle=bundle)
    result = build(ids + [future_id], bundle=bundle)
    assert result.context.option_quotes == baseline.context.option_quotes
    assert result.context.underlying_quotes == baseline.context.underlying_quotes
    assert result.context.input_reasons == baseline.context.input_reasons == ()
    assert result.context.rejections == baseline.context.rejections == ()
    assert result.context.input_digest == baseline.context.input_digest
    assert {c.member.record_id for c in result.components} == {*ids, future_id}
    future = component(result, future_id)
    assert future.requested and future.disposition == "future"
    assert "available_after_decision" in future.reasons
    if future_id == "future-B":
        assert any(getattr(result.rejections[i], "field", None) == "ask"
                   for i in future.rejection_indexes)


def test_unknown_availability_request_still_establishes_conservative_stream_scope():
    bundle = manifest("p08d-current-market-v1")
    result = build(["market-option_quote", "market-underlying_quote", "unknown-B"], bundle=bundle)
    assert result.context.option_quotes == ()
    assert result.context.underlying_quotes == (component(result, "market-underlying_quote").value,)
    assert not component(result, "unknown-A").requested
    unknown = component(result, "unknown-B")
    assert unknown.available_at is None and unknown.disposition == "unresolved"
    assert "availability_unknown" in result.context.input_reasons
    assert any(any(d.field == "available_at" for d in getattr(result.rejections[i], "diagnostics", ()))
               for i in unknown.rejection_indexes)
