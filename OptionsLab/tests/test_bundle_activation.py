"""Select a verified bundle using real calendar gaps and current context."""

from copy import copy
from datetime import datetime, timezone

import pytest

from options_lab.bundle_activation import choose_bundle_activation
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import verify_bundle
from options_lab.runtime import measure_runtime
from test_bundle_schedule import bundle_result
from test_bundle_availability import available
from test_bundle_schedule import admitted
from test_feature_vector import context as market_context


def gap_context(at, record="session-2026-08-31"):
    """Build actual session-only context from the registered C2 source."""
    source = admitted("p14d-aug-context-v1" if record.endswith("08-31") else "p14d-sep-context-v1")
    request = normalize_context_request(dict(decision_id="activation", decision_at=at.isoformat(),
        member_record_ids=[record]), event_id="activation-request", raw_ref="synthetic://activation",
        received_at=at)
    context = build_decision_context(request, source, config=StrategyConfig(),
                                     previous_feature_state=None).context
    assert context is not None and context.input_reasons == ()
    assert context.option_quotes == () and context.greeks == ()
    return context


def registered(record):
    """Reverify a registered D bundle from all real declared source owners."""
    fixture = admitted("p14d-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], event_id="d-test",
        raw_ref="synthetic://d-test", received_at=datetime(2026, 10, 2, tzinfo=timezone.utc)).value
    assert manifest is not None
    common = ("p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1",
              "p14b1b-assembly-v1", "p11-feature-vector-v1")
    fixed = record == "fixed-september"
    extra = (("p14d-aug-context-v1",) if fixed else
             ("p14d-aug-context-v1", "p14d-schedule-v1") if record in ("cash-renamed", "cash-at-close") else
             ("p14d-schedule-v1",) if record == "cash-later-gap" else
             ("p14d-aug-context-v1", "p14d-sep-calendar-v1", "p14d-schedule-v1")
             if record == "cash-calendar-renamed" else
             ("p14d-sep-context-v1", "p14d-oct-sources-v1", "p14d-oct-calendar-v1",
              "p14d-schedule-v1"))
    if fixed:
        common = ("p14c-partition-samples-v1", "p14c-partition-memberships-v1",
                  "p14c2-partition-v1", "p14c2-record-v1", *common)
    prior = available("fixed-available").value if fixed else None
    assert not fixed or prior is not None
    result = verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=prior.spec if fixed else None,
        normalization=prior.supplied_normalization if fixed else None,
        config=StrategyConfig(), runtime=measure_runtime().value,
        upstream_fixtures=tuple(admitted(name) for name in (*common, *extra)))
    assert result.value is not None, result.rejection
    return result


def choose(current, candidate, at, context=None):
    """Call the public selection boundary with actual trusted owners."""
    return choose_bundle_activation(current, candidate, context=context or gap_context(at),
        config=StrategyConfig(), now=at)


def test_first_gap_is_closed_at_previous_close_and_open_until_next_open():
    """A fresh available bundle enters only the actual first-session gap."""
    candidate = registered("cash-renamed")
    for at in (datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
               datetime(2026, 9, 1, 2, tzinfo=timezone.utc),
               datetime(2026, 9, 1, 4, tzinfo=timezone.utc)):
        result = choose(None, candidate, at)
        assert result.disposition == "candidate_selected", result.reasons
        assert result.selected is not candidate.value
        assert result.candidate_assessment is not None and result.candidate_assessment.available
    at = datetime(2026, 8, 31, 20, tzinfo=timezone.utc)
    early = choose(None, candidate, at)
    assert early.disposition == "cash_fallback"
    assert "bundle_unavailable" in early.candidate_assessment.reasons
    at = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    result = choose(None, candidate, at)
    assert result.disposition == "cash_fallback"
    assert "outside_first_activation_gap" in result.candidate_assessment.reasons


