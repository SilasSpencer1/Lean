"""Total admission, actual recomputation and availability of frozen volume artifacts."""

from dataclasses import dataclass
from datetime import datetime
import re

from ._input_parsing import (
    _InvalidInput, _fail, _parse_date, _parse_string, _parse_timestamp,
    _parse_token, _require_shape,
)
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import VerifiedFixtureManifest, _canonical_bytes
from .bar_inputs import _parse_amount, _identity_decimal, _UnsupportedIdentity
from .config import _snapshot_hash
from .observations import AvailabilityBasis, _AVAILABILITY_BASES
from .volume import VolumeBaseline, VolumeBaselineFit, fit_volume_baseline
from .volume_inputs import (
    _declared_inputs, _envelope, _freeze, _list, _require_manifest,
    normalize_volume_partition,
)


_GENERATOR = "optionslab-volume-normalization-fixture-builder"
_FIELDS = ("schema_version", "training_fixture_id", "training_payload_sha256",
           "partition_record_id", "cutoff", "baseline_snapshot", "baseline_content_hash",
           "available_at", "availability_basis")
_BASELINE_FIELDS = ("record_kind", "schema_version", "cutoff", "partition",
                    "volume_definition_id", "numeric_id", "normalization_id",
                    "training_input_hash", "buckets")
_PARTITION_FIELDS = ("partition_id", "record_id", "raw_hash", "input_manifest_id",
                     "input_manifest_hash", "training_sessions", "validation_sessions", "test_sessions")
_BUCKET_FIELDS = ("minute_index", "mean", "population_stddev", "sample_count",
                  "contributing_sessions", "reasons")
_BUCKET_REASONS = ("insufficient_training_sessions", "population_stddev_zero",
                   "arithmetic_precision_unsupported")


@dataclass(frozen=True, init=False)
class FeatureNormalization:
    """This class represents an actually recomputed, available frozen artifact.

    The baseline retains its actual upstream training manifest. The separate
    artifact manifest remains synthetic evidence and grants no live authority.
    """

    baseline: VolumeBaseline
    available_at: datetime
    availability_basis: AvailabilityBasis
    manifest: VerifiedFixtureManifest
    record_id: str
    raw_hash: str
    content_hash: str

    def __init__(self) -> None:
        """Prevent caller-written scaler values or availability from minting evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_feature_normalization.
        """
        raise TypeError("FeatureNormalization values come from normalization")

    @property
    def snapshot(self) -> dict[str, object]:
        """Return fresh semantic dependency fields, excluding containing-root identity.

        :returns: A noncircular snapshot suitable for canonical content hashing.
        """
        baseline = self.baseline
        return {
            "record_kind": "options_lab.feature_normalization", "schema_version": 1,
            "record_id": self.record_id, "baseline_content_hash": baseline.content_hash,
            "training_input_hash": baseline.training_input_hash,
            "training_fixture_id": baseline.manifest.fixture_id,
            "training_payload_sha256": baseline.manifest.payload_sha256,
            "partition_record_id": baseline.partition.record_id,
            "partition_raw_hash": baseline.partition.raw_hash,
            "available_at": self.available_at.isoformat(),
            "availability_basis": self.availability_basis,
        }


@dataclass(frozen=True, init=False)
class FeatureNormalizationInputRejection:
    """This class represents a bounded parsing/member rejection with actual receipt facts."""

    manifest: VerifiedFixtureManifest
    record_id: str
    event_id: str
    received_at: datetime
    raw_ref: str
    field: str
    code: str

    def __init__(self) -> None:
        """Prevent caller-built safe-failure evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_feature_normalization.
        """
        raise TypeError("FeatureNormalizationInputRejection values come from normalization")

    @property
    def stage(self) -> str:
        """Return this concrete raw artifact boundary's fixed stage.

        :returns: The feature_normalization stage.
        """
        return "feature_normalization"


@dataclass(frozen=True, init=False)
class FeatureNormalizationResult:
    """This class represents actual artifact evaluation and only the fit it reached."""

    value: FeatureNormalization | None
    rejection: FeatureNormalizationInputRejection | None
    fit: VolumeBaselineFit | None
    reasons: tuple[str, ...]
    manifest: VerifiedFixtureManifest
    training_manifest: VerifiedFixtureManifest
    record_id: str
    decision_at: datetime

    def __init__(self) -> None:
        """Prevent callers from substituting reached computation or success evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_feature_normalization.
        """
        raise TypeError("FeatureNormalizationResult values come from normalization")


