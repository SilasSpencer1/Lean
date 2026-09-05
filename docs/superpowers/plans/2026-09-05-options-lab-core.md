# Phase 1: OptionsLab Decision and Risk Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the dependency-light, broker-neutral Python core that validates option observations, creates executable-return labels and features, selects a call/put/cash action, enforces deterministic risk, tracks order lifecycle, and emits an audit record.

**Phase boundary:** This plan does not complete the end-to-end system. It freezes and verifies the shared core first; point-in-time data/training, the LEAN adapter, Alpaca shadow/paper execution, and licensed news/social shadow ingestion each require the later plans listed in the completion gate.

**Architecture:** The package lives under OptionsLab and knows nothing about LEAN or Alpaca. Immutable domain objects cross its boundaries. Pure functions handle labels, features, candidates, decisions, exit policy, and state transitions; one small RiskEngine owns the fail-closed capital rules. Later LEAN and Alpaca adapters must translate into these interfaces and pass replay-parity tests.

**Tech Stack:** Python 3.11+, standard library dataclasses/decimal/datetime/enum/json/tomllib, pytest. No runtime dependency is introduced in this core plan.

**Spec:** docs/superpowers/specs/2026-09-05-spy-options-paper-system-design.md

## Global Constraints

- SPY options only.
- Long single-leg calls or puts only; no shorts, spreads, 0DTE, overnight positions, or production execution.
- Decision cadence is five minutes between 10:00 and 15:00 America/New_York.
- Normal holding horizon is 30 minutes; hard liquidation deadline is 15:40 America/New_York.
- Eligible contracts have 7-21 calendar DTE, 0.40-0.60 absolute delta, positive uncrossed quotes, and bounded spread.
- At most one contract, one position, one pending entry, and three completed entries per session.
- Premium risk cap is 0.5% of virtual equity; daily loss cutoff is 1%; peak drawdown cutoff is 5%.
- Missing, stale, NaN, naive-datetime, schema-mismatched, or contradictory inputs fail closed.
- Every class and public method follows the repository's Javadoc-inspired Python documentation skill: concrete class description plus aligned :param, :returns:, and :raises fields.
- Keep the core independent from LEAN, Alpaca, pandas, NumPy, and scikit-learn.
- Use test-driven development and commit after each task passes.

## File Map

- OptionsLab/pyproject.toml: package metadata and pytest configuration.
- .gitignore: local OptionsLab environments, caches, and editable-install metadata.
- OptionsLab/src/options_lab/__init__.py: stable public exports only.
- OptionsLab/src/options_lab/domain.py: immutable enums and value objects shared by every component.
- OptionsLab/src/options_lab/config.py: validated strategy/risk configuration and TOML loading.
- OptionsLab/src/options_lab/labels.py: ask-to-bid 30-minute net-return labels.
- OptionsLab/src/options_lab/features.py: stable feature schema and finite numeric feature construction.
- OptionsLab/src/options_lab/candidates.py: deterministic eligibility and one-call/one-put ranking.
- OptionsLab/src/options_lab/decision.py: predictor protocol and call/put/cash policy.
- OptionsLab/src/options_lab/risk.py: deterministic capital and operational risk gate.
- OptionsLab/src/options_lab/exits.py: shared holding, safety-exit, and liquidation policy.
- OptionsLab/src/options_lab/lifecycle.py: pure order-state transition function.
- OptionsLab/src/options_lab/audit.py: lossless JSONL serialization with atomic append semantics.
- OptionsLab/tests/__init__.py: marks the test support package.
- OptionsLab/tests/factories.py: reusable, deterministic object factories.
- OptionsLab/tests/conftest.py: shared pytest fixtures built from the factories.
- OptionsLab/tests/test_*.py: focused unit tests matching the module names above.

---

### Task 1: Package, domain objects, and validated configuration

**Files:**
- Modify: .gitignore
- Create: OptionsLab/pyproject.toml
- Create: OptionsLab/src/options_lab/__init__.py
- Create: OptionsLab/src/options_lab/domain.py
- Create: OptionsLab/src/options_lab/config.py
- Create: OptionsLab/tests/__init__.py
- Create: OptionsLab/tests/factories.py
- Create: OptionsLab/tests/test_domain.py
- Create: OptionsLab/tests/test_config.py

**Interfaces:**
- Consumes: nothing.
- Produces: OptionRight, ValueSource, Action, ExchangeSession, OptionQuote, MarketFeatures, DecisionContext, Prediction, TradeIntent, PortfolioState, RiskCheck, RiskDecision, StrategyConfig, load_config(path), config_snapshot(config), and config_hash(config).

- [ ] **Step 1: Bootstrap isolated test tooling**

Append only these scoped entries to the existing root .gitignore:

```gitignore
/OptionsLab/.venv/
/OptionsLab/.pytest_cache/
/OptionsLab/.coverage
/OptionsLab/src/*.egg-info/
/OptionsLab/**/__pycache__/
```

Create an empty OptionsLab/src/options_lab/__init__.py and create OptionsLab/pyproject.toml:

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "options-lab"
version = "0.1.0"
description = "Broker-neutral decision and risk core for SPY options research"
requires-python = ">=3.11"