def test_fixed_model_selects_with_real_c2_support_in_session_only_gap():
    """The same D path admits a calibrated fixed forecast without entry quotes."""
    at = datetime(2026, 9, 1, 2, tzinfo=timezone.utc)
    candidate = registered("fixed-september")
    result = choose(None, candidate, at)
    assert result.disposition == "candidate_selected", result.reasons
    assert result.selected.model.model_kind != "cash"
    assert result.candidate_assessment.context_recheck.valid
    assert len(result.candidate_assessment.calibration) == 2
    assert all(item.supported for item in result.candidate_assessment.calibration)


def test_unavailable_candidate_cannot_enter_and_current_is_assessed_independently():
    """A rejected or unavailable challenger cannot dislodge a healthy current."""
    at = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
    current = available("cash-available").value
    assert current is not None
    rejected = available("cash-inconsistent")
    result = choose_bundle_activation(current, rejected, context=market_context().context,
        config=StrategyConfig(), now=at)
    assert result.disposition == "current_retained"
    assert result.selected is not current
    assert result.current_assessment.available
    assert not result.candidate_assessment.available
    assert "simulated_availability_mismatch" in result.candidate_assessment.reasons


def test_same_identity_is_idempotent_but_expired_current_is_not_retained():
    """The same freshly checked bundle stays current only on its healthy horizon."""
    at = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
    original = available("cash-available")
    current = original.value
    assert current is not None
    result = choose_bundle_activation(current, original, context=market_context().context,
        config=StrategyConfig(), now=at)
    assert result.disposition == "current_retained"
    assert result.selected.bundle_hash == current.bundle_hash
    later = datetime(2026, 10, 2, 14, 5, tzinfo=timezone.utc)
    result = choose_bundle_activation(current, original,
        context=market_context(at=later).context, config=StrategyConfig(), now=later)
    assert result.disposition == "cash_fallback"
    assert result.selected is None
    assert "outside_current_horizon" in result.current_assessment.reasons


def test_same_interval_locks_renamed_calendar_and_block():
    """A new descriptor owner and block name cannot dislodge healthy current."""
    at = datetime(2026, 9, 1, 2, tzinfo=timezone.utc)
    current = registered("cash-renamed").value
    assert current is not None
    challenger = registered("cash-calendar-renamed")
    result = choose(current, challenger, at)
    assert result.current_assessment.available and result.candidate_assessment.available
    assert result.current_assessment.bundle.bundle_hash != result.candidate_assessment.bundle.bundle_hash
    assert result.current_assessment.bundle.evaluation_block.content_hash != result.candidate_assessment.bundle.evaluation_block.content_hash
    assert result.current_assessment.bundle.evaluation_block.calendar.content_hash != result.candidate_assessment.bundle.evaluation_block.calendar.content_hash
    assert result.disposition == "current_retained"


def test_adjacent_october_gap_selects_new_manifest_with_same_cash_model():
    """An expired September bundle does not lock the next real monthly interval."""
    at = datetime(2026, 10, 1, 2, tzinfo=timezone.utc)
    current = registered("cash-renamed").value
    challenger = registered("cash-october")
    assert current is not None and challenger.value is not None
    assert current.model.model_hash == challenger.value.model.model_hash
    result = choose(current, challenger, at, gap_context(at, "session-2026-09-30"))
    assert result.disposition == "candidate_selected", result.reasons
    assert result.current_assessment is not None and not result.current_assessment.available
    assert result.candidate_assessment.available
    assert result.selected.bundle_hash != current.bundle_hash
    assert result.selected.model.model_kind == "cash"
    assert result.selected is not None
    after_open = datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc)
    result = choose(None, challenger, after_open, gap_context(after_open, "session-2026-09-30"))
    assert result.disposition == "cash_fallback"


def test_later_session_gap_cannot_activate_september_block():
    """A gap after the block's first open cannot serve as its activation gap."""
    at = datetime(2026, 9, 4, 21, tzinfo=timezone.utc)
    source = admitted("p11-feature-vector-v1")
    request = normalize_context_request(dict(decision_id="after-close", decision_at=at.isoformat(),
        member_record_ids=["session"]), event_id="d-request", raw_ref="synthetic://d-request",
        received_at=at)
    context = build_decision_context(request, source, config=StrategyConfig(),
                                     previous_feature_state=None).context
    assert context is not None and context.input_reasons == ()
    result = choose(None, registered("cash-later-gap"), at, context)
    assert result.disposition == "cash_fallback"
    assert "outside_first_activation_gap" in result.candidate_assessment.reasons


