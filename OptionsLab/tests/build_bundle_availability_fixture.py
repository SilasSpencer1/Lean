"""Build registered current availability cases from actual assembled A2 owners."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from build_calibration_inputs_fixture import admitted, add, base, profile, reference, runtime_claim
from build_fixture import _payload_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest


NAME = "p14b2-availability-bundle-v1"
FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"


def bundle() -> bytes:
    """Emit honest registered fixed and cash modeled schedules and adverse cases.

    :returns: Canonical A2 bundle source bytes with current source role declarations.
    :raises AssertionError: If any source manifest fails actual normalization.
    """
    prior = admitted("p14b1b-assembly-bundle-v1")
    market = admitted("p11-feature-vector-v1")
    schedule_root = admitted("p14b-schedule-v1")
    payload = base(NAME, [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                   "optionslab-bundle-fixture-builder")
    payload["generator_source_ref"] = "OptionsLab/tests/build_bundle_availability_fixture.py"
    scenarios = (("fixed-available", "fixed-validation"),
                 ("cash-available", "cash-validation"),
                 ("fixed-training-only", "fixed-validation"),
                 ("fixed-empty-profiles", "fixed-validation"),
                 ("fixed-no-bars", "fixed-validation"),
                 ("fixed-bad-delay", "fixed-validation"),
                 ("fixed-overflow-delay", "fixed-validation"),
                 ("fixed-actual-unknown", "fixed-validation"),
                 ("fixed-actual-reversed", "fixed-validation"),
                 ("cash-inconsistent", "cash-validation"),
                 ("cash-no-schedule", "cash-validation"),
                 ("cash-unknown-gap", "cash-validation"),
                 ("cash-outside-gap", "cash-validation"))
    for name, source in scenarios:
        old = next(member for member in prior.members if member.record_id == source).decode_raw_body()
        manifest = deepcopy(old["manifest"])
        manifest.update(model_id=name, fixture_id=NAME, bundle_record_id=name,
                        runtime_binding=runtime_claim())
        if not any(row["fixture_id"] == market.fixture_id for row in manifest["data_manifest_hashes"]):
            manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=market.fixture_id,
                                                         payload_sha256=market.payload_sha256))
        if name.startswith("fixed-"):
            binding = manifest["feature_binding"]
            if name == "fixed-empty-profiles":
                binding["source_profiles"] = []
            elif name == "fixed-training-only":
                binding["source_profiles"] = [binding["source_profiles"][0]]
            elif name == "fixed-no-bars":
                binding["source_profiles"] = [row for row in binding["source_profiles"]
                                              if row["profile_id"] != "bars"]
                for profile_id in ("session", "good-instrument_tradability",
                                   "good-provider_contract_mapping", "good-contract_reference"):
                    binding["source_profiles"].append(dict(fixture_id=market.fixture_id,
                                                            profile_id=profile_id))
            else:
                for profile_id in ("session", "good-instrument_tradability",
                                   "good-provider_contract_mapping", "good-contract_reference"):
                    binding["source_profiles"].append(dict(fixture_id=market.fixture_id,
                                                            profile_id=profile_id))
        provenance = manifest["provenance"]
        provenance["built_at"] = datetime.now(timezone.utc).isoformat()
        if name != "cash-inconsistent":
            provenance["simulated_available_at"] = "2026-09-01T01:00:00Z"
        if name.startswith("fixed-") and name != "fixed-bad-delay":
            provenance["simulated_schedule"].update(fit_delay_us=10800000000,
                                                     deployment_delay_us=0)
        if name == "fixed-overflow-delay":
            provenance["simulated_schedule"]["fit_delay_us"] = (
                timedelta.max.days * 86400 + timedelta.max.seconds) * 1000000 + timedelta.max.microseconds
        if name == "fixed-actual-unknown":
            provenance["availability_basis"] = "actual"
        if name == "fixed-actual-reversed":
            provenance.update(availability_basis="actual", promoted_at=provenance["built_at"],
                              activated_at="2026-09-01T01:00:00Z")
        if name == "cash-no-schedule":
            provenance.update(simulated_schedule=None, activation_gap=None, evaluation_block=None)
        if name == "cash-unknown-gap":
            gap = next(member for member in schedule_root.members if member.record_id == "unknown-gap")
            provenance["activation_gap"] = reference(schedule_root, gap)
            provenance["simulated_schedule"]["activation_gap"] = reference(schedule_root, gap)
        if name == "cash-outside-gap":
            provenance["simulated_available_at"] = "2026-09-01T14:00:00Z"
            provenance["simulated_schedule"]["simulated_available_at"] = "2026-09-01T14:00:00Z"
        checked = normalize_bundle_manifest(manifest, event_id="b2-build",
            raw_ref="synthetic://b2-build", received_at=datetime.now(timezone.utc))
        assert checked.value is not None, checked.rejection
        add(payload, name, "model_bundle", dict(schema_version=1,
            manifest=checked.value.snapshot(), model_utf8=old["model_utf8"]),
            "model-bundle", "2026-09-01T01:00:00Z")
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    raw = bundle()
    (FOLDER / (NAME + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != NAME]
    catalog["fixtures"].append(dict(fixture_id=NAME, generator_id="optionslab-bundle-fixture-builder",
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(NAME, hashlib.sha256(raw).hexdigest())