[project.optional-dependencies]
test = ["pytest>=8.3,<9"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create the isolated environment and install the pinned test runner before the red test:

```bash
cd OptionsLab
python3 -m venv .venv
.venv/bin/python -m pip install 'pytest>=8.3,<9'
```

- [ ] **Step 2: Add deterministic test factories and failing domain validation tests**

Create an empty OptionsLab/tests/__init__.py. Create OptionsLab/tests/factories.py with the reusable quote factory below so later tests never import from another test module:

```python
from datetime import date, datetime, timezone
from decimal import Decimal

from options_lab.domain import ExchangeSession, OptionQuote, OptionRight, ValueSource


def valid_session(**overrides: object) -> ExchangeSession:
    """Build a valid XNYS regular session for tests.

    :param    overrides: Field values that replace factory defaults.
    :returns:           A validated exchange session.
    """
    values = {
        "calendar": "XNYS",
        "session_date": date(2026, 9, 4),
        "opens_at": datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc),
        "closes_at": datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc),
        "is_regular": True,
        "source": "fixture",
    }
    values.update(overrides)
    return ExchangeSession(**values)


def valid_quote(**overrides: object) -> OptionQuote:
    """Build a valid deterministic option quote for tests.

    :param    overrides: Field values that replace factory defaults.
    :returns:           A validated SPY option quote.
    """
    observed_at = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)
    values = {
        "symbol": "SPY260918C00650000",
        "underlying": "SPY",
        "observed_at": observed_at,
        "source_at": observed_at,
        "source": "fixture",
        "quality_flags": (),
        "expiration": date(2026, 9, 18),
        "strike": Decimal("650"),
        "right": OptionRight.CALL,
        "bid": Decimal("5.00"),
        "ask": Decimal("5.10"),
        "delta": 0.50,
        "delta_source": ValueSource.FEED,
        "implied_volatility": 0.18,
        "implied_volatility_source": ValueSource.FEED,
    }
    if "observed_at" in overrides and "source_at" not in overrides:
        values["source_at"] = overrides["observed_at"]
    values.update(overrides)
    return OptionQuote(**values)
```

Create OptionsLab/tests/test_domain.py with tests that construct a valid UTC-aware OptionQuote and assert that naive timestamps, non-positive asks, crossed quotes, non-SPY underlyings, deltas outside [-1, 1], and non-finite values raise ValueError. Also test ExchangeSession with the regular fixture, a valid 13:00 early close marked non-regular, and rejected wrong-calendar/mismatched-date/naive/reversed cases. Include this representative quote test:

```python
from datetime import datetime

import pytest

from tests.factories import valid_quote


def test_option_quote_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        valid_quote(observed_at=datetime(2026, 9, 4, 14, 0))
```

- [ ] **Step 3: Add failing configuration tests**

Create OptionsLab/tests/test_config.py and verify the exact approved defaults plus fail-closed overrides:

```python
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from options_lab.config import StrategyConfig, load_config


def test_strategy_config_matches_approved_risk_limits() -> None:
    config = StrategyConfig()
    assert config.min_dte == 7
    assert config.max_dte == 21
    assert config.max_contracts == 1
    assert config.max_entries_per_day == 3
    assert config.estimated_round_trip_cost == Decimal("1.00")
    assert config.label_exit_lateness == timedelta(minutes=1)
    assert config.premium_risk_fraction == Decimal("0.005")
    assert config.daily_loss_fraction == Decimal("0.01")
    assert config.drawdown_fraction == Decimal("0.05")
    assert config.paper_only is True


def test_load_config_rejects_production_mode(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.toml"
    path.write_text('paper_only = false\n', encoding="utf-8")
    with pytest.raises(ValueError, match="paper_only must remain true"):
        load_config(path)
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```bash
cd OptionsLab
.venv/bin/python -m pytest tests/test_domain.py tests/test_config.py -v
```

Expected: collection fails because options_lab.domain and options_lab.config do not exist.

- [ ] **Step 5: Implement immutable domain objects**

Create OptionsLab/src/options_lab/domain.py. Use frozen, slotted dataclasses and validate public input in __post_init__. The implementation must expose these exact fields:

```python
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from math import isfinite


class OptionRight(StrEnum):
    """This class represents the right encoded by an option contract."""

    CALL = "call"
    PUT = "put"


class Action(StrEnum):
    """This class represents the only actions permitted by the strategy core."""

    CASH = "cash"
    BUY_CALL = "buy_call"
    BUY_PUT = "buy_put"


class ValueSource(StrEnum):
    """This class represents provenance for a supplied or modeled value."""

    FEED = "feed"
    LEAN_MODEL = "lean_model"


@dataclass(frozen=True, slots=True)
class ExchangeSession:
    """This class represents one authoritative XNYS trading session."""

    calendar: str
    session_date: date
    opens_at: datetime
    closes_at: datetime
    is_regular: bool
    source: str


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """This class represents one validated point-in-time SPY option quote."""

    symbol: str
    underlying: str
    observed_at: datetime
    source_at: datetime
    source: str
    quality_flags: tuple[str, ...]
    expiration: date
    strike: Decimal
    right: OptionRight
    bid: Decimal
    ask: Decimal
    delta: float
    delta_source: ValueSource
    implied_volatility: float
    implied_volatility_source: ValueSource
    multiplier: int = 100
    bid_size: int | None = None
    ask_size: int | None = None
    open_interest: int | None = None
    return_1m: float | None = None
    return_5m: float | None = None
    return_15m: float | None = None
    gamma: float | None = None
    gamma_source: ValueSource | None = None
    theta: float | None = None
    theta_source: ValueSource | None = None
    vega: float | None = None
    vega_source: ValueSource | None = None

    def __post_init__(self) -> None:
        """Validate the option quote.

        :returns:             None.
        :raises   ValueError: If the quote is not a finite, timezone-aware, uncrossed SPY quote.
        """
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("Option quote symbol cannot be empty")
        if self.underlying != "SPY":
            raise ValueError("Option quote underlying must be SPY")
        if not isinstance(self.expiration, date) or isinstance(self.expiration, datetime):
            raise ValueError("Option quote expiration must be a date")
        if not isinstance(self.right, OptionRight):
            raise ValueError("Option quote right must be an OptionRight")
        if not isinstance(self.observed_at, datetime):
            raise ValueError("Option quote timestamp must be a datetime")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Option quote timestamp must be timezone-aware")
        if not isinstance(self.source_at, datetime):
            raise ValueError("Option source timestamp must be a datetime")
        if self.source_at.tzinfo is None or self.source_at.utcoffset() is None:
            raise ValueError("Option source timestamp must be timezone-aware")
        if self.source_at > self.observed_at:
            raise ValueError("Option source timestamp cannot follow observation time")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("Option source cannot be empty")
        if not isinstance(self.quality_flags, tuple) or any(
            not isinstance(flag, str) or not flag for flag in self.quality_flags
        ):
            raise ValueError("Option source and quality flags cannot be empty")
        if len(set(self.quality_flags)) != len(self.quality_flags):
            raise ValueError("Option quality flags must be unique")
        prices = (self.strike, self.bid, self.ask)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in prices):
            raise ValueError("Option quote prices must be finite Decimal values")
        if self.strike <= 0 or self.bid <= 0 or self.ask <= 0 or self.ask <= self.bid:
            raise ValueError("Option quote prices must be positive and ask must exceed bid")
        if isinstance(self.delta, bool) or not isinstance(self.delta, (int, float)) or not isfinite(self.delta) or abs(self.delta) > 1:
            raise ValueError("Option quote delta must be finite and inside [-1, 1]")
        if isinstance(self.implied_volatility, bool) or not isinstance(self.implied_volatility, (int, float)) or not isfinite(self.implied_volatility) or self.implied_volatility <= 0:
            raise ValueError("Option quote implied volatility must be finite and positive")
        provenance = {ValueSource.FEED, ValueSource.LEAN_MODEL}
        if self.delta_source not in provenance or self.implied_volatility_source not in provenance:
            raise ValueError("Greek provenance must be feed or lean_model")
        if type(self.multiplier) is not int or self.multiplier != 100:
            raise ValueError("Version-one option multiplier must be 100")
        for name, value in (
            ("bid_size", self.bid_size),
            ("ask_size", self.ask_size),
            ("open_interest", self.open_interest),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer when supplied")
        for name, value in (
            ("return_1m", self.return_1m),
            ("return_5m", self.return_5m),
            ("return_15m", self.return_15m),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be finite when supplied")
        for name, value, source in (
            ("gamma", self.gamma, self.gamma_source),
            ("theta", self.theta, self.theta_source),
            ("vega", self.vega, self.vega_source),
        ):
            if (value is None) != (source is None):
                raise ValueError(f"{name} and its provenance must be supplied together")
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or source not in provenance
            ):
                raise ValueError(f"{name} must be finite with valid provenance")
        if self.gamma is not None and self.gamma < 0:
            raise ValueError("gamma cannot be negative")
        if self.vega is not None and self.vega < 0:
            raise ValueError("vega cannot be negative")

    @property
    def midpoint(self) -> Decimal:
        """Return the bid/ask midpoint.

        :returns: The arithmetic midpoint.
        """
        return (self.bid + self.ask) / Decimal("2")
```

Also implement the exact dataclasses below. Their __post_init__ methods must enforce these rules with ValueError: ExchangeSession requires calendar `XNYS`, a real date, aware open/close timestamps with open < close that map to session_date in America/New_York, exact Boolean is_regular, and a non-empty source; a regular session must be exactly 09:30-16:00 New York, while a non-regular session retains its authoritative calendar times. MarketFeatures requires aware source/observation timestamps with source_at <= observed_at, a non-empty source, unique non-empty quality flags, a finite positive SPY price, finite float features, exact non-negative integer volume/session-minute counts, a non-negative underlying spread fraction, and either no underlying bid/ask or a finite positive uncrossed pair whose computed spread fraction differs by no more than 1e-12. DecisionContext requires an aware decision time, `session: ExchangeSession | None`, a 64-character lowercase hexadecimal raw-data manifest hash, a non-empty feed_class, aware contained observations, and no duplicate option symbols (missing sessions and future/stale observations remain representable so candidate selection can reject and audit them). Prediction requires a non-empty symbol/model/schema, 64-character lowercase hexadecimal model/config hashes, finite expected return, and an aware training cutoff. TradeIntent requires a non-empty symbol/model, valid model/config hashes, quantity greater than zero, finite positive price/premium, finite expected return, and an aware decision time. PortfolioState requires finite Decimal balances/P&L, non-negative counters, and exact Boolean flags. RiskCheck requires a non-empty name and exact Boolean result. RiskDecision requires a non-empty, unique, ordered check ledger, an intent exactly when approved is true, every check passing when approved, and reason equal to the first failed check name when rejected.

```python
@dataclass(frozen=True, slots=True)
class MarketFeatures:
    """This class represents point-in-time SPY features supplied by an adapter."""

    observed_at: datetime
    source_at: datetime
    source: str
    quality_flags: tuple[str, ...]
    spy_price: Decimal
    return_1m: float
    return_5m: float
    return_15m: float
    return_30m: float
    realized_vol_5m: float
    realized_vol_15m: float
    realized_vol_30m: float
    distance_from_vwap: float
    volume: int
    volume_zscore: float
    underlying_spread_fraction: float
    minutes_since_open: int
    minutes_until_close: int
    underlying_bid: Decimal | None = None
    underlying_ask: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """This class represents all market information available at one decision time."""

    decision_time: datetime
    session: ExchangeSession | None
    market: MarketFeatures
    options: tuple[OptionQuote, ...]
    feed_class: str
    raw_data_manifest_hash: str


@dataclass(frozen=True, slots=True)
class Prediction:
    """This class represents a point-in-time expected executable option return."""

    symbol: str
    expected_net_return: float
    model_id: str
    model_hash: str
    strategy_config_hash: str
    training_cutoff: datetime
    feature_schema: str


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """This class represents a proposed long option entry before risk approval."""

    symbol: str
    right: OptionRight
    quantity: int
    limit_price: Decimal
    premium: Decimal
    expected_net_return: float
    decision_time: datetime
    model_id: str
    model_hash: str
    strategy_config_hash: str


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """This class represents the conservative account state used by the risk engine."""

    virtual_equity: Decimal
    session_start_equity: Decimal
    high_watermark: Decimal
    session_realized_pnl: Decimal
    marked_unrealized_pnl: Decimal
    entries_today: int
    open_quantity: int
    pending_entry: bool
    pending_exit: bool
    connected: bool
    broker_reconciled: bool
    data_healthy: bool
    model_valid: bool
    kill_switch: bool


@dataclass(frozen=True, slots=True)
class RiskCheck:
    """This class represents one evaluated deterministic risk guard."""

    name: str
    passed: bool


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """This class represents an approved or rejected trade intent."""

    approved: bool
    reason: str
    intent: TradeIntent | None
    checks: tuple[RiskCheck, ...]
```

- [ ] **Step 6: Implement configuration and TOML loading**

Create OptionsLab/src/options_lab/config.py. The dataclass must carry the exact defaults below and load only known keys from a TOML file:

```python
from dataclasses import dataclass, fields
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """This class represents the approved strategy and risk limits."""

    min_dte: int = 7
    max_dte: int = 21
    min_abs_delta: float = 0.40
    max_abs_delta: float = 0.60
    max_spread_fraction: Decimal = Decimal("0.08")
    spread_floor: Decimal = Decimal("0.05")
    estimated_round_trip_cost: Decimal = Decimal("1.00")
    max_quote_age: timedelta = timedelta(seconds=5)
    label_exit_lateness: timedelta = timedelta(minutes=1)
    entry_start: time = time(10, 0)
    entry_end: time = time(15, 0)
    liquidation_start: time = time(15, 35)
    liquidation_deadline: time = time(15, 40)
    holding_period: timedelta = timedelta(minutes=30)
    max_contracts: int = 1
    max_entries_per_day: int = 3
    premium_risk_fraction: Decimal = Decimal("0.005")
    daily_loss_fraction: Decimal = Decimal("0.01")
    drawdown_fraction: Decimal = Decimal("0.05")
    adverse_cost_return: float = 0.005
    paper_only: bool = True


def load_config(path: Path) -> StrategyConfig:
    """Load and validate strategy configuration from TOML.

    :param    path:       Path to the TOML file.
    :returns:             A validated StrategyConfig.
    :raises   ValueError: If a key is unknown or paper_only is false.
    """
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {field.name for field in fields(StrategyConfig)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown strategy configuration keys: {sorted(unknown)}")
    if raw.get("paper_only") is False:
        raise ValueError("paper_only must remain true")
    values = {name: _coerce_config_value(name, value) for name, value in raw.items()}
    return StrategyConfig(**values)
```

Implement `_coerce_config_value` with this exact external format:

- `max_spread_fraction`, `spread_floor`, `estimated_round_trip_cost`, `premium_risk_fraction`, `daily_loss_fraction`, and `drawdown_fraction` are finite decimal strings and are converted with `Decimal(value)`; a non-string, invalid decimal, NaN, or infinity raises ValueError naming the field.
- `entry_start`, `entry_end`, `liquidation_start`, and `liquidation_deadline` are offset-free `HH:MM[:SS]` strings and are converted with `time.fromisoformat(value)`; a non-string, invalid time, or value containing a UTC offset raises ValueError naming the field.
- `max_quote_age` and `label_exit_lateness` are positive TOML integers interpreted as seconds; `holding_period` is a positive TOML integer interpreted as minutes. Reject `bool` explicitly because it is an `int` subclass.
- All remaining known fields are passed through unchanged. StrategyConfig.__post_init__ performs exact-type checks, so `load_config` never guesses or coerces their types.

Use these conversion tables and implementation:

```python
from decimal import InvalidOperation
from typing import Any


_DECIMAL_FIELDS = frozenset({
    "max_spread_fraction", "spread_floor", "estimated_round_trip_cost",
    "premium_risk_fraction", "daily_loss_fraction", "drawdown_fraction",
})
_TIME_FIELDS = frozenset({
    "entry_start", "entry_end", "liquidation_start", "liquidation_deadline",
})
_DURATION_FIELDS = {
    "max_quote_age": "seconds",
    "label_exit_lateness": "seconds",
    "holding_period": "minutes",
}


def _coerce_config_value(name: str, value: Any) -> Any:
    if name in _DECIMAL_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a decimal string")
        try:
            converted = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f"{name} must be a valid decimal string") from error
        if not converted.is_finite():
            raise ValueError(f"{name} must be a finite decimal string")
        return converted
    if name in _TIME_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be an ISO local-time string")
        try:
            converted = time.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{name} must be an ISO local-time string") from error
        if converted.tzinfo is not None:
            raise ValueError(f"{name} must not include a UTC offset")
        return converted
    if name in _DURATION_FIELDS:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return timedelta(**{_DURATION_FIELDS[name]: value})
    return value
```

StrategyConfig.__post_init__ must first reject `paper_only is not True`, non-exact scalar types, non-time or offset-bearing time values, and every non-finite Decimal before comparison. Configuration may only preserve or tighten the approved envelope: `min_dte >= 7`, `max_dte <= 21`, `0.40 <= min_abs_delta <= max_abs_delta <= 0.60`, `max_spread_fraction <= 0.08`, `spread_floor <= 0.05`, `estimated_round_trip_cost >= 1.00`, `max_quote_age <= 5 seconds`, `label_exit_lateness <= 60 seconds`, `entry_start >= 10:00`, `entry_end <= 15:00`, `liquidation_start <= 15:35`, `liquidation_deadline <= 15:40`, `entry_start < entry_end < liquidation_start < liquidation_deadline`, `holding_period <= 30 minutes`, `max_contracts == 1`, `1 <= max_entries_per_day <= 3`, `premium_risk_fraction <= 0.005`, `daily_loss_fraction <= 0.01`, `drawdown_fraction <= 0.05`, and `adverse_cost_return >= 0.005`. DTE, durations, counts, spread floor, and fractions remain positive. A future design revision, not a TOML edit, is required to loosen a hard guard.

Implement `config_snapshot(config)` in config.py by converting dataclass fields to a new dictionary in field order: Decimal to fixed-point strings, time to ISO strings, timedelta to integer total seconds, and primitive values unchanged. Implement `config_hash(config)` by serializing that snapshot with `json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)`, UTF-8 encoding, and returning the SHA-256 hexadecimal digest. Add tests showing snapshot repeatability and that mutating the returned copy cannot mutate StrategyConfig, hash repeatability, a changed hash for a tighter valid setting, and rejection of every attempted loosening plus `"NaN"`, `"Infinity"`, and wrong TOML scalar types.

- [ ] **Step 7: Export the stable public surface**

Create OptionsLab/src/options_lab/__init__.py and re-export only the domain types and StrategyConfig/load_config/config_snapshot/config_hash. Set __all__ explicitly so adapters cannot accidentally couple to private implementation details.

- [ ] **Step 8: Run tests and commit**

Run:

```bash
cd OptionsLab
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest tests/test_domain.py tests/test_config.py -v
```

Expected: all tests pass.

Commit:

```bash
git add .gitignore OptionsLab
git commit -m "feat(options-lab): add domain and configuration core"
```

---

### Task 2: Quote quality and executable-return labels

**Files:**
- Create: OptionsLab/src/options_lab/labels.py
- Create: OptionsLab/tests/test_labels.py
- Modify: OptionsLab/src/options_lab/__init__.py

**Interfaces:**
- Consumes: OptionQuote and StrategyConfig.
- Produces: NetReturnLabel and build_net_return_label(entry, exit_quote, session, config).

- [ ] **Step 1: Write failing label tests**

Create tests covering ask-to-bid arithmetic, configured costs, config-hash binding, symbol mismatch, premature/late exit, stale source timestamps, outside-RTH observations, missing exit quote, and an invalid config argument type. The primary test is:

```python
from datetime import timedelta
from decimal import Decimal