def test_previous_close_is_inclusive_and_before_close_excluded():
    """An honestly available candidate enters exactly at the previous close."""
    at = datetime(2026, 8, 31, 20, tzinfo=timezone.utc)
    candidate = registered("cash-at-close")
    result = choose(None, candidate, at)
    assert result.disposition == "candidate_selected", result.reasons
    assert result.candidate_assessment.available_at == at
    before = datetime(2026, 8, 31, 19, 59, 59, tzinfo=timezone.utc)
    assert choose(None, candidate, before).disposition == "cash_fallback"


def test_damaged_candidate_exclusivity_and_top_types_are_bounded():
    """A copied verification cannot claim both success and rejection."""
    at = datetime(2026, 9, 1, 2, tzinfo=timezone.utc)
    candidate = registered("cash-renamed")
    damaged = copy(candidate)
    object.__setattr__(damaged, "rejection", object())
    result = choose(None, damaged, at)
    assert result.disposition == "cash_fallback"
    assert result.candidate_assessment is None
    assert "candidate_verification_invalid" in result.reasons
    with pytest.raises(TypeError):
        choose_bundle_activation(None, object(), context=gap_context(at),
            config=StrategyConfig(), now=at)


def test_native_rejection_is_retained_and_current_is_still_rechecked():
    """A failed actual A2 verification does not short circuit the healthy current."""
    at = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
    rejected = bundle_result("wrong-block-hash")
    assert rejected.value is None and rejected.rejection is not None
    current = available("cash-available").value
    assert current is not None
    result = choose_bundle_activation(current, rejected, context=market_context().context,
        config=StrategyConfig(), now=at)
    assert result.disposition == "current_retained"
    assert result.candidate_verification is rejected
    assert result.candidate_verification.rejection is rejected.rejection
    assert result.candidate_assessment is None
    assert result.current_assessment.available


@pytest.mark.parametrize("damage", ("gap", "runtime", "source", "member"))
def test_damaged_retained_owners_cannot_select(damage):
    """Fresh content, runtime, source, and owner checks reject copied damage."""
    at = datetime(2026, 9, 1, 2, tzinfo=timezone.utc)
    candidate = registered("cash-renamed")
    changed = copy(candidate.value)
    if damage == "gap":
        object.__setattr__(changed, "activation_gap", None)
    elif damage == "runtime":
        runtime = copy(changed.supplied_runtime)
        object.__setattr__(runtime, "implementation_digest", "0" * 64)
        object.__setattr__(changed, "supplied_runtime", runtime)
    elif damage == "source":
        object.__setattr__(changed, "supplied_upstream_fixtures", (object(),))
    else:
        object.__setattr__(changed, "member", object())
    altered = copy(candidate)
    object.__setattr__(altered, "value", changed)
    result = choose(None, altered, at)
    assert result.disposition == "cash_fallback"
    assert result.candidate_assessment is not None
    assert result.candidate_assessment.reasons
    assert not result.candidate_assessment.available


def test_wrong_public_types_and_real_config_mismatch():
    """Public boundary types fail explicitly; a different policy fails by proof."""
    at = datetime(2026, 9, 1, 2, tzinfo=timezone.utc)
    candidate = registered("cash-renamed")
    context = gap_context(at)
    good = dict(current=None, candidate=candidate, context=context,
                config=StrategyConfig(), now=at)
    for name in good:
        with pytest.raises(TypeError):
            choose_bundle_activation(**{**good, name: object()})
    changed = choose_bundle_activation(None, candidate, context=context,
        config=StrategyConfig(max_entries_per_session=2), now=at)
    assert changed.disposition == "cash_fallback"
    assert changed.candidate_assessment.reasons
