"""Pure risk state uses admitted account and calendar evidence."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.risk_inputs import normalize_risk_control


NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
VERIFIED = datetime(2026, 9, 7, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures/p12c-account-context-v1.json"
RISK_FIXTURE = Path(__file__).parent / "fixtures/p16b-risk-state-v1.json"


def context(*record_ids):
    """Build a real current P08 context from registered source records."""
    admitted = verify_fixture_bundle("p12c-account-context-v1", FIXTURE.read_bytes(),
                                     event_id="risk-verify", raw_ref="risk-test", received_at=VERIFIED)
    assert admitted.value is not None, admitted.rejection
    request = normalize_context_request(dict(decision_id="risk-decision", decision_at=NOW.isoformat(),
                                             member_record_ids=list(record_ids)), event_id="risk-request",
                                        raw_ref="risk-test", received_at=VERIFIED)
    built = build_decision_context(request, admitted.value, config=StrategyConfig(), previous_feature_state=None)
    assert built.context is not None
    return built.context


def test_start_uses_admitted_session_base_and_peak():
    """Losing the admitted base or peak would let a later drawdown hide."""
    from options_lab.risk import start_risk_state

    state = start_risk_state(context("flat", "session"), config=StrategyConfig(), now=NOW)
    assert state.session_start_equity == Decimal("100000")
    assert state.high_water_mark == Decimal("100000")
    assert state.session_entry_count == 0
    assert state.halt_state.entries == ()
    assert len(state.risk_revision) == 64


def risk_context(name, *, at=NOW, session="session", config=None):
    """Build a requested source-backed account/calendar transition."""
    admitted = verify_fixture_bundle("p16b-risk-state-v1", RISK_FIXTURE.read_bytes(),
                                     event_id="risk-sequence-verify", raw_ref="risk-sequence-test",
                                     received_at=VERIFIED)
    assert admitted.value is not None, admitted.rejection
    request = normalize_context_request(dict(decision_id="risk-" + name,
                                             decision_at=at.isoformat(),
                                             member_record_ids=[name, session]),
                                        event_id="request-" + name, raw_ref="risk-sequence-test",
                                        received_at=VERIFIED)
    built = build_decision_context(request, admitted.value, config=config or StrategyConfig(),
                                   previous_feature_state=None)
    assert built.context is not None, built.rejections
    return built.context


def request_control(kind, state, *, reason=None, event_id="operator-1", amount=None,
                    previous_event=None, current_event=None, before=None, after=None,
                    occurred_at=NOW, received_at=NOW):
    """Normalize one exact local control request bound to the current state."""
    body = dict(schema_version=1, kind=kind, account_id="risk-account",
                source="fixture-account-ledger", actor_id="operator-1",
                occurred_at=occurred_at.isoformat(), expected_risk_revision=state.risk_revision)
    if kind == "cashflow":
        body.update(previous_account_event_id=previous_event, current_account_event_id=current_event,
                    ledger_before=before, ledger_after=after, amount=amount)
    else:
        body["reason"] = reason
    result = normalize_risk_control(json.dumps(body).encode(), event_id=event_id,
                                    raw_ref="local://operator", received_at=received_at)
    assert result.value is not None, result.rejection
    return result.value


def test_exact_loss_and_drawdown_latch_independently_and_recovery_does_not_clear():
    """Skipping either money check or clearing on recovery would unlock risk."""
    from options_lab.risk import advance_risk_state, start_risk_state

    start = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    touched = advance_risk_state(start, risk_context("simultaneous", at=NOW + timedelta(seconds=1)),
                                 config=StrategyConfig(), now=NOW + timedelta(seconds=1))
    assert tuple(e.reason for e in touched.halt_state.entries) == ("daily_loss", "drawdown")
    assert touched.high_water_mark == Decimal("100000")
    recovered = advance_risk_state(touched, risk_context("recovered", at=NOW + timedelta(seconds=2)),
                                   config=StrategyConfig(), now=NOW + timedelta(seconds=2))
    assert tuple(e.reason for e in recovered.halt_state.entries) == ("daily_loss", "drawdown")
    assert recovered.halt_state.entries[0].latched_at == NOW + timedelta(seconds=1)


def test_new_verified_flat_session_clears_only_session_latch():
    """Clearing persistent drawdown on a calendar change would reopen entry."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("simultaneous", at=NOW + timedelta(seconds=1)),
                             config=StrategyConfig(), now=NOW + timedelta(seconds=1))
    next_at = datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc)
    following = advance_risk_state(first, risk_context("next-flat", at=next_at, session="next-session"),
                                   config=StrategyConfig(), now=next_at)
    assert tuple(e.reason for e in following.halt_state.entries) == ("drawdown",)
    assert following.session_entry_count == 0
    assert following.session_start_equity == Decimal("95000")
    assert following.high_water_mark == Decimal("100000")
    assert following.halt_state.resolved_entries[0].reason == "daily_loss"
    assert following.halt_state.resolved_entries[0].reset_evidence


