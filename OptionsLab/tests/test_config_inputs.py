from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from pathlib import Path

import pytest

import options_lab.config_inputs as api
from options_lab.config import ExecutionPolicy, StrategyConfig, config_hash, policy_hash


UTC = timezone.utc
RECEIVED = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)


def normalize(raw: object, **envelope: object) -> api.ConfigValidation:
    values = {
        "raw_ref": "raw://config/1",
        "event_id": "event-1",
        "received_at": RECEIVED,
    }
    values.update(envelope)
    return api.normalize_config(raw, **values)


def assert_failure(raw: object, field: str, code: str) -> api.ConfigInputRejection:
    result = normalize(raw)
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (field, code)
    return result.rejection


def explicit_defaults() -> dict[str, object]:
    return {
        "underlying": "SPY",
        "paper_only": True,
        "max_contracts": 1,
        "min_dte": 7,
        "max_dte": 21,
        "min_abs_delta": "0.400",
        "max_abs_delta": "0.60",
        "max_spread_fraction": "0.080",
        "spread_floor": "0.050",
        "round_trip_fee_floor": "1.00",
        "estimated_round_trip_cost": "1",
        "premium_fraction": "0.0050",
        "daily_loss_fraction": "0.010",
        "drawdown_fraction": "0.050",
        "max_entries_per_session": 3,
        "max_account_age_us": 5_000_000,
        "max_reconciliation_age_us": 5_000_000,
        "entry_start": "10:00",
        "entry_end": "15:00:00",
        "liquidation_start": "15:35",
        "liquidation_deadline": "15:40:00",
        "execution": {
            "entry_lifetime_us": 5_000_000,
            "max_quote_age_us": 5_000_000,
            "label_exit_lateness_us": 60_000_000,
            "max_exit_replacements": 3,
            "holding_period_us": 1_800_000_000,
            "decision_cadence_us": 300_000_000,
        },
    }


def test_empty_and_explicit_defaults_have_one_exact_immutable_value() -> None:
    empty = normalize({})
    explicit = normalize(explicit_defaults())

    assert type(empty) is api.ConfigValidation
    assert empty.rejection is None and type(empty.value) is StrategyConfig
    assert explicit.rejection is None and explicit.value == empty.value
    assert empty.value.initial_virtual_equity is None
    assert config_hash(explicit.value) == config_hash(empty.value)
    assert policy_hash(explicit.value) == policy_hash(empty.value)
    with pytest.raises(FrozenInstanceError):
        empty.value.execution.max_quote_age = timedelta(seconds=4)


def test_omitted_and_empty_execution_tables_are_equivalent() -> None:
    omitted = normalize({}).value
    empty = normalize({"execution": {}}).value
    assert omitted == empty == StrategyConfig()


class DictSubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


@pytest.mark.parametrize("raw", [None, [], "config", object(), DictSubclass()])
def test_root_requires_an_exact_dictionary(raw: object) -> None:
    assert_failure(raw, "$", "expected_exact_dict")