from options_lab.config import StrategyConfig
from options_lab.labels import build_net_return_label
from tests.factories import valid_quote, valid_session


def test_label_uses_entry_ask_and_exit_bid_after_costs() -> None:
    entry = valid_quote(ask=Decimal("5.10"), bid=Decimal("5.00"))
    exit_quote = valid_quote(
        observed_at=entry.observed_at + timedelta(minutes=30),
        bid=Decimal("5.60"),
        ask=Decimal("5.70"),
    )
    label = build_net_return_label(
        entry,
        exit_quote,
        valid_session(),
        StrategyConfig(),
    )
    assert label is not None
    assert label.net_return == (Decimal("560") - Decimal("510") - Decimal("1")) / Decimal("510")
```

- [ ] **Step 2: Run the test to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_labels.py -v

Expected: FAIL because options_lab.labels does not exist.

- [ ] **Step 3: Implement label creation**

Create the immutable NetReturnLabel with symbol, entry_time, exit_time, entry_ask, exit_bid, per_contract_cost, net_return, and strategy_config_hash. Its constructor rejects an empty symbol, naive/reversed times, non-Decimal or non-finite/negative money, non-positive entry ask/exit bid, a non-finite net return, and an invalid config hash. Implement:

```python
def build_net_return_label(
    entry: OptionQuote,
    exit_quote: OptionQuote | None,
    session: ExchangeSession | None,
    config: StrategyConfig,
) -> NetReturnLabel | None:
    """Build an executable ask-to-bid round-trip return label.

    :param    entry:              Entry-time option quote.
    :param    exit_quote:         Exit-time quote, or None when unavailable.
    :param    session:            Authoritative XNYS session, or None on a closed date.
    :param    config:             Validated target/cost/quality configuration.
    :returns:                     A label, or None when the exit is absent, stale, or outside RTH.
    :raises   ValueError:         If config/session has the wrong type or symbols differ.
    """
    if not isinstance(config, StrategyConfig):
        raise ValueError("config must be a StrategyConfig")
    if session is not None and not isinstance(session, ExchangeSession):
        raise ValueError("session must be an ExchangeSession or None")
    if exit_quote is None or session is None or not session.is_regular:
        return None
    if entry.symbol != exit_quote.symbol:
        raise ValueError("Entry and exit quotes must describe the same option")
    hard_flags = {"stale", "crossed", "missing", "unentitled", "invalid"}
    if hard_flags.intersection(entry.quality_flags) or hard_flags.intersection(
        exit_quote.quality_flags
    ):
        return None
    if (
        entry.observed_at - entry.source_at > config.max_quote_age
        or exit_quote.observed_at - exit_quote.source_at > config.max_quote_age
    ):
        return None
    elapsed = exit_quote.observed_at - entry.observed_at
    if (
        elapsed < config.holding_period
        or elapsed > config.holding_period + config.label_exit_lateness
    ):
        return None
    if not (
        session.opens_at <= entry.observed_at < session.closes_at
        and session.opens_at <= exit_quote.observed_at < session.closes_at
    ):
        return None
    entry_notional = entry.ask * Decimal("100")
    exit_notional = exit_quote.bid * Decimal("100")
    net_return = (
        exit_notional - entry_notional - config.estimated_round_trip_cost
    ) / entry_notional
    return NetReturnLabel(
        symbol=entry.symbol,
        entry_time=entry.observed_at,
        exit_time=exit_quote.observed_at,
        entry_ask=entry.ask,
        exit_bid=exit_quote.bid,
        per_contract_cost=config.estimated_round_trip_cost,
        net_return=net_return,
        strategy_config_hash=config_hash(config),
    )
