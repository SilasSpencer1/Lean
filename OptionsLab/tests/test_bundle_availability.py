"""Exercise source covered current bundle availability and causal schedules."""

from copy import copy
from datetime import datetime, timedelta, timezone

import pytest

from options_lab.bundle_availability import assess_bundle
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from test_bundle_validation import report_bundle
from test_feature_vector import context as market_context


NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def available(record):
    """Reverify a registered B2 member with all original A2 source owners."""
    from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
    from options_lab.bundles import verify_bundle
    from options_lab.feature_vector import EXACT_VWAP_SPEC
    from options_lab.runtime import measure_runtime
    from options_lab.volume_normalization import normalize_feature_normalization
    from test_bundle_schedule import admitted

    fixture = admitted("p14b2-availability-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], event_id="b2-test",
        raw_ref="synthetic://b2-test", received_at=NOW).value
    assert manifest is not None
    fixed = record.startswith("fixed-")
    names = ["p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1",
             "p14b1b-assembly-v1", "p11-feature-vector-v1"]
    if record.startswith("cash-risk-"):
        names.append("p16b-risk-state-v1")
    norm = None
    if fixed:
        names = ["p14c-partition-samples-v1", "p14c-partition-memberships-v1",
            "p14c2-partition-v1", "p14c2-record-v1", *names]
        training = admitted("p14c-volume-training-v1")
        artifact = admitted("p14c-volume-normalization-v1")
        member = next(m for m in artifact.members if m.record_id == "good")
        norm = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact,
            record_id="good", training_manifest=training,
            decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
        assert norm is not None
    return verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC if fixed else None, normalization=norm,
        config=StrategyConfig(), runtime=measure_runtime().value,
        upstream_fixtures=tuple(admitted(name) for name in names))


