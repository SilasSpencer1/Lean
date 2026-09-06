"""Typed strategy configuration and configured quote-budget wiring."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import json

from .quotes import (
    QuoteObservation,
    QuotePremiumAssessment,
    assess_quote_premium_budget,
)

_MAX_DECIMAL_DIGITS = 1000
_FIVE_SECONDS = timedelta(seconds=5)
_ONE_MINUTE = timedelta(minutes=1)


class _ConfigConstraintError(ValueError):
    """This class represents one safe typed-configuration constraint failure."""

    def __init__(self, field: str, code: str, message: str) -> None:
        """
        Initialize one failure with stable owner-defined evidence.

        :param    field:   Fixed schema field associated with the failure.
        :param    code:    Fixed owner-defined failure code.
        :param    message: Descriptive trusted-constructor error message.
        :returns:          None.
        """
        super().__init__(message)
        self.field = field
        self.code = code


@dataclass(frozen=True)
class ExecutionPolicy:
    """This class represents bounded execution settings and fixed assumptions."""

    entry_lifetime: timedelta = _FIVE_SECONDS
    max_quote_age: timedelta = _FIVE_SECONDS
    label_exit_lateness: timedelta = timedelta(seconds=60)
    max_exit_replacements: int = 3
    version: str = field(default="spy-one-contract-execution-v1", init=False)
    holding_period: timedelta = field(default=timedelta(minutes=30), init=False)
    decision_cadence: timedelta = field(default=timedelta(minutes=5), init=False)
    base_entry_latency: timedelta = field(default=timedelta(seconds=1), init=False)
    base_exit_latency: timedelta = field(default=timedelta(seconds=1), init=False)
    adverse_entry_latency: timedelta = field(default=timedelta(seconds=3), init=False)
    adverse_exit_latency: timedelta = field(default=timedelta(seconds=3), init=False)
    severe_stall: timedelta = field(default=timedelta(seconds=10), init=False)
    exit_reconciliation_interval: timedelta = field(default=timedelta(seconds=2), init=False)
    adverse_return_floor: Decimal = field(default=Decimal("0.005"), init=False)
    severe_charge_multiplier: int = field(default=2, init=False)
    entry_limit_rule: str = field(default="original-decision-ask-cap-no-upward-reprice-v1", init=False)
    entry_expiry_rule: str = field(default="cancel-at-original-decision-expiry-v1", init=False)
    exit_rule: str = field(default="confirmed-long-bid-limit-acknowledged-replacement-v1", init=False)
    target_definition: str = field(default="attempt-net-pnl-over-original-ask-capital-v1", init=False)
    quote_diagnostic_definition: str = field(default="decision-plus-30m-first-quote-v1", init=False)

    def __post_init__(self) -> None:
        """
        Validate exact trusted types and preserve-or-tighten limits.

        :returns:             None.
        :raises   TypeError:  If a configurable field has the wrong exact type.
        :raises   ValueError: If a configurable field loosens the fixed policy.
        """
        for name in ("entry_lifetime", "max_quote_age", "label_exit_lateness"):
            _require_duration(name, getattr(self, name))
        _require_integer("max_exit_replacements", self.max_exit_replacements)
        if self.entry_lifetime <= self.base_entry_latency:
            _fail("execution.entry_lifetime_us", "entry_lifetime_infeasible", "entry_lifetime must exceed the one-second base entry latency")
        if self.entry_lifetime > _FIVE_SECONDS:
            _fail("execution.entry_lifetime_us", "range_invalid", "entry_lifetime cannot exceed five seconds")
        _require_positive_bounded_duration("execution.max_quote_age_us", "max_quote_age", self.max_quote_age, _FIVE_SECONDS)
        _require_positive_bounded_duration("execution.label_exit_lateness_us", "label_exit_lateness", self.label_exit_lateness, timedelta(seconds=60))
        if not 0 <= self.max_exit_replacements <= 3:
            _fail("execution.max_exit_replacements", "range_invalid", "max_exit_replacements must be between zero and three")


@dataclass(frozen=True)
class StrategyConfig:
    """This class represents trusted SPY strategy and risk configuration."""

    underlying: str = "SPY"
    paper_only: bool = True
    max_contracts: int = 1
    min_dte: int = 7
    max_dte: int = 21
    min_abs_delta: Decimal = Decimal("0.40")
    max_abs_delta: Decimal = Decimal("0.60")
    max_spread_fraction: Decimal = Decimal("0.08")
    spread_floor: Decimal = Decimal("0.05")
    round_trip_fee_floor: Decimal = Decimal("1")
    estimated_round_trip_cost: Decimal = Decimal("1")
    premium_fraction: Decimal = Decimal("0.005")
    daily_loss_fraction: Decimal = Decimal("0.01")
    drawdown_fraction: Decimal = Decimal("0.05")
    max_entries_per_session: int = 3
    initial_virtual_equity: Decimal | None = None
    max_account_age: timedelta = _FIVE_SECONDS
    max_reconciliation_age: timedelta = _FIVE_SECONDS
    entry_start: time = time(10)
    entry_end: time = time(15)
    liquidation_start: time = time(15, 35)
    liquidation_deadline: time = time(15, 40)
    execution: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    config_schema_version: int = field(default=1, init=False)
    policy_definition_version: str = field(default="spy-intraday-long-options-v1", init=False)
    strategy_timezone: str = field(default="America/New_York", init=False)
    strategy_calendar: str = field(default="XNYS", init=False)
    contract_rule: str = field(default="standard-unadjusted-100-spy-shares-v1", init=False)
    selection_rule: str = field(default="abs-delta-distance-spread-expiry-contract-v1", init=False)
    feature_definition: str = field(default="spy-completed-minute-minimal-v2", init=False)

    def __post_init__(self) -> None:
        """
        Validate exact trusted types, tighter bounds, and schedule feasibility.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact trusted type.
        :raises   ValueError: If a field loosens policy or the schedule cannot fit.
        """
        _require_exact_string("underlying", self.underlying)
        if self.underlying != "SPY":
            _fail("underlying", "fixed_value_required", "underlying must be SPY")
        if type(self.paper_only) is not bool:
            raise TypeError("paper_only must be a boolean")
        if not self.paper_only:
            _fail("paper_only", "fixed_value_required", "paper_only must be true")
        for name in ("max_contracts", "min_dte", "max_dte", "max_entries_per_session"):
            _require_integer(name, getattr(self, name))
        if self.max_contracts != 1:
            _fail("max_contracts", "fixed_value_required", "max_contracts must be exactly one")
        if not 7 <= self.min_dte <= self.max_dte <= 21:
            _fail("min_dte", "range_invalid", "DTE bounds must satisfy 7 <= min_dte <= max_dte <= 21")
        if not 1 <= self.max_entries_per_session <= 3:
            _fail("max_entries_per_session", "range_invalid", "max_entries_per_session must be between one and three")

        decimal_fields = (
            "min_abs_delta", "max_abs_delta", "max_spread_fraction", "spread_floor",
            "round_trip_fee_floor", "estimated_round_trip_cost", "premium_fraction",
            "daily_loss_fraction", "drawdown_fraction",
        )
        for name in decimal_fields:
            _require_config_decimal(name, getattr(self, name))
        if self.initial_virtual_equity is not None:
            _require_config_decimal("initial_virtual_equity", self.initial_virtual_equity, nullable=True)
            if self.initial_virtual_equity <= 0:
                _fail("initial_virtual_equity", "range_invalid", "initial_virtual_equity must be positive when supplied")
        if not Decimal("0.40") <= self.min_abs_delta <= self.max_abs_delta <= Decimal("0.60"):
            _fail("min_abs_delta", "range_invalid", "delta bounds must satisfy 0.40 <= min_abs_delta <= max_abs_delta <= 0.60")
        _require_positive_decimal_limit("max_spread_fraction", self.max_spread_fraction, Decimal("0.08"))
        _require_positive_decimal_limit("spread_floor", self.spread_floor, Decimal("0.05"))
        for name in ("round_trip_fee_floor", "estimated_round_trip_cost"):
            if getattr(self, name) < 1:
                _fail(name, "range_invalid", f"{name} must be at least one dollar")
        for name, maximum in (
            ("premium_fraction", Decimal("0.005")),
            ("daily_loss_fraction", Decimal("0.01")),
            ("drawdown_fraction", Decimal("0.05")),
        ):
            _require_positive_decimal_limit(name, getattr(self, name), maximum)

        for name in ("max_account_age", "max_reconciliation_age"):
            _require_duration(name, getattr(self, name))
            _require_positive_bounded_duration(name, name, getattr(self, name), _FIVE_SECONDS)
        for name in ("entry_start", "entry_end", "liquidation_start", "liquidation_deadline"):
            _require_local_time(name, getattr(self, name))
        if type(self.execution) is not ExecutionPolicy:
            raise TypeError("execution must be an ExecutionPolicy")
        self._validate_schedule()

    def _validate_schedule(self) -> None:
        """
        Enforce local clock ordering, cadence, and conservative fit.

        :returns:             None.
        :raises   ValueError: If the complete configured schedule is infeasible.
        """
        start = _time_microseconds(self.entry_start)
        end = _time_microseconds(self.entry_end)
        liquidation_start = _time_microseconds(self.liquidation_start)
        liquidation_deadline = _time_microseconds(self.liquidation_deadline)
        if not _time_microseconds(time(10)) <= start <= end <= _time_microseconds(time(15)):
            _fail("entry_end", "window_invalid", "entry window must satisfy 10:00 <= start <= end <= 15:00")
        if any(value.second or value.minute % 5 for value in (self.entry_start, self.entry_end)):
            _fail("entry_end", "window_invalid", "entry clocks must align to the absolute five-minute grid")
        if self.liquidation_start > time(15, 35):
            _fail("liquidation_start", "window_invalid", "liquidation_start cannot be later than 15:35")
        if self.liquidation_deadline > time(15, 40):
            _fail("liquidation_deadline", "window_invalid", "liquidation_deadline cannot be later than 15:40")
        if not end < liquidation_start < liquidation_deadline:
            _fail("liquidation_start", "schedule_infeasible", "entry end, liquidation start, and deadline must be strictly ordered")
        if liquidation_start > liquidation_deadline - _duration_microseconds(_ONE_MINUTE):
            _fail("liquidation_start", "schedule_infeasible", "liquidation must begin at least one minute before the deadline")
        required_end = (
            end
            + _duration_microseconds(self.execution.entry_lifetime)
            + _duration_microseconds(self.execution.holding_period)
            + _duration_microseconds(self.execution.adverse_exit_latency)
        )
        if required_end > liquidation_start:
            _fail("liquidation_start", "schedule_infeasible", "latest normal-path exit cannot fit before liquidation")


def assess_configured_quote_budget(
    config: StrategyConfig,
    quote: QuoteObservation,
    *,
    decision_at: datetime,
    virtual_equity: Decimal | None,
    available_cash: Decimal | None,
    applicable_round_trip_fees: Decimal = Decimal("0"),
) -> QuotePremiumAssessment:
    """
    Assess a quote using configured fee, premium, freshness, and spread limits.

    Explicit runtime capital is forwarded unchanged. Declared initial capital is
    retained configuration and never substitutes for a missing runtime fact.

    :param    config:                      Exact trusted strategy configuration.
    :param    quote:                       Typed option quote evidence.
    :param    decision_at:                 Decision timestamp used by quote checks.
    :param    virtual_equity:              Runtime virtual capital, or None.
    :param    available_cash:              Runtime available cash, or None.
    :param    applicable_round_trip_fees:  Current nonnegative fee estimate.
    :returns:                              Bounded quote and premium evidence.
    :raises   TypeError:                   If a trusted input has the wrong exact type.
    :raises   ValueError:                  If a trusted input value is invalid.
    """
    if type(config) is not StrategyConfig:
        raise TypeError("config must be a StrategyConfig")
    _require_exact_decimal("applicable_round_trip_fees", applicable_round_trip_fees)
    if applicable_round_trip_fees < 0:
        raise ValueError("applicable_round_trip_fees cannot be negative")
    effective_fees = max(
        Decimal("1"), config.round_trip_fee_floor, config.estimated_round_trip_cost,
        applicable_round_trip_fees,
    )
    return assess_quote_premium_budget(
        quote,
        decision_at=decision_at,
        virtual_equity=virtual_equity,
        available_cash=available_cash,
        round_trip_fees=effective_fees,
        premium_fraction=config.premium_fraction,
        max_quote_age=config.execution.max_quote_age,
        max_spread_fraction=config.max_spread_fraction,
        spread_floor=config.spread_floor,
    )


def _fail(field: str, code: str, message: str) -> None:
    """Raise one safe typed-configuration constraint failure."""
    raise _ConfigConstraintError(field, code, message)


def _require_exact_string(name: str, value: object) -> None:
    """Require an exact trusted string."""
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")


def _require_integer(name: str, value: object) -> None:
    """Require an exact trusted integer that excludes booleans."""
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")


def _require_duration(name: str, value: object) -> None:
    """Require an exact trusted timedelta."""
    if type(value) is not timedelta:
        raise TypeError(f"{name} must be a timedelta")


def _require_positive_bounded_duration(
    field_name: str, display_name: str, value: timedelta, maximum: timedelta
) -> None:
    """Require a positive duration that does not loosen its fixed maximum."""
    if not timedelta(0) < value <= maximum:
        _fail(field_name, "range_invalid", f"{display_name} must be positive and at most {maximum}")


def _require_config_decimal(name: str, value: object, *, nullable: bool = False) -> None:
    """Require an exact finite Decimal with bounded fixed-point representation."""
    if type(value) is not Decimal:
        suffix = " or None" if nullable else ""
        raise TypeError(f"{name} must be a Decimal{suffix}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value and _fixed_point_digit_span(value) > _MAX_DECIMAL_DIGITS:
        _fail(name, "decimal_representation_unsupported", f"{name} fixed-point representation exceeds 1000 digits")


def _require_exact_decimal(name: str, value: object) -> None:
    """Require an exact finite runtime Decimal without changing its precision."""
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _fixed_point_digit_span(value: Decimal) -> int:
    """Return the digit span needed by a nonzero fixed-point representation."""
    digits = len(value.as_tuple().digits)
    exponent = value.as_tuple().exponent
    return digits + exponent if exponent >= 0 else max(digits, -exponent)


def _require_positive_decimal_limit(name: str, value: Decimal, maximum: Decimal) -> None:
    """Require a positive Decimal that does not exceed its fixed maximum."""
    if value <= 0 or value > maximum:
        _fail(name, "range_invalid", f"{name} must be positive and at most {maximum}")


def _require_local_time(name: str, value: object) -> None:
    """Require one exact offset-free, fold-zero, whole-second local clock."""
    if type(value) is not time:
        raise TypeError(f"{name} must be a time")
    if value.tzinfo is not None or value.fold != 0 or value.microsecond != 0:
        raise ValueError(f"{name} must be offset-free, fold zero, and use whole seconds")


def _duration_microseconds(value: timedelta) -> int:
    """Return exact integer microseconds without floating-point conversion."""
    return ((value.days * 86400) + value.seconds) * 1_000_000 + value.microseconds


def _time_microseconds(value: time) -> int:
    """Return exact integer microseconds since local midnight."""
    return ((value.hour * 60 + value.minute) * 60 + value.second) * 1_000_000 + value.microsecond


def config_snapshot(config: StrategyConfig) -> dict[str, object]:
    """
    Build the complete canonical configuration snapshot.

    :param    config:     Exact trusted strategy configuration.
    :returns:            Fresh canonical configuration snapshot.
    :raises   TypeError: If config is not an exact StrategyConfig.
    """
    _require_strategy_config(config)
    return {
        "record_kind": "options_lab.config",
        "config_snapshot_schema_version": 1,
        "config_schema_version": config.config_schema_version,
        "paper_only": config.paper_only,
        "policy": _policy_snapshot(config),
        "risk": {
            "initial_virtual_equity": (
                None
                if config.initial_virtual_equity is None
                else _decimal_string(config.initial_virtual_equity)
            ),
            "premium_fraction": _decimal_string(config.premium_fraction),
            "daily_loss_fraction": _decimal_string(config.daily_loss_fraction),
            "drawdown_fraction": _decimal_string(config.drawdown_fraction),
            "max_entries_per_session": config.max_entries_per_session,
        },
        "account": {
            "max_account_age": _duration_snapshot(config.max_account_age),
            "max_reconciliation_age": _duration_snapshot(
                config.max_reconciliation_age
            ),
        },
    }


def config_hash(config: StrategyConfig) -> str:
    """
    Return the SHA-256 identity of the canonical configuration snapshot.

    :param    config:     Exact trusted strategy configuration.
    :returns:            Lowercase hexadecimal SHA-256 configuration identity.
    :raises   TypeError: If config is not an exact StrategyConfig.
    """
    return _snapshot_hash(config_snapshot(config))


def policy_hash(config: StrategyConfig) -> str:
    """
    Return the SHA-256 identity of the canonical policy snapshot.

    :param    config:     Exact trusted strategy configuration.
    :returns:            Lowercase hexadecimal SHA-256 policy identity.
    :raises   TypeError: If config is not an exact StrategyConfig.
    """
    _require_strategy_config(config)
    return _snapshot_hash(_policy_snapshot(config))


def _policy_snapshot(config: StrategyConfig) -> dict[str, object]:
    """Build a fresh explicit snapshot of policy-affecting semantics."""
    execution = config.execution
    return {
        "record_kind": "options_lab.policy",
        "policy_snapshot_schema_version": 1,
        "config_snapshot_schema_version": 1,
        "config_schema_version": config.config_schema_version,
        "policy_definition_version": config.policy_definition_version,
        "scope": {
            "underlying": config.underlying,
            "paper_only": config.paper_only,
            "max_contracts": config.max_contracts,
            "contract_rule": config.contract_rule,
            "strategy_calendar": config.strategy_calendar,
            "strategy_timezone": config.strategy_timezone,
        },
        "semantics": {
            "target_definition": execution.target_definition,
            "quote_diagnostic_definition": execution.quote_diagnostic_definition,
            "selection_rule": config.selection_rule,
            "feature_definition": config.feature_definition,
        },
        "selection": {
            "min_dte": config.min_dte,
            "max_dte": config.max_dte,
            "min_abs_delta": _decimal_string(config.min_abs_delta),
            "max_abs_delta": _decimal_string(config.max_abs_delta),
        },
        "quote_costs": {
            "max_spread_fraction": _decimal_string(config.max_spread_fraction),
            "spread_floor": _decimal_string(config.spread_floor),
            "round_trip_fee_floor": _decimal_string(config.round_trip_fee_floor),
            "estimated_round_trip_cost": _decimal_string(
                config.estimated_round_trip_cost
            ),
        },
        "session": {
            "entry_start": config.entry_start.isoformat(),
            "entry_end": config.entry_end.isoformat(),
            "liquidation_start": config.liquidation_start.isoformat(),
            "liquidation_deadline": config.liquidation_deadline.isoformat(),
            "clock_timezone": config.strategy_timezone,
            "early_close_liquidation_start_formula": (
                "min(configured_liquidation_start,common_close-25minutes)"
            ),
            "early_close_liquidation_deadline_formula": (
                "min(configured_liquidation_deadline,common_close-20minutes)"
            ),
            "liquidation_escalation_formula": (
                "effective_liquidation_deadline-1minute"
            ),
            "normal_path_fit_formula": (
                "entry_end+entry_lifetime+holding_period+adverse_exit_latency"
                "<=liquidation_start"
            ),
        },
        "execution": {
            "version": execution.version,
            "entry_lifetime": _duration_snapshot(execution.entry_lifetime),
            "max_quote_age": _duration_snapshot(execution.max_quote_age),
            "label_exit_lateness": _duration_snapshot(
                execution.label_exit_lateness
            ),
            "max_exit_replacements": execution.max_exit_replacements,
            "holding_period": _duration_snapshot(execution.holding_period),
            "decision_cadence": _duration_snapshot(execution.decision_cadence),
            "base_entry_latency": _duration_snapshot(
                execution.base_entry_latency
            ),
            "base_exit_latency": _duration_snapshot(execution.base_exit_latency),
            "adverse_entry_latency": _duration_snapshot(
                execution.adverse_entry_latency
            ),
            "adverse_exit_latency": _duration_snapshot(
                execution.adverse_exit_latency
            ),
            "severe_stall": _duration_snapshot(execution.severe_stall),
            "exit_reconciliation_interval": _duration_snapshot(
                execution.exit_reconciliation_interval
            ),
            "adverse_return_floor": _decimal_string(
                execution.adverse_return_floor
            ),
            "severe_charge_multiplier": execution.severe_charge_multiplier,
            "entry_limit_rule": execution.entry_limit_rule,
            "entry_expiry_rule": execution.entry_expiry_rule,
            "exit_rule": execution.exit_rule,
            "entry_expiry_equality": "cancel",
            "upward_repricing": "forbidden",
            "exit_replacement_requires_acknowledgement": True,
            "causal_adverse_cost_formula": (
                "max(0.005*original_ask_capital,100*decision_spread)"
            ),
            "ex_post_adverse_cost_formula": (
                "max(0.005*original_ask_capital,"
                "100*0.5*(actual_entry_spread+actual_exit_spread))"
            ),
            "severe_adverse_cost_formula": "2*ex_post_adverse_cost",
        },
    }


def _require_strategy_config(config: object) -> None:
    """Require an exact trusted StrategyConfig for identity generation."""
    if type(config) is not StrategyConfig:
        raise TypeError("config must be a StrategyConfig")


def _decimal_string(value: Decimal) -> str:
    """Return a context-independent normalized fixed-point decimal string."""
    if not value:
        return "0"
    sign, digits, exponent = value.as_tuple()
    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        rendered = coefficient + ("0" * exponent)
    else:
        split = len(coefficient) + exponent
        if split > 0:
            rendered = coefficient[:split] + "." + coefficient[split:]
        else:
            rendered = "0." + ("0" * -split) + coefficient
        rendered = rendered.rstrip("0").rstrip(".")
    return ("-" if sign else "") + rendered


def _duration_snapshot(value: timedelta) -> dict[str, object]:
    """Return a fresh exact integer-microsecond duration snapshot."""
    return {"value": _duration_microseconds(value), "unit": "microseconds"}


def _snapshot_hash(snapshot: dict[str, object]) -> str:
    """Hash one owned snapshot through compact sorted ASCII JSON."""
    payload = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
