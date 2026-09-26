"""Recheck retained current context against fresh admitted source owners."""

from dataclasses import dataclass, fields, replace
from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from ._validation import _trusted_datetime
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember, verify_fixture_bundle
from .bundle_inputs import _make
from .calibration_inputs import _matches_retained, _retained_fixture_shape
from .config import StrategyConfig
from .context import ContextComponent, DecisionContext, _Row, _normalize, build_decision_context
from .context_inputs import ContextRequest, ContextRequestValidation, normalize_context_request
from .features import FeatureState, update_features
from .bars import UnderlyingBar
from .sessions import ExchangeSession


@dataclass(frozen=True, init=False)
class ContextRecheck:
    """This class represents a fresh source-backed replay of one retained context."""

    original_context: DecisionContext
    context: DecisionContext | None
    manifest: VerifiedFixtureManifest | None
    request: ContextRequestValidation | None
    previous_feature_state: FeatureState | None
    reasons: tuple[str, ...]
    rejection: object | None

    def __init__(self) -> None:
        """Prevent callers from writing their own recheck result.

        :returns: None.
        :raises TypeError: Always; use recheck_decision_context.
        """
        raise TypeError("ContextRecheck values come from recheck_decision_context")

    @property
    def valid(self) -> bool:
        """Report whether the complete retained context was freshly reproduced.

        :returns: True only when no recheck requirement failed.
        """
        return self.context is not None and not self.reasons


def _result(original, *, context=None, manifest=None, request=None, previous=None,
            reasons=(), rejection=None):
    """Retain only evidence reached before the first failed prerequisite."""
    return _make(ContextRecheck, original_context=original, context=context,
                 manifest=manifest, request=request, previous_feature_state=previous,
                 reasons=tuple(reasons), rejection=rejection)


def _standard_time(value):
    """Accept only safe exact aware instants before any time comparison."""
    return type(value) is datetime and type(value.tzinfo) in (timezone, ZoneInfo)


def _config(old):
    """Reconstruct a trusted config only after finite exact leaf preflight."""
    try:
        template = StrategyConfig()
        for value, expected in ((old, template), (object.__getattribute__(old, "execution"),
                                                  template.execution)):
            if type(value) is not type(expected):
                return None
            for field in fields(type(expected)):
                leaf = object.__getattribute__(value, field.name)
                ordinary = object.__getattribute__(expected, field.name)
                if field.name == "initial_virtual_equity":
                    if leaf is not None and type(leaf) is not Decimal:
                        return None
                elif type(leaf) is not type(ordinary):
                    return None
                if type(leaf) is Decimal and not leaf.is_finite():
                    return None
                if type(leaf) is time and leaf.tzinfo is not None:
                    return None
        fresh = replace(old, execution=replace(old.execution))
        return fresh if _matches_retained(old, fresh, set()) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _request(old):
    """Re-normalize retained valid request facts without repairing unknown raw errors."""
    try:
        if type(old) is not ContextRequestValidation:
            return None
        header, ids, errors = (object.__getattribute__(old, name) for name in
                               ("value", "member_record_ids", "rejections"))
        event, ref, received = (object.__getattribute__(old, name) for name in
                                ("event_id", "raw_ref", "received_at"))
        if (type(header) is not ContextRequest or type(ids) is not tuple or len(ids) > 4096
                or any(type(item) is not str or not item for item in ids)
                or type(errors) is not tuple or errors or type(event) is not str or not event
                or type(ref) is not str or not ref or not _standard_time(received)):
            return None
        decision_id = object.__getattribute__(header, "decision_id")
        decision_at = object.__getattribute__(header, "decision_at")
        if type(decision_id) is not str or not decision_id or not _standard_time(decision_at):
            return None
        fresh = normalize_context_request(dict(decision_id=decision_id,
            decision_at=decision_at.isoformat(), member_record_ids=list(ids)),
            event_id=event, raw_ref=ref, received_at=received)
        return fresh if not fresh.rejections and _matches_retained(old, fresh, set()) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _previous(old, original, manifest):
    """Rebuild retained P06 state from actual context member sources and event order."""
    try:
        if type(old) is not FeatureState:
            return None
        session = object.__getattribute__(old, "session")
        bars = object.__getattribute__(old, "bars")
        retired = object.__getattribute__(old, "retired_bars")
        as_of = object.__getattribute__(old, "as_of")
        components = object.__getattribute__(original, "components")
        if (type(session) is not ExchangeSession or type(bars) is not tuple
                or type(retired) is not tuple or len(bars) + len(retired) > 4096
                or any(type(bar) is not UnderlyingBar for bar in (*bars, *retired))
                or not _standard_time(as_of) or as_of > original.decision_at
                or type(components) is not tuple or len(components) > 4096
                or any(type(component) is not ContextComponent for component in components)):
            return None
        members = {member.record_id: member for member in manifest.members}
        profiles = {row["profile_id"]: row for row in manifest.decode_modeled_source_profiles()}
        rows = []
        for component in components:
            member = object.__getattribute__(component, "member")
            if type(member) is not VerifiedFixtureMember:
                return None
            record_id = object.__getattribute__(member, "record_id")
            if type(record_id) is not str:
                return None
            fresh = members.get(record_id)
            if fresh is None or fresh.kind not in ("exchange_session", "underlying_bar"):
                continue
            row = _Row(fresh, fresh.decode_envelope(), fresh.decode_raw_body(), False)
            _normalize(row, profiles[fresh.profile_id])
            if not row.reasons and not row.failures and row.available_at is not None and row.available_at <= as_of:
                rows.append(row)
        def match(value):
            matching = [row for row in rows if _matches_retained(value, row.value, set())]
            return min(matching, key=lambda row: row.member.record_id) if matching else None
        actual_session = match(session)
        actual_bars = [match(value) for value in (*bars, *retired)]
        if (actual_session is None or actual_session.member.kind != "exchange_session"
                or any(row is None or row.member.kind != "underlying_bar" for row in actual_bars)
                or len({row.member.record_id for row in actual_bars}) != len(actual_bars)):
            return None
        state = FeatureState(actual_session.value, as_of=as_of)
        for row in sorted(actual_bars, key=lambda row: (row.available_at,
                row.envelope["receive_sequence"] is None,
                row.envelope["receive_sequence"] or 0, row.member.record_id)):
            update = update_features(state, row.value, actual_session.value, as_of=as_of)
            if update.outcome not in ("accepted", "corrected"):
                return None
            state = update.next_state
        return state if _matches_retained(old, state, set()) else None
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError, MemoryError, RecursionError):
        return None