@pytest.mark.parametrize("record", ("fixed-available", "cash-available"))
def test_registered_bundle_available_at_actual_current_context(record):
    """Real context and honest registered modeled gap clocks authorize core use."""
    bundle = available(record).value
    assert bundle is not None
    context = market_context().context
    assert context is not None
    result = assess_bundle(bundle, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert result.available and result.synthetic_core_fixture_capable, result.reasons
    assert result.bundle is not bundle and result.context_recheck.valid
    assert result.available_at == datetime(2026, 9, 1, 1, tzinfo=timezone.utc)
    assert (len(result.calibration) == 0) == (record == "cash-available")
    assert not result.operational_allowed and not result.economic_allowed


@pytest.mark.parametrize("record,reason", (("fixed-training-only", "current_source_profile_uncovered"),
    ("fixed-empty-profiles", "current_source_profile_uncovered"),
    ("fixed-bad-delay", "calibration_after_fit_completion"),
    ("fixed-overflow-delay", "schedule_delay_overflow"),
    ("fixed-actual-unknown", "actual_activation_unknown"),
    ("fixed-actual-reversed", "actual_activation_order"),
    ("cash-inconsistent", "simulated_availability_mismatch"),
    ("cash-no-schedule", "simulated_schedule_missing"),
    ("cash-unknown-gap", "activation_gap_unavailable"),
    ("cash-outside-gap", "availability_outside_gap")))
def test_registered_adverse_content_cannot_authorize(record, reason):
    """Actual source coverage and schedule consistency are required."""
    bundle = available(record).value
    assert bundle is not None
    context = market_context().context
    result = assess_bundle(bundle, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert not result.available and reason in result.reasons


def test_tier0_denies_higher_uses_even_when_mechanics_pass():
    """Operational and economic use are separate explicit denials."""
    bundle = available("cash-available").value
    context = market_context().context
    assert bundle is not None and context is not None
    for requested, reason in (("operational_paper", "operational_use_unsupported"),
                              ("economic_research", "economic_use_unsupported")):
        result = assess_bundle(bundle, context=context, config=StrategyConfig(),
                               now=NOW, requested_use=requested)
        assert not result.available and reason in result.reasons
        assert result.synthetic_core_fixture_capable


def test_irrelevant_future_audit_does_not_need_feature_profile():
    """Only consumed current and retained history roles need feature claims."""
    bundle = available("fixed-available").value
    context = market_context(extra=("aaa-future-malformed",)).context
    assert bundle is not None and context is not None
    assert any(item.member.record_id == "aaa-future-malformed" and item.disposition == "future"
               for item in context.components)
    result = assess_bundle(bundle, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert result.available, result.reasons


def test_bundle_prerequisites_accept_real_session_only_after_close():
    """Availability uses source integrity without inventing entry quote readiness."""
    original = market_context()
    at = datetime(2026, 9, 4, 21, tzinfo=timezone.utc)
    request = normalize_context_request(dict(decision_id="after-close", decision_at=at.isoformat(),
        member_record_ids=["session"]), event_id="request", raw_ref="request", received_at=at)
    current = build_decision_context(request, original.manifest, config=StrategyConfig(),
                                     previous_feature_state=None).context
    bundle = available("cash-available").value
    assert bundle is not None and current is not None and current.input_reasons == ()
    assert current.option_quotes == () and current.greeks == ()
    result = assess_bundle(bundle, context=current, config=StrategyConfig(),
                           now=at, requested_use="core_fixture")
    assert result.available, result.reasons


def test_selected_tick_requires_its_current_source_profile():
    """A selected optional policy input cannot borrow unrelated profile coverage."""
    bundle = available("fixed-available").value
    context = market_context(extra=("optional-tick",)).context
    assert bundle is not None and context is not None
    assert any(item.member.record_id == "optional-tick" and item.disposition == "selected"
               for item in context.components)
    result = assess_bundle(bundle, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert not result.available and "current_source_profile_uncovered" in result.reasons


def test_retained_prior_bar_history_requires_declared_profile():
    """A source-profile gap in P06 retained history cannot authorize the bundle."""
    original = market_context()
    assert original.context is not None and original.context.feature_state is not None
    later = NOW + timedelta(seconds=1)
    request = normalize_context_request(dict(decision_id="later", decision_at=later.isoformat(),
        member_record_ids=["session", "bars-correction", "good-option_quote", "good-underlying_quote", "good-greek",
                           "good-proof", "good-instrument_tradability",
                           "good-provider_contract_mapping", "good-contract_reference"]),
        event_id="request", raw_ref="request", received_at=later)
    retained = build_decision_context(request, original.manifest, config=StrategyConfig(),
                                      previous_feature_state=original.context.feature_state).context
    assert retained is not None and retained.previous_feature_state is not None
    good = available("fixed-available").value
    bad = available("fixed-no-bars").value
    assert good is not None and bad is not None
    yes = assess_bundle(good, context=retained, config=StrategyConfig(),
                        now=later, requested_use="core_fixture")
    no = assess_bundle(bad, context=retained, config=StrategyConfig(),
                       now=later, requested_use="core_fixture")
    assert yes.available, yes.reasons
    assert not no.available and "current_source_profile_uncovered" in no.reasons


def test_current_context_config_clock_and_evaluation_interval():
    """Current evidence cannot be projected to another config, time or block."""
    bundle = available("cash-available").value
    context = market_context().context
    assert bundle is not None and context is not None
    different = assess_bundle(bundle, context=context,
        config=StrategyConfig(max_entries_per_session=2), now=NOW, requested_use="core_fixture")
    assert not different.available
    later = assess_bundle(bundle, context=context, config=StrategyConfig(),
        now=NOW + timedelta(days=30), requested_use="core_fixture")
    assert not later.available and "context_time_mismatch" in later.reasons
    outside = market_context(at=datetime(2026, 10, 2, 14, 5, tzinfo=timezone.utc)).context
    assert outside is not None
    result = assess_bundle(bundle, context=outside, config=StrategyConfig(),
        now=outside.decision_at, requested_use="core_fixture")
    assert not result.available and "outside_evaluation_block" in result.reasons


def test_fresh_bundle_and_report_are_required():
    """Retained copies and content-only A2 bundles cannot replace fresh report proof."""
    context = market_context().context
    bundle = available("cash-available").value
    assert context is not None and bundle is not None
    altered = copy(bundle)
    object.__setattr__(altered, "validation_report", None)
    result = assess_bundle(altered, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert not result.available and "retained_content_mismatch" in result.reasons
    old = report_bundle("cash-validation").value
    assert old is not None
    result = assess_bundle(old, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert not result.available
    from test_bundle_schedule import bundle_result
    no_report = bundle_result("cash-schedule").value
    assert no_report is not None
    result = assess_bundle(no_report, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert "validation_report_missing" in result.reasons


def test_retained_runtime_and_source_cannot_be_reused_after_mutation():
    """A copied A2 proof cannot substitute a changed runtime or original source."""
    context = market_context().context
    bundle = available("cash-available").value
    assert context is not None and bundle is not None
    runtime = copy(bundle.supplied_runtime)
    object.__setattr__(runtime, "implementation_digest", "0" * 64)
    stale = copy(bundle)
    object.__setattr__(stale, "supplied_runtime", runtime)
    result = assess_bundle(stale, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert not result.available and "retained_content_mismatch" in result.reasons
    roots = list(bundle.supplied_upstream_fixtures)
    source = copy(roots[-1])
    object.__setattr__(source, "payload_sha256", "0" * 64)
    roots[-1] = source
    stale = copy(bundle)
    object.__setattr__(stale, "supplied_upstream_fixtures", tuple(roots))
    result = assess_bundle(stale, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert not result.available and "fresh_bundle_rejected" in result.reasons


def test_trusted_api_boundaries():
    """Top-level wrong types and unsupported use tokens are explicit API errors."""
    context = market_context().context
    bundle = available("cash-available").value
    assert context is not None and bundle is not None
    arguments = dict(bundle=bundle, context=context, config=StrategyConfig(),
                     now=NOW, requested_use="core_fixture")
    for name in arguments:
        with pytest.raises(TypeError):
            assess_bundle(**{**arguments, name: object()})
    with pytest.raises(ValueError):
        assess_bundle(bundle, context=context, config=StrategyConfig(), now=NOW, requested_use="future")


@pytest.mark.parametrize("field", ("member", "supplied_upstream_fixtures", "config"))
def test_wrong_nested_owner_is_bounded_without_hostile_hooks(field):
    """Wrong retained owner types are content failures, unlike public top-level types."""
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError("hostile attribute hook invoked")

        def __eq__(self, other):
            raise AssertionError("hostile equality hook invoked")

    context = market_context().context
    bundle = available("cash-available").value
    assert context is not None and bundle is not None
    altered = copy(bundle)
    object.__setattr__(altered, field, (Hostile(),) if field == "supplied_upstream_fixtures" else Hostile())
    result = assess_bundle(altered, context=context, config=StrategyConfig(),
                           now=NOW, requested_use="core_fixture")
    assert result.reasons == ("retained_content_mismatch",)
    assert result.bundle is None and result.context_recheck is None
