"""Private ordered opening checks over existing source and risk owners."""

from datetime import datetime
from decimal import DecimalException

from .account import AccountSnapshot
from .bar_inputs import _UnsupportedIdentity
from .bundle_inputs import _make
from .bundles import VerifiedBundle
from .calibration_inputs import _matches_retained
from .candidates import assess_candidate
from .config import StrategyConfig, config_hash, policy_hash
from .context import DecisionContext
from .context_recheck import _standard_time
from .entry_intent import TradeIntent, _material_account, derive_trade_intent
from .risk import (RiskAdvanceError, RiskCheck, RiskDecision, RiskState,
                   _drawdown, _touched, advance_risk_state)
from .sessions import ExchangeSession, assess_submission_time


_NAMES = (
    "input_integrity", "time_ledger_freshness", "identity_binding", "local_halt",
    "broker_reconciliation", "data_model_readiness", "exposure", "quantity",
    "entry_count", "session_cadence", "submission_window", "virtual_equity",
    "premium_cap", "available_cash", "daily_loss", "drawdown",
)
_DATA_CATEGORIES = frozenset(("source_admission", "contract_reference", "option_quote",
    "underlying_quote", "greek_readiness", "coherence", "dte", "delta_sign",
    "delta_band", "tick_cap"))
_DAMAGED = (AttributeError, TypeError, ValueError, OverflowError, DecimalException,
            _UnsupportedIdentity, RecursionError, MemoryError)


def _category_reasons(candidate, names: frozenset[str]) -> tuple[str, ...]:
    """Select native P13 reasons for one ordered guard without trusting eligible.

    :param    candidate: Reached current CandidateAssessment or absence.
    :param    names:     Closed set of P13 reason categories.
    :returns:           Native qualified reasons in owner order.
    """
    return () if candidate is None else tuple(
        reason for reason in candidate.reasons if reason.split(":", 1)[0] in names)


def _result(proposed, now, previous, advanced, observation, checks, *,
            advance_reason=None, candidate=None, timing=None, budget=None):
    """Freeze one permission result from reached owners and first failure.

    :param    proposed:       Original proposed intent or absence.
    :param    now:            Explicit current instant.
    :param    previous:       Prior retained RiskState.
    :param    advanced:       Actual advanced RiskState or absence.
    :param    observation:    Fresh current risk observation or absence.
    :param    checks:         Ordered reached RiskCheck values.
    :param    advance_reason: P16 replay failure reason or absence.
    :param    candidate:      Reached current P13 assessment or absence.
    :param    timing:         Reached P04 submission assessment or absence.
    :param    budget:         Reached current premium budget or absence.
    :returns:                Canonical frozen RiskDecision.
    """
    approved = len(checks) == len(_NAMES) and all(check.passed for check in checks)
    first = next((check for check in checks if not check.passed), None)
    account = None if observation is None else observation.account
    revision = None if account is None else account.ledger_revision
    return _make(RiskDecision, approved=approved, intent=proposed if approved else None,
        proposed_intent=proposed, checks=tuple(checks),
        reason=None if first is None else first.reasons[0], evaluated_at=now,
        portfolio_revision=revision, previous_risk_state=previous,
        advanced_risk_state=advanced, risk_observation=observation,
        risk_advance_reason=advance_reason, candidate_assessment=candidate,
        timing_assessment=timing, premium_budget=budget)


