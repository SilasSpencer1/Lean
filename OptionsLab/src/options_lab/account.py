"""Immutable account claims and bounded currentness, never capital approval."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import (
    MAX_EMAX, MIN_EMIN, Context, Decimal, DecimalException, DivisionByZero,
    Inexact, InvalidOperation, Overflow, localcontext,
)
from typing import Literal
from zoneinfo import ZoneInfo

from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .bar_inputs import _identity_decimal, _MAX_INTEGER_EXCLUSIVE, _UnsupportedIdentity
from .config import StrategyConfig
from .contracts import ContractId
from .observations import ObservationAssessment, assess_observation
from .quotes import QuoteObservation
from .sessions import ExchangeSession

_NY = ZoneInfo("America/New_York")
_MONEY_FIELDS = (
    "virtual_cash", "virtual_equity", "session_start_equity", "high_water_mark",
    "session_realized_pnl", "reported_marked_pnl", "virtual_settled_cash",
    "virtual_reserved_cash", "broker_settled_cash", "broker_available_cash",
    "broker_nonmargin_buying_power", "applicable_round_trip_fees",
)


@dataclass(frozen=True)
class HoldingFact:
    """This class represents retained signed exposure, including recovery facts."""

    position_id: str
    instrument_ref: str
    contract: ContractId | None
    asset_kind: Literal["option", "equity", "unknown"]
    quantity_unit: Literal["contracts", "shares", "unknown"]
    quantity: Decimal | None
    basis_debit: Decimal | None
    entry_filled_at: datetime | None
    entry_client_order_id: str | None
    mark_quote: QuoteObservation | None
    estimated_remaining_close_cost: Decimal | None
    source: str
    provider_record_id: str
    raw_ref: str

    def __post_init__(self) -> None:
        """
        Validate representation without enforcing entry or monetary policy.

        Basis is total remaining entry premium excluding already posted fees.
        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact type.
        :raises   ValueError: If a string, token, decimal or timestamp is invalid.
        """
        _fact_fields(self)
        _require_nonempty_string("position_id", self.position_id)
        _require_token("asset_kind", self.asset_kind, ("option", "equity", "unknown"))
        _require_token("quantity_unit", self.quantity_unit, ("contracts", "shares", "unknown"))
        _optional_strings(self, ("entry_client_order_id",))
        _decimals(self, ("quantity", "basis_debit", "estimated_remaining_close_cost"))
        _times(self, ("entry_filled_at",))
        if self.mark_quote is not None and type(self.mark_quote) is not QuoteObservation:
            raise TypeError("mark_quote must be a QuoteObservation or None")


@dataclass(frozen=True)
class OpenOrderFact:
    """This class represents observed orders, not fills or lifecycle transitions."""

    order_ref: str
    client_order_id: str | None
    instrument_ref: str
    contract: ContractId | None
    side: Literal["buy", "sell", "unknown"]
    role: Literal["entry", "exit", "unknown"]
    status: Literal["open", "cancel_pending", "replace_pending", "terminal", "unknown"]
    remaining_quantity: Decimal | None
    cumulative_filled_quantity: Decimal | None
    reserved_cash: Decimal | None
    execution_uncertain: bool
    source: str
    provider_record_id: str
    raw_ref: str

    def __post_init__(self) -> None:
        """
        Validate representation while retaining contradictory order claims.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact type.
        :raises   ValueError: If a string, token or decimal is invalid.
        """
        _fact_fields(self)
        _require_nonempty_string("order_ref", self.order_ref)
        _optional_strings(self, ("client_order_id",))
        _require_token("side", self.side, ("buy", "sell", "unknown"))
        _require_token("role", self.role, ("entry", "exit", "unknown"))
        _require_token("status", self.status, ("open", "cancel_pending", "replace_pending", "terminal", "unknown"))
        _decimals(self, ("remaining_quantity", "cumulative_filled_quantity", "reserved_cash"))
        if type(self.execution_uncertain) is not bool:
            raise TypeError("execution_uncertain must be a bool")


@dataclass(frozen=True)
class AccountSnapshot:
    """This class represents complete retained account claims, including unknowns."""

    account_id: str
    event_id: str
    currency: str
    source: str
    provider_record_id: str
    raw_ref: str
    received_at: datetime
    available_at: datetime | None
    availability_basis: Literal["measured", "assumed"]
    as_of: datetime | None
    reconciled_at: datetime | None
    session_date: date
    ledger_revision: str | None
    reconciled_ledger_revision: str | None
    reconciliation_id: str | None
    risk_state_revision: str | None
    halt_checkpoint_ref: str | None
    connection: Literal["connected", "disconnected", "unknown"]
    holdings_completeness: Literal["complete", "incomplete", "unknown"]
    orders_completeness: Literal["complete", "incomplete", "unknown"]
    virtual_cash: Decimal | None
    virtual_equity: Decimal | None
    session_start_equity: Decimal | None
    high_water_mark: Decimal | None
    session_realized_pnl: Decimal | None
    reported_marked_pnl: Decimal | None
    virtual_settled_cash: Decimal | None
    virtual_reserved_cash: Decimal | None
    broker_settled_cash: Decimal | None
    broker_available_cash: Decimal | None
    broker_nonmargin_buying_power: Decimal | None
    applicable_round_trip_fees: Decimal | None
    holdings: tuple[HoldingFact, ...]
    open_orders: tuple[OpenOrderFact, ...]

    def __post_init__(self) -> None:
        """
        Validate exact fields without replacing missing capital or exposure.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact type.
        :raises   ValueError: If a string, token, decimal or timestamp is invalid.
        """
        for name in ("account_id", "event_id", "currency", "source", "provider_record_id", "raw_ref"):
            _require_nonempty_string(name, getattr(self, name))
        _optional_strings(self, ("ledger_revision", "reconciled_ledger_revision", "reconciliation_id",
                                "risk_state_revision", "halt_checkpoint_ref"))
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        _times(self, ("available_at", "as_of", "reconciled_at"))
        if type(self.session_date) is not date:
            raise TypeError("session_date must be a date")
        _require_token("availability_basis", self.availability_basis, ("measured", "assumed"))
        _require_token("connection", self.connection, ("connected", "disconnected", "unknown"))
        for name in ("holdings_completeness", "orders_completeness"):
            _require_token(name, getattr(self, name), ("complete", "incomplete", "unknown"))
        _decimals(self, _MONEY_FIELDS)
        for name, kind in (("holdings", HoldingFact), ("open_orders", OpenOrderFact)):
            values = getattr(self, name)
            if type(values) is not tuple or any(type(value) is not kind for value in values):
                raise TypeError(f"{name} must be an exact tuple of {kind.__name__} values")


@dataclass(frozen=True)
class AccountAssessment:
    """
    This class represents derived currentness and internally compared claims.

    Observed flatness requires actually empty complete lists and current coherent
    claims. It proves neither source admission, true reconciliation nor entry
    authority. Conservative dollars remain conditional on the retained claims;
    time failures do not erase independently interpretable adverse amounts.
    Risk references remain opaque on the actual snapshot.
    """

    account: AccountSnapshot | None
    session: ExchangeSession | None
    config: StrategyConfig
    now: datetime
    strategy_date: date | None = field(init=False)
    integrity_reasons: tuple[str, ...] = field(init=False)
    time_reasons: tuple[str, ...] = field(init=False)
    session_reasons: tuple[str, ...] = field(init=False)
    reconciliation_reasons: tuple[str, ...] = field(init=False)
    exposure_reasons: tuple[str, ...] = field(init=False)
    account_age: timedelta | None = field(init=False)
    reconciliation_age: timedelta | None = field(init=False)
    ledger_revision_matches: bool = field(init=False)
    has_nonzero_holding: bool = field(init=False)
    has_unknown_holding_quantity: bool = field(init=False)
    has_open_order_records: bool = field(init=False)
    execution_uncertain: bool = field(init=False)
    observed_flat: bool = field(init=False)
    holding_marks: tuple["HoldingMarkAssessment", ...] = field(init=False)
    capital_reasons: tuple[str, ...] = field(init=False)
    unrealized_pnl: Decimal | None = field(init=False)
    conservative_daily_pnl: Decimal | None = field(init=False)
    computed_liquidation_equity: Decimal | None = field(init=False)
    conservative_virtual_equity: Decimal | None = field(init=False)
    virtual_unencumbered_cash: Decimal | None = field(init=False)
    effective_available_cash: Decimal | None = field(init=False)

    def __post_init__(self) -> None:
        """
        Derive every result from actual retained inputs at the explicit time.

        :returns:             None.
        :raises   TypeError:  If a retained input has the wrong exact type.
        :raises   ValueError: If now is not a representable aware datetime.
        """
        for name, kind in (("account", AccountSnapshot), ("session", ExchangeSession)):
            value = getattr(self, name)
            if value is not None and type(value) is not kind:
                raise TypeError(f"{name} must be a {kind.__name__} or None")
        if type(self.config) is not StrategyConfig:
            raise TypeError("config must be a StrategyConfig")
        object.__setattr__(self, "now", _trusted_datetime("now", self.now))
        _derive_account(self)
        _derive_capital(self)


def assess_account(account: AccountSnapshot | None, *, session: ExchangeSession | None,
                   config: StrategyConfig, now: datetime) -> AccountAssessment:
    """
    Assess bounded account claims without reading a clock or authorizing action.

    :param    account:    Actual immutable snapshot, or None for unknown state.
    :param    session:    Actual calendar claim, or None when unavailable.
    :param    config:     Exact strategy configuration with independent age limits.
    :param    now:        Explicit aware assessment time.
    :returns:             Derived account evidence retaining every supplied fact.
    :raises   TypeError:  If a retained input has the wrong exact type.
    :raises   ValueError: If now is not a representable aware datetime.
    """
    return AccountAssessment(account, session, config, now)


def _fact_fields(fact: HoldingFact | OpenOrderFact) -> None:
    """Validate the common source and instrument fields of account facts."""
    for name in ("instrument_ref", "source", "provider_record_id", "raw_ref"):
        _require_nonempty_string(name, getattr(fact, name))
    if fact.contract is not None and type(fact.contract) is not ContractId:
        raise TypeError("contract must be a ContractId or None")


def _optional_strings(owner: object, names: tuple[str, ...]) -> None:
    """Validate explicitly nullable opaque source references."""
    for name in names:
        value = getattr(owner, name)
        if value is not None:
            _require_nonempty_string(name, value)


def _decimals(owner: object, names: tuple[str, ...]) -> None:
    """Validate nullable signed finite Decimal facts without policy bounds."""
    for name in names:
        value = getattr(owner, name)
        if value is not None:
            if type(value) is not Decimal:
                raise TypeError(f"{name} must be a Decimal or None")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")


def _times(owner: object, names: tuple[str, ...]) -> None:
    """Normalize explicitly nullable fact timestamps to UTC."""
    for name in names:
        value = getattr(owner, name)
        if value is not None:
            object.__setattr__(owner, name, _trusted_datetime(name, value))


def _derive_account(result: AccountAssessment) -> None:
    """Derive ordered clock/calendar/revision groups and all retained occupancy."""
    account, now = result.account, result.now
    times: list[str] = []
    try:
        day = now.astimezone(_NY).date()
    except (OverflowError, ValueError):
        day = None
        times.append("time_arithmetic_unsupported")
    integrity, exposure = _fact_reasons(account, day, now, times)
    sessions = _calendar_reasons(result, day)
    reconciliation: list[str] = []
    account_age = reconciliation_age = None
    matches = False
    holdings = () if account is None else account.holdings
    orders = () if account is None else account.open_orders
    if account is not None:
        if account.available_at is None:
            times.append("account_availability_unknown")
        elif account.available_at > now:
            times.append("account_available_after_now")
        if account.availability_basis != "measured":
            times.append("account_availability_not_measured")
        for stamp, prefix, maximum in ((account.as_of, "account_as_of", result.config.max_account_age),
                                       (account.reconciled_at, "reconciliation", result.config.max_reconciliation_age)):
            if stamp is None:
                times.append("account_as_of_missing" if prefix == "account_as_of" else "reconciliation_time_missing")
                continue
            if account.available_at is not None and stamp > account.available_at:
                times.append(f"{prefix}_after_available")
            age = now - stamp
            if stamp > now:
                times.append(f"{prefix}_after_now")
            elif age > maximum:
                times.append("account_too_old" if prefix == "account_as_of" else "reconciliation_too_old")
            if prefix == "account_as_of":
                account_age = age
            else:
                reconciliation_age = age
        for name in ("ledger_revision", "reconciled_ledger_revision", "reconciliation_id"):
            if getattr(account, name) is None:
                reconciliation.append(f"{name}_missing")
        matches = account.ledger_revision is not None and account.reconciled_ledger_revision is not None and account.ledger_revision == account.reconciled_ledger_revision
        if account.ledger_revision is not None and account.reconciled_ledger_revision is not None and not matches:
            reconciliation.append("ledger_revision_mismatch")
        if account.connection != "connected":
            reconciliation.append(f"connection_{account.connection}")
        for name in ("holdings", "orders"):
            completeness = getattr(account, f"{name}_completeness")
            if completeness != "complete":
                reconciliation.append(f"{name}_{completeness}")
    values = dict(strategy_date=day, integrity_reasons=tuple(sorted(integrity)), time_reasons=tuple(dict.fromkeys(times)),
                  session_reasons=tuple(sessions), reconciliation_reasons=tuple(reconciliation), exposure_reasons=tuple(sorted(exposure)),
                  account_age=account_age, reconciliation_age=reconciliation_age, ledger_revision_matches=matches,
                  has_nonzero_holding=any(f.quantity is not None and f.quantity != 0 for f in holdings),
                  has_unknown_holding_quantity=any(f.quantity is None for f in holdings), has_open_order_records=bool(orders),
                  execution_uncertain=any(f.execution_uncertain or f.status in ("cancel_pending", "replace_pending", "unknown") for f in orders),
                  observed_flat=account is not None and not holdings and not orders and not (integrity or times or sessions or reconciliation))
    for name, value in values.items():
        object.__setattr__(result, name, value)


def _calendar_reasons(result: AccountAssessment, day: date | None) -> list[str]:
    """Check accounting-day claims without entry hours, windows or cadence."""
    reasons = []
    if day is not None and result.account is not None and result.account.session_date != day:
        reasons.append("account_wrong_date")
    session = result.session
    if session is None:
        return [*reasons, "session_missing"]
    checks = ((session.calendar != result.config.strategy_calendar, "calendar_unsupported"),
              (day is not None and session.session_date != day, "session_wrong_date"),
              (session.available_at is None, "session_availability_unknown"),
              (session.available_at is not None and session.available_at > result.now, "session_available_after_now"),
              (session.availability_basis != "measured", "session_availability_not_measured"),
              (session.kind == "unknown", "session_unknown"))
    return [*reasons, *(reason for failed, reason in checks if failed)]


def _fractional(value: Decimal) -> bool:
    """Inspect finite coefficients without expanding large Decimal exponents."""
    _, digits, exponent = value.as_tuple()
    return exponent < 0 and any(digits[exponent:])


def _fact_reasons(account: AccountSnapshot | None, day: date | None, now: datetime,
                  times: list[str]) -> tuple[set[str], set[str]]:
    """Retain deterministic lexical reason sets without reducing source facts."""
    if account is None:
        return {"account_missing"}, {"account_state_unknown"}
    integrity, exposure = set(), set()
    if account.currency != "USD":
        integrity.add("currency_unsupported")
    for facts, key, prefix in ((account.holdings, "position_id", "holding"), (account.open_orders, "order_ref", "order")):
        identities, sources = {}, {}
        for fact in facts:
            for seen, identity, reason in ((identities, getattr(fact, key), f"{prefix}_identity_conflict"),
                                          (sources, (fact.source, fact.provider_record_id), f"{prefix}_source_identity_conflict")):
                if identity in seen and seen[identity] != fact:
                    integrity.add(reason)
                seen.setdefault(identity, fact)
    for owner, fields in ((account, ("virtual_reserved_cash", "applicable_round_trip_fees")),
                          *((fact, ("basis_debit", "estimated_remaining_close_cost")) for fact in account.holdings),
                          *((fact, ("reserved_cash",)) for fact in account.open_orders)):
        for name in fields:
            value = getattr(owner, name)
            if value is not None and value < 0:
                integrity.add(f"{name}_negative")
    if len(account.holdings) > 1:
        exposure.add("multiple_holdings")
    for fact in account.holdings:
        exposure.add("holding_records")
        if fact.mark_quote is not None and fact.mark_quote.contract != fact.contract:
            integrity.add("holding_mark_contract_mismatch")
        checks = ((fact.asset_kind != "option", "holding_asset_unsupported"), (fact.quantity_unit != "contracts", "holding_unit_unsupported"),
                  (fact.contract is None, "holding_contract_unknown"), (fact.quantity is None, "holding_quantity_unknown"),
                  (fact.entry_filled_at is None, "holding_entry_time_unknown"))
        exposure.update(reason for failed, reason in checks if failed)
        if fact.quantity is not None:
            checks = ((fact.quantity == 0, "holding_zero_quantity"), (fact.quantity < 0, "holding_short"),
                      (_fractional(fact.quantity), "holding_fractional_quantity"), (fact.quantity != 1, "holding_quantity_not_one"))
            exposure.update(reason for failed, reason in checks if failed)
        if fact.entry_filled_at is not None:
            if fact.entry_filled_at > now:
                exposure.add("holding_entry_after_now")
            try:
                if day is not None and fact.entry_filled_at.astimezone(_NY).date() < day:
                    exposure.add("holding_prior_session")
            except (OverflowError, ValueError):
                times.append("time_arithmetic_unsupported")
    for fact in account.open_orders:
        exposure.add("open_order_records")
        exposure.add(f"order_role_{fact.role}")
        exposure.add(f"order_status_{fact.status}")
        if fact.contract is None:
            exposure.add("order_contract_unknown")
        if (fact.side, fact.role) in (("buy", "exit"), ("sell", "entry")):
            integrity.add("order_side_role_mismatch")
        for quantity in (fact.remaining_quantity, fact.cumulative_filled_quantity):
            if quantity is None:
                exposure.add("order_quantity_unknown")
            elif quantity < 0:
                integrity.add("order_negative_quantity")
            if quantity is not None and _fractional(quantity):
                exposure.add("order_fractional_quantity")
    return integrity, exposure


@dataclass(frozen=True)
class HoldingMarkAssessment:
    """
    This class represents a bid observation or long-premium monetary stress.

    It retains actual source claims, never admitted provenance or a sell fill.
    Missing entry/ask/model prerequisites cannot erase an independent bid.
    """

    holding: HoldingFact
    config: StrategyConfig
    now: datetime
    observation: ObservationAssessment | None = field(init=False)
    holding_reasons: tuple[str, ...] = field(init=False)
    mark_reasons: tuple[str, ...] = field(init=False)
    cost_reasons: tuple[str, ...] = field(init=False)
    valuation_basis: Literal["observed_bid", "zero_bid_nonexecutable", "full_premium_stress", "unavailable"] = field(init=False)
    gross_liquidation_value: Decimal | None = field(init=False)
    net_liquidation_value: Decimal | None = field(init=False)
    unrealized_pnl: Decimal | None = field(init=False)

    def __post_init__(self) -> None:
        """
        Derive independent mark and cost evidence from the retained holding.

        :returns:             None.
        :raises   TypeError:  If holding or config has the wrong exact type.
        :raises   ValueError: If now is not a representable aware datetime.
        """
        if type(self.holding) is not HoldingFact:
            raise TypeError("holding must be a HoldingFact")
        if type(self.config) is not StrategyConfig:
            raise TypeError("config must be a StrategyConfig")
        object.__setattr__(self, "now", _trusted_datetime("now", self.now))
        _derive_mark(self)


def assess_holding_mark(holding: HoldingFact, *, config: StrategyConfig,
                        now: datetime) -> HoldingMarkAssessment:
    """
    Assess one actual bid and the separately bounded long-premium stress.

    :param    holding:    Actual immutable exposure, including unknown facts.
    :param    config:     Exact configuration supplying the independent quote age.
    :param    now:        Explicit aware assessment time.
    :returns:             Derived monetary observations, never a sale or approval.
    :raises   TypeError:  If holding or config has the wrong exact type.
    :raises   ValueError: If now is not a representable aware datetime.
    """
    return HoldingMarkAssessment(holding, config, now)


def _money_context() -> Context:
    """Return explicit exact arithmetic, allowing only value-preserving rounding."""
    return Context(prec=1000, Emax=MAX_EMAX, Emin=MIN_EMIN,
                   traps=[Inexact, InvalidOperation, Overflow, DivisionByZero])


def _money(value: Decimal) -> Decimal:
    """Canonicalize a bounded operand/result before any exponent expansion."""
    if not value.is_finite():
        raise _UnsupportedIdentity
    return Decimal(_identity_decimal(value))


def _sum_money(values) -> Decimal:
    """Sum bounded signed amounts exactly in deterministic numerical order."""
    operands = sorted(_money(value) for value in values)
    with localcontext(_money_context()):
        total = Decimal(0)
        for value in operands:
            total = _money(total + value)
    return total


def _derive_mark(result: HoldingMarkAssessment) -> None:
    """Derive holding, metadata, bid, cost and exact arithmetic evidence."""
    holding, quote, now = result.holding, result.holding.mark_quote, result.now
    holding_reasons, mark_reasons, cost_reasons = [], [], []
    checks = ((holding.asset_kind != "option", "holding_asset_unsupported"),
              (holding.quantity_unit != "contracts", "holding_unit_unsupported"),
              (holding.contract is None, "holding_contract_unknown"),
              (holding.contract is not None and holding.contract.multiplier != 100, "unsupported_multiplier"),
              (holding.quantity is None, "holding_quantity_unknown"),
              (holding.quantity is not None and holding.quantity <= 0, "holding_quantity_nonpositive"),
              (holding.quantity is not None and _fractional(holding.quantity), "holding_fractional_quantity"))
    holding_reasons.extend(reason for failed, reason in checks if failed)
    quantity = None
    if not holding_reasons:
        try:
            quantity = _money(holding.quantity)
        except (DecimalException, _UnsupportedIdentity):
            holding_reasons.append("arithmetic_precision_unsupported")
    observation = None
    zero_observed = False
    if quote is None:
        mark_reasons.append("mark_missing")
    else:
        observation = assess_observation(quote.meta, decision_at=now,
                                         max_quote_age=result.config.execution.max_quote_age)
        if quote.contract != holding.contract:
            mark_reasons.append("mark_contract_mismatch")
        if quote.bid_at is None:
            mark_reasons.append("bid_time_missing")
        else:
            if quote.bid_at > quote.meta.available_at:
                mark_reasons.append("bid_after_available")
            if quote.bid_at > now:
                mark_reasons.append("bid_after_now")
            elif now - quote.bid_at > result.config.execution.max_quote_age:
                mark_reasons.append("bid_too_old")
        if quote.bid is None:
            mark_reasons.append("bid_missing")
        elif quote.bid == 0:
            zero_observed = observation.live_quote_time_suitable and not mark_reasons
            mark_reasons.append("zero_bid_nonexecutable")
        if quote.bid_size is None:
            mark_reasons.append("bid_size_unknown")
        elif quote.bid_size >= _MAX_INTEGER_EXCLUSIVE:
            mark_reasons.append("arithmetic_precision_unsupported")
        elif holding.quantity is not None and quote.bid_size < holding.quantity:
            mark_reasons.append("bid_size_insufficient")
    gross = net = unrealized = None
    basis = "unavailable"
    if not holding_reasons:
        if quote is not None and observation.live_quote_time_suitable and not mark_reasons:
            try:
                with localcontext(_money_context()):
                    gross = _money(_money(quantity * Decimal(100)) * _money(quote.bid))
                basis = "observed_bid"
            except (DecimalException, _UnsupportedIdentity):
                mark_reasons.append("arithmetic_precision_unsupported")
        else:
            gross = Decimal(0)
            basis = "zero_bid_nonexecutable" if zero_observed else "full_premium_stress"
    costs = {}
    for name in ("estimated_remaining_close_cost", "basis_debit"):
        value = getattr(holding, name)
        costs[name] = None
        if value is None:
            cost_reasons.append(f"{name}_missing")
        elif value < 0:
            cost_reasons.append(f"{name}_negative")
        else:
            try:
                costs[name] = _money(value)
            except (DecimalException, _UnsupportedIdentity):
                cost_reasons.append("arithmetic_precision_unsupported")
    if gross is not None and costs["estimated_remaining_close_cost"] is not None:
        try:
            net = _sum_money((gross, costs["estimated_remaining_close_cost"].copy_negate()))
        except (DecimalException, _UnsupportedIdentity):
            cost_reasons.append("arithmetic_precision_unsupported")
    if net is not None and costs["basis_debit"] is not None:
        try:
            unrealized = _sum_money((net, costs["basis_debit"].copy_negate()))
        except (DecimalException, _UnsupportedIdentity):
            cost_reasons.append("arithmetic_precision_unsupported")
    values = dict(observation=observation, holding_reasons=tuple(holding_reasons),
                  mark_reasons=tuple(mark_reasons), cost_reasons=tuple(dict.fromkeys(cost_reasons)),
                  valuation_basis=basis, gross_liquidation_value=gross,
                  net_liquidation_value=net, unrealized_pnl=unrealized)
    for name, value in values.items():
        object.__setattr__(result, name, value)


def _derive_capital(result: AccountAssessment) -> None:
    """Derive dependency-specific amounts without overriding account currentness."""
    account = result.account
    marks = () if account is None else tuple(
        assess_holding_mark(holding, config=result.config, now=result.now) for holding in account.holdings)
    values = dict(unrealized_pnl=None, conservative_daily_pnl=None, computed_liquidation_equity=None,
                  conservative_virtual_equity=None, virtual_unencumbered_cash=None, effective_available_cash=None)
    reasons = []
    if account is None:
        reasons.append("account_missing")
    else:
        if account.currency != "USD":
            reasons.append("currency_unsupported")
        if result.integrity_reasons or not result.ledger_revision_matches or account.reconciliation_id is None:
            reasons.append("ledger_interpretation_unavailable")
        for facts, key in ((account.holdings, "position_id"), (account.open_orders, "order_ref")):
            if (len({getattr(fact, key) for fact in facts}) != len(facts)
                    or len({(fact.source, fact.provider_record_id) for fact in facts}) != len(facts)):
                reasons.append("duplicate_source_fact_unresolved")
        if account.open_orders:
            reasons.append("order_execution_accounting_unresolved")
        if account.holdings_completeness != "complete":
            reasons.append("holdings_incomplete_or_unknown")
        if account.orders_completeness != "complete":
            reasons.append("orders_incomplete_or_unknown")
        interpretable = not reasons
        complete_marks = all(mark.unrealized_pnl is not None for mark in marks)
        if not complete_marks:
            reasons.append("holding_valuation_unavailable")
        operands = {}
        arithmetic_failed = False
        for name in ("virtual_cash", "virtual_equity", "session_realized_pnl", "virtual_settled_cash",
                     "virtual_reserved_cash", "broker_settled_cash", "broker_available_cash", "broker_nonmargin_buying_power"):
            value = getattr(account, name)
            operands[name] = None
            if value is None:
                reasons.append(f"{name}_missing")
            else:
                try:
                    operands[name] = _money(value)
                except (DecimalException, _UnsupportedIdentity):
                    arithmetic_failed = True
        if interpretable and complete_marks:
            try:
                values["unrealized_pnl"] = _sum_money(mark.unrealized_pnl for mark in marks)
                if operands["session_realized_pnl"] is not None:
                    values["conservative_daily_pnl"] = _sum_money((operands["session_realized_pnl"], min(values["unrealized_pnl"], Decimal(0))))
            except (DecimalException, _UnsupportedIdentity):
                arithmetic_failed = True
        if interpretable and operands["virtual_cash"] is not None and all(mark.net_liquidation_value is not None for mark in marks):
            try:
                values["computed_liquidation_equity"] = _sum_money((operands["virtual_cash"], *(mark.net_liquidation_value for mark in marks)))
            except (DecimalException, _UnsupportedIdentity):
                arithmetic_failed = True
        if values["computed_liquidation_equity"] is not None and complete_marks and values["unrealized_pnl"] is not None and operands["virtual_equity"] is not None:
            values["conservative_virtual_equity"] = min(operands["virtual_equity"], values["computed_liquidation_equity"])
        virtual = (operands["virtual_cash"], operands["virtual_settled_cash"], operands["virtual_reserved_cash"])
        if account.currency == "USD" and all(value is not None for value in virtual) and virtual[2] >= 0:
            try:
                values["virtual_unencumbered_cash"] = _sum_money((min(virtual[:2]), virtual[2].copy_negate()))
            except (DecimalException, _UnsupportedIdentity):
                arithmetic_failed = True
        broker = (operands["broker_settled_cash"], operands["broker_available_cash"], operands["broker_nonmargin_buying_power"])
        if interpretable and complete_marks and values["virtual_unencumbered_cash"] is not None and all(value is not None for value in broker):
            values["effective_available_cash"] = min(values["virtual_unencumbered_cash"], *broker)
        if arithmetic_failed:
            reasons.append("arithmetic_precision_unsupported")
    for name, value in dict(values, holding_marks=marks, capital_reasons=tuple(dict.fromkeys(reasons))).items():
        object.__setattr__(result, name, value)
