"""Choose an actual bundle for one freshly checked activation instant."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .bundle_availability import BundleAssessment, _prerequisites, _result as _availability_result
from .bundle_inputs import _make
from .bundles import BundleRejection, BundleVerification, VerifiedBundle
from .config import StrategyConfig
from .context import DecisionContext


@dataclass(frozen=True, init=False)
class ActivationSelection:
    """This class represents a pure choice with both reached bundle assessments."""

    original_current: VerifiedBundle | None
    candidate_verification: BundleVerification
    candidate_assessment: BundleAssessment | None
    current_assessment: BundleAssessment | None
    selected: VerifiedBundle | None
    disposition: str
    reasons: tuple[str, ...]

    def __init__(self) -> None:
        """Block caller-selected activation outcomes.

        :returns: None.
        :raises TypeError: Always; use choose_bundle_activation.
        """
        raise TypeError("ActivationSelection values come from choose_bundle_activation")


def _first_gap(bundle, now):
    """Check the declared gap against the first actual open in its block."""
    gap, block = bundle.activation_gap, bundle.evaluation_block
    if gap is None or block is None or not gap.starts_at <= now < gap.ends_at:
        return False
    first = next((row.session for row in block.calendar.sessions
                  if block.starts_at <= row.session.opens_at < block.ends_at), None)
    return (first is not None and gap.next_session == first.session_date
            and gap.ends_at == first.opens_at and gap.calendar.calendar == block.calendar.calendar)


def _assessment(original, context, config, now, current):
    """Reuse B's one prerequisite group and add the actual D horizon."""
    fresh, checked, calibration, available_at, reasons, rejection = _prerequisites(
        original, context, config, now)
    if fresh is not None and checked is not None and checked.valid:
        block = fresh.evaluation_block
        eligible = (_first_gap(fresh, now) or (current and block is not None
                    and block.starts_at <= now < block.ends_at))
        if not eligible:
            reasons.append("outside_current_horizon" if current else "outside_first_activation_gap")
    return _availability_result(original, config, now, "core_fixture", bundle=fresh,
        context_recheck=checked, calibration=calibration, available_at=available_at,
        reasons=reasons, rejection=rejection)


def _calendar_facts(block):
    """Compare XNYS dates and hours within an interval, independent of owner names."""
    calendar = block.calendar
    start = block.starts_at.astimezone(ZoneInfo("America/New_York")).date()
    end = (block.ends_at - timedelta(microseconds=1)).astimezone(
        ZoneInfo("America/New_York")).date()
    return (calendar.calendar, tuple((row.session.session_date, row.session.kind,
        row.session.opens_at, row.session.closes_at) for row in calendar.sessions
        if start <= row.session.session_date <= end),
        tuple(day for day in calendar.closed_dates if start <= day <= end))


def _same_interval(left, right):
    """Lock a healthy current against a renamed challenger on identical XNYS facts."""
    a, b = left.evaluation_block, right.evaluation_block
    return (a is not None and b is not None and a.starts_at == b.starts_at
            and a.ends_at == b.ends_at and _calendar_facts(a) == _calendar_facts(b))


def choose_bundle_activation(current: VerifiedBundle | None, candidate: BundleVerification, *,
        context: DecisionContext, config: StrategyConfig, now: datetime) -> ActivationSelection:
    """Select one fresh bundle during its first actual gap or retain healthy current.

    :param current: Retained current verified bundle, or None.
    :param candidate: Actual verification outcome, including a possible native rejection.
    :param context: Actual current P08 source context.
    :param config: Exact strategy configuration.
    :param now: Actual aware decision instant matching the context.
    :returns: Pure selection and both reached assessments; None means cash fallback.
    :raises TypeError: If a top-level trusted input has the wrong exact type.
    :raises ValueError: If now lacks a standard aware timezone.
    """
    if ((current is not None and type(current) is not VerifiedBundle)
            or type(candidate) is not BundleVerification or type(context) is not DecisionContext
            or type(config) is not StrategyConfig or type(now) is not datetime):
        raise TypeError("current, candidate, context, config and now require exact types")
    if type(now.tzinfo) not in (timezone, ZoneInfo):
        raise ValueError("now must have a standard aware timezone")
    try:
        value = object.__getattribute__(candidate, "value")
        rejection = object.__getattribute__(candidate, "rejection")
        valid = ((type(value) is VerifiedBundle and rejection is None)
                 or (value is None and type(rejection) is BundleRejection))
    except AttributeError:
        value, valid = None, False
    candidate_assessment = _assessment(value, context, config, now, False) if valid and value is not None else None
    current_assessment = _assessment(current, context, config, now, True) if current is not None else None
    current_ok = current_assessment is not None and current_assessment.available
    candidate_ok = candidate_assessment is not None and candidate_assessment.available
    if current_ok and (not candidate_ok or current_assessment.bundle.bundle_hash == candidate_assessment.bundle.bundle_hash
                       or _same_interval(current_assessment.bundle, candidate_assessment.bundle)):
        selected, disposition = current_assessment.bundle, "current_retained"
    elif candidate_ok:
        selected, disposition = candidate_assessment.bundle, "candidate_selected"
    elif current_ok:
        selected, disposition = current_assessment.bundle, "current_retained"
    else:
        selected, disposition = None, "cash_fallback"
    reasons = ([] if valid and value is not None else
               ["candidate_rejected" if valid else "candidate_verification_invalid"])
    for assessment in (candidate_assessment, current_assessment):
        if assessment is not None:
            reasons.extend(assessment.reasons)
    return _make(ActivationSelection, original_current=current, candidate_verification=candidate,
        candidate_assessment=candidate_assessment, current_assessment=current_assessment,
        selected=selected, disposition=disposition, reasons=tuple(dict.fromkeys(reasons)))
