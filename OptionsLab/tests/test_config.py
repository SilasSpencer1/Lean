from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, Inexact, localcontext

import pytest

import options_lab.config as api
from options_lab.contracts import ContractId
from options_lab.observations import ObservationMeta
from options_lab.quotes import QuoteObservation


UTC = timezone.utc
DECISION = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def quote(**changes: object) -> QuoteObservation:
    contract = ContractId(
        "SPY", date(2026, 9, 18), "call", Decimal("650"), 100, "standard-spy-100"
    )
    meta = ObservationMeta(
        "sip",
        "quote-42",
        "raw://quote/42",
        "realtime",
        "genuine",
        "quote",
        DECISION,
        DECISION,
        DECISION,
        "measured",
        "capture://quote/42",
    )
    values = {
        "contract": contract,
        "meta": meta,
        "bid": Decimal("5.00"),
        "ask": Decimal("5.10"),
        "bid_at": DECISION,
        "ask_at": DECISION,
        "bid_size": 1,
        "ask_size": 1,
    }
    values.update(changes)
    return QuoteObservation(**values)


def assess(config: api.StrategyConfig, **changes: object):
    values = {
        "decision_at": DECISION,
        "virtual_equity": Decimal("104200"),
        "available_cash": Decimal("521"),
    }
    values.update(changes)
    return api.assess_configured_quote_budget(config, quote(), **values)


def test_defaults_are_exact_fixed_and_immutable() -> None:
    config = api.StrategyConfig()
    execution = config.execution

    assert config.initial_virtual_equity is None
    assert (
        config.underlying,
        config.paper_only,
        config.max_contracts,
        config.min_dte,
        config.max_dte,
    ) == ("SPY", True, 1, 7, 21)
    assert (
        config.min_abs_delta,
        config.max_abs_delta,
        config.max_spread_fraction,
        config.spread_floor,
    ) == (Decimal("0.40"), Decimal("0.60"), Decimal("0.08"), Decimal("0.05"))
    assert execution.holding_period == timedelta(minutes=30)
    assert execution.decision_cadence == timedelta(minutes=5)
    assert execution.base_entry_latency == timedelta(seconds=1)
    assert execution.adverse_exit_latency == timedelta(seconds=3)
    assert execution.adverse_return_floor == Decimal("0.005")
    assert execution.version == "spy-one-contract-execution-v1"
    assert config.config_schema_version == 1
    assert config.strategy_timezone == "America/New_York"
    assert config.strategy_calendar == "XNYS"
    with pytest.raises(FrozenInstanceError):
        execution.max_quote_age = timedelta(seconds=4)
    with pytest.raises(FrozenInstanceError):
        config.max_contracts = 2


