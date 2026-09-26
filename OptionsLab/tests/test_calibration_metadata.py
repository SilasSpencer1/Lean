"""Actual calibration record admission and bundle retention."""

from copy import copy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import pytest

from build_calibration_metadata_fixture import partition_owner
from options_lab.admission import verify_fixture_bundle
from options_lab.calibration import normalize_calibration_metadata
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundles import verify_bundle
from options_lab.config import StrategyConfig
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.runtime import measure_runtime
from options_lab.volume_normalization import normalize_feature_normalization


FOLDER = Path(__file__).parent / "fixtures"


def admitted(name):
    result = verify_fixture_bundle(name, (FOLDER / (name + ".json")).read_bytes(),
        event_id="metadata-test", raw_ref="synthetic://metadata-test",
        received_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert result.value is not None, result.rejection
    return result.value


def record_result(record):
    partition, _ = partition_owner()
    fixture = admitted("p14c-metadata-v1")
    member = next(m for m in fixture.members if m.record_id == record)
    return normalize_calibration_metadata(member.decode_raw_body(), fixture=fixture, record_id=record,
        model=partition.model, spec=partition.spec, normalization=partition.normalization,
        upstream_fixtures=(partition.fixture, *partition.upstream_fixtures))


def test_metadata_public_consumer_exists():
    assert callable(normalize_calibration_metadata)


def test_registered_record_retains_full_population_and_only_complete_pairs():
    result = record_result("record")
    assert result.value is not None, result.rejection
    value = result.value
    assert sum(len(bucket.member_refs) for bucket in value.buckets) == 102
    assert all(len(bucket.two_session_blocks) == 10 for bucket in value.buckets)
    assert len(value.partition.session_pairs[-1]) == 1
    assert value.statistical_status == "not_performed" and value.statistical_readiness is False
    assert all(bucket.modeled_estimator_status == "not_performed" for bucket in value.buckets)
    assert value.content_hash


def test_registered_record_structural_adverses_reject():
    for record, code in (("wrong-model-hash", "counterpart_mismatch"),
                         ("missing-member", "population_mismatch"),
                         ("wrong-pair", "population_mismatch"),
                         ("negative-penalty", "invalid_value"),
                         ("wrong-method", "unsupported_claim"),
                         ("wrong-partition", "raw_hash_mismatch"),
                         ("over-bucket-population", "resource_limit")):
        result = record_result(record)
        assert result.value is None and result.rejection.code == code


def test_shared_bundle_member_boundary_rejects_oversized_c_body_before_decode():
    from options_lab.bundles import _BundleFailure, _member

    fixture = admitted("p14c-partition-v1")
    member = copy(next(m for m in fixture.members if m.record_id == "partition"))
    body = b"{" + b" " * (8 * 1024 * 1024)
    object.__setattr__(member, "raw_body_bytes", body)
    object.__setattr__(member, "raw_hash", hashlib.sha256(body).hexdigest())
    altered = copy(fixture)
    object.__setattr__(altered, "members", (member,))
    with pytest.raises(_BundleFailure) as error:
        _member(altered, member.record_id, "calibration_partition", member.raw_hash)
    assert error.value.args[1] == "resource_limit"


def bundle_result(record):
    fixture = admitted("p14c-metadata-bundle-v1")
    body = next(m for m in fixture.members if m.record_id == record).decode_raw_body()
    receipt = dict(event_id="metadata-test", raw_ref="synthetic://metadata-test",
                   received_at=datetime(2026, 9, 30, tzinfo=timezone.utc))
    manifest = normalize_bundle_manifest(body["manifest"], **receipt).value
    training, artifact = admitted("p14c-volume-training-v1"), admitted("p14c-volume-normalization-v1")
    member = next(m for m in artifact.members if m.record_id == "good")
    norm = normalize_feature_normalization(member.decode_raw_body(), manifest=artifact,
        record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    roots = tuple(admitted(name) for name in ("p11-feature-vector-v1", "p14c-partition-samples-v1",
        "p14c-partition-memberships-v1", "p14c-partition-v1", "p14c-fit-sources-v1",
        "p14c-fit-membership-v1", "p14c-metadata-v1"))
    return verify_bundle(manifest, body["model_utf8"].encode(), fixture=fixture,
        spec=EXACT_VWAP_SPEC, normalization=norm, config=StrategyConfig(),
        runtime=measure_runtime().value, upstream_fixtures=roots)


@pytest.mark.parametrize("record", ("fixed-metadata", "fixed-direct"))
def test_bundle_traverses_record_to_partition_and_retains_actual_owners(record):
    result = bundle_result(record)
    assert result.value is not None, result.rejection
    value = result.value
    assert value.calibration_metadata is not None and value.calibration_partition is not None
    assert value.calibration_metadata.partition.member.raw_body_bytes == value.calibration_partition.member.raw_body_bytes
    assert value.model_membership is not None and value.tuning_membership is not None
    assert (value.manifest.prediction_contract.calibration_partition is None) == (record == "fixed-metadata")


@pytest.mark.parametrize("record,code", (("wrong-direct-partition", "counterpart_mismatch"),
                                        ("wrong-direct-membership", "membership_mismatch"),
                                        ("wrong-threshold", "threshold_mismatch"),
                                        ("wrong-method", "counterpart_mismatch")))
def test_bundle_rejects_record_counterparty_mismatch(record, code):
    result = bundle_result(record)
    assert result.value is None and result.rejection.code == code
