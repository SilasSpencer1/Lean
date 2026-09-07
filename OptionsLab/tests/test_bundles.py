"""Bundle content must be backed by current runtime and actual registered bytes."""

from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from test_runtime import source, run_at
from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.config import StrategyConfig
from options_lab.feature_vector import EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC
from options_lab.runtime import measure_runtime
from options_lab.volume_normalization import normalize_feature_normalization


FOLDER = Path(__file__).parent / "fixtures"
FIXTURE_ID = "p14a-bundle-content-v1"
RECEIPT = dict(event_id="bundle-test", raw_ref="synthetic://bundle-test",
               received_at=datetime(2026, 9, 7, tzinfo=timezone.utc))


def api():
    """Require the real consumer, making missing implementation an explicit RED."""
    assert importlib.util.find_spec("options_lab.bundles") is not None
    from options_lab import bundles
    return bundles


def admitted(name):
    """Read real registered payloads without any expected-hash override."""
    result = verify_fixture_bundle(name, (FOLDER / (name + ".json")).read_bytes(), **RECEIPT)
    assert result.value is not None, result.rejection
    return result.value


def inputs(record="cash"):
    """Use actual retained model text, manifest claims and P10 dependencies."""
    api()
    fixture = admitted(FIXTURE_ID)
    member = next(m for m in fixture.members if m.record_id == record)
    body = member.decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], **RECEIPT).value
    assert manifest is not None
    normalization = None
    fixed = manifest.model_kind != "cash"
    if fixed:
        artifact = admitted("p10b-volume-normalization-v1")
        raw = next(m for m in artifact.members if m.record_id == "good").decode_raw_body()
        normalization = normalize_feature_normalization(raw, manifest=artifact, record_id="good",
            training_manifest=admitted("p10a-volume-training-v1"),
            decision_at=datetime(2026, 9, 3, 1, tzinfo=timezone.utc)).value
        assert normalization is not None
    runtime = measure_runtime().value
    assert runtime is not None
    return manifest, body["model_utf8"].encode(), dict(fixture=fixture,
        spec=EXACT_VWAP_SPEC if fixed else None, normalization=normalization,
        config=StrategyConfig(), runtime=runtime,
        upstream_fixtures=(admitted("p11-feature-vector-v1"),) if fixed else ())


def verify(record="cash", **overrides):
    """Execute the actual consumer with narrowly replaced trusted inputs."""
    manifest, model, kwargs = inputs(record)
    return api().verify_bundle(manifest, model, **(kwargs | overrides))


