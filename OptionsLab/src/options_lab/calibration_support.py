"""Assess actual synthetic calibration mechanics from a fresh verified bundle."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .admission import VerifiedFixtureManifest, VerifiedFixtureMember, verify_fixture_bundle
from .bundle_inputs import _make
from .bundle_manifest_inputs import normalize_bundle_manifest
from .bundles import VerifiedBundle, verify_bundle
from .calibration import CalibrationBucket, CalibrationMetadata, _normalization_shape
from .calibration_inputs import CalibrationMember, _matches_retained, _retained_fixture_shape
from .config import ExecutionPolicy, StrategyConfig
from .feature_vector import EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC
from .runtime import measure_runtime
from .volume_normalization import FeatureNormalization


@dataclass(frozen=True, init=False)
class CalibrationAssessment:
    """This class represents fresh causal and mechanical support for one actual bucket."""

    bundle: VerifiedBundle | None
    metadata: CalibrationMetadata | None
    bucket: CalibrationBucket | None
    bucket_id: str
    supported: bool
    synthetic_core_fixture_capable: bool
    reasons: tuple[str, ...]
    rejection: object | None
    covered_attempts: int | None
    covered_sessions: int | None
    complete_pairs: int | None
    observed_attempts: int | None
    supported_attempts: int | None
    supported_pairs: int | None
    contributing_regular_sessions: int | None
    identity_conflicts: tuple[str, ...]
    training_feature_cutoff: datetime | None
    fit_cutoff: datetime | None
    last_label_available_at: datetime | None
    penalty: Decimal | None
    penalty_method: str | None
    modeled_estimator_status: str | None
    statistical_status: str
    statistical_readiness: bool
    operational_allowed: bool
    economic_allowed: bool

    def __init__(self) -> None:
        """Block caller selected calibration authority.

        :returns: None.
        :raises TypeError: Always; use assess_calibration.
        """
        raise TypeError("CalibrationAssessment values come from assess_calibration")


def _result(bucket_id: str, *, bundle: VerifiedBundle | None = None,
            metadata: CalibrationMetadata | None = None, reasons: list[str],
            rejection: object | None = None, covered: int | None = None,
            covered_sessions: int | None = None, complete_pairs: int | None = None,
            observed: int | None = None, attempts: int | None = None,
            pairs: int | None = None, sessions: int | None = None,
            conflicts: tuple[str, ...] = (), training_max: datetime | None = None,
            fit_cutoff: datetime | None = None, label_max: datetime | None = None,
            penalty: Decimal | None = None, method: str | None = None,
            modeled: str | None = None, bucket: CalibrationBucket | None = None) -> CalibrationAssessment:
    """Create the one immutable result without manufacturing absent evidence.

    :param    bucket_id:     Requested actual bucket identifier.
    :param    bundle:        Fresh reverified bundle when reached.
    :param    metadata:      Fresh actual metadata when reached.
    :param    reasons:       Ordered failed requirements.
    :param    rejection:     Fresh A2 rejection when reached.
    :param    covered:       All assigned tail rows.
    :param    covered_sessions: Distinct assigned tail dates.
    :param    complete_pairs:   Actual declared two-session blocks.
    :param    observed:      All assigned observed rows.
    :param    attempts:      Distinct observed rows in supported pairs.
    :param    pairs:         Supported complete pairs.
    :param    sessions:      Contributing regular sessions.
    :param    conflicts:     Retained conflicting identity descriptions.
    :param    training_max:  Actual model feature maximum.
    :param    fit_cutoff:    Declared fit cutoff.
    :param    label_max:     All used supervised or observed label maximum.
    :param    penalty:       Actual fixed penalty if reached.
    :param    method:        Actual penalty method if reached.
    :param    modeled:       Actual modeled estimator status if reached.
    :param    bucket:        Actual reverified bucket if reached.
    :returns:                One immutable assessment.
    """
    ready = not reasons
    return _make(CalibrationAssessment, bundle=bundle, metadata=metadata, bucket=bucket, bucket_id=bucket_id,
        supported=ready, synthetic_core_fixture_capable=ready, reasons=tuple(reasons), rejection=rejection,
        covered_attempts=covered, covered_sessions=covered_sessions, complete_pairs=complete_pairs,
        observed_attempts=observed, supported_attempts=attempts,
        supported_pairs=pairs, contributing_regular_sessions=sessions, identity_conflicts=conflicts,
        training_feature_cutoff=training_max, fit_cutoff=fit_cutoff,
        last_label_available_at=label_max, penalty=penalty, penalty_method=method,
        modeled_estimator_status=modeled, statistical_status="not_performed",
        statistical_readiness=False, operational_allowed=False, economic_allowed=False)


def assess_calibration(bundle: VerifiedBundle, bucket_id: str, *, now: datetime) -> CalibrationAssessment:
    """Reverify the current runtime and sources, then assess one fixed calibration bucket.

    :param    bundle:     Actual prior A2 bundle with retained original inputs.
    :param    bucket_id:  Fixed model bucket, or cash's requested bucket.
    :param    now:        Aware decision time; no implicit current clock.
    :returns:             Immutable actual counts, causal maxima, and ordered reasons.
    :raises   TypeError:  If trusted inputs have unexpected exact types.
    :raises   ValueError: If bucket identity or decision time is invalid.
    """
    if type(bundle) is not VerifiedBundle or type(bucket_id) is not str or type(now) is not datetime:
        raise TypeError("bundle, bucket_id and now require exact verified, string and datetime types")
    if not bucket_id:
        raise ValueError("bucket_id must be nonempty")
    if type(now.tzinfo) not in (timezone, ZoneInfo):
        raise ValueError("now must have a standard aware timezone")
    try:
        fixture, member, upstream, normalization, spec = (bundle.original_fixture, bundle.member,
            bundle.supplied_upstream_fixtures, bundle.supplied_normalization, bundle.spec)
        if (type(fixture) is not VerifiedFixtureManifest
                or type(member) is not VerifiedFixtureMember
                or type(member.record_id) is not str
                or type(upstream) is not tuple
                or any(type(root) is not VerifiedFixtureManifest for root in upstream)
                or type(bundle.config) is not StrategyConfig
                or type(bundle.config.execution) is not ExecutionPolicy
                or not (spec is None or spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC)
                or (normalization is not None and type(normalization) is not FeatureNormalization)):
            raise TypeError("bundle must retain exact A0/A2/C input owners")
        if (not _retained_fixture_shape(fixture)
                or any(not _retained_fixture_shape(root) for root in upstream)
                or (normalization is not None and not _normalization_shape(normalization))):
            return _result(bucket_id, reasons=["retained_content_mismatch"])
        config = replace(bundle.config, execution=replace(bundle.config.execution))
    except AttributeError:
        return _result(bucket_id, reasons=["retained_content_mismatch"])
    root = verify_fixture_bundle(fixture.fixture_id, fixture.payload_bytes,
        event_id=fixture.event_id, raw_ref=fixture.raw_ref,
        received_at=fixture.received_at)
    if root.value is None:
        return _result(bucket_id, reasons=["fresh_bundle_rejected"], rejection=root.rejection)
    fresh_member = next((item for item in root.value.members if item.record_id == member.record_id), None)
    if fresh_member is None or fresh_member.kind != "model_bundle":
        return _result(bucket_id, reasons=["fresh_bundle_rejected"])
    body = fresh_member.decode_raw_body()
    if type(body) is not dict or type(body.get("model_utf8")) is not str:
        return _result(bucket_id, reasons=["fresh_bundle_rejected"])
    receipt = dict(event_id=root.value.event_id, raw_ref=root.value.raw_ref, received_at=root.value.received_at)
    inspected = normalize_bundle_manifest(body.get("manifest"), **receipt)
    runtime = measure_runtime()
    if inspected.value is None or runtime.value is None:
        return _result(bucket_id, reasons=["fresh_bundle_rejected"],
                       rejection=inspected.rejection if inspected.value is None else runtime.rejection)
    try:
        model_bytes = body["model_utf8"].encode("utf-8")
    except UnicodeEncodeError:
        return _result(bucket_id, reasons=["fresh_bundle_rejected"])
    checked = verify_bundle(inspected.value, model_bytes,
        fixture=fixture, spec=spec, normalization=normalization, config=config,
        runtime=runtime.value, upstream_fixtures=upstream)
    if checked.value is None:
        return _result(bucket_id, reasons=["fresh_bundle_rejected"], rejection=checked.rejection)
    fresh = checked.value
    if not _matches_retained(bundle, fresh, set()):
        return _result(bucket_id, bundle=fresh, reasons=["retained_content_mismatch"])
    if fresh.model.model_kind == "cash":
        return _result(bucket_id, bundle=fresh, reasons=["calibration_not_applicable"])
    metadata = fresh.calibration_metadata
    if metadata is None:
        return _result(bucket_id, bundle=fresh, reasons=["calibration_metadata_missing"])
    bucket = next((item for item in metadata.buckets if item.bucket_id == bucket_id), None)
    if bucket is None:
        return _result(bucket_id, bundle=fresh, metadata=metadata, reasons=["unknown_bucket"])
    partition = metadata.partition
    prediction = fresh.manifest.prediction_contract
    assert prediction is not None
    reasons: list[str] = []
    def need(condition: bool, reason: str) -> None:
        if not condition and reason not in reasons:
            reasons.append(reason)

    model, tuning, tail = partition.model_membership.rows, partition.tuning_membership.rows, partition.calibration_members
    used = (*model, *tuning, *tail)
    norm_at, fit_at, freeze, tune_at = (partition.normalization.available_at,
        prediction.fit_cutoff, partition.frozen_at, partition.tuning_cutoff)
    need(fit_at is not None and fit_at <= now, "fit_cutoff_unavailable")
    need(fit_at is not None and fit_at < partition.fold.evaluation_start,
         "fit_after_evaluation")
    need(norm_at is not None and fit_at is not None and norm_at <= fit_at and norm_at <= now,
         "normalization_unavailable")
    need(freeze is not None and tune_at is not None and tune_at <= freeze,
         "freeze_invalid")
    need(partition.frozen_threshold_return is not None and prediction.threshold_return == partition.frozen_threshold_return
         and partition.model_hash == fresh.model.model_hash and metadata.model_hash == fresh.model.model_hash
         and metadata.bucket_rule_id == partition.bucket_rule_id, "frozen_mapping_incomplete")
    need(metadata.available_at <= now, "record_unavailable")
    need(fit_at is not None and metadata.available_at >= fit_at, "record_before_fit")
    need(metadata.available_at < partition.fold.evaluation_start, "record_after_evaluation")
    calendar = partition.calendar
    need(calendar.available_at is not None, "calendar_availability_unknown")
    need(calendar.available_at is not None and fit_at is not None and calendar.available_at <= fit_at
         and calendar.available_at <= now, "calendar_unavailable")
    used_dates = {row.session_date for row in used}
    session_by_date = {item.session.session_date: item.session for item in calendar.sessions}
    need(used_dates <= session_by_date.keys(), "calendar_uncovered")
    for day, session in session_by_date.items():
        if day in used_dates or partition.fold.calibration_tail_start_date <= day < partition.fold.calibration_tail_end_date:
            need(session.available_at is not None and fit_at is not None
                 and session.available_at <= fit_at and session.available_at <= now,
                 "session_unavailable")
    ids, outcomes, roles = set(), set(), {}
    conflicts = []
    label_times = []
    for role, rows in (("model", model), ("tuning", tuning), ("tail", tail)):
        for row in rows:
            if type(row) is not CalibrationMember:
                raise TypeError("calibration population must retain exact sample owners")
            key = (row.decision_id, row.contract)
            if row.sample_id in ids or key in outcomes:
                conflicts.append("duplicate_outcome:" + row.sample_id)
            ids.add(row.sample_id)
            outcomes.add(key)
            prior = roles.setdefault(row.decision_id, role)
            if prior != role:
                conflicts.append("cross_role_decision:" + row.decision_id)
            available = row.available_at
            need(available is not None and fit_at is not None and available <= fit_at and available <= now,
                 "row_unavailable")
            if role != "tail":
                need(available is not None and freeze is not None and available <= freeze,
                     "fit_status_after_freeze")
                if role == "tuning":
                    need(available is not None and tune_at is not None and available <= tune_at,
                         "tuning_status_after_cutoff")
            known = tuple(getattr(row, name) for name in ("feature_available_at", "information_start",
                "information_end", "label_available_at"))
            need(available is not None and all(t is None or t <= available for t in known),
                 "partial_time_after_row")
            feature, start, end, label = known
            if feature is not None:
                need(norm_at is not None and norm_at <= feature, "normalization_after_feature")
                session = session_by_date.get(row.session_date)
                need(session is not None and session.available_at is not None and session.available_at <= feature,
                     "session_after_feature")
                need(calendar.available_at is not None and calendar.available_at <= feature,
                     "calendar_after_feature")
            observed = row.outcome_status.startswith("observed_")
            if role != "tail":
                need(observed, "fit_row_not_observed")
            if observed:
                need(all(t is not None for t in known) and available is not None
                     and norm_at is not None and norm_at <= feature <= start <= end <= label <= available,
                     "observed_time_chain_invalid")
                need(label is not None and label < partition.fold.evaluation_start,
                     "label_after_evaluation")
                if role != "tail":
                    need(label is not None and freeze is not None and label <= freeze,
                         "fit_label_after_freeze")
                if label is not None:
                    label_times.append(label)
    need(not conflicts, "identity_conflict")
    tail_features = [row.feature_available_at for row in tail if row.feature_available_at is not None]
    need(bool(tail_features) and freeze is not None and freeze < min(tail_features), "freeze_after_tail")
    tune_labels = [row.label_available_at for row in tuning if row.label_available_at is not None]
    need(tune_at is not None and all(label <= tune_at for label in tune_labels), "tuning_after_cutoff")
    model_features = [row.feature_available_at for row in model if row.feature_available_at is not None]
    training_max = max(model_features, default=None)
    label_max = max(label_times, default=None)
    need(training_max is not None and prediction.training_feature_cutoff == training_max,
         "training_feature_cutoff_mismatch")
    need(label_max is not None and prediction.last_label_available_at == label_max,
         "last_label_available_at_mismatch")
    schedule = fresh.manifest.provenance.simulated_schedule
    if schedule is not None:
        need(schedule.fit_cutoff == fit_at, "schedule_fit_cutoff_mismatch")
    need(bucket.penalty is not None and bucket.penalty.is_finite() and bucket.penalty >= 0
         and bucket.uncertainty_method_id == "fixture_fixed_penalty_v1"
         and bucket.modeled_estimator_status == "not_performed"
         and bucket.statistical_status == metadata.statistical_status == "not_performed"
         and bucket.statistical_readiness is False and metadata.statistical_readiness is False,
         "fixed_penalty_unsupported")
    members = [row for row in tail if row.bucket_id == bucket_id]
    observed_rows = [row for row in members if row.outcome_status.startswith("observed_")]
    supported_pairs = []
    contributing = set()
    attempts = set()
    for pair in partition.session_pairs:
        if len(pair) != 2 or any(session_by_date[day].kind != "regular" for day in pair):
            continue
        pair_rows = [row for row in observed_rows if row.session_date in pair]
        if all(any(row.session_date == day for row in pair_rows) for day in pair):
            supported_pairs.append(pair)
            contributing.update(pair)
            attempts.update((row.decision_id, row.contract) for row in pair_rows)
    need(len(supported_pairs) >= 10, "insufficient_supported_pairs")
    need(len(contributing) >= 20, "insufficient_regular_sessions")
    need(len(attempts) >= 50, "insufficient_distinct_observed_attempts")
    return _result(bucket_id, bundle=fresh, metadata=metadata, bucket=bucket, reasons=reasons,
        covered=len(members), covered_sessions=len({row.session_date for row in members}),
        complete_pairs=len(bucket.two_session_blocks), observed=len(observed_rows), attempts=len(attempts),
        pairs=len(supported_pairs), sessions=len(contributing), conflicts=tuple(conflicts),
        training_max=training_max, fit_cutoff=fit_at, label_max=label_max,
        penalty=bucket.penalty, method=bucket.uncertainty_method_id,
        modeled=bucket.modeled_estimator_status)