def recheck_decision_context(context: DecisionContext, *, config: StrategyConfig,
                             now: datetime) -> ContextRecheck:
    """Re-admit actual sources and reproduce every retained current context fact.

    This proves source and reducer identity. It does not turn partial market
    readiness into entry authorization.

    :param context: Exact owner-produced context with retained request and prior.
    :param config: Exact current strategy configuration.
    :param now: Actual decision instant, equal to the context decision time.
    :returns: Fresh complete context or bounded reached-owner reasons.
    :raises TypeError: If a trusted top-level argument has a wrong exact type.
    :raises ValueError: If now is not a standard aware instant.
    """
    if type(context) is not DecisionContext or type(config) is not StrategyConfig or type(now) is not datetime:
        raise TypeError("context, config and now require exact trusted types")
    if not _standard_time(now):
        raise ValueError("now must have a standard aware timezone")
    now = _trusted_datetime("now", now)
    try:
        decision_at = object.__getattribute__(context, "decision_at")
        old_request = object.__getattribute__(context, "request")
        old_manifest = object.__getattribute__(context, "manifest")
        old_previous = object.__getattribute__(context, "previous_feature_state")
    except AttributeError:
        return _result(context, reasons=("retained_context_mismatch",))
    if not _standard_time(decision_at) or now != decision_at:
        return _result(context, reasons=("context_time_mismatch",))
    checked_config = _config(config)
    if checked_config is None:
        return _result(context, reasons=("retained_config_mismatch",))
    request = _request(old_request)
    if request is None:
        return _result(context, reasons=("retained_request_mismatch",))
    if not _retained_fixture_shape(old_manifest):
        return _result(context, request=request, reasons=("retained_manifest_mismatch",))
    inspected = verify_fixture_bundle(old_manifest.fixture_id, old_manifest.payload_bytes,
        event_id=old_manifest.event_id, raw_ref=old_manifest.raw_ref,
        received_at=old_manifest.received_at)
    if inspected.value is None:
        return _result(context, request=request, reasons=("fresh_manifest_rejected",),
                       rejection=inspected.rejection)
    manifest = inspected.value
    if not _matches_retained(old_manifest, manifest, set()):
        return _result(context, request=request, manifest=manifest,
                       reasons=("retained_manifest_mismatch",))
    previous = None
    if old_previous is not None:
        previous = _previous(old_previous, context, manifest)
        if previous is None:
            return _result(context, request=request, manifest=manifest,
                           reasons=("previous_state_replay_failed",))
    fresh = build_decision_context(request, manifest, config=checked_config,
                                   previous_feature_state=previous).context
    if fresh is None or not _matches_retained(context, fresh, set()):
        return _result(context, context=fresh, request=request, manifest=manifest,
                       previous=previous, reasons=("retained_context_mismatch",))
    return _result(context, context=fresh, request=request, manifest=manifest, previous=previous)