```

Import `config_hash` and ExchangeSession. Add explicit tests showing a hard quality flag, stale entry or exit source timestamp, an exit after config.label_exit_lateness, an observation outside the supplied session, a non-regular session, and `session=None` all return None. The future data/LEAN adapters must obtain sessions from their authoritative exchange calendar; the core never guesses weekdays, holidays, or early closes.

- [ ] **Step 4: Export, run tests, and commit**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_labels.py tests/test_domain.py -v

Expected: all tests pass.

Commit:

```bash
git add OptionsLab
git commit -m "feat(options-lab): add executable option return labels"
```

---

### Task 3: Stable features and deterministic candidate ranking

**Files:**
- Create: OptionsLab/src/options_lab/features.py
- Create: OptionsLab/src/options_lab/candidates.py
- Create: OptionsLab/tests/conftest.py
- Create: OptionsLab/tests/test_features.py
- Create: OptionsLab/tests/test_candidates.py
- Modify: OptionsLab/src/options_lab/__init__.py

**Interfaces:**
- Consumes: DecisionContext, MarketFeatures, OptionQuote, StrategyConfig, virtual equity.
- Produces: FEATURE_SCHEMA_VERSION, FEATURE_SCHEMA, FEATURE_SCHEMA_ID, FeatureVector, build_feature_vector(context, quote), freeze_feature_vector(symbol, values), CandidateRejection, CandidateSet, select_candidates(context, virtual_equity, config).

- [ ] **Step 1: Write failing feature tests**

Assert exact key order, call/put encoding, log moneyness, DTE, spread fraction, and rejection of non-finite output:

```python
from datetime import date, datetime, timezone
from decimal import Decimal

from options_lab.domain import DecisionContext, MarketFeatures
from options_lab.features import (
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_ID,
    FEATURE_SCHEMA_VERSION,
    build_feature_vector,
)
from tests.factories import valid_quote, valid_session


def test_feature_vector_has_stable_schema() -> None:
    observed_at = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)
    market = MarketFeatures(
        observed_at=observed_at,
        source_at=observed_at,
        source="fixture",
        quality_flags=(),
        spy_price=Decimal("650"),
        return_1m=0.001,
        return_5m=0.002,
        return_15m=-0.003,
        return_30m=0.004,
        realized_vol_5m=0.10,
        realized_vol_15m=0.12,
        realized_vol_30m=0.14,
        distance_from_vwap=0.002,
        volume=1_500_000,
        volume_zscore=1.2,
        underlying_spread_fraction=0.0001,
        minutes_since_open=30,
        minutes_until_close=360,
    )
    quote = valid_quote(expiration=date(2026, 9, 18), strike=Decimal("650"))
    context = DecisionContext(
        decision_time=observed_at,
        session=valid_session(),
        market=market,
        options=(quote,),
        feed_class="fixture",
        raw_data_manifest_hash="a" * 64,
    )
    vector = build_feature_vector(context, quote)
    assert tuple(vector) == FEATURE_SCHEMA
    assert FEATURE_SCHEMA_VERSION == "options-lab-spy-option-v1"
    assert len(FEATURE_SCHEMA_ID) == 64
    assert all(type(value) is float for value in vector.values())
    assert vector["is_call"] == 1.0
    assert vector["log_moneyness"] == 0.0
```

- [ ] **Step 2: Write failing candidate tests**

Cover every eligibility boundary and tie-breaker. Assert that the selected call is closest to 0.50 delta, then tightest spread, then nearest expiration; assert that missing/non-regular sessions and quotes outside 7-21 DTE, stale beyond five seconds, over risk cap, too wide, or wrong-time are rejected with stable reason strings.

- [ ] **Step 3: Run tests to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_features.py tests/test_candidates.py -v

Expected: FAIL because features and candidates modules do not exist.

- [ ] **Step 4: Implement the feature vector**

Define FEATURE_SCHEMA as the minimum version-one model schema below. It deliberately uses the universally available subset of the approved feature catalog; quote quality and eligibility remain enforced before the predictor. Any later optional Greeks, sizes, open interest, or option-return fields require a new schema tuple and therefore a new FEATURE_SCHEMA_ID rather than silently changing this model input.

```python
FEATURE_SCHEMA_VERSION = "options-lab-spy-option-v1"
FEATURE_SCHEMA = (
    "return_1m", "return_5m", "return_15m", "return_30m",
    "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    "distance_from_vwap", "volume", "volume_zscore",
    "underlying_spread_fraction", "minutes_since_open",
    "minutes_until_close", "is_call", "log_moneyness", "dte",
    "bid", "ask", "spread_fraction", "quote_age_seconds", "delta",
    "implied_volatility",
)

FEATURE_SCHEMA_ID = sha256(
    (FEATURE_SCHEMA_VERSION + "\x1e" + "\x1f".join(FEATURE_SCHEMA)).encode("ascii")
).hexdigest()
```

Import sha256 from hashlib and log from math. `build_feature_vector(context, quote) -> dict[str, float]` returns an insertion-ordered dictionary with exactly these keys and every value explicitly converted to float; Decimal bid/ask/price-derived values use `float(value)`, integer volume/minutes/DTE use `float(value)`, and existing numeric features use `float(value)` only after rejecting Boolean/non-numeric input. Compute log_moneyness as log(spy_price / strike), DTE from context.session.session_date to expiration, is_call as 1.0/0.0, spread_fraction as (ask - bid) / midpoint, and quote_age_seconds as `(context.decision_time - quote.source_at).total_seconds()`. Raise ValueError if context.session is None, the quote is not a member of the context, a source/observation is later than decision_time, the quote does not belong to session_date, or any output value is non-finite. Any change to a field definition, unit, ordering, or transformation must increment FEATURE_SCHEMA_VERSION so the ID changes even if field names do not.

Define immutable FeatureVector with `symbol: str`, `feature_schema: str`, and `values: tuple[float, ...]`. It requires a non-empty symbol, exact FEATURE_SCHEMA_ID, exactly len(FEATURE_SCHEMA) finite non-Boolean float values, and stores no mutable mapping. `freeze_feature_vector(symbol, values)` requires `tuple(values) == FEATURE_SCHEMA` and constructs FeatureVector from values in schema order. This is the per-candidate representation retained in DecisionResult and audit records.

- [ ] **Step 5: Implement candidate eligibility and ranking**

Create the immutable result types below. Their tuple fields preserve deterministic audit/replay order:

```python
@dataclass(frozen=True, slots=True)
class CandidateRejection:
    """This class represents all eligibility failures for one option quote."""

    symbol: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """This class represents deterministic option eligibility and selection."""

    call: OptionQuote | None
    put: OptionQuote | None
    eligible: tuple[OptionQuote, ...]
    rejections: tuple[CandidateRejection, ...]
    global_rejections: tuple[str, ...]
