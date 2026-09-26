"""Opening authorization binds one original intent to fresh source and risk evidence."""

from copy import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.candidates import select_candidates
from options_lab.config import StrategyConfig
from options_lab.context import build_decision_context
from options_lab.context_inputs import normalize_context_request
from options_lab.decision import FixedJsonPredictor, _score_candidates
from options_lab.entry_intent import derive_trade_intent
from options_lab.feature_vector import EXACT_VWAP_SPEC, build_features
from options_lab.risk import start_risk_state
from options_lab.sessions import assess_submission_time
from test_decision import NOW, bundle, source
from test_risk import risk_context


P17_SOURCE = Path(__file__).parent / "fixtures/p17b-entry-source-v1.json"
P17_BUNDLE = Path(__file__).parent / "fixtures/p17b-entry-bundle-v1.json"
LATE = datetime(2026, 9, 4, 19, tzinfo=timezone.utc)


def test_registered_entry_source_has_actual_current_quote_and_accounts():
    """Current facts must be admitted records rather than forged owner fields."""
    admitted = verify_fixture_bundle("p17b-entry-source-v1", P17_SOURCE.read_bytes(),
        event_id="entry-source-check", raw_ref="entry-source-check", received_at=NOW)
    assert admitted.value is not None
    ids = {member.record_id for member in admitted.value.members}
    assert {"entry-worse-option_quote", "entry-wide-option_quote",
            "entry-fresh-option_quote", "riskref-refresh-account_snapshot",
            "material-change-account_snapshot", "disconnected-account_snapshot",
            "pending-order-account_snapshot", "late-flat-account_snapshot",
            "entry-order3-one-account_snapshot", "entry-outside-option_quote",
            "outside-flat-account_snapshot"} <= ids


def test_registered_entry_bundle_declares_actual_source_profiles():
    """The current source cannot gain model authority from an undeclared profile."""
    admitted = verify_fixture_bundle("p17b-entry-bundle-v1", P17_BUNDLE.read_bytes(),
        event_id="entry-bundle-check", raw_ref="entry-bundle-check", received_at=NOW)
    assert admitted.value is not None
    call = next(m for m in admitted.value.members if m.record_id == "call")
    body = call.decode_raw_body()
    profiles = body["manifest"]["feature_binding"]["source_profiles"]
    assert {"bars", "c99-option_quote", "account_snapshot",
            "entry-worse-option_quote", "entry-fresh-option_quote"} <= {row["profile_id"] for row in profiles
                                            if row["fixture_id"] == "p17b-entry-source-v1"}


