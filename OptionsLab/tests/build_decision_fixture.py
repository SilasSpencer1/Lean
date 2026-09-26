"""Build a registered decision source with actual P13 selection and P11 bars."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from build_fixture import _member, _payload_bytes
from build_calibration_inputs_fixture import admitted, add, base, profile, reference, runtime_claim
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
SOURCE = "p15-decision-source-v1"
PARTITION = "p15-decision-partition-v1"
RECORD = "p15-decision-record-v1"
REPORT = "p15-decision-report-v1"
SCHEDULE = "p15-decision-schedule-v1"
BUNDLE = "p15-decision-bundle-v1"
MEANS = {"call": ("0.08", "0.04"), "put": ("0.04", "0.08"),
         "tie": ("0.08", "0.08"), "negative": ("0.02", "-0.01")}


def model_text(name: str) -> str:
    """Serialize one literal fixed-right model without executable content.

    :param    name: Registered variant identifier.
    :returns: Exact compact UTF-8 model text.
    """
    call, put = MEANS[name]
    return json.dumps(dict(schema_version=1, format_id="options_lab.fixed_by_right_json.v1",
        call=dict(mean_attempt_return=call, calibration_bucket="call"),
        put=dict(mean_attempt_return=put, calibration_bucket="put")), separators=(",", ":"))


def support(stage: str) -> bytes:
    """Bind actual C2 support and B1b report to each new fixed model hash.

    :param    stage: Exact partition, record or report generation stage.
    :returns: Canonical newly framed support fixture bytes.
    :raises   AssertionError: If the requested stage is unknown.
    """
    target, kind, stream, original_root, original_record = {
        "partition": (PARTITION, "calibration_partition", "partitions", "p14c2-partition-v1", "partition"),
        "record": (RECORD, "calibration_record", "records", "p14c2-record-v1", "record"),
        "report": (REPORT, "bundle_validation", "reports", "p14b1b-assembly-v1", "fixed-validation"),
    }[stage]
    upstream = admitted(original_root)
    original = next(m for m in upstream.members if m.record_id == original_record).decode_raw_body()
    generator = ("optionslab-bundle-validation-fixture-builder" if stage == "report"
                 else "optionslab-calibration-inputs-fixture-builder")
    payload = base(target, [profile(kind, generator, stream)], generator)
    payload["generator_source_ref"] = "OptionsLab/tests/build_decision_fixture.py"
    for name in MEANS:
        body = deepcopy(original)
        body["model_hash"] = sha256(model_text(name).encode()).hexdigest()
        if stage == "partition":
            body["partition_id"] = "p15-" + name
        elif stage == "record":
            owner = admitted(PARTITION)
            body.update(calibration_id="p15-" + name,
                        partition_ref=reference(owner, next(m for m in owner.members if m.record_id == name)))
        else:
            partition, record = admitted(PARTITION), admitted(RECORD)
            body.update(validation_id="p15-" + name,
                calibration_partition_ref=reference(partition, next(m for m in partition.members if m.record_id == name)),
                calibration_record_ref=reference(record, next(m for m in record.members if m.record_id == name)),
                implementation_digest=runtime_claim()["implementation_digest"],
                runtime_contract_digest=runtime_claim()["runtime_contract_digest"],
                performed_at=datetime.now(timezone.utc).isoformat())
        add(payload, name, kind, body, stream,
            "2026-09-01T01:00:00Z" if stage != "report" else body["performed_at"])
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def build_schedule() -> bytes:
    """Bind each model variant to its actual support and September block.

    :returns: Canonical evaluation-block source bytes.
    """
    old = admitted("p14b-schedule-v1")
    original = next(m for m in old.members if m.record_id == "block").decode_raw_body()
    partition, record = admitted(PARTITION), admitted(RECORD)
    generator = "optionslab-bundle-schedule-fixture-builder"
    payload = base(SCHEDULE, [profile("evaluation_block", generator, "blocks")], generator)
    payload["generator_source_ref"] = "OptionsLab/tests/build_decision_fixture.py"
    for name in MEANS:
        body = deepcopy(original)
        body.update(model_hash=sha256(model_text(name).encode()).hexdigest(),
            calibration_partition_ref=reference(partition, next(m for m in partition.members if m.record_id == name)),
            calibration_record_ref=reference(record, next(m for m in record.members if m.record_id == name)))
        add(payload, name, "evaluation_block", body, "blocks", "2026-09-01T01:00:00Z")
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def build_source() -> bytes:
    """Combine unchanged source members that share the actual decision clock.

    :returns: Canonical source bytes with one ranked population and one bar stream.
    :raises OSError: If a registered upstream fixture is missing.
    """
    selection = json.loads((FOLDER / "p13c-candidate-selection-v1.json").read_bytes())
    history = json.loads((FOLDER / "p11-feature-vector-v1.json").read_bytes())
    source = deepcopy(selection)
    source.update(fixture_id=SOURCE, generator_id="optionslab-decision-fixture-builder",
        generator_version="1", generator_source_ref="OptionsLab/tests/build_decision_fixture.py",
        assembled_at=datetime.now(timezone.utc).isoformat())
    source["modeled_source_profiles"].append(next(p for p in history["modeled_source_profiles"]
        if p["profile_id"] == "bars"))
    source["members"].extend(m for m in history["members"] if m["record_id"].startswith("bars-"))
    account = next(m for m in source["members"] if m["record_id"] == "account_snapshot")
    account_profile = next(p for p in source["modeled_source_profiles"]
                           if p["profile_id"] == "account_snapshot")
    for name, fee in (("fee2-account_snapshot", "2"), ("missing-fee-account_snapshot", None)):
        row, profile = deepcopy(account), deepcopy(account_profile)
        row["raw_body"]["applicable_round_trip_fees"] = fee
        row["envelope"].update(event_id="event-" + name, raw_ref="synthetic://p15/" + name,
                               stream_id=name)
        profile.update(profile_id=name, stream_id=name)
        source["modeled_source_profiles"].append(profile)
        source["members"].append(_member(name, "account_snapshot", name,
                                          row["raw_body"], row["envelope"]))
    return _payload_bytes(source)


def build_bundle() -> bytes:
    """Bind the fixed and cash B2 models to the actual combined P15 source.

    :returns: Canonical containing bundle bytes with current runtime identity.
    :raises   AssertionError: If a derived normalized manifest is invalid.
    """
    prior, source = admitted("p14b2-availability-bundle-v1"), admitted(SOURCE)
    partition, record, report, schedule = (admitted(name) for name in (PARTITION, RECORD, REPORT, SCHEDULE))
    payload = base(BUNDLE, [profile("model_bundle", "optionslab-bundle-fixture-builder",
                                    "model-bundle")], "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_decision_fixture.py"
    for name in (*MEANS, "cash"):
        original = "cash-available" if name == "cash" else "fixed-available"
        old = next(m for m in prior.members if m.record_id == original).decode_raw_body()
        manifest = deepcopy(old["manifest"])
        manifest.update(model_id="p15-" + name, fixture_id=BUNDLE,
                        bundle_record_id=name, runtime_binding=runtime_claim())
        manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
        manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=SOURCE,
                                                       payload_sha256=source.payload_sha256))
        if name != "cash":
            manifest["model_hash"] = sha256(model_text(name).encode()).hexdigest()
            manifest["prediction_contract"]["calibration_record"] = reference(record,
                next(m for m in record.members if m.record_id == name))
            manifest["validation_report"] = reference(report,
                next(m for m in report.members if m.record_id == name))
            block_ref = reference(schedule, next(m for m in schedule.members if m.record_id == name))
            manifest["provenance"]["evaluation_block"] = block_ref
            manifest["provenance"]["simulated_schedule"]["evaluation_block"] = block_ref
            manifest["data_manifest_hashes"] = [row for row in manifest["data_manifest_hashes"]
                if row["fixture_id"] not in ("p14c2-partition-v1", "p14c2-record-v1", "p14b1b-assembly-v1")]
            manifest["data_manifest_hashes"].extend(dict(role=role, fixture_id=root.fixture_id,
                payload_sha256=root.payload_sha256) for role, root in
                (("calibration", partition), ("calibration", record), ("source", report), ("source", schedule)))
            manifest["feature_binding"]["source_profiles"].extend(dict(fixture_id=SOURCE,
                profile_id=p["profile_id"]) for p in source.decode_modeled_source_profiles()
                if p["profile_id"] in ("session", "account_snapshot", "fee2-account_snapshot",
                                       "missing-fee-account_snapshot", "underlying_quote", "bars")
                or p["profile_id"].startswith(("c99-", "c100-", "p99-", "pdelta-")))
        checked = normalize_bundle_manifest(manifest, event_id="p15-build",
            raw_ref="synthetic://p15-build", received_at=datetime.now(timezone.utc))
        assert checked.value is not None, checked.rejection
        add(payload, name, "model_bundle", dict(schema_version=1, manifest=checked.value.snapshot(),
            model_utf8=old["model_utf8"] if name == "cash" else model_text(name)),
            "model-bundle", "2026-09-01T01:00:00Z")
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def register(name: str, raw: bytes) -> None:
    """Register one newly generated fixture's actual bytes.

    :param    name: New fixture identifier.
    :param    raw: Newly generated canonical bytes.
    :returns: None.
    :raises   OSError: If the fixture or catalog cannot be written.
    """
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name,
        generator_id=("optionslab-bundle-fixture-builder" if name == BUNDLE else
            "optionslab-calibration-inputs-fixture-builder" if name in (PARTITION, RECORD) else
            "optionslab-bundle-validation-fixture-builder" if name == REPORT else
            "optionslab-bundle-schedule-fixture-builder" if name == SCHEDULE else
            "optionslab-decision-fixture-builder"),
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "source"
    name = {"source": SOURCE, "partition": PARTITION, "record": RECORD,
            "report": REPORT, "schedule": SCHEDULE, "bundle": BUNDLE}[stage]
    register(name, build_source() if stage == "source" else build_bundle() if stage == "bundle"
             else build_schedule() if stage == "schedule" else support(stage))
    print(name)
