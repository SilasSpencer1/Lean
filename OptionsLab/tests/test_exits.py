"""Exit policy consumes admitted account, calendar, instrument, and mark facts."""

from datetime import datetime, timedelta, timezone
from copy import copy
import json
from pathlib import Path

import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import verify_bundle
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.exits import evaluate_exit, observe_exit_inputs
from options_lab.runtime import measure_runtime
from options_lab.risk import advance_risk_state, start_risk_state
from options_lab.risk_inputs import normalize_risk_control


FOLDER = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)


def context(fixture_id, at, *records):
    """Build an actual current context from registered source bytes.

    :param    fixture_id: Registered synthetic fixture identity.
    :param    at:         Explicit current decision instant.
    :param    records:    Actual requested source member IDs.
    :returns:             Owner-produced decision context.
    """
    raw = (FOLDER / (fixture_id + ".json")).read_bytes()
    admitted = verify_fixture_bundle(fixture_id, raw, event_id="exit-verify",
        raw_ref="exit-test", received_at=at + timedelta(days=3))
    assert admitted.value is not None, admitted.rejection
    request = normalize_context_request(dict(decision_id="exit-" + at.isoformat(),
        decision_at=at.isoformat(), member_record_ids=list(records)),
        event_id="exit-request", raw_ref="exit-test", received_at=at)
    result = build_decision_context(request, admitted.value, config=StrategyConfig(),
        previous_feature_state=None)
    assert result.context is not None, result.rejections
    return result.context


def cash_bundle():
    """Verify an actual B2 cash bundle covering the P18 source fixture.

    :returns:             Source-backed healthy cash bundle.
    """
    def admitted(name):
        """Admit one current registered fixture needed by the bundle.

        :param    name: Catalogued fixture ID.
        :returns:       Verified actual source manifest.
        """
        raw = (FOLDER / (name + ".json")).read_bytes()
        result = verify_fixture_bundle(name, raw, event_id="exit-bundle-verify",
            raw_ref="exit-bundle-test", received_at=NOW + timedelta(days=3))
        assert result.value is not None, result.rejection
        return result.value

    fixture = admitted("p18-exit-cash-bundle-v1")
    body = next(member for member in fixture.members if member.record_id == "cash").decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], event_id="exit-cash",
        raw_ref="exit-cash-test", received_at=NOW).value
    assert manifest is not None
    names = ("p14c2-sources-v1", "p14c2-calendar-v1", "p14b-schedule-v1",
        "p11-feature-vector-v1", "p14b1b-assembly-v1", "p18-exit-source-v1",
        "p18-exit-validation-v1")
    result = verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=None, normalization=None, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=tuple(admitted(name) for name in names))
    assert result.value is not None, (result.rejection.field, result.rejection.code)
    return result.value


def decision(at, account, *, fixture_id="p12c-account-context-v1", session="session",
             instrument="good-instrument_tradability", mark="good-option_quote", halts=None,
             bundle=None):
    """Evaluate one source-selected account and independent exit evidence.

    :param    at:         Exact current instant.
    :param    account:    Requested account record ID.
    :param    fixture_id: Registered source bundle ID.
    :param    session:    Requested calendar record, or None.
    :param    instrument: Requested instrument record, or None.
    :param    mark:       Requested held mark record, or None.
    :param    halts:      Previous source-backed risk state, or None.
    :param    bundle:     Optional actual B2 bundle.
    :returns:             Canonical exit instruction from actual source owners.
    """
    ids = [account]
    ids.extend(x for x in (session, instrument, mark) if x is not None)
    evidence = observe_exit_inputs(context(fixture_id, at, *ids), config=StrategyConfig(),
        now=at, halts=halts, bundle=bundle)
    return evaluate_exit(evidence.position, evidence.orders, evidence.session,
        halts, evidence, at, StrategyConfig())


def test_actual_account_flat_and_bid_only_held_mark():
    """A missing ask and Greeks do not turn a current held bid into failure."""
    flat = decision(NOW, "flat", instrument=None, mark=None)
    held = decision(NOW, "bid-only", mark="mark-bid-only")
    assert (flat.action, flat.reason) == ("flat", "observed_flat")
    assert (held.action, held.reason) == ("monitor", "before_horizon")
    assert held.position_identity == ("fixture-holdings", "holding-report", "position-1")