def test_audited_drawdown_reset_clears_only_drawdown_after_fresh_recovery():
    """A named reset cannot erase the same-session daily-loss latch."""
    from options_lab.risk import advance_risk_state, start_risk_state

    touched_at = NOW + timedelta(seconds=1)
    touched = start_risk_state(risk_context("simultaneous", at=touched_at),
                               config=StrategyConfig(), now=touched_at)
    recovered_at = NOW + timedelta(seconds=2)
    control = request_control("reset", touched, reason="drawdown", event_id="reset-drawdown",
                              occurred_at=recovered_at, received_at=recovered_at)
    state = advance_risk_state(touched, risk_context("recovered", at=recovered_at),
                               config=StrategyConfig(), now=recovered_at, control=control)
    assert tuple(e.reason for e in state.halt_state.entries) == ("daily_loss",)
    assert state.halt_state.resolved_entries[0].reason == "drawdown"
    assert state.halt_state.resolved_entries[0].reset_evidence[0] == "reset-drawdown"


def test_reset_timestamp_before_latch_cannot_clear_later_drawdown():
    """A backdated audited request cannot clear a loss observed after it occurred."""
    from options_lab.risk import advance_risk_state, start_risk_state

    touched_at = NOW + timedelta(seconds=1)
    touched = start_risk_state(risk_context("simultaneous", at=touched_at),
                               config=StrategyConfig(), now=touched_at)
    old = request_control("reset", touched, reason="drawdown", event_id="old-reset")
    recovered_at = NOW + timedelta(seconds=2)
    state = advance_risk_state(touched, risk_context("recovered", at=recovered_at),
                               config=StrategyConfig(), now=recovered_at, control=old)
    assert "drawdown" in tuple(e.reason for e in state.halt_state.entries)
    assert "ledger_corrupt" in tuple(e.reason for e in state.halt_state.entries)


