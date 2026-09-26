"""Assemble five staged synthetic calibration-input fixtures from actual owners."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_fixture import _member, _metadata, _payload_bytes
from options_lab.admission import verify_fixture_bundle
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest
from options_lab.volume_inputs import normalize_volume_partition, _declared_inputs
from options_lab.volume import fit_volume_baseline
from options_lab.volume_normalization import normalize_feature_normalization


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
GENERATOR = "optionslab-calibration-inputs-fixture-builder"
NAMES = (
    "p14c-volume-training-v1", "p14c-volume-normalization-v1", "p14c-fit-sources-v1",
    "p14c-fit-membership-v1", "p14c-membership-bundle-v1",
)
APRIL = ("01", "02", "06", "07", "08", "09", "10", "13", "14", "15", "16", "17", "20", "21",
         "22", "23", "24", "27", "28", "29")
FIT_START, FIT_END = date(2026, 5, 4), date(2026, 9, 30)
HOLIDAYS = {date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7)}
SIMULATED = "2026-09-30T22:00:00Z"


def profile(kind, source, stream, *, units=None):
    """Build the exact P08 profile for a staged modeled source.

    :param kind: Admitted member kind.
    :param source: Explicit modeled source identifier.
    :param stream: Explicit stream and profile identifier.
    :param units: Optional existing measured unit dictionary.
    :returns: One exact profile dictionary.
    """
    calendar = kind == "exchange_session"
    bar = kind == "underlying_bar"
    return dict(profile_id=stream, kind=kind, source=source, stream_id=stream,
        feed_class="realtime" if bar else None, fidelity="genuine" if calendar or bar else None,
        availability_basis="measured", units={} if units is None else units,
        record_identity_rule="provider_record_id_and_revision_id" if bar else
        "new_provider_record_id_per_update" if calendar else "new_event_id_per_update")


def base(name, profiles, generator=GENERATOR):
    """Use unchanged Greek definitions and truthful current assembly time.

    :param name: Catalog fixture identifier.
    :param profiles: Complete modeled profile list.
    :param generator: Actual existing or C owner producer identity.
    :returns: Empty staged fixture payload.
    """
    definitions = json.loads((FOLDER / "p08a-greek-ready-v1.json").read_bytes())["definitions"]
    return dict(schema_version=1, normalization_version=1, fixture_id=name, generator_id=generator,
        generator_version="1", assembled_at=datetime.now(timezone.utc).isoformat(),
        generator_source_ref="OptionsLab/tests/build_calibration_inputs_fixture.py", origin="synthetic",
        permitted_use="core_fixture", modeled_source_profiles=profiles, definitions=definitions, members=[])


def add(payload, record, kind, body, stream, at, *, metadata=None):
    """Append one canonical body with its separate simulated receipt.

    :param payload: Mutable builder-local fixture dictionary.
    :param record: Unique member ID.
    :param kind: Actual P08 member kind.
    :param body: Explicit JSON body.
    :param stream: Actual profile and stream identifier.
    :param at: Modeled historical receipt timestamp.
    :param metadata: Bar metadata, if this is a bar.
    :returns: Added six-field member dictionary.
    """
    envelope = dict(event_id="event-" + record, raw_ref="synthetic://calibration/" + record,
        simulated_received_at=at, stream_id=stream, receive_sequence=None,
        supersedes_record_id=None, contract=None, metadata=metadata)
    row = _member(record, kind, stream, body, envelope)
    payload["members"].append(row)
    return row


def admitted(name):
    """Read one catalog admitted upstream in this fresh builder process.

    :param name: Registered fixture identifier.
    :returns: Actual verified manifest.
    :raises AssertionError: If its current bytes or descriptor disagree.
    """
    result = verify_fixture_bundle(name, (FOLDER / (name + ".json")).read_bytes(),
        event_id="calibration-build", raw_ref="synthetic://calibration-build/" + name,
        received_at=datetime.now(timezone.utc))
    assert result.value is not None, result.rejection
    return result.value


def reference(root, member):
    """Project four actual external reference facts.

    :param root: Actual admitted containing source.
    :param member: Actual admitted member.
    :returns: Exact external reference dictionary.
    """
    return dict(fixture_id=root.fixture_id, payload_sha256=root.payload_sha256,
        record_id=member.record_id, raw_hash=member.raw_hash)


def april_training():
    """Build explicit 20-session April one-minute volume training.

    :returns: Complete stage-one payload.
    """
    name = NAMES[0]
    bar_units = dict(price="USD_per_share", volume="shares", vwap_numerator="USD", vwap_denominator="shares")
    payload = base(name, [profile("exchange_session", "fixture-calendar", "volume-calendar"),
                          profile("underlying_bar", "fixture-volume-bars", "volume-bars", units=bar_units),
                          profile("volume_partition", "optionslab-volume-training-fixture-builder", "volume-partitions")],
                   "optionslab-volume-training-fixture-builder")
    groups = []
    for index, suffix in enumerate(APRIL):
        day = "2026-04-" + suffix
        session_id, bar_id = "session-" + day, "bar-" + day + "-35"
        add(payload, session_id, "exchange_session", dict(calendar="XNYS", session_date=day, kind="regular",
            opens_at=day + "T13:30:00Z", closes_at=day + "T20:00:00Z", source="fixture-calendar",
            provider_record_id=session_id, source_version="1", available_at=day + "T12:00:00Z",
            availability_basis="measured", fidelity="genuine"), "volume-calendar", day + "T12:00:00Z")
        metadata = _metadata("fixture-volume-bars", bar_id)
        metadata.update(kind="interval", event_at=None, available_at=day + "T14:05:00Z",
            interval_start=day + "T14:04:00Z", interval_end=day + "T14:05:00Z")
        add(payload, bar_id, "underlying_bar", dict(symbol="SPY", close_price=None,
            volume="900" if index < 10 else "1100", vwap_numerator=None, vwap_denominator=None,
            price_basis="raw", volume_definition_id="synthetic-volume-v1", vwap_definition_id=None,
            revision_id="r1", supersedes_revision_id=None), "volume-bars", day + "T14:05:00Z", metadata=metadata)
        groups.append(dict(session_record_id=session_id, bar_record_ids=[bar_id]))
    add(payload, "training", "volume_partition", dict(schema_version=1, partition_id="training",
        training_sessions=["2026-04-" + day for day in APRIL], validation_sessions=[], test_sessions=[],
        volume_definition_id="synthetic-volume-v1", training_inputs=groups),
        "volume-partitions", "2026-04-30T00:00:00Z")
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def april_normalization():
    """Fit the actual P10 owners against admitted April training bytes.

    :returns: Complete stage-two payload.
    :raises AssertionError: If P10 rejects the staged training.
    """
    training = admitted(NAMES[0])
    partition = normalize_volume_partition(next(m for m in training.members if m.record_id == "training").decode_raw_body(),
        manifest=training, record_id="training").value
    assert partition is not None
    cutoff = datetime(2026, 4, 30, tzinfo=timezone.utc)
    groups, _ = _declared_inputs(partition, training, cutoff)
    fit = fit_volume_baseline(groups, cutoff, partition=partition, manifest=training)
    assert fit.baseline is not None, fit.reasons
    available = "2026-04-30T01:00:00Z"
    raw = dict(schema_version=1, training_fixture_id=training.fixture_id,
        training_payload_sha256=training.payload_sha256, partition_record_id="training",
        cutoff=cutoff.isoformat(), baseline_snapshot=fit.baseline.snapshot,
        baseline_content_hash=fit.baseline.content_hash, available_at=available, availability_basis="measured")
    payload = base(NAMES[1], [profile("feature_normalization", "optionslab-volume-normalization-fixture-builder", "volume-normalization", units={"volume": "shares"})],
                   "optionslab-volume-normalization-fixture-builder")
    add(payload, "good", "feature_normalization", raw, "volume-normalization", available)
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def fit_sources():
    """Record every modeled covered date and sparse independent fit samples.

    :returns: Complete stage-three payload with explicit open and closed dates.
    """
    payload = base(NAMES[2], [profile("exchange_session", "fixture-calendar", "fit-calendar"),
        profile("fit_sample", GENERATOR, "fit-samples")])
    day = FIT_START
    while day <= FIT_END:
        if day.weekday() < 5 and day not in HOLIDAYS:
            text = day.isoformat()
            session_id = "session-" + text
            closes = "17:00:00Z" if text == "2026-07-02" else "20:00:00Z"
            add(payload, session_id, "exchange_session", dict(calendar="XNYS", session_date=text,
                kind="early_close" if text == "2026-07-02" else "regular",
                opens_at=text + "T13:30:00Z", closes_at=text + "T" + closes,
                source="fixture-calendar", provider_record_id=session_id, source_version="1",
                available_at=text + "T12:00:00Z", availability_basis="measured", fidelity="genuine"),
                "fit-calendar", text + "T12:00:00Z")
        day += timedelta(days=1)
    for name, day, right, bucket, status, label, available in (
        ("model-call", "2026-05-04", "call", "call", "observed_fill", "2026-05-05T00:00:00Z", "2026-05-05T01:00:00Z"),
        ("model-put", "2026-05-05", "put", "put", "observed_no_fill", None, "2026-05-06T01:00:00Z"),
        ("tuning-call", "2026-06-01", "call", "call", "censored", None, None),
        ("tuning-put", "2026-07-01", "put", "put", "invalid", "2026-06-30T00:00:00Z", "2026-07-02T01:00:00Z"),
        ("wrong-bucket", "2026-05-06", "call", "put", "observed_fill", None, None),
    ):
        contract = dict(underlying="SPY", expiry="2026-09-18", right=right, strike="650",
            multiplier=100, deliverable_id="standard-spy-100")
        body = dict(schema_version=1, sample_id=name, decision_id="decision-" + name, contract=contract,
            session_date=day, feature_available_at=day + "T13:45:00Z",
            information_start=day + "T13:40:00Z", information_end=day + "T13:46:00Z",
            label_available_at=label, available_at=available, outcome_status=status, bucket_id=bucket)
        add(payload, name, "fit_sample", body, "fit-samples", day + "T14:10:00Z")
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def fit_membership():
    """Reference finalized source rows in one calendar and two fit roles.

    :returns: Complete stage-four payload with registered adverse variants.
    """
    source = admitted(NAMES[2])
    payload = base(NAMES[3], [profile("calendar_descriptor", GENERATOR, "calendar-descriptors"),
        profile("model_membership", GENERATOR, "model-memberships"),
        profile("tuning_membership", GENERATOR, "tuning-memberships")])
    sessions = [m for m in source.members if m.kind == "exchange_session"]
    session_refs = [reference(source, member) for member in sessions]
    opened = {member.decode_raw_body()["session_date"] for member in sessions}
    closed = []
    day = FIT_START
    while day <= FIT_END:
        if day.isoformat() not in opened:
            closed.append(day.isoformat())
        day += timedelta(days=1)
    calendar = dict(schema_version=1, calendar_id="xnys-may-september-2026", calendar="XNYS",
        coverage_start_date=FIT_START.isoformat(), coverage_end_date=FIT_END.isoformat(),
        session_refs=session_refs, closed_dates=closed, available_at=None)
    add(payload, "calendar", "calendar_descriptor", calendar, "calendar-descriptors", SIMULATED)
    incomplete = deepcopy(calendar)
    incomplete["closed_dates"].pop()
    add(payload, "calendar-incomplete", "calendar_descriptor", incomplete, "calendar-descriptors", SIMULATED)
    for role, ids in (("model", ("model-call", "model-put")), ("tuning", ("tuning-call", "tuning-put"))):
        refs = [reference(source, next(m for m in source.members if m.record_id == record)) for record in ids]
        body = dict(schema_version=1, membership_id=role + "-sparse", sample_refs=refs)
        add(payload, role, role + "_membership", body, role + "-memberships", SIMULATED)
        duplicate = deepcopy(body)
        duplicate["sample_refs"].append(refs[0])
        add(payload, role + "-duplicate", role + "_membership", duplicate, role + "-memberships", SIMULATED)
        wrong = deepcopy(body)
        wrong["sample_refs"][0]["raw_hash"] = "0" * 64
        add(payload, role + "-wrong-hash", role + "_membership", wrong, role + "-memberships", SIMULATED)
    bad_bucket = dict(schema_version=1, membership_id="bad-bucket", sample_refs=[reference(source,
        next(m for m in source.members if m.record_id == "wrong-bucket"))])
    add(payload, "model-bad-bucket", "model_membership", bad_bucket, "model-memberships", SIMULATED)
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


def membership_bundle():
    """Bind current runtime fixed bytes to April P10 and direct C memberships.

    :returns: Complete stage-five model bundle payload.
    :raises AssertionError: If actual P10 or manifest inspection fails.
    """
    from build_bundle_fixture import build_fixture as old_bundle
    template = json.loads(old_bundle())
    fixed = next(m for m in template["members"] if m["record_id"] == "fixed")
    manifest = deepcopy(fixed["raw_body"]["manifest"])
    training, artifact, sources, membership, profile_source = (admitted(name) for name in
        (NAMES[0], NAMES[1], NAMES[2], NAMES[3], "p11-feature-vector-v1"))
    normalized = normalize_feature_normalization(next(m for m in artifact.members if m.record_id == "good").decode_raw_body(),
        manifest=artifact, record_id="good", training_manifest=training,
        decision_at=datetime(2026, 4, 30, 1, tzinfo=timezone.utc))
    assert normalized.value is not None, normalized.rejection
    norm = normalized.value
    manifest.update(fixture_id=NAMES[4], bundle_record_id="fixed-membership", model_id="fixed-membership")
    manifest["feature_binding"].update(normalization_hash=norm.content_hash,
        volume_baseline_hash=norm.baseline.content_hash)
    manifest["data_manifest_hashes"] = [dict(role=role, fixture_id=root.fixture_id, payload_sha256=root.payload_sha256)
        for role, root in (("training", training), ("normalization", artifact), ("source", profile_source),
                           ("training", membership), ("training", sources), ("tuning", membership), ("tuning", sources))]
    model_ref = reference(membership, next(m for m in membership.members if m.record_id == "model"))
    tuning_ref = reference(membership, next(m for m in membership.members if m.record_id == "tuning"))
    manifest["prediction_contract"].update(model_membership=model_ref, tuning_membership=tuning_ref)
    manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
    manifest["provenance"]["simulated_available_at"] = SIMULATED
    checked = normalize_bundle_manifest(manifest, event_id="calibration-bundle-build",
        raw_ref="synthetic://calibration-bundle-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    payload = base(NAMES[4], [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                   "optionslab-bundle-fixture-builder")
    add(payload, "fixed-membership", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=fixed["raw_body"]["model_utf8"]), "model-bundle", SIMULATED)
    payload["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(payload)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    payload = (april_training, april_normalization, fit_sources, fit_membership, membership_bundle)[stage - 1]()
    name = NAMES[stage - 1]
    (FOLDER / (name + ".json")).write_bytes(payload)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name, generator_id=("optionslab-volume-training-fixture-builder",
        "optionslab-volume-normalization-fixture-builder", GENERATOR, GENERATOR, "optionslab-bundle-fixture-builder")[stage - 1],
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(payload).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(payload).hexdigest())