def test_adverse_multiple_holdings_reconcile_without_invented_quantity():
    """Recovery exposure remains visible even when no sole position exists."""
    result = decision(NOW, "adverse")
    assert result.action == "reconcile"
    assert result.reason == "unknown_exposure"
    assert result.position_identity is None


def test_missing_nested_mark_source_requests_safety_exit_for_known_holding():
    """A missing bound mark is a safety trigger, not a false flat claim."""
    result = decision(NOW, "occupied", mark=None)
    assert (result.action, result.reason) == ("prepare_exit", "safety_data")
    assert result.reconciliation_required


@pytest.mark.parametrize("name,stamp,action,reason", [
    ("horizon-before", "2026-09-04T14:34:59+00:00", "monitor", "before_horizon"),
    ("horizon-at", "2026-09-04T14:35:00+00:00", "prepare_exit", "holding_period"),
    ("liquidation-before", "2026-09-04T19:34:59+00:00", "monitor", "before_horizon"),
    ("liquidation-at", "2026-09-04T19:35:00+00:00", "prepare_exit", "liquidation_window"),
    ("escalation-at", "2026-09-04T19:39:00+00:00", "reconcile", "escalation"),
    ("deadline-at", "2026-09-04T19:40:00+00:00", "incident", "deadline"),
])
def test_exact_horizon_and_liquidation_clocks(name, stamp, action, reason):
    """Exact source clocks decide the boundary without fabricating a fill."""
    at = datetime.fromisoformat(stamp)
    result = decision(at, name + "-account", fixture_id="p18-exit-source-v1",
        mark=name + "-mark")
    assert (result.action, result.reason) == (action, reason)
    assert result.deadline_incident == (reason == "deadline")


def test_early_close_and_missing_calendar_recovery():
    """Actual common hours shorten clocks; missing calendar cannot allow blind exit."""
    at = datetime(2026, 9, 4, 17, 35, tzinfo=timezone.utc)
    early = decision(at, "early-at-account", fixture_id="p18-exit-source-v1",
        session="early-session", instrument="early-instrument", mark="early-at-mark")
    unknown = decision(at, "early-at-account", fixture_id="p18-exit-source-v1",
        session=None, instrument="early-instrument", mark="early-at-mark")
    assert (early.action, early.reason) == ("prepare_exit", "liquidation_window")
    assert (unknown.action, unknown.reason) == ("reconcile", "unknown_session")
    assert unknown.effective_deadline == datetime(2026, 9, 4, 17, 40, tzinfo=timezone.utc)


@pytest.mark.parametrize("name,action,reason", [
    ("unknown-fill", "prepare_exit", "unknown_fill_time"),
    ("prior-session", "prepare_exit", "prior_session_holding"),
    ("prior-local-day", "prepare_exit", "prior_session_holding"),
    ("quantity-two", "monitor", "before_horizon"),
    ("quantity-unknown", "reconcile", "unknown_exposure"),
    ("stale-bid", "prepare_exit", "safety_data"),
    ("missing-bid", "prepare_exit", "safety_data"),
    ("disconnected", "reconcile", "broker_mismatch"),
    ("stale-account", "reconcile", "unknown_exposure"),
    ("empty-but-entry", "reconcile", "pending_entry"),
])
def test_recovery_facts_and_safety_do_not_invent_flat_or_price(name, action, reason):
    """Independent adverse account facts retain their distinct policy outcomes."""
    result = decision(NOW, name + "-account", fixture_id="p18-exit-source-v1",
        mark=name + "-mark" if name != "empty-but-entry" else None)
    assert (result.action, result.reason) == (action, reason)


