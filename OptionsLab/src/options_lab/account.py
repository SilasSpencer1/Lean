"""Immutable account claims and bounded currentness, never capital approval."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .config import StrategyConfig
from .contracts import ContractId
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
    authority. Capital and risk references remain on the actual snapshot.
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
