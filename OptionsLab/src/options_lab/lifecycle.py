"""Pure, bounded reduction of re-proved order reports into local event state."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from .admission import VerifiedFixtureManifest
from .bundle_inputs import _make
from .config import _snapshot_hash
from .contracts import ContractId
from .order_inputs import (OrderInputAdmission, OrderInputValidation, OrderUpdate,
                           admit_order_update)


_MAX_EVENTS = 256
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_ID = 256
_Alias = tuple[Literal["broker", "client"], str]
_Input = OrderInputAdmission | OrderInputValidation | OrderUpdate


@dataclass(frozen=True, init=False)
class CompletedEntry:
    """This class represents one source-reproved entry completion identity."""

    account_id: str
    source: str
    order_aliases: tuple[_Alias, ...]
    contract: ContractId
    first_q1_proof: tuple[str, str, str]
    first_q1_event_at: datetime | None
    first_q1_available_at: datetime | None
    prior_zero_proof: tuple[str, str, str] | None

    def __init__(self) -> None:
        """Reject caller-created completion evidence.

        :returns: None.
        :raises TypeError: Always; reduction creates completion evidence.
        """
        raise TypeError("completed entries come from order reduction")


@dataclass(frozen=True, init=False)
class _EventFact:
    """This class represents one retained normalized claim and proof locator."""

    value: OrderUpdate
    source_record: tuple[str, str] | None
    proved: bool

    def __init__(self) -> None:
        """Block construction outside the reducer.

        :returns: None.
        :raises TypeError: Always; reduction owns event facts.
        """
        raise TypeError("event facts come from order reduction")


@dataclass(frozen=True, init=False)
class _OrderFact:
    """This class represents one locally linked physical order watermark."""

    aliases: tuple[_Alias, ...]
    predecessors: tuple[_Alias, ...]
    successors: tuple[_Alias, ...]
    role: str
    contract: ContractId | None
    cumulative_quantity: int | None
    cumulative_fill_notional_usd: Decimal | None
    cumulative_fees_usd: Decimal | None
    pending: bool
    terminal: bool
    uncertainty_reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Block caller-created order watermarks.

        :returns: None.
        :raises TypeError: Always; reduction owns order facts.
        """
        raise TypeError("order facts come from order reduction")


@dataclass(frozen=True, init=False)
class _LifecycleTransition:
    """This class represents one original attempted lifecycle input."""

    update: _Input

    def __init__(self) -> None:
        """Block caller-created replay transitions.

        :returns: None.
        :raises TypeError: Always; reduction retains original inputs.
        """
        raise TypeError("transitions come from order reduction")


@dataclass(frozen=True, init=False)
class LifecycleState:
    """This class represents bounded local order history, never broker flat proof."""

    account_id: str
    source: str
    phase: Literal["FLAT", "ENTRY_PENDING", "OPEN", "EXIT_PENDING"]
    local_long_quantity: int | None
    possible_exposure: bool
    reconciliation_required: bool
    halt_reasons: tuple[str, ...]
    orders: tuple[_OrderFact, ...]
    seen_events: tuple[_EventFact, ...]
    completed_entries: tuple[CompletedEntry, ...]
    lifecycle_revision: str
    transcript: tuple[_LifecycleTransition, ...]

    def __init__(self) -> None:
        """Require genesis/replay construction.

        :returns: None.
        :raises TypeError: Always; use start_lifecycle or reduce_order.
        """
        raise TypeError("lifecycle states come from order reduction")


