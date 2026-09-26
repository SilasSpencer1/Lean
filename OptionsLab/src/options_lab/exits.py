"""Source-backed independent exit observation and pure exit policy."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from ._validation import _trusted_datetime
from .account import HoldingFact, OpenOrderFact
from .bundle_inputs import _make
from .bundles import VerifiedBundle
from .calibration_inputs import _matches_retained
from .config import StrategyConfig
from .context import DecisionContext
from .risk import RiskAdvanceError, RiskState, _drawdown, _touched, advance_risk_state
from .risk_inputs import RiskObservation, observe_risk_inputs
from .sessions import InstrumentTradability, SessionAssessment, assess_session


ExitAction = Literal["monitor", "reconcile", "prepare_exit", "flat", "incident"]
ExitReason = Literal[
    "unknown_session", "unknown_source", "unknown_exposure", "deadline", "escalation",
    "broker_mismatch", "liquidation_window", "safety_data", "safety_model",
    "safety_loss", "safety_halt", "observed_flat", "pending_exit", "pending_entry",
    "risk_history_unverified", "unknown_fill_time", "prior_session_holding",
    "holding_period", "before_horizon",
]
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True, init=False)
class ExitEvidence:
    """This class represents actual selected source and reached risk evidence."""

    risk_observation: RiskObservation
    position: HoldingFact | None
    orders: tuple[OpenOrderFact, ...]
    session: SessionAssessment
    prior_risk_state: RiskState | None
    advanced_risk_state: RiskState | None
    risk_advance_reason: str | None
    source_reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Require the source-backed exit observation factory.

        :returns:             None.
        :raises   TypeError:  Always; use observe_exit_inputs.
        """
        raise TypeError("exit evidence comes from observe_exit_inputs")


@dataclass(frozen=True, init=False)
class ExitInstruction:
    """This class represents a policy request without execution authority."""

    action: ExitAction
    reason: ExitReason
    evaluated_at: datetime
    position_identity: tuple[str, str, str] | None
    effective_deadline: datetime | None
    reconciliation_required: bool
    deadline_incident: bool
    evidence: ExitEvidence

    def __init__(self) -> None:
        """Require the canonical source-backed policy evaluator.

        :returns:             None.
        :raises   TypeError:  Always; use evaluate_exit.
        """
        raise TypeError("exit instructions come from evaluate_exit")


def observe_exit_inputs(context: DecisionContext, *, config: StrategyConfig, now: datetime,
                        halts: RiskState | None = None,
                        bundle: VerifiedBundle | None = None) -> ExitEvidence:
    """Recheck the actual account, held instrument and current risk history.

    :param    context: Actual owner-produced current context.
    :param    config:  Current trusted strategy configuration.
    :param    now:     Exact current aware instant.
    :param    halts:   Prior retained risk state, or explicit absence.
    :param    bundle:  Optional actual B2 bundle.
    :returns:          Closed reached exit evidence, including adverse facts.
    :raises   TypeError: If trusted arguments have incorrect exact types.
    :raises   ValueError: If the current instant is invalid.
    """
    if (type(context) is not DecisionContext or type(config) is not StrategyConfig
            or type(now) is not datetime or (halts is not None and type(halts) is not RiskState)
            or (bundle is not None and type(bundle) is not VerifiedBundle)):
        raise TypeError("context, config, now, halts and bundle require exact trusted types")
    now = _trusted_datetime("now", now)
    advanced = None
    failure = None
    if halts is None:
        observed = observe_risk_inputs(context, config=config, now=now, bundle=bundle)
    else:
        try:
            advanced = advance_risk_state(halts, context, config=config, now=now, bundle=bundle)
            observed = advanced.last_observation
        except RiskAdvanceError as error:
            failure = error.reason
            observed = error.observation or observe_risk_inputs(
                context, config=config, now=now, bundle=bundle)
    account = observed.account
    position = account.holdings[0] if account is not None and len(account.holdings) == 1 else None
    orders = () if account is None else account.open_orders
    fresh = observed.context
    live_orders = tuple(order for order in orders if _active(order))
    target = (position if position is not None else live_orders[0]
              if len(live_orders) == 1 else None)
    candidates = () if fresh is None or target is None else tuple(
        component.value for component in fresh.selected_components
        if type(component.value) is InstrumentTradability
        and component.value.contract == target.contract
        and component.value.instrument_ref == target.instrument_ref)
    tradability = candidates[0] if len(candidates) == 1 else None
    session = assess_session(observed.session, tradability, config=config, now=now)
    reasons = list(observed.context_reasons)
    reasons.extend(observed.mark_source_reasons)
    if target is not None and len(candidates) != 1:
        reasons.append("instrument_source_missing" if not candidates else "instrument_source_ambiguous")
    if observed.account_assessment is not None:
        reasons.extend(observed.account_assessment.integrity_reasons)
        reasons.extend(observed.account_assessment.time_reasons)
        reasons.extend(observed.account_assessment.reconciliation_reasons)
    return _make(ExitEvidence, risk_observation=observed, position=position,
        orders=orders, session=session, prior_risk_state=halts,
        advanced_risk_state=advanced, risk_advance_reason=failure,
        source_reasons=tuple(dict.fromkeys(reasons)))


