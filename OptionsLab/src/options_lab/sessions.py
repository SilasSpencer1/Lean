"""Typed session evidence and pure current-session assessment."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .config import StrategyConfig, config_hash as _config_hash
from .contracts import ContractId


SessionKind = Literal["regular", "early_close", "closed", "unknown"]
InstrumentStatus = Literal["tradable", "closed", "halted", "unknown"]
AvailabilityBasis = Literal["measured", "assumed"]
Fidelity = Literal["genuine", "synthetic", "unknown"]
RecoveryBasis = Literal["common_hours", "instrument_hours_only", "unknown"]

_SESSION_KINDS = ("regular", "early_close", "closed", "unknown")
_INSTRUMENT_STATUSES = ("tradable", "closed", "halted", "unknown")
_AVAILABILITY_BASES = ("measured", "assumed")
_FIDELITIES = ("genuine", "synthetic", "unknown")
_NY = ZoneInfo("America/New_York")
_UTC = timezone.utc


@dataclass(frozen=True)
class ExchangeSession:
    """This class represents supplied strategy-calendar session evidence."""

    calendar: str
    session_date: date
    kind: SessionKind
    opens_at: datetime | None
    closes_at: datetime | None
    source: str
    provider_record_id: str
    source_version: str
    available_at: datetime | None
    availability_basis: AvailabilityBasis
    received_at: datetime
    raw_ref: str
    fidelity: Fidelity

    def __post_init__(self) -> None:
        """
        Validate representation while retaining adverse session claims.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact type.
        :raises   ValueError: If a string, token, or timestamp is invalid.
        """
        _require_nonempty_string("calendar", self.calendar)
        if type(self.session_date) is not date:
            raise TypeError("session_date must be a date")
        _require_token("kind", self.kind, _SESSION_KINDS)
        _normalize_optional_datetimes(self, "opens_at", "closes_at", "available_at")
        for name in ("source", "provider_record_id", "source_version", "raw_ref"):
            _require_nonempty_string(name, getattr(self, name))
        _require_token("availability_basis", self.availability_basis, _AVAILABILITY_BASES)
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        _require_token("fidelity", self.fidelity, _FIDELITIES)

    @property
    def is_regular(self) -> bool:
        """
        Return whether the retained source classified the session as regular.

        :returns: True only for the exact ``regular`` source claim.
        """
        return self.kind == "regular"


@dataclass(frozen=True)
class InstrumentTradability:
    """This class represents supplied instrument hours and operability evidence."""

    contract: ContractId | None
    instrument_ref: str
    session_date: date
    opens_at: datetime | None
    closes_at: datetime | None
    status: InstrumentStatus
    effective_from: datetime | None
    effective_until: datetime | None
    source: str
    provider_record_id: str
    source_version: str
    available_at: datetime | None
    availability_basis: AvailabilityBasis
    received_at: datetime
    raw_ref: str
    fidelity: Fidelity

    def __post_init__(self) -> None:
        """
        Validate representation while retaining adverse instrument claims.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact type.
        :raises   ValueError: If a string, token, or timestamp is invalid.
        """
        if self.contract is not None and type(self.contract) is not ContractId:
            raise TypeError("contract must be a ContractId or None")
        _require_nonempty_string("instrument_ref", self.instrument_ref)
        if type(self.session_date) is not date:
            raise TypeError("session_date must be a date")
        _normalize_optional_datetimes(
            self, "opens_at", "closes_at", "effective_from", "effective_until", "available_at"
        )
        _require_token("status", self.status, _INSTRUMENT_STATUSES)
        for name in ("source", "provider_record_id", "source_version", "raw_ref"):
            _require_nonempty_string(name, getattr(self, name))
        _require_token("availability_basis", self.availability_basis, _AVAILABILITY_BASES)
        object.__setattr__(self, "received_at", _trusted_datetime("received_at", self.received_at))
        _require_token("fidelity", self.fidelity, _FIDELITIES)


@dataclass(frozen=True)
class SessionAssessment:
    """This class represents immutable derived current-session timing evidence."""

    session: ExchangeSession | None
    tradability: InstrumentTradability | None
    config: StrategyConfig
    now: datetime
    config_hash: str = field(init=False)
    session_reasons: tuple[str, ...] = field(init=False)
    instrument_hours_reasons: tuple[str, ...] = field(init=False)
    operability_reasons: tuple[str, ...] = field(init=False)
    entry_reasons: tuple[str, ...] = field(init=False)
    common_opens_at: datetime | None = field(init=False)
    common_closes_at: datetime | None = field(init=False)
    configured_entry_start: datetime | None = field(init=False)
    configured_entry_end: datetime | None = field(init=False)
    effective_liquidation_start: datetime | None = field(init=False)
    effective_liquidation_escalation: datetime | None = field(init=False)
    effective_liquidation_deadline: datetime | None = field(init=False)
    recovery_basis: RecoveryBasis = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive all bounded timing evidence.

        :returns:             None.
        :raises   TypeError:  If a retained input has the wrong exact type.
        :raises   ValueError: If ``now`` is not an aware representable instant.
        """
        if self.session is not None and type(self.session) is not ExchangeSession:
            raise TypeError("session must be an ExchangeSession or None")
        if self.tradability is not None and type(self.tradability) is not InstrumentTradability:
            raise TypeError("tradability must be an InstrumentTradability or None")
        if type(self.config) is not StrategyConfig:
            raise TypeError("config must be a StrategyConfig")
        object.__setattr__(self, "now", _trusted_datetime("now", self.now))
        object.__setattr__(self, "config_hash", _config_hash(self.config))
        _derive_assessment(self)

    @property
    def entry_timing_suitable(self) -> bool:
        """
        Return whether current bounded session timing checks passed.

        This property does not establish full entry eligibility, source
        admission, feature readiness, account readiness, or order authority.

        :returns: True only when ``entry_reasons`` is empty.
        """
        return not self.entry_reasons

    @property
    def liquidation_due(self) -> bool:
        """
        Return whether the derived liquidation-start clock has been reached.

        :returns: True at or after a known effective liquidation start.
        """
        return self.effective_liquidation_start is not None and self.now >= self.effective_liquidation_start

    @property
    def escalation_due(self) -> bool:
        """
        Return whether the derived escalation clock has been reached.

        :returns: True at or after a known effective escalation time.
        """
        return self.effective_liquidation_escalation is not None and self.now >= self.effective_liquidation_escalation

    @property
    def liquidation_deadline_reached(self) -> bool:
        """
        Return whether the derived liquidation deadline has been reached.

        :returns: True at or after a known effective deadline.
        """
        return self.effective_liquidation_deadline is not None and self.now >= self.effective_liquidation_deadline