@pytest.mark.parametrize(
    ("raw", "field", "code"),
    [
        ({"underlying": 1}, "underlying", "invalid_type"),
        ({"underlying": "QQQ"}, "underlying", "fixed_value_required"),
        ({"paper_only": 1}, "paper_only", "invalid_type"),
        ({"paper_only": False}, "paper_only", "fixed_value_required"),
        ({"max_contracts": True}, "max_contracts", "invalid_type"),
        ({"max_contracts": 2}, "max_contracts", "fixed_value_required"),
        ({"min_dte": 6}, "min_dte", "range_invalid"),
        ({"max_dte": 22}, "min_dte", "range_invalid"),
        ({"min_dte": 14, "max_dte": 13}, "min_dte", "range_invalid"),
        ({"min_abs_delta": "0.39"}, "min_abs_delta", "range_invalid"),
        ({"max_abs_delta": "0.61"}, "min_abs_delta", "range_invalid"),
        ({"min_abs_delta": "0.55", "max_abs_delta": "0.54"}, "min_abs_delta", "range_invalid"),
        ({"max_spread_fraction": "0.081"}, "max_spread_fraction", "range_invalid"),
        ({"spread_floor": "0.051"}, "spread_floor", "range_invalid"),
        ({"round_trip_fee_floor": "0.99"}, "round_trip_fee_floor", "range_invalid"),
        ({"estimated_round_trip_cost": "0.99"}, "estimated_round_trip_cost", "range_invalid"),
        ({"premium_fraction": "0.0051"}, "premium_fraction", "range_invalid"),
        ({"daily_loss_fraction": "0.0101"}, "daily_loss_fraction", "range_invalid"),
        ({"drawdown_fraction": "0.0501"}, "drawdown_fraction", "range_invalid"),
        ({"max_entries_per_session": 0}, "max_entries_per_session", "range_invalid"),
        ({"max_entries_per_session": 4}, "max_entries_per_session", "range_invalid"),
        ({"initial_virtual_equity": None}, "initial_virtual_equity", "invalid_type"),
        ({"initial_virtual_equity": "0"}, "initial_virtual_equity", "range_invalid"),
        ({"max_account_age_us": 0}, "max_account_age_us", "range_invalid"),
        ({"max_account_age_us": 5_000_001}, "max_account_age_us", "range_invalid"),
        ({"max_reconciliation_age_us": 0}, "max_reconciliation_age_us", "range_invalid"),
        ({"entry_start": "10:02"}, "entry_end", "window_invalid"),
        ({"entry_end": "15:05"}, "entry_end", "window_invalid"),
        ({"entry_start": "10:05", "entry_end": "10:00"}, "entry_end", "window_invalid"),
        ({"liquidation_start": "15:35:01"}, "liquidation_start", "window_invalid"),
        ({"liquidation_deadline": "15:40:01"}, "liquidation_deadline", "window_invalid"),
        ({"liquidation_start": "15:35", "liquidation_deadline": "15:35:30"}, "liquidation_start", "schedule_infeasible"),
        ({"entry_end": "15:00", "liquidation_start": "15:30:07"}, "liquidation_start", "schedule_infeasible"),
        ({"execution": []}, "execution", "expected_exact_dict"),
        ({"execution": DictSubclass()}, "execution", "expected_exact_dict"),
        ({"execution": {"entry_lifetime_us": 1_000_000}}, "execution.entry_lifetime_us", "entry_lifetime_infeasible"),
        ({"execution": {"entry_lifetime_us": 5_000_001}}, "execution.entry_lifetime_us", "range_invalid"),
        ({"execution": {"max_quote_age_us": 0}}, "execution.max_quote_age_us", "range_invalid"),
        ({"execution": {"max_quote_age_us": 5_000_001}}, "execution.max_quote_age_us", "range_invalid"),
        ({"execution": {"label_exit_lateness_us": 0}}, "execution.label_exit_lateness_us", "range_invalid"),
        ({"execution": {"label_exit_lateness_us": 60_000_001}}, "execution.label_exit_lateness_us", "range_invalid"),
        ({"execution": {"max_exit_replacements": -1}}, "execution.max_exit_replacements", "range_invalid"),
        ({"execution": {"max_exit_replacements": 4}}, "execution.max_exit_replacements", "range_invalid"),
        ({"execution": {"holding_period_us": 1_799_999_999}}, "execution.holding_period_us", "fixed_value_required"),
        ({"execution": {"decision_cadence_us": 300_000_001}}, "execution.decision_cadence_us", "fixed_value_required"),
    ],
)
def test_well_typed_looser_or_infeasible_values_have_owned_codes(
    raw: object, field: str, code: str
) -> None:
    assert_failure(raw, field, code)


@pytest.mark.parametrize(
    ("raw", "field", "code"),
    [
        ({"min_dte": False}, "min_dte", "invalid_type"),
        ({"max_entries_per_session": IntSubclass(1)}, "max_entries_per_session", "invalid_type"),
        ({"max_account_age_us": True}, "max_account_age_us", "invalid_type"),
        ({"entry_start": datetime(2026, 9, 5, 10)}, "entry_start", "invalid_type"),
        ({"entry_start": " 10:00"}, "entry_start", "invalid_time"),
        ({"entry_start": "10:00:00.000000"}, "entry_start", "invalid_time"),
        ({"entry_start": "10:00Z"}, "entry_start", "invalid_time"),
        ({"execution": {"max_exit_replacements": False}}, "execution.max_exit_replacements", "invalid_type"),
        ({"execution": {"holding_period_us": True}}, "execution.holding_period_us", "invalid_type"),
    ],
)
def test_scalar_boundary_rejects_subclasses_bools_and_native_times(
    raw: object, field: str, code: str
) -> None:
    assert_failure(raw, field, code)


