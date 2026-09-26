"""Assess actual source backed bundle availability at one decision instant."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .bundle_inputs import _make
from .bundle_recheck import _fresh_bundle
from .bundles import VerifiedBundle
from .calibration_support import CalibrationAssessment, _assess_fresh_calibration
from .config import StrategyConfig, config_hash, policy_hash
from .context import DecisionContext
from .context_recheck import ContextRecheck, recheck_decision_context
from .greeks import FIXTURE_GREEK_METHOD, _method_hash


_USES = ("core_fixture", "operational_paper", "economic_research")


@dataclass(frozen=True, init=False)
class BundleAssessment:
    """This class retains reached A0/A2/C/current context and causal availability proof."""

    original_bundle: VerifiedBundle
    bundle: VerifiedBundle | None
    context_recheck: ContextRecheck | None
    config: StrategyConfig
    now: datetime
    requested_use: str
    calibration: tuple[CalibrationAssessment, ...]
    available_at: datetime | None
    available: bool
    synthetic_core_fixture_capable: bool
    reasons: tuple[str, ...]
    rejection: object | None
    operational_allowed: bool
    economic_allowed: bool

    def __init__(self) -> None:
        """Block caller-selected availability authority.

        :returns: None.
        :raises TypeError: Always; use assess_bundle.
        """
        raise TypeError("BundleAssessment values come from assess_bundle")


def _result(original, config, now, requested_use, *, bundle=None, context_recheck=None,
            calibration=(), available_at=None, reasons=(), rejection=None):
    """Retain only actual reached evidence and ordered failures."""
    reasons = tuple(dict.fromkeys(reasons))
    ready = not reasons
    core_ready = not any(reason not in ("operational_use_unsupported", "economic_use_unsupported")
                         for reason in reasons)
    return _make(BundleAssessment, original_bundle=original, bundle=bundle,
        context_recheck=context_recheck, config=config, now=now, requested_use=requested_use,
        calibration=tuple(calibration), available_at=available_at, available=ready,
        synthetic_core_fixture_capable=core_ready,
        reasons=reasons, rejection=rejection, operational_allowed=False, economic_allowed=False)


def _covered_sources(bundle, context):
    """Bind every actual current input member to declared source role and profile."""
    source_ids = {row.fixture_id for row in bundle.manifest.data_manifest_hashes if row.role == "source"}
    owner = context.manifest
    if owner is None or owner.fixture_id not in source_ids:
        return False
    source = next((root for root in bundle.upstream_fixtures if root.fixture_id == owner.fixture_id), None)
    if source is None or source.payload_sha256 != owner.payload_sha256 or source.payload_bytes != owner.payload_bytes:
        return False
    if bundle.model.model_kind == "cash":
        return True
    declared = {(profile.claim.fixture_id, profile.claim.profile_id):
                {member.record_id for member in profile.members} for profile in bundle.source_profiles}
    consumed = (component for component in context.components
                if component.disposition == "selected"
                or (component.member.kind == "underlying_bar" and component.disposition == "superseded"))
    for component in consumed:
        member = component.member
        if member.record_id not in declared.get((owner.fixture_id, member.profile_id), set()):
            return False
    return bool(context.selected_components)


def _definitions(bundle, context):
    """Match actual current source definitions and fixed feature/Greek claims."""
    owner = context.manifest
    if owner is None or bundle.model.model_kind == "cash":
        return owner is not None
    binding = bundle.manifest.feature_binding
    method = owner.greek_method
    return (method == FIXTURE_GREEK_METHOD
        and binding.greek_method_spec_hash == _method_hash(method)
        and set(binding.coherence_protocol_ids) <= set(owner.coherence_protocol_ids)
        and bundle.spec is not None and bundle.normalization_result is not None
        and binding.feature_schema_id == bundle.spec.feature_schema_id
        and binding.transform_id == bundle.spec.transform_id
        and binding.normalization_hash == bundle.normalization_result.value.content_hash)


def _plus(instant, microseconds):
    """Keep modeled delay arithmetic inside representable datetime bounds."""
    if instant is None or type(microseconds) is not int or microseconds < 0:
        return None
    try:
        return instant + timedelta(microseconds=microseconds)
    except (OverflowError, ValueError):
        return None


def _prerequisites(bundle, context, config, now):
    """Assess real shared B prerequisites without a decision interval assumption.

    D can later consume this group with its actual gap context, while public B
    alone adds the current evaluation interval requirement.
    """
    reasons = []
    def need(condition, reason):
        if not condition and reason not in reasons:
            reasons.append(reason)

    fresh, failure, rejection = _fresh_bundle(bundle)
    if failure is not None:
        return fresh, None, (), None, [failure], rejection
    checked = recheck_decision_context(context, config=config, now=now)
    if not checked.valid:
        return fresh, checked, (), None, list(checked.reasons), checked.rejection
    current = checked.context
    need(config_hash(config) == config_hash(fresh.config)
         and policy_hash(config) == policy_hash(fresh.config)
         and current.config_hash == config_hash(config)
         and current.policy_hash == policy_hash(config), "config_mismatch")
    need(current.input_digest is not None and not current.input_reasons,
         "current_context_incomplete")
    need(_covered_sources(fresh, current), "current_source_profile_uncovered")
    need(_definitions(fresh, current), "current_definition_mismatch")
    need(fresh.validation_report is not None, "validation_report_missing")
    gap, block = fresh.activation_gap, fresh.evaluation_block
    need(gap is not None, "activation_gap_missing")
    need(block is not None, "evaluation_block_missing")
    if gap is not None and block is not None:
        need(gap.available_at is not None and gap.available_at <= now, "activation_gap_unavailable")
        need(block.available_at is not None and block.available_at <= now, "evaluation_block_unavailable")
        need(gap.calendar.content_hash == block.calendar.content_hash,
             "schedule_calendar_mismatch")
        need(gap.starts_at < block.starts_at < gap.ends_at <= block.ends_at,
             "activation_block_mismatch")
    assessments = ()
    if fresh.model.model_kind != "cash":
        metadata = fresh.calibration_metadata
        if metadata is None:
            need(False, "calibration_metadata_missing")
        else:
            assessments = tuple(_assess_fresh_calibration(fresh, row.bucket_id, now)
                                for row in metadata.buckets)
            need(bool(assessments) and all(item.supported for item in assessments),
                 "calibration_unsupported")
            need(all(item.bundle is fresh for item in assessments), "calibration_bundle_mismatch")
    provenance = fresh.manifest.provenance
    schedule = provenance.simulated_schedule
    if schedule is not None:
        need(provenance.simulated_available_at == schedule.simulated_available_at,
             "simulated_availability_mismatch")
        if fresh.model.model_kind == "cash":
            need(schedule.fit_cutoff is None and schedule.fit_delay_us is None
                 and schedule.deployment_delay_us is None, "cash_fit_claim")
        else:
            prediction = fresh.manifest.prediction_contract
            fit = None if prediction is None else prediction.fit_cutoff
            need(fit is not None and schedule.fit_cutoff == fit, "schedule_fit_cutoff_mismatch")
            completed = _plus(fit, schedule.fit_delay_us)
            deployed = _plus(completed, schedule.deployment_delay_us)
            need(completed is not None and deployed is not None, "schedule_delay_overflow")
            if completed is not None:
                metadata = fresh.calibration_metadata
                norm = fresh.normalization_result
                need(metadata is not None and metadata.available_at <= completed,
                     "calibration_after_fit_completion")
                need(norm is not None and norm.value.available_at <= completed,
                     "normalization_after_fit_completion")
            need(deployed == schedule.simulated_available_at, "schedule_availability_mismatch")
    available_at = None
    if provenance.availability_basis == "simulated":
        need(provenance.activated_at is None, "simulated_actual_activation_conflict")
        need(schedule is not None and provenance.simulated_available_at is not None,
             "simulated_schedule_missing")
        available_at = None if schedule is None else schedule.simulated_available_at
    else:
        available_at = provenance.activated_at
        need(provenance.activated_at is not None and provenance.promoted_at is not None,
             "actual_activation_unknown")
        if provenance.activated_at is not None and provenance.promoted_at is not None:
            need(provenance.built_at <= provenance.promoted_at <= provenance.activated_at <= now,
                 "actual_activation_order")
        if fresh.model.model_kind != "cash" and available_at is not None:
            metadata = fresh.calibration_metadata
            fit = fresh.manifest.prediction_contract.fit_cutoff
            need(metadata is not None and fit is not None and fit <= metadata.available_at <= available_at,
                 "actual_fit_order")
            need(fresh.normalization_result is not None
                 and fresh.normalization_result.value.available_at <= available_at,
                 "actual_normalization_unavailable")
    need(available_at is not None and available_at <= now, "bundle_unavailable")
    if available_at is not None and gap is not None and block is not None:
        need(gap.available_at is not None and gap.available_at <= available_at,
             "gap_after_availability")
        need(block.available_at is not None and block.available_at <= available_at,
             "block_after_availability")
        need(all(value is not None and value <= available_at for value in (
            gap.calendar.available_at, gap.previous_evidence.session.available_at,
            gap.next_evidence.session.available_at, block.calendar.available_at)),
            "schedule_source_after_availability")
        need(gap.starts_at <= available_at < gap.ends_at, "availability_outside_gap")
    return fresh, checked, assessments, available_at, reasons, rejection


def assess_bundle(bundle: VerifiedBundle, *, context: DecisionContext, config: StrategyConfig,
                  now: datetime, requested_use: str) -> BundleAssessment:
    """Assess fresh actual bundle and current context for one bounded use.

    :param bundle: Actual retained A2 content bundle.
    :param context: Original P08 current context and request.
    :param config: Current exact strategy configuration.
    :param now: Actual current context decision instant.
    :param requested_use: Exact core_fixture, operational_paper or economic_research token.
    :returns: Reached source, support and temporal evidence with ordered reasons.
    :raises TypeError: If a top-level trusted input has the wrong exact type.
    :raises ValueError: If requested use or aware decision instant is invalid.
    """
    if (type(bundle) is not VerifiedBundle or type(context) is not DecisionContext
            or type(config) is not StrategyConfig or type(now) is not datetime
            or type(requested_use) is not str):
        raise TypeError("bundle, context, config, now and requested_use require exact types")
    if type(now.tzinfo) not in (timezone, ZoneInfo):
        raise ValueError("now must have a standard aware timezone")
    if requested_use not in _USES:
        raise ValueError("unsupported requested_use")
    fresh, checked, calibration, available_at, reasons, rejection = _prerequisites(
        bundle, context, config, now)
    if checked is not None and checked.valid and fresh is not None:
        block = fresh.evaluation_block
        if block is None or not (block.starts_at <= now < block.ends_at):
            reasons.append("outside_evaluation_block")
    if requested_use == "operational_paper":
        reasons.append("operational_use_unsupported")
    elif requested_use == "economic_research":
        reasons.append("economic_use_unsupported")
    return _result(bundle, config, now, requested_use, bundle=fresh, context_recheck=checked,
        calibration=calibration, available_at=available_at, reasons=reasons, rejection=rejection)