```

Validate non-empty rejection symbols/reasons, unique symbols, and that call/put have the corresponding right and appear in eligible. Implement this stable ranking key:

```python
def _rank_key(quote: OptionQuote, session_date: date) -> tuple[float, Decimal, int, str]:
    spread_fraction = (quote.ask - quote.bid) / quote.midpoint
    dte = (quote.expiration - session_date).days
    return (abs(abs(quote.delta) - 0.50), spread_fraction, dte, quote.symbol)
```

Accumulate global rejections in this order: `invalid_virtual_equity` when virtual equity is not a finite positive Decimal and `market_session_missing` when context.session is None. If the session is missing, return immediately without dereferencing it. Otherwise continue with `non_regular_market_session` when it is an early/other non-regular session; `decision_outside_market_session` when decision_time is not inside `[opens_at, closes_at)`; `market_not_available_at_decision` when market.observed_at is later than decision_time; `market_too_old` when decision_time - market.source_at exceeds max_quote_age; `market_from_different_session` when either market source/observation timestamp is outside `[opens_at, closes_at)`; `outside_entry_window` when decision_time converted to New York is outside the inclusive configured entry window; then one `disqualifying_market_quality_flag:<flag>` for each sorted member of `{stale, crossed, missing, unentitled, invalid}` present in market quality_flags. If any global rejection exists, score no quote. Otherwise inspect quotes in symbol order and accumulate per-contract reasons in this order: `dte_out_of_range`, `delta_out_of_range`, `delta_right_mismatch`, `quote_not_available_at_decision`, `quote_too_old`, `quote_from_different_session` when either quote timestamp is outside that same session interval, `spread_too_wide`, `premium_risk_limit`, and one `disqualifying_quality_flag:<flag>` for each sorted member of the same hard-failure flag set present in quality_flags. Eligibility uses source quote age relative to context.decision_time, observation availability, the supplied session_date, spread <= max(spread_floor, max_spread_fraction * midpoint), and `ask * 100 + estimated_round_trip_cost <= virtual_equity * premium_risk_fraction`. Keep every zero-reason quote in eligible; select one per right with `_rank_key(quote, context.session.session_date)`. Return all rejection reasons rather than silently dropping contracts.

- [ ] **Step 6: Add shared decision fixtures**

Create OptionsLab/tests/conftest.py. Define typed pytest fixtures named `config`, `valid_market`, `valid_session`, `valid_context`, and `valid_candidates`. Use tests.factories.valid_session for the 2026-09-04 regular XNYS session; a UTC decision/observation/source time of 14:00; source `fixture`; no quality flags; a SPY price of 650; the market values from the feature test; feed class `fixture`; manifest hash `"a" * 64`; and one 2026-09-18 call plus one 2026-09-18 put from tests.factories. The put must use symbol SPY260918P00650000, OptionRight.PUT, and delta -0.50. Build `valid_candidates` by calling `select_candidates(valid_context, Decimal("200000"), config)` and assert both selected sides are non-None before returning it.

- [ ] **Step 7: Run tests and commit**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_features.py tests/test_candidates.py -v

Expected: all tests pass.

Commit:

```bash
git add OptionsLab
git commit -m "feat(options-lab): add features and option candidate ranking"
```

---

### Task 4: Predictor-neutral call/put/cash decision policy

**Files:**
- Create: OptionsLab/src/options_lab/decision.py
- Create: OptionsLab/tests/test_decision.py
- Modify: OptionsLab/src/options_lab/__init__.py

**Interfaces:**
- Consumes: DecisionContext, CandidateSet, StrategyConfig, Predictor protocol.
- Produces: DecisionResult and choose_action(context, candidates, predictor, config).

- [ ] **Step 1: Write failing decision tests**

Use a deterministic fake predictor and cover call wins, put wins, below-threshold cash, adverse-cost cash, equal-score stable tie, a training cutoff at or after decision time, schema mismatch, and non-finite prediction. The main test is:

```python
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from options_lab.decision import choose_action
from options_lab.domain import Action
from options_lab.config import config_hash
from options_lab.features import FEATURE_SCHEMA_ID


@dataclass(frozen=True)
class FakePredictor:
    """This class provides deterministic expected returns for policy tests."""

    model_id: str
    model_hash: str
    strategy_config_hash: str
    feature_schema: str
    decision_threshold: float
    training_cutoff: datetime
    values: dict[str, float]

    def predict(self, features: Mapping[str, float], symbol: str) -> float:
        """Return the configured prediction for a symbol.

        :param    features: The validated feature vector, unused by this fake.
        :param    symbol:   Contract symbol to score.
        :returns:           The configured expected net return.
        :raises   KeyError: If the symbol has no configured prediction.
        """
        return self.values[symbol]


def test_choose_action_selects_higher_net_call(valid_context, valid_candidates, config) -> None:
    predictor = FakePredictor(
        model_id="model-1",
        model_hash="b" * 64,
        strategy_config_hash=config_hash(config),
        feature_schema=FEATURE_SCHEMA_ID,
        decision_threshold=0.01,
        training_cutoff=valid_context.decision_time - timedelta(days=1),
        values={
            valid_candidates.call.symbol: 0.04,
            valid_candidates.put.symbol: 0.02,
        },
    )
    result = choose_action(valid_context, valid_candidates, predictor, config)
    assert result.action is Action.BUY_CALL
    assert result.intent is not None
    assert result.intent.limit_price == valid_candidates.call.ask
```

- [ ] **Step 2: Run test to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_decision.py -v

Expected: FAIL because options_lab.decision does not exist.

- [ ] **Step 3: Implement predictor protocol and decision result**

Define a runtime-checkable Predictor Protocol with `model_id: str`, `model_hash: str`, `strategy_config_hash: str`, `feature_schema: str`, `decision_threshold: float`, `training_cutoff: datetime`, and `predict(self, features: Mapping[str, float], symbol: str) -> float`. Define DecisionResult exactly as follows:

```python
@dataclass(frozen=True, slots=True)
class DecisionResult:
    """This class represents the complete output of one decision evaluation."""

    action: Action
    intent: TradeIntent | None
    feature_vectors: tuple[FeatureVector, ...]
    predictions: tuple[Prediction, ...]
    reason: str

    @classmethod
    def cash(
        cls,
        reason: str,
        feature_vectors: tuple[FeatureVector, ...] = (),
        predictions: tuple[Prediction, ...] = (),
    ) -> "DecisionResult":
        """Build a fail-closed cash result.

        :param    reason:      Stable reason code for taking no position.
        :param    feature_vectors: Per-candidate vectors built before rejection.
        :param    predictions: Predictions completed before the rejection.
        :returns:              A cash decision with no trade intent.
        :raises   ValueError:  If reason is empty.
        """
        if not reason:
            raise ValueError("Cash decision reason cannot be empty")
        return cls(Action.CASH, None, feature_vectors, predictions, reason)
```

DecisionResult.__post_init__ rejects an empty reason, duplicate feature/prediction symbols, a prediction without a same-symbol feature vector, CASH with an intent, a buy without an intent, and a buy action whose right does not match the intent.

Implement choose_action with these exact rules:

```python
if not isinstance(context, DecisionContext) or not isinstance(candidates, CandidateSet):
    return DecisionResult.cash("invalid_decision_inputs")
if not isinstance(config, StrategyConfig):
    return DecisionResult.cash("invalid_strategy_config")

model_id = getattr(predictor, "model_id", None)
model_hash = getattr(predictor, "model_hash", None)
artifact_config_hash = getattr(predictor, "strategy_config_hash", None)
feature_schema = getattr(predictor, "feature_schema", None)
decision_threshold = getattr(predictor, "decision_threshold", None)
training_cutoff = getattr(predictor, "training_cutoff", None)

if feature_schema != FEATURE_SCHEMA_ID:
    return DecisionResult.cash("feature_schema_mismatch")
if artifact_config_hash != config_hash(config):
    return DecisionResult.cash("strategy_config_mismatch")
if (
    not isinstance(training_cutoff, datetime)
    or training_cutoff.tzinfo is None
    or training_cutoff.utcoffset() is None
    or training_cutoff >= context.decision_time
):
    return DecisionResult.cash("invalid_training_cutoff")
