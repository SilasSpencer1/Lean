"""Assemble bundle bytes once, after executable owners and exports are finalized."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes
from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import _execution_hash
from options_lab.candidates import SELECTION_RULE_ID, _selection_definition_snapshot
from options_lab.config import StrategyConfig, config_hash, policy_hash, _snapshot_hash
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.greeks import FIXTURE_GREEK_METHOD
from options_lab.runtime import measure_runtime
from options_lab.volume_normalization import normalize_feature_normalization
from options_lab.vwap_features import ELIGIBLE_VOLUME_DEFINITION_ID


FIXTURE_ID = "p14a-bundle-content-v1"
GENERATOR = "optionslab-bundle-fixture-builder"
SIMULATED_AT = "2026-09-04T14:05:00+00:00"
CASH = '{"schema_version":1,"format_id":"options_lab.cash_json.v1"}'
FIXED = ('{"schema_version":1,"format_id":"options_lab.fixed_by_right_json.v1",'
         '"call":{"mean_attempt_return":"0.02","calibration_bucket":"call"},'
         '"put":{"mean_attempt_return":"-0.01","calibration_bucket":"put"}}')


def build_fixture() -> bytes:
    """Derive current claims from final code and unchanged real upstream payloads.

    :returns: Readable bundle payload with truthful actual build and assembly times.
    :raises AssertionError: If actual runtime, upstream admission or P10 recomputation fails.
    """
    built = datetime.now(timezone.utc)
    folder = Path(__file__).parent / "fixtures"
    receipt = dict(event_id="bundle-build", raw_ref="synthetic://bundle-build", received_at=built)
    roots = {}
    for name in ("p10a-volume-training-v1", "p10b-volume-normalization-v1", "p11-feature-vector-v1"):
        result = verify_fixture_bundle(name, (folder / (name + ".json")).read_bytes(), **receipt)
        assert result.value is not None, result.rejection
        roots[name] = result.value
    artifact = roots["p10b-volume-normalization-v1"]
    raw = next(m for m in artifact.members if m.record_id == "good").decode_raw_body()
    normalized = normalize_feature_normalization(raw, manifest=artifact, record_id="good",
        training_manifest=roots["p10a-volume-training-v1"], decision_at=datetime.fromisoformat(raw["available_at"]))
    assert normalized.value is not None, normalized.reasons
    norm, spec, config, method = normalized.value, EXACT_VWAP_SPEC, StrategyConfig(), FIXTURE_GREEK_METHOD
    runtime = measure_runtime().value
    assert runtime is not None
    assert SELECTION_RULE_ID == _snapshot_hash(_selection_definition_snapshot())
    runtime_claim = {name: getattr(runtime, name) for name in ("implementation_scheme", "implementation_digest",
        "python_implementation", "exact_python_version", "requires_python", "runtime_dependencies", "runtime_contract_digest")}
    runtime_claim.update(exact_python_version=list(runtime.exact_python_version), runtime_dependencies=[])
    policy = dict(config_hash=config_hash(config), policy_hash=policy_hash(config), execution_policy_hash=_execution_hash(config),
        target_definition_id=config.execution.target_definition, selection_rule_id=SELECTION_RULE_ID, contract_rule_id=config.contract_rule)
    source = roots["p11-feature-vector-v1"]
    feature = dict(feature_schema_id=spec.feature_schema_id, transform_id=spec.transform_id,
        normalization_hash=norm.content_hash, volume_baseline_hash=norm.baseline.content_hash,
        volume_definition_id=ELIGIBLE_VOLUME_DEFINITION_ID,
        source_profiles=[dict(fixture_id=source.fixture_id, profile_id=p) for p in (
            "bars", "good-option_quote", "good-underlying_quote", "good-greek", "good-proof")],
        greek_method_id=method.method_id, greek_method_version=method.method_version,
        greek_method_spec_hash=method.method_spec_hash, greek_assumptions_id=method.assumptions_id,
        delta_unit=method.delta_unit, iv_unit=method.iv_unit, option_price_basis=method.option_price_basis,
        underlying_price_basis=method.underlying_price_basis, coherence_protocol_ids=list(source.coherence_protocol_ids),
        input_normalization_version=1)
    prediction = dict(return_unit="attempt_net_return_over_original_ask_capital",
        capital_basis_rule="100_times_original_decision_ask_cap", threshold_return="0", uncertainty_rule_id=None,
        calibration_record=None, training_feature_cutoff=None, fit_cutoff=None, last_label_available_at=None,
        model_membership=None, tuning_membership=None, calibration_partition=None,
        base_cost_profile=dict(profile_id="fixture-base-cost", profile_version=1, filled_attempt_round_trip_fee="1",
            fee_charging_rule="filled_round_trip_once_no_fill_zero_v1", base_execution_hash=policy["execution_policy_hash"],
            target_definition_id=config.execution.target_definition))
    profile = dict(profile_id="model-bundle", kind="model_bundle", source=GENERATOR, stream_id="model-bundle",
        feed_class=None, fidelity=None, availability_basis="measured", units={}, record_identity_rule="new_event_id_per_update")
    payload = dict(schema_version=1, normalization_version=1, fixture_id=FIXTURE_ID, generator_id=GENERATOR,
        generator_version="1", generator_source_ref="OptionsLab/tests/build_bundle_fixture.py", origin="synthetic",
        permitted_use="core_fixture", modeled_source_profiles=[profile, {**profile, "profile_id": "wrong-source", "source": "other"}],
        definitions=json.loads(source.payload_bytes)["definitions"], members=[])
    for name in ("cash", "fixed", "cash-whitespace", "wrong-runtime", "wrong-selection", "wrong-normalization", "wrong-source",
                 "missing-profile", "undeclared-source", "future-reference", "built-after-assembly", "superseded", "wrong-profile",
                 "empty-profiles", "implicit-only", "bad-model-type", "surrogate-model", "malformed-model", "model-kind-mismatch"):
        cash = name in ("cash", "cash-whitespace", "wrong-runtime", "wrong-selection", "built-after-assembly", "superseded",
                        "wrong-profile", "bad-model-type", "surrogate-model", "malformed-model", "model-kind-mismatch")
        model = CASH if cash else FIXED
        if name == "cash-whitespace":
            model += "\n"
        manifest = dict(bundle_schema_version=1, model_id=name, model_kind="cash" if cash else "fixture_fixed_by_right",
            model_format_id="options_lab.cash_json.v1" if cash else "options_lab.fixed_by_right_json.v1",
            model_hash=hashlib.sha256(model.encode()).hexdigest(), fixture_id=FIXTURE_ID, bundle_record_id=name,
            feature_binding=None if cash else deepcopy(feature), policy_binding=deepcopy(policy), runtime_binding=deepcopy(runtime_claim),
            data_manifest_hashes=[] if cash else [dict(role=role, fixture_id=root.fixture_id, payload_sha256=root.payload_sha256)
                for role, root in (("training", norm.baseline.manifest), ("normalization", artifact), ("source", source))],
            validation_report=None, seed=None, provenance=dict(built_at=built.isoformat(), promoted_at=None, activated_at=None,
                availability_basis="simulated", simulated_available_at=SIMULATED_AT, simulated_schedule=None,
                evaluation_block=None, activation_gap=None), prediction_contract=None if cash else deepcopy(prediction),
            claimed_origin="synthetic", claimed_permitted_use="core_fixture")
        if name == "wrong-runtime":
            manifest["runtime_binding"]["implementation_digest"] = "0" * 64
        if name == "wrong-selection":
            manifest["policy_binding"]["selection_rule_id"] = "0" * 64
        if name == "wrong-normalization":
            manifest["feature_binding"]["normalization_hash"] = "0" * 64
        if name in ("wrong-source", "missing-profile"):
            manifest["feature_binding"]["source_profiles"][0]["profile_id"] = "wrong-source-bars" if name == "wrong-source" else "absent"
        if name == "undeclared-source":
            manifest["data_manifest_hashes"] = manifest["data_manifest_hashes"][:2]
        if name == "future-reference":
            target = next(m for m in source.members if m.record_id == "good-proof")
            manifest["validation_report"] = dict(fixture_id=source.fixture_id, payload_sha256=source.payload_sha256,
                                                record_id=target.record_id, raw_hash=target.raw_hash)
        if name == "built-after-assembly":
            manifest["provenance"]["built_at"] = (built + timedelta(days=1)).isoformat()
        if name == "empty-profiles":
            manifest["feature_binding"]["source_profiles"] = []
        if name == "implicit-only":
            manifest["data_manifest_hashes"] = manifest["data_manifest_hashes"][2:]
        inspected = normalize_bundle_manifest(manifest, **receipt)
        assert inspected.value is not None, inspected.rejection
        body = dict(schema_version=1, manifest=inspected.value.snapshot(), model_utf8=model)
        if name in ("bad-model-type", "surrogate-model", "malformed-model", "model-kind-mismatch"):
            body["model_utf8"] = {"bad-model-type": False, "surrogate-model": "\ud800",
                                 "malformed-model": "{", "model-kind-mismatch": FIXED}[name]
        env = dict(event_id="event-" + name, raw_ref="synthetic://bundle/" + name, simulated_received_at=SIMULATED_AT,
            stream_id="model-bundle", receive_sequence=None, supersedes_record_id="cash" if name == "superseded" else None,
            contract=None, metadata=None)
        payload["members"].append(_member(name, "model_bundle", "wrong-source" if name == "wrong-profile" else "model-bundle", body, env))
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    payload = build_fixture()
    target.write_bytes(payload)
    print(target, hashlib.sha256(payload).hexdigest())