def test_tighter_configuration_is_preserved_exactly() -> None:
    execution = api.ExecutionPolicy(
        entry_lifetime=timedelta(seconds=4),
        max_quote_age=timedelta(seconds=4),
        label_exit_lateness=timedelta(seconds=30),
        max_exit_replacements=1,
    )
    config = api.StrategyConfig(
        min_dte=10,
        max_dte=14,
        min_abs_delta=Decimal("0.45"),
        max_abs_delta=Decimal("0.55"),
        max_spread_fraction=Decimal("0.06"),
        spread_floor=Decimal("0.04"),
        premium_fraction=Decimal("0.004"),
        daily_loss_fraction=Decimal("0.005"),
        drawdown_fraction=Decimal("0.04"),
        max_entries_per_session=1,
        max_account_age=timedelta(seconds=4),
        max_reconciliation_age=timedelta(seconds=4),
        entry_start=time(10, 5),
        entry_end=time(14, 55),
        liquidation_start=time(15, 30),
        liquidation_deadline=time(15, 39),
        execution=execution,
    )

    assert config.execution is execution
    assert config.min_dte == 10 and config.max_dte == 14
    assert config.entry_start == time(10, 5) and config.entry_end == time(14, 55)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"entry_lifetime": 5}, TypeError),
        ({"entry_lifetime": timedelta(seconds=1)}, ValueError),
        ({"entry_lifetime": timedelta(seconds=5, microseconds=1)}, ValueError),
        ({"max_quote_age": True}, TypeError),
        ({"max_quote_age": timedelta(0)}, ValueError),
        ({"max_quote_age": timedelta(seconds=5, microseconds=1)}, ValueError),
        ({"label_exit_lateness": Decimal("1")}, TypeError),
        ({"label_exit_lateness": timedelta(0)}, ValueError),
        ({"label_exit_lateness": timedelta(seconds=60, microseconds=1)}, ValueError),
        ({"max_exit_replacements": False}, TypeError),
        ({"max_exit_replacements": -1}, ValueError),
        ({"max_exit_replacements": 4}, ValueError),
    ],
)
def test_execution_policy_rejects_wrong_types_and_looser_bounds(changes, error) -> None:
    with pytest.raises(error):
        api.ExecutionPolicy(**changes)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"underlying": 1}, TypeError),
        ({"underlying": "QQQ"}, ValueError),
        ({"paper_only": 1}, TypeError),
        ({"paper_only": False}, ValueError),
        ({"max_contracts": True}, TypeError),
        ({"max_contracts": 2}, ValueError),
        ({"min_dte": True}, TypeError),
        ({"min_dte": 6}, ValueError),
        ({"max_dte": 22}, ValueError),
        ({"min_dte": 14, "max_dte": 13}, ValueError),
        ({"min_abs_delta": Decimal("0.39")}, ValueError),
        ({"max_abs_delta": Decimal("0.61")}, ValueError),
        ({"min_abs_delta": Decimal("0.55"), "max_abs_delta": Decimal("0.54")}, ValueError),
        ({"max_spread_fraction": Decimal("0.081")}, ValueError),
        ({"spread_floor": Decimal("0.051")}, ValueError),
        ({"round_trip_fee_floor": Decimal("0.99")}, ValueError),
        ({"estimated_round_trip_cost": Decimal("0.99")}, ValueError),
        ({"premium_fraction": Decimal("0.0051")}, ValueError),
        ({"daily_loss_fraction": Decimal("0.0101")}, ValueError),
        ({"drawdown_fraction": Decimal("0.0501")}, ValueError),
        ({"max_entries_per_session": 0}, ValueError),
        ({"max_entries_per_session": 4}, ValueError),
        ({"max_account_age": timedelta(0)}, ValueError),
        ({"max_account_age": timedelta(seconds=5, microseconds=1)}, ValueError),
        ({"max_reconciliation_age": timedelta(0)}, ValueError),
        ({"execution": {}}, TypeError),
    ],
)
def test_strategy_config_rejects_looser_or_untyped_settings(changes, error) -> None:
    with pytest.raises(error):
        api.StrategyConfig(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"min_abs_delta": "0.40"},
        {"max_abs_delta": Decimal("NaN")},
        {"max_spread_fraction": Decimal("Infinity")},
        {"spread_floor": Decimal("0")},
        {"round_trip_fee_floor": 1},
        {"estimated_round_trip_cost": Decimal("-Infinity")},
        {"premium_fraction": Decimal("1e-1001")},
        {"daily_loss_fraction": False},
        {"drawdown_fraction": 0.05},
        {"initial_virtual_equity": Decimal("0")},
        {"initial_virtual_equity": Decimal("1e1000")},
    ],
)
def test_strategy_decimal_fields_require_exact_finite_bounded_values(changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        api.StrategyConfig(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"entry_start": datetime(2026, 9, 5, 10)},
        {"entry_start": time(10, tzinfo=UTC)},
        {"entry_start": time(10, fold=1)},
        {"entry_start": time(10, microsecond=1)},
        {"entry_start": time(9, 55)},
        {"entry_start": time(10, 2)},
        {"entry_end": time(15, 5)},
        {"entry_start": time(10, 5), "entry_end": time(10)},
        {"liquidation_start": time(15, 35, 1)},
        {"liquidation_deadline": time(15, 40, 1)},
        {"liquidation_start": time(15, 35), "liquidation_deadline": time(15, 35, 30)},
        {"entry_end": time(15), "liquidation_start": time(15, 30, 7)},
    ],
)
def test_strategy_config_rejects_invalid_clocks_or_infeasible_schedule(changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        api.StrategyConfig(**changes)


def test_single_slot_and_exact_planning_bound_are_valid() -> None:
    single = api.StrategyConfig(entry_start=time(10, 5), entry_end=time(10, 5))
    boundary = api.StrategyConfig(
        liquidation_start=time(15, 30, 5),
        execution=api.ExecutionPolicy(entry_lifetime=timedelta(seconds=2)),
    )

    assert single.entry_start == single.entry_end == time(10, 5)
    assert boundary.liquidation_start == time(15, 30, 5)


@pytest.mark.parametrize(
    ("factory", "field", "code"),
    [
        (lambda: api.ExecutionPolicy(entry_lifetime=timedelta(seconds=1)), "execution.entry_lifetime_us", "entry_lifetime_infeasible"),
        (lambda: api.StrategyConfig(min_dte=6), "min_dte", "range_invalid"),
        (lambda: api.StrategyConfig(entry_end=time(15), liquidation_start=time(15, 30, 7)), "liquidation_start", "schedule_infeasible"),
    ],
)
def test_constraint_failures_expose_fixed_safe_field_and_code(factory, field, code) -> None:
    with pytest.raises(api._ConfigConstraintError) as caught:
        factory()

    assert (caught.value.field, caught.value.code) == (field, code)


def test_decimal_context_does_not_change_typed_validation() -> None:
    with localcontext() as context:
        context.prec = 4
        context.traps[Inexact] = True
        config = api.StrategyConfig(
            round_trip_fee_floor=Decimal("1.000"),
            premium_fraction=Decimal("0.004"),
        )

    assert config.round_trip_fee_floor == Decimal("1")
    assert config.premium_fraction == Decimal("0.004")


@pytest.mark.parametrize(
    ("config", "applicable", "equity", "cash", "required"),
    [
        (api.StrategyConfig(), Decimal("0"), "104200", "521", "521.00"),
        (api.StrategyConfig(round_trip_fee_floor=Decimal("2")), Decimal("0"), "104400", "522", "522.00"),
        (api.StrategyConfig(estimated_round_trip_cost=Decimal("3")), Decimal("0"), "104600", "523", "523.00"),
        (api.StrategyConfig(), Decimal("4"), "104800", "524", "524.00"),
    ],
)
def test_configured_fee_inputs_change_actual_quote_budget(
    config, applicable, equity, cash, required
) -> None:
    result = assess(
        config,
        applicable_round_trip_fees=applicable,
        virtual_equity=Decimal(equity),
        available_cash=Decimal(cash),
    )

    assert result.budget is not None
    assert result.budget.required_cash == Decimal(required)
    assert result.budget.fees == max(
        Decimal("1"), config.round_trip_fee_floor, config.estimated_round_trip_cost, applicable
    )
    assert result.quote_budget_suitable is True


def test_tighter_premium_keeps_fixed_adverse_floor() -> None:
    result = api.assess_configured_quote_budget(
        api.StrategyConfig(premium_fraction=Decimal("0.004")),
        quote(bid=Decimal("5.099"), ask=Decimal("5.10")),
        decision_at=DECISION,
        virtual_equity=Decimal("130250"),
        available_cash=Decimal("521"),
    )

    assert result.budget is not None
    assert result.budget.adverse_reserve == Decimal("2.55000")
    assert result.budget.required_cash == Decimal("513.55000")
    assert result.quote_budget_suitable is True


def test_configured_quote_age_reaches_metadata_and_each_side_check() -> None:
    stale_at = DECISION - timedelta(seconds=4, microseconds=1)
    fresh = quote()
    observed = quote(
        meta=replace(fresh.meta, event_at=stale_at),
        bid_at=stale_at,
        ask_at=stale_at,
    )

    result = api.assess_configured_quote_budget(
        api.StrategyConfig(
            execution=api.ExecutionPolicy(max_quote_age=timedelta(seconds=4))
        ),
        observed,
        decision_at=DECISION,
        virtual_equity=Decimal("104200"),
        available_cash=Decimal("521"),
    )

    assert result.max_quote_age == timedelta(seconds=4)
    assert result.observation.live_quote_reasons == ("quote_too_old",)
    assert result.quote_reasons == ("bid_too_old", "ask_too_old")


@pytest.mark.parametrize(
    ("config", "observed"),
    [
        (
            api.StrategyConfig(max_spread_fraction=Decimal("0.06")),
            quote(bid=Decimal("0.93"), ask=Decimal("1.00")),
        ),
        (
            api.StrategyConfig(spread_floor=Decimal("0.04")),
            quote(bid=Decimal("0.04"), ask=Decimal("0.09")),
        ),
    ],
)
def test_configured_spread_limits_reach_quote_check(config, observed) -> None:
    result = api.assess_configured_quote_budget(
        config,
        observed,
        decision_at=DECISION,
        virtual_equity=Decimal("1000000"),
        available_cash=Decimal("1000000"),
    )

    assert result.max_spread_fraction == config.max_spread_fraction
    assert result.spread_floor == config.spread_floor
    assert result.quote_reasons == ("spread_too_wide",)


def test_declared_initial_capital_never_replaces_missing_runtime_capital() -> None:
    result = assess(
        api.StrategyConfig(initial_virtual_equity=Decimal("150000")),
        virtual_equity=None,
    )

    assert result.virtual_equity is None
    assert result.budget is not None
    assert result.budget.reason == "virtual equity is undeclared"


def test_extreme_applicable_fee_uses_quote_arithmetic_resource_evidence() -> None:
    result = assess(
        api.StrategyConfig(),
        applicable_round_trip_fees=Decimal("1e1000"),
        virtual_equity=Decimal("1e1001"),
        available_cash=Decimal("1e1001"),
    )

    assert result.quote_reasons == ("arithmetic_precision_unsupported",)
    assert result.budget is None


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"config": object()}, TypeError),
        ({"applicable_round_trip_fees": 0}, TypeError),
        ({"applicable_round_trip_fees": Decimal("NaN")}, ValueError),
        ({"applicable_round_trip_fees": Decimal("-0.01")}, ValueError),
    ],
)
def test_configured_wrapper_rejects_invalid_trusted_inputs(changes, error) -> None:
    config = changes.pop("config", api.StrategyConfig())
    with pytest.raises(error):
        assess(config, **changes)
