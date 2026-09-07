"""Full admitted vectors preserve numerical order, source causality and identity."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, ROUND_UP
import hashlib
import importlib
import json

import pytest

from test_context import build, manifest
from options_lab.volume_normalization import normalize_feature_normalization

NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
GOLDEN = tuple(map(Decimal, (
    "0.002306806097915013731464615471169539", "0.01158761517238788894107293299073990",
    "0.03517361417826712941881526775822312", "0.07162965613925479073308667961437122",
    "0.005182166877311561399250942314051412", "0.009082254214374406240937276548535604",
    "0.01308051902088299805000259276580310", "0.04115807249350747940874120614240408",
    "1100", "1", "0.00004608294930875576036866359447004608", "35", "355", "1", "0", "14",
    "4.90", "5.10", "0.04", "2", "0.50", "0.20",
)))


def api():
    """Import the production owner after real source setup, never a stub."""
    return importlib.import_module("options_lab.feature_vector")


def context(scenario="good", history="bars", *, at=NOW, extra=(), omit=(), config=None):
    """Use real admitted market members and the single P08 context builder."""
    bundle = manifest("p11-feature-vector-v1")
    count = 30 if scenario == "warmup" else 35
    ids = ["session", *[m.record_id for m in bundle.members if m.record_id.startswith(scenario + "-")],
           *[f"{history}-{i:02d}" for i in range(1, count + 1)], *extra]
    ids = [i for i in ids if i not in omit]
    if config is None:
        return build(ids, at=at, bundle=bundle)
    from options_lab.context import build_decision_context
    from options_lab.context_inputs import normalize_context_request
    request = normalize_context_request(dict(decision_id="decision", decision_at=at.isoformat(), member_record_ids=ids),
                                        event_id="request", raw_ref="request", received_at=NOW)
    return build_decision_context(request, bundle, config=config, previous_feature_state=None)


def normalization(record="good"):
    """Recompute actual frozen B from actual upstream A using P10's boundary."""
    bundle = manifest("p10b-volume-normalization-v1")
    member = next(m for m in bundle.members if m.record_id == record)
    return normalize_feature_normalization(member.decode_raw_body(), manifest=bundle, record_id=record,
                                           training_manifest=manifest("p10a-volume-training-v1"), decision_at=NOW).value


def vector(scenario="good", history="bars", *, proxy=False, **kwargs):
    """Build one original vector from its exact selected immutable context inputs."""
    ctx = context(scenario, history, **kwargs).context
    owner = api()
    return owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], now=ctx.decision_at,
                                spec=owner.CLOSE_VOLUME_PROXY_SPEC if proxy else owner.EXACT_VWAP_SPEC,
                                normalization=normalization())


def test_actual_admitted_complete_22_literal_vector_and_proxy():
    exact, proxy = vector(), vector(proxy=True)
    assert exact.reasons == () and exact.vector.values == GOLDEN
    expected = list(GOLDEN)
    expected[7] = Decimal("0.03995831230160341186771903364564402")
    assert proxy.reasons == () and proxy.vector.values == tuple(expected)
    assert exact.vector.feature_schema_id != proxy.vector.feature_schema_id
    assert exact.vector.transform_id != proxy.vector.transform_id
    assert exact.vector.spec.fidelity != proxy.vector.spec.fidelity
    assert exact.vector.available_at == NOW
    assert exact.vector.origin == "synthetic" and exact.vector.fidelity_tier == 0
    assert exact.vector.operational_allowed is exact.vector.economic_allowed is False
    assert len(exact.source_components) == 43


def test_context_retains_original_component_objects_and_index_namespace():
    ordinary = context()
    future = context(extra=("aaa-future-malformed",))
    assert ordinary.context.input_digest == future.context.input_digest
    assert future.context.selected_components == tuple(c for c in future.components if c.disposition == "selected")
    assert all(any(c is original for original in future.components) for c in future.context.selected_components)
    bad = next(c for c in future.components if c.member.record_id == "aaa-future-malformed")
    assert bad.rejection_indexes and future.rejections[bad.rejection_indexes[0]].field == "contract.multiplier"
    assert len(future.rejections) > len(future.context.rejections)


