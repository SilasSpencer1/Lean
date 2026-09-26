"""Admit finite assembly checks against actual model, runtime and calibration owners."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from ._input_parsing import _InvalidInput
from .admission import VerifiedFixtureManifest, VerifiedFixtureMember
from .bar_inputs import _UnsupportedIdentity
from .bundle_inputs import ParsedModelData, _identifier, _make, _shape, normalize_model_bytes
from .bundle_manifest_inputs import ExternalReference, _one, _reference, _timestamp
from .calibration import CalibrationMetadata, CalibrationPartition, _normalization_shape, normalize_calibration_metadata
from .calibration_inputs import (_Failure, _body, _body_size, _check, _matches_retained, _rejected,
                                 _retained_fixture_shape, _roots, _trusted)
from .candidates import SELECTION_RULE_ID, _selection_definition_snapshot
from .config import StrategyConfig, _snapshot_hash, config_hash, policy_hash
from .feature_vector import CLOSE_VOLUME_PROXY_SPEC, EXACT_VWAP_SPEC, FeatureSpec
from .runtime import RuntimeEvidence, measure_runtime
from .volume_normalization import FeatureNormalization, normalize_feature_normalization


_GENERATOR = "optionslab-bundle-validation-fixture-builder"
_FIELDS = ("schema_version", "validation_id", "method_id", "performed_at", "model_hash",
    "feature_schema_id", "transform_id", "normalization_hash", "calibration_partition_ref",
    "calibration_record_ref", "execution_policy_hash", "selection_rule_id",
    "implementation_digest", "runtime_contract_digest")
_METHOD = dict(record_kind="options_lab.bundle_assembly_validation_method", schema_version=1,
    checks=("exact_original_model_bytes_reparsed_and_retained", "current_a0_remeasured_safe_full_counterpart_compare",
        "actual_config_execution_and_selection_definitions", "fixed_code_owned_feature_spec",
        "fixed_p10_recomputed_from_admitted_bytes", "fixed_c_record_and_partition_recomputed_from_admitted_bytes",
        "cash_requires_null_feature_and_calibration"))
BUNDLE_ASSEMBLY_METHOD_ID = _snapshot_hash(_METHOD)


@dataclass(frozen=True, init=False)
class BundleAssemblyValidation:
    """This class represents source-bound finite assembly evidence and checked owners."""

    validation_id: str
    method_id: str
    performed_at: datetime
    model_hash: str
    feature_schema_id: str | None
    transform_id: str | None
    normalization_hash: str | None
    calibration_partition_ref: ExternalReference | None
    calibration_record_ref: ExternalReference | None
    execution_policy_hash: str
    selection_rule_id: str
    implementation_digest: str
    runtime_contract_digest: str
    model: ParsedModelData
    spec: FeatureSpec | None
    normalization: FeatureNormalization | None
    partition: CalibrationPartition | None
    metadata: CalibrationMetadata | None
    config: StrategyConfig
    runtime: RuntimeEvidence
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    content_hash: str

    def __init__(self) -> None:
        """Block caller-authored validation evidence.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_validation.
        """
        raise TypeError("BundleAssemblyValidation comes from normalize_bundle_validation")


@dataclass(frozen=True, init=False)
class BundleAssemblyValidationResult:
    """This class represents one admitted validation report or bounded rejection."""

    value: BundleAssemblyValidation | None
    rejection: object | None

    def __init__(self) -> None:
        """Block caller-authored validation results.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_validation.
        """
        raise TypeError("BundleAssemblyValidationResult comes from normalize_bundle_validation")


def _execution_hash(config: StrategyConfig) -> str:
    """Hash the actual complete execution policy without importing bundle A2."""
    from .config import config_snapshot
    return _snapshot_hash(dict(record_kind="options_lab.execution_policy", schema_version=1,
        target_definition_id=config.execution.target_definition,
        execution=config_snapshot(config)["policy"]["execution"]))


def _checked_inputs(model, spec, normalization, partition, metadata, config, runtime, roots):
    """Perform the closed method's finite checks before any report timestamp."""
    if (type(model) is not ParsedModelData or type(config) is not StrategyConfig
            or type(runtime) is not RuntimeEvidence or type(roots) is not tuple
            or any(type(root) is not VerifiedFixtureManifest for root in roots)
            or (spec is not None and type(spec) is not FeatureSpec)
            or (normalization is not None and type(normalization) is not FeatureNormalization)
            or (partition is not None and type(partition) is not CalibrationPartition)
            or (metadata is not None and type(metadata) is not CalibrationMetadata)):
        raise TypeError("assembly checks require exact model, feature, calibration, config, runtime and root owners")
    try:
        raw_bytes = model.original_model_bytes
        _check(type(raw_bytes) is bytes and len(raw_bytes) <= 65536, "model", "retained_content_mismatch")
        inspected = normalize_model_bytes(raw_bytes, event_id="bundle-validation",
            raw_ref="synthetic://bundle-validation", received_at=datetime.now(timezone.utc))
        _check(inspected.value is not None, "model", "normalization_failed", inspected.rejection)
        checked_model = inspected.value
        _check(_matches_retained(model, checked_model, set()), "model", "retained_content_mismatch")
        checked = measure_runtime()
        _check(checked.value is not None, "runtime", "runtime_unavailable", checked.rejection)
        checked_runtime = checked.value
        _check(_matches_retained(runtime, checked_runtime, set()), "runtime", "retained_content_mismatch")
        _check(SELECTION_RULE_ID == _snapshot_hash(_selection_definition_snapshot())
               and _selection_definition_snapshot()["vocabulary"] == config.selection_rule,
               "selection_rule_id", "definition_mismatch")
        # Evaluate both complete configuration owners, even though this schema retains only execution.
        config_hash(config), policy_hash(config)
        fields = dict(model_hash=checked_model.model_hash, feature_schema_id=None, transform_id=None,
            normalization_hash=None, calibration_partition_ref=None, calibration_record_ref=None,
            execution_policy_hash=_execution_hash(config), selection_rule_id=SELECTION_RULE_ID,
            implementation_digest=checked_runtime.implementation_digest,
            runtime_contract_digest=checked_runtime.runtime_contract_digest)
        checked_norm = checked_partition = checked_metadata = None
        used = set()
        if checked_model.model_kind == "cash":
            _check(spec is None and normalization is None and partition is None and metadata is None,
                   "binding", "cash_counterpart_present")
        else:
            _check((spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC)
                   and normalization is not None and partition is not None and metadata is not None,
                   "binding", "missing_counterpart")
            _check(_normalization_shape(normalization)
                   and _retained_fixture_shape(normalization.manifest)
                   and _retained_fixture_shape(normalization.baseline.manifest),
                   "normalization", "retained_content_mismatch")
            by_id = {root.fixture_id: root for root in roots}
            norm_root = by_id.get(normalization.manifest.fixture_id)
            training = by_id.get(normalization.baseline.manifest.fixture_id)
            _check(norm_root is not None and training is not None, "normalization", "unknown_fixture")
            member = next((m for m in norm_root.members if m.record_id == normalization.record_id), None)
            _check(member is not None and member.kind == "feature_normalization", "normalization", "unknown_member")
            normalized = normalize_feature_normalization(member.decode_raw_body(), manifest=norm_root,
                record_id=member.record_id, training_manifest=training, decision_at=normalization.available_at)
            _check(normalized.value is not None, "normalization", "normalization_failed", normalized)
            checked_norm = normalized.value
            _check(_matches_retained(normalization, checked_norm, set()),
                   "normalization", "retained_content_mismatch")
            _check(spec.feature_schema_id == _snapshot_hash(spec.schema_snapshot)
                   and spec.transform_id == _snapshot_hash(spec.transform_snapshot),
                   "spec", "definition_mismatch")
            try:
                retained = (_retained_fixture_shape(metadata.fixture)
                    and type(metadata.member) is VerifiedFixtureMember
                    and type(metadata.member.record_id) is str
                    and type(metadata.member.raw_body_bytes) is bytes
                    and type(metadata.upstream_fixtures) is tuple
                    and len(metadata.upstream_fixtures) <= 64
                    and all(_retained_fixture_shape(root) for root in metadata.upstream_fixtures)
                    and _retained_fixture_shape(partition.fixture))
            except AttributeError:
                retained = False
            _check(retained, "calibration_record", "retained_content_mismatch")
            record_root = by_id.get(metadata.fixture.fixture_id)
            _check(record_root is not None, "calibration_record", "unknown_fixture")
            record_member = next((m for m in record_root.members if m.record_id == metadata.member.record_id), None)
            _check(record_member is not None and record_member.kind == "calibration_record",
                   "calibration_record", "unknown_member")
            needed = tuple(by_id[root.fixture_id] for root in metadata.upstream_fixtures
                           if root.fixture_id in by_id and root.fixture_id != record_root.fixture_id)
            _check(len(needed) == len(metadata.upstream_fixtures), "calibration_record", "unknown_fixture")
            recomputed = normalize_calibration_metadata(record_member.decode_raw_body(),
                fixture=record_root, record_id=record_member.record_id, model=checked_model, spec=spec,
                normalization=checked_norm, upstream_fixtures=needed)
            _check(recomputed.value is not None, "calibration_record", "normalization_failed", recomputed.rejection)
            checked_metadata = recomputed.value
            checked_partition = checked_metadata.partition
            _check(_matches_retained(metadata, checked_metadata, set())
                   and _matches_retained(partition, checked_partition, set()),
                   "calibration_record", "retained_content_mismatch")
            fields.update(feature_schema_id=spec.feature_schema_id, transform_id=spec.transform_id,
                normalization_hash=checked_norm.content_hash,
                calibration_partition_ref=checked_metadata.partition_ref,
                calibration_record_ref=_reference(dict(fixture_id=record_root.fixture_id,
                    payload_sha256=record_root.payload_sha256, record_id=record_member.record_id,
                    raw_hash=record_member.raw_hash), "calibration_record_ref", False))
            used.update((norm_root.fixture_id, training.fixture_id, record_root.fixture_id,
                         checked_partition.fixture.fixture_id))
            used.update(root.fixture_id for root in checked_metadata.upstream_fixtures)
        return fields, (checked_model, checked_norm, checked_partition, checked_metadata, checked_runtime, used)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError) as error:
        raise _Failure("binding", "retained_content_mismatch", None) from error