def normalize_feature_normalization(
    raw: object, *, manifest: VerifiedFixtureManifest, record_id: str,
    training_manifest: VerifiedFixtureManifest, decision_at: datetime,
) -> FeatureNormalizationResult:
    """Validate actual B bytes and recompute its complete baseline from actual A.

    Shape failures retain no invented fit. Explicit unknown availability is
    assessed after recomputation. Cutoff, artifact time and decision stay distinct.

    :param raw: Untrusted full frozen artifact body.
    :param manifest: Actual admitted artifact manifest B.
    :param record_id: Trusted registered artifact member identifier.
    :param training_manifest: Explicit admitted upstream manifest A.
    :param decision_at: Original aware decision assessing artifact availability.
    :returns: Available frozen descriptor or safe rejection/reached fit evidence.
    :raises TypeError: If a trusted argument has an unexpected exact type.
    :raises ValueError: If trusted identity is empty or decision_at is naive.
    """
    _require_manifest(manifest)
    _require_manifest(training_manifest)
    _require_nonempty_string("record_id", record_id)
    decision_at = _trusted_datetime("decision_at", decision_at)
    member = next((m for m in manifest.members if m.record_id == record_id), None)
    value = rejection = fit = None
    reasons = []
    try:
        cutoff, available_at, basis = _parse_artifact(raw)
        if member is None:
            _fail("record_id", "unknown_member")
        if member.kind != "feature_normalization":
            _fail("record_id", "member_kind_mismatch")
        if _canonical_bytes(raw) != member.raw_body_bytes:
            _fail("$", "member_content_mismatch")
        profile = next(p for p in manifest.decode_modeled_source_profiles() if p["profile_id"] == member.profile_id)
        if manifest.generator_id != _GENERATOR or profile["source"] != _GENERATOR:
            _fail("record_id", "profile_mismatch")
        if member.decode_envelope()["supersedes_record_id"] is not None:
            _fail("record_id", "artifact_not_immutable")
        if basis != profile["availability_basis"]:
            _fail("availability_basis", "profile_mismatch")
        if manifest.payload_sha256 == training_manifest.payload_sha256:
            reasons.append("training_manifest_is_artifact")
        if (raw["training_fixture_id"] != training_manifest.fixture_id or
                raw["training_payload_sha256"] != training_manifest.payload_sha256):
            reasons.append("training_manifest_mismatch")
        if not reasons:
            partition_member = next((m for m in training_manifest.members if m.record_id == raw["partition_record_id"]), None)
            if partition_member is None:
                reasons.append("partition_unknown_member")
            elif partition_member.kind != "volume_partition":
                reasons.append("partition_member_kind_mismatch")
            else:
                normalized = normalize_volume_partition(partition_member.decode_raw_body(),
                                                       manifest=training_manifest, record_id=partition_member.record_id)
                if normalized.value is None:
                    reasons.append("partition_" + normalized.rejection.code)
                else:
                    groups, _ = _declared_inputs(normalized.value, training_manifest, cutoff)
                    fit = fit_volume_baseline(groups, cutoff, partition=normalized.value, manifest=training_manifest)
                    reasons.extend(fit.reasons)
                    if fit.baseline is not None:
                        if raw["baseline_snapshot"] != fit.baseline.snapshot:
                            reasons.append("baseline_snapshot_mismatch")
                        if raw["baseline_content_hash"] != fit.baseline.content_hash:
                            reasons.append("baseline_content_hash_mismatch")
                        if available_at is None:
                            reasons.append("availability_unknown")
                        elif available_at < fit.baseline.cutoff:
                            reasons.append("availability_before_cutoff")
                        if available_at is not None and available_at > decision_at:
                            reasons.append("available_after_decision")
                        if not reasons:
                            value = _freeze(FeatureNormalization, baseline=fit.baseline,
                                            available_at=available_at, availability_basis=basis,
                                            manifest=manifest, record_id=record_id, raw_hash=member.raw_hash)
                            object.__setattr__(value, "content_hash", _snapshot_hash(value.snapshot))
    except _InvalidInput as failure:
        envelope = _envelope(member) if member is not None else dict(
            event_id=manifest.event_id, received_at=manifest.received_at, raw_ref=manifest.raw_ref)
        rejection = _freeze(FeatureNormalizationInputRejection, manifest=manifest,
                            record_id=record_id, **envelope, field=failure.args[0], code=failure.args[1])
        reasons.append("normalization_failed")
    return _freeze(FeatureNormalizationResult, value=value, rejection=rejection, fit=fit,
                   reasons=tuple(sorted(set(reasons))), manifest=manifest, training_manifest=training_manifest,
                   record_id=record_id, decision_at=decision_at)