def test_original_call_can_be_authorized_one_second_later_with_all_checks():
    """A valid opening must keep its original cap and pass every current guard."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call")
    original, candidates, vectors = source(model.normalization_result.value)
    decision = _score_candidates(original, candidates, vectors, FixedJsonPredictor(model), config)
    intent = derive_trade_intent(candidates, decision, config)
    assert intent is not None
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    request = normalize_context_request(dict(decision_id="current-entry",
        decision_at=current_at.isoformat(),
        member_record_ids=list(original.request.member_record_ids)),
        event_id="current-request", raw_ref="current-request", received_at=current_at)
    current = build_decision_context(request, original.manifest, config=config,
                                     previous_feature_state=None).context
    assert current is not None
    account = next(c.value for c in current.selected_components
                   if c.member.kind == "account_snapshot")

    result = authorize_entry(intent, current, account, current.session, config, current_at,
                             previous_risk_state=previous, bundle=model)

    assert result.approved
    assert result.intent is intent
    assert result.reason is None
    assert len(result.checks) == 16
    assert all(check.passed for check in result.checks)
    assert result.portfolio_revision == "ledger-1"
    assert result.candidate_assessment.option_quote.ask <= intent.limit_price
    assert result.timing_assessment.timing_suitable
    assert result.premium_budget.required_cash == intent.max_cost == 531


def _selected_account(context):
    """Return the account selected by the actual admitted context."""
    return next(c.value for c in context.selected_components
                if c.member.kind == "account_snapshot")


def _entry_source(at, *, account_record="account_snapshot", quote_variant=None,
                  fixture_id="p17b-entry-source-v1", include_bars=True):
    """Build one actual P17 selected source context at its explicit clock."""
    path = (P17_SOURCE if fixture_id == "p17b-entry-source-v1" else
            Path(__file__).parent / "fixtures/p15-decision-source-v1.json")
    fixture = verify_fixture_bundle(fixture_id, path.read_bytes(),
        event_id="p17-test-source", raw_ref="p17-test-source", received_at=NOW).value
    assert fixture is not None
    ids = ["session", account_record,
           "entry-" + quote_variant + "-underlying_quote"
           if quote_variant in ("fresh", "outside") else "underlying_quote"]
    population = ("c99",) if quote_variant in ("fresh", "outside") else ("c99", "c100", "p99", "pdelta")
    ids.extend(member.record_id for member in fixture.members if
        include_bars and member.record_id.startswith("bars-") or
        member.record_id.startswith(tuple(name + "-" for name in
            population)) and
        member.record_id != "c99-redelivery")
    if quote_variant:
        ids = [name for name in ids if name not in
            ("c99-option_quote", "c99-greek_observation", "c99-quote_coherence")]
        ids.extend("entry-" + quote_variant + "-" + kind for kind in
                   ("option_quote", "greek_observation", "quote_coherence"))
    request = normalize_context_request(dict(decision_id="p17-" + at.isoformat(),
        decision_at=at.isoformat(), member_record_ids=ids),
        event_id="p17-request", raw_ref="p17-request", received_at=at)
    built = build_decision_context(request, fixture, config=StrategyConfig(),
                                   previous_feature_state=None)
    assert built.context is not None, built.rejections
    return built.context


def _entry_intent(model):
    """Reprove the original 14:05 choice from the actual P17 source."""
    config = StrategyConfig()
    original = _entry_source(NOW)
    candidates = select_candidates(original, _selected_account(original), config)
    vectors = tuple(build_features(original, choice.option_quote,
        choice.greek_readiness.greek, now=NOW, spec=EXACT_VWAP_SPEC,
        normalization=model.normalization_result.value).vector
        for choice in candidates.selected)
    decision = _score_candidates(original, candidates, vectors,
                                 FixedJsonPredictor(model), config)
    intent = derive_trade_intent(candidates, decision, config)
    assert intent is not None
    return original, intent


def test_actual_session_clock_1500_is_inclusive_at_p04_owner():
    """P04 accepts the configured end and rejects the next microsecond."""
    config = StrategyConfig()
    context = _entry_source(LATE)
    tradability = next(item for item in context.tradability
                       if item.contract.strike == 99 and item.contract.right == "call")
    at_end = assess_submission_time(context.session, tradability, config=config,
        decision_at=LATE, original_expires_at=LATE + timedelta(seconds=5), now=LATE)
    later = assess_submission_time(context.session, tradability, config=config,
        decision_at=LATE, original_expires_at=LATE + timedelta(seconds=5),
        now=LATE + timedelta(microseconds=1))
    assert at_end.timing_suitable
    assert "outside_configured_entry_window" in later.reasons
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    call = next(item for item in context.option_quotes
                if item.contract.strike == 99 and item.contract.right == "call")
    greek = next(item for item in context.greeks if item.contract == call.contract)
    feature = build_features(context, call, greek, now=LATE,
        spec=EXACT_VWAP_SPEC, normalization=model.normalization_result.value)
    assert "normalization_bucket_missing" in feature.reasons
    assert feature.vector is None


def test_higher_current_ask_fails_readiness_before_submission_window():
    """The current ask cannot chase above the original submitted cap."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    current = _entry_source(current_at, quote_variant="worse")
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous, bundle=model)
    assert not result.approved and result.intent is None
    assert result.checks[-1].name == "data_model_readiness"
    assert "tick_cap:quote:ask_exceeds_original_cap" in result.checks[-1].reasons
    assert intent.limit_price == intent.original_ask_cap


@pytest.mark.parametrize(("variant", "check_name", "reason"), (
    ("wide", "premium_cap", "premium_cap:intent:reservation_ceiling_exceeded"),
    ("fresh", "submission_window", "original_intent_expired"),
))
def test_current_spread_and_absolute_expiry_keep_original_limit(variant, check_name, reason):
    """A later quote cannot raise reserved cost or renew the five-second clock."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=5 if variant == "fresh" else 1)
    current = _entry_source(current_at, quote_variant=variant)
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous, bundle=model)
    assert not result.approved and result.intent is None
    assert result.checks[-1].name == check_name
    assert reason in result.checks[-1].reasons
    assert intent.original_ask_cap == Decimal("5.10")
    if variant == "wide":
        assert result.premium_budget.required_cash == 541
        assert result.premium_budget.required_cash > intent.max_cost == 531


def test_refreshed_risk_references_are_allowed_but_material_change_is_not():
    """Local opaque refs may change; economic account facts remain frozen."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    refreshed = _entry_source(current_at, account_record="riskref-refresh-account_snapshot")
    good = authorize_entry(intent, refreshed, _selected_account(refreshed),
        refreshed.session, config, current_at, previous_risk_state=previous, bundle=model)
    assert good.approved and good.intent is intent
    changed = _entry_source(current_at, account_record="material-change-account_snapshot")
    denied = authorize_entry(intent, changed, _selected_account(changed), changed.session,
        config, current_at, previous_risk_state=previous, bundle=model)
    assert not denied.approved and denied.checks[-1].name == "input_integrity"
    assert "input_integrity:account:material_mismatch" in denied.checks[-1].reasons