@pytest.mark.parametrize(
    "value", [Decimal("1"), 1, 1.0, True, StringSubclass("1"), "NaN", "Infinity", "1e2", " 1", "1_000"]
)
def test_decimal_inputs_are_exact_ascii_fixed_point_strings(value: object) -> None:
    code = "invalid_decimal" if type(value) is str else "invalid_type"
    assert_failure({"round_trip_fee_floor": value}, "round_trip_fee_floor", code)


@pytest.mark.parametrize(
    "field",
    (
        "min_abs_delta", "max_abs_delta", "max_spread_fraction", "spread_floor",
        "round_trip_fee_floor", "estimated_round_trip_cost", "premium_fraction",
        "daily_loss_fraction", "drawdown_fraction", "initial_virtual_equity",
    ),
)
def test_every_decimal_field_rejects_native_decimal_objects(field: str) -> None:
    assert_failure({field: Decimal("1")}, field, "invalid_type")


@pytest.mark.parametrize(
    "field", ("max_contracts", "min_dte", "max_dte", "max_entries_per_session")
)
def test_every_integer_field_rejects_boolean_values(field: str) -> None:
    assert_failure({field: True}, field, "invalid_type")


@pytest.mark.parametrize(
    "field", ("entry_start", "entry_end", "liquidation_start", "liquidation_deadline")
)
def test_every_clock_field_rejects_native_toml_times(field: str) -> None:
    assert_failure({field: datetime(2026, 9, 5, 10).time()}, field, "invalid_type")


def test_valid_tightening_and_exact_duration_are_preserved() -> None:
    result = normalize(
        {
            "min_dte": 10,
            "max_dte": 14,
            "min_abs_delta": "0.45",
            "max_abs_delta": "0.55",
            "max_spread_fraction": "0.06",
            "spread_floor": "0.04",
            "premium_fraction": "0.004",
            "daily_loss_fraction": "0.005",
            "drawdown_fraction": "0.04",
            "max_entries_per_session": 1,
            "max_account_age_us": 4_000_000,
            "max_reconciliation_age_us": 4_000_000,
            "entry_start": "10:05",
            "entry_end": "14:55",
            "liquidation_start": "15:30",
            "liquidation_deadline": "15:39",
            "execution": {
                "entry_lifetime_us": 1_500_000,
                "max_quote_age_us": 4_000_000,
                "label_exit_lateness_us": 30_000_000,
                "max_exit_replacements": 0,
            },
        }
    )

    assert result.rejection is None and result.value is not None
    assert result.value.execution.entry_lifetime == timedelta(microseconds=1_500_000)
    assert result.value.execution.max_exit_replacements == 0
    assert result.value.entry_start.isoformat() == "10:05:00"


def test_exact_planning_boundary_and_single_slot_are_accepted() -> None:
    result = normalize(
        {
            "entry_start": "15:00",
            "entry_end": "15:00",
            "liquidation_start": "15:30:05",
            "execution": {"entry_lifetime_us": 2_000_000},
        }
    )

    assert result.rejection is None and result.value is not None
    assert result.value.entry_start == result.value.entry_end


def test_huge_duration_is_rejected_before_timedelta_construction() -> None:
    assert_failure(
        {"execution": {"max_quote_age_us": 10**100_000}},
        "execution.max_quote_age_us",
        "range_invalid",
    )


def test_extreme_decimal_and_hostile_context_return_bounded_results() -> None:
    huge = "0." + ("0" * 1000) + "1"
    with localcontext() as context:
        context.prec = 4
        context.flags[Inexact] = True
        context.traps[InvalidOperation] = True
        result = normalize({"initial_virtual_equity": huge})

    assert result.value is None and result.rejection is not None
    assert result.rejection.code == "decimal_representation_unsupported"


class CollidingKey:
    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def __hash__(self) -> int:
        return hash("execution")

    def __eq__(self, other: object) -> bool:
        self.callbacks.append("eq")
        raise AssertionError("must not compare hostile keys")

    def __str__(self) -> str:
        self.callbacks.append("str")
        raise AssertionError("must not stringify hostile keys")

    def __repr__(self) -> str:
        self.callbacks.append("repr")
        raise AssertionError("must not repr hostile keys")