def test_pending_sell_suppresses_duplicate_prepare_but_not_deadline_incident():
    """An outstanding sell is no fill and cannot hide a possible exposure."""
    horizon = decision(datetime(2026, 9, 4, 14, 35, tzinfo=timezone.utc),
        "pending-horizon-account", fixture_id="p18-exit-source-v1",
        mark="pending-horizon-mark")
    deadline = decision(datetime(2026, 9, 4, 19, 40, tzinfo=timezone.utc),
        "pending-deadline-account", fixture_id="p18-exit-source-v1",
        mark="pending-deadline-mark")
    assert (horizon.action, horizon.reason) == ("monitor", "pending_exit")
    assert (deadline.action, deadline.reason, deadline.deadline_incident) == (
        "incident", "deadline", True)


def test_pending_entry_and_unknown_calendar_keep_actual_deadline_incident():
    """A known order instrument supplies clocks without claiming a holding fill."""
    at = datetime(2026, 9, 4, 19, 40, tzinfo=timezone.utc)
    entry = decision(at, "empty-entry-deadline-account",
        fixture_id="p18-exit-source-v1", mark=None)
    no_calendar = decision(at, "deadline-at-account",
        fixture_id="p18-exit-source-v1", session=None, mark="deadline-at-mark")
    wrong_day = decision(at, "deadline-at-account",
        fixture_id="p18-exit-source-v1", session="wrong-day-session",
        mark="deadline-at-mark")
    assert (entry.action, entry.reason, entry.deadline_incident) == (
        "incident", "deadline", True)
    assert entry.position_identity is None
    for result in (no_calendar, wrong_day):
        assert (result.action, result.reason, result.deadline_incident) == (
            "reconcile", "unknown_session", True)


def test_optional_healthy_cash_and_bad_source_bound_bundle():
    """Only a provided invalid bundle is a model safety trigger."""
    actual_cash = cash_bundle()
    absent = decision(NOW, "quantity-two-account", fixture_id="p18-exit-source-v1",
        mark="quantity-two-mark")
    healthy = decision(NOW, "quantity-two-account", fixture_id="p18-exit-source-v1",
        mark="quantity-two-mark", bundle=actual_cash)
    invalid = decision(NOW, "occupied", bundle=actual_cash)
    assert (absent.action, absent.reason) == ("monitor", "before_horizon")
    assert (healthy.action, healthy.reason) == ("monitor", "before_horizon")
    assert healthy.evidence.risk_observation.bundle_assessment.available
    assert (invalid.action, invalid.reason) == ("prepare_exit", "safety_model")


def test_source_backed_daily_loss_and_operator_kill_request_exit():
    """Current adverse money and retained control proof trigger independent exit."""
    names = ("session", "good-instrument_tradability")
    daily_context = context("p18-exit-source-v1", NOW,
        "daily-loss-account", "daily-loss-mark", *names)
    daily_state = start_risk_state(daily_context, config=StrategyConfig(), now=NOW)
    daily = decision(NOW, "daily-loss-account", fixture_id="p18-exit-source-v1",
        mark="daily-loss-mark", halts=daily_state)
    assert (daily.action, daily.reason) == ("prepare_exit", "safety_loss")
    assert daily.evidence.advanced_risk_state is not None

    held_context = context("p18-exit-source-v1", NOW,
        "quantity-two-account", "quantity-two-mark", *names)
    first = start_risk_state(held_context, config=StrategyConfig(), now=NOW)
    raw = json.dumps(dict(schema_version=1, kind="halt", account_id="exit-account",
        source="fixture-account-ledger", actor_id="operator", occurred_at=NOW.isoformat(),
        expected_risk_revision=first.risk_revision, reason="kill_switch")).encode()
    control = normalize_risk_control(raw, event_id="exit-kill", raw_ref="local://operator",
        received_at=NOW).value
    assert control is not None
    killed = advance_risk_state(first, held_context, config=StrategyConfig(), now=NOW,
        control=control)
    result = decision(NOW, "quantity-two-account", fixture_id="p18-exit-source-v1",
        mark="quantity-two-mark", halts=killed)
    assert (result.action, result.reason) == ("prepare_exit", "safety_halt")