if (
    not isinstance(model_id, str)
    or not model_id
    or not isinstance(model_hash, str)
    or re.fullmatch(r"[0-9a-f]{64}", model_hash) is None
    or isinstance(decision_threshold, bool)
    or not isinstance(decision_threshold, (int, float))
    or not isfinite(decision_threshold)
    or decision_threshold < 0
):
    return DecisionResult.cash("invalid_predictor_metadata")

minimum_edge = float(decision_threshold) + config.adverse_cost_return
scored: list[tuple[Prediction, OptionQuote]] = []
feature_vectors: list[FeatureVector] = []
for quote in (candidates.call, candidates.put):
    if quote is None:
        continue
    try:
        features = build_feature_vector(context, quote)
        frozen_features = freeze_feature_vector(quote.symbol, features)
    except (TypeError, ValueError):
        return DecisionResult.cash(
            "feature_construction_error",
            feature_vectors=tuple(feature_vectors),
            predictions=tuple(prediction for prediction, _ in scored),
        )
    feature_vectors.append(frozen_features)
    try:
        expected = predictor.predict(features, quote.symbol)
    except Exception:
        return DecisionResult.cash(
            "predictor_error",
            feature_vectors=tuple(feature_vectors),
            predictions=tuple(prediction for prediction, _ in scored),
        )
    if (
        isinstance(expected, bool)
        or not isinstance(expected, (int, float))
        or not isfinite(expected)
    ):
        return DecisionResult.cash(
            "non_finite_prediction",
            feature_vectors=tuple(feature_vectors),
            predictions=tuple(prediction for prediction, _ in scored),
        )
    prediction = Prediction(
        symbol=quote.symbol,
        expected_net_return=float(expected),
        model_id=model_id,
        model_hash=model_hash,
        strategy_config_hash=artifact_config_hash,
        training_cutoff=training_cutoff,
        feature_schema=feature_schema,
    )
    scored.append((prediction, quote))

if not scored:
    return DecisionResult.cash(
        "no_eligible_contract", feature_vectors=tuple(feature_vectors)
    )
prediction, quote = max(
    scored,
    key=lambda item: (
        item[0].expected_net_return,
        item[1].right is OptionRight.CALL,
    ),
)
predictions = tuple(item[0] for item in scored)
if prediction.expected_net_return <= minimum_edge:
    return DecisionResult.cash(
        "expected_return_below_cost_buffer",
        feature_vectors=tuple(feature_vectors),
        predictions=predictions,
    )

action = Action.BUY_CALL if quote.right is OptionRight.CALL else Action.BUY_PUT
intent = TradeIntent(
    symbol=quote.symbol,
    right=quote.right,
    quantity=1,
    limit_price=quote.ask,
    premium=quote.ask * Decimal("100"),
    expected_net_return=prediction.expected_net_return,
    decision_time=context.decision_time,
    model_id=model_id,
    model_hash=model_hash,
    strategy_config_hash=artifact_config_hash,
)
return DecisionResult(
    action=action,
    intent=intent,
    feature_vectors=tuple(feature_vectors),
    predictions=predictions,
    reason="highest_expected_return_after_cost_buffer",
)
```

Import re plus every referenced type/function. Do not catch BaseException. The explicit feature-construction boundary makes a mismatched context/candidate fail closed, while predictor exceptions and Boolean/non-numeric/NaN/infinite outputs can never produce an intent. Tests assert every stable failure reason in the code above.

- [ ] **Step 4: Run tests and commit**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_decision.py tests/test_features.py tests/test_candidates.py -v

Expected: all tests pass.

Commit:

```bash
git add OptionsLab
git commit -m "feat(options-lab): add call put or cash decision policy"
```

---

### Task 5: Deterministic fail-closed risk engine

**Files:**
- Create: OptionsLab/src/options_lab/risk.py
- Create: OptionsLab/tests/test_risk.py
- Modify: OptionsLab/tests/conftest.py
- Modify: OptionsLab/src/options_lab/__init__.py

**Interfaces:**
- Consumes: TradeIntent, PortfolioState, ExchangeSession, StrategyConfig.
- Produces: RiskEngine.evaluate(intent, state, session) -> RiskDecision with an ordered RiskCheck ledger.

- [ ] **Step 1: Write parameterized failing risk tests**

Extend OptionsLab/tests/conftest.py with `valid_intent` and `valid_state` fixtures. The intent is one call at a $5.10 limit, $510 premium, model ID `model-1`, model hash `"b" * 64`, strategy config hash `config_hash(config)`, and context.decision_time. The state uses $200,000 for virtual, session-start, and high-watermark equity; zero P&L, entries, and open quantity; no pending entry/exit or kill switch; and true connected/broker-reconciled/data-healthy/model-valid flags.

Parameterize every rejection independently: kill switch, disconnected broker, unreconciled broker state, unhealthy data, invalid model, pending entry/exit, open position, quantity not one, config hash mismatch, premium above 0.5%, three entries, -1% conservative daily P&L, 5% drawdown, non-positive equity, missing/non-regular/closed session, and decision outside the configured entry window. Test the exact configured inequalities: premium plus costs exactly at its cap is accepted, while daily loss and drawdown exactly at their cutoffs are rejected.

```python
from dataclasses import replace

import pytest

from options_lab.risk import RiskEngine


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"kill_switch": True}, "kill_switch"),
        ({"connected": False}, "broker_disconnected"),
        ({"broker_reconciled": False}, "broker_unreconciled"),
        ({"data_healthy": False}, "market_data_unhealthy"),
        ({"model_valid": False}, "model_invalid"),
        ({"pending_entry": True}, "entry_already_pending"),
        ({"pending_exit": True}, "exit_already_pending"),
        ({"open_quantity": 1}, "position_already_open"),
        ({"entries_today": 3}, "daily_entry_limit"),
    ],
)
def test_risk_engine_rejects_operational_failure(
    valid_intent, valid_state, valid_session, config, change, reason
) -> None:
    state = replace(valid_state, **change)
    result = RiskEngine(config).evaluate(valid_intent, state, valid_session)
    assert result.approved is False
    assert result.reason == reason
    assert result.intent is None
    assert result.checks[-1].name == reason
    assert result.checks[-1].passed is False
```

- [ ] **Step 2: Run tests to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_risk.py -v

Expected: FAIL because options_lab.risk does not exist.

- [ ] **Step 3: Implement risk checks in stable precedence**

RiskEngine.evaluate must check operational integrity before financial limits so one input always yields one stable primary reason. For each check reached, append `RiskCheck(name, passed)`; return immediately after appending the first failure. Use this order:

```python
checks = (
    (state.kill_switch, "kill_switch"),
    (not state.connected, "broker_disconnected"),
    (not state.broker_reconciled, "broker_unreconciled"),
    (not state.data_healthy, "market_data_unhealthy"),
    (not state.model_valid, "model_invalid"),
    (state.pending_entry, "entry_already_pending"),
    (state.pending_exit, "exit_already_pending"),
    (state.open_quantity != 0, "position_already_open"),
    (intent.quantity != 1, "quantity_not_one"),
    (state.entries_today >= config.max_entries_per_day, "daily_entry_limit"),
)
```

Before the tuple above, reject malformed inputs in this stable order: any non-finite money as `invalid_money`; virtual/session/high-watermark equity less than or equal to zero as `non_positive_equity`; high_watermark below virtual_equity as `invalid_high_watermark`; negative entries/open quantity as `invalid_position_count`; both pending flags true as `contradictory_order_state`; mismatched `intent.strategy_config_hash != config_hash(config)` as `strategy_config_mismatch`; and mismatched `intent.premium != intent.limit_price * Decimal("100") * intent.quantity` as `invalid_intent_premium`. After the operational tuple, reject session None as `market_session_missing`, a non-regular session as `non_regular_market_session`, and a decision time outside `[session.opens_at, session.closes_at)` as `market_closed`; then reject a decision local time outside the inclusive configured entry window as `outside_entry_window`. Convert the aware decision timestamp with `ZoneInfo("America/New_York")`; never compare a UTC wall clock directly with the configured local times. Then calculate:

```python
premium_cap = state.virtual_equity * config.premium_risk_fraction
premium_at_risk = intent.premium + config.estimated_round_trip_cost * intent.quantity
conservative_daily_pnl = state.session_realized_pnl + min(state.marked_unrealized_pnl, Decimal("0"))
daily_loss_limit = -(state.session_start_equity * config.daily_loss_fraction)
drawdown = (state.high_watermark - state.virtual_equity) / state.high_watermark
```

Reject premium_at_risk above cap as `premium_risk_limit`, conservative P&L at or below the daily loss limit as `daily_loss_limit`, and drawdown at or above the drawdown limit as `drawdown_limit`, in that order. Approve with reason `approved` by returning the original immutable intent and the all-passing ledger. Every rejection returns `intent=None` plus the ledger through its first failed check. Tests must assert exact check-name order for one approval and for failures at the first, middle, and last guard.

- [ ] **Step 4: Run tests and commit**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_risk.py -v

Expected: all tests pass.

Commit:

```bash
git add OptionsLab
git commit -m "feat(options-lab): enforce deterministic paper risk limits"
```

---

### Task 6: Shared exit policy and position-preserving order lifecycle

**Files:**
- Create: OptionsLab/src/options_lab/exits.py
- Create: OptionsLab/src/options_lab/lifecycle.py
- Create: OptionsLab/tests/test_exits.py
- Create: OptionsLab/tests/test_lifecycle.py
- Modify: OptionsLab/tests/conftest.py
- Modify: OptionsLab/src/options_lab/__init__.py

**Interfaces:**
- Consumes: ExchangeSession through ExitContext, StrategyConfig, LifecycleState, and LifecycleEvent.
- Produces: evaluate_exit(context, config) -> ExitInstruction and transition(state, event) -> LifecycleState.

- [ ] **Step 1: Write failing exit-policy tests**

Extend OptionsLab/tests/conftest.py with `valid_exit_context`: `session` is the valid regular XNYS session, `entry_filled_at` is the valid market timestamp, `now` is 29 minutes later, a position is open, no entry/exit order is pending, data/broker/model flags are healthy, and both halt flags are false. Cover flat, pending-entry, pending-exit, and open states; just before/exactly at the 30-minute holding horizon; every deterministic safety flag; just before/exactly at 15:35; exactly at/after 15:40; an authoritative early close; a missing session; unknown entry time; broker mismatch; and naive timestamps. Prove that the effective liquidation window moves earlier on an early close, that liquidation/safety halts cancel a pending entry, and that an existing pending exit never causes a duplicate exit submission. The main timing test is:

```python
from dataclasses import replace
from datetime import timedelta