def test_unknown_root_and_nested_keys_precede_scalars_without_callbacks() -> None:
    root_key = CollidingKey()
    nested_key = CollidingKey()
    assert_failure({"min_dte": "bad", root_key: None}, "$", "unknown_fields")
    assert_failure(
        {"min_dte": "bad", "execution": {nested_key: None}},
        "execution",
        "unknown_fields",
    )
    assert root_key.callbacks == nested_key.callbacks == []


def test_scalar_failures_follow_declaration_order_not_input_order() -> None:
    assert_failure(
        {"max_entries_per_session": "bad", "min_abs_delta": 1},
        "min_abs_delta",
        "invalid_type",
    )
    assert_failure(
        {"entry_start": 1, "max_account_age_us": "bad"},
        "max_account_age_us",
        "invalid_type",
    )


def test_source_mutation_cannot_change_normalized_configuration() -> None:
    execution = {"entry_lifetime_us": 2_000_000}
    raw = {"initial_virtual_equity": "150000", "execution": execution}
    config = normalize(raw).value
    raw["initial_virtual_equity"] = "1"
    execution["entry_lifetime_us"] = 5_000_000

    assert config is not None
    assert config.initial_virtual_equity == Decimal("150000")
    assert config.execution.entry_lifetime == timedelta(seconds=2)


def test_trusted_envelope_is_validated_before_raw_callbacks() -> None:
    key = CollidingKey()
    with pytest.raises(ValueError, match="event_id"):
        normalize({key: None}, event_id="")
    assert key.callbacks == []


def test_rejection_is_safe_normalized_and_exactly_one_outcome() -> None:
    received = datetime(2026, 9, 5, 10, 30, tzinfo=timezone(timedelta(hours=-4)))
    result = normalize({"secret_token": "classified"}, received_at=received)

    assert result.rejection is not None
    assert result.rejection.received_at == RECEIVED
    assert result.rejection.stage == "config_normalization"
    assert result.rejection.reasons == ("unknown_fields",)
    assert "secret_token" not in repr(result.rejection)
    assert "classified" not in repr(result.rejection)
    with pytest.raises(ValueError):
        api.ConfigValidation()
    with pytest.raises(TypeError):
        api.ConfigValidation(value=object())


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("entry_start", "schedule_infeasible"),
        ("max_dte", "range_invalid"),
        ("max_abs_delta", "range_invalid"),
        ("unknown", "invalid_type"),
    ],
)
def test_rejection_rejects_impossible_field_code_pairs(field: str, code: str) -> None:
    with pytest.raises(ValueError, match="incompatible"):
        api.ConfigInputRejection("event", RECEIVED, "raw", field, code)


@pytest.mark.parametrize(
    ("contents", "code"),
    [
        (b"\xff", "invalid_utf8"),
        (b"entry_start = 'bad'\n[", "invalid_toml"),
        (b"max_contracts = 1\nmax_contracts = 1\n", "invalid_toml"),
    ],
)
def test_load_config_returns_safe_file_rejections(tmp_path: Path, contents: bytes, code: str) -> None:
    path = tmp_path / "contains-secret-name.toml"
    path.write_bytes(contents)

    result = api.load_config(
        path,
        raw_ref="raw://config/file-1",
        event_id="event-1",
        received_at=RECEIVED,
    )

    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == ("$", code)
    assert result.rejection.stage == "config_load"
    assert str(path) not in repr(result.rejection)


@pytest.mark.parametrize(
    "contents",
    [
        "max_account_age_us = " + ("9" * 5000),
        "max_contracts = " + ("[" * 1100) + "1" + ("]" * 1100),
    ],
    ids=("giant_integer", "deep_array"),
)
def test_toml_parser_resource_failures_return_bounded_rejection(
    tmp_path: Path, contents: str
) -> None:
    path = tmp_path / "sensitive-name.toml"
    path.write_text(contents, encoding="utf-8")

    result = api.load_config(
        path, raw_ref="raw://config/resource", event_id="event",
        received_at=RECEIVED,
    )

    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (
        "$", "toml_resource_unsupported",
    )
    assert result.rejection.stage == "config_load"
    assert result.rejection.reasons == ("toml_resource_unsupported",)
    assert str(path) not in repr(result.rejection)


