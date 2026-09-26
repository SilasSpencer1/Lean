"""Build staged actual partition sources, memberships and partition payloads."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_calibration_inputs_fixture import admitted, add, base, profile, reference, GENERATOR
from build_fixture import _payload_bytes
from options_lab.bundle_inputs import normalize_model_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.calibration import BUCKET_RULE_ID
from options_lab.feature_vector import EXACT_VWAP_SPEC
from options_lab.volume_normalization import normalize_feature_normalization


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
NAMES = ("p14c-partition-samples-v1", "p14c-partition-memberships-v1", "p14c-partition-v1",
         "p14c-partition-bundle-v1")
AT = "2026-09-30T22:00:00Z"


def fixture(name, kinds):
    """Prepare one C producer root with exact source profiles.

    :param    name:   Catalog identifier.
    :param    kinds:  Member kinds and profile names.
    :returns:         New fixture payload dictionary.
    """
    payload = base(name, [profile(kind, GENERATOR, stream) for kind, stream in kinds])
    payload["generator_source_ref"] = "OptionsLab/tests/build_calibration_partition_fixture.py"
    return payload


def sample(record, day, right, status, *, sequence=0):
    """Create one explicit synthetic sample without inferred labels.

    :param    record:    Unique source member and decision identity.
    :param    day:       Actual declared session date.
    :param    right:     Call or put model row.
    :param    status:    Explicit observed, censored or invalid status.
    :param    sequence:  Minute offset within the session.
    :returns:            Complete exact sample body.
    """
    minute = 40 + sequence
    def clock(value):
        return (datetime.fromisoformat(day + "T13:00:00+00:00") + timedelta(minutes=value)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    observed = status.startswith("observed_")
    return dict(schema_version=1, sample_id=record, decision_id="decision-" + record,
        contract=dict(underlying="SPY", expiry="2026-09-18", right=right,
            strike="650", multiplier=100, deliverable_id="standard-spy-100"),
        session_date=day, feature_available_at=clock(minute),
        information_start=clock(minute + 1), information_end=clock(minute + 2),
        label_available_at=clock(minute + 30) if observed else None,
        available_at=clock(minute + 31) if observed else None,
        outcome_status=status, bucket_id=right)


def sources():
    """Build two sparse role populations and 100 measured observed tail facts.

    :returns: Canonical source fixture bytes.
    """
    payload = fixture(NAMES[0], (("fit_sample", "fit-samples"),))
    for record, day, right in (("new-model-call", "2026-05-04", "call"),
                               ("new-model-put", "2026-05-05", "put"),
                               ("new-tuning-call", "2026-06-01", "call"),
                               ("new-tuning-put", "2026-07-01", "put")):
        add(payload, record, "fit_sample", sample(record, day, right, "observed_fill"),
            "fit-samples", day + "T14:11:00Z")
    day = date(2026, 8, 3)
    index = 0
    while index < 20:
        if day.weekday() < 5:
            rights = ("call", "call", "call", "put", "put") if index % 2 == 0 else (
                "call", "call", "put", "put", "put")
            for number, right in enumerate(rights):
                record = f"tail-{index:02d}-{number}"
                add(payload, record, "fit_sample", sample(record, day.isoformat(), right,
                    "observed_fill" if number % 2 == 0 else "observed_no_fill", sequence=number),
                    "fit-samples", day.isoformat() + "T15:00:00Z")
            index += 1
        day += timedelta(days=1)
    for right, status in (("call", "censored"), ("put", "invalid")):
        record = "odd-" + right
        add(payload, record, "fit_sample", sample(record, "2026-08-31", right, status),
            "fit-samples", "2026-08-31T15:00:00Z")
    return _payload_bytes(payload)


def memberships():
    """Build separate actual source-bound model and tuning declarations.

    :returns: Canonical membership fixture bytes.
    """
    source = admitted(NAMES[0])
    payload = fixture(NAMES[1], (("model_membership", "model-memberships"),
                                 ("tuning_membership", "tuning-memberships"),
                                 ("calendar_descriptor", "calendar-descriptors")))
    for role in ("model", "tuning"):
        members = [member for member in source.members if member.record_id.startswith("new-" + role)]
        raw = dict(schema_version=1, membership_id="causal-" + role,
                   sample_refs=[reference(source, member) for member in members])
        add(payload, role, role + "_membership", raw, role + "-memberships", AT)
    old = admitted("p14c-fit-membership-v1")
    calendar = deepcopy(next(m for m in old.members if m.record_id == "calendar").decode_raw_body())
    calendar["calendar_id"] = "truncated-august-2026"
    calendar["coverage_end_date"] = "2026-08-28"
    calendar["session_refs"] = [ref for ref in calendar["session_refs"]
        if ref["record_id"] <= "session-2026-08-28"]
    calendar["closed_dates"] = [day for day in calendar["closed_dates"] if day <= "2026-08-28"]
    add(payload, "short-calendar", "calendar_descriptor", calendar, "calendar-descriptors", AT)
    return _payload_bytes(payload)


def partition():
    """Build the exact registered fold with all actual external references.

    :returns: Canonical partition fixture bytes.
    """
    source, members, old_membership, artifact = (admitted(name) for name in
        (NAMES[0], NAMES[1], "p14c-fit-membership-v1", "p14c-volume-normalization-v1"))
    training = admitted("p14c-volume-training-v1")
    norm_member = next(m for m in artifact.members if m.record_id == "good")
    normalization = normalize_feature_normalization(norm_member.decode_raw_body(), manifest=artifact,
        record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc)).value
    assert normalization is not None
    bundle = admitted("p14a-bundle-content-v1")
    fixed = next(m for m in bundle.members if m.record_id == "fixed").decode_raw_body()
    model = normalize_model_bytes(fixed["model_utf8"].encode(), event_id="partition-build",
        raw_ref="synthetic://partition-build", received_at=datetime.now(timezone.utc)).value
    assert model is not None
    by_id = {member.record_id: member for member in members.members}
    tail = [member for member in source.members if member.record_id.startswith(("tail-", "odd-"))]
    raw = dict(schema_version=1, partition_id="august-2026-fixed-right",
        model_membership_ref=reference(members, by_id["model"]),
        tuning_membership_ref=reference(members, by_id["tuning"]),
        calibration_sample_refs=[reference(source, member) for member in tail],
        calendar_ref=reference(old_membership, next(m for m in old_membership.members if m.record_id == "calendar")),
        fold=dict(fold_id="fold-2026-09", recipe_id="xnys-12-3-1-2026-v1",
            training_start_date="2025-06-01", training_end_date="2026-06-01",
            tuning_start_date="2026-06-01", tuning_end_date="2026-08-01",
            calibration_tail_start_date="2026-08-01", calibration_tail_end_date="2026-09-01",
            evaluation_block_id="september-2026", evaluation_start="2026-09-01T04:00:00Z",
            evaluation_end="2026-10-01T04:00:00Z"),
        feature_schema_id=EXACT_VWAP_SPEC.feature_schema_id, transform_id=EXACT_VWAP_SPEC.transform_id,
        normalization_ref=reference(artifact, norm_member), normalization_hash=normalization.content_hash,
        model_hash=model.model_hash, frozen_threshold_return="0", bucket_rule_id=BUCKET_RULE_ID,
        frozen_at="2026-07-31T22:00:00Z", tuning_cutoff="2026-07-31T21:00:00Z")
    payload = fixture(NAMES[2], (("calibration_partition", "partitions"),))
    add(payload, "partition", "calibration_partition", raw, "partitions", AT)
    for record, mutate in (("wrong-model-ref", lambda body: body["model_membership_ref"].update(raw_hash="0" * 64)),
                           ("wrong-fold", lambda body: body["fold"].update(tuning_end_date="2026-07-31")),
                           ("duplicate-tail", lambda body: body["calibration_sample_refs"].append(body["calibration_sample_refs"][0])),
                           ("wrong-normalization", lambda body: body.update(normalization_hash="0" * 64)),
                           ("wrong-model-hash", lambda body: body.update(model_hash="0" * 64)),
                           ("wrong-bucket-rule", lambda body: body.update(bucket_rule_id="0" * 64)),
                           ("short-tail-calendar", lambda body: body.update(
                               calendar_ref=reference(members, next(m for m in members.members
                                   if m.record_id == "short-calendar")),
                               calibration_sample_refs=[ref for ref in body["calibration_sample_refs"]
                                   if ref["record_id"].startswith("tail-")])),
                           ("over-population", lambda body: body.update(calibration_sample_refs=[
                               body["calibration_sample_refs"][0] | {"record_id": f"missing-{i:04d}"}
                               for i in range(4093)]))):
        adverse = deepcopy(raw)
        mutate(adverse)
        add(payload, record, "calibration_partition", adverse, "partitions", AT)
    return _payload_bytes(payload)


def bundle():
    """Bind current runtime and actual direct and nested partition members.

    :returns: Canonical runtime-bound model bundle bytes.
    """
    old = admitted("p14c-membership-bundle-v1")
    fixed = next(m for m in old.members if m.record_id == "fixed-membership").decode_raw_body()
    manifest = deepcopy(fixed["manifest"])
    sources, memberships, partition_root, calendar_source, calendar_root, profile_source = (admitted(name)
        for name in (NAMES[0], NAMES[1], NAMES[2], "p14c-fit-sources-v1",
                     "p14c-fit-membership-v1", "p11-feature-vector-v1"))
    manifest.update(fixture_id=NAMES[3], bundle_record_id="fixed-partition", model_id="fixed-partition")
    manifest["prediction_contract"].update(
        model_membership=reference(memberships, next(m for m in memberships.members if m.record_id == "model")),
        tuning_membership=reference(memberships, next(m for m in memberships.members if m.record_id == "tuning")),
        calibration_partition=reference(partition_root, next(m for m in partition_root.members if m.record_id == "partition")))
    manifest["data_manifest_hashes"] = [dict(role=role, fixture_id=root.fixture_id,
        payload_sha256=root.payload_sha256) for role, root in (
        ("training", admitted("p14c-volume-training-v1")),
        ("normalization", admitted("p14c-volume-normalization-v1")),
        ("source", profile_source), ("training", memberships), ("tuning", memberships),
        ("training", sources), ("tuning", sources), ("calibration", partition_root),
        ("calibration", sources))]
    manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
    checked = normalize_bundle_manifest(manifest, event_id="partition-bundle-build",
        raw_ref="synthetic://partition-bundle-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    payload = base(NAMES[3], [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                   "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_calibration_partition_fixture.py"
    add(payload, "fixed-partition", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", AT)
    adverse = deepcopy(manifest)
    adverse.update(bundle_record_id="mismatched-direct", model_id="mismatched-direct")
    adverse["prediction_contract"]["model_membership"] = reference(calendar_root,
        next(m for m in calendar_root.members if m.record_id == "model"))
    bad = normalize_bundle_manifest(adverse, event_id="partition-bundle-build",
        raw_ref="synthetic://partition-bundle-build", received_at=datetime.now(timezone.utc))
    assert bad.value is not None, bad.rejection
    add(payload, "mismatched-direct", "model_bundle", dict(schema_version=1,
        manifest=bad.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", AT)
    excessive = deepcopy(manifest)
    excessive.update(bundle_record_id="over-population", model_id="over-population")
    excessive["prediction_contract"]["calibration_partition"] = reference(partition_root,
        next(m for m in partition_root.members if m.record_id == "over-population"))
    bounded = normalize_bundle_manifest(excessive, event_id="partition-bundle-build",
        raw_ref="synthetic://partition-bundle-build", received_at=datetime.now(timezone.utc))
    assert bounded.value is not None, bounded.rejection
    add(payload, "over-population", "model_bundle", dict(schema_version=1,
        manifest=bounded.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", AT)
    threshold = deepcopy(manifest)
    threshold.update(bundle_record_id="mismatched-threshold", model_id="mismatched-threshold")
    threshold["prediction_contract"]["threshold_return"] = "0.01"
    different = normalize_bundle_manifest(threshold, event_id="partition-bundle-build",
        raw_ref="synthetic://partition-bundle-build", received_at=datetime.now(timezone.utc))
    assert different.value is not None, different.rejection
    add(payload, "mismatched-threshold", "model_bundle", dict(schema_version=1,
        manifest=different.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", AT)
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    name = NAMES[stage - 1]
    payload = (sources, memberships, partition, bundle)[stage - 1]()
    (FOLDER / (name + ".json")).write_bytes(payload)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name, generator_id=GENERATOR if stage < 4
        else "optionslab-bundle-fixture-builder", generator_version="1",
        payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(payload).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(payload).hexdigest())
