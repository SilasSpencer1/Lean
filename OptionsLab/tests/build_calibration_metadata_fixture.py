"""Build actual registered calibration metadata and its runtime-bound consumer fixture."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_calibration_inputs_fixture import admitted, add, base, profile, reference, runtime_claim, GENERATOR
from build_fixture import _payload_bytes
from options_lab.bundle_inputs import normalize_model_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.calibration import normalize_calibration_partition
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.volume_normalization import normalize_feature_normalization


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
NAMES = ("p14c-metadata-v1", "p14c-metadata-bundle-v1")
AT = "2026-09-30T22:00:00Z"


def partition_owner():
    """Resolve the registered partition through its actual P08, model and P10 owners.

    :returns: Actual partition value and original model UTF-8 string.
    :raises AssertionError: If any prerequisite fixture is not admitted.
    """
    partition = admitted("p14c-partition-v1")
    member = next(m for m in partition.members if m.record_id == "partition")
    fixed = next(m for m in admitted("p14a-bundle-content-v1").members if m.record_id == "fixed").decode_raw_body()
    model = normalize_model_bytes(fixed["model_utf8"].encode(), event_id="metadata-build",
        raw_ref="synthetic://metadata-build", received_at=datetime.now(timezone.utc)).value
    assert model is not None
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    norm_member = next(m for m in artifact.members if m.record_id == "good")
    normalization = normalize_feature_normalization(norm_member.decode_raw_body(), manifest=artifact,
        record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    assert normalization is not None
    roots = tuple(admitted(name) for name in ("p14c-partition-samples-v1", "p14c-partition-memberships-v1",
        "p14c-fit-sources-v1", "p14c-fit-membership-v1"))
    checked = normalize_calibration_partition(member.decode_raw_body(), fixture=partition,
        record_id=member.record_id, model=model, spec=EXACT_VWAP_SPEC, normalization=normalization,
        upstream_fixtures=roots)
    assert checked.value is not None, checked.rejection
    return checked.value, fixed["model_utf8"]


def metadata() -> bytes:
    """Build a registered record with complete assigned populations and pair declarations.

    :returns: Canonical metadata fixture bytes.
    """
    owner, _ = partition_owner()
    pairs = [[day.isoformat() for day in pair] for pair in owner.session_pairs if len(pair) == 2]
    raw = dict(schema_version=1, calibration_id="august-2026-fixed-right-metadata",
        partition_ref=reference(owner.fixture, owner.member), model_hash=owner.model_hash,
        frozen_threshold_return=None if owner.frozen_threshold_return is None else str(owner.frozen_threshold_return),
        bucket_rule_id=owner.bucket_rule_id, available_at=AT, buckets=[])
    for row in owner.model.rows:
        refs = [ref for ref, sample in zip(owner.calibration_sample_refs, owner.calibration_members)
                if sample.bucket_id == row.calibration_bucket]
        raw["buckets"].append(dict(bucket_id=row.calibration_bucket, member_refs=[ref.snapshot() for ref in refs],
            two_session_blocks=pairs, penalty="0.01", uncertainty_method_id="fixture_fixed_penalty_v1",
            modeled_estimator_status="not_performed"))
    payload = base(NAMES[0], [profile("calibration_record", GENERATOR, "calibration-records")])
    payload["generator_source_ref"] = "OptionsLab/tests/build_calibration_metadata_fixture.py"
    add(payload, "record", "calibration_record", raw, "calibration-records", AT)
    for record, mutate in (("wrong-model-hash", lambda body: body.update(model_hash="0" * 64)),
                           ("missing-member", lambda body: body["buckets"][0]["member_refs"].pop()),
                           ("wrong-pair", lambda body: body["buckets"][0]["two_session_blocks"].pop()),
                           ("negative-penalty", lambda body: body["buckets"][0].update(penalty="-0.01")),
                           ("wrong-method", lambda body: body["buckets"][0].update(uncertainty_method_id="other")),
                           ("wrong-partition", lambda body: body["partition_ref"].update(raw_hash="0" * 64)),
                           ("over-bucket-population", lambda body: body["buckets"][0].update(
                               member_refs=[body["buckets"][0]["member_refs"][0] |
                                   {"record_id": f"missing-{index:04d}"} for index in range(4096)]))):
        adverse = deepcopy(raw)
        mutate(adverse)
        add(payload, record, "calibration_record", adverse, "calibration-records", AT)
    return _payload_bytes(payload)


def bundle() -> bytes:
    """Build a runtime-bound bundle that finds the partition through the record.

    :returns: Canonical model bundle fixture bytes.
    :raises AssertionError: If its inspected manifest is invalid.
    """
    old = admitted("p14c-partition-bundle-v1")
    fixed = next(m for m in old.members if m.record_id == "fixed-partition").decode_raw_body()
    manifest = deepcopy(fixed["manifest"])
    record = admitted(NAMES[0])
    manifest.update(fixture_id=NAMES[1], bundle_record_id="fixed-metadata", model_id="fixed-metadata")
    manifest["runtime_binding"] = runtime_claim()
    manifest["prediction_contract"].update(
        calibration_record=reference(record, next(m for m in record.members if m.record_id == "record")),
        calibration_partition=None, model_membership=None, tuning_membership=None,
        uncertainty_rule_id="fixture_fixed_penalty_v1")
    manifest["data_manifest_hashes"].append(dict(role="calibration", fixture_id=record.fixture_id,
        payload_sha256=record.payload_sha256))
    manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
    checked = normalize_bundle_manifest(manifest, event_id="metadata-bundle-build",
        raw_ref="synthetic://metadata-bundle-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    payload = base(NAMES[1], [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                   "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_calibration_metadata_fixture.py"
    add(payload, "fixed-metadata", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", AT)
    partition = admitted("p14c-partition-v1")
    partition_member = next(m for m in partition.members if m.record_id == "partition")
    partition_body = partition_member.decode_raw_body()
    for record_name, mutate in (("fixed-direct", lambda claim: claim.update(
            calibration_partition=reference(partition, partition_member),
            model_membership=partition_body["model_membership_ref"],
            tuning_membership=partition_body["tuning_membership_ref"])),
        ("wrong-direct-partition", lambda claim: claim.update(calibration_partition=reference(
            partition, next(m for m in partition.members if m.record_id == "wrong-model-hash")))),
        ("wrong-direct-membership", lambda claim: claim.update(model_membership=reference(
            admitted("p14c-fit-membership-v1"), next(m for m in admitted("p14c-fit-membership-v1").members
                if m.record_id == "model")))),
        ("wrong-threshold", lambda claim: claim.update(threshold_return="0.01")),
        ("wrong-method", lambda claim: claim.update(uncertainty_rule_id=None))):
        changed = deepcopy(manifest)
        changed.update(bundle_record_id=record_name, model_id=record_name)
        mutate(changed["prediction_contract"])
        inspected = normalize_bundle_manifest(changed, event_id="metadata-bundle-build",
            raw_ref="synthetic://metadata-bundle-build", received_at=datetime.now(timezone.utc))
        assert inspected.value is not None, inspected.rejection
        add(payload, record_name, "model_bundle", dict(schema_version=1,
            manifest=inspected.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", AT)
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    name = NAMES[stage - 1]
    payload = (metadata, bundle)[stage - 1]()
    (FOLDER / (name + ".json")).write_bytes(payload)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name, generator_id=GENERATOR if stage == 1
        else "optionslab-bundle-fixture-builder", generator_version="1",
        payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(payload).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(payload).hexdigest())