from options_lab.exits import ExitMode, evaluate_exit


def test_holding_horizon_requests_bid_limit_exit(valid_exit_context, config) -> None:
    context = replace(
        valid_exit_context,
        now=valid_exit_context.entry_filled_at + timedelta(minutes=30),
    )
    instruction = evaluate_exit(context, config)
    assert instruction.mode is ExitMode.LIMIT_AT_BID
    assert instruction.reason == "holding_period_elapsed"
    assert instruction.cancel_pending_entry is False
```

- [ ] **Step 2: Run the exit test to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_exits.py -v

Expected: FAIL because options_lab.exits does not exist.

- [ ] **Step 3: Implement one broker-neutral exit policy**

Define ExitMode values NONE, RECONCILE_ONLY, LIMIT_AT_BID, RECONCILE_THEN_LIMIT, and FORCE_RECONCILE_AND_LIQUIDATE. Define immutable ExitContext with `now`, `session: ExchangeSession | None`, `entry_filled_at: datetime | None`, `has_position`, `pending_entry`, `pending_exit`, `data_healthy`, `broker_reconciled`, `model_valid`, `daily_loss_halted`, and `kill_switch`. Define immutable ExitInstruction with `mode`, stable `reason`, and `cancel_pending_entry`.

Both dataclasses validate exact Boolean types and aware timestamps. For a supplied session, construct the configured start/deadline on session.session_date in America/New_York, then compute `effective_start = min(configured_start, session.closes_at - timedelta(minutes=25))` and `effective_deadline = min(configured_deadline, session.closes_at - timedelta(minutes=20))`. This preserves the regular 15:35/15:40 policy and advances it for an early close. evaluate_exit applies this stable precedence:

1. If session is None, set cancel_pending_entry to pending_entry and return FORCE_RECONCILE_AND_LIQUIDATE with `market_session_unknown` for an open position/pending exit, RECONCILE_ONLY with that reason for an unreconciled broker or pending entry, and otherwise NONE with that reason.
2. At or after effective_deadline, set cancel_pending_entry to pending_entry and return FORCE_RECONCILE_AND_LIQUIDATE with `liquidation_deadline` whenever broker state is unreconciled, a position is believed open, or an exit is pending; if broker state is reconciled and flat after any entry cancellation, return RECONCILE_ONLY with `liquidation_entry_cancellation` when an entry was pending and otherwise NONE with `no_open_position`.
3. If broker_reconciled is false before the deadline, set cancel_pending_entry to pending_entry. Return RECONCILE_ONLY with `broker_state_mismatch` when an exit is already pending, RECONCILE_THEN_LIMIT when a position is open without a pending exit, and otherwise RECONCILE_ONLY.
4. At or after effective_start, set cancel_pending_entry to pending_entry. Return RECONCILE_ONLY with `liquidation_exit_pending` for a pending exit, RECONCILE_THEN_LIMIT with `liquidation_window` for an open position, RECONCILE_ONLY with `liquidation_entry_cancellation` for a pending entry, and otherwise NONE with `no_open_position`.
5. Select the first active safety reason in this order: `market_data_unhealthy`, `model_invalid`, `daily_loss_limit`, `kill_switch`. Set cancel_pending_entry to pending_entry. For a pending exit return RECONCILE_ONLY; for an open position return RECONCILE_THEN_LIMIT when data is unhealthy and LIMIT_AT_BID otherwise; for no position return RECONCILE_ONLY when canceling an entry and NONE otherwise. Preserve the selected safety reason in every case.
6. If no position is open, return NONE with `entry_pending` when an entry is pending and `no_open_position` otherwise.
7. If an exit is already pending, return NONE with `exit_already_pending` so no duplicate exit is submitted.
8. If entry_filled_at is absent, return RECONCILE_THEN_LIMIT with `unknown_entry_time`.
9. At or after entry_filled_at + holding_period, return LIMIT_AT_BID with `holding_period_elapsed`.
10. Otherwise return NONE with `holding_period_active`.

Reject `entry_filled_at > now`, a supplied entry time outside the supplied session, pending_entry together with a position/pending_exit, pending_exit without a position, and a non-None entry_filled_at when both position and pending_exit are false. FORCE_RECONCILE_AND_LIQUIDATE never means sending a blind sell quantity: the adapter must first query actual broker holdings, cancel incompatible orders, and then use the brokerage-supported liquidation action needed to flatten that reconciled long position. This function chooses policy only; adapters own quote retrieval, bounded limit replacement, execution, and alerts, but they may not alter these triggers or precedence.

- [ ] **Step 4: Write failing lifecycle transition tests**

Test the complete entry/exit path, entry and exit rejection paths, forbidden duplicate entries, and a halt during every phase. Prove that a halt preserves whether an entry is pending, a position is open, or an exit is pending, and that an open halted state still permits exit submission/fill:

```python
import pytest

from options_lab.lifecycle import LifecycleEvent, LifecyclePhase, LifecycleState, transition


def test_halt_preserves_open_position_and_allows_exit() -> None:
    state = LifecycleState(LifecyclePhase.OPEN)
    state = transition(state, LifecycleEvent.HALT)
    assert state == LifecycleState(LifecyclePhase.OPEN, halted=True)
    state = transition(state, LifecycleEvent.EXIT_SUBMITTED)
    state = transition(state, LifecycleEvent.EXIT_FILLED)
    assert state == LifecycleState(LifecyclePhase.FLAT, halted=True)


def test_halted_flat_state_forbids_entry() -> None:
    with pytest.raises(ValueError, match="Invalid lifecycle transition"):
        transition(
            LifecycleState(LifecyclePhase.FLAT, halted=True),
            LifecycleEvent.ENTRY_SUBMITTED,
        )