def assess_session(
    session: ExchangeSession | None,
    tradability: InstrumentTradability | None,
    *,
    config: StrategyConfig,
    now: datetime,
) -> SessionAssessment:
    """
    Assess supplied session evidence at one explicit time.

    The pure result supplies session timing and recovery clocks only. It does
    not read a clock, infer a holiday, certify a source, or authorize an order.

    :param    session:      Calendar evidence, or None when unavailable.
    :param    tradability: Instrument evidence, or None when unavailable.
    :param    config:      Exact trusted strategy configuration.
    :param    now:         Explicit aware assessment instant.
    :returns:              Immutable derived session evidence.
    :raises   TypeError:   If a trusted argument has the wrong exact type.
    :raises   ValueError:  If ``now`` is not an aware representable instant.
    """
    return SessionAssessment(session, tradability, config, now)


def _normalize_optional_datetimes(owner: object, *names: str) -> None:
    """Normalize optional retained datetime fields to UTC in place."""
    for name in names:
        value = getattr(owner, name)
        if value is not None:
            object.__setattr__(owner, name, _trusted_datetime(name, value))


def _derive_assessment(result: SessionAssessment) -> None:
    """Derive all reason groups, intervals, and clocks from retained inputs."""
    try:
        strategy_date = result.now.astimezone(_NY).date()
    except (OverflowError, ValueError):
        _set_conversion_failure(result)
        return

    try:
        entry_start = _dated_utc(strategy_date, result.config.entry_start)
        entry_end = _dated_utc(strategy_date, result.config.entry_end)
        configured_start = _dated_utc(strategy_date, result.config.liquidation_start)
        configured_deadline = _dated_utc(strategy_date, result.config.liquidation_deadline)
        session_reasons, session_hours_usable = _session_evidence(result, strategy_date)
        instrument_reasons, instrument_hours_usable = _instrument_hours_evidence(result, strategy_date)
    except (OverflowError, ValueError):
        _set_conversion_failure(result)
        return

    operability_reasons = _operability_evidence(result)
    common_open = common_close = None
    common_usable = False
    if session_hours_usable and instrument_hours_usable:
        assert result.session is not None and result.tradability is not None
        common_open = max(result.session.opens_at, result.tradability.opens_at)
        common_close = min(result.session.closes_at, result.tradability.closes_at)
        common_usable = common_open < common_close
        if not common_usable:
            instrument_reasons.append("instrument_hours_incompatible")

    recovery_close = None
    recovery_basis: RecoveryBasis = "unknown"
    if common_usable:
        recovery_close = common_close
        recovery_basis = "common_hours"
    elif not session_hours_usable and instrument_hours_usable:
        assert result.tradability is not None
        recovery_close = result.tradability.closes_at
        recovery_basis = "instrument_hours_only"

    effective_start = effective_escalation = effective_deadline = None
    arithmetic_unsupported = False
    if recovery_close is not None:
        try:
            effective_start = min(configured_start, recovery_close - timedelta(minutes=25))
            effective_deadline = min(configured_deadline, recovery_close - timedelta(minutes=20))
            effective_escalation = effective_deadline - timedelta(minutes=1)
        except OverflowError:
            recovery_basis = "unknown"
            effective_start = effective_escalation = effective_deadline = None
            arithmetic_unsupported = True

    entry_reasons = [*session_reasons, *instrument_reasons, *operability_reasons]
    if not common_usable:
        entry_reasons.append("no_common_interval")
    elif not common_open <= result.now < common_close:
        entry_reasons.append("now_outside_common_interval")
    if not entry_start <= result.now <= entry_end:
        entry_reasons.append("outside_configured_entry_window")
    if arithmetic_unsupported:
        entry_reasons.append("time_arithmetic_unsupported")
    elif common_usable and effective_start is not None:
        try:
            required_until = result.now + (
                result.config.execution.entry_lifetime
                + result.config.execution.holding_period
                + result.config.execution.adverse_exit_latency
            )
        except OverflowError:
            entry_reasons.append("time_arithmetic_unsupported")
        else:
            if required_until > effective_start:
                entry_reasons.append("insufficient_time_before_liquidation")

    _set_fields(
        result,
        session_reasons=tuple(session_reasons),
        instrument_hours_reasons=tuple(instrument_reasons),
        operability_reasons=tuple(operability_reasons),
        entry_reasons=tuple(entry_reasons),
        common_opens_at=common_open,
        common_closes_at=common_close,
        configured_entry_start=entry_start,
        configured_entry_end=entry_end,
        effective_liquidation_start=effective_start,
        effective_liquidation_escalation=effective_escalation,
        effective_liquidation_deadline=effective_deadline,
        recovery_basis=recovery_basis,
    )