def test_current_bundle_source_identity_fails_before_model_corrupt_halt():
    """Actual B2 source mismatch owns check three despite P16's independent latch."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    old_source = _entry_source(current_at, fixture_id="p15-decision-source-v1")
    result = authorize_entry(intent, old_source, _selected_account(old_source),
        old_source.session, config, current_at, previous_risk_state=previous, bundle=model)

    assert not result.approved
    assert result.risk_observation.bundle_assessment.reasons == (
        "current_source_profile_uncovered",)
    assert result.candidate_assessment.reasons == ()
    assert result.checks[-1].name == "identity_binding"
    assert result.reason == "identity_binding:bundle:current_source_profile_uncovered"
    assert "model_corrupt" in {entry.reason for entry in result.advanced_risk_state.halt_state.entries}


@pytest.mark.parametrize(("record", "latch"), (
    ("disconnected-account_snapshot", "broker_disconnected"),
    ("pending-order-account_snapshot", "possible_exposure"),
))
def test_current_broker_or_pending_exposure_cannot_skip_material_binding(record, latch):
    """Actual changed account facts deny early while P16 retains independent risk."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    current = _entry_source(current_at, account_record=record)
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous, bundle=model)

    assert result.checks[-1].name == "input_integrity"
    assert "input_integrity:account:material_mismatch" in result.checks[-1].reasons
    if latch == "possible_exposure":
        assert result.advanced_risk_state.possible_exposure
    else:
        assert latch in {entry.reason for entry in result.advanced_risk_state.halt_state.entries}


def test_three_real_prior_fills_do_not_override_earlier_ledger_conflict():
    """An expired proposal cannot use another account's fills to force check nine."""
    from options_lab.risk import advance_risk_state, authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    _, intent = _entry_intent(model)
    state = start_risk_state(risk_context("order1-zero"), config=config, now=NOW)
    for number in (1, 2, 3):
        if number != 1:
            at = NOW + timedelta(seconds=number * 2 - 2)
            state = advance_risk_state(state, risk_context(f"order{number}-zero", at=at),
                                       config=config, now=at)
        at = NOW + timedelta(seconds=number * 2 - 1)
        state = advance_risk_state(state, risk_context(f"order{number}-one", at=at),
                                   config=config, now=at)
    assert state.session_entry_count == config.max_entries_per_session == 3
    current_at = NOW + timedelta(seconds=6)
    assert current_at >= intent.expires_at
    current = _entry_source(current_at, account_record="late-flat-account_snapshot",
                            quote_variant="fresh")
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=state, bundle=model)
    assert result.advanced_risk_state.session_entry_count == 3
    assert result.checks[-1].name == "local_halt"
    assert result.reason == "ledger_corrupt"


def test_fourth_entry_count_precedes_actual_outside_window_and_expiry():
    """Three same-account completed source reports stop a fourth opening at check nine."""
    from options_lab.risk import advance_risk_state, authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    _, intent = _entry_intent(model)
    first_at = NOW + timedelta(microseconds=100000)
    first = _entry_source(first_at, account_record="entry-order1-zero-account_snapshot")
    state = start_risk_state(first, config=config, now=first_at)
    for number in (1, 2, 3):
        if number != 1:
            at = NOW + timedelta(seconds=(number - 1) * 2)
            current = _entry_source(at,
                account_record=f"entry-order{number}-zero-account_snapshot")
            state = advance_risk_state(state, current, config=config, now=at, bundle=model)
        at = NOW + timedelta(seconds=(number - 1) * 2 + 1)
        current = _entry_source(at,
            account_record=f"entry-order{number}-one-account_snapshot")
        state = advance_risk_state(state, current, config=config, now=at, bundle=model)
        assert state.session_entry_count == number
    current_at = LATE + timedelta(microseconds=1)
    current = _entry_source(current_at, account_record="outside-flat-account_snapshot",
                            quote_variant="outside", include_bars=False)
    assert not any(component.member.kind == "underlying_bar"
                   for component in current.selected_components)
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=state, bundle=model)

    assert current_at > intent.expires_at
    assert result.advanced_risk_state.session_entry_count == 3
    assert result.checks[-1].name == "entry_count"
    assert result.reason == "entry_count:session:limit_reached"
    assert all(check.passed for check in result.checks[:-1])