def test_cashflow_adjusts_base_and_peak_once_and_semantic_redelivery_is_stable():
    """A duplicate local cashflow must not raise the drawdown base twice."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("cash-before"), config=StrategyConfig(), now=NOW)
    control = request_control("cashflow", first, amount="12.50", previous_event="event-cash-before",
                              current_event="event-cash-after", before="ledger-cash-before",
                              after="ledger-cash-after")
    current = risk_context("cash-after", at=NOW + timedelta(seconds=1))
    applied = advance_risk_state(first, current, config=StrategyConfig(),
                                 now=NOW + timedelta(seconds=1), control=control)
    assert applied.session_start_equity == Decimal("100012.50")
    assert applied.high_water_mark == Decimal("100012.50")
    duplicate = advance_risk_state(applied, current, config=StrategyConfig(),
                                   now=NOW + timedelta(seconds=1), control=control)
    assert duplicate.session_start_equity == applied.session_start_equity
    assert duplicate.high_water_mark == applied.high_water_mark
    assert duplicate.risk_revision == applied.risk_revision


def test_completed_matching_fill_counts_once_at_configured_limit():
    """A third completed one-contract fill must count immediately, not retroactively."""
    from options_lab.risk import advance_risk_state, start_risk_state

    state = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    for number in (1, 2, 3):
        if number != 1:
            at = NOW + timedelta(seconds=number * 2 - 2)
            state = advance_risk_state(state, risk_context(f"order{number}-zero", at=at),
                                       config=StrategyConfig(), now=at)
        at = NOW + timedelta(seconds=number * 2 - 1)
        state = advance_risk_state(state, risk_context(f"order{number}-one", at=at),
                                   config=StrategyConfig(), now=at)
        assert state.session_entry_count == number
    assert tuple(e.reason for e in state.halt_state.entries) == ("entry_count",)


def test_inclusive_money_boundaries_and_admitted_peak_claim():
    """One cent above each floor must pass; the admitted earlier peak matters."""
    from options_lab.risk import start_risk_state

    assert tuple(e.reason for e in start_risk_state(risk_context("loss-exact"),
        config=StrategyConfig(), now=NOW).halt_state.entries) == ("daily_loss",)
    assert start_risk_state(risk_context("loss-above"),
        config=StrategyConfig(), now=NOW).halt_state.entries == ()
    assert tuple(e.reason for e in start_risk_state(risk_context("dd-exact"),
        config=StrategyConfig(), now=NOW).halt_state.entries) == ("drawdown",)
    assert start_risk_state(risk_context("dd-above"),
        config=StrategyConfig(), now=NOW).halt_state.entries == ()
    assert tuple(e.reason for e in start_risk_state(risk_context("prior-peak"),
        config=StrategyConfig(), now=NOW).halt_state.entries) == ("drawdown",)


@pytest.mark.parametrize("reason", ("kill_switch", "manual_halt"))
def test_protective_halt_during_disconnection_and_selected_audited_reset(reason):
    """A disconnected broker cannot prevent a kill; reset clears only its reason."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    halt = request_control("halt", first, reason=reason, event_id="kill-1")
    down_at = NOW + timedelta(seconds=1)
    down = advance_risk_state(first, risk_context("disconnected", at=down_at),
                              config=StrategyConfig(), now=down_at, control=halt)
    assert tuple(e.reason for e in down.halt_state.entries) == (reason, "broker_disconnected", "broker_unreconciled")
    reset = request_control("reset", down, reason=reason, event_id="reset-1")
    up_at = NOW + timedelta(seconds=2)
    up = advance_risk_state(down, risk_context("recovered", at=up_at),
                            config=StrategyConfig(), now=up_at, control=reset)
    assert up.halt_state.entries == ()
    assert tuple(e.reason for e in up.halt_state.resolved_entries) == (
        reason, "broker_disconnected", "broker_unreconciled")
    assert up.halt_state.resolved_entries[0].latch_evidence[0] == "kill-1"
    assert up.halt_state.resolved_entries[0].reset_evidence[0] == "reset-1"


def test_invalid_control_fails_closed_without_erasing_possible_exposure():
    """A stale local revision must not turn a rejected reset into a healthy state."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    raw = dict(schema_version=1, kind="reset", reason="drawdown", account_id="risk-account",
               source="fixture-account-ledger", actor_id="operator-1", occurred_at=NOW.isoformat(),
               expected_risk_revision="stale")
    normalized = normalize_risk_control(json.dumps(raw).encode(), event_id="stale-reset",
                                        raw_ref="local://operator", received_at=NOW)
    assert normalized.value is not None
    later = advance_risk_state(first, risk_context("order1-zero"), config=StrategyConfig(),
                               now=NOW, control=normalized.value)
    assert later.possible_exposure
    assert "ledger_corrupt" in tuple(e.reason for e in later.halt_state.entries)


def test_bad_fill_quantities_never_fabricate_entry_count():
    """Fractional and q2 transitions retain exposure without crediting an entry."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    for name in ("order1-half", "order1-two"):
        later_at = NOW + timedelta(seconds=1)
        later = advance_risk_state(first, risk_context(name, at=later_at),
                                   config=StrategyConfig(), now=later_at)
        assert later.session_entry_count == 0
        assert later.possible_exposure
        assert "ledger_corrupt" in tuple(e.reason for e in later.halt_state.entries)