@dataclass(frozen=True, init=False)
class LifecycleResult:
    """This class represents one reduction and its newly reached evidence."""

    state: LifecycleState
    applied_event: bool
    proof_upgraded: bool
    newly_completed_entries: tuple[CompletedEntry, ...]
    reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Require a reducer outcome.

        :returns: None.
        :raises TypeError: Always; use reduce_order.
        """
        raise TypeError("lifecycle results come from order reduction")


class LifecycleAdvanceError(ValueError):
    """This class represents a bounded replay denial with retained input."""

    def __init__(self, reason: str, *, previous: LifecycleState,
                 reached_update: _Input) -> None:
        """Keep the prior state and attempted input when reduction is denied.

        :param reason:         Closed reducer error code.
        :param previous:       Caller-supplied prior state.
        :param reached_update: Reached original input.
        :returns:              None.
        """
        super().__init__(reason)
        self.reason = reason
        self.previous = previous
        self.reached_update = reached_update


def _identity(value: str, name: str) -> str:
    """Validate one trusted bootstrap identity.

    :param value: Identity value.
    :param name:  Error field name.
    :returns:     The original bounded string.
    :raises TypeError: If value is not an exact string.
    :raises ValueError: If value is empty, nonprintable or oversized.
    """
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    if not value or len(value) > _MAX_ID or not value.isprintable():
        raise ValueError(f"{name} must be a bounded printable string")
    return value


def start_lifecycle(account_id: str, source: str) -> LifecycleState:
    """Bootstrap only known account/source, retaining unknown broker exposure.

    :param account_id: Trusted account identity.
    :param source:     Trusted provider/source identity.
    :returns:         Initial unreconciled local lifecycle state.
    :raises TypeError: If a trusted identity has the wrong exact type.
    :raises ValueError: If a trusted identity is invalid.
    """
    account_id, source = _identity(account_id, "account_id"), _identity(source, "source")
    return _state(account_id, source, (), (), (), (), 0, "FLAT", ())


def _state(account_id: str, source: str, transcript: tuple[_LifecycleTransition, ...],
           events: tuple[_EventFact, ...], orders: tuple[_OrderFact, ...],
           entries: tuple[CompletedEntry, ...], quantity: int | None,
           phase: Literal["FLAT", "ENTRY_PENDING", "OPEN", "EXIT_PENDING"],
           reasons: tuple[str, ...]) -> LifecycleState:
    """Build one derived state with no broker-flat claim."""
    revision = _snapshot_hash(dict(record_kind="options_lab.lifecycle", version=1,
        account_id=account_id, source=source, phase=phase, quantity=quantity,
        reasons=reasons, events=sorted((e.value.normalized_event_id,
            e.value.semantic_content_hash, e.proved) for e in events),
        orders=[(o.aliases, o.role, o.cumulative_quantity,
            None if o.cumulative_fill_notional_usd is None else str(o.cumulative_fill_notional_usd),
            None if o.cumulative_fees_usd is None else str(o.cumulative_fees_usd)) for o in orders],
        completed=[(e.order_aliases, e.first_q1_proof[2],
                    e.prior_zero_proof is not None) for e in entries]))
    return _make(LifecycleState, account_id=account_id, source=source, phase=phase,
        local_long_quantity=quantity, possible_exposure=True,
        reconciliation_required=True, halt_reasons=reasons, orders=orders,
        seen_events=events, completed_entries=entries,
        lifecycle_revision=revision, transcript=transcript)


def _bounded(transcript: tuple[_LifecycleTransition, ...]) -> bool:
    """Bound original source references before nested reproof."""
    if type(transcript) is not tuple or len(transcript) > _MAX_EVENTS:
        return False
    size = 0
    for step in transcript:
        if type(step) is not _LifecycleTransition:
            return False
        update = object.__getattribute__(step, "update")
        if type(update) not in (OrderInputAdmission, OrderInputValidation, OrderUpdate):
            return False
        if type(update) is OrderInputAdmission:
            fixture = object.__getattribute__(update, "supplied_fixture")
            if type(fixture) is not VerifiedFixtureManifest:
                return False
            payload = object.__getattribute__(fixture, "payload_bytes")
            if type(payload) is not bytes or len(payload) > 8_000_000:
                return False
            size += len(payload)
            if size > _MAX_SOURCE_BYTES:
                return False
    return True


def _claim_bounded(value: OrderUpdate) -> bool:
    """Reject damaged direct claims before comparing any caller-owned value."""
    try:
        for item in vars(value).values():
            if item is None or type(item) in (bool, datetime):
                continue
            if type(item) is str and len(item) <= _MAX_ID:
                continue
            if type(item) is int and 0 <= item <= 2**63 - 1:
                continue
            if type(item) is Decimal and len(item.as_tuple().digits) <= 128:
                continue
            if type(item) is ContractId and all(
                    type(part) in (str, int, Decimal, date)
                    for part in vars(item).values()):
                continue
            return False
        return True
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _facts(transcript: tuple[_LifecycleTransition, ...], account_id: str,
           source: str) -> tuple[tuple[_EventFact, ...], tuple[str, ...]]:
    """Reprove each retained source and keep failed attempts uncertain."""
    events, reasons = {}, set()
    for step in transcript:
        update = step.update
        if type(update) is OrderInputAdmission:
            fresh = admit_order_update(update.supplied_fixture, update.record_id)
            if fresh.source_failure is not None:
                reasons.add("source_admission_failed")
                continue
            validation = fresh.validation
            record = (fresh.manifest.fixture_id, fresh.member.record_id)
            proved = True
        elif type(update) is OrderInputValidation:
            validation, record, proved = update, None, False
        else:
            validation, record, proved = None, None, False
        value = update if validation is None else validation.value
        if value is None or type(value) is not OrderUpdate:
            reasons.add("normalization_failed")
            continue
        if not proved and not _claim_bounded(value):
            reasons.add("unproved_claim_damaged")
            continue
        if (value.account_id, value.source) != (account_id, source):
            reasons.add("account_source_mismatch")
            continue
        if not proved:
            reasons.add("unproved_claim")
        key = (value.normalized_event_id, value.semantic_content_hash)
        prior = events.get(key)
        if prior is None or (proved and not prior.proved):
            events[key] = _make(_EventFact, value=value, source_record=record, proved=proved)
    return tuple(events.values()), tuple(sorted(reasons))


def _derive(account_id: str, source: str,
            transcript: tuple[_LifecycleTransition, ...]) -> LifecycleState:
    """Rebuild one local state from original source attempts."""
    events, reasons = _facts(transcript, account_id, source)
    orders, entries, quantity, phase, more = _reduce_facts(events, account_id, source)
    return _state(account_id, source, transcript, events, orders, entries,
                  quantity, phase, tuple(sorted(set(reasons) | set(more))))


def _source_precedes(zero: OrderUpdate, filled: OrderUpdate) -> tuple[bool, bool]:
    """Require strict, noncontradictory source order for an observed q0.

    :param zero:   Source-admitted cumulative-zero report.
    :param filled: Source-admitted cumulative-one report of the same order.
    :returns:      Strictly earlier proof and contradictory-clock flags.
    """
    comparisons = []
    if zero.provider_sequence is not None and filled.provider_sequence is not None:
        comparisons.append((zero.provider_sequence > filled.provider_sequence)
                           - (zero.provider_sequence < filled.provider_sequence))
    if zero.event_at is not None and filled.event_at is not None:
        comparisons.append((zero.event_at > filled.event_at) - (zero.event_at < filled.event_at))
    return (bool(comparisons) and all(order < 0 for order in comparisons),
            -1 in comparisons and 1 in comparisons)


def _reduce_facts(events: tuple[_EventFact, ...], account_id: str, source: str):
    """Reduce only proved, identified facts; retain all other exposure."""
    reasons = set()
    proved = [e for e in events if e.proved]
    seen = {}
    for event in events:
        key = event.value.normalized_event_id
        prior = seen.get(key)
        if prior is not None and prior != event.value.semantic_content_hash:
            reasons.add("event_identity_conflict")
        seen.setdefault(key, event.value.semantic_content_hash)
    groups: dict[_Alias, set[_Alias]] = {}
    for event in proved:
        value = event.value
        aliases = {("broker", value.broker_order_id)} if value.broker_order_id else set()
        if value.client_order_id:
            aliases.add(("client", value.client_order_id))
        if not aliases:
            reasons.add("order_identity_missing")
            continue
        merged = set(aliases)
        for alias in aliases:
            merged.update(groups.get(alias, ()))
        for alias in merged:
            groups[alias] = merged
    components = sorted({tuple(sorted(value)) for value in groups.values()})
    orders, entries = [], []
    for aliases in components:
        related = [e for e in proved if (("broker", e.value.broker_order_id) in aliases
                   if e.value.broker_order_id else False) or
                   (("client", e.value.client_order_id) in aliases if e.value.client_order_id else False)]
        alias_conflict = (sum(kind == "broker" for kind, _ in aliases) > 1
                          or sum(kind == "client" for kind, _ in aliases) > 1)
        if alias_conflict:
            reasons.add("order_alias_conflict")
        role = related[0].value.role
        contract = related[0].value.contract
        identity_conflict = any(e.value.role != role or e.value.contract != contract
                                for e in related)
        if identity_conflict:
            reasons.add("order_identity_conflict")
            role, contract = "unknown", None
        quantities = [e.value.safe_cumulative_quantity for e in related
                      if e.value.safe_cumulative_quantity is not None]
        quantity = max(quantities) if quantities else None
        if any(e.value.safe_cumulative_quantity is None for e in related):
            reasons.add("quantity_unknown")
        if quantity is not None and quantity > 1:
            reasons.add("quantity_outside_strategy")
        for field in ("cumulative_fill_notional_usd", "cumulative_fees_usd"):
            values = [getattr(e.value, field) for e in related if getattr(e.value, field) is not None]
            if any(v < 0 for v in values) or any(b < a for a, b in zip(values, values[1:])):
                reasons.add("money_conflict")
        money = []
        for field in ("cumulative_fill_notional_usd", "cumulative_fees_usd"):
            known = [getattr(e.value, field) for e in related
                     if getattr(e.value, field) is not None and getattr(e.value, field) >= 0]
            money.append(max(known) if known else None)
        for before, after in zip(related, related[1:]):
            a, b = before.value, after.value
            if (not identity_conflict
                    and a.safe_cumulative_quantity is not None and b.safe_cumulative_quantity is not None
                    and b.safe_cumulative_quantity < a.safe_cumulative_quantity):
                reasons.add("quantity_regression")
            if (not identity_conflict
                    and a.provider_sequence is not None and b.provider_sequence is not None
                    and b.provider_sequence < a.provider_sequence
                    and a.safe_cumulative_quantity is not None
                    and b.safe_cumulative_quantity is not None
                    and b.safe_cumulative_quantity > a.safe_cumulative_quantity):
                reasons.add("old_sequence_higher_quantity")
            if (not identity_conflict and a.terminal is True and b.terminal is False
                    and ((a.provider_sequence is not None and b.provider_sequence is not None
                          and b.provider_sequence > a.provider_sequence)
                         or (a.event_at is not None and b.event_at is not None
                             and b.event_at > a.event_at))):
                reasons.add("terminal_regression")
        statuses = {e.value.status for e in related if e.value.terminal}
        if len(statuses) > 1 or any(e.value.pending and e.value.terminal for e in related):
            reasons.add("terminal_conflict")
        pending = any(e.value.pending is True for e in related)
        terminal = any(e.value.terminal is True for e in related)
        if terminal:
            pending = False
        orders.append(_make(_OrderFact, aliases=aliases, predecessors=(), successors=(),
            role=role, contract=contract, cumulative_quantity=quantity,
            cumulative_fill_notional_usd=money[0], cumulative_fees_usd=money[1],
            pending=pending, terminal=terminal, uncertainty_reasons=()))
        q1 = [e for e in related if e.value.role == "buy_entry"
              and e.value.safe_cumulative_quantity == 1 and e.source_record]
        if q1 and not identity_conflict and not alias_conflict and contract is not None:
            chosen = min(q1, key=lambda e: (*e.source_record, e.value.normalized_event_id))
            proof = (*chosen.source_record, chosen.value.normalized_event_id)
            prior_zero = None
            zero_candidates = []
            for earlier in related:
                a, b = earlier.value, chosen.value
                if (a.safe_cumulative_quantity != 0 or not earlier.source_record
                        or a.role != "buy_entry" or a.contract != b.contract):
                    continue
                relations = [_source_precedes(a, item.value) for item in q1]
                if any(conflict for _, conflict in relations):
                    reasons.add("source_order_conflict")
                if all(prior for prior, _ in relations):
                    zero_candidates.append((*earlier.source_record, a.normalized_event_id))
            if zero_candidates:
                prior_zero = min(zero_candidates)
            entries.append(_make(CompletedEntry, account_id=account_id, source=source,
                order_aliases=aliases, contract=chosen.value.contract,
                first_q1_proof=proof,
                first_q1_event_at=chosen.value.event_at,
                first_q1_available_at=chosen.value.available_at,
                prior_zero_proof=prior_zero))
    alias_index = {alias: index for index, order in enumerate(orders)
                   for alias in order.aliases}
    links: set[tuple[int, int]] = set()
    predecessors: list[set[_Alias]] = [set() for _ in orders]
    successors: list[set[_Alias]] = [set() for _ in orders]
    for event in proved:
        value = event.value
        own = [alias_index[key] for key in
               (("broker", value.broker_order_id), ("client", value.client_order_id))
               if key in alias_index]
        if not own:
            continue
        current = own[0]
        if any(index != current for index in own):
            reasons.add("order_identity_conflict")
            continue
        for kind, name in (("broker", value.replaces_broker_order_id),
                           ("client", value.replaces_client_order_id)):
            if name is None:
                continue
            target = (kind, name)
            predecessors[current].add(target)
            if target in alias_index:
                parent = alias_index[target]
                if parent == current:
                    reasons.add("replacement_cycle")
                else:
                    links.add((parent, current))
        for kind, name in (("broker", value.replaced_by_broker_order_id),
                           ("client", value.replaced_by_client_order_id)):
            if name is None:
                continue
            target = (kind, name)
            successors[current].add(target)
            if target in alias_index:
                child = alias_index[target]
                if child == current:
                    reasons.add("replacement_cycle")
                else:
                    links.add((current, child))
    for parent, child in links:
        successors[parent].update(orders[child].aliases)
        predecessors[child].update(orders[parent].aliases)
    orders = [_make(_OrderFact, **{**vars(order),
        "predecessors": tuple(sorted(predecessors[index])),
        "successors": tuple(sorted(successors[index]))})
        for index, order in enumerate(orders)]
    chain_sets = [{index} for index in range(len(orders))]
    for parent, child in links:
        joined = chain_sets[parent] | chain_sets[child]
        for index in joined:
            chain_sets[index] = joined
    chains = sorted({tuple(sorted(component)) for component in chain_sets})
    merged_entries = []
    for chain in chains:
        members = [entry for entry in entries
                   if any(alias in orders[index].aliases for index in chain
                          for alias in entry.order_aliases)]
        if not members:
            continue
        selected = min(members, key=lambda entry: entry.first_q1_proof)
        all_aliases = tuple(sorted({alias for index in chain
                                    for alias in orders[index].aliases}))
        if any(entry.contract != selected.contract for entry in members):
            reasons.add("replacement_contract_conflict")
        merged_entries.append(_make(CompletedEntry, account_id=account_id,
            source=source, order_aliases=all_aliases, contract=selected.contract,
            first_q1_proof=selected.first_q1_proof,
            first_q1_event_at=selected.first_q1_event_at,
            first_q1_available_at=selected.first_q1_available_at,
            prior_zero_proof=selected.prior_zero_proof))
        if sum(orders[index].cumulative_quantity is not None
               and orders[index].cumulative_quantity > 0 for index in chain) > 1:
            reasons.add("replacement_quantity_ambiguous")
    entries = merged_entries
    entry_qty = sum(o.cumulative_quantity or 0 for o in orders if o.role == "buy_entry")
    exit_qty = sum(o.cumulative_quantity or 0 for o in orders if o.role == "sell_exit")
    local = entry_qty - exit_qty
    if local < 0:
        reasons.add("negative_local_balance")
        local = None
    if any(o.role == "unknown" and (o.cumulative_quantity or 0) > 0 for o in orders):
        reasons.add("role_unknown")
    if ("quantity_unknown" in reasons or "order_identity_missing" in reasons
            or "replacement_quantity_ambiguous" in reasons
            or "order_alias_conflict" in reasons or "role_unknown" in reasons
            or "order_identity_conflict" in reasons):
        local = None
    exit_pending = any(o.pending and o.role == "sell_exit" for o in orders)
    entry_pending = any(o.pending and o.role == "buy_entry" for o in orders)
    phase = ("EXIT_PENDING" if exit_pending else "OPEN" if local is None or local > 0
             else "ENTRY_PENDING" if entry_pending else "FLAT")
    return tuple(orders), tuple(entries), local, phase, tuple(sorted(reasons))


def reduce_order(state: LifecycleState, update: _Input) -> LifecycleResult:
    """Reprove original reports and reduce one reached order update.

    :param state:  Prior exact replayable lifecycle state.
    :param update: Actual admission or explicitly unproved P19 claim.
    :returns:     Next local state and newly reached completion evidence.
    :raises TypeError: If a trusted top-level argument has the wrong exact type.
    :raises LifecycleAdvanceError: If history is damaged or a bound is reached.
    """
    if type(state) is not LifecycleState or type(update) not in (
            OrderInputAdmission, OrderInputValidation, OrderUpdate):
        raise TypeError("state and update require exact lifecycle/P19 types")
    try:
        if not _bounded(state.transcript):
            raise LifecycleAdvanceError("history_limit", previous=state, reached_update=update)
        previous = _derive(state.account_id, state.source, state.transcript)
        if previous != state:
            raise LifecycleAdvanceError("state_replay_mismatch", previous=state,
                                        reached_update=update)
        transcript = (*state.transcript, _make(_LifecycleTransition, update=update))
        if not _bounded(transcript):
            raise LifecycleAdvanceError("history_limit", previous=state, reached_update=update)
        current = _derive(state.account_id, state.source, transcript)
        old_aliases = [set(item.order_aliases) for item in state.completed_entries]
        new_entries = tuple(item for item in current.completed_entries
                            if not any(set(item.order_aliases) & old for old in old_aliases))
        prior_unproved = {(e.value.normalized_event_id, e.value.semantic_content_hash)
                          for e in state.seen_events if not e.proved}
        upgraded = any((e.value.normalized_event_id, e.value.semantic_content_hash)
                       in prior_unproved for e in current.seen_events if e.proved)
        old_proved = {(e.value.normalized_event_id, e.value.semantic_content_hash)
                      for e in state.seen_events if e.proved}
        applied = any((e.value.normalized_event_id, e.value.semantic_content_hash)
                      not in old_proved for e in current.seen_events if e.proved)
        return _make(LifecycleResult, state=current,
            applied_event=applied,
            proof_upgraded=upgraded, newly_completed_entries=new_entries,
            reasons=current.halt_reasons)
    except LifecycleAdvanceError:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError, RecursionError, MemoryError) as error:
        raise LifecycleAdvanceError("state_replay_mismatch", previous=state,
                                    reached_update=update) from error
