"""Build finite actual-session P14D activation fixtures in dependency order."""

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from build_calibration_inputs_fixture import admitted, add, base, profile, reference, runtime_claim, GENERATOR
from build_fixture import _payload_bytes
from options_lab.bundle_manifest_inputs import normalize_bundle_manifest


FOLDER = Path(__file__).parent / "fixtures"
CATALOG = Path(__file__).parent.parent / "src/options_lab/_fixture_catalog.json"
NAMES = ("p14d-aug-context-v1", "p14d-sep-context-v1", "p14d-oct-sources-v1",
         "p14d-oct-calendar-v1", "p14d-sep-calendar-v1", "p14d-schedule-v1", "p14d-bundle-v1")
SCHEDULE_GENERATOR = "optionslab-bundle-schedule-fixture-builder"
APRIL = "2026-04-30T00:00:00Z"
SEPTEMBER = "2026-09-01T01:00:00Z"
CLOSE = "2026-08-31T20:00:00Z"
OCTOBER = "2026-10-01T01:00:00Z"
OCT_OPEN_DAYS = (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)


def context_source(name, day):
    """Emit one session stream with complete actual source definitions."""
    original = admitted("p14c2-sources-v1")
    record = "session-" + day
    body = deepcopy(next(m for m in original.members if m.record_id == record).decode_raw_body())
    value = base(name, [profile("exchange_session", "fixture-calendar-c2", "gap-session")])
    value["definitions"] = json.loads((FOLDER / "p11-feature-vector-v1.json").read_bytes())["definitions"]
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_activation_fixture.py"
    add(value, record, "exchange_session", body, "gap-session", APRIL)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def october_sources():
    """Emit explicit October XNYS open sessions, without weekday inference."""
    value = base(NAMES[2], [profile("exchange_session", "fixture-calendar-d", "oct-sessions")])
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_activation_fixture.py"
    for day in OCT_OPEN_DAYS:
        text = f"2026-10-{day:02d}"
        body = dict(calendar="XNYS", session_date=text, kind="regular",
            opens_at=text + "T13:30:00Z", closes_at=text + "T20:00:00Z",
            source="fixture-calendar-d", provider_record_id="oct-" + text,
            source_version="1", available_at=APRIL, availability_basis="measured", fidelity="genuine")
        add(value, "session-" + text, "exchange_session", body, "oct-sessions", APRIL)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def october_calendar():
    """Cover every September 30 through October 31 date with explicit facts."""
    c2, october = admitted("p14c2-sources-v1"), admitted(NAMES[2])
    value = base(NAMES[3], [profile("calendar_descriptor", GENERATOR, "calendar-d")])
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_activation_fixture.py"
    old = admitted("p14c2-calendar-v1")
    body = deepcopy(next(m for m in old.members if m.record_id == "calendar").decode_raw_body())
    body.update(calendar_id="xnys-sep30-oct2026-d", coverage_start_date="2026-09-30",
        coverage_end_date="2026-10-31", available_at=APRIL,
        session_refs=[reference(c2, next(m for m in c2.members
                     if m.record_id == "session-2026-09-30")),
                      *(reference(october, member) for member in october.members)],
        closed_dates=[f"2026-10-{day:02d}" for day in range(1, 32) if day not in OCT_OPEN_DAYS])
    add(value, "calendar", "calendar_descriptor", body, "calendar-d", APRIL)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def september_calendar():
    """Rename the descriptor owner while retaining identical XNYS date facts."""
    old = admitted("p14c2-calendar-v1")
    body = deepcopy(next(m for m in old.members if m.record_id == "calendar").decode_raw_body())
    body["calendar_id"] = "same-xnys-september-renamed-d"
    value = base(NAMES[4], [profile("calendar_descriptor", GENERATOR, "calendar-d")])
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_activation_fixture.py"
    add(value, "calendar", "calendar_descriptor", body, "calendar-d", APRIL)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def schedule():
    """Bind renamed September and adjacent October cash blocks to real calendars."""
    old = admitted("p14b-schedule-v1")
    descriptor = admitted(NAMES[3])
    renamed_calendar = admitted(NAMES[4])
    october_calendar_member = next(m for m in descriptor.members if m.record_id == "calendar")
    renamed_calendar_member = next(m for m in renamed_calendar.members if m.record_id == "calendar")
    sep = deepcopy(next(m for m in old.members if m.record_id == "cash-block").decode_raw_body())
    value = base(NAMES[5], [profile("activation_gap", SCHEDULE_GENERATOR, "gaps"),
                            profile("evaluation_block", SCHEDULE_GENERATOR, "blocks")], SCHEDULE_GENERATOR)
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_activation_fixture.py"
    sep["block_id"] = "renamed-september-2026"
    add(value, "renamed-september", "evaluation_block", sep, "blocks", SEPTEMBER)
    close_block = deepcopy(sep)
    close_block.update(block_id="september-at-close-2026", available_at=CLOSE)
    add(value, "close-block", "evaluation_block", close_block, "blocks", CLOSE)
    close_gap = deepcopy(next(m for m in old.members if m.record_id == "gap").decode_raw_body())
    close_gap.update(gap_id="august-september-at-close-2026", available_at=CLOSE)
    add(value, "close-gap", "activation_gap", close_gap, "gaps", CLOSE)
    renamed = deepcopy(sep)
    renamed.update(block_id="renamed-calendar-september-2026",
                   calendar_descriptor_ref=reference(renamed_calendar, renamed_calendar_member))
    add(value, "renamed-calendar-september", "evaluation_block", renamed, "blocks", SEPTEMBER)
    sep_gap = deepcopy(next(m for m in old.members if m.record_id == "gap").decode_raw_body())
    sep_gap.update(gap_id="renamed-calendar-august-september-2026",
                   calendar_descriptor_ref=reference(renamed_calendar, renamed_calendar_member))
    add(value, "renamed-calendar-gap", "activation_gap", sep_gap, "gaps", SEPTEMBER)
    gap = deepcopy(next(m for m in old.members if m.record_id == "gap").decode_raw_body())
    gap.update(gap_id="september-october-2026", calendar_descriptor_ref=reference(descriptor, october_calendar_member),
               previous_session="2026-09-30", next_session="2026-10-01", available_at=OCTOBER)
    add(value, "october-gap", "activation_gap", gap, "gaps", OCTOBER)
    block = deepcopy(sep)
    block.update(block_id="october-2026", starts_at="2026-10-01T04:00:00Z",
                 ends_at="2026-11-01T04:00:00Z",
                 calendar_descriptor_ref=reference(descriptor, october_calendar_member), available_at=OCTOBER)
    add(value, "october-block", "evaluation_block", block, "blocks", OCTOBER)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