```

- [ ] **Step 5: Run the lifecycle test to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_lifecycle.py -v

Expected: FAIL because options_lab.lifecycle does not exist.

- [ ] **Step 6: Implement a closed, position-preserving transition table**

Define LifecyclePhase values FLAT, ENTRY_PENDING, OPEN, and EXIT_PENDING. Define immutable LifecycleState with `phase` and `halted=False`. Define LifecycleEvent values ENTRY_SUBMITTED, ENTRY_FILLED, ENTRY_CANCELED, ENTRY_REJECTED, EXIT_SUBMITTED, EXIT_FILLED, EXIT_CANCELED, EXIT_REJECTED, and HALT. Use this phase table:

```python
_PHASE_TRANSITIONS = {
    (LifecyclePhase.FLAT, LifecycleEvent.ENTRY_SUBMITTED): LifecyclePhase.ENTRY_PENDING,
    (LifecyclePhase.ENTRY_PENDING, LifecycleEvent.ENTRY_FILLED): LifecyclePhase.OPEN,
    (LifecyclePhase.ENTRY_PENDING, LifecycleEvent.ENTRY_CANCELED): LifecyclePhase.FLAT,
    (LifecyclePhase.ENTRY_PENDING, LifecycleEvent.ENTRY_REJECTED): LifecyclePhase.FLAT,
    (LifecyclePhase.OPEN, LifecycleEvent.EXIT_SUBMITTED): LifecyclePhase.EXIT_PENDING,
    (LifecyclePhase.EXIT_PENDING, LifecycleEvent.EXIT_FILLED): LifecyclePhase.FLAT,
    (LifecyclePhase.EXIT_PENDING, LifecycleEvent.EXIT_CANCELED): LifecyclePhase.OPEN,
    (LifecyclePhase.EXIT_PENDING, LifecycleEvent.EXIT_REJECTED): LifecyclePhase.OPEN,
}
```

HALT sets halted=True without changing phase. ENTRY_SUBMITTED is invalid while halted; cancellation, fill, and every exit event remain governed by the table so safety exits can finish. No event clears halted. Resumption requires an adapter to reconcile broker state and explicitly construct the verified LifecycleState; unknown pairs raise ValueError. Because version one permits exactly one contract, there is no fractional/partial quantity state: an unfilled order remains pending and a filled contract is OPEN.

- [ ] **Step 7: Run tests and commit**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_exits.py tests/test_lifecycle.py -v

Expected: all tests pass.

Commit:

```bash
git add OptionsLab
git commit -m "feat(options-lab): add shared exit policy and safe lifecycle"
```

---

### Task 7: Deterministic JSONL audit records and core verification

**Files:**
- Create: OptionsLab/src/options_lab/audit.py
- Create: OptionsLab/tests/test_audit.py
- Modify: OptionsLab/tests/conftest.py
- Modify: OptionsLab/src/options_lab/__init__.py
- Create: OptionsLab/README.md

**Interfaces:**
- Consumes: domain dataclasses, decisions, risk decisions, lifecycle state.
- Produces: AuditedModelMetadata, DecisionRecord, canonical_json(record), JsonlAuditWriter.append(record).

- [ ] **Step 1: Write failing canonical serialization tests**

Extend OptionsLab/tests/conftest.py with `valid_record`, using the existing valid context/candidates/intent/state, frozen feature vectors for both selected candidates, a successful RiskDecision and its typed check ledger, the pre-order LifecycleState FLAT, FEATURE_SCHEMA_ID, config_snapshot(config), config_hash(config), 64-character lowercase hexadecimal raw-manifest/model hashes, a 40-character lowercase hexadecimal Git commit, and source timestamps for `spy_trade` and both option quotes. Assert stable key order, UTC ISO-8601 datetimes, decimal strings, enum values, rejection of naive/non-finite/credential-like data, one JSON object per line, and an os.fsync call. Include:

```python
import json

from options_lab.audit import JsonlAuditWriter, canonical_json


def test_canonical_json_is_repeatable(valid_record) -> None:
    first = canonical_json(valid_record)
    second = canonical_json(valid_record)
    assert first == second
    assert json.loads(first)["final_action"] in {"cash", "buy_call", "buy_put"}
    assert "api_key" not in first.lower()


def test_writer_appends_exactly_one_record_per_line(tmp_path, valid_record) -> None:
    path = tmp_path / "decisions.jsonl"
    writer = JsonlAuditWriter(path)
    writer.append(valid_record)
    writer.append(valid_record)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
```

- [ ] **Step 2: Run test to verify failure**

Run: cd OptionsLab && .venv/bin/python -m pytest tests/test_audit.py -v

Expected: FAIL because options_lab.audit does not exist.

- [ ] **Step 3: Implement canonical audit serialization**

Define immutable AuditedModelMetadata with `model_id: str | None`, `model_hash: str | None`, `training_cutoff: str | None`, `feature_schema: str | None`, `strategy_config_hash: str | None`, and `validation_reason: str`. The first five fields deliberately preserve raw, possibly invalid predictor metadata as strings so a schema/config mismatch, malformed hash, or naive cutoff can still be audited; validation_reason must be non-empty.

Use these concrete DecisionRecord field types:

```python
ConfigScalar = bool | int | float | str


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """This class represents one immutable pre-order decision audit record."""

    record_version: int
    decision_id: str
    decision_time: datetime
    exchange_session: ExchangeSession | None
    source_timestamps: Mapping[str, datetime]
    raw_data_manifest_hash: str
    strategy_config: Mapping[str, ConfigScalar]
    strategy_config_hash: str
    feed_class: str
    feature_schema: str
    feature_vectors: tuple[FeatureVector, ...]
    eligible_symbols: tuple[str, ...]
    rejected_candidates: tuple[CandidateRejection, ...]
    candidate_global_rejections: tuple[str, ...]
    predictions: tuple[Prediction, ...]
    proposed_action: Action
    proposed_intent: TradeIntent | None
    decision_reason: str
    portfolio_state: PortfolioState
    risk_decision: RiskDecision | None
    final_action: Action
    lifecycle_state: LifecycleState
    exit_instruction: ExitInstruction | None
    model_metadata: AuditedModelMetadata
    code_commit: str
```

In __post_init__, defensively copy and sort source_timestamps and strategy_config into MappingProxyType objects so the frozen record is deeply immutable. Validate exact scalar/value types; unique eligible/rejected/vector/prediction symbols; non-empty global rejection and decision reasons; each prediction backed by a same-symbol FeatureVector; non-empty identifiers; exchange-session consistency when supplied; aware source timestamps no later than decision_time; 64-character lowercase hexadecimal manifest/config hashes; a strategy-config snapshot whose recomputed canonical SHA-256 equals strategy_config_hash; FEATURE_SCHEMA_ID; Prediction/TradeIntent config hashes equal to the record hash; and a 40- or 64-character lowercase hexadecimal Git commit. Empty feature_vectors/predictions are valid for a no-trade result rejected before scoring. Do not require a valid model hash on this audit type; valid Prediction/TradeIntent objects already enforce it, while invalid raw metadata must remain recordable. A proposed CASH action requires no proposed intent/risk decision and a CASH final action. A proposed buy requires an intent and RiskDecision; approval requires final_action equal proposed_action, while rejection requires final_action CASH. `exit_instruction` is None for an entry-time record and populated on an exit evaluation.

canonical_json recursively converts finite Decimal values to fixed-point strings, aware datetime values to UTC ISO-8601 strings ending in Z, dates to ISO strings, StrEnum values to strings, tuples to arrays, any collections.abc.Mapping (including MappingProxyType) to a plain dictionary, and dataclasses via fields rather than lossy string conversion. It rejects naive datetimes, non-finite Decimal/float values, non-string mapping keys, and any case-insensitive mapping key containing `api_key`, `secret`, `token`, or `password`. Serialize with `sort_keys=True`, `separators=(",", ":")`, and `allow_nan=False`.

JsonlAuditWriter.append creates the parent directory, encodes `canonical_json(record) + "\n"` once as UTF-8, opens the file with `os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)`, performs exactly one `os.write`, verifies the returned byte count equals the payload length, calls `os.fsync`, and closes the descriptor in finally. It never rewrites earlier records. Add a concurrency test with a ThreadPoolExecutor that appends 100 distinct records and proves all 100 lines parse and all decision IDs survive exactly once.

This core record captures every fact available before/order-policy evaluation, including source provenance and the raw-data manifest. The later broker-adapter plans add append-only OrderEventRecord and FillEventRecord types keyed by decision_id for order IDs, replacements, fills, final exit reason, and realized P&L; those broker facts are never fabricated or folded into an earlier immutable decision record.

- [ ] **Step 4: Add a concise package README**

Document the package boundary, Python setup, full test command, immutable interface rule, paper-only constraint, and the fact that no model or broker is included in this core plan. Include this exact quick start:

```bash
cd OptionsLab
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

- [ ] **Step 5: Run the complete core verification**

Run:

```bash
cd OptionsLab
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests
```

Expected: all tests pass and compileall exits 0.

- [ ] **Step 6: Verify the public boundary and diff**

Run:

```bash
rg -n "alpaca|QuantConnect|AlgorithmImports|pandas|numpy|sklearn" OptionsLab/src/options_lab
git diff --check
git status --short
```

Expected: rg has no matches in the core package; diff check is clean; only intended OptionsLab files are changed.

- [ ] **Step 7: Commit the verified core**

```bash
git add OptionsLab
git commit -m "feat(options-lab): add deterministic decision audit trail"
```

## Completion Gate

This plan is complete only when:

- Every core unit test passes on Python 3.11 or newer.
- Every invalid-data and risk boundary fails closed with a stable reason.
- The decision package has no LEAN, Alpaca, data-science, or network dependency.
- The public API is exported explicitly from options_lab.__init__.
- Audit output is deterministic and contains no credential-like field.
- The working tree is clean after the final commit.

After this gate, create separate implementation plans in dependency order for: point-in-time data and model training; the LEAN adapter and quote-aware backtests; the Alpaca shadow/paper runner; and licensed news/social shadow ingestion. Each plan consumes the frozen interfaces produced here.
