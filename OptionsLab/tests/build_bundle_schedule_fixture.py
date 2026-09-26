"""Build an immutable schedule source and runtime bound bundle examples."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_calibration_inputs_fixture import admitted, add, base, profile, reference, runtime_claim
from build_fixture import _payload_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.calibration import BUCKET_RULE_ID


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
GENERATOR = "optionslab-bundle-schedule-fixture-builder"
NAMES = ("p14b-schedule-v1", "p14b-schedule-bundle-v1")
AVAILABLE = "2026-09-01T01:00:00Z"


def schedule() -> bytes:
    """Declare source bound September activation gaps and evaluation blocks.

    :returns: Canonical schedule source fixture bytes.
    """
    calendar = admitted("p14c2-calendar-v1")
    descriptor = next(m for m in calendar.members if m.record_id == "calendar")
    record = admitted("p14c2-record-v1")
    record_member = next(m for m in record.members if m.record_id == "record")
    partition_ref = record_member.decode_raw_body()["partition_ref"]
    fixed_bundle = admitted("p14c2-bundle-v1")
    fixed = next(m for m in fixed_bundle.members if m.record_id == "fixed-c2").decode_raw_body()
    manifest = fixed["manifest"]
    binding = manifest["feature_binding"]
    value = base(NAMES[0], (profile("activation_gap", GENERATOR, "gaps"),
                            profile("evaluation_block", GENERATOR, "blocks")), GENERATOR)
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_schedule_fixture.py"
    gap = dict(schema_version=1, gap_id="august-september-2026", calendar_descriptor_ref=reference(calendar, descriptor),
               previous_session="2026-08-31", next_session="2026-09-01", available_at=AVAILABLE)
    add(value, "gap", "activation_gap", gap, "gaps", AVAILABLE)
    holiday = deepcopy(gap)
    holiday.update(gap_id="labor-day-2026", previous_session="2026-09-04", next_session="2026-09-08")
    add(value, "holiday-gap", "activation_gap", holiday, "gaps", AVAILABLE)
    nonadjacent = deepcopy(gap)
    nonadjacent.update(gap_id="nonadjacent", previous_session="2026-08-28")
    add(value, "nonadjacent", "activation_gap", nonadjacent, "gaps", AVAILABLE)
    unknown = deepcopy(gap)
    unknown.update(gap_id="unknown-availability", available_at=None)
    add(value, "unknown-gap", "activation_gap", unknown, "gaps", AVAILABLE)
    early = deepcopy(gap)
    early.update(gap_id="early-close", calendar_descriptor_ref=reference(calendar,
        next(m for m in calendar.members if m.record_id == "early-close")),
        previous_session="2026-08-03", next_session="2026-08-04")
    add(value, "early-close", "activation_gap", early, "gaps", AVAILABLE)
    block = dict(schema_version=1, block_id="september-2026", starts_at="2026-09-01T04:00:00Z",
        ends_at="2026-10-01T04:00:00Z", calendar_descriptor_ref=reference(calendar, descriptor),
        model_hash=manifest["model_hash"], feature_schema_id=binding["feature_schema_id"],
        transform_id=binding["transform_id"], normalization_hash=binding["normalization_hash"],
        calibration_partition_ref=partition_ref, calibration_record_ref=reference(record, record_member),
        frozen_threshold_return=manifest["prediction_contract"]["threshold_return"],
        bucket_rule_id=BUCKET_RULE_ID, available_at=AVAILABLE)
    add(value, "block", "evaluation_block", block, "blocks", AVAILABLE)
    wrong_model = deepcopy(block)
    wrong_model["model_hash"] = "0" * 64
    add(value, "wrong-model", "evaluation_block", wrong_model, "blocks", AVAILABLE)
    wrong_interval = deepcopy(block)
    wrong_interval["ends_at"] = "2026-10-02T04:00:00Z"
    add(value, "wrong-interval", "evaluation_block", wrong_interval, "blocks", AVAILABLE)
    for name, field, replacement in (("wrong-schema", "feature_schema_id", "0" * 64),
                                     ("wrong-normalization", "normalization_hash", "0" * 64),
                                     ("wrong-threshold", "frozen_threshold_return", "0.01"),
                                     ("wrong-bucket-rule", "bucket_rule_id", "0" * 64),
                                     ("wrong-calendar", "calendar_descriptor_ref", reference(calendar,
                                         next(m for m in calendar.members if m.record_id == "early-close"))),
                                     ("wrong-record-ref", "calibration_record_ref",
                                         {**reference(record, record_member), "raw_hash": "0" * 64})):
        adverse = deepcopy(block)
        adverse[field] = replacement
        add(value, name, "evaluation_block", adverse, "blocks", AVAILABLE)
    cash = {**block, "block_id": "cash-september-2026"}
    for field in ("model_hash", "feature_schema_id", "transform_id", "normalization_hash",
                  "calibration_partition_ref", "calibration_record_ref", "frozen_threshold_return", "bucket_rule_id"):
        cash[field] = None
    add(value, "cash-block", "evaluation_block", cash, "blocks", AVAILABLE)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def bundle() -> bytes:
    """Bind actual fixed and cash manifests to the registered schedule source.

    :returns: Canonical runtime bound bundle fixture bytes.
    :raises AssertionError: If a source manifest no longer normalizes.
    """
    schedule_root = admitted(NAMES[0])
    calendar_root = admitted("p14c2-calendar-v1")
    session_root = admitted("p14c2-sources-v1")
    old = admitted("p14c2-bundle-v1")
    fixed = next(m for m in old.members if m.record_id == "fixed-c2").decode_raw_body()
    cash_root = admitted("p14a-bundle-content-v1")
    cash = next(m for m in cash_root.members if m.record_id == "cash").decode_raw_body()
    gap = next(m for m in schedule_root.members if m.record_id == "gap")
    block = next(m for m in schedule_root.members if m.record_id == "block")
    cash_block = next(m for m in schedule_root.members if m.record_id == "cash-block")
    value = base(NAMES[1], [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                 "optionslab-bundle-fixture-builder")
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_schedule_fixture.py"
    for name, source, block_member in (("fixed-schedule", fixed, block), ("cash-schedule", cash, cash_block),
                                        ("direct-gap-mismatch", fixed, block),
                                        ("direct-block-mismatch", fixed, block),
                                        ("wrong-gap-kind", fixed, block),
                                        ("wrong-block-hash", fixed, block)):
        manifest = deepcopy(source["manifest"])
        manifest.update(model_id=name, fixture_id=NAMES[1], bundle_record_id=name, runtime_binding=runtime_claim(),
                        validation_report=None)
        manifest["data_manifest_hashes"].extend(dict(role="source", fixture_id=root.fixture_id,
            payload_sha256=root.payload_sha256) for root in (session_root, calendar_root, schedule_root)
            if (root.fixture_id, root.payload_sha256) not in
            {(row["fixture_id"], row["payload_sha256"]) for row in manifest["data_manifest_hashes"]})
        manifest["provenance"].update(built_at=datetime.now(timezone.utc).isoformat(),
            evaluation_block=reference(schedule_root, block_member),
            activation_gap=reference(schedule_root, gap),
            simulated_schedule=dict(schedule_id="september-2026", version=1,
                fit_cutoff=None if name == "cash-schedule" else manifest["prediction_contract"]["fit_cutoff"],
                fit_delay_us=None if name == "cash-schedule" else 0,
                deployment_delay_us=None if name == "cash-schedule" else 10800000000,
                simulated_available_at=AVAILABLE, evaluation_block=reference(schedule_root, block_member),
                activation_gap=reference(schedule_root, gap)))
        if name == "direct-gap-mismatch":
            manifest["provenance"]["activation_gap"] = reference(schedule_root,
                next(m for m in schedule_root.members if m.record_id == "holiday-gap"))
        if name == "direct-block-mismatch":
            manifest["provenance"]["evaluation_block"] = reference(schedule_root, cash_block)
        if name == "wrong-gap-kind":
            wrong = reference(schedule_root, block)
            manifest["provenance"]["activation_gap"] = wrong
            manifest["provenance"]["simulated_schedule"]["activation_gap"] = wrong
        if name == "wrong-block-hash":
            wrong = {**reference(schedule_root, block), "raw_hash": "0" * 64}
            manifest["provenance"]["evaluation_block"] = wrong
            manifest["provenance"]["simulated_schedule"]["evaluation_block"] = wrong
        checked = normalize_bundle_manifest(manifest, event_id="schedule-build",
            raw_ref="synthetic://schedule-build", received_at=datetime.now(timezone.utc))
        assert checked.value is not None, checked.rejection
        add(value, name, "model_bundle", dict(schema_version=1,
            manifest=checked.value.snapshot(), model_utf8=source["model_utf8"]), "model-bundle", AVAILABLE)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    name = NAMES[stage - 1]
    raw = schedule() if stage == 1 else bundle()
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name, generator_id=GENERATOR if stage == 1
        else "optionslab-bundle-fixture-builder", generator_version="1",
        payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(raw).hexdigest())
