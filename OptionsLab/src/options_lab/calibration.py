"""Admit an actual fixed-recipe calibration partition without readiness claims."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib

from ._input_parsing import _InvalidInput, _parse_date
from .admission import VerifiedFixtureManifest, _canonical_bytes
from .bar_inputs import _timestamp_string, _UnsupportedIdentity
from .bundle_inputs import ModelDataRow, ParsedModelData, _decimal, _identifier, _make, _shape, normalize_model_bytes
from .bundle_manifest_inputs import ExternalReference, _one, _reference, _timestamp
from .calibration_inputs import (CalendarDescriptor, FitMembership, CalibrationMember, _Failure, _body,
    _body_size, _check, _own_member, _receipt, _refs, _rejected, _resolve, _roots, _sample,
    _trusted, _MAX_BODY, normalize_calendar_descriptor, normalize_fit_membership)
from .config import _snapshot_hash
from .feature_vector import FeatureSpec, EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC
from .volume import VolumeBaseline, VolumeBucket
from .volume_inputs import VolumePartition
from .volume_normalization import FeatureNormalization, normalize_feature_normalization


_FIELDS = ("schema_version", "partition_id", "model_membership_ref", "tuning_membership_ref",
    "calibration_sample_refs", "calendar_ref", "fold", "feature_schema_id", "transform_id",
    "normalization_ref", "normalization_hash", "model_hash", "frozen_threshold_return",
    "bucket_rule_id", "frozen_at", "tuning_cutoff")
_FOLD_FIELDS = ("fold_id", "recipe_id", "training_start_date", "training_end_date",
    "tuning_start_date", "tuning_end_date", "calibration_tail_start_date",
    "calibration_tail_end_date", "evaluation_block_id", "evaluation_start", "evaluation_end")
_WINDOWS = ("2025-06-01", "2026-06-01", "2026-08-01", "2026-09-01")
_EVALUATION = ("2026-09-01T04:00:00Z", "2026-10-01T04:00:00Z")
_RECIPE_ID = "xnys-12-3-1-2026-v1"
_BUCKET_RULE = dict(record_kind="options_lab.calibration_bucket_rule", schema_version=1,
    row_assignment="exact_contract_right_to_actual_fixed_model_row_calibration_bucket",
    right_buckets="distinct_call_put",
    outcome_identity=["decision_id", "contract.underlying", "contract.expiry", "contract.right",
                      "contract.strike", "contract.multiplier", "contract.deliverable_id"],
    population="all_ordered_tail_refs_including_censored_invalid_and_odd_last_session",
    pairing="chronological_open_tail_sessions_pairs_0_1_2_3_odd_last_retained",
    early_close="retained_in_chronological_order")
BUCKET_RULE_ID = _snapshot_hash(_BUCKET_RULE)


@dataclass(frozen=True, init=False)
class CalibrationFold:
    """This class represents the exact registered 2026 training, tuning, tail and evaluation windows."""

    fold_id: str
    recipe_id: str
    training_start_date: date
    training_end_date: date
    tuning_start_date: date
    tuning_end_date: date
    calibration_tail_start_date: date
    calibration_tail_end_date: date
    evaluation_block_id: str
    evaluation_start: datetime
    evaluation_end: datetime

    def __init__(self) -> None:
        """Block caller selected fold authority.

        :returns: None.
        :raises TypeError: Always; use normalize_calibration_partition.
        """
        raise TypeError("CalibrationFold values come from partition normalization")

    def snapshot(self) -> dict[str, object]:
        """Return every retained fold field.

        :returns: A fresh exact normalized fold snapshot.
        """
        return dict(fold_id=self.fold_id, recipe_id=self.recipe_id,
            **{name: getattr(self, name).isoformat() for name in _FOLD_FIELDS[2:8]},
            evaluation_block_id=self.evaluation_block_id,
            evaluation_start=_timestamp_string(self.evaluation_start),
            evaluation_end=_timestamp_string(self.evaluation_end))


@dataclass(frozen=True, init=False)
class CalibrationPartition:
    """This class represents a resolved immutable calibration tail and all its actual owners."""

    partition_id: str
    model_membership_ref: ExternalReference
    tuning_membership_ref: ExternalReference
    calibration_sample_refs: tuple[ExternalReference, ...]
    calendar_ref: ExternalReference
    fold: CalibrationFold
    feature_schema_id: str
    transform_id: str
    normalization_ref: ExternalReference
    normalization_hash: str
    model_hash: str
    frozen_threshold_return: Decimal | None
    bucket_rule_id: str
    frozen_at: datetime | None
    tuning_cutoff: datetime | None
    model_membership: FitMembership
    tuning_membership: FitMembership
    calibration_members: tuple[CalibrationMember, ...]
    calendar: CalendarDescriptor
    normalization: FeatureNormalization
    model: ParsedModelData
    spec: FeatureSpec
    fixture: VerifiedFixtureManifest
    member: object
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    session_pairs: tuple[tuple[date, ...], ...]
    content_hash: str

    def __init__(self) -> None:
        """Block caller selected partition authority.

        :returns: None.
        :raises TypeError: Always; use normalize_calibration_partition.
        """
        raise TypeError("CalibrationPartition values come from normalization")

    def snapshot(self) -> dict[str, object]:
        """Return every normalized semantic fact, without the containing root.

        :returns: A fresh source bound partition snapshot.
        """
        return dict(record_kind="options_lab.calibration_partition", schema_version=1,
            partition_id=self.partition_id, model_membership_ref=self.model_membership_ref.snapshot(),
            tuning_membership_ref=self.tuning_membership_ref.snapshot(),
            calibration_sample_refs=[ref.snapshot() for ref in self.calibration_sample_refs],
            calendar_ref=self.calendar_ref.snapshot(), fold=self.fold.snapshot(),
            feature_schema_id=self.feature_schema_id, transform_id=self.transform_id,
            normalization_ref=self.normalization_ref.snapshot(), normalization_hash=self.normalization_hash,
            model_hash=self.model_hash,
            frozen_threshold_return=None if self.frozen_threshold_return is None else str(self.frozen_threshold_return),
            bucket_rule_id=self.bucket_rule_id, frozen_at=_timestamp_string(self.frozen_at),
            tuning_cutoff=_timestamp_string(self.tuning_cutoff),
            model_membership=self.model_membership.snapshot(), tuning_membership=self.tuning_membership.snapshot(),
            calibration_members=[row.snapshot() for row in self.calibration_members],
            calendar=self.calendar.snapshot(), normalization=self.normalization.snapshot,
            session_pairs=[[day.isoformat() for day in pair] for pair in self.session_pairs])


@dataclass(frozen=True, init=False)
class CalibrationPartitionValidation:
    """This class represents one actual partition or a safe reached rejection."""

    value: CalibrationPartition | None
    rejection: object | None

    def __init__(self) -> None:
        """Block caller selected partition outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_calibration_partition.
        """
        raise TypeError("CalibrationPartitionValidation values come from normalization")


def _fold(raw):
    """Parse the exact registered 12/3/1 recipe and retain its actual date and time types."""
    _shape(raw, "fold", _FOLD_FIELDS)
    fold_id = _identifier(raw["fold_id"], "fold.fold_id")
    recipe_id = _identifier(raw["recipe_id"], "fold.recipe_id")
    block_id = _identifier(raw["evaluation_block_id"], "fold.evaluation_block_id")
    dates = {name: _parse_date(raw[name], "fold." + name) for name in _FOLD_FIELDS[2:8]}
    times = {name: _timestamp(raw[name], "fold." + name) for name in _FOLD_FIELDS[9:]}
    expected = tuple(map(date.fromisoformat, (_WINDOWS[0], _WINDOWS[1], _WINDOWS[1],
        _WINDOWS[2], _WINDOWS[2], _WINDOWS[3])))
    _check(recipe_id == _RECIPE_ID and tuple(dates.values()) == expected
           and tuple(times.values()) == tuple(_timestamp(value, "fold.evaluation") for value in _EVALUATION),
           "fold", "unsupported_recipe")
    return _make(CalibrationFold, fold_id=fold_id, recipe_id=recipe_id, **dates,
        evaluation_block_id=block_id, **times)


def _nested(ref, roots, kind, count):
    """Resolve one membership body and preflight its bounded sample-reference count."""
    root, member = _resolve(ref, roots, kind)
    body = member.decode_raw_body()
    _shape(body, kind, ("schema_version", "membership_id", "sample_refs"))
    refs = _refs(body["sample_refs"], kind + ".sample_refs", 4096)
    _check(count + len(refs) <= 4096, "sample_refs", "resource_limit")
    return root, member, body, refs


def _sources(refs, roots, owner):
    """Select only actually referenced external roots for one lower normalizer."""
    needed = {ref.fixture_id for ref in refs}
    return tuple(root for root in roots.values() if root.fixture_id in needed and root.fixture_id != owner.fixture_id)


def _normalization_shape(value):
    """Preflight retained P10 leaves before snapshots or equality can invoke them."""
    try:
        baseline = value.baseline
        if type(baseline) is not VolumeBaseline:
            return False
        partition = baseline.partition
        if type(partition) is not VolumePartition:
            return False
        return (type(value.manifest) is VerifiedFixtureManifest
            and type(baseline.manifest) is VerifiedFixtureManifest
            and type(value.available_at) is datetime and type(value.available_at.tzinfo) is timezone
            and all(type(getattr(value, name)) is str for name in
                    ("availability_basis", "record_id", "raw_hash", "content_hash"))
            and type(baseline.cutoff) is datetime and type(baseline.cutoff.tzinfo) is timezone
            and all(type(getattr(baseline, name)) is str for name in
                    ("volume_definition_id", "numeric_id", "normalization_id", "training_input_hash", "content_hash"))
            and all(type(getattr(partition, name)) is str for name in
                    ("partition_id", "record_id", "raw_hash", "input_manifest_id",
                     "input_manifest_hash", "volume_definition_id"))
            and type(partition.training_inputs) is tuple
            and all(type(group) is tuple and len(group) == 2 and type(group[0]) is str
                and type(group[1]) is tuple and all(type(item) is str for item in group[1])
                for group in partition.training_inputs)
            and all(type(getattr(partition, name)) is tuple
                    and all(type(day) is date for day in getattr(partition, name))
                    for name in ("training_sessions", "validation_sessions", "test_sessions"))
            and type(baseline.buckets) is tuple
            and all(type(bucket) is VolumeBucket and type(bucket.minute_index) is int
                and type(bucket.sample_count) is int
                and all(item is None or type(item) is Decimal for item in (bucket.mean, bucket.population_stddev))
                and type(bucket.contributing_sessions) is tuple
                and all(type(day) is date for day in bucket.contributing_sessions)
                and type(bucket.reasons) is tuple and all(type(reason) is str for reason in bucket.reasons)
                for bucket in baseline.buckets))
    except (AttributeError, TypeError, ValueError):
        return False


def normalize_calibration_partition(raw: object, *, fixture: VerifiedFixtureManifest, record_id: str,
                                    model: ParsedModelData, spec: FeatureSpec,
                                    normalization: FeatureNormalization,
                                    upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> CalibrationPartitionValidation:
    """Admit one complete partition from current P08, A1, P10 and feature owners.

    :param    raw:                Exact untrusted partition body.
    :param    fixture:            Actual containing P08 fixture.
    :param    record_id:          Trusted containing member identifier.
    :param    model:              Inspected fixed-right model bytes and rows.
    :param    spec:               Actual code-owned feature definition.
    :param    normalization:      Actual P10 artifact and training owner.
    :param    upstream_fixtures:  Referenced P08 source, descriptor and membership roots.
    :returns:                     Exclusive immutable partition or safe reached rejection.
    :raises   TypeError:         If a trusted owner has an unexpected exact type.
    :raises   ValueError:        If the trusted record identifier is empty.
    """
    _trusted(fixture, record_id, upstream_fixtures)
    if type(model) is not ParsedModelData or type(spec) is not FeatureSpec or type(normalization) is not FeatureNormalization:
        raise TypeError("model, spec and normalization require exact A1, feature and P10 owners")
    reached = None
    try:
        _check(_normalization_shape(normalization), "normalization", "retained_content_mismatch")
        _shape(raw, "$", _FIELDS)
        _one(raw["schema_version"], "schema_version")
        partition_id = _identifier(raw["partition_id"], "partition_id")
        refs = {name: _reference(raw[name], name, False) for name in
                ("model_membership_ref", "tuning_membership_ref", "calendar_ref", "normalization_ref")}
        tail_refs = _refs(raw["calibration_sample_refs"], "calibration_sample_refs", 4096)
        fold = _fold(raw["fold"])
        claims = {name: _identifier(raw[name], name) for name in
                  ("feature_schema_id", "transform_id", "normalization_hash", "model_hash", "bucket_rule_id")}
        threshold = None if raw["frozen_threshold_return"] is None else _decimal(raw["frozen_threshold_return"], "frozen_threshold_return")
        frozen = _timestamp(raw["frozen_at"], "frozen_at", True)
        cutoff = _timestamp(raw["tuning_cutoff"], "tuning_cutoff", True)
        _check(_body_size(raw) <= _MAX_BODY, "$", "resource_limit")
        implicit = (normalization.manifest, normalization.baseline.manifest)
        unique = tuple(root for root in implicit if root.fixture_id != fixture.fixture_id
                       and all(root.fixture_id != explicit.fixture_id for explicit in upstream_fixtures))
        roots = _roots(fixture, (*unique, *upstream_fixtures))
        own = _own_member(roots[fixture.fixture_id], record_id, ("calibration_partition",))
        reached = own
        _body(raw, own)
        model_owner = _nested(refs["model_membership_ref"], roots, "model_membership", len(tail_refs))
        tune_owner = _nested(refs["tuning_membership_ref"], roots, "tuning_membership", len(tail_refs) + len(model_owner[3]))
        _check(len(tail_refs) + len(model_owner[3]) + len(tune_owner[3]) <= 4096,
               "sample_refs", "resource_limit")
        _check(type(model.original_model_bytes) is bytes and type(model.model_hash) is str
               and type(model.model_kind) is str and type(model.format_id) is str
               and type(model.rows) is tuple and all(type(row) is ModelDataRow
                   and type(row.right) is str and type(row.calibration_bucket) is str
                   and type(row.mean_attempt_return) is Decimal for row in model.rows),
               "model", "retained_content_mismatch")
        parsed = normalize_model_bytes(model.original_model_bytes, **_receipt(own, fixture))
        _check(parsed.value is not None, "model", "normalization_failed", parsed.rejection)
        actual_model = parsed.value
        _check(actual_model == model and actual_model.model_kind == "fixture_fixed_by_right"
               and len(actual_model.rows) == 2 and all(type(row) is ModelDataRow for row in actual_model.rows)
               and len({row.calibration_bucket for row in actual_model.rows}) == 2,
               "model", "model_mismatch")
        _check(spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC, "spec", "unsupported_spec")
        _check(_snapshot_hash(spec.schema_snapshot) == spec.feature_schema_id == claims["feature_schema_id"]
               and _snapshot_hash(spec.transform_snapshot) == spec.transform_id == claims["transform_id"],
               "feature_schema_id", "definition_mismatch")
        norm_ref = refs["normalization_ref"]
        norm_root, norm_member = _resolve(norm_ref, roots, "feature_normalization")
        _check(norm_root.fixture_id == normalization.manifest.fixture_id
               and norm_member.record_id == normalization.record_id and norm_member.raw_hash == normalization.raw_hash,
               "normalization_ref", "counterpart_mismatch")
        training = roots.get(normalization.baseline.manifest.fixture_id)
        _check(training is not None, "normalization", "unknown_fixture")
        recomputed = normalize_feature_normalization(norm_member.decode_raw_body(), manifest=norm_root,
            record_id=norm_member.record_id, training_manifest=training, decision_at=normalization.available_at)
        _check(recomputed.value is not None, "normalization", "recomputation_failed", recomputed)
        actual_norm = recomputed.value
        _check(actual_norm.snapshot == normalization.snapshot and actual_norm.content_hash == normalization.content_hash
               and actual_norm.baseline.snapshot == normalization.baseline.snapshot
               and actual_norm.baseline.partition == normalization.baseline.partition,
               "normalization", "retained_content_mismatch", recomputed)
        _check(claims["normalization_hash"] == actual_norm.content_hash
               and claims["model_hash"] == actual_model.model_hash
               and claims["bucket_rule_id"] == BUCKET_RULE_ID,
               "binding", "counterpart_mismatch")
        memberships = []
        for owner, role in ((model_owner, "model"), (tune_owner, "tuning")):
            root, member, body, sample_refs = owner
            result = normalize_fit_membership(body, fixture=root, record_id=member.record_id,
                model=actual_model, upstream_fixtures=_sources(sample_refs, roots, root))
            _check(result.value is not None and result.value.role == role,
                   role + "_membership_ref", "normalization_failed", result.rejection)
            memberships.append(result.value)
        calendar_root, calendar_member = _resolve(refs["calendar_ref"], roots, "calendar_descriptor")
        calendar_body = calendar_member.decode_raw_body()
        _shape(calendar_body, "calendar", ("schema_version", "calendar_id", "calendar",
            "coverage_start_date", "coverage_end_date", "session_refs", "closed_dates", "available_at"))
        calendar_refs = _refs(calendar_body["session_refs"], "calendar.session_refs", 4096)
        calendar_result = normalize_calendar_descriptor(calendar_body, fixture=calendar_root,
            record_id=calendar_member.record_id, upstream_fixtures=_sources(calendar_refs, roots, calendar_root))
        _check(calendar_result.value is not None, "calendar_ref", "normalization_failed", calendar_result.rejection)
        calendar = calendar_result.value
        _check(calendar.coverage_start_date <= fold.calibration_tail_start_date
               and calendar.coverage_end_date >= fold.calibration_tail_end_date - timedelta(days=1),
               "calendar_ref", "incomplete_tail")
        buckets = {row.right: row.calibration_bucket for row in actual_model.rows}
        tail = []
        for ref in tail_refs:
            source, member = _resolve(ref, roots, "fit_sample")
            reached = member
            row = _sample(source, member)
            _check(row.bucket_id == buckets[row.contract.right], "calibration_sample_refs", "bucket_mismatch")
            tail.append(row)
        all_rows = tuple(memberships[0].rows) + tuple(memberships[1].rows) + tuple(tail)
        identities = [(row.decision_id, row.contract) for row in all_rows]
        _check(len({row.sample_id for row in all_rows}) == len(all_rows)
               and len(set(identities)) == len(all_rows), "sample_refs", "duplicate_outcome")
        sessions = tuple(item.session.session_date for item in calendar.sessions)
        _check(all(row.session_date in sessions for row in all_rows), "calendar_ref", "uncovered_sample")
        _check(all(fold.training_start_date <= row.session_date < fold.training_end_date for row in memberships[0].rows)
               and all(fold.tuning_start_date <= row.session_date < fold.tuning_end_date for row in memberships[1].rows)
               and all(fold.calibration_tail_start_date <= row.session_date < fold.calibration_tail_end_date for row in tail),
               "fold", "population_outside_window")
        tail_days = tuple(day for day in sessions if fold.calibration_tail_start_date <= day < fold.calibration_tail_end_date)
        _check(bool(tail_days) and all(day in tail_days for day in (row.session_date for row in tail)),
               "calendar_ref", "incomplete_tail")
        pairs = tuple(tail_days[index:index + 2] for index in range(0, len(tail_days), 2))
        used_ids = {ref.fixture_id for ref in (*refs.values(), *tail_refs, *model_owner[3], *tune_owner[3], *calendar_refs)}
        used_ids.add(training.fixture_id)
        _check(all(root.fixture_id in used_ids for root in upstream_fixtures), "upstream_fixtures", "unused_reference")
        value = _make(CalibrationPartition, partition_id=partition_id,
            model_membership_ref=refs["model_membership_ref"], tuning_membership_ref=refs["tuning_membership_ref"],
            calibration_sample_refs=tail_refs, calendar_ref=refs["calendar_ref"], fold=fold,
            feature_schema_id=claims["feature_schema_id"], transform_id=claims["transform_id"],
            normalization_ref=norm_ref, normalization_hash=claims["normalization_hash"],
            model_hash=claims["model_hash"], frozen_threshold_return=threshold,
            bucket_rule_id=BUCKET_RULE_ID, frozen_at=frozen, tuning_cutoff=cutoff,
            model_membership=memberships[0], tuning_membership=memberships[1],
            calibration_members=tuple(tail), calendar=calendar, normalization=actual_norm,
            model=actual_model, spec=spec, fixture=roots[fixture.fixture_id], member=own,
            upstream_fixtures=tuple(root for root in roots.values()
                                    if root.fixture_id in used_ids and root.fixture_id != fixture.fixture_id),
            session_pairs=pairs, content_hash="")
        object.__setattr__(value, "content_hash", _snapshot_hash(value.snapshot()))
    except (_InvalidInput, _Failure, _UnsupportedIdentity, MemoryError, RecursionError) as failure:
        return _rejected(CalibrationPartitionValidation, "calibration_partition", fixture, reached, failure)
    return _make(CalibrationPartitionValidation, value=value, rejection=None)