def test_regression_or_changed_client_preserves_uncertainty_without_count():
    """A regressed or reidentified fill cannot become a completed entry fact."""
    from options_lab.risk import advance_risk_state, start_risk_state

    zero = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    at = NOW + timedelta(seconds=1)
    mismatch = advance_risk_state(zero, risk_context("order1-mismatch", at=at),
                                  config=StrategyConfig(), now=at)
    assert mismatch.session_entry_count == 0
    assert "ledger_corrupt" in tuple(e.reason for e in mismatch.halt_state.entries)
    one = advance_risk_state(zero, risk_context("order1-one", at=at),
                             config=StrategyConfig(), now=at)
    later_at = NOW + timedelta(seconds=2)
    regress = advance_risk_state(one, risk_context("order1-regress", at=later_at),
                                 config=StrategyConfig(), now=later_at)
    assert regress.session_entry_count == 1
    assert "ledger_corrupt" in tuple(e.reason for e in regress.halt_state.entries)


def test_repeated_completion_after_regression_cannot_count_stable_order_twice():
    """One completed order identity can earn one count despite a source regression."""
    from options_lab.risk import advance_risk_state, start_risk_state

    state = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    for name, seconds in (("order1-one", 1), ("order1-regress", 2), ("order1-one", 3)):
        at = NOW + timedelta(seconds=seconds)
        state = advance_risk_state(state, risk_context(name, at=at), config=StrategyConfig(), now=at)
    assert state.session_entry_count == 1
    assert state.possible_exposure
    assert "ledger_corrupt" in tuple(e.reason for e in state.halt_state.entries)


@pytest.mark.parametrize("initial,final", (
    ("order1-sibling-zero", "order1-sibling-vanished"),
    ("order1-zero", "order1-one-same-event"),
))
def test_ambiguous_order_lineage_never_grants_completion_credit(initial, final):
    """A vanished sibling or conflicting source event denies a completed count."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context(initial), config=StrategyConfig(), now=NOW)
    at = NOW + timedelta(seconds=1)
    later = advance_risk_state(first, risk_context(final, at=at), config=StrategyConfig(), now=at)
    assert later.session_entry_count == 0
    assert later.possible_exposure
    assert "ledger_corrupt" in tuple(e.reason for e in later.halt_state.entries)


def test_configured_single_entry_limit_latches_on_first_completed_fill():
    """The state must use actual max_entries_per_session, including tighter policy."""
    from options_lab.risk import advance_risk_state, start_risk_state

    config = StrategyConfig(max_entries_per_session=1)
    first = start_risk_state(risk_context("order1-zero", config=config), config=config, now=NOW)
    at = NOW + timedelta(seconds=1)
    state = advance_risk_state(first, risk_context("order1-one", at=at, config=config),
                               config=config, now=at)
    assert state.session_entry_count == 1
    assert tuple(e.reason for e in state.halt_state.entries) == ("entry_count",)


def test_same_event_material_conflict_but_opaque_ref_refresh_is_harmless():
    """Changing broker risk refs alone must not conceal a real cash conflict."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    at = NOW + timedelta(seconds=1)
    refreshed = advance_risk_state(first, risk_context("riskref-refresh", at=at),
                                   config=StrategyConfig(), now=at)
    assert refreshed.halt_state.entries == ()
    assert refreshed.risk_revision == first.risk_revision
    conflict = advance_risk_state(first, risk_context("same-event-conflict", at=at),
                                  config=StrategyConfig(), now=at)
    assert "ledger_corrupt" in tuple(e.reason for e in conflict.halt_state.entries)


