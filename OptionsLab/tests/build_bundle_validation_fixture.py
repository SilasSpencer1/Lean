"""Build real finite assembly reports and bundles from admitted source owners."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_bundle_schedule_fixture import AVAILABLE
from build_calibration_inputs_fixture import add, admitted, base, profile, reference, runtime_claim
from build_fixture import _payload_bytes
from test_bundle_schedule import bundle_result
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.bundle_validation import BUNDLE_ASSEMBLY_METHOD_ID, _checked_inputs


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
SOURCE = "p14b1b-assembly-v1"
BUNDLE = "p14b1b-assembly-bundle-v1"
GENERATOR = "optionslab-bundle-validation-fixture-builder"


def _report(name, bundle):
    """Run the real method before taking a report performance timestamp.

    :param name: Source member identifier.
    :param bundle: Actually verified prior bundle and its inspected owners.
    :returns: Complete primitive report and actual UTC performance time.
    """
    fields, _ = _checked_inputs(bundle.model, bundle.spec, bundle.supplied_normalization,
        bundle.calibration_partition, bundle.calibration_metadata, bundle.config, bundle.runtime,
        bundle.upstream_fixtures)
    for field in ("calibration_partition_ref", "calibration_record_ref"):
        if fields[field] is not None:
            fields[field] = fields[field].snapshot()
    performed = datetime.now(timezone.utc)
    return dict(schema_version=1, validation_id=name, method_id=BUNDLE_ASSEMBLY_METHOD_ID,
                performed_at=performed.isoformat(), **fields), performed


def source() -> bytes:
    """Emit actual checked cash and fixed reports plus bounded adverse members.

    :returns: Canonical report source bytes assembled after real check timestamps.
    :raises AssertionError: If the prior verified owners cannot be inspected.
    """
    cash = bundle_result("cash-schedule").value
    fixed = bundle_result("fixed-schedule").value
    assert cash is not None and fixed is not None
    value = base(SOURCE, [profile("bundle_validation", GENERATOR, "reports")], GENERATOR)
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_validation_fixture.py"
    cash_body, cash_at = _report("cash-validation", cash)
    fixed_body, fixed_at = _report("fixed-validation", fixed)
    for name, body, at in (("cash-validation", cash_body, cash_at),
                           ("fixed-validation", fixed_body, fixed_at)):
        add(value, name, "bundle_validation", body, "reports", at.isoformat())
    for name, field, replacement in (("bad-method", "method_id", "0" * 64),
                                     ("bad-binding", "execution_policy_hash", "0" * 64),
                                     ("bad-model", "model_hash", "0" * 64),
                                     ("late-validation", "performed_at",
                                      (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())):
        body = deepcopy(cash_body)
        body["validation_id"] = name
        body[field] = replacement
        add(value, name, "bundle_validation", body, "reports", cash_at.isoformat())
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def bundle() -> bytes:
    """Reference the admitted report source from later actual bundle assembly.

    :returns: Canonical runtime-bound bundle fixture bytes.
    :raises AssertionError: If report or prior bundle sources are unavailable.
    """
    reports = admitted(SOURCE)
    prior = admitted("p14b-schedule-bundle-v1")
    payload = base(BUNDLE, [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                   "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_bundle_validation_fixture.py"
    for name, old_name, report_name in (("cash-validation", "cash-schedule", "cash-validation"),
        ("fixed-validation", "fixed-schedule", "fixed-validation"),
        ("bad-method", "cash-schedule", "bad-method"),
        ("bad-binding", "cash-schedule", "bad-binding"),
        ("bad-model", "cash-schedule", "bad-model"),
        ("late-validation", "cash-schedule", "late-validation"),
        ("bad-ref", "cash-schedule", "cash-validation"),
        ("missing-owner", "cash-schedule", "cash-validation")):
        prior_body = next(m for m in prior.members if m.record_id == old_name).decode_raw_body()
        manifest = deepcopy(prior_body["manifest"])
        report_member = next(m for m in reports.members if m.record_id == report_name)
        report_ref = reference(reports, report_member)
        if name == "bad-ref":
            report_ref["raw_hash"] = "0" * 64
        manifest.update(model_id=name, fixture_id=BUNDLE, bundle_record_id=name,
                        runtime_binding=runtime_claim(), validation_report=report_ref)
        if name != "missing-owner":
            manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=reports.fixture_id,
                                                        payload_sha256=reports.payload_sha256))
        manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
        checked = normalize_bundle_manifest(manifest, event_id="assembly-build",
            raw_ref="synthetic://assembly-build", received_at=datetime.now(timezone.utc))
        assert checked.value is not None, checked.rejection
        add(payload, name, "model_bundle", dict(schema_version=1,
            manifest=checked.value.snapshot(), model_utf8=prior_body["model_utf8"]), "model-bundle", AVAILABLE)
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    name = SOURCE if stage == 1 else BUNDLE
    raw = source() if stage == 1 else bundle()
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name,
        generator_id=GENERATOR if stage == 1 else "optionslab-bundle-fixture-builder",
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(raw).hexdigest())
