"""Typed completed underlying bars and independent component evidence."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from ._validation import _require_nonempty_string, _require_token, _trusted_datetime
from .observations import ObservationAssessment, ObservationMeta, assess_observation
from .sessions import ExchangeSession, _calendar_facts


PriceBasis = Literal[
    "raw", "split_adjusted", "total_return_adjusted", "unknown"
]

_PRICE_BASES = ("raw", "split_adjusted", "total_return_adjusted", "unknown")
_MINUTE = timedelta(minutes=1)
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class UnderlyingBar:
    """
    This class represents one trusted typed completed underlying bar.

    The record retains source-claimed close, share-volume, exact VWAP
    contribution, adjustment, revision, and receipt-order facts. It does not
    admit the source or infer unavailable values.
    """

    symbol: str
    meta: ObservationMeta
    close_price: Decimal | None
    volume: Decimal | None
    vwap_numerator: Decimal | None
    vwap_denominator: Decimal | None
    price_basis: PriceBasis
    volume_definition_id: str | None
    vwap_definition_id: str | None
    revision_id: str
    supersedes_revision_id: str | None
    receive_sequence: int | None

    def __post_init__(self) -> None:
        """
        Validate representation while retaining adverse and missing claims.

        Zero amounts, adjusted bases, partial VWAP pairs, self-supersession,
        and unknown receive order remain representable for assessment.

        :returns:             None.
        :raises   TypeError:  If a field has the wrong exact trusted type.
        :raises   ValueError: If a value is negative, nonfinite, empty, or unsupported.
        """
        _require_nonempty_string("symbol", self.symbol)
        if type(self.meta) is not ObservationMeta:
            raise TypeError("meta must be an ObservationMeta")
        for name in (
            "close_price", "volume", "vwap_numerator", "vwap_denominator"
        ):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative_decimal(name, value)
        _require_token("price_basis", self.price_basis, _PRICE_BASES)
        for name in ("volume_definition_id", "vwap_definition_id"):
            value = getattr(self, name)
            if value is not None:
                _require_nonempty_string(name, value)
        _require_nonempty_string("revision_id", self.revision_id)
        if self.supersedes_revision_id is not None:
            _require_nonempty_string(
                "supersedes_revision_id", self.supersedes_revision_id
            )
        if self.receive_sequence is not None:
            if type(self.receive_sequence) is not int:
                raise TypeError("receive_sequence must be an integer or None")
            if self.receive_sequence < 0:
                raise ValueError("receive_sequence cannot be negative")

    @property
    def interval_start(self) -> datetime | None:
        """
        Return the interval start retained by observation metadata.

        :returns: The normalized UTC start, or None when unknown.
        """
        return self.meta.interval_start

    @property
    def interval_end(self) -> datetime | None:
        """
        Return the interval end retained by observation metadata.

        :returns: The normalized UTC end, or None when unknown.
        """
        return self.meta.interval_end

    @property
    def available_at(self) -> datetime:
        """
        Return the availability time retained by observation metadata.

        :returns: The normalized UTC availability time.
        """
        return self.meta.available_at


@dataclass(frozen=True)
class BarAssessment:
    """
    This class represents bounded completed-bar and component evidence.

    Calendar, observation, close-history, ordinary-volume, and exact-VWAP
    claims remain independently visible. Positive properties do not establish
    source admission, complete feature readiness, or trading authority.
    """

    bar: UnderlyingBar
    session: ExchangeSession | None
    as_of: datetime
    observation: ObservationAssessment = field(init=False)
    availability_reasons: tuple[str, ...] = field(init=False)
    price_history_reasons: tuple[str, ...] = field(init=False)
    volume_reasons: tuple[str, ...] = field(init=False)
    vwap_reasons: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        """
        Validate retained inputs and derive all component evidence once.

        :returns:             None.
        :raises   TypeError:  If a trusted argument has the wrong exact type.
        :raises   ValueError: If ``as_of`` is not an aware representable instant.
        """
        if type(self.bar) is not UnderlyingBar:
            raise TypeError("bar must be an UnderlyingBar")
        if self.session is not None and type(self.session) is not ExchangeSession:
            raise TypeError("session must be an ExchangeSession or None")
        object.__setattr__(self, "as_of", _trusted_datetime("as_of", self.as_of))
        _derive_bar_assessment(self)

    @property
    def price_history_suitable(self) -> bool:
        """
        Return whether this is bounded evidence for one raw SPY close.

        :returns: True when availability, calendar, interval, and close checks pass.
        """
        return self._common_suitable and not self.price_history_reasons

    @property
    def volume_available(self) -> bool:
        """
        Return whether a defined eligible-share volume claim is available.

        Zero is available data. This does not approve the source definition.

        :returns: True when common and ordinary-volume checks pass.
        """
        return self._common_suitable and not self.volume_reasons

    @property
    def exact_vwap_available(self) -> bool:
        """
        Return whether a defined, internally consistent VWAP contribution exists.

        A known zero-over-zero pair is an available zero-volume contribution,
        not a computed zero VWAP. This does not approve its source definition.

        :returns: True when common and exact-contribution checks pass.
        """
        return self._common_suitable and not self.vwap_reasons

    @property
    def _common_suitable(self) -> bool:
        """Return whether shared availability, calendar, and interval checks pass."""
        return all(
            reason == "session_not_regular"
            for reason in self.availability_reasons
        )


def assess_underlying_bar(
    bar: UnderlyingBar,
    session: ExchangeSession | None,
    *,
    as_of: datetime,
) -> BarAssessment:
    """
    Assess one typed completed underlying bar at an explicit cutoff.

    ``as_of`` is an availability cutoff and may be later than the supplied
    authoritative session day. The function reads no clock and does not infer
    calendar hours, source admission, missing volume, or VWAP contributions.

    :param    bar:      Typed underlying bar and observation metadata.
    :param    session:  Authoritative session evidence, or None if unavailable.
    :param    as_of:    Explicit aware availability cutoff.
    :returns:           Immutable independent completed-bar evidence.
    :raises   TypeError:  If a trusted argument has the wrong exact type.
    :raises   ValueError: If ``as_of`` is not an aware representable instant.
    """
    return BarAssessment(bar, session, as_of)


def _derive_bar_assessment(result: BarAssessment) -> None:
    """Derive fixed-order common and component evidence from retained inputs."""
    bar = result.bar
    observation = assess_observation(bar.meta, decision_at=result.as_of)
    availability_reasons = list(observation.availability_reasons)

    expected_date = _expected_interval_date(bar, availability_reasons)
    if expected_date is None and result.session is not None:
        expected_date = result.session.session_date
    try:
        calendar_reasons, _ = _calendar_facts(
            result.session,
            expected_calendar="XNYS",
            expected_session_date=expected_date or date.min,
            cutoff=result.as_of,
        )
    except (OverflowError, ValueError):
        calendar_reasons = []
        _append_reason(availability_reasons, "time_conversion_unsupported")

    for reason in calendar_reasons:
        _append_reason(availability_reasons, reason)

    _append_interval_reasons(bar, result.session, availability_reasons)

    common_component_reasons = [] if bar.symbol == "SPY" else ["unsupported_symbol"]
    price_reasons = list(common_component_reasons)
    if bar.price_basis != "raw":
        price_reasons.append("price_basis_not_raw")
    if bar.close_price is None:
        price_reasons.append("close_price_missing")
    elif bar.close_price <= 0:
        price_reasons.append("close_price_nonpositive")
    if bar.meta.is_fill_forward:
        price_reasons.append("fill_forward")
    if bar.meta.quality_flags:
        price_reasons.append("quality_flags_present")
    if bar.supersedes_revision_id == bar.revision_id:
        price_reasons.append("revision_self_supersession")

    volume_reasons = list(common_component_reasons)
    if bar.volume is None:
        volume_reasons.append("volume_missing")
    if bar.volume_definition_id is None:
        volume_reasons.append("volume_definition_unknown")

    vwap_reasons = list(common_component_reasons)
    if bar.vwap_definition_id is None:
        vwap_reasons.append("vwap_definition_unknown")
    if bar.vwap_numerator is None:
        vwap_reasons.append("vwap_numerator_missing")
    if bar.vwap_denominator is None:
        vwap_reasons.append("vwap_denominator_missing")
    if bar.vwap_numerator is not None and bar.vwap_denominator is not None:
        if bar.vwap_denominator == 0 < bar.vwap_numerator:
            vwap_reasons.append("vwap_numerator_without_volume")
        elif bar.vwap_denominator > 0 and bar.vwap_numerator <= 0:
            vwap_reasons.append("vwap_numerator_nonpositive")

    object.__setattr__(result, "observation", observation)
    object.__setattr__(result, "availability_reasons", tuple(availability_reasons))
    object.__setattr__(result, "price_history_reasons", tuple(price_reasons))
    object.__setattr__(result, "volume_reasons", tuple(volume_reasons))
    object.__setattr__(result, "vwap_reasons", tuple(vwap_reasons))


def _expected_interval_date(
    bar: UnderlyingBar, reasons: list[str]
) -> date | None:
    """Return the interval's New York date or bounded conversion evidence."""
    if bar.interval_start is None:
        return None
    try:
        return bar.interval_start.astimezone(_NY).date()
    except (OverflowError, ValueError):
        _append_reason(reasons, "time_conversion_unsupported")
        return None


def _append_interval_reasons(
    bar: UnderlyingBar,
    session: ExchangeSession | None,
    reasons: list[str],
) -> None:
    """Append one-minute alignment and supplied-session containment reasons."""
    if bar.meta.kind != "interval":
        reasons.append("not_interval")
    start = bar.interval_start
    end = bar.interval_end
    if start is None or end is None or start >= end:
        return
    if end - start != _MINUTE:
        reasons.append("interval_duration_not_one_minute")
    if session is None or session.opens_at is None or session.closes_at is None:
        return
    if (start - session.opens_at) % _MINUTE or (end - session.opens_at) % _MINUTE:
        reasons.append("interval_not_minute_aligned")
    if start < session.opens_at or end > session.closes_at:
        reasons.append("interval_outside_session")


def _append_reason(reasons: list[str], reason: str) -> None:
    """Append one reason only once while retaining first-occurrence order."""
    if reason not in reasons:
        reasons.append(reason)


def _require_nonnegative_decimal(name: str, value: object) -> None:
    """Validate one exact finite nonnegative Decimal trusted value."""
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal or None")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
