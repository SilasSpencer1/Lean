"""Actual partition admission and bundle consumption."""

from datetime import datetime, timezone
from copy import copy, deepcopy
from pathlib import Path
import pytest

from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_inputs import normalize_model_bytes
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.volume_normalization import normalize_feature_normalization
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.config import StrategyConfig
from options_lab.runtime import measure_runtime


FOLDER = Path(__file__).parent / "fixtures"
RECEIPT = dict(event_id="partition-test", raw_ref="synthetic://partition-test",
               received_at=datetime(2026, 9, 30, tzinfo=timezone.utc))


def admitted(name):
    result = verify_fixture_bundle(name, (FOLDER / (name + ".json")).read_bytes(), **RECEIPT)
    assert result.value is not None, result.rejection
    return result.value


def test_partition_public_consumer_exists():
    from options_lab.calibration import normalize_calibration_partition
    assert callable(normalize_calibration_partition)


def test_registered_partition_resolves_actual_population_and_odd_tail():
    from options_lab.calibration import normalize_calibration_partition
    fixture = admitted("p14c-partition-v1")
    member = next(m for m in fixture.members if m.record_id == "partition")
    bundle = admitted("p14a-bundle-content-v1")
    fixed = next(m for m in bundle.members if m.record_id == "fixed").decode_raw_body()
    model = normalize_model_bytes(fixed["model_utf8"].encode(), **RECEIPT).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    norm_member = next(m for m in artifact.members if m.record_id == "good")
    norm = normalize_feature_normalization(norm_member.decode_raw_body(), manifest=artifact,
        record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    roots = tuple(admitted(name) for name in ("p14c-partition-samples-v1", "p14c-partition-memberships-v1",
        "p14c-fit-sources-v1", "p14c-fit-membership-v1"))
    result = normalize_calibration_partition(member.decode_raw_body(), fixture=fixture,
        record_id="partition", model=model, spec=EXACT_VWAP_SPEC, normalization=norm,
        upstream_fixtures=roots)
    assert result.value is not None, result.rejection
    value = result.value
    assert len(value.calibration_members) == 102
    assert len(value.session_pairs) == 11 and len(value.session_pairs[-1]) == 1
    assert value.content_hash


def test_registered_partition_structural_adverses_reject_safely():
    from options_lab.calibration import normalize_calibration_partition
    fixture = admitted("p14c-partition-v1")
    fixed = next(m for m in admitted("p14a-bundle-content-v1").members
                 if m.record_id == "fixed").decode_raw_body()
    model = normalize_model_bytes(fixed["model_utf8"].encode(), **RECEIPT).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    norm = normalize_feature_normalization(next(m for m in artifact.members if m.record_id == "good").decode_raw_body(),
        manifest=artifact, record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    roots = tuple(admitted(name) for name in ("p14c-partition-samples-v1", "p14c-partition-memberships-v1",
        "p14c-fit-sources-v1", "p14c-fit-membership-v1"))
    for record, code in (("wrong-model-ref", "raw_hash_mismatch"),
                         ("wrong-fold", "unsupported_recipe"),
                         ("duplicate-tail", "duplicate_reference"),
                         ("wrong-normalization", "counterpart_mismatch"),
                         ("wrong-model-hash", "counterpart_mismatch"),
                         ("wrong-bucket-rule", "counterpart_mismatch"),
                         ("short-tail-calendar", "incomplete_tail"),
                         ("over-population", "resource_limit")):
        member = next(m for m in fixture.members if m.record_id == record)
        supplied = tuple(root for root in roots if record != "short-tail-calendar"
                         or root.fixture_id != "p14c-fit-membership-v1")
        result = normalize_calibration_partition(member.decode_raw_body(), fixture=fixture,
            record_id=record, model=model, spec=EXACT_VWAP_SPEC, normalization=norm,
            upstream_fixtures=supplied)
        assert result.value is None and result.rejection.code == code


def test_partition_raw_limit_and_trusted_types_precede_owner_traversal():
    from options_lab.calibration import normalize_calibration_partition
    fixture = admitted("p14c-partition-v1")
    raw = next(m for m in fixture.members if m.record_id == "partition").decode_raw_body()
    fixed = next(m for m in admitted("p14a-bundle-content-v1").members
                 if m.record_id == "fixed").decode_raw_body()
    model = normalize_model_bytes(fixed["model_utf8"].encode(), **RECEIPT).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    norm = normalize_feature_normalization(next(m for m in artifact.members if m.record_id == "good").decode_raw_body(),
        manifest=artifact, record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    kwargs = dict(fixture=fixture, record_id="partition", model=model, spec=EXACT_VWAP_SPEC,
                  normalization=norm, upstream_fixtures=tuple(admitted(name) for name in
                      ("p14c-partition-samples-v1", "p14c-partition-memberships-v1",
                       "p14c-fit-sources-v1", "p14c-fit-membership-v1")))
    for key in ("fixture", "model", "spec", "normalization"):
        with pytest.raises(TypeError):
            normalize_calibration_partition(raw, **(kwargs | {key: object()}))
    huge = deepcopy(raw)
    seed = huge["calibration_sample_refs"][0]
    huge["calibration_sample_refs"] = [seed | {"fixture_id": "f" * 1020 + f"{i:04d}",
                                             "record_id": "r" * 1020 + f"{i:04d}"} for i in range(4096)]
    result = normalize_calibration_partition(huge, **kwargs)
    assert result.value is None and result.rejection.code == "resource_limit"
    assert result.rejection.member is None
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality ran")

    changed_model = copy(model)
    object.__setattr__(changed_model, "rows", (Hostile(), model.rows[1]))
    changed_norm = copy(norm)
    object.__setattr__(changed_norm, "availability_basis", Hostile())
    class HostileBaseline:
        @property
        def partition(self):
            raise AssertionError("hostile partition getter ran")

    changed_baseline_norm = copy(norm)
    object.__setattr__(changed_baseline_norm, "baseline", HostileBaseline())
    changed_partition_norm = copy(norm)
    changed_baseline = copy(norm.baseline)
    changed_partition = copy(norm.baseline.partition)
    object.__setattr__(changed_partition, "training_inputs", ())
    object.__setattr__(changed_baseline, "partition", changed_partition)
    object.__setattr__(changed_partition_norm, "baseline", changed_baseline)
    for changed in (dict(model=changed_model), dict(normalization=changed_norm),
                    dict(normalization=changed_baseline_norm),
                    dict(normalization=changed_partition_norm)):
        result = normalize_calibration_partition(raw, **(kwargs | changed))
        assert result.value is None and result.rejection.code == "retained_content_mismatch"


def test_bundle_consumes_partition_without_record():
    from options_lab.bundles import verify_bundle
    fixture = admitted("p14c-partition-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == "fixed-partition").decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], **RECEIPT).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    norm_member = next(m for m in artifact.members if m.record_id == "good")
    norm = normalize_feature_normalization(norm_member.decode_raw_body(), manifest=artifact,
        record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    roots = tuple(admitted(name) for name in ("p11-feature-vector-v1", "p14c-partition-samples-v1",
        "p14c-partition-memberships-v1", "p14c-partition-v1", "p14c-fit-sources-v1",
        "p14c-fit-membership-v1"))
    result = verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC, normalization=norm, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=roots)
    assert result.value is not None, result.rejection
    assert result.value.calibration_partition is not None
    assert result.value.manifest.prediction_contract.calibration_record is None


@pytest.mark.parametrize("record,code", (("mismatched-direct", "membership_mismatch"),
                                         ("over-population", "resource_limit"),
                                         ("mismatched-threshold", "threshold_mismatch")))
def test_bundle_rejects_direct_disagreement_and_aggregate_population(record, code):
    from options_lab.bundles import verify_bundle
    fixture = admitted("p14c-partition-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    manifest = normalize_bundle_manifest(body["manifest"], **RECEIPT).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    norm = normalize_feature_normalization(next(m for m in artifact.members if m.record_id == "good").decode_raw_body(),
        manifest=artifact, record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    roots = tuple(admitted(name) for name in ("p11-feature-vector-v1", "p14c-partition-samples-v1",
        "p14c-partition-memberships-v1", "p14c-partition-v1", "p14c-fit-sources-v1",
        "p14c-fit-membership-v1"))
    result = verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC, normalization=norm, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=roots)
    assert result.value is None
    assert result.rejection.code == code