@pytest.mark.parametrize(("prior_record", "reason", "check_name"), (
    ("prior-loss-account_snapshot", "daily_loss", "daily_loss"),
    ("prior-peak-account_snapshot", "drawdown", "drawdown"),
))
def test_prior_adverse_latch_survives_flat_current_opening(prior_record, reason, check_name):
    """P16 prior loss or peak cannot be cleared by a fresh flat current quote."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    prior_at = NOW - timedelta(seconds=1)
    prior = _entry_source(prior_at, account_record=prior_record)
    previous = start_risk_state(prior, config=config, now=prior_at)
    current_at = NOW + timedelta(seconds=1)
    current = _entry_source(current_at)
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous, bundle=model)
    assert not result.approved and result.checks[-1].name == check_name
    assert reason in {entry.reason for entry in result.advanced_risk_state.halt_state.entries}


def test_damaged_original_cost_and_absent_current_bundle_fail_in_order():
    """Original proof corruption and missing current model have distinct owners."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    current = _entry_source(current_at)
    damaged = copy(intent)
    object.__setattr__(damaged, "max_cost", Decimal("1"))
    bad = authorize_entry(damaged, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous, bundle=model)
    assert bad.checks[-1].name == "input_integrity"
    assert bad.intent is None and bad.proposed_intent is damaged
    absent = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous)
    assert absent.checks[-1].name == "identity_binding"
    assert absent.advanced_risk_state is not None


@pytest.mark.parametrize(("offset", "check_name"), ((4, "data_model_readiness"),
                                                      (6, "time_ledger_freshness")))
def test_stale_quote_or_account_fails_at_its_earlier_owner(offset, check_name):
    """A stale market or ledger clock cannot be bypassed by a later expiry."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    model = bundle("call", fixture_id="p17b-entry-bundle-v1",
                   source_id="p17b-entry-source-v1")
    original, intent = _entry_intent(model)
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=offset)
    current = _entry_source(current_at)
    result = authorize_entry(intent, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous, bundle=model)
    assert result.checks[-1].name == check_name


@pytest.mark.parametrize("cash_model", (False, True))
def test_none_intent_still_advances_independent_loss_and_drawdown(cash_model):
    """A rejected opening cannot skip current P16 adverse latches."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    original = risk_context("base")
    previous = start_risk_state(original, config=config, now=NOW)
    current_at = NOW + timedelta(seconds=1)
    current = risk_context("simultaneous", at=current_at)
    result = authorize_entry(None, current, _selected_account(current), current.session,
        config, current_at, previous_risk_state=previous,
        bundle=bundle("cash") if cash_model else None)

    assert not result.approved and result.intent is None
    assert result.reason == "input_integrity:intent:intent_missing"
    assert [check.name for check in result.checks] == ["input_integrity"]
    assert result.advanced_risk_state is not None
    assert {entry.reason for entry in result.advanced_risk_state.halt_state.entries} >= {
        "daily_loss", "drawdown"}
    assert result.risk_observation.account.event_id == "event-simultaneous"


def test_replay_failure_keeps_fresh_adverse_observation_without_new_state():
    """A damaged prior count cannot become approval or erase fresh loss evidence."""
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    original = risk_context("base")
    previous = start_risk_state(original, config=config, now=NOW)
    damaged = copy(previous)
    object.__setattr__(damaged, "session_entry_count", 2)
    current_at = NOW + timedelta(seconds=1)
    current = risk_context("simultaneous", at=current_at)
    result = authorize_entry(None, current, _selected_account(current), current.session,
                             config, current_at, previous_risk_state=damaged)

    assert not result.approved and result.advanced_risk_state is None
    assert result.previous_risk_state is damaged
    assert result.risk_advance_reason == "risk_state_replay_mismatch"
    assert result.risk_observation.account.event_id == "event-simultaneous"
    assert result.risk_observation.account_assessment.conservative_daily_pnl == -5000


def test_wrong_public_types_and_naive_now_raise_before_replay():
    """A copied owner or naive clock cannot enter the risk boundary."""
    from datetime import datetime
    from options_lab.risk import authorize_entry

    config = StrategyConfig()
    current = risk_context("base")
    previous = start_risk_state(current, config=config, now=NOW)
    account = _selected_account(current)
    with pytest.raises(TypeError):
        authorize_entry(False, current, account, current.session, config, NOW,
                        previous_risk_state=previous)
    with pytest.raises(ValueError):
        authorize_entry(None, current, account, current.session, config,
                        datetime(2026, 9, 4, 14, 5), previous_risk_state=previous)