def bundle():
    """Register renamed same-interval and honestly new-interval cash candidates."""
    prior, schedule_root = admitted("p14b2-availability-bundle-v1"), admitted(NAMES[5])
    old_schedule = admitted("p14b-schedule-v1")
    old = next(m for m in prior.members if m.record_id == "cash-available").decode_raw_body()
    value = base(NAMES[6], [profile("model_bundle", "optionslab-bundle-fixture-builder", "model-bundle")],
                 "optionslab-bundle-fixture-builder")
    value["generator_source_ref"] = "OptionsLab/tests/build_bundle_activation_fixture.py"
    for name, source_names, block_name, gap_root, gap_name, available_at in (
        ("cash-renamed", (NAMES[0],), "renamed-september", old_schedule, "gap", SEPTEMBER),
        ("cash-at-close", (NAMES[0],), "close-block", schedule_root, "close-gap", CLOSE),
        ("cash-later-gap", (), "renamed-september", old_schedule, "holiday-gap",
         "2026-09-04T21:00:00Z"),
        ("cash-calendar-renamed", (NAMES[0], NAMES[4]), "renamed-calendar-september",
         schedule_root, "renamed-calendar-gap", SEPTEMBER),
        ("cash-october", (NAMES[1], NAMES[2], NAMES[3]), "october-block", schedule_root,
         "october-gap", OCTOBER)):
        manifest = deepcopy(old["manifest"])
        manifest.update(model_id=name, fixture_id=NAMES[6], bundle_record_id=name,
                        runtime_binding=runtime_claim())
        for root_name in (*source_names, NAMES[5]):
            root = admitted(root_name)
            manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=root.fixture_id,
                                                         payload_sha256=root.payload_sha256))
        block = next(m for m in schedule_root.members if m.record_id == block_name)
        gap = next(m for m in gap_root.members if m.record_id == gap_name)
        block_ref, gap_ref = reference(schedule_root, block), reference(gap_root, gap)
        provenance = manifest["provenance"]
        provenance.update(built_at=datetime.now(timezone.utc).isoformat(),
            evaluation_block=block_ref, activation_gap=gap_ref,
            simulated_available_at=available_at)
        provenance["simulated_schedule"].update(schedule_id=name,
            evaluation_block=block_ref, activation_gap=gap_ref,
            simulated_available_at=available_at)
        checked = normalize_bundle_manifest(manifest, event_id="d-build",
            raw_ref="synthetic://d-build", received_at=datetime.now(timezone.utc))
        assert checked.value is not None, checked.rejection
        add(value, name, "model_bundle", dict(schema_version=1,
            manifest=checked.value.snapshot(), model_utf8=old["model_utf8"]),
            "model-bundle", available_at)
    fixed = next(m for m in prior.members if m.record_id == "fixed-available").decode_raw_body()
    manifest = deepcopy(fixed["manifest"])
    manifest.update(model_id="fixed-september", fixture_id=NAMES[6],
                    bundle_record_id="fixed-september", runtime_binding=runtime_claim())
    source = admitted(NAMES[0])
    manifest["data_manifest_hashes"].append(dict(role="source", fixture_id=source.fixture_id,
                                                 payload_sha256=source.payload_sha256))
    manifest["feature_binding"]["source_profiles"].append(dict(
        fixture_id=source.fixture_id, profile_id="gap-session"))
    manifest["provenance"]["built_at"] = datetime.now(timezone.utc).isoformat()
    checked = normalize_bundle_manifest(manifest, event_id="d-build",
        raw_ref="synthetic://d-build", received_at=datetime.now(timezone.utc))
    assert checked.value is not None, checked.rejection
    add(value, "fixed-september", "model_bundle", dict(schema_version=1,
        manifest=checked.value.snapshot(), model_utf8=fixed["model_utf8"]),
        "model-bundle", SEPTEMBER)
    value["assembled_at"] = datetime.now(timezone.utc).isoformat()
    return _payload_bytes(value)


if __name__ == "__main__":
    stage = int(sys.argv[1])
    name = NAMES[stage - 1]
    raw = (lambda: context_source(NAMES[0], "2026-08-31"),
           lambda: context_source(NAMES[1], "2026-09-30"), october_sources,
           october_calendar, september_calendar, schedule, bundle)[stage - 1]()
    (FOLDER / (name + ".json")).write_bytes(raw)
    catalog = json.loads(CATALOG.read_bytes())
    catalog["fixtures"] = [row for row in catalog["fixtures"] if row["fixture_id"] != name]
    catalog["fixtures"].append(dict(fixture_id=name,
        generator_id=SCHEDULE_GENERATOR if stage == 6 else
            "optionslab-bundle-fixture-builder" if stage == 7 else GENERATOR,
        generator_version="1", payload_schema_version=1, normalization_version=1,
        expected_payload_sha256=hashlib.sha256(raw).hexdigest()))
    CATALOG.write_bytes(_payload_bytes(catalog))
    print(name, hashlib.sha256(raw).hexdigest())