def _active(order: OpenOrderFact) -> bool:
    """Keep unresolved or executing orders as possible exposure.

    :param    order: Actual retained order fact.
    :returns:        Whether it can still affect execution.
    """
    return order.status != "terminal" or order.execution_uncertain


def evaluate_exit(position: HoldingFact | None, orders: tuple[OpenOrderFact, ...],
                  session: SessionAssessment, halts: RiskState | None,
                  health: ExitEvidence, now: datetime,
                  config: StrategyConfig) -> ExitInstruction:
    """Choose one exit instruction from independently rechecked facts.

    :param    position: Sole exact selected holding, if one exists.
    :param    orders:   Exact selected account order tuple.
    :param    session:  Exact selected session and held instrument assessment.
    :param    halts:    Prior full-history risk state or explicit absence.
    :param    health:   Factory-produced current exit evidence.
    :param    now:      Exact current aware instant.
    :param    config:   Exact current strategy configuration.
    :returns:           Closed policy instruction without quantity or price.
    :raises   TypeError:  If any trusted argument has an incorrect type.
    :raises   ValueError: If arguments do not bind to current source evidence.
    """
    if (type(health) is not ExitEvidence or type(config) is not StrategyConfig
            or type(now) is not datetime or type(orders) is not tuple
            or any(type(order) is not OpenOrderFact for order in orders)
            or type(session) is not SessionAssessment
            or (position is not None and type(position) is not HoldingFact)
            or (halts is not None and type(halts) is not RiskState)):
        raise TypeError("exit inputs require exact trusted types")
    now = _trusted_datetime("now", now)
    observed = health.risk_observation
    if (type(observed) is not RiskObservation
            or type(observed.original_context) is not DecisionContext
            or (observed.original_bundle is not None
                and type(observed.original_bundle) is not VerifiedBundle)):
        raise TypeError("exit evidence requires exact trusted owner types")
    if (position is not health.position or orders is not health.orders
            or session is not health.session or halts is not health.prior_risk_state):
        raise ValueError("exit arguments do not bind to current evidence")
    fresh = observe_exit_inputs(observed.original_context, config=config, now=now,
        halts=halts, bundle=observed.original_bundle)
    if (not _matches_retained(health, fresh, set())
            or not _matches_retained(config, fresh.risk_observation.config, set())):
        raise ValueError("exit evidence does not recheck against actual sources")
    observed = fresh.risk_observation
    position, orders, session = fresh.position, fresh.orders, fresh.session

    account = observed.account
    assessment = observed.account_assessment
    advanced = fresh.advanced_risk_state
    active = set() if advanced is None else {entry.reason for entry in advanced.halt_state.entries}
    pending_exit = any(_active(order) and (order.side == "sell" or order.role == "exit")
                       for order in orders)
    pending_entry = any(_active(order) and (order.side == "buy" or order.role == "entry")
                        for order in orders)
    known_holding = position is not None and position.asset_kind == "option" \
        and position.contract is not None and position.quantity_unit == "contracts" \
        and position.quantity is not None and position.quantity > 0 \
        and position.quantity == position.quantity.to_integral_value()
    observed_flat = assessment is not None and assessment.observed_flat
    possible = (not observed_flat or (advanced is not None and advanced.possible_exposure)
                or pending_entry or pending_exit)
    deadline = session.effective_liquidation_deadline
    incident = deadline is not None and now >= deadline and possible
    reconcile = (halts is None or fresh.risk_advance_reason is not None
                 or bool(observed.mark_source_reasons))
    identity = None if position is None else (
        position.source, position.provider_record_id, position.position_id)

    def result(action: ExitAction, reason: ExitReason, *, needs_reconcile=False) -> ExitInstruction:
        """Apply the account-level duplicate-sell barrier to one priority result.

        :param    action:           Chosen closed policy action.
        :param    reason:           First reached priority reason.
        :param    needs_reconcile:  Additional source or control uncertainty.
        :returns:                   Factory-produced instruction.
        """
        if action == "prepare_exit" and pending_exit:
            action = "reconcile"
            needs_reconcile = True
        return _make(ExitInstruction, action=action, reason=reason, evaluated_at=now,
            position_identity=identity, effective_deadline=deadline,
            reconciliation_required=reconcile or needs_reconcile,
            deadline_incident=incident, evidence=health)

    if (session.session_reasons and set(session.session_reasons) != {"session_not_regular"}
            or (known_holding or account is not None and not account.holdings
                and any(_active(order) for order in orders))
                and (session.tradability is None
                or session.instrument_hours_reasons or session.operability_reasons)):
        return result("reconcile", "unknown_session", needs_reconcile=True)
    if (account is None or not observed.context_recheck.valid or assessment is None
            or assessment.time_reasons or assessment.session_reasons
            or account.holdings_completeness != "complete"
            or account.orders_completeness != "complete" or len(account.holdings) > 1
            or assessment.integrity_reasons and set(assessment.integrity_reasons)
                != {"holding_mark_contract_mismatch"}
            or position is not None and not known_holding):
        return result("reconcile", "unknown_exposure" if account is not None
                      else "unknown_source", needs_reconcile=True)
    if incident:
        return result("incident", "deadline", needs_reconcile=True)
    if session.escalation_due and possible:
        return result("reconcile", "escalation", needs_reconcile=True)
    if (assessment.reconciliation_reasons or account.connection != "connected"
            or active.intersection(("broker_disconnected", "broker_unreconciled", "ledger_corrupt"))):
        return result("reconcile", "broker_mismatch", needs_reconcile=True)
    if session.liquidation_due and possible:
        return result("prepare_exit" if known_holding else "reconcile",
                      "liquidation_window", needs_reconcile=not known_holding)
    mark_bad = (position is not None and (not assessment.holding_marks
        or bool(assessment.holding_marks[0].mark_reasons)
        or bool(observed.mark_source_reasons)))
    model_bad = observed.bundle_assessment is not None and bool(observed.bundle_reasons)
    base = (advanced.session_start_equity if advanced is not None else
            None if account is None else account.session_start_equity)
    daily = None if assessment is None else assessment.conservative_daily_pnl
    loss_bad = "daily_loss" in active or _touched(daily, base,
        config.daily_loss_fraction, negative=True)
    peak = (advanced.high_water_mark if advanced is not None else
            None if account is None else account.high_water_mark)
    drawdown_bad = "drawdown" in active or _drawdown(assessment, account, peak, config)
    if known_holding and (mark_bad or model_bad or loss_bad or drawdown_bad or active):
        reason: ExitReason = ("safety_data" if mark_bad else "safety_model" if model_bad
            else "safety_loss" if loss_bad or drawdown_bad else "safety_halt")
        return result("prepare_exit", reason, needs_reconcile=mark_bad)
    if observed_flat:
        return result("flat", "observed_flat")
    if pending_exit:
        return result("monitor", "pending_exit")
    if pending_entry or orders:
        return result("reconcile", "pending_entry", needs_reconcile=True)
    if known_holding and position.entry_filled_at is None:
        return result("prepare_exit", "unknown_fill_time", needs_reconcile=True)
    if known_holding and assessment.strategy_date is not None \
            and position.entry_filled_at.astimezone(_NY).date() \
                < assessment.strategy_date:
        return result("prepare_exit", "prior_session_holding", needs_reconcile=True)
    if known_holding:
        try:
            due = position.entry_filled_at + config.execution.holding_period
        except OverflowError:
            return result("reconcile", "unknown_fill_time", needs_reconcile=True)
        return result("prepare_exit" if now >= due else "monitor",
                      "holding_period" if now >= due else "before_horizon")
    if fresh.risk_advance_reason is not None:
        return result("reconcile", "risk_history_unverified", needs_reconcile=True)
    return result("reconcile", "unknown_exposure", needs_reconcile=True)