@pytest.mark.parametrize("scenario,history,proxy,reason", [
    ("warmup", "bars", False, "insufficient_close_history"),
    ("good", "late-bars", False, "required_bar_endpoint_missing"),
    ("good", "proxy-bars", False, "vwap_definition_unknown"),
    ("good", "source-bars", True, "profile_mismatch"),
    ("stale", "bars", False, "too_old"),
    ("missing-event", "bars", False, "event"),
    ("bad-proof", "bars", False, "greek_coherence_missing"),
])
def test_no_complete_vector_when_required_current_evidence_fails(scenario, history, proxy, reason):
    at = NOW - timedelta(minutes=5) if scenario == "warmup" else NOW
    result = vector(scenario, history, proxy=proxy, at=at)
    assert result.vector is None and any(reason in r for r in result.reasons)
    assert (result.return_features is not None) == (result.context.feature_state is not None)


def test_only_selected_mode_missingness_is_excluded_from_aggregate():
    proxy = vector(history="proxy-bars", proxy=True)
    exact = vector(history="exact-bars")
    assert proxy.vector is not None and exact.vector.values == GOLDEN
    assert "vwap_definition_unknown" in proxy.context.input_reasons
    assert "volume_missing" in exact.context.input_reasons
    assert vector(history="exact-bars", proxy=True).vector is None


def test_equal_vwap_values_still_have_different_concrete_identities():
    exact, proxy = vector(history="equal-bars"), vector(history="equal-bars", proxy=True)
    assert exact.vector.values == proxy.vector.values
    assert exact.vector.feature_schema_id != proxy.vector.feature_schema_id
    assert exact.vector.transform_id != proxy.vector.transform_id
    assert exact.vector.input_hash != proxy.vector.input_hash


def test_immutable_canonical_snapshot_and_actual_hashes():
    original = vector().vector
    snapshot = original.snapshot
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    assert digest == original.content_hash
    snapshot["values"][0] = "999"
    assert original.snapshot["values"][0] != "999"
    with pytest.raises(FrozenInstanceError):
        original.values = ()
    later = context(at=NOW + timedelta(microseconds=1), extra=("bars-correction",))
    assert later.context.feature_state.input_hash != original.context.feature_state.input_hash
    assert original.content_hash == digest and original.values == GOLDEN


def test_raw_spec_selector_resolves_only_matching_code_owned_identities():
    owner = api()
    for spec in (owner.EXACT_VWAP_SPEC, owner.CLOSE_VOLUME_PROXY_SPEC):
        raw = dict(schema_version=1, feature_schema_id=spec.feature_schema_id, transform_id=spec.transform_id)
        result = owner.normalize_feature_spec(raw, event_id="spec", raw_ref="spec", received_at=NOW)
        assert result.value is spec and result.rejection is None
        raw["transform_id"] = "0" * 64
        rejected = owner.normalize_feature_spec(raw, event_id="spec", raw_ref="spec", received_at=NOW)
        assert rejected.value is None and rejected.rejection.code == "unsupported_spec"


@pytest.mark.parametrize("raw", [None, [], True, {}, {"schema_version": True}, {"schema_version": 2},
                                 {"schema_version": 1, "feature_schema_id": None, "transform_id": "0" * 64}])
def test_raw_spec_malformed_shapes_are_bounded(raw):
    result = api().normalize_feature_spec(raw, event_id="spec", raw_ref="spec", received_at=NOW)
    assert result.value is None and result.rejection.stage == "feature_spec"


def test_put_keeps_positive_nonzero_log_moneyness_and_separate_signed_right():
    result = vector("put")
    assert result.reasons == ()
    assert result.vector.values[13:16] == (Decimal(-1), Decimal("0.009259325412796752960915221413502265"), Decimal(14))
    assert result.vector.values[20:] == (Decimal("-0.50"), Decimal("0.20"))


def test_current_midpoint_drives_vwap_and_moneyness_independently_of_last_close():
    result = vector("spot")
    assert result.vector.values[7] == Decimal("0.04345956948178671213626861920931552")
    assert result.vector.values[14] == Decimal("0.002301496988279232727527413066911442")
    assert result.vector.values[:7] == GOLDEN[:7]
    assert vector("locked").vector.values[10] == 0


@pytest.mark.parametrize("extra,reason", [
    ("overlap-halt", "instrument_halted"), ("unknown-hours", "operability_interval_unknown"),
    ("future-unknown-hours", "operability_interval_unknown"), ("overlap-reference", "deliverable_unknown"),
])
def test_every_applicable_adverse_reference_and_status_blocks(extra, reason):
    result = vector(extra=(extra,))
    assert result.vector is None and reason in result.reasons
    assert result.return_features.values == GOLDEN[:7]