def test_failed_risk_replay_cannot_veto_known_liquidation():
    """A future prior state is retained as uncertainty while the exit remains due."""
    later = datetime(2026, 9, 4, 19, 40, tzinfo=timezone.utc)
    prior = start_risk_state(context("p18-exit-source-v1", later,
        "deadline-at-account", "deadline-at-mark", "session",
        "good-instrument_tradability"), config=StrategyConfig(), now=later)
    at = datetime(2026, 9, 4, 19, 35, tzinfo=timezone.utc)
    result = decision(at, "liquidation-at-account", fixture_id="p18-exit-source-v1",
        mark="liquidation-at-mark", halts=prior)
    assert (result.action, result.reason) == ("prepare_exit", "liquidation_window")
    assert result.reconciliation_required
    assert result.evidence.risk_advance_reason == "observation_regression"
    assert result.evidence.advanced_risk_state is None


@pytest.mark.parametrize("name,stamp,reason", [
    ("horizon-at", "2026-09-04T14:35:00+00:00", "holding_period"),
    ("unknown-fill", "2026-09-04T14:05:00+00:00", "unknown_fill_time"),
    ("prior-session", "2026-09-04T14:05:00+00:00", "prior_session_holding"),
])
def test_damaged_risk_history_cannot_veto_independent_due_exit(name, stamp, reason):
    """A failed replay adds reconciliation while current holding evidence decides exit."""
    at = datetime.fromisoformat(stamp)
    prior = start_risk_state(context("p18-exit-source-v1", at,
        name + "-account", name + "-mark", "session", "good-instrument_tradability"),
        config=StrategyConfig(), now=at)
    damaged = copy(prior)
    object.__setattr__(damaged, "risk_revision", "damaged-retained-revision")
    result = decision(at, name + "-account", fixture_id="p18-exit-source-v1",
        mark=name + "-mark", halts=damaged)
    assert (result.action, result.reason) == ("prepare_exit", reason)
    assert result.reconciliation_required
    assert result.evidence.risk_advance_reason == "risk_state_replay_mismatch"
    assert result.evidence.advanced_risk_state is None


def test_future_risk_history_cannot_veto_holding_period_exit():
    """An actual later prior observation cannot suppress a current horizon exit."""
    later = datetime(2026, 9, 4, 19, 40, tzinfo=timezone.utc)
    prior = start_risk_state(context("p18-exit-source-v1", later,
        "deadline-at-account", "deadline-at-mark", "session",
        "good-instrument_tradability"), config=StrategyConfig(), now=later)
    at = datetime(2026, 9, 4, 14, 35, tzinfo=timezone.utc)
    result = decision(at, "horizon-at-account", fixture_id="p18-exit-source-v1",
        mark="horizon-at-mark", halts=prior)
    assert (result.action, result.reason) == ("prepare_exit", "holding_period")
    assert result.reconciliation_required
    assert result.evidence.risk_advance_reason == "observation_regression"


def test_retained_exit_evidence_rejects_tamper_without_hostile_equality():
    """Recheck complete owner facts before consulting copied favorable claims."""
    evidence = observe_exit_inputs(context("p12c-account-context-v1", NOW,
        "occupied", "session", "good-instrument_tradability"),
        config=StrategyConfig(), now=NOW)

    class Hostile:
        """Fail if a forged nested value receives an equality call."""

        def __eq__(self, other):
            """Reject unsafe comparison to the forged value."""
            raise AssertionError("hostile equality executed")

    forged = copy(evidence)
    object.__setattr__(forged, "source_reasons", (Hostile(),))
    with pytest.raises(ValueError, match="recheck"):
        evaluate_exit(forged.position, forged.orders, forged.session, None,
            forged, NOW, StrategyConfig())

    favorable = copy(evidence)
    observed = copy(evidence.risk_observation)
    object.__setattr__(observed, "mark_source_reasons", ())
    object.__setattr__(favorable, "risk_observation", observed)
    with pytest.raises(ValueError, match="recheck"):
        evaluate_exit(favorable.position, favorable.orders, favorable.session, None,
            favorable, NOW, StrategyConfig())
    with pytest.raises(TypeError, match="exact trusted types"):
        evaluate_exit(evidence.position, evidence.orders, evidence.session, None,
            object(), NOW, StrategyConfig())
