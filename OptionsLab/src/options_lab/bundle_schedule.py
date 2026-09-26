"""Admit actual adjacent calendar gaps and covered bundle evaluation blocks."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
from zoneinfo import ZoneInfo

from ._input_parsing import _InvalidInput, _parse_date
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember
from .bar_inputs import _UnsupportedIdentity, _timestamp_string
from .bundle_inputs import ModelDataRow, ParsedModelData, _decimal, _identifier, _make, _shape, normalize_model_bytes
from .bundle_manifest_inputs import ExternalReference, _one, _reference, _timestamp
from .calibration import (CalibrationMetadata, CalibrationPartition, _normalization_shape,
                          normalize_calibration_metadata)
from .calibration_inputs import (CalendarDescriptor, CalendarSessionEvidence, CalibrationInputRejection,
    _Failure, _body, _body_size, _check, _matches_retained, _rejected, _resolve, _roots, _trusted,
    normalize_calendar_descriptor)
from .config import _snapshot_hash
from .feature_vector import FeatureSpec, EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC
from .volume_normalization import FeatureNormalization, normalize_feature_normalization


_GENERATOR = "optionslab-bundle-schedule-fixture-builder"
_GAP_FIELDS = ("schema_version", "gap_id", "calendar_descriptor_ref", "previous_session",
    "next_session", "available_at")
_BLOCK_FIELDS = ("schema_version", "block_id", "starts_at", "ends_at", "calendar_descriptor_ref",
    "model_hash", "feature_schema_id", "transform_id", "normalization_hash",
    "calibration_partition_ref", "calibration_record_ref", "frozen_threshold_return",
    "bucket_rule_id", "available_at")
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True, init=False)
class ActivationGap:
    """This class represents a source bound gap between actual adjacent open sessions."""

    gap_id: str
    calendar_descriptor_ref: ExternalReference
    previous_session: date
    next_session: date
    starts_at: datetime
    ends_at: datetime
    available_at: datetime | None
    calendar: CalendarDescriptor
    previous_evidence: CalendarSessionEvidence
    next_evidence: CalendarSessionEvidence
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    content_hash: str

    def __init__(self) -> None:
        """Block caller selected gap evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_activation_gap.
        """
        raise TypeError("ActivationGap values come from normalize_activation_gap")


@dataclass(frozen=True, init=False)
class ActivationGapValidation:
    """This class represents one admitted gap or safe reached rejection."""

    value: ActivationGap | None
    rejection: CalibrationInputRejection | None

    def __init__(self) -> None:
        """Block caller selected gap outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_activation_gap.
        """
        raise TypeError("ActivationGapValidation values come from normalization")


@dataclass(frozen=True, init=False)
class EvaluationBlock:
    """This class represents a covered evaluation interval bound to actual frozen owners."""

    block_id: str
    starts_at: datetime
    ends_at: datetime
    calendar_descriptor_ref: ExternalReference
    model_hash: str | None
    feature_schema_id: str | None
    transform_id: str | None
    normalization_hash: str | None
    calibration_partition_ref: ExternalReference | None
    calibration_record_ref: ExternalReference | None
    frozen_threshold_return: Decimal | None
    bucket_rule_id: str | None
    available_at: datetime | None
    calendar: CalendarDescriptor
    model: ParsedModelData
    spec: FeatureSpec | None
    normalization: FeatureNormalization | None
    partition: CalibrationPartition | None
    metadata: CalibrationMetadata | None
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    content_hash: str

    def __init__(self) -> None:
        """Block caller selected block evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_evaluation_block.
        """
        raise TypeError("EvaluationBlock values come from normalize_evaluation_block")


@dataclass(frozen=True, init=False)
class EvaluationBlockValidation:
    """This class represents one admitted evaluation block or safe rejection."""

    value: EvaluationBlock | None
    rejection: CalibrationInputRejection | None

    def __init__(self) -> None:
        """Block caller selected block outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_evaluation_block.
        """
        raise TypeError("EvaluationBlockValidation values come from normalization")


def _own(fixture, record_id, kind):
    """Reach one immutable body in this exact schedule producer."""
    member = next((item for item in fixture.members if item.record_id == record_id), None)
    _check(member is not None, "record_id", "unknown_member")
    _check(len(member.raw_body_bytes) <= 8 * 1024 * 1024, "record_id", "resource_limit")
    _check(member.kind == kind and hashlib.sha256(member.raw_body_bytes).hexdigest() == member.raw_hash,
           "record_id", "member_kind_mismatch")
    profile = next((item for item in fixture.decode_modeled_source_profiles()
                    if item["profile_id"] == member.profile_id), None)
    _check(profile is not None and profile["kind"] == kind and profile["source"] == fixture.generator_id == _GENERATOR
           and fixture.generator_version == "1" and member.decode_envelope()["supersedes_record_id"] is None,
           "record_id", "profile_mismatch")
    return member


def _calendar(ref, supplied, roots):
    """Re-admit the actual descriptor and every referenced session before comparison."""
    owner, member = _resolve(ref, roots, "calendar_descriptor")
    try:
        retained = (type(supplied.content_hash) is str and type(supplied.fixture) is VerifiedFixtureManifest
            and type(supplied.member) is VerifiedFixtureMember
            and type(supplied.fixture.fixture_id) is str
            and type(supplied.member.record_id) is str
            and type(supplied.member.raw_body_bytes) is bytes
            and type(supplied.upstream_fixtures) is tuple and len(supplied.upstream_fixtures) <= 64
            and all(type(item) is VerifiedFixtureManifest and type(item.fixture_id) is str
                    for item in supplied.upstream_fixtures))
    except AttributeError:
        retained = False
    _check(retained, "calendar_descriptor_ref", "retained_content_mismatch")
    needed = tuple(roots[item.fixture_id] for item in supplied.upstream_fixtures
                   if item.fixture_id in roots)
    result = normalize_calendar_descriptor(member.decode_raw_body(), fixture=owner,
        record_id=member.record_id, upstream_fixtures=needed)
    _check(result.value is not None, "calendar_descriptor_ref", "normalization_failed", result.rejection)
    fresh = result.value
    _check(_matches_retained(supplied, fresh, set()),
           "calendar_descriptor_ref", "retained_content_mismatch")
    return fresh


def _upstream(roots, fixture, used):
    """Retain only actually traversed roots and reject unused supplied authorities."""
    _check(all(root.fixture_id in used for root in roots.values() if root.fixture_id != fixture.fixture_id),
           "upstream_fixtures", "unused_reference")
    return tuple(root for root in roots.values() if root.fixture_id in used and root.fixture_id != fixture.fixture_id)


def normalize_activation_gap(raw: object, *, fixture: VerifiedFixtureManifest, record_id: str,
                             calendar: CalendarDescriptor,
                             upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> ActivationGapValidation:
    """Resolve exact session date keys to consecutive actual calendar entries.

    :param raw: Exact untrusted gap body.
    :param fixture: Admitted containing P08 root.
    :param record_id: Trusted containing member identifier.
    :param calendar: Actual descriptor counterpart to reverify.
    :param upstream_fixtures: Exact admitted referenced roots.
    :returns: Source bound gap or bounded reached rejection.
    :raises TypeError: If trusted owners have unexpected exact types.
    :raises ValueError: If the trusted record identifier is empty.
    """
    _trusted(fixture, record_id, upstream_fixtures)
    if type(calendar) is not CalendarDescriptor:
        raise TypeError("calendar must be an exact CalendarDescriptor")
    reached = None
    try:
        _shape(raw, "$", _GAP_FIELDS)
        _one(raw["schema_version"], "schema_version")
        gap_id = _identifier(raw["gap_id"], "gap_id")
        ref = _reference(raw["calendar_descriptor_ref"], "calendar_descriptor_ref", False)
        previous = _parse_date(raw["previous_session"], "previous_session")
        following = _parse_date(raw["next_session"], "next_session")
        available = _timestamp(raw["available_at"], "available_at", True)
        _check(_body_size(raw) <= 8 * 1024 * 1024, "$", "resource_limit")
        roots = _roots(fixture, upstream_fixtures)
        own = _own(roots[fixture.fixture_id], record_id, "activation_gap")
        reached = own
        _body(raw, own)
        actual_calendar = _calendar(ref, calendar, roots)
        dates = tuple(item.session.session_date for item in actual_calendar.sessions)
        _check(previous in dates and following in dates, "sessions", "unknown_session")
        index = dates.index(previous)
        _check(index + 1 < len(dates) and dates[index + 1] == following,
               "sessions", "nonadjacent_session")
        prior, next_item = actual_calendar.sessions[index:index + 2]
        starts, ends = prior.session.closes_at, next_item.session.opens_at
        _check(type(starts) is datetime and type(ends) is datetime and starts < ends,
               "sessions", "invalid_interval")
        used = {actual_calendar.fixture.fixture_id, *(root.fixture_id for root in actual_calendar.upstream_fixtures)}
        value = _make(ActivationGap, gap_id=gap_id, calendar_descriptor_ref=ref,
            previous_session=previous, next_session=following, starts_at=starts, ends_at=ends,
            available_at=available, calendar=actual_calendar, previous_evidence=prior,
            next_evidence=next_item, fixture=roots[fixture.fixture_id], member=own,
            upstream_fixtures=_upstream(roots, fixture, used), content_hash="")
        object.__setattr__(value, "content_hash", _snapshot_hash(dict(record_kind="options_lab.activation_gap",
            **raw, calendar_content_hash=actual_calendar.content_hash,
            previous_ref=prior.reference.snapshot(), next_ref=next_item.reference.snapshot(),
            starts_at=_timestamp_string(starts), ends_at=_timestamp_string(ends))))
    except (_InvalidInput, _Failure, _UnsupportedIdentity, MemoryError, RecursionError) as failure:
        return _rejected(ActivationGapValidation, "activation_gap", fixture, reached, failure)
    return _make(ActivationGapValidation, value=value, rejection=None)


def normalize_evaluation_block(raw: object, *, fixture: VerifiedFixtureManifest, record_id: str,
                               calendar: CalendarDescriptor, model: ParsedModelData,
                               spec: FeatureSpec | None, normalization: FeatureNormalization | None,
                               partition: CalibrationPartition | None, metadata: CalibrationMetadata | None,
                               upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> EvaluationBlockValidation:
    """Bind one covered interval to real cash or fixed model and calibration owners.

    :param raw: Exact untrusted block body.
    :param fixture: Admitted containing P08 root.
    :param record_id: Trusted containing member identifier.
    :param calendar: Actual descriptor counterpart to reverify.
    :param model: Inspected original model bytes.
    :param spec: Actual code owned feature definition, or None for cash.
    :param normalization: Actual P10 artifact, or None for cash.
    :param partition: Actual C partition, or None for cash.
    :param metadata: Actual C record, or None for cash.
    :param upstream_fixtures: Exact admitted referenced roots.
    :returns: Source bound block or bounded reached rejection.
    :raises TypeError: If trusted owners have unexpected exact types.
    :raises ValueError: If the trusted record identifier is empty.
    """
    _trusted(fixture, record_id, upstream_fixtures)
    if (type(calendar) is not CalendarDescriptor or type(model) is not ParsedModelData
            or (spec is not None and type(spec) is not FeatureSpec)
            or (normalization is not None and type(normalization) is not FeatureNormalization)
            or (partition is not None and type(partition) is not CalibrationPartition)
            or (metadata is not None and type(metadata) is not CalibrationMetadata)):
        raise TypeError("block counterparts require exact calendar, model, feature and calibration owners")
    reached = None
    try:
        _shape(raw, "$", _BLOCK_FIELDS)
        _one(raw["schema_version"], "schema_version")
        block_id = _identifier(raw["block_id"], "block_id")
        start, end = _timestamp(raw["starts_at"], "starts_at"), _timestamp(raw["ends_at"], "ends_at")
        _check(start < end, "ends_at", "invalid_interval")
        ref = _reference(raw["calendar_descriptor_ref"], "calendar_descriptor_ref", False)
        fields = {name: None if raw[name] is None else _identifier(raw[name], name)
                  for name in ("model_hash", "feature_schema_id", "transform_id", "normalization_hash", "bucket_rule_id")}
        partition_ref = _reference(raw["calibration_partition_ref"], "calibration_partition_ref")
        record_ref = _reference(raw["calibration_record_ref"], "calibration_record_ref")
        threshold = None if raw["frozen_threshold_return"] is None else _decimal(raw["frozen_threshold_return"], "frozen_threshold_return")
        available = _timestamp(raw["available_at"], "available_at", True)
        _check(_body_size(raw) <= 8 * 1024 * 1024, "$", "resource_limit")
        roots = _roots(fixture, upstream_fixtures)
        own = _own(roots[fixture.fixture_id], record_id, "evaluation_block")
        reached = own
        _body(raw, own)
        actual_calendar = _calendar(ref, calendar, roots)
        first_day, last_day = start.astimezone(_NY).date(), (end - timedelta(microseconds=1)).astimezone(_NY).date()
        _check(actual_calendar.coverage_start_date <= first_day <= last_day <= actual_calendar.coverage_end_date,
               "calendar_descriptor_ref", "incomplete_coverage")
        used = {actual_calendar.fixture.fixture_id, *(root.fixture_id for root in actual_calendar.upstream_fixtures)}
        try:
            retained_model = (type(model.original_model_bytes) is bytes and type(model.model_hash) is str
                and type(model.model_kind) is str and type(model.format_id) is str
                and type(model.rows) is tuple and len(model.rows) <= 2
                and all(type(row) is ModelDataRow
                    and type(row.right) is str and type(row.mean_attempt_return) is Decimal
                    and type(row.calibration_bucket) is str for row in model.rows))
        except AttributeError:
            retained_model = False
        _check(retained_model, "model", "retained_content_mismatch")
        inspected = normalize_model_bytes(model.original_model_bytes, event_id=fixture.event_id,
            raw_ref=fixture.raw_ref, received_at=fixture.received_at)
        _check(inspected.value is not None, "model", "normalization_failed", inspected.rejection)
        actual_model = inspected.value
        _check(_matches_retained(model, actual_model, set()), "model", "retained_content_mismatch")
        if actual_model.model_kind == "cash":
            _check(spec is None and normalization is None and partition is None and metadata is None
                   and all(value is None for value in fields.values()) and partition_ref is None
                   and record_ref is None and threshold is None, "binding", "cash_counterpart_present")
            actual_norm = actual_partition = actual_metadata = None
        else:
            _check((spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC) and normalization is not None
                   and partition is not None and metadata is not None,
                   "binding", "missing_counterpart")
            _check(_normalization_shape(normalization), "normalization", "retained_content_mismatch")
            norm_root, training = roots.get(normalization.manifest.fixture_id), roots.get(normalization.baseline.manifest.fixture_id)
            _check(norm_root is not None and training is not None, "normalization", "unknown_fixture")
            norm_member = next((m for m in norm_root.members if m.record_id == normalization.record_id), None)
            _check(norm_member is not None and norm_member.kind == "feature_normalization", "normalization", "unknown_member")
            recomputed = normalize_feature_normalization(norm_member.decode_raw_body(), manifest=norm_root,
                record_id=norm_member.record_id, training_manifest=training, decision_at=normalization.available_at)
            _check(recomputed.value is not None, "normalization", "normalization_failed", recomputed)
            actual_norm = recomputed.value
            _check(_matches_retained(normalization, actual_norm, set()),
                   "normalization", "retained_content_mismatch")
            try:
                retained_record = (type(metadata.fixture) is VerifiedFixtureManifest
                    and type(metadata.fixture.fixture_id) is str
                    and type(metadata.member) is VerifiedFixtureMember
                    and type(metadata.member.raw_body_bytes) is bytes and type(metadata.content_hash) is str
                    and type(metadata.upstream_fixtures) is tuple and len(metadata.upstream_fixtures) <= 64
                    and all(type(item) is VerifiedFixtureManifest and type(item.fixture_id) is str
                            for item in metadata.upstream_fixtures)
                    and type(partition.fixture) is VerifiedFixtureManifest
                    and type(partition.fixture.fixture_id) is str
                    and type(partition.member) is VerifiedFixtureMember
                    and type(partition.member.raw_body_bytes) is bytes and type(partition.content_hash) is str)
            except AttributeError:
                retained_record = False
            _check(retained_record, "calibration_record_ref", "retained_content_mismatch")
            _check(partition_ref is not None and record_ref is not None, "binding", "missing_counterpart")
            record_root, record_member = _resolve(record_ref, roots, "calibration_record")
            needed = tuple(roots[item.fixture_id] for item in metadata.upstream_fixtures
                           if item.fixture_id in roots
                           and item.fixture_id != record_root.fixture_id)
            recomputed_record = normalize_calibration_metadata(record_member.decode_raw_body(),
                fixture=record_root, record_id=record_member.record_id, model=actual_model,
                spec=spec, normalization=actual_norm, upstream_fixtures=needed)
            _check(recomputed_record.value is not None, "calibration_record_ref", "normalization_failed",
                   recomputed_record.rejection)
            actual_metadata = recomputed_record.value
            actual_partition = actual_metadata.partition
            _check(_matches_retained(metadata, actual_metadata, set())
                   and _matches_retained(partition, actual_partition, set()),
                   "calibration_record_ref", "retained_content_mismatch")
            _check(actual_partition.calendar.content_hash == actual_calendar.content_hash
                   and actual_partition.calendar.fixture.fixture_id == actual_calendar.fixture.fixture_id
                   and actual_partition.calendar.member.raw_body_bytes == actual_calendar.member.raw_body_bytes,
                   "calendar_descriptor_ref", "counterpart_mismatch")
            _check(fields == dict(model_hash=actual_model.model_hash, feature_schema_id=spec.feature_schema_id,
                   transform_id=spec.transform_id, normalization_hash=actual_norm.content_hash,
                   bucket_rule_id=actual_partition.bucket_rule_id)
                   and threshold == actual_metadata.frozen_threshold_return
                   and block_id == actual_partition.fold.evaluation_block_id
                   and start == actual_partition.fold.evaluation_start
                   and end == actual_partition.fold.evaluation_end,
                   "binding", "counterpart_mismatch")
            used.update((norm_root.fixture_id, training.fixture_id, record_root.fixture_id,
                         actual_partition.fixture.fixture_id))
            used.update(root.fixture_id for root in actual_metadata.upstream_fixtures)
        value = _make(EvaluationBlock, block_id=block_id, starts_at=start, ends_at=end,
            calendar_descriptor_ref=ref, **fields, calibration_partition_ref=partition_ref,
            calibration_record_ref=record_ref, frozen_threshold_return=threshold, available_at=available,
            calendar=actual_calendar, model=actual_model, spec=spec, normalization=actual_norm,
            partition=actual_partition, metadata=actual_metadata, fixture=roots[fixture.fixture_id],
            member=own, upstream_fixtures=_upstream(roots, fixture, used), content_hash="")
        object.__setattr__(value, "content_hash", _snapshot_hash(dict(record_kind="options_lab.evaluation_block",
            **raw, calendar_content_hash=actual_calendar.content_hash,
            actual_model_hash=actual_model.model_hash,
            normalization_content_hash=None if actual_norm is None else actual_norm.content_hash,
            partition_content_hash=None if actual_partition is None else actual_partition.content_hash,
            record_content_hash=None if actual_metadata is None else actual_metadata.content_hash)))
    except (_InvalidInput, _Failure, _UnsupportedIdentity, MemoryError, RecursionError) as failure:
        return _rejected(EvaluationBlockValidation, "evaluation_block", fixture, reached, failure)
    return _make(EvaluationBlockValidation, value=value, rejection=None)
