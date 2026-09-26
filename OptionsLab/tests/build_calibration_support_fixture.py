"""Build separate causally dated synthetic C2 calibration fixtures."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_calibration_inputs_fixture import admitted, add, base, profile, reference, runtime_claim, GENERATOR
from build_fixture import _payload_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
NAMES = ("p14c2-sources-v1", "p14c2-calendar-v1", "p14c2-partition-v1",
         "p14c2-record-v1", "p14c2-bundle-v1")
CALENDAR_AT = "2026-04-30T00:00:00Z"
FIT_AT = "2026-08-31T22:00:00Z"
RECORD_AT = "2026-09-01T01:00:00Z"
ADVERSES = ("late-status", "cross-role-leak", "late-odd-label", "forty-nine-call",
            "unsupported-pair", "late-tune-status", "early-close", "censored-partial",
            "status-unknown", "duplicate-sample")


def payload(name: str, kinds: tuple[tuple[str, str], ...]) -> dict:
    """Make one current synthetic source root.

    :param    name:   Registered fixture identifier.
    :param    kinds:  Kind and stream pairs.
    :returns:         Mutable canonical fixture body.
    """
    value = base(name, [profile(kind, GENERATOR, stream) for kind, stream in kinds])
    value["generator_source_ref"] = "OptionsLab/tests/build_calibration_support_fixture.py"
    return value


def sources() -> bytes:
    """Copy actual tail outcomes and model a calendar known on April 30.

    :returns: Canonical source payload bytes.
    """
    value = payload(NAMES[0], (("exchange_session", "fit-calendar-c2"), ("fit_sample", "fit-samples-c2")))
    calendar_profile = value["modeled_source_profiles"][0]
    calendar_profile["source"] = "fixture-calendar-c2"
    old_calendar = admitted("p14c-fit-sources-v1")
    for member in old_calendar.members:
        if member.kind != "exchange_session":
            continue
        body = deepcopy(member.decode_raw_body())
        body.update(source="fixture-calendar-c2", available_at=CALENDAR_AT)
        add(value, member.record_id, "exchange_session", body, "fit-calendar-c2", CALENDAR_AT)
        if body["session_date"] == "2026-08-03":
            adverse = deepcopy(body)
            adverse.update(kind="early_close", closes_at="2026-08-03T17:00:00Z",
                           provider_record_id="adverse-early-close")
            add(value, "adverse-early-close", "exchange_session", adverse, "fit-calendar-c2", CALENDAR_AT)
    old_samples = admitted("p14c-partition-samples-v1")
    for member in old_samples.members:
        if member.record_id.startswith("new-tuning-"):
            add(value, member.record_id, "fit_sample", member.decode_raw_body(), "fit-samples-c2",
                member.decode_raw_body()["session_date"] + "T15:00:00Z")
            if member.record_id == "new-tuning-call":
                late = deepcopy(member.decode_raw_body())
                late.update(sample_id="adverse-late-tune-status", decision_id="decision-adverse-late-tune-status",
                            available_at="2026-08-01T01:00:00Z")
                add(value, "adverse-late-tune-status", "fit_sample", late, "fit-samples-c2",
                    "2026-08-01T01:00:00Z")
        if not member.record_id.startswith(("tail-", "odd-")):
            continue
        body = deepcopy(member.decode_raw_body())
        if member.record_id.startswith("odd-"):
            body["available_at"] = "2026-08-31T14:11:00Z"
        add(value, member.record_id, "fit_sample", body, "fit-samples-c2",
            body["session_date"] + "T15:00:00Z")
        if member.record_id in ("tail-00-0", "tail-00-3", "odd-call"):
            adverse = deepcopy(body)
            if member.record_id == "tail-00-0":
                adverse["available_at"] = "2026-09-01T03:00:00Z"
                name = "adverse-late-status"
            elif member.record_id == "tail-00-3":
                adverse["decision_id"] = "decision-new-model-call"
                name = "adverse-cross-role-leak"
            else:
                adverse.update(outcome_status="observed_fill", label_available_at="2026-09-01T05:00:00Z",
                               available_at="2026-09-01T05:01:00Z")
                name = "adverse-late-odd-label"
            adverse["sample_id"] = name
            add(value, name, "fit_sample", adverse, "fit-samples-c2", RECORD_AT)
        if member.record_id == "odd-call":
            partial = deepcopy(body)
            partial.update(sample_id="adverse-censored-partial", decision_id="decision-adverse-censored-partial",
                           information_end="2026-08-31T15:00:00Z")
            add(value, "adverse-censored-partial", "fit_sample", partial, "fit-samples-c2", RECORD_AT)
        if member.record_id == "tail-00-0":
            unknown = deepcopy(body)
            unknown.update(sample_id="adverse-status-unknown", decision_id="decision-adverse-status-unknown",
                           available_at=None)
            add(value, "adverse-status-unknown", "fit_sample", unknown, "fit-samples-c2", RECORD_AT)
            duplicate = deepcopy(body)
            duplicate["sample_id"] = "tail-00-1"
            duplicate["decision_id"] = "decision-adverse-duplicate-sample"
            add(value, "adverse-duplicate-sample", "fit_sample", duplicate, "fit-samples-c2", RECORD_AT)
    return _payload_bytes(value)


def calendar() -> bytes:
    """Bind the whole declared May to September calendar to April source facts.

    :returns: Canonical calendar descriptor payload bytes.
    """
    source = admitted(NAMES[0])
    old = admitted("p14c-fit-membership-v1")
    body = deepcopy(next(m for m in old.members if m.record_id == "calendar").decode_raw_body())
    body["calendar_id"] = "xnys-modeled-april-2026-c2"
    body["session_refs"] = [reference(source, member) for member in source.members
                            if member.record_id.startswith("session-")]
    body["available_at"] = CALENDAR_AT
    value = payload(NAMES[1], (("calendar_descriptor", "calendar-c2"),
                               ("tuning_membership", "tuning-c2")))
    add(value, "calendar", "calendar_descriptor", body, "calendar-c2", CALENDAR_AT)
    early = deepcopy(body)
    early["calendar_id"] += "-early-close"
    early["session_refs"] = [reference(source,
        next(member for member in source.members if member.record_id == "adverse-early-close"))
        if ref["record_id"] == "session-2026-08-03" else ref for ref in early["session_refs"]]
    add(value, "early-close", "calendar_descriptor", early, "calendar-c2", CALENDAR_AT)
    tuning_refs = [reference(source, member) for member in source.members
                   if member.record_id in ("adverse-late-tune-status", "new-tuning-put")]
    add(value, "late-tune-status", "tuning_membership",
        dict(schema_version=1, membership_id="late-tune-status", sample_refs=tuning_refs),
        "tuning-c2", RECORD_AT)
    return _payload_bytes(value)


def partition() -> bytes:
    """Bind immutable old fit roles to the new tail and calendar owners.

    :returns: Canonical partition payload bytes.
    """
    source, calendar_root = admitted(NAMES[0]), admitted(NAMES[1])
    old = admitted("p14c-partition-v1")
    body = deepcopy(next(m for m in old.members if m.record_id == "partition").decode_raw_body())
    body["partition_id"] = "august-2026-causal-fixed-right"
    body["calibration_sample_refs"] = [reference(source, member) for member in source.members
                                       if member.record_id.startswith(("tail-", "odd-"))]
    body["calendar_ref"] = reference(calendar_root, calendar_root.members[0])
    value = payload(NAMES[2], (("calibration_partition", "partition-c2"),))
    add(value, "partition", "calibration_partition", body, "partition-c2", RECORD_AT)
    members = {member.record_id: member for member in source.members}
    for name in ADVERSES:
        bad = deepcopy(body)
        bad["partition_id"] += "-" + name
        if name in ("late-status", "cross-role-leak", "late-odd-label", "censored-partial",
                    "status-unknown", "duplicate-sample"):
            old_id = {"late-status": "tail-00-0", "cross-role-leak": "tail-00-3",
                      "late-odd-label": "odd-call", "censored-partial": "odd-call",
                      "status-unknown": "tail-00-0", "duplicate-sample": "tail-00-0"}[name]
            bad["calibration_sample_refs"] = [reference(source, members["adverse-" + name])
                if ref["record_id"] == old_id else ref for ref in bad["calibration_sample_refs"]]
        elif name == "forty-nine-call":
            bad["calibration_sample_refs"] = [ref for ref in bad["calibration_sample_refs"]
                if ref["record_id"] != "tail-00-0"]
        elif name == "unsupported-pair":
            bad["calibration_sample_refs"] = [ref for ref in bad["calibration_sample_refs"]
                if ref["record_id"] not in ("tail-00-0", "tail-00-1", "tail-00-2")]
        elif name == "early-close":
            bad["calendar_ref"] = reference(calendar_root,
                next(member for member in calendar_root.members if member.record_id == name))
        else:
            bad["tuning_membership_ref"] = reference(calendar_root,
                next(member for member in calendar_root.members if member.record_id == name))
        add(value, name, "calibration_partition", bad, "partition-c2", RECORD_AT)
    return _payload_bytes(value)


def record() -> bytes:
    """Declare actual complete bucket populations and fixed penalties.

    :returns: Canonical record payload bytes.
    """
    source, partition_root = admitted(NAMES[0]), admitted(NAMES[2])
    old = admitted("p14c-metadata-v1")
    body = deepcopy(next(m for m in old.members if m.record_id == "record").decode_raw_body())
    body["calibration_id"] = "august-2026-causal-fixed-right-c2"
    body["partition_ref"] = reference(partition_root, partition_root.members[0])
    body["available_at"] = RECORD_AT
    for bucket in body["buckets"]:
        bucket["member_refs"] = [reference(source, member) for member in source.members
            if member.record_id.startswith(("tail-", "odd-"))
            and member.decode_raw_body()["bucket_id"] == bucket["bucket_id"]]
    value = payload(NAMES[3], (("calibration_record", "record-c2"),))
    add(value, "record", "calibration_record", body, "record-c2", RECORD_AT)
    for name in (*ADVERSES, "late-record", "record-before-fit"):
        bad = deepcopy(body)
        bad["calibration_id"] += "-" + name
        if name == "late-record":
            bad["available_at"] = "2026-09-01T03:00:00Z"
        elif name == "record-before-fit":
            bad["available_at"] = "2026-08-30T22:00:00Z"
        else:
            partition_member = next(member for member in partition_root.members if member.record_id == name)
            partition_body = partition_member.decode_raw_body()
            bad["partition_ref"] = reference(partition_root, partition_member)
            for bucket in bad["buckets"]:
                bucket["member_refs"] = [ref for ref in partition_body["calibration_sample_refs"]
                    if next(member for member in source.members if member.record_id == ref["record_id"])
                    .decode_raw_body()["bucket_id"] == bucket["bucket_id"]]
        add(value, name, "calibration_record", bad, "record-c2", RECORD_AT)
    return _payload_bytes(value)


def bundle() -> bytes:
    """Bind the current runtime and actual C2 source maxima to the model bundle.

    :returns: Canonical runtime-bound bundle payload bytes.
    :raises   AssertionError: If the current normalized claims are invalid.
    """
    old = admitted("p14c-metadata-bundle-v1")
    fixed = next(m for m in old.members if m.record_id == "fixed-metadata").decode_raw_body()
    manifest = deepcopy(fixed["manifest"])
    source, calendar_root, partition_root, record_root = (admitted(name) for name in NAMES[:4])
    manifest.update(fixture_id=NAMES[4], bundle_record_id="fixed-c2", model_id="fixed-c2")
    manifest["runtime_binding"] = runtime_claim()
    claim = manifest["prediction_contract"]
    claim.update(calibration_record=reference(record_root, record_root.members[0]),
                 training_feature_cutoff="2026-05-05T13:40:00Z",
                 fit_cutoff=FIT_AT, last_label_available_at="2026-08-28T14:14:00Z")
    manifest["provenance"].update(built_at=datetime.now(timezone.utc).isoformat(),
                                  simulated_available_at=RECORD_AT)
    manifest["data_manifest_hashes"] = [row for row in manifest["data_manifest_hashes"]
        if row["fixture_id"] not in ("p14c-partition-v1", "p14c-metadata-v1")
        and not (row["fixture_id"] == "p14c-partition-samples-v1" and row["role"] == "calibration")]
    manifest["data_manifest_hashes"].extend(dict(role=role, fixture_id=root.fixture_id,
        payload_sha256=root.payload_sha256) for role, root in (("source", source),
        ("source", calendar_root), ("calibration", partition_root),
        ("calibration", source), ("calibration", record_root)))
    checked = normalize_bundle_manifest(manifest, event_id="c2-bundle-build",
        raw_ref="synthetic://c2-bundle-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    value = base(NAMES[4], [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                 "optionslab-bundle-fixture-builder")
    value["generator_source_ref"] = "OptionsLab/tests/build_calibration_support_fixture.py"
    add(value, "fixed-c2", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", RECORD_AT)
    for name in (*ADVERSES, "late-record", "record-before-fit",
                 "wrong-training-max", "wrong-label-max"):
        adverse = deepcopy(manifest)
        adverse.update(bundle_record_id=name, model_id=name)
        record_name = name if name not in ("wrong-training-max", "wrong-label-max") else "record"
        record_member = next(member for member in record_root.members if member.record_id == record_name)
        adverse["prediction_contract"]["calibration_record"] = reference(record_root, record_member)
        if name == "wrong-training-max":
            adverse["prediction_contract"]["training_feature_cutoff"] = "2026-05-05T13:39:00Z"
        if name == "wrong-label-max":
            adverse["prediction_contract"]["last_label_available_at"] = "2026-08-28T14:13:00Z"
        if name == "late-tune-status":
            adverse["data_manifest_hashes"] = [row for row in adverse["data_manifest_hashes"]
                if not (row["role"] == "tuning" and row["fixture_id"] in
                        ("p14c-partition-memberships-v1", "p14c-partition-samples-v1"))]
            adverse["data_manifest_hashes"].extend(dict(role="tuning", fixture_id=root.fixture_id,
                payload_sha256=root.payload_sha256) for root in (calendar_root, source))
        checked = normalize_bundle_manifest(adverse, event_id="c2-bundle-build",
            raw_ref="synthetic://c2-bundle-build", received_at=datetime.now(timezone.utc))
        assert checked.value is not None, checked.rejection
        add(value, name, "model_bundle", dict(schema_version=1,
            manifest=checked.value.snapshot(), model_utf8=fixed["model_utf8"]), "model-bundle", RECORD_AT)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    name = NAMES[stage - 1]
    raw = (sources, calendar, partition, record, bundle)[stage - 1]()
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name, generator_id=GENERATOR if stage < 5
        else "optionslab-bundle-fixture-builder", generator_version="1",
        payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(raw).hexdigest())