def _session_evidence(result: SessionAssessment, strategy_date: date) -> tuple[list[str], bool]:
    """Return ordered calendar reasons and whether its hours are usable."""
    value = result.session
    if value is None:
        return ["session_missing"], False
    reasons: list[str] = []
    checks = (
        (value.calendar != result.config.strategy_calendar, "calendar_unsupported"),
        (value.session_date != strategy_date, "session_wrong_date"),
        (value.available_at is None, "session_availability_unknown"),
        (value.available_at is not None and value.available_at > result.now, "session_available_after_now"),
        (value.availability_basis != "measured", "session_availability_not_measured"),
        (value.kind == "closed", "session_closed"),
        (value.kind == "unknown", "session_unknown"),
        (value.kind == "early_close", "session_not_regular"),
    )
    reasons.extend(reason for failed, reason in checks if failed)
    hours_valid = _hours_reasons(value, "session", reasons)
    blockers = set(reasons) - {"session_not_regular"}
    usable = hours_valid and not blockers and value.kind in ("regular", "early_close")
    return reasons, usable


def _instrument_hours_evidence(result: SessionAssessment, strategy_date: date) -> tuple[list[str], bool]:
    """Return ordered instrument-hours reasons and whether hours are usable."""
    value = result.tradability
    if value is None:
        return ["instrument_missing"], False
    reasons: list[str] = []
    checks = (
        (value.contract is None, "instrument_unresolved"),
        (value.session_date != strategy_date, "instrument_wrong_date"),
        (value.available_at is None, "instrument_availability_unknown"),
        (value.available_at is not None and value.available_at > result.now, "instrument_available_after_now"),
        (value.availability_basis != "measured", "instrument_availability_not_measured"),
    )
    reasons.extend(reason for failed, reason in checks if failed)
    hours_valid = _hours_reasons(value, "instrument", reasons)
    hour_blockers = {
        "instrument_wrong_date", "instrument_availability_unknown",
        "instrument_available_after_now", "instrument_availability_not_measured",
        "instrument_hours_missing", "instrument_hours_reversed",
        "instrument_hours_wrong_date",
    }
    return reasons, hours_valid and not any(reason in hour_blockers for reason in reasons)


