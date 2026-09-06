"""Normalize untrusted raw and TOML strategy configuration."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
import re
import tomllib
from typing import Literal, Never, TypeAlias

from ._input_parsing import _InvalidInput, _parse_decimal
from ._validation import _require_nonempty_string, _trusted_datetime
from .config import ExecutionPolicy, StrategyConfig, _ConfigConstraintError


ConfigRejectionCode: TypeAlias = Literal[
    "expected_exact_dict", "unknown_fields", "invalid_type", "invalid_value",
    "invalid_decimal", "invalid_time", "fixed_value_required", "range_invalid",
    "entry_lifetime_infeasible", "window_invalid", "schedule_infeasible",
    "decimal_representation_unsupported", "config_read_failed", "invalid_utf8",
    "invalid_toml", "toml_resource_unsupported",
]

_ROOT_FIELDS = (
    "underlying", "paper_only", "max_contracts", "min_dte", "max_dte",
    "min_abs_delta", "max_abs_delta", "max_spread_fraction", "spread_floor",
    "round_trip_fee_floor", "estimated_round_trip_cost", "premium_fraction",
    "daily_loss_fraction", "drawdown_fraction", "max_entries_per_session",
    "initial_virtual_equity", "max_account_age_us", "max_reconciliation_age_us",
    "entry_start", "entry_end", "liquidation_start", "liquidation_deadline",
    "execution",
)
_EXECUTION_FIELDS = (
    "entry_lifetime_us", "max_quote_age_us", "label_exit_lateness_us",
    "max_exit_replacements", "holding_period_us", "decision_cadence_us",
)
_DECIMAL_FIELDS = (
    "min_abs_delta", "max_abs_delta", "max_spread_fraction", "spread_floor",
    "round_trip_fee_floor", "estimated_round_trip_cost", "premium_fraction",
    "daily_loss_fraction", "drawdown_fraction", "initial_virtual_equity",
)
_INTEGER_FIELDS = (
    "max_contracts", "min_dte", "max_dte", "max_entries_per_session",
)
_TIME_FIELDS = (
    "entry_start", "entry_end", "liquidation_start", "liquidation_deadline",
)
_TIME_PATTERN = re.compile(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?")
_LOAD_CODES = (
    "config_read_failed", "invalid_utf8", "invalid_toml",
    "toml_resource_unsupported",
)


@dataclass(frozen=True)
class ConfigInputRejection:
    """This class represents one safe configuration input failure."""

    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: ConfigRejectionCode

    def __post_init__(self) -> None:
        """
        Validate trusted identity and the bounded configuration error vocabulary.

        :returns:             None.
        :raises   TypeError:  If identity, time, field, or code has a wrong type.
        :raises   ValueError: If identity is empty or field and code are incompatible.
        """
        _require_nonempty_string("event_id", self.event_id)
        _require_nonempty_string("raw_ref", self.raw_ref)
        object.__setattr__(
            self, "received_at", _trusted_datetime("received_at", self.received_at)
        )
        _require_nonempty_string("field", self.field)
        _require_nonempty_string("code", self.code)
        if self.code not in _allowed_codes(self.field):
            raise ValueError("rejection field and code are incompatible")

    @property
    def stage(self) -> Literal["config_normalization", "config_load"]:
        """Return the fixed stage associated with this bounded failure.

        :returns: The configuration load or normalization stage.
        """
        return "config_load" if self.code in _LOAD_CODES else "config_normalization"

    @property
    def reasons(self) -> tuple[ConfigRejectionCode]:
        """Return the single safe code as immutable evidence.

        :returns: A one-item tuple containing the rejection code.
        """
        return (self.code,)


@dataclass(frozen=True)
class ConfigValidation:
    """This class represents exactly one normalized config or safe rejection."""

    value: StrategyConfig | None = None
    rejection: ConfigInputRejection | None = None

    def __post_init__(self) -> None:
        """
        Enforce one exact concrete configuration-normalization outcome.

        :returns:             None.
        :raises   TypeError:  If an outcome has the wrong concrete record type.
        :raises   ValueError: If neither or both outcomes are supplied.
        """
        if (self.value is None) == (self.rejection is None):
            raise ValueError("validation result must contain exactly one outcome")
        if self.value is not None and type(self.value) is not StrategyConfig:
            raise TypeError("validation result value must be a StrategyConfig")
        if self.rejection is not None and type(self.rejection) is not ConfigInputRejection:
            raise TypeError("validation result rejection must be a ConfigInputRejection")


def normalize_config(
    raw: object, *, raw_ref: str, event_id: str, received_at: datetime
) -> ConfigValidation:
    """
    Normalize one exact raw configuration dictionary.

    Omitted keys use trusted dataclass defaults. Decimal settings must be ASCII
    fixed-point strings, durations are bounded integer microseconds, and local
    clocks use ``HH:MM`` or ``HH:MM:SS``. Malformed source data returns one
    bounded rejection while trusted envelope misuse raises.

    :param    raw:         Untrusted raw configuration value.
    :param    raw_ref:     Trusted reference to the source record.
    :param    event_id:    Trusted ingestion event identifier.
    :param    received_at: Trusted receipt timestamp.
    :returns:              Exactly one normalized config or safe rejection.
    :raises   TypeError:   If a trusted envelope argument has the wrong type.
    :raises   ValueError:  If a trusted envelope argument is invalid.
    """
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        root, execution_raw = _snapshot_input(raw)
        values = _parse_root(root)
        values["execution"] = _parse_execution(execution_raw)
        config = StrategyConfig(**values)
    except _InvalidInput as failure:
        field, code = failure.args
        return _rejected(event_id, received_at, raw_ref, field, code)
    except _ConfigConstraintError as failure:
        return _rejected(event_id, received_at, raw_ref, failure.field, failure.code)
    return ConfigValidation(value=config)


def load_config(
    path: str | Path, *, raw_ref: str, event_id: str, received_at: datetime
) -> ConfigValidation:
    """
    Load and normalize one TOML configuration file exactly once.

    Ordinary file, UTF-8, and TOML failures become bounded rejection evidence.
    The path and parser exception text are never retained. Parsed TOML uses
    ``normalize_config`` as the only raw conversion path.

    :param    path:        Trusted string or pathlib path to the TOML file.
    :param    raw_ref:     Trusted reference to the source record.
    :param    event_id:    Trusted ingestion event identifier.
    :param    received_at: Trusted receipt timestamp.
    :returns:              Exactly one normalized config or safe rejection.
    :raises   TypeError:   If a trusted argument has the wrong type.
    :raises   ValueError:  If a trusted envelope argument is invalid.
    """
    if type(path) is not str and not isinstance(path, Path):
        raise TypeError("path must be a string or Path")
    _require_nonempty_string("raw_ref", raw_ref)
    _require_nonempty_string("event_id", event_id)
    received_at = _trusted_datetime("received_at", received_at)
    try:
        with open(path, "rb") as stream:
            try:
                raw = tomllib.load(stream)
            except UnicodeDecodeError:
                return _rejected(event_id, received_at, raw_ref, "$", "invalid_utf8")
            except tomllib.TOMLDecodeError:
                return _rejected(event_id, received_at, raw_ref, "$", "invalid_toml")
            except (ValueError, RecursionError):
                return _rejected(
                    event_id, received_at, raw_ref, "$",
                    "toml_resource_unsupported",
                )
    except OSError:
        return _rejected(event_id, received_at, raw_ref, "$", "config_read_failed")
    return normalize_config(raw, raw_ref=raw_ref, event_id=event_id, received_at=received_at)


def _snapshot_input(raw: object) -> tuple[dict[str, object], dict[str, object]]:
    """Validate structural keys and detach root and execution mappings."""
    if type(raw) is not dict:
        _parse_fail("$", "expected_exact_dict")
    root_keys = tuple(raw)
    if any(type(key) is not str for key in root_keys):
        _parse_fail("$", "unknown_fields")
    if any(key not in _ROOT_FIELDS for key in root_keys):
        _parse_fail("$", "unknown_fields")
    root = raw.copy()
    nested = root.get("execution", {})
    if type(nested) is not dict:
        _parse_fail("execution", "expected_exact_dict")
    nested_keys = tuple(nested)
    if any(type(key) is not str for key in nested_keys):
        _parse_fail("execution", "unknown_fields")
    if any(key not in _EXECUTION_FIELDS for key in nested_keys):
        _parse_fail("execution", "unknown_fields")
    return root, nested.copy()


def _parse_root(root: dict[str, object]) -> dict[str, object]:
    """Parse supplied root scalars in declaration order without defaults."""
    values: dict[str, object] = {}
    if "underlying" in root:
        values["underlying"] = _parse_nonempty_string(root["underlying"], "underlying")
    if "paper_only" in root:
        values["paper_only"] = _parse_bool(root["paper_only"], "paper_only")
    for field in ("max_contracts", "min_dte", "max_dte"):
        if field in root:
            values[field] = _parse_integer(root[field], field)
    for field in (
        "min_abs_delta", "max_abs_delta", "max_spread_fraction", "spread_floor",
        "round_trip_fee_floor", "estimated_round_trip_cost", "premium_fraction",
        "daily_loss_fraction", "drawdown_fraction",
    ):
        if field in root:
            values[field] = _parse_decimal(root[field], field)
    if "max_entries_per_session" in root:
        values["max_entries_per_session"] = _parse_integer(
            root["max_entries_per_session"], "max_entries_per_session"
        )
    if "initial_virtual_equity" in root:
        values["initial_virtual_equity"] = _parse_decimal(
            root["initial_virtual_equity"], "initial_virtual_equity"
        )
    if "max_account_age_us" in root:
        values["max_account_age"] = _parse_duration(
            root["max_account_age_us"], "max_account_age_us", 0, 5_000_000
        )
    if "max_reconciliation_age_us" in root:
        values["max_reconciliation_age"] = _parse_duration(
            root["max_reconciliation_age_us"], "max_reconciliation_age_us", 0, 5_000_000
        )
    for field in _TIME_FIELDS:
        if field in root:
            values[field] = _parse_time(root[field], field)
    return values


def _parse_execution(raw: dict[str, object]) -> ExecutionPolicy:
    """Parse nested execution assertions and configurable limits."""
    values: dict[str, object] = {}
    if "entry_lifetime_us" in raw:
        value = _parse_integer(raw["entry_lifetime_us"], "execution.entry_lifetime_us")
        if value <= 1_000_000:
            _parse_fail("execution.entry_lifetime_us", "entry_lifetime_infeasible")
        if value > 5_000_000:
            _parse_fail("execution.entry_lifetime_us", "range_invalid")
        values["entry_lifetime"] = timedelta(microseconds=value)
    if "max_quote_age_us" in raw:
        values["max_quote_age"] = _parse_duration(
            raw["max_quote_age_us"], "execution.max_quote_age_us", 0, 5_000_000
        )
    if "label_exit_lateness_us" in raw:
        values["label_exit_lateness"] = _parse_duration(
            raw["label_exit_lateness_us"], "execution.label_exit_lateness_us",
            0, 60_000_000,
        )
    if "max_exit_replacements" in raw:
        value = _parse_integer(
            raw["max_exit_replacements"], "execution.max_exit_replacements"
        )
        if not 0 <= value <= 3:
            _parse_fail("execution.max_exit_replacements", "range_invalid")
        values["max_exit_replacements"] = value
    _parse_fixed_integer_assertion(raw, "holding_period_us", 1_800_000_000)
    _parse_fixed_integer_assertion(raw, "decision_cadence_us", 300_000_000)
    return ExecutionPolicy(**values)


def _parse_nonempty_string(value: object, field: str) -> str:
    """Parse an exact nonempty string."""
    if type(value) is not str:
        _parse_fail(field, "invalid_type")
    if not value:
        _parse_fail(field, "invalid_value")
    return value


def _parse_bool(value: object, field: str) -> bool:
    """Parse an exact boolean."""
    if type(value) is not bool:
        _parse_fail(field, "invalid_type")
    return value


def _parse_integer(value: object, field: str) -> int:
    """Parse an exact integer that excludes booleans and subclasses."""
    if type(value) is not int:
        _parse_fail(field, "invalid_type")
    return value


def _parse_duration(
    value: object, field: str, minimum_exclusive: int, maximum: int
) -> timedelta:
    """Parse bounded integer microseconds before constructing a timedelta."""
    parsed = _parse_integer(value, field)
    if not minimum_exclusive < parsed <= maximum:
        _parse_fail(field, "range_invalid")
    return timedelta(microseconds=parsed)


def _parse_fixed_integer_assertion(
    raw: dict[str, object], key: str, expected: int
) -> None:
    """Validate an optional exact assertion of a registered duration."""
    if key not in raw:
        return
    field = f"execution.{key}"
    if _parse_integer(raw[key], field) != expected:
        _parse_fail(field, "fixed_value_required")


def _parse_time(value: object, field: str) -> time:
    """Parse one exact ASCII offset-free local clock with whole-second precision."""
    if type(value) is not str:
        _parse_fail(field, "invalid_type")
    if _TIME_PATTERN.fullmatch(value) is None:
        _parse_fail(field, "invalid_time")
    try:
        return time.fromisoformat(value)
    except ValueError:
        _parse_fail(field, "invalid_time")


def _parse_fail(field: str, code: ConfigRejectionCode) -> Never:
    """Stop raw configuration parsing at one bounded field and code."""
    raise _InvalidInput(field, code)


def _rejected(
    event_id: str, received_at: datetime, raw_ref: str, field: str,
    code: ConfigRejectionCode,
) -> ConfigValidation:
    """Build one exact bounded rejection outcome."""
    return ConfigValidation(
        rejection=ConfigInputRejection(event_id, received_at, raw_ref, field, code)
    )


def _allowed_codes(field: str) -> tuple[ConfigRejectionCode, ...]:
    """Return bounded rejection codes compatible with one config field."""
    if field == "$":
        return (
            "expected_exact_dict", "unknown_fields", "config_read_failed",
            "invalid_utf8", "invalid_toml", "toml_resource_unsupported",
        )
    if field == "execution":
        return ("expected_exact_dict", "unknown_fields")
    if field == "underlying":
        return ("invalid_type", "invalid_value", "fixed_value_required")
    if field == "paper_only":
        return ("invalid_type", "fixed_value_required")
    if field == "max_contracts":
        return ("invalid_type", "fixed_value_required")
    if field in ("min_dte", "max_entries_per_session"):
        return ("invalid_type", "range_invalid")
    if field == "max_dte":
        return ("invalid_type",)
    if field in _DECIMAL_FIELDS:
        common = (
            "invalid_type", "invalid_decimal", "decimal_representation_unsupported",
        )
        return common if field == "max_abs_delta" else common + ("range_invalid",)
    if field in ("max_account_age_us", "max_reconciliation_age_us"):
        return ("invalid_type", "range_invalid")
    if field == "entry_start":
        return ("invalid_type", "invalid_time")
    if field in ("entry_end", "liquidation_deadline"):
        return ("invalid_type", "invalid_time", "window_invalid")
    if field == "liquidation_start":
        return (
            "invalid_type", "invalid_time", "window_invalid", "schedule_infeasible",
        )
    if field == "execution.entry_lifetime_us":
        return ("invalid_type", "range_invalid", "entry_lifetime_infeasible")
    if field in (
        "execution.max_quote_age_us", "execution.label_exit_lateness_us",
        "execution.max_exit_replacements",
    ):
        return ("invalid_type", "range_invalid")
    if field in (
        "execution.holding_period_us", "execution.decision_cadence_us",
    ):
        return ("invalid_type", "fixed_value_required")
    return ()
