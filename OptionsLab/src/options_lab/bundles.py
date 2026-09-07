"""Verify actual bundle content without calibration, readiness or trading permission."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib

from ._input_parsing import _InvalidInput, _parse_timestamp
from .admission import (FixtureInputRejection, VerifiedFixtureManifest, VerifiedFixtureMember,
                        _canonical_bytes, verify_fixture_bundle)
from .bar_inputs import _UnsupportedIdentity
from .bundle_inputs import BundleInputRejection, ParsedModelData, _make, _shape, normalize_model_bytes
from .bundle_manifest_inputs import ModelBundleManifest, QualifiedProfile, normalize_bundle_manifest
from .candidates import SELECTION_RULE_ID, _selection_definition_snapshot
from .config import StrategyConfig, _snapshot_hash, config_hash, config_snapshot, policy_hash
from .feature_math import NUMERIC_CONVENTION_ID, _numeric_snapshot
from .feature_vector import FeatureSpec, EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC
from .greeks import FIXTURE_GREEK_METHOD, _method_hash
from .runtime import RuntimeEvidence, RuntimeRejection, recheck_runtime
from .volume import VolumeBaseline, VOLUME_NORMALIZATION_ID
from .volume_normalization import FeatureNormalization, FeatureNormalizationResult, normalize_feature_normalization
from .vwap_features import ELIGIBLE_VOLUME_DEFINITION_ID


_GENERATOR = "optionslab-bundle-fixture-builder"
_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, init=False)
class BundleSourceProfile:
    """This class represents declared profile bytes and actual members, not eligibility."""

    claim: QualifiedProfile
    manifest: VerifiedFixtureManifest
    profile_bytes: bytes
    members: tuple[VerifiedFixtureMember, ...]

    def __init__(self) -> None:
        """Block caller-selected source evidence.

        :returns: None.
        :raises TypeError: Always; use verify_bundle.
        """
        raise TypeError("BundleSourceProfile values come from verify_bundle")


@dataclass(frozen=True, init=False)
class VerifiedBundle:
    """This class represents verified CONTENT and retained actual upstream authority.

    Missing source-role coverage and support references remain explicit facts.
    Later consumers must check every actual required input against these claims.
    """

    manifest: ModelBundleManifest
    model: ParsedModelData
    bundle_hash: str
    original_fixture: VerifiedFixtureManifest
    fixture: VerifiedFixtureManifest
    member: VerifiedFixtureMember
    supplied_runtime: RuntimeEvidence
    runtime: RuntimeEvidence
    config: StrategyConfig
    spec: FeatureSpec | None
    supplied_normalization: FeatureNormalization | None
    normalization_result: FeatureNormalizationResult | None
    upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    supplied_upstream_fixtures: tuple[VerifiedFixtureManifest, ...]
    source_profiles: tuple[BundleSourceProfile, ...]
    origin: str = field(default="synthetic", init=False)
    fidelity_tier: int = field(default=0, init=False)
    permitted_use: str = field(default="core_fixture", init=False)
    operational_allowed: bool = field(default=False, init=False)
    economic_allowed: bool = field(default=False, init=False)

    def __init__(self) -> None:
        """Block caller-selected successful bundle evidence.

        :returns: None.
        :raises TypeError: Always; use verify_bundle.
        """
        raise TypeError("VerifiedBundle values come from verify_bundle")


@dataclass(frozen=True, init=False)
class BundleRejection:
    """This class represents a safe content failure and only the owner it reached."""

    manifest: ModelBundleManifest
    fixture: VerifiedFixtureManifest
    event_id: str
    raw_ref: str
    received_at: datetime
    field: str
    code: str
    cause: RuntimeRejection | FixtureInputRejection | BundleInputRejection | FeatureNormalizationResult | None
    stage: str = field(default="bundle_verification", init=False)

    def __init__(self) -> None:
        """Block caller-written failure evidence.

        :returns: None.
        :raises TypeError: Always; use verify_bundle.
        """
        raise TypeError("BundleRejection values come from verify_bundle")


@dataclass(frozen=True, init=False)
class BundleVerification:
    """This class represents exactly one actual content success or bounded failure."""

    value: VerifiedBundle | None
    rejection: BundleRejection | None

    def __init__(self) -> None:
        """Block caller-selected verification outcomes.

        :returns: None.
        :raises TypeError: Always; use verify_bundle.
        """
        raise TypeError("BundleVerification values come from verify_bundle")


class _BundleFailure(Exception):
    """This class represents private fixed-field rejection with reached owner facts."""


def _check(condition, path, code="counterpart_mismatch", cause=None):
    """Reject one failed concrete comparison without exposing raw content."""
    if not condition:
        raise _BundleFailure(path, code, cause)


def _authorities(fixture, normalization, explicit):
    """Bound distinct retained bytes before any payload or member decoding."""
    _check(len(explicit) <= 64, "upstream_fixtures", "resource_limit")
    implicit = () if normalization is None else (normalization.manifest, normalization.baseline.manifest)
    roots, identifiers, supplied_ids = {}, {}, set()
    total = len(fixture.payload_bytes)
    _check(total <= _MAX_PAYLOAD_BYTES, "fixtures", "resource_limit")
    for index, old in enumerate((*implicit, *explicit)):
        _check(type(old.payload_bytes) is bytes, "fixtures", "payload_mismatch")
        _check(old.payload_sha256 != fixture.payload_sha256, "upstream_fixtures", "containing_reference")
        _check(old.fixture_id != fixture.fixture_id, "upstream_fixtures", "conflicting_reference")
        if index >= len(implicit):
            _check(old.fixture_id not in supplied_ids, "upstream_fixtures", "duplicate_reference")
            supplied_ids.add(old.fixture_id)
        prior = identifiers.get(old.fixture_id)
        same_root = roots.get(old.payload_sha256)
        _check(prior is None or (prior.payload_sha256 == old.payload_sha256 and prior.payload_bytes == old.payload_bytes),
               "upstream_fixtures", "conflicting_reference")
        _check(same_root is None or (same_root.fixture_id == old.fixture_id and same_root.payload_bytes == old.payload_bytes),
               "upstream_fixtures", "conflicting_reference")
        if prior is None:
            total += len(old.payload_bytes)
            _check(total <= _MAX_PAYLOAD_BYTES, "fixtures", "resource_limit")
            identifiers[old.fixture_id], roots[old.payload_sha256] = old, old
    return tuple(identifiers.values())


def _readmit(old):
    """Re-admit original bytes under the current catalog and compare content facts."""
    _check(hashlib.sha256(old.payload_bytes).hexdigest() == old.payload_sha256, "fixtures", "payload_mismatch")
    result = verify_fixture_bundle(old.fixture_id, old.payload_bytes, event_id=old.event_id,
                                   raw_ref=old.raw_ref, received_at=old.received_at)
    _check(result.value is not None, "fixtures", "admission_failed", result.rejection)
    fresh = result.value
    for name in ("fixture_id", "payload_sha256", "generator_id", "generator_version", "schema_version",
                 "normalization_version", "assembled_at", "generator_source_ref", "members",
                 "modeled_source_profiles_bytes", "greek_method", "coherence_protocol_ids", "tick_definition_ids",
                 "origin", "fidelity_tier", "permitted_use", "operational_allowed", "economic_allowed"):
        _check(getattr(old, name) == getattr(fresh, name), "fixtures", "retained_content_mismatch")
    return fresh


def _member(manifest, record_id, kind, raw_hash=None):
    """Resolve an actual immutable member with its exact kind and optional raw hash."""
    member = next((m for m in manifest.members if m.record_id == record_id), None)
    _check(member is not None, "member", "unknown_member")
    _check(member.kind == kind, "member", "kind_mismatch")
    _check(hashlib.sha256(member.raw_body_bytes).hexdigest() == member.raw_hash,
           "member", "raw_hash_mismatch")
    _check(raw_hash is None or member.raw_hash == raw_hash, "member", "raw_hash_mismatch")
    return member


def _execution_hash(config):
    """Commit the complete execution snapshot and actual target, including BASE rules."""
    return _snapshot_hash(dict(record_kind="options_lab.execution_policy", schema_version=1,
        target_definition_id=config.execution.target_definition, execution=config_snapshot(config)["policy"]["execution"]))


def _policy(manifest, config, runtime):
    """Compare every actual runtime/config/execution/selection counterpart."""
    for name in ("implementation_scheme", "implementation_digest", "python_implementation", "exact_python_version",
                 "requires_python", "runtime_dependencies", "runtime_contract_digest"):
        _check(getattr(manifest.runtime_binding, name) == getattr(runtime, name), "runtime_binding." + name)
    selection = _selection_definition_snapshot()
    selector_hash = _snapshot_hash(selection)
    _check(selector_hash == SELECTION_RULE_ID and selection["vocabulary"] == config.selection_rule,
           "policy_binding.selection_rule_id")
    expected = dict(config_hash=config_hash(config), policy_hash=policy_hash(config),
        execution_policy_hash=_execution_hash(config), target_definition_id=config.execution.target_definition,
        selection_rule_id=selector_hash, contract_rule_id=config.contract_rule)
    for name, actual in expected.items():
        _check(getattr(manifest.policy_binding, name) == actual, "policy_binding." + name)
    prediction = manifest.prediction_contract
    if prediction is not None:
        profile = prediction.base_cost_profile
        _check(profile.base_execution_hash == expected["execution_policy_hash"]
               and profile.target_definition_id == config.execution.target_definition,
               "prediction_contract.base_cost_profile")
        _check(profile.content_hash == _snapshot_hash(dict(record_kind="options_lab.base_cost_profile",
               schema_version=1, **profile.snapshot())), "prediction_contract.base_cost_profile")


def _declared_roots(manifest, roots, normalization):
    """Resolve declared data roots and reject currently unsupported C/B member owners."""
    used = set()
    for row in manifest.data_manifest_hashes:
        root = next((r for r in roots if r.fixture_id == row.fixture_id), None)
        _check(root is not None, "data_manifest_hashes", "unknown_fixture")
        _check(root.payload_sha256 == row.payload_sha256, "data_manifest_hashes", "payload_mismatch")
        if normalization is not None and row.role in ("normalization", "training"):
            owner = normalization.manifest if row.role == "normalization" else normalization.baseline.manifest
            _check((root.fixture_id, root.payload_sha256) == (owner.fixture_id, owner.payload_sha256),
                   "data_manifest_hashes", "role_mismatch")
        used.add(root.fixture_id)
    refs = [("validation_report", manifest.validation_report),
            ("provenance.evaluation_block", manifest.provenance.evaluation_block),
            ("provenance.activation_gap", manifest.provenance.activation_gap)]
    if manifest.prediction_contract is not None:
        refs.extend(("prediction_contract." + name, getattr(manifest.prediction_contract, name))
                    for name in ("calibration_record", "model_membership", "tuning_membership", "calibration_partition"))
    if manifest.provenance.simulated_schedule is not None:
        refs.extend(("provenance.simulated_schedule." + name, getattr(manifest.provenance.simulated_schedule, name))
                    for name in ("evaluation_block", "activation_gap"))
    for path, ref in refs:
        _check(ref is None, path, "unsupported_reference")
    return used


def _normalization(old, roots):
    """Recompute the actual artifact at its retained availability through P10 only."""
    artifact = next(r for r in roots if r.fixture_id == old.manifest.fixture_id)
    training = next(r for r in roots if r.fixture_id == old.baseline.manifest.fixture_id)
    member = _member(artifact, old.record_id, "feature_normalization", old.raw_hash)
    _member(training, old.baseline.partition.record_id, "volume_partition", old.baseline.partition.raw_hash)
    result = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact, record_id=old.record_id,
                                            training_manifest=training, decision_at=old.available_at)
    _check(result.value is not None, "normalization", "recomputation_failed", result)
    fresh = result.value
    _check(fresh.snapshot == old.snapshot and fresh.baseline.snapshot == old.baseline.snapshot
           and fresh.raw_hash == old.raw_hash and fresh.baseline.partition == old.baseline.partition,
           "normalization", "retained_content_mismatch", result)
    _check(fresh.content_hash == old.content_hash == _snapshot_hash(fresh.snapshot)
           and fresh.baseline.content_hash == old.baseline.content_hash == _snapshot_hash(fresh.baseline.snapshot),
           "normalization", "content_hash_mismatch", result)
    baseline = fresh.baseline
    _check(baseline.numeric_id == NUMERIC_CONVENTION_ID == _snapshot_hash(_numeric_snapshot())
           and baseline.normalization_id == VOLUME_NORMALIZATION_ID == _snapshot_hash(baseline.normalization_snapshot)
           and baseline.volume_definition_id == ELIGIBLE_VOLUME_DEFINITION_ID,
           "normalization", "definition_mismatch", result)
    return result


def _source_profiles(manifest, roots, normalization_result):
    """Retain every declared qualified profile and compare its claimed source facts."""
    binding, profiles = manifest.feature_binding, []
    training_sources = set()
    for row in normalization_result.fit.inputs.selected_inputs:
        if row.meta is not None:
            training_sources.add((row.meta.source, row.meta.feed_class, row.meta.fidelity,
                                  row.meta.availability_basis, row.price_basis))
    source_roots = {r.fixture_id for r in manifest.data_manifest_hashes if r.role == "source"}
    protocols = {p for root in roots if root.fixture_id in source_roots for p in root.coherence_protocol_ids}
    for claim in binding.source_profiles:
        path = "feature_binding.source_profiles"
        _check(claim.fixture_id in source_roots, path, "undeclared_source")
        root = next(r for r in roots if r.fixture_id == claim.fixture_id)
        profile = next((p for p in root.decode_modeled_source_profiles() if p["profile_id"] == claim.profile_id), None)
        _check(profile is not None, path, "unknown_profile")
        _check(root.normalization_version == binding.input_normalization_version, "feature_binding.input_normalization_version")
        _check(root.greek_method == FIXTURE_GREEK_METHOD, "feature_binding.greek_method_spec_hash")
        members = tuple(m for m in root.members if m.profile_id == claim.profile_id)
        for member in members:
            raw, env = member.decode_raw_body(), member.decode_envelope()
            if member.kind in ("underlying_bar", "option_quote", "underlying_quote"):
                meta = env["metadata"]
                _check(all(meta.get(k) == profile[k] for k in ("source", "feed_class", "fidelity", "availability_basis")),
                       path, "profile_mismatch")
                if member.kind == "underlying_bar":
                    source = tuple(meta.get(k) for k in ("source", "feed_class", "fidelity", "availability_basis"))
                    _check((*source, raw.get("price_basis")) in training_sources, path, "training_source_mismatch")
            elif member.kind == "greek_observation":
                expected = dict(source=profile["source"], availability_basis=profile["availability_basis"],
                    method_id=binding.greek_method_id, method_version=binding.greek_method_version,
                    delta_unit=binding.delta_unit, iv_unit=binding.iv_unit)
                _check(all(raw.get(k) == v for k, v in expected.items()), path, "profile_mismatch")
                _check(type(raw.get("inputs")) is dict
                       and raw["inputs"].get("assumptions_id") == binding.greek_assumptions_id, path, "profile_mismatch")
            elif member.kind == "quote_coherence":
                _check(raw.get("source") == profile["source"] and raw.get("protocol_id") in binding.coherence_protocol_ids,
                       path, "profile_mismatch")
        profiles.append(_make(BundleSourceProfile, claim=claim, manifest=root,
                              profile_bytes=_canonical_bytes(profile), members=members))
    _check(all(p in protocols for p in binding.coherence_protocol_ids), "feature_binding.coherence_protocol_ids")
    return tuple(profiles)


def _features(manifest, spec, normalization, roots):
    """Bind the actual code-owned schema, recomputed normalizer and all feature claims."""
    if manifest.model_kind == "cash":
        _check(spec is None and normalization is None, "feature_binding", "cash_counterpart_present")
        return None, ()
    _check(spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC, "feature_binding", "unsupported_spec")
    result = _normalization(normalization, roots)
    current, method = result.value, FIXTURE_GREEK_METHOD
    schema, transform = _snapshot_hash(spec.schema_snapshot), _snapshot_hash(spec.transform_snapshot)
    _check(schema == spec.feature_schema_id and transform == spec.transform_id, "feature_binding", "definition_mismatch")
    expected = dict(feature_schema_id=schema, transform_id=transform, normalization_hash=current.content_hash,
        volume_baseline_hash=current.baseline.content_hash, volume_definition_id=ELIGIBLE_VOLUME_DEFINITION_ID,
        greek_method_id=method.method_id, greek_method_version=method.method_version,
        greek_method_spec_hash=_method_hash(method), greek_assumptions_id=method.assumptions_id,
        delta_unit=method.delta_unit, iv_unit=method.iv_unit, option_price_basis=method.option_price_basis,
        underlying_price_basis=method.underlying_price_basis, input_normalization_version=1)
    for name, actual in expected.items():
        _check(getattr(manifest.feature_binding, name) == actual, "feature_binding." + name)
    return result, _source_profiles(manifest, roots, result)


def verify_bundle(manifest: ModelBundleManifest, model_bytes: bytes, *, fixture: VerifiedFixtureManifest,
                  spec: FeatureSpec | None, normalization: FeatureNormalization | None, config: StrategyConfig,
                  runtime: RuntimeEvidence, upstream_fixtures: tuple[VerifiedFixtureManifest, ...] = ()) -> BundleVerification:
    """Verify actual current runtime, member bytes and complete bundle counterparts.

    Every used fixture is re-admitted under the current owned catalog. Original
    receipts remain audit facts; no new decision or verification time is invented.

    :param manifest: Exact inspected expected manifest, matched to actual member data.
    :param model_bytes: Exact original model bytes, never parsed and reserialized.
    :param fixture: Actual containing P08 authority and retained original receipt.
    :param spec: Actual code-owned feature definition, or None for cash.
    :param normalization: Actual P10 artifact with implicit training/artifact roots.
    :param config: Exact actual strategy configuration.
    :param runtime: Current A0 evidence, rechecked completely before compatibility.
    :param upstream_fixtures: At most64 explicit external authorities; no unused roots.
    :returns: Exclusive immutable content evidence or safe reached-owner rejection.
    :raises TypeError: If trusted inputs have unexpected exact types.
    """
    for name, value, cls in (("manifest", manifest, ModelBundleManifest), ("model_bytes", model_bytes, bytes),
        ("fixture", fixture, VerifiedFixtureManifest), ("config", config, StrategyConfig), ("runtime", runtime, RuntimeEvidence),
        ("upstream_fixtures", upstream_fixtures, tuple)):
        if type(value) is not cls:
            raise TypeError(name + " has an unexpected exact type")
    if spec is not None and type(spec) is not FeatureSpec:
        raise TypeError("spec must be an exact FeatureSpec or None")
    if normalization is not None:
        if (type(normalization) is not FeatureNormalization or type(normalization.baseline) is not VolumeBaseline
                or type(normalization.manifest) is not VerifiedFixtureManifest
                or type(normalization.baseline.manifest) is not VerifiedFixtureManifest):
            raise TypeError("normalization must retain exact P10 and P08 owners")
    if len(upstream_fixtures) <= 64 and any(type(f) is not VerifiedFixtureManifest for f in upstream_fixtures):
        raise TypeError("upstream_fixtures must contain exact VerifiedFixtureManifest values")
    receipt = dict(event_id=fixture.event_id, raw_ref=fixture.raw_ref, received_at=fixture.received_at)
    try:
        old_roots = _authorities(fixture, normalization, upstream_fixtures)
        checked = recheck_runtime(runtime)
        _check(checked.value is not None, "runtime", "runtime_unavailable", checked.rejection)
        fresh = _readmit(fixture)
        roots = tuple(_readmit(r) for r in old_roots)
        _check(manifest.fixture_id == fresh.fixture_id, "fixture_id", "fixture_mismatch")
        member = _member(fresh, manifest.bundle_record_id, "model_bundle")
        env, body = member.decode_envelope(), member.decode_raw_body()
        receipt = dict(event_id=env["event_id"], raw_ref=env["raw_ref"],
                       received_at=_parse_timestamp(env["simulated_received_at"], "member.received_at"))
        profile = next(p for p in fresh.decode_modeled_source_profiles() if p["profile_id"] == member.profile_id)
        _check(fresh.generator_id == profile["source"] == _GENERATOR and fresh.generator_version == "1",
               "member", "profile_mismatch")
        _check(env["supersedes_record_id"] is None, "member", "artifact_not_immutable")
        _shape(body, "member.raw_body", ("schema_version", "manifest", "model_utf8"))
        _check(type(body["schema_version"]) is int and body["schema_version"] == 1, "member.schema_version", "invalid_value")
        _check(_canonical_bytes(body) == member.raw_body_bytes, "member", "raw_hash_mismatch")
        text = body["model_utf8"]
        _check(type(text) is str, "model_utf8", "invalid_type")
        _check(len(text) <= 65536 and len(model_bytes) <= 65536, "model_utf8", "resource_limit")
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            raise _BundleFailure("model_utf8", "invalid_utf8", None) from None
        _check(len(encoded) <= 65536, "model_utf8", "resource_limit")
        _check(encoded == model_bytes, "model_utf8", "model_bytes_mismatch")
        parsed = normalize_model_bytes(encoded, **receipt)
        _check(parsed.value is not None, "model_bytes", "normalization_failed", parsed.rejection)
        inspected = normalize_bundle_manifest(body["manifest"], **receipt)
        _check(inspected.value is not None, "manifest", "normalization_failed", inspected.rejection)
        actual = inspected.value
        _check(actual.snapshot() == manifest.snapshot() and actual.manifest_hash == manifest.manifest_hash,
               "manifest", "manifest_mismatch")
        _check((actual.model_hash, actual.model_kind, actual.model_format_id) ==
               (parsed.value.model_hash, parsed.value.model_kind, parsed.value.format_id), "model", "model_mismatch")
        _check(actual.provenance.built_at <= fresh.assembled_at, "provenance.built_at", "built_after_assembly")
        _policy(actual, config, checked.value)
        _check(actual.model_kind == "cash" or normalization is not None, "normalization", "normalization_missing")
        used = _declared_roots(actual, roots, normalization)
        normalization_result, profiles = _features(actual, spec, normalization, roots)
        if normalization is not None:
            used.update((normalization.manifest.fixture_id, normalization.baseline.manifest.fixture_id))
        _check(all(r.fixture_id in used for r in upstream_fixtures), "upstream_fixtures", "unused_reference")
        value = _make(VerifiedBundle, manifest=actual, model=parsed.value,
            bundle_hash=_snapshot_hash(dict(record_kind="options_lab.model_bundle", schema_version=1,
                manifest_hash=actual.manifest_hash, model_hash=parsed.value.model_hash)),
            original_fixture=fixture, fixture=fresh, member=member, supplied_runtime=runtime, runtime=checked.value,
            config=config, spec=spec, supplied_normalization=normalization, normalization_result=normalization_result,
            upstream_fixtures=roots, supplied_upstream_fixtures=upstream_fixtures, source_profiles=profiles,
            origin=fresh.origin, fidelity_tier=fresh.fidelity_tier, permitted_use=fresh.permitted_use,
            operational_allowed=fresh.operational_allowed, economic_allowed=fresh.economic_allowed)
    except _BundleFailure as failure:
        path, code, cause = failure.args
    except _InvalidInput as failure:
        path, code = failure.args
        cause = None
    except (_UnsupportedIdentity, MemoryError, RecursionError):
        path, code, cause = "bundle", "resource_limit", None
    else:
        return _make(BundleVerification, value=value, rejection=None)
    return _make(BundleVerification, value=None, rejection=_make(BundleRejection,
        manifest=manifest, fixture=fixture, **receipt, field=path, code=code, cause=cause, stage="bundle_verification"))