def _hours_reasons(value: object, prefix: str, reasons: list[str]) -> bool:
    """Append ordered structural-hour reasons and return structural validity."""
    opened = getattr(value, "opens_at")
    closed = getattr(value, "closes_at")
    if opened is None or closed is None:
        reasons.append(f"{prefix}_hours_missing")
        return False
    if opened >= closed:
        reasons.append(f"{prefix}_hours_reversed")
        return False
    session_date = getattr(value, "session_date")
    if opened.astimezone(_NY).date() != session_date or closed.astimezone(_NY).date() != session_date:
        reasons.append(f"{prefix}_hours_wrong_date")
        return False
    return True


def _operability_evidence(result: SessionAssessment) -> list[str]:
    """Return ordered current-operability reasons without erasing known hours."""
    value = result.tradability
    if value is None:
        return []
    reasons: list[str] = []
    if value.effective_from is None or value.effective_until is None:
        reasons.append("operability_interval_unknown")
    elif value.effective_until <= value.effective_from:
        reasons.append("operability_interval_invalid")
    elif result.now < value.effective_from:
        reasons.append("operability_not_yet_effective")
    elif result.now >= value.effective_until:
        reasons.append("operability_expired")
    status_reasons = {
        "closed": "instrument_closed",
        "halted": "instrument_halted",
        "unknown": "instrument_status_unknown",
    }
    if value.status in status_reasons:
        reasons.append(status_reasons[value.status])
    return reasons


def _dated_utc(day: date, clock: object) -> datetime:
    """Combine a validated strategy date and clock under New York zone rules."""
    return datetime.combine(day, clock, tzinfo=_NY).astimezone(_UTC)


def _set_conversion_failure(result: SessionAssessment) -> None:
    """Store bounded evidence when a valid UTC instant cannot map to New York."""
    session_reasons = ("session_missing",) if result.session is None else ()
    instrument_reasons = ("instrument_missing",) if result.tradability is None else ()
    _set_fields(
        result,
        session_reasons=session_reasons,
        instrument_hours_reasons=instrument_reasons,
        operability_reasons=(),
        entry_reasons=(*session_reasons, *instrument_reasons, "time_conversion_unsupported"),
        common_opens_at=None,
        common_closes_at=None,
        configured_entry_start=None,
        configured_entry_end=None,
        effective_liquidation_start=None,
        effective_liquidation_escalation=None,
        effective_liquidation_deadline=None,
        recovery_basis="unknown",
    )


def _set_fields(result: SessionAssessment, **values: object) -> None:
    """Set constructor-derived fields on one frozen assessment."""
    for name, value in values.items():
        object.__setattr__(result, name, value)