def test_shorter_current_common_close_and_unconsumed_schedules_are_distinct():
    short = vector(extra=("short-hours",))
    assert short.vector.values[12] == 295 and len(short.session_assessments) == 2
    fractional = vector(extra=("fractional-hours",))
    assert fractional.vector.values[12] == Decimal("295.0000000166666666666666666666667")
    scheduled = vector(extra=("scheduled-halt", "scheduled-reference", "optional-tick"))
    assert scheduled.vector.values == GOLDEN
    assert len(scheduled.context.tick_rules) == 1
    assert not {"scheduled-halt", "scheduled-reference", "optional-tick"} & {c.member.record_id for c in scheduled.source_components}
    assert scheduled.vector.input_hash != vector().vector.input_hash


def test_current_volume_must_match_the_actual_frozen_population_source_and_definition():
    for history, reason in (("wrong-source-bars", "normalization_source_mismatch"),
                            ("wrong-definition-bars", "volume_definition_unsupported")):
        result = vector(history=history)
        assert result.vector is None and reason in result.reasons
        assert result.return_features.values == GOLDEN[:7]


def test_unsupported_moneyness_preserves_reached_components_without_partial_vector():
    result = vector("moneyness-bound")
    assert result.vector is None and result.reasons == ("arithmetic_precision_unsupported",)
    assert result.vwap_feature.distance == GOLDEN[7] and result.return_features.values == GOLDEN[:7]


def test_observed_bid_is_unrounded_and_ambient_decimal_context_is_untouched():
    ordinary = vector("raw-precision").vector
    assert ordinary.values[16] == Decimal("4.90000000000000000000000000000000000001")
    for precision in (2, 120):
        with localcontext() as ambient:
            ambient.prec = precision
            ambient.rounding = ROUND_UP
            ambient.Emin, ambient.Emax, ambient.clamp = -5, 5, 1
            flags = ambient.flags.copy()
            changed = vector("raw-precision").vector
            assert changed.values == ordinary.values and changed.content_hash == ordinary.content_hash
            assert ambient.flags == flags


def test_original_time_slot_and_actual_member_objects_cannot_be_substituted():
    owner, ctx = api(), context().context
    good = dict(now=NOW, spec=owner.EXACT_VWAP_SPEC, normalization=normalization())
    altered = owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], **{**good, "now": NOW + timedelta(microseconds=1)})
    assert altered.vector is None and "original_decision_mismatch" in altered.reasons
    for quote, greek, reason in ((replace(ctx.option_quotes[0]), ctx.greeks[0], "quote_not_selected"),
                                 (ctx.option_quotes[0], replace(ctx.greeks[0]), "greek_not_selected"),
                                 (ctx.option_quotes[0], None, "greek_not_selected")):
        result = owner.build_features(ctx, quote, greek, **good)
        assert result.vector is None and reason in result.reasons


def test_entry_window_policy_does_not_replace_numerical_source_readiness():
    from datetime import time
    from options_lab.config import StrategyConfig
    default = vector().vector
    result = vector(config=replace(StrategyConfig(), entry_start=time(10, 10)))
    assert result.vector.values == default.values
    assert "decision_outside_configured_entry_window" in result.context.timing.reasons
    assert result.vector.config_hash != default.config_hash and result.vector.input_hash != default.input_hash


def test_full_frozen_normalization_never_updates_from_current_volume():
    owner, ctx, norm = api(), context().context, normalization()
    before = norm.baseline.snapshot
    result = owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], now=NOW, spec=owner.EXACT_VWAP_SPEC, normalization=norm)
    assert result.vector.values[9] == 1 and norm.baseline.snapshot == before
    assert norm.baseline.buckets[-1].sample_count == 20
    unready = owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], now=NOW, spec=owner.EXACT_VWAP_SPEC,
                                  normalization=normalization("raw-omission"))
    assert unready.vector is None and "normalization_bucket_unready" in unready.reasons


@pytest.mark.parametrize("name", ["FeatureSpec", "FeatureVector", "FeatureResult", "FeatureSpecValidation", "FeatureSpecInputRejection"])
def test_public_factories_cannot_mint_success_from_caller_fields(name):
    with pytest.raises(TypeError):
        getattr(api(), name)()