def _parse_artifact(raw):
    """Check every exact nested type before comparing caller leaves to actual bytes."""
    _require_shape(raw, "$", _FIELDS)
    _version(raw["schema_version"], "schema_version")
    for name in ("training_fixture_id", "partition_record_id"):
        _parse_string(raw[name], name)
    for name in ("training_payload_sha256", "baseline_content_hash"):
        _hash(raw[name], name)
    cutoff = _parse_timestamp(raw["cutoff"], "cutoff")
    available_at = _parse_timestamp(raw["available_at"], "available_at", nullable=True)
    basis = _parse_token(raw["availability_basis"], "availability_basis", _AVAILABILITY_BASES)
    snapshot = raw["baseline_snapshot"]
    path = "baseline_snapshot"
    _require_shape(snapshot, path, _BASELINE_FIELDS)
    _parse_token(snapshot["record_kind"], path + ".record_kind", ("options_lab.volume_baseline",))
    _version(snapshot["schema_version"], path + ".schema_version")
    _parse_timestamp(snapshot["cutoff"], path + ".cutoff")
    _parse_string(snapshot["volume_definition_id"], path + ".volume_definition_id")
    for name in ("numeric_id", "normalization_id", "training_input_hash"):
        _hash(snapshot[name], path + "." + name)
    partition = snapshot["partition"]
    _require_shape(partition, path + ".partition", _PARTITION_FIELDS)
    for name in ("partition_id", "record_id", "input_manifest_id"):
        _parse_string(partition[name], path + ".partition." + name)
    for name in ("raw_hash", "input_manifest_hash"):
        _hash(partition[name], path + ".partition." + name)
    for name in ("training_sessions", "validation_sessions", "test_sessions"):
        _dates(partition[name], path + ".partition." + name)
    for index, bucket in enumerate(_list(snapshot["buckets"], path + ".buckets")):
        field = path + f".buckets[{index}]"
        _require_shape(bucket, field, _BUCKET_FIELDS)
        _integer(bucket["minute_index"], field + ".minute_index", minimum=1)
        _integer(bucket["sample_count"], field + ".sample_count", minimum=0)
        for name in ("mean", "population_stddev"):
            amount = _parse_amount(bucket[name], field + "." + name)
            try:
                _identity_decimal(amount)
            except _UnsupportedIdentity:
                _fail(field + "." + name, "decimal_representation_unsupported")
        _dates(bucket["contributing_sessions"], field + ".contributing_sessions")
        for reason in _list(bucket["reasons"], field + ".reasons"):
            _parse_token(reason, field + ".reasons", _BUCKET_REASONS)
    return cutoff, available_at, basis


def _hash(raw, field):
    """Require exact lowercase SHA-256 spelling without touching hostile equality."""
    parsed = _parse_string(raw, field)
    if re.fullmatch(r"[0-9a-f]{64}", parsed) is None:
        _fail(field, "invalid_value")


def _integer(raw, field, *, minimum):
    """Bound exact JSON integers before canonical serialization."""
    if type(raw) is not int:
        _fail(field, "invalid_type")
    if raw < minimum or raw >= 10 ** 1000:
        _fail(field, "invalid_value")


def _version(raw, field):
    """Parse the single current schema version excluding bool and subclasses."""
    _integer(raw, field, minimum=1)
    if raw != 1:
        _fail(field, "invalid_value")


def _dates(raw, field):
    """Validate each original exact date leaf, leaving economic comparison to fitting."""
    for value in _list(raw, field):
        _parse_date(value, field)