def test_cashflow_wrong_amount_cannot_adjust_base_and_latches_failure():
    """Cashflow identity alone cannot authorize an unequal cash delta."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("cash-before"), config=StrategyConfig(), now=NOW)
    control = request_control("cashflow", first, amount="13", previous_event="event-cash-before",
                              current_event="event-cash-after", before="ledger-cash-before",
                              after="ledger-cash-after")
    at = NOW + timedelta(seconds=1)
    state = advance_risk_state(first, risk_context("cash-after", at=at),
                               config=StrategyConfig(), now=at, control=control)
    assert state.session_start_equity == Decimal("100000")
    assert "ledger_corrupt" in tuple(e.reason for e in state.halt_state.entries)


def test_replay_rejects_copied_state_with_forged_count():
    """A copied state field cannot substitute for replay of admitted fill history."""
    from options_lab.risk import RiskAdvanceError, advance_risk_state, start_risk_state
    import pytest

    first = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    at = NOW + timedelta(seconds=1)
    counted = advance_risk_state(first, risk_context("order1-one", at=at),
                                 config=StrategyConfig(), now=at)
    object.__setattr__(counted, "session_entry_count", 0)
    with pytest.raises(RiskAdvanceError) as failure:
        advance_risk_state(counted, risk_context("order1-one", at=at),
                           config=StrategyConfig(), now=at)
    assert failure.value.reason == "risk_state_replay_mismatch"
    assert failure.value.previous is counted
    assert failure.value.observation.account.event_id == "event-order1-one"


def test_later_source_high_water_and_known_adverse_bounds_survive_bad_orders():
    """Order uncertainty must not erase known source peak, realized loss, or equity."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    at = NOW + timedelta(seconds=1)
    peak = advance_risk_state(first, risk_context("later-peak", at=at),
                              config=StrategyConfig(), now=at)
    assert peak.high_water_mark == Decimal("110000")
    assert "drawdown" in tuple(e.reason for e in peak.halt_state.entries)
    adverse = advance_risk_state(first, risk_context("adverse-order", at=at),
                                 config=StrategyConfig(), now=at)
    assert adverse.last_observation.account_assessment.conservative_daily_pnl is None
    assert adverse.last_observation.account_assessment.conservative_virtual_equity is None
    assert tuple(e.reason for e in adverse.halt_state.entries) == ("daily_loss", "drawdown", "ledger_corrupt")
    assert adverse.possible_exposure


def test_semantic_control_redelivery_ignores_json_and_receipt_but_detects_id_conflict():
    """One semantic cashflow effect survives alternate transport, while ID conflict halts."""
    from options_lab.risk import advance_risk_state, start_risk_state

    first = start_risk_state(risk_context("cash-before"), config=StrategyConfig(), now=NOW)
    control = request_control("cashflow", first, amount="12.50", previous_event="event-cash-before",
                              current_event="event-cash-after", before="ledger-cash-before",
                              after="ledger-cash-after")
    at = NOW + timedelta(seconds=1)
    current = risk_context("cash-after", at=at)
    applied = advance_risk_state(first, current, config=StrategyConfig(), now=at, control=control)
    body = json.loads(control.raw_bytes)
    body.update(amount="12.5", occurred_at="2026-09-04T10:05:00-04:00")
    redelivery = normalize_risk_control(json.dumps(body, separators=(",", ":")).encode(),
        event_id="operator-redelivery", raw_ref="local://redelivery", received_at=at)
    assert redelivery.value is not None and redelivery.value.content_hash == control.content_hash
    repeated = advance_risk_state(applied, current, config=StrategyConfig(),
                                  now=at, control=redelivery.value)
    assert repeated.risk_revision == applied.risk_revision
    assert repeated.session_start_equity == Decimal("100012.50")
    body["amount"] = "13"
    conflict = normalize_risk_control(json.dumps(body).encode(), event_id="operator-1",
                                      raw_ref="local://conflict", received_at=at)
    assert conflict.value is not None
    blocked = advance_risk_state(repeated, current, config=StrategyConfig(),
                                 now=at, control=conflict.value)
    assert "ledger_corrupt" in tuple(e.reason for e in blocked.halt_state.entries)