def normalize_bundle_validation(raw: object, *, fixture: VerifiedFixtureManifest, record_id: str,
                                model: ParsedModelData, spec: FeatureSpec | None,
                                normalization: FeatureNormalization | None,
                                partition: CalibrationPartition | None, metadata: CalibrationMetadata | None,
                                config: StrategyConfig, runtime: RuntimeEvidence,
                                upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> BundleAssemblyValidationResult:
    """Admit exact report bytes and rerun the same finite assembly method.

    :param raw: Exact untrusted report body.
    :param fixture: Admitted containing P08 root.
    :param record_id: Trusted containing member identifier.
    :param model: Actual parsed original model bytes.
    :param spec: Code owned feature definition or None for cash.
    :param normalization: Actual P10 artifact or None for cash.
    :param partition: Actual calibration partition or None for cash.
    :param metadata: Actual calibration record or None for cash.
    :param config: Actual strategy configuration.
    :param runtime: Current A0 evidence.
    :param upstream_fixtures: Exact admitted roots required by these owners.
    :returns: Source bound report and checked owners, or bounded reached rejection.
    :raises TypeError: If a trusted owner has an unexpected exact type.
    :raises ValueError: If the trusted record identifier is empty.
    """
    _trusted(fixture, record_id, upstream_fixtures)
    if (type(model) is not ParsedModelData or type(config) is not StrategyConfig
            or type(runtime) is not RuntimeEvidence or (spec is not None and type(spec) is not FeatureSpec)
            or (normalization is not None and type(normalization) is not FeatureNormalization)
            or (partition is not None and type(partition) is not CalibrationPartition)
            or (metadata is not None and type(metadata) is not CalibrationMetadata)):
        raise TypeError("model, spec, normalization, calibration, config and runtime require exact owners")
    reached = None
    try:
        _shape(raw, "$", _FIELDS)
        _one(raw["schema_version"], "schema_version")
        validation_id = _identifier(raw["validation_id"], "validation_id")
        method_id = _identifier(raw["method_id"], "method_id")
        performed = _timestamp(raw["performed_at"], "performed_at")
        fields = {name: None if raw[name] is None else _identifier(raw[name], name)
                  for name in ("model_hash", "feature_schema_id", "transform_id", "normalization_hash",
                               "execution_policy_hash", "selection_rule_id", "implementation_digest",
                               "runtime_contract_digest")}
        fields["calibration_partition_ref"] = _reference(raw["calibration_partition_ref"], "calibration_partition_ref")
        fields["calibration_record_ref"] = _reference(raw["calibration_record_ref"], "calibration_record_ref")
        _check(_body_size(raw) <= 8 * 1024 * 1024, "$", "resource_limit")
        roots = _roots(fixture, upstream_fixtures)
        own = next((m for m in roots[fixture.fixture_id].members if m.record_id == record_id), None)
        _check(own is not None, "record_id", "unknown_member")
        _check(len(own.raw_body_bytes) <= 8 * 1024 * 1024, "record_id", "resource_limit")
        _check(own.kind == "bundle_validation" and hashlib.sha256(own.raw_body_bytes).hexdigest() == own.raw_hash,
               "record_id", "member_kind_mismatch")
        profile = next((p for p in fixture.decode_modeled_source_profiles() if p["profile_id"] == own.profile_id), None)
        _check(profile is not None and profile["kind"] == own.kind and profile["source"] == fixture.generator_id == _GENERATOR
               and fixture.generator_version == "1" and own.decode_envelope()["supersedes_record_id"] is None,
               "record_id", "profile_mismatch")
        reached = own
        _body(raw, own)
        _check(method_id == BUNDLE_ASSEMBLY_METHOD_ID, "method_id", "unsupported_method")
        _check(performed <= fixture.assembled_at, "performed_at", "performed_after_assembly")
        expected, checked = _checked_inputs(model, spec, normalization, partition, metadata, config, runtime,
            tuple(roots.values()))
        _check(fields == expected, "binding", "counterpart_mismatch")
        actual_model, actual_norm, actual_partition, actual_metadata, actual_runtime, used = checked
        _check(all(root.fixture_id in used for root in roots.values() if root.fixture_id != fixture.fixture_id),
               "upstream_fixtures", "unused_reference")
        value = _make(BundleAssemblyValidation, validation_id=validation_id, method_id=method_id,
            performed_at=performed, **fields, model=actual_model, spec=spec, normalization=actual_norm,
            partition=actual_partition, metadata=actual_metadata, config=config, runtime=actual_runtime,
            fixture=roots[fixture.fixture_id], member=own,
            upstream_fixtures=tuple(root for root in roots.values() if root.fixture_id in used
                                    and root.fixture_id != fixture.fixture_id),
            content_hash=_snapshot_hash(dict(record_kind="options_lab.bundle_assembly_validation", **raw)))
    except (_InvalidInput, _Failure, _UnsupportedIdentity, MemoryError, RecursionError) as failure:
        return _rejected(BundleAssemblyValidationResult, "bundle_validation", fixture, reached, failure)
    return _make(BundleAssemblyValidationResult, value=value, rejection=None)
