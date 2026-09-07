"""Complete data-only manifest inspection and independent semantic identities."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
import hashlib
import importlib.util
import json
import sys

import pytest


STAMP = "2026-09-07T04:00:00+00:00"
RECEIPT = dict(event_id="manifest-inspection", raw_ref="memory:manifest",
               received_at=datetime(2026, 9, 7, tzinfo=timezone(timedelta(hours=-4))))
REF = dict(fixture_id="external", payload_sha256="a" * 64, record_id="member", raw_hash="b" * 64)


def digest(raw):
    """Compute test-owned canonical JSON identities independently."""
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def manifest(cash=False):
    """Build complete raw examples without production constructors or snapshots."""
    runtime = dict(implementation_scheme="CORE_IMPLEMENTATION_BYTES_V1", implementation_digest="c" * 64,
        python_implementation="CPython", exact_python_version=[3, 11, 11, "final", 0],
        requires_python=">=3.11,<3.12", runtime_dependencies=[])
    runtime["runtime_contract_digest"] = digest(dict(scheme="RUNTIME_CONTRACT_V1",
        **{k: runtime[k] for k in ("python_implementation", "exact_python_version", "requires_python", "runtime_dependencies")}))
    return dict(bundle_schema_version=1, model_id="model", model_kind="cash" if cash else "fixture_fixed_by_right",
        model_format_id="options_lab.cash_json.v1" if cash else "options_lab.fixed_by_right_json.v1",
        model_hash="d" * 64, fixture_id="bundle", bundle_record_id="bundle-member",
        feature_binding=None if cash else dict(feature_schema_id="1" * 64, transform_id="2" * 64,
            normalization_hash="3" * 64, volume_baseline_hash="4" * 64, volume_definition_id="volume",
            source_profiles=[dict(fixture_id="source", profile_id="profile")], greek_method_id="greek",
            greek_method_version="1", greek_method_spec_hash="5" * 64, greek_assumptions_id="assumptions",
            delta_unit="delta", iv_unit="iv", option_price_basis="raw", underlying_price_basis="raw",
            coherence_protocol_ids=["coherence"], input_normalization_version=1),
        policy_binding=dict(config_hash="6" * 64, policy_hash="7" * 64, execution_policy_hash="8" * 64,
            target_definition_id="target", selection_rule_id="9" * 64, contract_rule_id="contract"),
        runtime_binding=runtime, data_manifest_hashes=[dict(role="source", fixture_id="source", payload_sha256="e" * 64)],
        validation_report=deepcopy(REF), seed=None,
        provenance=dict(built_at=STAMP, promoted_at=None, activated_at=None, availability_basis="simulated",
            simulated_available_at=STAMP, evaluation_block=deepcopy(REF), activation_gap=deepcopy(REF),
            simulated_schedule=dict(schedule_id="schedule", version=1, fit_cutoff=None if cash else STAMP,
                fit_delay_us=None if cash else 0, deployment_delay_us=None if cash else 1,
                simulated_available_at=STAMP, evaluation_block=deepcopy(REF), activation_gap=deepcopy(REF))),
        prediction_contract=None if cash else dict(return_unit="attempt_net_return_over_original_ask_capital",
            capital_basis_rule="100_times_original_decision_ask_cap", threshold_return="-0.125",
            uncertainty_rule_id="fixture_fixed_penalty_v1", calibration_record=deepcopy(REF),
            training_feature_cutoff=None, fit_cutoff=STAMP, last_label_available_at=None,
            model_membership=deepcopy(REF), tuning_membership=deepcopy(REF), calibration_partition=deepcopy(REF),
            base_cost_profile=dict(profile_id="fees", profile_version=1, filled_attempt_round_trip_fee="1.25",
                fee_charging_rule="filled_round_trip_once_no_fill_zero_v1", base_execution_hash="8" * 64,
                target_definition_id="target")), claimed_origin="synthetic", claimed_permitted_use="core_fixture")


def inspect(raw, **receipt):
    """Call the real owner with an explicit missing-implementation RED."""
    assert importlib.util.find_spec("options_lab.bundle_manifest_inputs") is not None
    from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
    return normalize_bundle_manifest(raw, **(RECEIPT | receipt))


def changed(raw, path, value):
    """Change a named nested field in an independent raw example."""
    result = deepcopy(raw)
    parent = result
    for name in path.split(".")[:-1]:
        parent = parent[int(name)] if type(parent) is list else parent[name]
    leaf = path.split(".")[-1]
    parent[int(leaf) if type(parent) is list else leaf] = value
    return result


def rejected(raw, field=None, code=None):
    """Require exclusive failure with fixed fields and actual receipt facts."""
    result = inspect(raw)
    assert result.value is None and result.rejection is not None
    failure = result.rejection
    assert failure.stage == "bundle_manifest"
    if field is not None:
        assert failure.field == field
    if code is not None:
        assert failure.code == code
    for item in (result, failure):
        assert (item.event_id, item.raw_ref, item.received_at) == (
            RECEIPT["event_id"], RECEIPT["raw_ref"], datetime(2026, 9, 7, 4, tzinfo=timezone.utc))
    return result


@pytest.mark.parametrize("cash", [False, True])
def test_complete_examples_retain_every_field_and_independent_hashes(cash):
    raw = manifest(cash)
    result = inspect(raw)
    assert result.rejection is None
    value = result.value
    assert value.snapshot() == raw
    assert value.manifest_hash == digest(dict(record_kind="options_lab.model_bundle_manifest", schema_version=1, **raw))
    assert value.manifest_hash == ("a0bf962b6093f252f40db6da8d0d2b6716d6b6e884695fc40f7664a2280f3de7" if cash
                                   else "7f84a68e81d25569776131e55d97dc1fc8a22f197e9fad8ddf0324ceba0a5f90")
    assert value.runtime_binding.runtime_contract_digest == "1608d088e3fd388ab0f00d3eec9fe5042880c5ec92858b8be02985f74aec81c5"
    assert value.runtime_binding.exact_python_version == (3, 11, 11, "final", 0)
    assert value.runtime_binding.runtime_dependencies == ()
    assert value.provenance.built_at == datetime(2026, 9, 7, 4, tzinfo=timezone.utc)
    assert value.validation_report.record_id == "member"
    if not cash:
        profile = value.prediction_contract.base_cost_profile
        assert profile.filled_attempt_round_trip_fee == Decimal("1.25")
        assert profile.content_hash == "c1bf17014fd12c2f41d477233f9e1e1b78b2d0e61a8ec86fb9be213be99f3a8e"
        assert profile.content_hash == digest(dict(record_kind="options_lab.base_cost_profile", schema_version=1,
                                                   **raw["prediction_contract"]["base_cost_profile"]))
        assert value.prediction_contract.threshold_return == Decimal("-0.125")


SHAPES = ("", "feature_binding", "policy_binding", "runtime_binding", "data_manifest_hashes.0",
          "validation_report", "provenance", "provenance.simulated_schedule", "prediction_contract",
          "prediction_contract.base_cost_profile", "feature_binding.source_profiles.0")


@pytest.mark.parametrize("path", SHAPES)
def test_each_of_89_named_slots_is_required_and_every_shape_is_closed(path):
    raw = manifest()
    group = raw
    for name in path.split(".") if path else ():
        group = group[int(name)] if type(group) is list else group[name]
    for key in group:
        missing = {k: v for k, v in group.items() if k != key}
        rejected(changed(raw, path, missing) if path else missing, code="missing")
    extended = group | {"secret_unknown": None}
    rejected(changed(raw, path, extended) if path else extended, code="unknown_fields")


@pytest.mark.parametrize("path,value", [
    ("bundle_schema_version", True), ("bundle_schema_version", 2), ("seed", 0),
    ("model_kind", "unknown"), ("model_format_id", "options_lab.cash_json.v1"),
    ("claimed_origin", "market"), ("claimed_permitted_use", "live"),
    ("feature_binding", None), ("prediction_contract", None),
    ("feature_binding.greek_method_version", 1), ("feature_binding.input_normalization_version", True),
    ("policy_binding.selection_rule_id", "abs-delta-distance-spread-expiry-contract-v1"),
    ("prediction_contract.capital_basis_rule", "100_times_original_decision_ask_capital"),
    ("prediction_contract.uncertainty_rule_id", "arbitrary"),
    ("prediction_contract.base_cost_profile.profile_version", True),
    ("prediction_contract.base_cost_profile.fee_charging_rule", "per_leg"),
    ("prediction_contract.base_cost_profile.filled_attempt_round_trip_fee", "-0.1"),
    ("prediction_contract.base_cost_profile.base_execution_hash", "0" * 64),
    ("prediction_contract.base_cost_profile.target_definition_id", "other"),
    ("provenance.simulated_schedule.fit_cutoff", None),
    ("provenance.simulated_schedule.fit_delay_us", True),
    ("provenance.simulated_schedule.deployment_delay_us", -1),
    ("provenance.simulated_schedule.evaluation_block", None),
])
def test_semantic_claims_require_exact_supported_values(path, value):
    rejected(changed(manifest(), path, value))


def test_nullable_claims_and_cash_schedule_are_explicit_without_invented_chronology():
    raw = manifest()
    for field in ("threshold_return", "uncertainty_rule_id", "calibration_record", "training_feature_cutoff",
                  "fit_cutoff", "last_label_available_at", "model_membership", "tuning_membership", "calibration_partition"):
        raw["prediction_contract"][field] = None
    for field in ("promoted_at", "activated_at", "simulated_available_at", "simulated_schedule", "evaluation_block", "activation_gap"):
        raw["provenance"][field] = None
    raw["validation_report"] = None
    assert inspect(raw).value.snapshot() == raw
    raw["provenance"]["activated_at"] = "2000-01-01T00:00:00+00:00"
    assert inspect(raw).value is not None
    for field in ("fit_cutoff", "fit_delay_us", "deployment_delay_us"):
        rejected(changed(manifest(True), "provenance.simulated_schedule." + field, 0))
    for field in ("feature_binding", "prediction_contract"):
        rejected(changed(manifest(True), field, manifest()[field]))


@pytest.mark.parametrize("version", [(3, 11, 11, "final", 0), [3, 11], [True, 11, 11, "final", 0],
    [3, 12, 0, "final", 0], [3, 11, -1, "final", 0], [3, 11, 0, "beta", 0],
    [3, 11, 1, "FINAL", 0], [3, 11, 1, "final", False], [3, 11, 10 ** 1000, "final", 0]])
def test_runtime_versions_reject_wrong_positions_and_bounded_integers(version):
    rejected(changed(manifest(), "runtime_binding.exact_python_version", version))


def test_runtime_claim_hashes_bind_exact_components_without_measuring_runtime():
    raw = manifest()
    rejected(changed(raw, "runtime_binding.runtime_contract_digest", "0" * 64))
    rejected(changed(raw, "runtime_binding.runtime_dependencies", [["dependency", "1"]]))
    for field, value in (("python_implementation", "PyPy"), ("requires_python", ">=3.11"),
                         ("implementation_scheme", "other")):
        rejected(changed(raw, "runtime_binding." + field, value))
    for version in ([3, 11, 0, "final", 0], [3, 11, 1, "alpha", 0], [3, 11, 10 ** 999, "final", 10 ** 999]):
        runtime = raw["runtime_binding"]
        runtime["exact_python_version"] = version
        runtime["runtime_contract_digest"] = digest(dict(scheme="RUNTIME_CONTRACT_V1",
            **{k: runtime[k] for k in ("python_implementation", "exact_python_version", "requires_python", "runtime_dependencies")}))
        assert inspect(raw).value.runtime_binding.exact_python_version == tuple(version)
    previous_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        rejected(raw, "runtime_binding.exact_python_version", "resource_limit")
        assert inspect(manifest()).value is not None
        assert sys.get_int_max_str_digits() == 640
    finally:
        sys.set_int_max_str_digits(previous_limit)
    assert sys.get_int_max_str_digits() == previous_limit


def test_canonical_equivalence_order_nulls_and_snapshot_mutation():
    raw = manifest()
    value = inspect(raw).value
    variant = changed(raw, "prediction_contract.threshold_return", "-.12500")
    variant["provenance"]["built_at"] = "2026-09-07T00:00:00-04:00"
    variant["prediction_contract"]["base_cost_profile"]["filled_attempt_round_trip_fee"] = "+01.2500"
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = context.traps[Rounded] = True
        assert inspect(variant).value.manifest_hash == value.manifest_hash
    assert inspect(changed(raw, "prediction_contract.threshold_return", None)).value.manifest_hash != value.manifest_hash
    for path, rows in (("feature_binding.coherence_protocol_ids", ["one", "two"]),
                       ("feature_binding.source_profiles", [dict(fixture_id="a", profile_id="x"), dict(fixture_id="b", profile_id="x")]),
                       ("data_manifest_hashes", [dict(role="source", fixture_id="a", payload_sha256="1" * 64),
                                                 dict(role="training", fixture_id="b", payload_sha256="2" * 64)])):
        assert inspect(changed(raw, path, rows)).value.manifest_hash != inspect(changed(raw, path, rows[::-1])).value.manifest_hash
    snapshot = value.snapshot()
    snapshot["feature_binding"]["source_profiles"][0]["profile_id"] = "changed"
    raw["provenance"]["built_at"] = "changed"
    assert value.snapshot() == manifest()
    records = (value, inspect(manifest()), value.feature_binding, value.feature_binding.source_profiles[0],
        value.policy_binding, value.runtime_binding, value.data_manifest_hashes[0], value.validation_report,
        value.provenance, value.provenance.simulated_schedule, value.prediction_contract,
        value.prediction_contract.base_cost_profile, rejected(None).rejection)
    for record in records:
        with pytest.raises(TypeError):
            type(record)()
        with pytest.raises(TypeError):
            replace(record)
        with pytest.raises(FrozenInstanceError):
            record.forged = True


@pytest.mark.parametrize("path,values", [
    ("model_id", [True, "", "\ud800", "x" * 1025]),
    ("model_hash", [None, "A" * 64, "a" * 63, "a" * 1025]),
    ("provenance.built_at", [None, STAMP * 100, "2026-09-07", "invalid", "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"]),
    ("prediction_contract.threshold_return", [True, 1, 0.1, Decimal("1"), "1e1", "NaN", "1" * 1001, "1" * 1003]),
    ("provenance.simulated_schedule.fit_delay_us", [None, 0.1, 10 ** 10000,
        (timedelta.max.days * 86400 + timedelta.max.seconds) * 1000000 + timedelta.max.microseconds + 1]),
])
def test_scalar_boundaries_reject_safely(path, values):
    for value in values:
        rejected(changed(manifest(), path, value), path)


def test_inclusive_scalar_list_limits_and_conflicting_reference_reuse():
    raw = manifest()
    raw["model_id"] = "é" * 1024
    raw["prediction_contract"]["threshold_return"] = "1" * 1000
    raw["provenance"]["simulated_schedule"]["fit_delay_us"] = (timedelta.max.days * 86400 + timedelta.max.seconds) * 1000000 + timedelta.max.microseconds
    paths = {"feature_binding.source_profiles": [dict(fixture_id=str(i), profile_id="same") for i in range(64)],
             "feature_binding.coherence_protocol_ids": [str(i) for i in range(32)],
             "data_manifest_hashes": [dict(role="source", fixture_id=str(i), payload_sha256=f"{i:064x}") for i in range(64)]}
    for path, rows in paths.items():
        assert inspect(changed(raw, path, rows)).value is not None
        rejected(changed(raw, path, rows + [rows[0]]), path, "resource_limit")
        rejected(changed(raw, path, rows[:1] * 2), code="duplicate_reference")
        rejected(changed(raw, path, tuple(rows)), path, "invalid_type")
    row = raw["data_manifest_hashes"][0]
    for other in (row | {"role": "training", "payload_sha256": "0" * 64}, row | {"fixture_id": "other"}):
        rejected(changed(raw, "data_manifest_hashes", [row, other]), code="conflicting_reference")
    assert inspect(changed(raw, "data_manifest_hashes", [row, row | {"role": "training"}])).value is not None


def test_exact_canonical_byte_limit_counts_ascii_escaping_and_all_domain_fields():
    raw = manifest()
    rows = [dict(fixture_id=f"p{i}", profile_id="x" * 1024) for i in range(64)]
    raw["feature_binding"]["source_profiles"] = rows
    framed = dict(record_kind="options_lab.model_bundle_manifest", schema_version=1, **raw)
    remaining = 262144 - len(json.dumps(framed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    for row in rows:
        unicode_count = min(1020, remaining // 6)
        ascii_count = min(1020 - unicode_count, remaining - unicode_count * 6)
        row["fixture_id"] += "é" * unicode_count + "x" * ascii_count
        remaining -= unicode_count * 6 + ascii_count
    assert remaining == 0
    assert inspect(raw).value is not None
    rejected(raw | {"model_id": "modelx"}, "$", "resource_limit")


def test_hostile_hooks_cycles_allocation_and_process_control(monkeypatch):
    class Hostile:
        """This class represents untrusted hooks that inspection must not invoke."""
        def __eq__(self, other):
            """Fail if an untrusted value is compared."""
            raise AssertionError("secret")
        def __repr__(self):
            """Fail if an untrusted value is rendered."""
            raise AssertionError("secret")
        __hash__ = object.__hash__
    for raw in (Hostile(), {Hostile(): None}, changed(manifest(), "model_kind", Hostile()),
                changed(manifest(), "runtime_binding.exact_python_version.2", Hostile())):
        assert "secret" not in repr(rejected(raw))
    class HostileList(list):
        """This class represents a container whose subclass hook must not run."""
        def __len__(self):
            """Fail if inspection traverses the subclass."""
            raise AssertionError("secret")
    rejected(changed(manifest(), "data_manifest_hashes", HostileList()), "data_manifest_hashes", "invalid_type")
    raw = manifest()
    raw["feature_binding"] = raw
    rejected(raw)
    from options_lab import bundle_manifest_inputs as owner
    def exhausted(*args, **kwargs):
        """Simulate canonical encoding allocation failure."""
        raise MemoryError("secret")
    with monkeypatch.context() as patch:
        patch.setattr(owner, "_canonical_bytes", exhausted)
        rejected(manifest(), "$", "resource_limit")
    def interrupted(*args, **kwargs):
        """Simulate process control at the encoder boundary."""
        raise KeyboardInterrupt
    monkeypatch.setattr(owner, "_canonical_bytes", interrupted)
    with pytest.raises(KeyboardInterrupt):
        inspect(manifest())


@pytest.mark.parametrize("receipt,error", [({"event_id": ""}, ValueError), ({"raw_ref": 7}, TypeError),
    ({"received_at": "2026-09-07"}, TypeError), ({"received_at": datetime(2026, 9, 7)}, ValueError)])
def test_trusted_receipt_misuse_raises(receipt, error):
    with pytest.raises(error):
        inspect(manifest(), **receipt)