def _authorize_entry(intent: TradeIntent | None, context: DecisionContext,
                     account: AccountSnapshot | None, session: ExchangeSession | None,
                     config: StrategyConfig, now: datetime, *,
                     previous_risk_state: RiskState,
                     bundle: VerifiedBundle | None = None) -> RiskDecision:
    """Replay P16 first, then stop at the first of sixteen opening failures.

    :param    intent:              Original source-proved proposal or absence.
    :param    context:             Current P08 context.
    :param    account:             Supplied current source-selected account.
    :param    session:             Supplied current source-selected session.
    :param    config:              Current trusted configuration.
    :param    now:                 Actual standard aware current instant.
    :param    previous_risk_state: Previous full-genesis risk state.
    :param    bundle:              Current actual B2 bundle or absence.
    :returns:                     Canonical RiskDecision with reached prefix.
    :raises   TypeError:         If a public owner has the wrong exact type.
    :raises   ValueError:        If now is not a standard aware instant.
    """
    if (intent is not None and type(intent) is not TradeIntent
            or type(context) is not DecisionContext
            or account is not None and type(account) is not AccountSnapshot
            or session is not None and type(session) is not ExchangeSession
            or type(config) is not StrategyConfig or type(now) is not datetime
            or type(previous_risk_state) is not RiskState
            or bundle is not None and type(bundle) is not VerifiedBundle):
        raise TypeError("entry arguments require exact trusted owner types")
    if not _standard_time(now):
        raise ValueError("now must have a standard aware timezone")
    checks = []

    def reached(name: str, reasons: tuple[str, ...]) -> bool:
        """Append one derived check and report whether evaluation may continue.

        :param    name:     Fixed ordered check name.
        :param    reasons: Native failure reasons, empty on pass.
        :returns:           True exactly when this check passes.
        """
        assert name == _NAMES[len(checks)]
        checks.append(_make(RiskCheck, name=name, passed=not reasons,
                            reasons=tuple(reasons)))
        return not reasons

    try:
        advanced = advance_risk_state(previous_risk_state, context,
                                      config=config, now=now, bundle=bundle)
    except RiskAdvanceError as error:
        name = ("time_ledger_freshness" if error.reason == "observation_regression"
                else "input_integrity")
        if name != "input_integrity":
            reached("input_integrity", ())
        reached(name, (error.reason,))
        return _result(intent, now, previous_risk_state, None, error.observation,
                       checks, advance_reason=error.reason)
    observation = advanced.last_observation
    fresh = observation.context
    current = candidate = timing = budget = None
    integrity = []
    if intent is None:
        integrity.append("input_integrity:intent:intent_missing")
    else:
        try:
            original = derive_trade_intent(intent.candidates, intent.decision, intent.config)
            if original is None or not _matches_retained(intent, original, set()):
                integrity.append("input_integrity:intent:original_proof_mismatch")
            if fresh is not None and observation.account is not None:
                if (account is None or not _matches_retained(account, observation.account, set())
                        or session is None or not _matches_retained(session, observation.session, set())):
                    integrity.append("input_integrity:current:selected_owner_mismatch")
                if _material_account(observation.account) != intent.material_account:
                    integrity.append("input_integrity:account:material_mismatch")
            if fresh is not None and observation.account is not None:
                current = assess_candidate(intent.contract, fresh, observation.account,
                    config, now=now, original_ask_cap=intent.original_ask_cap)
                integrity.extend(_category_reasons(current, frozenset(("input_integrity",))))
        except _DAMAGED:
            integrity.append("input_integrity:intent:damaged_owner")
    assessment = observation.account_assessment
    if assessment is not None:
        integrity.extend("input_integrity:account:" + code
                         for code in assessment.integrity_reasons)
    integrity.extend("input_integrity:context:" + code for code in
        observation.context_reasons if code in ("retained_context_mismatch",
            "retained_request_mismatch", "retained_manifest_mismatch",
            "fresh_manifest_rejected", "previous_state_replay_failed"))
    if not reached("input_integrity", tuple(dict.fromkeys(integrity))):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    time_reasons = ["time_ledger_freshness:context:" + code for code in
        observation.context_reasons if code == "context_time_mismatch"]
    if (fresh is None and not time_reasons
            and observation.context_reasons != ("retained_config_mismatch",)):
        time_reasons.append("time_ledger_freshness:context:current_source_unavailable")
    if now < intent.created_at:
        time_reasons.append("time_ledger_freshness:intent:decision_in_future")
    if assessment is not None:
        time_reasons.extend("time_ledger_freshness:account:" + code
                            for code in assessment.time_reasons)
    time_reasons.extend("time_ledger_freshness:source:" + code for code in
                        observation.mark_source_reasons)
    if not reached("time_ledger_freshness", tuple(time_reasons)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    bundle_assessment = observation.bundle_assessment
    identity = []
    if config_hash(config) != intent.config_hash or policy_hash(config) != intent.policy_hash:
        identity.append("identity_binding:config:original_mismatch")
    identity.extend("identity_binding:context:" + code for code in
        observation.context_reasons if code == "retained_config_mismatch")
    if (bundle is None or bundle.bundle_hash != intent.bundle_hash
            or bundle.model.model_kind != "fixture_fixed_by_right"):
        identity.append("identity_binding:bundle:original_model_mismatch")
    if bundle_assessment is not None:
        if (bundle_assessment.bundle is not None
                and bundle_assessment.bundle.bundle_hash != intent.bundle_hash):
            identity.append("identity_binding:bundle:current_model_mismatch")
        identity.extend("identity_binding:bundle:" + reason for reason in
            bundle_assessment.reasons if reason in ("config_mismatch",
                "current_source_profile_uncovered", "current_definition_mismatch"))
    if fresh is not None and (fresh.config_hash != intent.config_hash
            or fresh.policy_hash != intent.policy_hash):
        identity.append("identity_binding:context:config_policy_mismatch")
    if current is not None:
        identity.extend(_category_reasons(current, frozenset(("config_policy_input_binding",))))
    if not reached("identity_binding", tuple(identity)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    active = {entry.reason for entry in advanced.halt_state.entries}
    local = tuple(reason for reason in ("kill_switch", "manual_halt", "ledger_corrupt",
                                        "model_corrupt") if reason in active)
    if not reached("local_halt", local):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    broker = []
    if observation.account is None or observation.account.connection != "connected":
        broker.append("broker_reconciliation:account:disconnected")
    if assessment is None:
        broker.append("broker_reconciliation:account:unavailable")
    else:
        broker.extend("broker_reconciliation:account:" + code
                      for code in assessment.reconciliation_reasons
                      if not code.startswith(("holdings_", "orders_")))
    broker.extend(reason for reason in ("broker_disconnected", "broker_unreconciled")
                  if reason in active)
    if not reached("broker_reconciliation", tuple(broker)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    data = []
    if bundle_assessment is None or not bundle_assessment.available:
        data.extend(("data_model_readiness:bundle:unavailable",)
                    if bundle_assessment is None else bundle_assessment.reasons)
    data.extend(reason for reason in ("market_data_unready", "model_unready")
                if reason in active)
    data.extend(_category_reasons(current, _DATA_CATEGORIES))
    if current is None:
        data.append("data_model_readiness:candidate:unavailable")
    if assessment is not None:
        data.extend("data_model_readiness:mark:" + code for mark in
                    assessment.holding_marks for code in mark.mark_reasons)
    if not reached("data_model_readiness", tuple(data)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    exposure = []
    if advanced.possible_exposure or assessment is None or not assessment.observed_flat:
        exposure.append("exposure:account:not_observed_flat")
    if assessment is not None:
        exposure.extend("exposure:account:" + code for code in assessment.exposure_reasons)
    exposure.extend(_category_reasons(current, frozenset(("account_exposure",))))
    if not reached("exposure", tuple(exposure)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    if not reached("quantity", () if intent.quantity == config.max_contracts == 1
                   else ("quantity:intent:exactly_one_required",)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)
    count = (() if advanced.session_entry_count < config.max_entries_per_session
             and "entry_count" not in active else ("entry_count:session:limit_reached",))
    if not reached("entry_count", count):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    cadence = []
    if (observation.session is None or observation.session.calendar != "XNYS"
            or observation.session.session_date != advanced.session_date
            or assessment is None or assessment.session_reasons):
        cadence.append("session_cadence:session:calendar_or_date_mismatch")
    if (observation.session is None or not intent.slot_key
            or not intent.slot_key.startswith(
                "XNYS/" + observation.session.session_date.isoformat() + "/")):
        cadence.append("session_cadence:intent:original_slot_mismatch")
    cadence.extend(reason for reason in _category_reasons(current, frozenset(("current_session",)))
                   if reason.rsplit(":", 1)[-1] in
                   ("calendar_unsupported", "session_wrong_date", "instrument_wrong_date"))
    if not reached("session_cadence", tuple(cadence)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current)

    tradability = (current.session_assessments[0].tradability
                   if len(current.session_assessments) == 1 else None)
    timing = assess_submission_time(observation.session, tradability,
        config=config, decision_at=intent.decision_at,
        original_expires_at=intent.expires_at, now=now)
    if not reached("submission_window", timing.reasons):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current, timing=timing)

    equity = assessment.conservative_virtual_equity
    equity_reasons = _category_reasons(current,
                                      frozenset(("declared_conservative_equity",)))
    if not reached("virtual_equity", equity_reasons if equity_reasons else
                   () if equity is not None and equity > 0 else
                   ("virtual_equity:account:unknown_or_nonpositive",)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current, timing=timing)

    joined = current.quote_budget_assessment
    budget = None if joined is None else joined.budget
    premium = []
    if budget is None:
        premium.append("premium_cap:budget:unavailable")
    else:
        if budget.required_cash > budget.equity_limit:
            premium.append("premium_cap:budget:premium_cap_exceeded")
        if budget.required_cash > intent.max_cost:
            premium.append("premium_cap:intent:reservation_ceiling_exceeded")
    if not reached("premium_cap", tuple(premium)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current, timing=timing, budget=budget)

    cash = assessment.effective_available_cash
    if not reached("available_cash", () if cash is not None and budget.required_cash <= cash
                   else ("available_cash:account:insufficient_or_unknown",)):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current, timing=timing, budget=budget)

    if advanced.session_start_equity is None or assessment.conservative_daily_pnl is None:
        daily = ("daily_loss:session:base_or_pnl_unknown",)
    else:
        daily = (() if "daily_loss" not in active and not _touched(
            assessment.conservative_daily_pnl, advanced.session_start_equity,
            config.daily_loss_fraction, negative=True) else ("daily_loss:session:limit_touched",))
    if not reached("daily_loss", daily):
        return _result(intent, now, previous_risk_state, advanced, observation,
                       checks, candidate=current, timing=timing, budget=budget)
    if advanced.high_water_mark is None or assessment.conservative_virtual_equity is None:
        drawdown = ("drawdown:account:peak_or_equity_unknown",)
    else:
        drawdown = (() if "drawdown" not in active and not _drawdown(
            assessment, observation.account, advanced.high_water_mark, config)
            else ("drawdown:account:limit_touched",))
    reached("drawdown", drawdown)
    return _result(intent, now, previous_risk_state, advanced, observation,
                   checks, candidate=current, timing=timing, budget=budget)
