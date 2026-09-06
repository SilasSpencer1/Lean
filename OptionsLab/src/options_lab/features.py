"""Immutable causal completed-bar selection state and update evidence."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ._validation import _trusted_datetime
from .bar_inputs import identify_underlying_bar
from .bars import BarAssessment, UnderlyingBar, assess_underlying_bar
from .config import _snapshot_hash
from .sessions import ExchangeSession


FeatureOutcome = Literal["accepted", "corrected", "duplicate", "rejected"]
EventOrder = tuple[datetime, int | None]


@dataclass(frozen=True, init=False)
class FeatureState:
    """
    This class represents immutable selected completed-bar state for one session.

    Public construction creates an explicit empty state. Nonempty states are
    produced only by successful reducer transitions, so callers cannot supply
    unchecked history, ordering, retired identities, or input hashes.
    """

    session: ExchangeSession
    bars: tuple[UnderlyingBar, ...]
    retired_bars: tuple[UnderlyingBar, ...]
    as_of: datetime
    last_event_order: EventOrder | None
    input_hash: str

    def __init__(self, session: ExchangeSession, *, as_of: datetime) -> None:
        """
        Create an empty causal state for one explicit session and cutoff.

        :param    session:  Exact trusted session evidence owned by this state.
        :param    as_of:    Explicit aware initial cutoff.
        :returns:           None.
        :raises   TypeError:  If ``session`` has the wrong exact type.
        :raises   ValueError: If ``as_of`` is not an aware representable instant.
        """
        if type(session) is not ExchangeSession:
            raise TypeError("session must be an ExchangeSession")
        normalized = _trusted_datetime("as_of", as_of)
        _set_state(self, session, (), (), normalized, None)


@dataclass(frozen=True, init=False)
class FeatureUpdate:
    """
    This class represents one fully derived bar-state update and its audit facts.

    The constructor recomputes the bar assessment and reducer result from the
    trusted inputs. It accepts no caller-supplied approval, outcome, reason,
    next state, history, or hash.
    """

    previous_state: FeatureState
    next_state: FeatureState
    input_bar: UnderlyingBar
    assessment: BarAssessment
    outcome: FeatureOutcome
    update_reasons: tuple[str, ...]

    def __init__(
        self,
        previous_state: FeatureState,
        input_bar: UnderlyingBar,
        session: ExchangeSession,
        *,
        as_of: datetime,
    ) -> None:
        """
        Reassess one bar and derive its immutable state transition.

        :param    previous_state:  Existing immutable same-session state.
        :param    input_bar:       Exact trusted bar arrival to process.
        :param    session:         Exact trusted session evidence for assessment.
        :param    as_of:           Explicit aware availability cutoff.
        :returns:                  None.
        :raises   TypeError:       If a trusted argument has the wrong exact type.
        :raises   ValueError:      If ``as_of`` is not an aware representable instant.
        """
        if type(previous_state) is not FeatureState:
            raise TypeError("previous_state must be a FeatureState")
        if type(input_bar) is not UnderlyingBar:
            raise TypeError("input_bar must be an UnderlyingBar")
        if type(session) is not ExchangeSession:
            raise TypeError("session must be an ExchangeSession")
        normalized = _trusted_datetime("as_of", as_of)
        assessment = assess_underlying_bar(input_bar, session, as_of=normalized)
        next_state, outcome, reasons = _reduce(
            previous_state, input_bar, session, normalized, assessment
        )
        object.__setattr__(self, "previous_state", previous_state)
        object.__setattr__(self, "next_state", next_state)
        object.__setattr__(self, "input_bar", input_bar)
        object.__setattr__(self, "assessment", assessment)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "update_reasons", reasons)


def update_features(
    state: FeatureState,
    bar: UnderlyingBar,
    session: ExchangeSession,
    *,
    as_of: datetime,
) -> FeatureUpdate:
    """
    Apply one completed-bar arrival to immutable causal feature input state.

    The returned update always retains the incoming bar and recomputed
    assessment. Rejected and duplicate arrivals retain the exact prior state.

    :param    state:    Existing immutable same-session state.
    :param    bar:      Exact trusted completed-bar arrival.
    :param    session:  Exact trusted session evidence used for assessment.
    :param    as_of:    Explicit aware availability cutoff.
    :returns:           Fully derived immutable update evidence.
    :raises   TypeError:  If a trusted argument has the wrong exact type.
    :raises   ValueError: If ``as_of`` is not an aware representable instant.
    """
    return FeatureUpdate(state, bar, session, as_of=as_of)


def _reduce(
    state: FeatureState,
    bar: UnderlyingBar,
    session: ExchangeSession,
    as_of: datetime,
    assessment: BarAssessment,
) -> tuple[FeatureState, FeatureOutcome, tuple[str, ...]]:
    """Return one deterministic state transition after trusted validation."""
    if as_of < state.as_of:
        return state, "rejected", ("state_time_regression",)
    if not assessment.price_history_suitable:
        reasons = tuple(dict.fromkeys(
            (*assessment.availability_reasons, *assessment.price_history_reasons)
        ))
        return state, "rejected", reasons
    if session != state.session:
        return state, "rejected", ("session_mismatch",)

    identity = identify_underlying_bar(bar)
    if identity.economic_content_hash is None:
        return state, "rejected", identity.economic_reasons
    incoming_key = _source_identity(bar)
    prior_hash = None
    for retained in (*state.bars, *state.retired_bars):
        if _source_identity(retained) != incoming_key:
            continue
        retained_identity = identify_underlying_bar(retained)
        if retained_identity.economic_content_hash is None:
            return state, "rejected", ("retained_identity_unsupported",)
        prior_hash = retained_identity.economic_content_hash
        break
    if prior_hash is not None:
        if prior_hash == identity.economic_content_hash:
            return state, "duplicate", ("duplicate",)
        return state, "rejected", ("source_identity_conflict",)

    ordering_reason = _ordering_reason(state.last_event_order, bar)
    if ordering_reason is not None:
        return state, "rejected", (ordering_reason,)

    selected_index = _selected_interval_index(state.bars, bar)
    if selected_index is not None:
        current = state.bars[selected_index]
        if bar.supersedes_revision_id != current.revision_id:
            return state, "rejected", ("revision_lineage_mismatch",)
        if not _same_correction_stream(current, bar):
            return state, "rejected", ("correction_stream_changed",)
        bars = state.bars[:selected_index] + (bar,) + state.bars[selected_index + 1:]
        return (
            _transition_state(
                state.session,
                bars,
                state.retired_bars + (current,),
                as_of,
                (bar.available_at, bar.receive_sequence),
            ),
            "corrected",
            (),
        )

    if bar.supersedes_revision_id is not None:
        return state, "rejected", ("revision_lineage_mismatch",)
    if state.bars and bar.interval_start < state.bars[-1].interval_start:
        return state, "rejected", ("late_backfill",)
    if state.bars and not _same_price_stream(state.bars[0], bar):
        return state, "rejected", ("price_stream_changed",)
    return (
        _transition_state(
            state.session,
            state.bars + (bar,),
            state.retired_bars,
            as_of,
            (bar.available_at, bar.receive_sequence),
        ),
        "accepted",
        (),
    )


def _ordering_reason(previous: EventOrder | None, bar: UnderlyingBar) -> str | None:
    """Return bounded evidence when one fresh identity lacks later event order."""
    if previous is None:
        return None
    previous_time, previous_sequence = previous
    if bar.available_at < previous_time:
        return "out_of_order"
    if bar.available_at > previous_time:
        return None
    if (
        previous_sequence is None
        or bar.receive_sequence is None
        or bar.receive_sequence == previous_sequence
    ):
        return "event_order_ambiguous"
    if bar.receive_sequence < previous_sequence:
        return "out_of_order"
    return None


def _source_identity(bar: UnderlyingBar) -> tuple[str, str, str]:
    """Return the accepted source-event identity required for redelivery checks."""
    return bar.meta.source, bar.meta.provider_record_id, bar.revision_id


def _same_price_stream(left: UnderlyingBar, right: UnderlyingBar) -> bool:
    """Return whether two bars claim the same raw close stream semantics."""
    return (
        left.symbol,
        left.meta.source,
        left.meta.feed_class,
        left.meta.fidelity,
        left.price_basis,
    ) == (
        right.symbol,
        right.meta.source,
        right.meta.feed_class,
        right.meta.fidelity,
        right.price_basis,
    )


def _same_correction_stream(left: UnderlyingBar, right: UnderlyingBar) -> bool:
    """Return whether a revision preserves the selected interval and price stream."""
    return (
        _same_price_stream(left, right)
        and left.interval_start == right.interval_start
        and left.interval_end == right.interval_end
    )


def _selected_interval_index(
    bars: tuple[UnderlyingBar, ...], bar: UnderlyingBar
) -> int | None:
    """Return the selected index with the incoming interval start, if present."""
    for index, retained in enumerate(bars):
        if retained.interval_start == bar.interval_start:
            return index
    return None


def _transition_state(
    session: ExchangeSession,
    bars: tuple[UnderlyingBar, ...],
    retired_bars: tuple[UnderlyingBar, ...],
    as_of: datetime,
    last_event_order: EventOrder,
) -> FeatureState:
    """Create one internally proven nonempty state from a successful transition."""
    result = object.__new__(FeatureState)
    _set_state(result, session, bars, retired_bars, as_of, last_event_order)
    return result


def _set_state(
    result: FeatureState,
    session: ExchangeSession,
    bars: tuple[UnderlyingBar, ...],
    retired_bars: tuple[UnderlyingBar, ...],
    as_of: datetime,
    last_event_order: EventOrder | None,
) -> None:
    """Set one empty or internally proven transition state and its input hash."""
    object.__setattr__(result, "session", session)
    object.__setattr__(result, "bars", bars)
    object.__setattr__(result, "retired_bars", retired_bars)
    object.__setattr__(result, "as_of", as_of)
    object.__setattr__(result, "last_event_order", last_event_order)
    object.__setattr__(result, "input_hash", _state_hash(session, bars))


def _state_hash(session: ExchangeSession, bars: tuple[UnderlyingBar, ...]) -> str:
    """Return a canonical selected-input hash excluding reducer receipt memory."""
    bar_hashes = []
    for bar in bars:
        identity = identify_underlying_bar(bar)
        if identity.economic_content_hash is None:
            raise ValueError("selected bar economic identity must be supported")
        bar_hashes.append(identity.economic_content_hash)
    snapshot = {
        "record_kind": "options_lab.feature_state_inputs",
        "feature_state_schema_version": 1,
        "session": _session_snapshot(session),
        "selected_bar_economic_hashes": bar_hashes,
    }
    return _snapshot_hash(snapshot)


def _session_snapshot(session: ExchangeSession) -> dict[str, object]:
    """Build one explicit canonical dictionary of actual retained session facts."""
    return {
        "calendar": session.calendar,
        "session_date": session.session_date.isoformat(),
        "kind": session.kind,
        "opens_at": _timestamp(session.opens_at),
        "closes_at": _timestamp(session.closes_at),
        "source": session.source,
        "provider_record_id": session.provider_record_id,
        "source_version": session.source_version,
        "available_at": _timestamp(session.available_at),
        "availability_basis": session.availability_basis,
        "received_at": _timestamp(session.received_at),
        "raw_ref": session.raw_ref,
        "fidelity": session.fidelity,
    }


def _timestamp(value: datetime | None) -> str | None:
    """Return one already-normalized UTC timestamp in canonical ISO form."""
    return None if value is None else value.isoformat()
