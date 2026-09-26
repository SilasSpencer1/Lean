"""Pure source-backed risk history and per-reason local halts."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, DecimalException, localcontext

from .account import AccountSnapshot, _money, _money_context, _sum_money
from .account_inputs import account_snapshot
from .admission import VerifiedFixtureManifest
from .bar_inputs import _UnsupportedIdentity
from .bundle_inputs import _make
from .bundles import VerifiedBundle
from .config import StrategyConfig, _snapshot_hash, config_hash
from .context import DecisionContext
from .risk_inputs import RiskControlEvent, RiskObservation, normalize_risk_control, observe_risk_inputs


_RULES = (
    ("daily_loss", "session", "new_reconciled_session"),
    ("entry_count", "session", "new_reconciled_session"),
    ("drawdown", "persistent", "explicit_audited_reset"),
    ("manual_halt", "persistent", "explicit_audited_reset"),
    ("kill_switch", "persistent", "explicit_audited_reset"),
    ("ledger_corrupt", "persistent", "explicit_audited_reset"),
    ("model_corrupt", "persistent", "explicit_audited_reset"),
    ("broker_disconnected", "persistent", "verified_recovery"),
    ("broker_unreconciled", "persistent", "verified_recovery"),
    ("market_data_unready", "persistent", "verified_recovery"),
    ("model_unready", "persistent", "verified_recovery"),
)
_ORDER = {row[0]: index for index, row in enumerate(_RULES)}
_CORRUPT_BUNDLE = frozenset((
    "config_mismatch", "current_definition_mismatch", "current_source_profile_uncovered",
    "calibration_bundle_mismatch", "simulated_availability_mismatch", "cash_fit_claim",
    "schedule_calendar_mismatch", "activation_block_mismatch", "schedule_fit_cutoff_mismatch",
    "schedule_availability_mismatch", "validation_report_missing", "calibration_metadata_missing",
    "simulated_actual_activation_conflict", "actual_activation_order", "actual_fit_order",
))
# ponytail: finite full-genesis replay; extend only with measured cost and proof-preserving storage.
_MAX_TRANSITIONS = 256
_MAX_SOURCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, init=False)
class HaltEntry:
    """This class represents one individually latched reason and its clearing proof."""

    reason: str
    scope: str
    reset_rule: str
    latched_at: datetime
    latch_evidence: tuple[str, ...]
    cleared_at: datetime | None
    reset_evidence: tuple[str, ...] | None

    def __init__(self) -> None:
        """Require the source-backed risk factory.

        :returns:             None.
        :raises   TypeError:  Always; use start_risk_state or advance_risk_state.
        """
        raise TypeError("halt entries come from risk state factories")


@dataclass(frozen=True, init=False)
class HaltState:
    """This class represents active and individually resolved local halts."""

    entries: tuple[HaltEntry, ...]
    resolved_entries: tuple[HaltEntry, ...]

    def __init__(self) -> None:
        """Require the source-backed risk factory.

        :returns:             None.
        :raises   TypeError:  Always; use start_risk_state or advance_risk_state.
        """
        raise TypeError("halt states come from risk state factories")


@dataclass(frozen=True, init=False)
class _RiskTransition:
    """This class represents one retained original source/control transition."""

    context: DecisionContext
    config: StrategyConfig
    now: datetime
    bundle: VerifiedBundle | None
    control: RiskControlEvent | None

    def __init__(self) -> None:
        """Require the reducer to retain original inputs.

        :returns:             None.
        :raises   TypeError:  Always; transitions come from risk state factories.
        """
        raise TypeError("risk transitions come from risk state factories")


@dataclass(frozen=True, init=False)
class RiskState:
    """This class represents bounded rederivable risk history and current evidence."""

    account_id: str | None
    source: str | None
    session_date: date | None
    session_start_equity: Decimal | None
    high_water_mark: Decimal | None
    session_entry_count: int
    ledger_revision: str | None
    last_account: AccountSnapshot | None
    last_observation: RiskObservation
    halt_state: HaltState
    possible_exposure: bool
    risk_revision: str
    transcript: tuple[_RiskTransition, ...]

    def __init__(self) -> None:
        """Require full-genesis source-backed reduction.

        :returns:             None.
        :raises   TypeError:  Always; use start_risk_state or advance_risk_state.
        """
        raise TypeError("risk states come from risk state factories")


class RiskAdvanceError(ValueError):
    """This class represents denial while retaining reached current evidence."""

    def __init__(self, reason: str, *, previous: RiskState | None,
                 observation: RiskObservation | None) -> None:
        """Retain the unchanged prior state and reached current observation.

        :param    reason:       Closed local failure code.
        :param    previous:     Caller-supplied unchanged prior state, if any.
        :param    observation:  Freshly reached current evidence, if available.
        :returns:               None.
        """
        super().__init__(reason)
        self.reason = reason
        self.previous = previous
        self.observation = observation


def _material(account: AccountSnapshot | None) -> dict[str, object] | None:
    """Project portfolio facts without transport receipt or opaque risk refs."""
    if account is None:
        return None
    try:
        value = account_snapshot(account)
    except ValueError:
        return {"account_id": account.account_id, "source": account.source,
                "event_id": account.event_id, "representation_unsupported": True}
    for name in ("raw_ref", "received_at", "risk_state_revision", "halt_checkpoint_ref"):
        value.pop(name)
    for fact in (*value["holdings"], *value["open_orders"]):
        fact.pop("raw_ref")
    return value


def _revision(state: RiskState, config: StrategyConfig) -> str:
    """Identify effective semantic facts without receipts or evaluation time."""
    return _snapshot_hash(dict(record_kind="options_lab.risk_state", schema_version=1,
        account=_material(state.last_account), config_hash=config_hash(config),
        session_date=None if state.session_date is None else state.session_date.isoformat(),
        session_start_equity=None if state.session_start_equity is None else str(state.session_start_equity),
        high_water_mark=None if state.high_water_mark is None else str(state.high_water_mark),
        session_entry_count=state.session_entry_count, possible_exposure=state.possible_exposure,
        entries=[(e.reason, e.latched_at.isoformat(), e.latch_evidence) for e in state.halt_state.entries],
        resolved=[(e.reason, e.latched_at.isoformat(), e.latch_evidence, e.cleared_at.isoformat(),
                   e.reset_evidence) for e in state.halt_state.resolved_entries]))


def _amount(value: Decimal | None) -> Decimal | None:
    """Retain a bounded known positive money claim without guessing unknowns."""
    if value is None or value <= 0:
        return None
    try:
        return _money(value)
    except (DecimalException, ValueError, _UnsupportedIdentity):
        return None


def _add(a: Decimal, b: Decimal) -> Decimal | None:
    """Add two bounded dollar amounts independent of ambient Decimal context."""
    try:
        return _sum_money((a, b))
    except (DecimalException, ValueError, _UnsupportedIdentity):
        return None


def _touched(value: Decimal | None, base: Decimal | None, fraction: Decimal,
             *, negative: bool = False) -> bool:
    """Compare one inclusive configured loss threshold with bounded exact math."""
    if value is None or base is None:
        return False
    try:
        with localcontext(_money_context()):
            threshold = _money(_money(base) * _money(fraction))
            if negative:
                threshold = threshold.copy_negate()
            return _money(value) <= threshold
    except (DecimalException, ValueError, _UnsupportedIdentity):
        return False


def _at(observation: RiskObservation) -> datetime:
    """Use the actual reached source timestamp for a source-triggered latch."""
    if observation.account is not None and observation.account.as_of is not None:
        return observation.account.as_of
    if observation.session is not None and observation.session.available_at is not None:
        return observation.session.available_at
    return observation.original_context.decision_at


def _evidence(observation: RiskObservation, code: str) -> tuple[str, ...]:
    """Keep bounded source identity and the owner reason for one latch."""
    account = observation.account
    return (code, *(() if account is None else (account.event_id, account.source,
                                                account.ledger_revision or "ledger_unknown")))


def _latch(active: dict[str, HaltEntry], reason: str, at: datetime,
           evidence: tuple[str, ...]) -> None:
    """Preserve the first latch time and proof of an active reason."""
    if reason not in active:
        _, scope, reset_rule = _RULES[_ORDER[reason]]
        active[reason] = _make(HaltEntry, reason=reason, scope=scope, reset_rule=reset_rule,
                              latched_at=at, latch_evidence=evidence,
                              cleared_at=None, reset_evidence=None)


def _clear(active: dict[str, HaltEntry], resolved: list[HaltEntry], reason: str,
           at: datetime, evidence: tuple[str, ...]) -> None:
    """Clear exactly one reason while retaining its own original latch."""
    old = active.pop(reason, None)
    if old is not None:
        resolved.append(_make(HaltEntry, reason=old.reason, scope=old.scope,
            reset_rule=old.reset_rule, latched_at=old.latched_at,
            latch_evidence=old.latch_evidence, cleared_at=at, reset_evidence=evidence))


def _fresh_flat(observation: RiskObservation) -> bool:
    """Require actual current source and account-owner flat reconciliation."""
    assessment = observation.account_assessment
    return (observation.context_recheck.valid and observation.account is not None
            and assessment is not None and assessment.observed_flat
            and not any(reason.startswith("source_admission:") for reason in observation.account_evidence_reasons))


def _source_ok(observation: RiskObservation) -> bool:
    """Require one actual selected, fresh, coherent account source."""
    account, assessment = observation.account, observation.account_assessment
    return (observation.context_recheck.valid and account is not None and assessment is not None
            and not assessment.integrity_reasons and not assessment.time_reasons
            and not assessment.reconciliation_reasons
            and not any(reason.startswith("source_admission:") for reason in observation.account_evidence_reasons))


def _filled(previous: AccountSnapshot | None, current: AccountSnapshot | None) -> tuple | None:
    """Return one stable completed entry identity from an exact 0-to-1 transition."""
    if previous is None or current is None or (previous.account_id, previous.source) != (current.account_id, current.source):
        return None
    candidates = []
    for old in previous.open_orders:
        if (old.role, old.side, old.cumulative_filled_quantity) != ("entry", "buy", Decimal(0)):
            continue
        for new in current.open_orders:
            if (new.role, new.side, new.status, new.remaining_quantity,
                new.cumulative_filled_quantity) != ("entry", "buy", "terminal", Decimal(0), Decimal(1)):
                continue
            if (old.order_ref, old.client_order_id, old.contract, old.source) == (
                new.order_ref, new.client_order_id, new.contract, new.source) and old.client_order_id and old.contract:
                candidates.append((old, new))
    if (len(candidates) != 1 or len({o.order_ref for o in previous.open_orders}) != len(previous.open_orders)
            or len({o.order_ref for o in current.open_orders}) != len(current.open_orders)):
        return None
    order = candidates[0][1]
    return (current.account_id, current.source, order.order_ref, order.client_order_id,
            order.contract, order.source)


def _order_conflict(previous: AccountSnapshot | None,
                    current: AccountSnapshot | None) -> bool:
    """Retain ambiguous quantities, regressions and same-order identity changes."""
    if previous is None or current is None or (previous.account_id, previous.source) != (current.account_id, current.source):
        return False
    prior = {order.order_ref: order for order in previous.open_orders}
    current_refs = {order.order_ref for order in current.open_orders}
    if any(order.status != "terminal" and order.order_ref not in current_refs
           for order in previous.open_orders):
        return True
    for order in current.open_orders:
        quantity = order.cumulative_filled_quantity
        if quantity is None or quantity not in (Decimal(0), Decimal(1)) or order.execution_uncertain:
            return True
        old = prior.get(order.order_ref)
        if old is None:
            continue
        if (old.client_order_id, old.contract, old.source, old.side, old.role) != (
                order.client_order_id, order.contract, order.source, order.side, order.role):
            return True
        if old.cumulative_filled_quantity is not None and quantity < old.cumulative_filled_quantity:
            return True
    return False


def _cashflow(control: RiskControlEvent, previous: AccountSnapshot | None,
              observation: RiskObservation, prior_observation: RiskObservation | None) -> bool:
    """Prove every explicit flat ledger and equal-delta cashflow precondition."""
    current = observation.account
    if (previous is None or current is None or prior_observation is None
            or not _fresh_flat(prior_observation) or not _fresh_flat(observation)
            or (previous.account_id, previous.source) != (current.account_id, current.source)
            or previous.event_id == current.event_id or previous.ledger_revision == current.ledger_revision
            or not previous.as_of <= control.occurred_at <= current.as_of
            or (control.previous_account_event_id, control.current_account_event_id,
                control.ledger_before, control.ledger_after) != (
                    previous.event_id, current.event_id, previous.ledger_revision, current.ledger_revision)
            or previous.session_realized_pnl is None or current.session_realized_pnl != previous.session_realized_pnl):
        return False
    try:
        for name in ("virtual_cash", "virtual_equity", "virtual_settled_cash"):
            before, after = getattr(previous, name), getattr(current, name)
            if before is None or after is None or _sum_money((after, before.copy_negate())) != _money(control.amount):
                return False
    except (DecimalException, ValueError, _UnsupportedIdentity):
        return False
    return True


def _control(current: RiskControlEvent | None, prior: RiskState | None,
             observation: RiskObservation, used_ids: dict[str, str],
             used_content: set[str]) -> tuple[str | None, RiskControlEvent | None]:
    """Re-normalize original bytes and bind one request to local/source facts."""
    if current is None:
        return None, None
    try:
        checked = normalize_risk_control(current.raw_bytes, event_id=current.event_id,
            raw_ref=current.raw_ref, received_at=current.received_at)
    except (TypeError, ValueError, AttributeError):
        return "control_invalid", None
    if checked.value is None or checked.value != current:
        return "control_invalid", None
    if current.event_id in used_ids and used_ids[current.event_id] != current.content_hash:
        return "control_conflict", None
    used_ids[current.event_id] = current.content_hash
    if current.content_hash in used_content:
        return None, None
    if (prior is None or current.expected_risk_revision != prior.risk_revision
            or (current.account_id, current.source) != (prior.account_id, prior.source)
            or current.occurred_at > current.received_at
            or current.received_at > observation.now):
        return "control_binding", None
    used_content.add(current.content_hash)
    return None, current


def _step(previous: RiskState | None, observation: RiskObservation,
          transition: _RiskTransition, transcript: tuple[_RiskTransition, ...],
          used_ids: dict[str, str], used_content: set[str],
          source_events: dict[tuple[str, str, str], dict[str, object]],
          counted_orders: set[tuple]) -> RiskState:
    """Reduce one freshly observed source step and every independent latch."""
    account, assessment = observation.account, observation.account_assessment
    active = {} if previous is None else {entry.reason: entry for entry in previous.halt_state.entries}
    resolved = [] if previous is None else list(previous.halt_state.resolved_entries)
    base = None if previous is None else previous.session_start_equity
    peak = None if previous is None else previous.high_water_mark
    count = 0 if previous is None else previous.session_entry_count
    session_date = observation.session.session_date if observation.session is not None else None if previous is None else previous.session_date
    at = _at(observation)
    source_conflict = False
    if account is not None:
        key = (account.account_id, account.source, account.event_id)
        material = _material(account)
        source_conflict = key in source_events and source_events[key] != material
        source_events.setdefault(key, material)
    order_conflict = _order_conflict(None if previous is None else previous.last_account, account)
    if previous is None or (previous.account_id is None and account is not None and _source_ok(observation)):
        base = _amount(None if account is None else account.session_start_equity)
        peak = _amount(None if account is None else account.high_water_mark)
    else:
        later = (observation.session is not None and previous.session_date is not None
                 and observation.session.session_date > previous.session_date)
        if (later and _fresh_flat(observation) and _amount(account.session_start_equity) is not None
                and (previous.account_id is None or (account.account_id, account.source)
                     == (previous.account_id, previous.source))):
            session_date = observation.session.session_date
            base = _amount(account.session_start_equity)
            count = 0
            for reason in ("daily_loss", "entry_count"):
                _clear(active, resolved, reason, at, _evidence(observation, "new_reconciled_session"))
        elif later:
            session_date = previous.session_date
        elif _source_ok(previous.last_observation) and _source_ok(observation):
            entry = _filled(previous.last_account, account)
            if entry is not None and entry not in counted_orders and not (source_conflict or order_conflict):
                counted_orders.add(entry)
                count += 1
    failure, control = _control(transition.control, previous, observation, used_ids, used_content)
    if control is not None:
        if control.kind == "halt":
            _latch(active, control.reason, control.occurred_at, (control.event_id, control.content_hash))
        elif _source_ok(observation) and (account.account_id, account.source) == (control.account_id, control.source):
            if control.kind == "cashflow" and _cashflow(control, None if previous is None else previous.last_account,
                                                         observation, None if previous is None else previous.last_observation):
                new_base = None if base is None else _add(base, control.amount)
                new_peak = None if peak is None else _add(peak, control.amount)
                if new_base is not None and new_base > 0 and new_peak is not None and new_peak > 0:
                    base, peak = new_base, new_peak
                else:
                    failure = "cashflow_arithmetic"
            elif control.kind == "reset":
                allowed = (control.reason in active
                           and control.occurred_at >= active[control.reason].latched_at)
                if control.reason == "drawdown":
                    allowed &= not _drawdown(assessment, account, peak, transition.config)
                if control.reason == "model_corrupt":
                    identity = _model_identity(observation)
                    latch = active.get("model_corrupt")
                    allowed &= (identity is not None and latch is not None
                                and observation.bundle_assessment.available
                                and latch.latch_evidence[1:4] == identity)
                if control.reason == "ledger_corrupt":
                    allowed &= not source_conflict
                if allowed:
                    _clear(active, resolved, control.reason, control.occurred_at,
                           (control.event_id, control.content_hash, account.event_id, account.ledger_revision))
                else:
                    failure = "reset_unproved"
            else:
                failure = "cashflow_unproved"
        else:
            failure = "control_source_unproved"
    equity = None if assessment is None else assessment.conservative_virtual_equity
    positive_equity = _amount(equity)
    if positive_equity is not None and (peak is None or positive_equity > peak):
        peak = positive_equity
    if account is not None:
        source_peak = _amount(account.high_water_mark)
        if source_peak is not None and (peak is None or source_peak > peak):
            peak = source_peak
    daily = None if assessment is None else assessment.conservative_daily_pnl
    if daily is None and account is not None:
        daily = account.session_realized_pnl
    if _touched(daily, base,
                                            transition.config.daily_loss_fraction, negative=True):
        _latch(active, "daily_loss", at, _evidence(observation, "conservative_daily_pnl"))
    if _drawdown(assessment, account, peak, transition.config):
        _latch(active, "drawdown", at, _evidence(observation, "conservative_virtual_equity"))
    if count >= transition.config.max_entries_per_session:
        _latch(active, "entry_count", at, _evidence(observation, "completed_entry_count"))
    source_regression = (previous is not None and previous.last_account is not None and account is not None
        and (account.account_id, account.source) == (previous.last_account.account_id, previous.last_account.source)
        and account.event_id != previous.last_account.event_id and account.as_of is not None
        and previous.last_account.as_of is not None and account.as_of < previous.last_account.as_of)
    if source_conflict or source_regression or failure is not None or order_conflict or (account is not None and assessment is not None and (
            assessment.integrity_reasons or "ledger_revision_mismatch" in assessment.reconciliation_reasons
            or any(r.startswith("input_integrity:account:") for r in observation.account_evidence_reasons))):
        _latch(active, "ledger_corrupt", at, _evidence(observation, failure or "source_conflict"))
    if account is not None and account.connection != "connected":
        _latch(active, "broker_disconnected", at, _evidence(observation, "connection_" + account.connection))
    elif _source_ok(observation):
        _clear(active, resolved, "broker_disconnected", at, _evidence(observation, "verified_recovery"))
    if account is None or assessment is None or assessment.reconciliation_reasons or assessment.time_reasons:
        _latch(active, "broker_unreconciled", at, _evidence(observation, "account_unreconciled"))
    elif _source_ok(observation):
        _clear(active, resolved, "broker_unreconciled", at, _evidence(observation, "verified_recovery"))
    unready = (observation.context_reasons or observation.mark_source_reasons
        or (assessment is not None and any(mark.mark_reasons for mark in assessment.holding_marks))
        or any(r.startswith("source_admission:") for r in observation.account_evidence_reasons))
    if unready:
        _latch(active, "market_data_unready", at, _evidence(observation, "source_unready"))
    elif observation.context_recheck.valid:
        _clear(active, resolved, "market_data_unready", at, _evidence(observation, "verified_recovery"))
    bundle_assessment = observation.bundle_assessment
    if bundle_assessment is not None:
        if bundle_assessment.bundle is None or _CORRUPT_BUNDLE.intersection(bundle_assessment.reasons):
            identity = _model_identity(observation)
            _latch(active, "model_corrupt", at, ("bundle_recheck_failed",
                   *(identity if identity is not None else ("unknown",) * 3),
                   *_evidence(observation, "bundle_recheck_failed")))
        elif bundle_assessment.reasons:
            _latch(active, "model_unready", at, _evidence(observation, "bundle_unready"))
        else:
            _clear(active, resolved, "model_unready", at, _evidence(observation, "verified_recovery"))
    if previous is not None and previous.account_id is not None and account is not None and (account.account_id, account.source) != (previous.account_id, previous.source):
        _latch(active, "ledger_corrupt", at, _evidence(observation, "account_source_changed"))
    wrong_source = (previous is not None and previous.account_id is not None and account is not None
                    and (account.account_id, account.source) != (previous.account_id, previous.source))
    possible = (True if account is None or failure is not None or wrong_source
                or "ledger_corrupt" in active or not _source_ok(observation)
                else not assessment.observed_flat)
    state = _make(RiskState, account_id=(previous.account_id if previous is not None and previous.account_id is not None else None if account is None else account.account_id),
        source=(previous.source if previous is not None and previous.source is not None else None if account is None else account.source),
        session_date=session_date, session_start_equity=base, high_water_mark=peak,
        session_entry_count=count, ledger_revision=(previous.ledger_revision if account is None and previous is not None else None if account is None else account.ledger_revision),
        last_account=(previous.last_account if account is None and previous is not None else account),
        last_observation=observation,
        halt_state=_make(HaltState, entries=tuple(active[name] for name in _ORDER if name in active),
                         resolved_entries=tuple(resolved)), possible_exposure=possible,
        risk_revision="", transcript=transcript)
    object.__setattr__(state, "risk_revision", _revision(state, transition.config))
    return state


def _drawdown(assessment, account: AccountSnapshot | None,
              peak: Decimal | None, config: StrategyConfig) -> bool:
    """Compare conservative equity with the inclusive peak drawdown floor."""
    equity = None if assessment is None else assessment.conservative_virtual_equity
    if equity is None and account is not None:
        equity = account.virtual_equity
    if equity is None or peak is None:
        return False
    try:
        with localcontext(_money_context()):
            floor = _money(_money(peak) * _money(Decimal(1) - _money(config.drawdown_fraction)))
            return _money(equity) <= floor
    except (DecimalException, ValueError, _UnsupportedIdentity):
        return False


def _model_identity(observation: RiskObservation) -> tuple[str, str, str] | None:
    """Use only a fresh reverified bundle to name its exact model content path."""
    assessed = observation.bundle_assessment
    if assessed is None or assessed.bundle is None:
        return None
    bundle = assessed.bundle
    return bundle.model.model_kind, bundle.manifest.model_id, bundle.model.model_hash


def _replay(transcript: tuple[_RiskTransition, ...]) -> RiskState:
    """Recompute every retained owner and local effect from the original genesis."""
    state = None
    used_ids: dict[str, str] = {}
    used_content: set[str] = set()
    source_events: dict[tuple[str, str, str], dict[str, object]] = {}
    counted_orders: set[tuple] = set()
    for index, transition in enumerate(transcript):
        observed = observe_risk_inputs(transition.context, config=transition.config,
                                       now=transition.now, bundle=transition.bundle)
        state = _step(state, observed, transition, transcript[:index + 1],
                      used_ids, used_content, source_events, counted_orders)
    return state


def _bounded(transcript: tuple[_RiskTransition, ...]) -> bool:
    """Check finite retained source bytes and transition types before nested hooks."""
    if type(transcript) is not tuple or not 1 <= len(transcript) <= _MAX_TRANSITIONS:
        return False
    used, seen = 0, set()
    try:
        for transition in transcript:
            if (type(transition) is not _RiskTransition or type(transition.context) is not DecisionContext
                    or type(transition.config) is not StrategyConfig or type(transition.now) is not datetime
                    or (transition.bundle is not None and type(transition.bundle) is not VerifiedBundle)
                    or (transition.control is not None and type(transition.control) is not RiskControlEvent)):
                return False
            manifests = (transition.context.manifest,)
            if transition.bundle is not None:
                bundle = transition.bundle
                if (type(bundle.upstream_fixtures) is not tuple
                        or type(bundle.supplied_upstream_fixtures) is not tuple
                        or len(bundle.upstream_fixtures) + len(bundle.supplied_upstream_fixtures) > 4096):
                    return False
                manifests += (bundle.original_fixture, bundle.fixture,
                              *bundle.upstream_fixtures, *bundle.supplied_upstream_fixtures)
            for manifest in manifests:
                if manifest is None:
                    continue
                if type(manifest) is not VerifiedFixtureManifest or type(manifest.payload_bytes) is not bytes:
                    return False
                raw = manifest.payload_bytes
                if id(raw) not in seen:
                    seen.add(id(raw))
                    used += len(raw)
            if transition.control is not None:
                raw = transition.control.raw_bytes
                if type(raw) is not bytes:
                    return False
                used += len(raw)
            if used > _MAX_SOURCE_BYTES:
                return False
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def start_risk_state(context: DecisionContext, *, config: StrategyConfig,
                     now: datetime) -> RiskState:
    """Start one source-backed risk history at an actual current P08 context.

    :param    context:  Original admitted current P08 context.
    :param    config:   Exact current strategy configuration.
    :param    now:      Exact current standard aware instant.
    :returns:           Rederivable risk state and reached evidence.
    :raises   TypeError: If trusted top-level types are incorrect.
    :raises   ValueError: If the current instant is invalid.
    :raises   RiskAdvanceError: If the retained source exceeds the resource bound.
    """
    observed = observe_risk_inputs(context, config=config, now=now)
    transition = _make(_RiskTransition, context=context, config=config, now=now, bundle=None, control=None)
    transcript = (transition,)
    if not _bounded(transcript):
        raise RiskAdvanceError("history_limit", previous=None, observation=observed)
    return _step(None, observed, transition, transcript, {}, set(), {}, set())


def advance_risk_state(previous: RiskState, context: DecisionContext, *,
                       config: StrategyConfig, now: datetime,
                       bundle: VerifiedBundle | None = None,
                       control: RiskControlEvent | None = None) -> RiskState:
    """Replay original history and apply one freshly observed risk transition.

    :param    previous:  Prior pure state containing original source/control history.
    :param    context:   Actual current admitted P08 context.
    :param    config:    Exact current strategy configuration.
    :param    now:       Exact current standard aware instant.
    :param    bundle:    Optional actual B2 bundle for fresh readiness proof.
    :param    control:   Optional original normalized local control request.
    :returns:            Rederivable next state and reached evidence.
    :raises   TypeError: If trusted top-level argument types are incorrect.
    :raises   ValueError: If now is not a standard aware instant.
    :raises   RiskAdvanceError: If prior history mismatches or resource bounds exhaust.
    """
    if type(previous) is not RiskState or (control is not None and type(control) is not RiskControlEvent):
        raise TypeError("previous and control require exact trusted types")
    current = observe_risk_inputs(context, config=config, now=now, bundle=bundle)
    try:
        prior_transcript = previous.transcript
        if not _bounded(prior_transcript):
            raise RiskAdvanceError("history_limit", previous=previous, observation=current)
        replayed = _replay(prior_transcript)
        if replayed != previous:
            raise RiskAdvanceError("risk_state_replay_mismatch", previous=previous, observation=current)
        if now < replayed.last_observation.now:
            raise RiskAdvanceError("observation_regression", previous=previous, observation=current)
        transition = _make(_RiskTransition, context=context, config=config, now=now,
                           bundle=bundle, control=control)
        transcript = (*prior_transcript, transition)
        if not _bounded(transcript):
            raise RiskAdvanceError("history_limit", previous=previous, observation=current)
        return _replay(transcript)
    except RiskAdvanceError:
        raise
    except (AttributeError, TypeError, ValueError, DecimalException,
            _UnsupportedIdentity, RecursionError, MemoryError) as error:
        raise RiskAdvanceError("risk_state_replay_mismatch", previous=previous,
                               observation=current) from error