def test_history_limit_keeps_reached_observation_and_previous_exposure():
    """Oversized forged history must fail before replay and retain account evidence."""
    from options_lab.risk import RiskAdvanceError, advance_risk_state, start_risk_state
    import pytest

    previous = start_risk_state(risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    object.__setattr__(previous, "transcript", previous.transcript * 257)
    with pytest.raises(RiskAdvanceError) as failure:
        advance_risk_state(previous, risk_context("order1-zero"), config=StrategyConfig(), now=NOW)
    assert failure.value.reason == "history_limit"
    assert failure.value.previous is previous and previous.possible_exposure
    assert failure.value.observation.account.event_id == "event-order1-zero"


def test_missing_account_source_recovers_only_with_fresh_p08_account():
    """Unknown genesis account must not become a permanent false source conflict."""
    from options_lab.risk import advance_risk_state, start_risk_state

    absent = start_risk_state(context("session"), config=StrategyConfig(), now=NOW)
    assert tuple(e.reason for e in absent.halt_state.entries) == (
        "broker_unreconciled", "market_data_unready")
    found = advance_risk_state(absent, context("flat", "session"),
                               config=StrategyConfig(), now=NOW)
    assert found.account_id == "account-flat"
    assert found.halt_state.entries == ()
    assert tuple(e.reason for e in found.halt_state.resolved_entries) == (
        "broker_unreconciled", "market_data_unready")


def test_actual_cash_bundle_distinguishes_unready_corrupt_and_verified_recovery():
    """A provided cash bundle must pass its owner; absence grants no fake recovery."""
    from options_lab.risk import advance_risk_state, start_risk_state
    from test_bundle_availability import available
    from test_feature_vector import context as market_context

    market = market_context().context
    assert market is not None
    first = start_risk_state(market, config=StrategyConfig(), now=NOW)
    waiting = available("cash-unknown-gap").value
    corrupt = available("cash-inconsistent").value
    healthy = available("cash-available").value
    assert waiting is not None and corrupt is not None and healthy is not None
    unready = advance_risk_state(first, market, config=StrategyConfig(), now=NOW, bundle=waiting)
    assert "model_unready" in tuple(e.reason for e in unready.halt_state.entries)
    unchanged = advance_risk_state(unready, market, config=StrategyConfig(), now=NOW)
    assert "model_unready" in tuple(e.reason for e in unchanged.halt_state.entries)
    recovered = advance_risk_state(unchanged, market, config=StrategyConfig(), now=NOW, bundle=healthy)
    assert "model_unready" not in tuple(e.reason for e in recovered.halt_state.entries)
    broken = advance_risk_state(first, market, config=StrategyConfig(), now=NOW, bundle=corrupt)
    assert "model_corrupt" in tuple(e.reason for e in broken.halt_state.entries)
    assert broken.last_observation.bundle_assessment is not None


def test_model_corrupt_reset_requires_same_reverified_model_content_path():
    """A healthy different model cannot clear a prior corrupt model latch."""
    from options_lab.risk import advance_risk_state, start_risk_state
    from test_bundle_availability import available

    bundles = {name: available("cash-risk-" + name).value for name in
               ("corrupt", "valid", "other")}
    assert all(bundle is not None for bundle in bundles.values())
    first = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    bad = advance_risk_state(first, risk_context("base"), config=StrategyConfig(),
                             now=NOW, bundle=bundles["corrupt"])
    assert "model_corrupt" in tuple(e.reason for e in bad.halt_state.entries)
    assert bad.last_observation.bundle_assessment.bundle is not None
    assert bundles["corrupt"].model.model_hash == bundles["valid"].model.model_hash
    assert bad.halt_state.entries[0].latch_evidence[1:4] == (
        bundles["corrupt"].model.model_kind, bundles["corrupt"].manifest.model_id,
        bundles["corrupt"].model.model_hash)
    at = NOW + timedelta(seconds=1)
    current = risk_context("riskref-refresh", at=at)
    control = request_control("reset", bad, reason="model_corrupt", event_id="reset-model",
                              occurred_at=at, received_at=at)
    blocked = advance_risk_state(bad, current, config=StrategyConfig(), now=at,
                                 bundle=bundles["other"], control=control)
    assert blocked.last_observation.bundle_assessment.available
    assert "model_corrupt" in tuple(e.reason for e in blocked.halt_state.entries)
    assert "ledger_corrupt" in tuple(e.reason for e in blocked.halt_state.entries)
    recovered = advance_risk_state(bad, current, config=StrategyConfig(), now=at,
                                   bundle=bundles["valid"], control=control)
    assert recovered.last_observation.bundle_assessment.available
    assert "model_corrupt" not in tuple(e.reason for e in recovered.halt_state.entries)
    assert recovered.halt_state.resolved_entries[0].reason == "model_corrupt"


def test_model_corrupt_unknown_identity_cannot_be_reset_as_known_model():
    """A rejected retained bundle has no model identity with reset authority."""
    from copy import copy
    from options_lab.risk import advance_risk_state, start_risk_state
    from test_bundle_availability import available

    healthy = available("cash-risk-valid").value
    assert healthy is not None
    damaged = copy(healthy)
    object.__setattr__(damaged, "original_fixture", None)
    first = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    bad = advance_risk_state(first, risk_context("base"), config=StrategyConfig(),
                             now=NOW, bundle=damaged)
    assert bad.last_observation.bundle_assessment.bundle is None
    assert bad.halt_state.entries[0].latch_evidence[1:4] == ("unknown",) * 3
    at = NOW + timedelta(seconds=1)
    reset = request_control("reset", bad, reason="model_corrupt", event_id="reset-unknown-model",
                            occurred_at=at, received_at=at)
    blocked = advance_risk_state(bad, risk_context("riskref-refresh", at=at),
                                 config=StrategyConfig(), now=at, bundle=healthy, control=reset)
    assert blocked.last_observation.bundle_assessment.available
    assert "model_corrupt" in tuple(e.reason for e in blocked.halt_state.entries)


def test_model_reset_without_active_corruption_denies_without_exception():
    """A named reset cannot index an absent latch as if proof existed."""
    from options_lab.risk import advance_risk_state, start_risk_state
    from test_bundle_availability import available

    healthy = available("cash-risk-valid").value
    assert healthy is not None
    first = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    at = NOW + timedelta(seconds=1)
    reset = request_control("reset", first, reason="model_corrupt", event_id="reset-no-model",
                            occurred_at=at, received_at=at)
    later = advance_risk_state(first, risk_context("riskref-refresh", at=at),
                               config=StrategyConfig(), now=at, bundle=healthy, control=reset)
    assert "ledger_corrupt" in tuple(e.reason for e in later.halt_state.entries)


def test_unsupported_account_money_keeps_source_and_latches_ledger_corruption():
    """An unbounded amount cannot disappear behind a missing local hash."""
    from options_lab.risk import start_risk_state

    state = start_risk_state(context("huge", "session"), config=StrategyConfig(), now=NOW)
    assert state.last_account.event_id == "event-huge"
    assert "ledger_corrupt" in tuple(e.reason for e in state.halt_state.entries)
    assert state.possible_exposure


def test_future_control_receipt_cannot_authorize_a_halt_or_reset():
    """A normalized request delivered after now is not current local control proof."""
    from options_lab.risk import advance_risk_state, start_risk_state

    state = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    body = dict(schema_version=1, kind="halt", reason="manual_halt", account_id="risk-account",
                source="fixture-account-ledger", actor_id="operator-1", occurred_at=NOW.isoformat(),
                expected_risk_revision=state.risk_revision)
    future = normalize_risk_control(json.dumps(body).encode(), event_id="future-halt",
                                    raw_ref="local://future", received_at=NOW + timedelta(seconds=1))
    assert future.value is not None
    blocked = advance_risk_state(state, risk_context("base"), config=StrategyConfig(),
                                 now=NOW, control=future.value)
    assert "manual_halt" not in tuple(e.reason for e in blocked.halt_state.entries)
    assert "ledger_corrupt" in tuple(e.reason for e in blocked.halt_state.entries)


def test_out_of_order_decision_time_fails_closed_with_current_evidence():
    """An earlier P08 context cannot roll back a later observed risk state."""
    from options_lab.risk import RiskAdvanceError, advance_risk_state, start_risk_state
    import pytest

    at = NOW + timedelta(seconds=1)
    later = start_risk_state(risk_context("simultaneous", at=at),
                             config=StrategyConfig(), now=at)
    with pytest.raises(RiskAdvanceError) as failure:
        advance_risk_state(later, risk_context("base"), config=StrategyConfig(), now=NOW)
    assert failure.value.reason == "observation_regression"
    assert failure.value.observation.account.event_id == "event-base"
    assert tuple(e.reason for e in later.halt_state.entries) == ("daily_loss", "drawdown")


def test_damaged_retained_transition_time_fails_closed_before_nested_replay():
    """A forged naive historical instant cannot bubble out as a raw owner error."""
    from options_lab.risk import RiskAdvanceError, advance_risk_state, start_risk_state

    state = start_risk_state(risk_context("base"), config=StrategyConfig(), now=NOW)
    object.__setattr__(state.transcript[0], "now", NOW.replace(tzinfo=None))
    with pytest.raises(RiskAdvanceError) as failure:
        advance_risk_state(state, risk_context("base"), config=StrategyConfig(), now=NOW)
    assert failure.value.reason == "risk_state_replay_mismatch"
    assert failure.value.observation.account.event_id == "event-base"