def digest(value):
    """Independently compute the documented canonical JSON digest."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize("record", ["cash", "fixed"])
def test_actual_registered_cash_and_fixed_are_content_only(record):
    result = verify(record)
    assert result.rejection is None
    value = result.value
    assert value.manifest.model_kind == ("cash" if record == "cash" else "fixture_fixed_by_right")
    assert value.fixture is not value.original_fixture
    assert value.fixture.payload_bytes == value.original_fixture.payload_bytes
    assert value.runtime == value.supplied_runtime and value.runtime is not value.supplied_runtime
    assert value.model.model_hash == hashlib.sha256(value.model.original_model_bytes).hexdigest()
    assert value.bundle_hash == digest(dict(record_kind="options_lab.model_bundle", schema_version=1,
        manifest_hash=value.manifest.manifest_hash, model_hash=value.model.model_hash))
    assert value.manifest.provenance.built_at <= value.fixture.assembled_at
    assert (value.origin, value.fidelity_tier, value.permitted_use) == ("synthetic", 0, "core_fixture")
    assert value.operational_allowed is value.economic_allowed is False
    assert not any(hasattr(value, name) for name in ("ready", "predict", "activate"))
    assert value.manifest.validation_report is value.manifest.provenance.simulated_schedule is None
    if record == "cash":
        assert value.model.rows == () and value.spec is value.normalization_result is None
        assert value.source_profiles == value.upstream_fixtures == ()
    else:
        assert tuple(r.mean_attempt_return for r in value.model.rows) == (Decimal("0.02"), Decimal("-0.01"))
        normalization = value.normalization_result.value
        assert normalization is not value.supplied_normalization
        assert normalization.snapshot == value.supplied_normalization.snapshot
        assert normalization.baseline.snapshot == value.supplied_normalization.baseline.snapshot
        assert value.normalization_result.decision_at == normalization.available_at
        assert len(value.upstream_fixtures) == 3
        assert tuple(p.claim.profile_id for p in value.source_profiles) == (
            "bars", "good-option_quote", "good-underlying_quote", "good-greek", "good-proof")
        assert len(value.source_profiles[0].members) == 36
        assert json.loads(value.source_profiles[0].profile_bytes)["source"] == "fixture-volume-bars"


@pytest.mark.parametrize("record,field,code", [
    ("wrong-runtime", "runtime_binding.implementation_digest", "counterpart_mismatch"),
    ("wrong-selection", "policy_binding.selection_rule_id", "counterpart_mismatch"),
    ("wrong-normalization", "feature_binding.normalization_hash", "counterpart_mismatch"),
    ("wrong-source", "feature_binding.source_profiles", "training_source_mismatch"),
    ("missing-profile", "feature_binding.source_profiles", "unknown_profile"),
    ("undeclared-source", "feature_binding.source_profiles", "undeclared_source"),
    ("future-reference", "validation_report", "unsupported_reference"),
    ("built-after-assembly", "provenance.built_at", "built_after_assembly"),
    ("superseded", "member", "artifact_not_immutable"),
    ("wrong-profile", "member", "profile_mismatch"),
])
def test_registered_adverse_content_is_not_authorized_by_p08(record, field, code):
    result = verify(record)
    assert result.value is None
    assert (result.rejection.stage, result.rejection.field, result.rejection.code) == (
        "bundle_verification", field, code)
    assert result.rejection.fixture.fixture_id == FIXTURE_ID


def test_exact_inner_model_bytes_and_complete_manifest_membership():
    manifest, model, kwargs = inputs("fixed")
    for bad in (model + b"\n", b"", b"{}"):
        result = api().verify_bundle(manifest, bad, **kwargs)
        assert result.value is None and result.rejection.code == "model_bytes_mismatch"
    raw = manifest.snapshot()
    raw["prediction_contract"]["threshold_return"] = "0.009"
    altered = normalize_bundle_manifest(raw, **RECEIPT).value
    result = api().verify_bundle(altered, model, **kwargs)
    assert result.value is None and result.rejection.code == "manifest_mismatch"


def test_exact_trusted_inputs_and_tuple_bound():
    manifest, model, kwargs = inputs()
    for key, bad in (("fixture", object()), ("runtime", None), ("spec", object()),
                     ("normalization", object()), ("config", {}), ("upstream_fixtures", []),
                     ("upstream_fixtures", (object(),))):
        with pytest.raises(TypeError):
            api().verify_bundle(manifest, model, **(kwargs | {key: bad}))
    for bad_manifest, bad_model in ((None, model), (manifest, bytearray(model))):
        with pytest.raises(TypeError):
            api().verify_bundle(bad_manifest, bad_model, **kwargs)
    result = api().verify_bundle(manifest, model, **(kwargs | {"upstream_fixtures": (kwargs["fixture"],) * 65}))
    assert result.rejection.code == "resource_limit"


def test_fresh_normalization_authorities_need_no_redundant_resupply():
    manifest, model, kwargs = inputs("fixed")
    norm = kwargs["normalization"]
    roots = kwargs["upstream_fixtures"] + (norm.manifest, norm.baseline.manifest)
    assert api().verify_bundle(manifest, model, **(kwargs | {"upstream_fixtures": roots})).value is not None
    for supplied, code in ((roots + (roots[0],), "duplicate_reference"),
        (kwargs["upstream_fixtures"] + (kwargs["fixture"],), "containing_reference"),
        (kwargs["upstream_fixtures"] + (admitted("p08a-greek-ready-v1"),), "unused_reference"),
        ((), "unknown_fixture")):
        result = api().verify_bundle(manifest, model, **(kwargs | {"upstream_fixtures": supplied}))
        assert result.value is None and result.rejection.code == code


def test_actual_config_spec_and_normalization_mismatches_fail():
    assert verify(config=StrategyConfig(max_entries_per_session=2)).rejection.field == "policy_binding.config_hash"
    assert verify("fixed", spec=CLOSE_VOLUME_PROXY_SPEC).rejection.field == "feature_binding.feature_schema_id"
    assert verify("fixed", normalization=None).rejection.code == "normalization_missing"
    assert verify(spec=EXACT_VWAP_SPEC).rejection.code == "cash_counterpart_present"


def test_success_failure_and_profiles_cannot_be_constructed_or_mutated():
    result = verify("fixed")
    for value in (result, result.value, result.value.source_profiles[0], verify("wrong-runtime").rejection):
        with pytest.raises(TypeError):
            type(value)()
        with pytest.raises(TypeError):
            replace(value)
        with pytest.raises(FrozenInstanceError):
            value.extra = True


@pytest.mark.parametrize("record,code", [("bad-model-type", "invalid_type"), ("surrogate-model", "invalid_utf8"),
    ("malformed-model", "normalization_failed"), ("model-kind-mismatch", "model_mismatch")])
def test_actual_registered_bad_model_wrapper_reaches_owned_failure(record, code):
    api()
    fixture = admitted(FIXTURE_ID)
    member = next(m for m in fixture.members if m.record_id == record)
    body = member.decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], **RECEIPT).value
    model = body["model_utf8"].encode() if record in ("malformed-model", "model-kind-mismatch") else b"{}"
    result = api().verify_bundle(manifest, model, fixture=fixture, spec=None, normalization=None,
                                config=StrategyConfig(), runtime=measure_runtime().value)
    assert result.value is None and result.rejection.code == code
    assert result.rejection.received_at == datetime(2026, 9, 4, 14, 5, tzinfo=timezone.utc)
    if record == "malformed-model":
        assert result.rejection.cause.stage == "model_data"


def test_whitespace_identity_and_explicit_missing_coverage_are_retained():
    plain, spaced = verify().value, verify("cash-whitespace").value
    assert plain.model.rows == spaced.model.rows == ()
    assert spaced.model.original_model_bytes == plain.model.original_model_bytes + b"\n"
    assert spaced.model.model_hash != plain.model.model_hash
    empty = verify("empty-profiles").value
    assert empty is not None and empty.source_profiles == ()
    assert empty.manifest.feature_binding.coherence_protocol_ids == (
        "fixture-joint-book-snapshot-v1", "fixture-side-validity-overlap-v1")
    implicit = verify("implicit-only").value
    assert implicit is not None and tuple(r.role for r in implicit.manifest.data_manifest_hashes) == ("source",)


@pytest.mark.parametrize("which", ["payload", "member", "normalization", "baseline", "partition", "runtime"])
def test_retained_factory_evidence_is_rechecked_against_actual_counterparts(which):
    manifest, model, kwargs = inputs("fixed")
    if which in ("payload", "member"):
        fixture = copy(kwargs["fixture"])
        if which == "payload":
            object.__setattr__(fixture, "payload_bytes", fixture.payload_bytes + b"\n")
            object.__setattr__(fixture, "payload_sha256", hashlib.sha256(fixture.payload_bytes).hexdigest())
        else:
            member = copy(fixture.members[0])
            object.__setattr__(member, "raw_hash", "0" * 64)
            object.__setattr__(fixture, "members", (member, *fixture.members[1:]))
        kwargs["fixture"] = fixture
    elif which == "runtime":
        runtime = copy(kwargs["runtime"])
        object.__setattr__(runtime, "catalog_sha256", "0" * 64)
        kwargs["runtime"] = runtime
    else:
        norm = copy(kwargs["normalization"])
        if which == "normalization":
            object.__setattr__(norm, "available_at", norm.available_at + timedelta(seconds=1))
        else:
            baseline = copy(norm.baseline)
            object.__setattr__(norm, "baseline", baseline)
            if which == "baseline":
                object.__setattr__(baseline, "training_input_hash", "0" * 64)
            else:
                partition = copy(baseline.partition)
                object.__setattr__(baseline, "partition", partition)
                object.__setattr__(partition, "training_inputs", ())
        kwargs["normalization"] = norm
    result = api().verify_bundle(manifest, model, **kwargs)
    assert result.value is None
    assert result.rejection.code in ("admission_failed", "retained_content_mismatch", "runtime_unavailable")
    if which == "runtime":
        assert result.rejection.cause.code == "runtime_changed"


def test_aggregate_preflight_and_conflicting_authorities_do_not_decode(monkeypatch):
    manifest, model, kwargs = inputs("fixed")
    owner = api()
    root = copy(kwargs["upstream_fixtures"][0])
    object.__setattr__(root, "payload_bytes", b" " * (32 * 1024 * 1024))
    def unexpected(*args, **kw):
        raise AssertionError("preflight must precede admission")
    monkeypatch.setattr(owner, "verify_fixture_bundle", unexpected)
    result = owner.verify_bundle(manifest, model, **(kwargs | {"upstream_fixtures": (root,)}))
    assert result.rejection.code == "resource_limit"
    root = copy(kwargs["normalization"].manifest)
    object.__setattr__(root, "payload_sha256", "0" * 64)
    result = owner.verify_bundle(manifest, model, **(kwargs | {"upstream_fixtures": (root,)}))
    assert result.rejection.code == "conflicting_reference"


def test_catalog_and_receipt_only_changes_do_not_replace_content_identity():
    manifest, model, kwargs = inputs("fixed")
    old = copy(kwargs["fixture"])
    object.__setattr__(old, "catalog_sha256", "0" * 64)
    object.__setattr__(old, "received_at", RECEIPT["received_at"] + timedelta(hours=1))
    norm = copy(kwargs["normalization"])
    old_artifact = copy(norm.manifest)
    object.__setattr__(old_artifact, "catalog_sha256", "0" * 64)
    object.__setattr__(norm, "manifest", old_artifact)
    result = api().verify_bundle(manifest, model, **(kwargs | {"fixture": old, "normalization": norm}))
    assert result.value is not None
    assert result.value.original_fixture.catalog_sha256 != result.value.fixture.catalog_sha256
    assert result.value.normalization_result.manifest.catalog_sha256 != old_artifact.catalog_sha256


def test_execution_digest_commits_full_actual_snapshot():
    value = verify("fixed").value
    from options_lab.config import config_snapshot
    expected = dict(record_kind="options_lab.execution_policy", schema_version=1,
        target_definition_id=value.config.execution.target_definition,
        execution=config_snapshot(value.config)["policy"]["execution"])
    assert value.manifest.policy_binding.execution_policy_hash == digest(expected)
    assert value.manifest.prediction_contract.base_cost_profile.base_execution_hash == digest(expected)
    for key in ("adverse_return_floor", "entry_limit_rule", "max_exit_replacements"):
        altered = deepcopy(expected)
        altered["execution"][key] = "changed"
        assert digest(altered) != value.manifest.policy_binding.execution_policy_hash


def test_actual_catalog_only_refresh_preserves_bundle_in_fresh_process(source):
    body = ("sys.path.insert(0," + repr(str(FOLDER.parent)) + ")\nimport test_bundles as t\n"
        "v=t.verify('fixed').value\nassert v is not None\n"
        "print(json.dumps([v.bundle_hash,v.runtime.implementation_digest,v.runtime.catalog_sha256,v.runtime.inventory_digest]))")
    before = run_at(source / "src", body=body)
    catalog = source / "src/options_lab/_fixture_catalog.json"
    catalog.write_bytes(catalog.read_bytes() + b"\n")
    after = run_at(source / "src", body=body)
    assert before[:2] == after[:2]
    assert before[2] != after[2] and before[3] != after[3]


@pytest.mark.parametrize("target", ["bundles.py", "_fixture_catalog.json"])
def test_actual_runtime_recheck_and_fresh_code_mismatch_fail(source, target):
    body = ("sys.path.insert(0," + repr(str(FOLDER.parent)) + ")\nimport test_bundles as t\n"
        "m,b,k=t.inputs('cash')\n"
        "p=package/" + repr(target) + "\np.write_bytes(p.read_bytes()+b'\\n')\n"
        "r=t.api().verify_bundle(m,b,**k)\nassert r.value is None\n"
        "out=[r.rejection.code,r.rejection.cause.code]\n"
        "fresh=api.measure_runtime()\n"
        "if fresh.value is not None:\n"
        " r=t.api().verify_bundle(m,b,**(k|{'runtime':fresh.value}))\n"
        " assert r.value is None\n out.append(r.rejection.field)\n"
        "print(json.dumps(out))")
    actual = run_at(source / "src", body=body)
    assert actual == (["runtime_unavailable", "runtime_changed", "runtime_binding.implementation_digest"]
                      if target == "bundles.py" else ["runtime_unavailable", "catalog_unavailable"])
