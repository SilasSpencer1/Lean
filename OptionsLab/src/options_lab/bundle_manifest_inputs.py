"""Inspect complete bounded manifest claims without granting bundle authority."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ._input_parsing import _InvalidInput, _fail, _parse_timestamp
from ._validation import _require_nonempty_string, _trusted_datetime
from .admission import _canonical_bytes
from .bar_inputs import _identity_decimal, _MAX_INTEGER_EXCLUSIVE, _timestamp_string
from .bundle_inputs import BundleInputRejection, _CASH_FORMAT, _FIXED_FORMAT, _decimal, _identifier, _make, _shape
from .config import _snapshot_hash


@dataclass(frozen=True, init=False)
class QualifiedProfile:
    """This class represents one declared fixture-qualified source profile."""

    fixture_id: str
    profile_id: str

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("QualifiedProfile values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete profile-reference fields.
        """
        return {"fixture_id": self.fixture_id, "profile_id": self.profile_id}


@dataclass(frozen=True, init=False)
class FeatureBinding:
    """This class represents expected feature, normalization and source claims."""

    feature_schema_id: str
    transform_id: str
    normalization_hash: str
    volume_baseline_hash: str
    volume_definition_id: str
    source_profiles: tuple[QualifiedProfile, ...]
    greek_method_id: str
    greek_method_version: str
    greek_method_spec_hash: str
    greek_assumptions_id: str
    delta_unit: str
    iv_unit: str
    option_price_basis: str
    underlying_price_basis: str
    coherence_protocol_ids: tuple[str, ...]
    input_normalization_version: int

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("FeatureBinding values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete feature claims with ordered source profiles.
        """
        return dict(feature_schema_id=self.feature_schema_id, transform_id=self.transform_id,
            normalization_hash=self.normalization_hash, volume_baseline_hash=self.volume_baseline_hash,
            volume_definition_id=self.volume_definition_id, source_profiles=[p.snapshot() for p in self.source_profiles],
            greek_method_id=self.greek_method_id, greek_method_version=self.greek_method_version,
            greek_method_spec_hash=self.greek_method_spec_hash, greek_assumptions_id=self.greek_assumptions_id,
            delta_unit=self.delta_unit, iv_unit=self.iv_unit, option_price_basis=self.option_price_basis,
            underlying_price_basis=self.underlying_price_basis, coherence_protocol_ids=list(self.coherence_protocol_ids),
            input_normalization_version=self.input_normalization_version)


@dataclass(frozen=True, init=False)
class PolicyBinding:
    """This class represents expected policy identities, without actual config."""

    config_hash: str
    policy_hash: str
    execution_policy_hash: str
    target_definition_id: str
    selection_rule_id: str
    contract_rule_id: str

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("PolicyBinding values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: All declared policy identities.
        """
        return dict(config_hash=self.config_hash, policy_hash=self.policy_hash,
            execution_policy_hash=self.execution_policy_hash, target_definition_id=self.target_definition_id,
            selection_rule_id=self.selection_rule_id, contract_rule_id=self.contract_rule_id)


@dataclass(frozen=True, init=False)
class RuntimeBinding:
    """This class represents internally consistent expected runtime claims."""

    implementation_scheme: str
    implementation_digest: str
    python_implementation: str
    exact_python_version: tuple[int, int, int, str, int]
    requires_python: str
    runtime_dependencies: tuple[tuple[str, str], ...]
    runtime_contract_digest: str

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("RuntimeBinding values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Expected runtime components; no measured evidence.
        """
        return dict(implementation_scheme=self.implementation_scheme, implementation_digest=self.implementation_digest,
            python_implementation=self.python_implementation, exact_python_version=list(self.exact_python_version),
            requires_python=self.requires_python, runtime_dependencies=[list(row) for row in self.runtime_dependencies],
            runtime_contract_digest=self.runtime_contract_digest)


@dataclass(frozen=True, init=False)
class DataManifestHash:
    """This class represents an ordered declared fixture root and its role."""

    role: str
    fixture_id: str
    payload_sha256: str

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("DataManifestHash values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete role and fixture-root claim.
        """
        return dict(role=self.role, fixture_id=self.fixture_id, payload_sha256=self.payload_sha256)


@dataclass(frozen=True, init=False)
class ExternalReference:
    """This class represents a declared external member, without admission."""

    fixture_id: str
    payload_sha256: str
    record_id: str
    raw_hash: str

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("ExternalReference values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete member and raw-body identity claims.
        """
        return dict(fixture_id=self.fixture_id, payload_sha256=self.payload_sha256,
                    record_id=self.record_id, raw_hash=self.raw_hash)


@dataclass(frozen=True, init=False)
class SimulatedSchedule:
    """This class represents an inspected schedule, without temporal assessment."""

    schedule_id: str
    version: int
    fit_cutoff: datetime | None
    fit_delay_us: int | None
    deployment_delay_us: int | None
    simulated_available_at: datetime
    evaluation_block: ExternalReference
    activation_gap: ExternalReference

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("SimulatedSchedule values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete schedule with explicit cash no-fit nulls.
        """
        return dict(schedule_id=self.schedule_id, version=self.version, fit_cutoff=_timestamp_string(self.fit_cutoff),
            fit_delay_us=self.fit_delay_us, deployment_delay_us=self.deployment_delay_us,
            simulated_available_at=_timestamp_string(self.simulated_available_at),
            evaluation_block=self.evaluation_block.snapshot(), activation_gap=self.activation_gap.snapshot())


@dataclass(frozen=True, init=False)
class BundleProvenance:
    """This class represents distinct actual and simulated provenance claims."""

    built_at: datetime
    promoted_at: datetime | None
    activated_at: datetime | None
    availability_basis: str
    simulated_available_at: datetime | None
    simulated_schedule: SimulatedSchedule | None
    evaluation_block: ExternalReference | None
    activation_gap: ExternalReference | None

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("BundleProvenance values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete provenance without inferred chronology.
        """
        return dict(built_at=_timestamp_string(self.built_at), promoted_at=_timestamp_string(self.promoted_at),
            activated_at=_timestamp_string(self.activated_at), availability_basis=self.availability_basis,
            simulated_available_at=_timestamp_string(self.simulated_available_at),
            simulated_schedule=None if self.simulated_schedule is None else self.simulated_schedule.snapshot(),
            evaluation_block=None if self.evaluation_block is None else self.evaluation_block.snapshot(),
            activation_gap=None if self.activation_gap is None else self.activation_gap.snapshot())


@dataclass(frozen=True, init=False)
class BaseCostProfile:
    """This class represents declared filled-round-trip fees and semantic identity."""

    profile_id: str
    profile_version: int
    filled_attempt_round_trip_fee: Decimal
    fee_charging_rule: str
    base_execution_hash: str
    target_definition_id: str
    content_hash: str

    def __init__(self) -> None:
        """Block caller-selected profile contents or hashes.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("BaseCostProfile values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields, excluding the derived hash.

        :returns: All six profile fields, suitable for manifest embedding.
        """
        return dict(profile_id=self.profile_id, profile_version=self.profile_version,
            filled_attempt_round_trip_fee=_identity_decimal(self.filled_attempt_round_trip_fee),
            fee_charging_rule=self.fee_charging_rule, base_execution_hash=self.base_execution_hash,
            target_definition_id=self.target_definition_id)


@dataclass(frozen=True, init=False)
class PredictionContract:
    """This class represents forecast data conventions and nullable support claims."""

    return_unit: str
    capital_basis_rule: str
    base_cost_profile: BaseCostProfile
    threshold_return: Decimal | None
    uncertainty_rule_id: str | None
    calibration_record: ExternalReference | None
    training_feature_cutoff: datetime | None
    fit_cutoff: datetime | None
    last_label_available_at: datetime | None
    model_membership: ExternalReference | None
    tuning_membership: ExternalReference | None
    calibration_partition: ExternalReference | None

    def __init__(self) -> None:
        """Block uninspected construction.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("PredictionContract values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return fresh normalized raw fields.

        :returns: Complete conventions and support claims, including all nulls.
        """
        return dict(return_unit=self.return_unit, capital_basis_rule=self.capital_basis_rule,
            base_cost_profile=self.base_cost_profile.snapshot(), threshold_return=_identity_decimal(self.threshold_return),
            uncertainty_rule_id=self.uncertainty_rule_id,
            calibration_record=None if self.calibration_record is None else self.calibration_record.snapshot(),
            training_feature_cutoff=_timestamp_string(self.training_feature_cutoff), fit_cutoff=_timestamp_string(self.fit_cutoff),
            last_label_available_at=_timestamp_string(self.last_label_available_at),
            model_membership=None if self.model_membership is None else self.model_membership.snapshot(),
            tuning_membership=None if self.tuning_membership is None else self.tuning_membership.snapshot(),
            calibration_partition=None if self.calibration_partition is None else self.calibration_partition.snapshot())


@dataclass(frozen=True, init=False)
class ModelBundleManifest:
    """This class represents all normalized manifest claims and their actual hash."""

    bundle_schema_version: int
    model_id: str
    model_kind: str
    model_format_id: str
    model_hash: str
    fixture_id: str
    bundle_record_id: str
    feature_binding: FeatureBinding | None
    policy_binding: PolicyBinding
    runtime_binding: RuntimeBinding
    data_manifest_hashes: tuple[DataManifestHash, ...]
    validation_report: ExternalReference | None
    seed: None
    provenance: BundleProvenance
    prediction_contract: PredictionContract | None
    claimed_origin: str
    claimed_permitted_use: str
    manifest_hash: str

    def __init__(self) -> None:
        """Block caller-selected manifest contents or hashes.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("ModelBundleManifest values come from normalize_bundle_manifest")

    def snapshot(self) -> dict[str, object]:
        """Return every normalized raw field, excluding derived hashes.

        :returns: Fresh complete manifest with ordered arrays and explicit nulls.
        """
        return dict(bundle_schema_version=self.bundle_schema_version, model_id=self.model_id, model_kind=self.model_kind,
            model_format_id=self.model_format_id, model_hash=self.model_hash, fixture_id=self.fixture_id,
            bundle_record_id=self.bundle_record_id,
            feature_binding=None if self.feature_binding is None else self.feature_binding.snapshot(),
            policy_binding=self.policy_binding.snapshot(), runtime_binding=self.runtime_binding.snapshot(),
            data_manifest_hashes=[row.snapshot() for row in self.data_manifest_hashes],
            validation_report=None if self.validation_report is None else self.validation_report.snapshot(), seed=self.seed,
            provenance=self.provenance.snapshot(),
            prediction_contract=None if self.prediction_contract is None else self.prediction_contract.snapshot(),
            claimed_origin=self.claimed_origin, claimed_permitted_use=self.claimed_permitted_use)


@dataclass(frozen=True, init=False)
class BundleManifestValidation:
    """This class represents one normalized manifest or safe rejection and receipt."""

    event_id: str
    raw_ref: str
    received_at: datetime
    value: ModelBundleManifest | None
    rejection: BundleInputRejection | None

    def __init__(self) -> None:
        """Block caller-selected inspection outcomes.

        :returns: None.
        :raises TypeError: Always; use normalize_bundle_manifest.
        """
        raise TypeError("BundleManifestValidation values come from normalize_bundle_manifest")


def _hash(value, path):
    """Inspect a bounded lowercase SHA claim using this input owner's failures."""
    value = _identifier(value, path)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        _fail(path, "invalid_value")
    return value


def _token(value, path, allowed):
    """Inspect one bounded identifier against an owned finite vocabulary."""
    value = _identifier(value, path)
    if value not in allowed:
        _fail(path, "invalid_value")
    return value


def _integer(value, path, maximum):
    """Preflight exact nonnegative integer size before comparison or serialization."""
    if type(value) is not int:
        _fail(path, "invalid_type")
    if value.bit_length() > maximum.bit_length() or value > maximum:
        _fail(path, "resource_limit")
    if value < 0:
        _fail(path, "invalid_value")
    return value


def _one(value, path):
    """Require exact schema version one."""
    if type(value) is not int:
        _fail(path, "invalid_type")
    if value != 1:
        _fail(path, "invalid_value")
    return value


def _timestamp(value, path, nullable=False):
    """Bound timestamp spelling before the existing aware-UTC parser."""
    if value is None and nullable:
        return None
    _identifier(value, path)
    return _parse_timestamp(value, path)


def _list(raw, path, limit):
    """Check an exact array's finite length before traversing its elements."""
    if type(raw) is not list:
        _fail(path, "invalid_type")
    if len(raw) > limit:
        _fail(path, "resource_limit")
    return raw


def _reference(raw, path, nullable=True):
    """Inspect one external member claim without resolving its authority."""
    if raw is None and nullable:
        return None
    _shape(raw, path, ("fixture_id", "payload_sha256", "record_id", "raw_hash"))
    return _make(ExternalReference, fixture_id=_identifier(raw["fixture_id"], path + ".fixture_id"),
        payload_sha256=_hash(raw["payload_sha256"], path + ".payload_sha256"),
        record_id=_identifier(raw["record_id"], path + ".record_id"), raw_hash=_hash(raw["raw_hash"], path + ".raw_hash"))


def _feature(raw):
    """Inspect the complete forecast feature group and ordered unique source claims."""
    path = "feature_binding"
    hashes = ("feature_schema_id", "transform_id", "normalization_hash", "volume_baseline_hash", "greek_method_spec_hash")
    identifiers = ("volume_definition_id", "greek_method_id", "greek_method_version", "greek_assumptions_id",
                   "delta_unit", "iv_unit", "option_price_basis", "underlying_price_basis")
    _shape(raw, path, hashes + identifiers + ("source_profiles", "coherence_protocol_ids", "input_normalization_version"))
    profiles = []
    for i, row in enumerate(_list(raw["source_profiles"], path + ".source_profiles", 64)):
        row_path = f"{path}.source_profiles.{i}"
        _shape(row, row_path, ("fixture_id", "profile_id"))
        profile = _make(QualifiedProfile, fixture_id=_identifier(row["fixture_id"], row_path + ".fixture_id"),
                        profile_id=_identifier(row["profile_id"], row_path + ".profile_id"))
        if profile in profiles:
            _fail(row_path, "duplicate_reference")
        profiles.append(profile)
    protocols = tuple(_identifier(item, f"{path}.coherence_protocol_ids.{i}") for i, item in enumerate(
        _list(raw["coherence_protocol_ids"], path + ".coherence_protocol_ids", 32)))
    if len(set(protocols)) != len(protocols):
        _fail(path + ".coherence_protocol_ids", "duplicate_reference")
    return _make(FeatureBinding, **{name: _hash(raw[name], path + "." + name) for name in hashes},
        **{name: _identifier(raw[name], path + "." + name) for name in identifiers}, source_profiles=tuple(profiles),
        coherence_protocol_ids=protocols, input_normalization_version=_one(raw["input_normalization_version"], path + ".input_normalization_version"))


def _policy(raw):
    """Inspect expected policy hashes and code-owned identifier claims."""
    hashes = ("config_hash", "policy_hash", "execution_policy_hash", "selection_rule_id")
    identifiers = ("target_definition_id", "contract_rule_id")
    _shape(raw, "policy_binding", hashes + identifiers)
    return _make(PolicyBinding, **{name: _hash(raw[name], "policy_binding." + name) for name in hashes},
                 **{name: _identifier(raw[name], "policy_binding." + name) for name in identifiers})


def _runtime(raw):
    """Inspect expected components and their concrete A0 contract preimage."""
    path = "runtime_binding"
    _shape(raw, path, ("implementation_scheme", "implementation_digest", "python_implementation", "exact_python_version",
                       "requires_python", "runtime_dependencies", "runtime_contract_digest"))
    scheme = _token(raw["implementation_scheme"], path + ".implementation_scheme", ("CORE_IMPLEMENTATION_BYTES_V1",))
    implementation = _token(raw["python_implementation"], path + ".python_implementation", ("CPython",))
    requires = _token(raw["requires_python"], path + ".requires_python", (">=3.11,<3.12",))
    version = _list(raw["exact_python_version"], path + ".exact_python_version", 5)
    if len(version) != 5:
        _fail(path + ".exact_python_version", "invalid_value")
    for index in (0, 1, 2, 4):
        _integer(version[index], f"{path}.exact_python_version.{index}", _MAX_INTEGER_EXCLUSIVE - 1)
    _token(version[3], path + ".exact_python_version.3", ("alpha", "beta", "candidate", "final"))
    if version[:2] != [3, 11] or (version[2] == 0 and version[3] != "final"):
        _fail(path + ".exact_python_version", "invalid_value")
    _list(raw["runtime_dependencies"], path + ".runtime_dependencies", 0)
    contract_hash = _hash(raw["runtime_contract_digest"], path + ".runtime_contract_digest")
    try:
        calculated_hash = _snapshot_hash(dict(scheme="RUNTIME_CONTRACT_V1", python_implementation=implementation,
            exact_python_version=version, requires_python=requires, runtime_dependencies=[]))
    except ValueError:
        _fail(path + ".exact_python_version", "resource_limit")
    if contract_hash != calculated_hash:
        _fail(path + ".runtime_contract_digest", "claim_mismatch")
    return _make(RuntimeBinding, implementation_scheme=scheme,
        implementation_digest=_hash(raw["implementation_digest"], path + ".implementation_digest"),
        python_implementation=implementation, exact_python_version=tuple(version), requires_python=requires,
        runtime_dependencies=(), runtime_contract_digest=contract_hash)


def _roots(raw):
    """Inspect ordered fixture roots, rejecting duplicate roles and conflicting aliases."""
    rows, identities, roots = [], {}, {}
    for i, row in enumerate(_list(raw, "data_manifest_hashes", 64)):
        path = f"data_manifest_hashes.{i}"
        _shape(row, path, ("role", "fixture_id", "payload_sha256"))
        role = _token(row["role"], path + ".role", ("training", "tuning", "calibration", "source", "normalization"))
        identity = _identifier(row["fixture_id"], path + ".fixture_id")
        root = _hash(row["payload_sha256"], path + ".payload_sha256")
        if any(item.role == role and item.fixture_id == identity for item in rows):
            _fail(path, "duplicate_reference")
        if identities.get(identity, root) != root or roots.get(root, identity) != identity:
            _fail(path, "conflicting_reference")
        identities[identity], roots[root] = root, identity
        rows.append(_make(DataManifestHash, role=role, fixture_id=identity, payload_sha256=root))
    return tuple(rows)


def _provenance(raw, cash):
    """Inspect all provenance fields and the complete optional schedule."""
    path = "provenance"
    dates = ("built_at", "promoted_at", "activated_at", "simulated_available_at")
    _shape(raw, path, dates + ("availability_basis", "simulated_schedule", "evaluation_block", "activation_gap"))
    schedule = raw["simulated_schedule"]
    if schedule is not None:
        p = path + ".simulated_schedule"
        _shape(schedule, p, ("schedule_id", "version", "fit_cutoff", "fit_delay_us", "deployment_delay_us",
                             "simulated_available_at", "evaluation_block", "activation_gap"))
        if cash:
            for name in ("fit_cutoff", "fit_delay_us", "deployment_delay_us"):
                if schedule[name] is not None:
                    _fail(p + "." + name, "invalid_value")
        maximum = (timedelta.max.days * 86400 + timedelta.max.seconds) * 1000000 + timedelta.max.microseconds
        schedule = _make(SimulatedSchedule, schedule_id=_identifier(schedule["schedule_id"], p + ".schedule_id"),
            version=_one(schedule["version"], p + ".version"),
            fit_cutoff=None if cash else _timestamp(schedule["fit_cutoff"], p + ".fit_cutoff"),
            fit_delay_us=None if cash else _integer(schedule["fit_delay_us"], p + ".fit_delay_us", maximum),
            deployment_delay_us=None if cash else _integer(schedule["deployment_delay_us"], p + ".deployment_delay_us", maximum),
            simulated_available_at=_timestamp(schedule["simulated_available_at"], p + ".simulated_available_at"),
            evaluation_block=_reference(schedule["evaluation_block"], p + ".evaluation_block", False),
            activation_gap=_reference(schedule["activation_gap"], p + ".activation_gap", False))
    return _make(BundleProvenance, **{name: _timestamp(raw[name], path + "." + name, name != "built_at") for name in dates},
        availability_basis=_token(raw["availability_basis"], path + ".availability_basis", ("actual", "simulated")),
        simulated_schedule=schedule, evaluation_block=_reference(raw["evaluation_block"], path + ".evaluation_block"),
        activation_gap=_reference(raw["activation_gap"], path + ".activation_gap"))


def _prediction(raw, policy):
    """Inspect complete forecast conventions and internally bind the cost profile."""
    path = "prediction_contract"
    refs = ("calibration_record", "model_membership", "tuning_membership", "calibration_partition")
    dates = ("training_feature_cutoff", "fit_cutoff", "last_label_available_at")
    _shape(raw, path, refs + dates + ("return_unit", "capital_basis_rule", "base_cost_profile", "threshold_return", "uncertainty_rule_id"))
    p, profile = path + ".base_cost_profile", raw["base_cost_profile"]
    _shape(profile, p, ("profile_id", "profile_version", "filled_attempt_round_trip_fee", "fee_charging_rule",
                        "base_execution_hash", "target_definition_id"))
    fee = _decimal(profile["filled_attempt_round_trip_fee"], p + ".filled_attempt_round_trip_fee")
    if fee < 0:
        _fail(p + ".filled_attempt_round_trip_fee", "invalid_value")
    profile = _make(BaseCostProfile, profile_id=_identifier(profile["profile_id"], p + ".profile_id"),
        profile_version=_one(profile["profile_version"], p + ".profile_version"), filled_attempt_round_trip_fee=fee,
        fee_charging_rule=_token(profile["fee_charging_rule"], p + ".fee_charging_rule", ("filled_round_trip_once_no_fill_zero_v1",)),
        base_execution_hash=_hash(profile["base_execution_hash"], p + ".base_execution_hash"),
        target_definition_id=_identifier(profile["target_definition_id"], p + ".target_definition_id"))
    if profile.base_execution_hash != policy.execution_policy_hash:
        _fail(p + ".base_execution_hash", "claim_mismatch")
    if profile.target_definition_id != policy.target_definition_id:
        _fail(p + ".target_definition_id", "claim_mismatch")
    object.__setattr__(profile, "content_hash", _snapshot_hash(dict(record_kind="options_lab.base_cost_profile",
                                                                 schema_version=1, **profile.snapshot())))
    return _make(PredictionContract,
        return_unit=_token(raw["return_unit"], path + ".return_unit", ("attempt_net_return_over_original_ask_capital",)),
        capital_basis_rule=_token(raw["capital_basis_rule"], path + ".capital_basis_rule", ("100_times_original_decision_ask_cap",)),
        base_cost_profile=profile, threshold_return=None if raw["threshold_return"] is None else _decimal(raw["threshold_return"], path + ".threshold_return"),
        uncertainty_rule_id=None if raw["uncertainty_rule_id"] is None else _token(raw["uncertainty_rule_id"], path + ".uncertainty_rule_id", ("fixture_fixed_penalty_v1",)),
        **{name: _reference(raw[name], path + "." + name) for name in refs},
        **{name: _timestamp(raw[name], path + "." + name, True) for name in dates})


def normalize_bundle_manifest(raw: object, *, event_id: str, raw_ref: str, received_at: datetime) -> BundleManifestValidation:
    """Normalize complete manifest data and derive actual semantic claim hashes.

    Parsed references and expected runtime fields grant no admission, counterpart
    verification, temporal suitability, calibration support or readiness.

    :param raw: Untrusted exact closed manifest dictionary.
    :param event_id: Trusted nonempty inspection-event identifier.
    :param raw_ref: Trusted nonempty receipt locator.
    :param received_at: Trusted aware receipt timestamp, normalized to UTC.
    :returns: Exclusive immutable manifest or bounded rejection with actual receipt.
    :raises TypeError: If a trusted receipt has the wrong exact type.
    :raises ValueError: If trusted receipt identity or timestamp is invalid.
    """
    _require_nonempty_string("event_id", event_id)
    _require_nonempty_string("raw_ref", raw_ref)
    received_at = _trusted_datetime("received_at", received_at)
    receipt = dict(event_id=event_id, raw_ref=raw_ref, received_at=received_at)
    try:
        _shape(raw, "$", ("bundle_schema_version", "model_id", "model_kind", "model_format_id", "model_hash", "fixture_id",
            "bundle_record_id", "feature_binding", "policy_binding", "runtime_binding", "data_manifest_hashes",
            "validation_report", "seed", "provenance", "prediction_contract", "claimed_origin", "claimed_permitted_use"))
        kind = _token(raw["model_kind"], "model_kind", ("cash", "fixture_fixed_by_right"))
        cash = kind == "cash"
        if raw["seed"] is not None:
            _fail("seed", "invalid_value")
        if cash:
            for name in ("feature_binding", "prediction_contract"):
                if raw[name] is not None:
                    _fail(name, "invalid_value")
        policy = _policy(raw["policy_binding"])
        value = _make(ModelBundleManifest, bundle_schema_version=_one(raw["bundle_schema_version"], "bundle_schema_version"),
            model_id=_identifier(raw["model_id"], "model_id"), model_kind=kind,
            model_format_id=_token(raw["model_format_id"], "model_format_id", (_CASH_FORMAT if cash else _FIXED_FORMAT,)),
            model_hash=_hash(raw["model_hash"], "model_hash"), fixture_id=_identifier(raw["fixture_id"], "fixture_id"),
            bundle_record_id=_identifier(raw["bundle_record_id"], "bundle_record_id"),
            feature_binding=None if cash else _feature(raw["feature_binding"]), policy_binding=policy,
            runtime_binding=_runtime(raw["runtime_binding"]), data_manifest_hashes=_roots(raw["data_manifest_hashes"]),
            validation_report=_reference(raw["validation_report"], "validation_report"), seed=None,
            provenance=_provenance(raw["provenance"], cash), prediction_contract=None if cash else _prediction(raw["prediction_contract"], policy),
            claimed_origin=_token(raw["claimed_origin"], "claimed_origin", ("synthetic",)),
            claimed_permitted_use=_token(raw["claimed_permitted_use"], "claimed_permitted_use", ("core_fixture",)))
        snapshot = dict(record_kind="options_lab.model_bundle_manifest", schema_version=1, **value.snapshot())
        if len(_canonical_bytes(snapshot)) > 262144:
            _fail("$", "resource_limit")
        object.__setattr__(value, "manifest_hash", _snapshot_hash(snapshot))
    except (MemoryError, RecursionError):
        failure = ("$", "resource_limit")
    except _InvalidInput as invalid:
        failure = invalid.args
    else:
        return _make(BundleManifestValidation, **receipt, value=value, rejection=None)
    return _make(BundleManifestValidation, **receipt, value=None,
        rejection=_make(BundleInputRejection, stage="bundle_manifest", **receipt, field=failure[0], code=failure[1]))