def test_missing_file_returns_bounded_read_failure(tmp_path: Path) -> None:
    result = api.load_config(
        tmp_path / "credential-name.toml",
        raw_ref="raw://config/file-1",
        event_id="event-1",
        received_at=RECEIVED,
    )
    assert result.rejection is not None
    assert result.rejection.code == "config_read_failed"
    assert "credential-name" not in repr(result.rejection)


def test_load_config_matches_equivalent_raw_and_empty_toml(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.toml"
    empty_path.write_bytes(b"")
    explicit_path = tmp_path / "explicit.toml"
    explicit_path.write_text(
        "initial_virtual_equity = '150000.00'\n"
        "entry_start = '10:00'\n"
        "[execution]\n"
        "entry_lifetime_us = 5000000\n",
        encoding="utf-8",
    )

    empty = api.load_config(
        empty_path, raw_ref="raw://one", event_id="one", received_at=RECEIVED
    )
    loaded = api.load_config(
        str(explicit_path), raw_ref="raw://two", event_id="two",
        received_at=RECEIVED + timedelta(seconds=1),
    )
    direct = normalize(
        {
            "execution": {"entry_lifetime_us": 5_000_000},
            "entry_start": "10:00:00",
            "initial_virtual_equity": "150000",
        }
    )

    assert empty.value == StrategyConfig()
    assert loaded.value is not None and direct.value is not None
    assert config_hash(loaded.value) == config_hash(direct.value)
    assert policy_hash(loaded.value) == policy_hash(direct.value)


def test_load_config_uses_the_single_normalizer_path(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"max_contracts = 1\n")
    calls: list[object] = []
    real_normalize = api.normalize_config

    def recording_normalize(raw: object, **envelope: object) -> api.ConfigValidation:
        calls.append(raw)
        return real_normalize(raw, **envelope)

    monkeypatch.setattr(api, "normalize_config", recording_normalize)
    result = api.load_config(
        path, raw_ref="raw://config", event_id="event", received_at=RECEIVED
    )

    assert result.value == StrategyConfig()
    assert calls == [{"max_contracts": 1}]


class HostilePath:
    def __fspath__(self) -> str:
        raise AssertionError("path callback must not run")


class ValueErrorPath(type(Path())):
    def __fspath__(self) -> str:
        raise ValueError("trusted path conversion failure")


def test_path_and_envelope_misuse_raise_before_file_io(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="path"):
        api.load_config(
            HostilePath(), raw_ref="raw", event_id="event", received_at=RECEIVED
        )
    with pytest.raises(ValueError, match="raw_ref"):
        api.load_config(
            tmp_path / "missing.toml", raw_ref="", event_id="event",
            received_at=RECEIVED,
        )


def test_trusted_path_value_error_propagates_from_open() -> None:
    with pytest.raises(ValueError, match="trusted path conversion failure"):
        api.load_config(
            ValueErrorPath("ignored.toml"), raw_ref="raw", event_id="event",
            received_at=RECEIVED,
        )


@pytest.mark.parametrize(
    "failure", [RuntimeError("parser bug"), KeyboardInterrupt(), SystemExit(7)]
)
def test_unrelated_parser_and_process_control_failures_propagate(
    tmp_path: Path, monkeypatch, failure: BaseException
) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"")

    def fail(stream: object) -> object:
        raise failure

    monkeypatch.setattr(api.tomllib, "load", fail)
    with pytest.raises(type(failure)):
        api.load_config(
            path, raw_ref="raw", event_id="event", received_at=RECEIVED
        )


@pytest.mark.parametrize("failure", [ValueError("normalizer bug"), RuntimeError("normalizer bug")])
def test_loader_does_not_swallow_normalizer_failures(
    tmp_path: Path, monkeypatch, failure: Exception
) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"")

    def fail(raw: object, **envelope: object) -> api.ConfigValidation:
        raise failure

    monkeypatch.setattr(api, "normalize_config", fail)
    with pytest.raises(type(failure), match="normalizer bug"):
        api.load_config(
            path, raw_ref="raw", event_id="event", received_at=RECEIVED
        )


def test_normalizer_does_not_swallow_unexpected_programming_failures(monkeypatch) -> None:
    def fail(**values: object) -> StrategyConfig:
        raise RuntimeError("programming failure")

    monkeypatch.setattr(api, "StrategyConfig", fail)
    with pytest.raises(RuntimeError, match="programming failure"):
        normalize({})