@pytest.mark.parametrize("field", ["schema_version", "feature_schema_id", "transform_id", "extra"])
def test_raw_selector_rejects_hostile_leaves_without_their_hooks(field):
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality")
        def __str__(self):
            raise AssertionError("hostile stringification")
    owner = api()
    raw = dict(schema_version=1, feature_schema_id=owner.FEATURE_SCHEMA_ID, transform_id=owner.TRANSFORM_ID)
    raw[field] = Hostile()
    result = owner.normalize_feature_spec(raw, event_id="spec", raw_ref="spec", received_at=NOW)
    assert result.value is None and result.rejection is not None


def test_artifact_availability_is_rechecked_at_the_original_context_decision():
    owner = api()
    ctx = context("warmup", at=NOW - timedelta(minutes=5)).context
    result = owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], now=ctx.decision_at,
                                  spec=owner.EXACT_VWAP_SPEC, normalization=normalization("at-decision"))
    assert result.vector is None and "normalization_availability_unusable" in result.reasons
    ctx = context().context
    first = owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], now=NOW,
                                 spec=owner.EXACT_VWAP_SPEC, normalization=normalization("at-cutoff"))
    second = owner.build_features(ctx, ctx.option_quotes[0], ctx.greeks[0], now=NOW,
                                  spec=owner.EXACT_VWAP_SPEC, normalization=normalization("at-decision"))
    assert first.vector.values == second.vector.values == GOLDEN
    assert first.vector.normalization_hash != second.vector.normalization_hash
    assert first.vector.input_hash != second.vector.input_hash


def test_required_member_inventory_keeps_real_envelope_and_original_availability():
    result = vector()
    records = result.vector.input_snapshot["source_records"]
    for record, component in zip(records, result.source_components):
        assert record["raw_hash"] == component.member.raw_hash
        assert record["envelope_hash"] == hashlib.sha256(component.member.envelope_bytes).hexdigest()
        assert record["available_at"] == component.available_at.isoformat()
    assert result.vector.input_hash == hashlib.sha256(result.vector.input_bytes).hexdigest()
    assert result.vector.input_snapshot["greek_observed_input_hash"] == result.greek_readiness.expected_input_hash


def test_missing_current_reference_never_uses_an_unrelated_favorable_record():
    result = vector(omit=("good-contract_reference",))
    assert result.vector is None and "contract_reference_missing" in result.reasons
    assert result.reference_assessments == () and result.vwap_feature.distance == GOLDEN[7]


def test_spec_snapshots_hash_actual_order_and_cannot_be_mutated_through_views():
    owner = api()
    for spec in (owner.EXACT_VWAP_SPEC, owner.CLOSE_VOLUME_PROXY_SPEC):
        canonical = lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        assert hashlib.sha256(canonical(spec.schema_snapshot)).hexdigest() == spec.feature_schema_id
        assert hashlib.sha256(canonical(spec.transform_snapshot)).hexdigest() == spec.transform_id
        changed = spec.schema_snapshot
        changed["fields"][0]["name"] = "not-a-feature"
        assert spec.schema_snapshot["fields"][0]["name"] == "log_return_1m"
    rejected = owner.normalize_feature_spec(dict(schema_version=1, feature_schema_id=owner.FEATURE_SCHEMA_ID,
                                                transform_id=owner.PROXY_TRANSFORM_ID),
                                            event_id="spec", raw_ref="spec", received_at=NOW)
    assert rejected.value is None and rejected.rejection.code == "unsupported_spec"


@pytest.mark.parametrize("field,value", [("context", None), ("quote", None), ("greek", False),
                                         ("now", NOW.replace(tzinfo=None)), ("spec", "exact"), ("normalization", {})])
def test_builder_trusted_type_and_time_errors_are_explicit(field, value):
    owner, ctx = api(), context().context
    args = dict(context=ctx, quote=ctx.option_quotes[0], greek=ctx.greeks[0], now=NOW,
                spec=owner.EXACT_VWAP_SPEC, normalization=normalization())
    args[field] = value
    with pytest.raises((TypeError, ValueError)):
        owner.build_features(**args)


def test_source_event_age_is_independent_of_fresher_side_and_availability_times():
    result = vector("source-age")
    assert result.vector.values[19] == 3
    assert result.quote.bid_at == result.quote.ask_at == NOW - timedelta(seconds=2)
    assert result.quote.meta.available_at == NOW - timedelta(seconds=1)
    assert result.vector.values[:19] == GOLDEN[:19]
