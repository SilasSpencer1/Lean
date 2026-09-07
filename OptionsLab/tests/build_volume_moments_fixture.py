"""Build finite population-moment cases with actual admitted session dependencies."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes


FIXTURE_ID = "p10a-volume-moments-v1"


def build_fixture() -> bytes:
    """Reuse finalized upstream session bytes and construct explicit moment inputs.

    :returns: New fixture bytes with truthful generator version and assembly time.
    """
    source = json.loads((Path(__file__).parent / "fixtures/p10a-volume-training-v1.json").read_bytes())
    original = next(m["raw_body"] for m in source["members"] if m["record_id"] == "training")
    days = original["training_sessions"]
    payload = {**source, "fixture_id": FIXTURE_ID, "generator_version": "2",
               "assembled_at": datetime.now(timezone.utc).isoformat(),
               "generator_source_ref": "OptionsLab/tests/build_volume_moments_fixture.py",
               "members": [], "modeled_source_profiles": [p for p in source["modeled_source_profiles"] if p["profile_id"] in ("volume-calendar", "volume-bars", "volume-partitions")]}
    members = payload["members"]
    by_id = {m["record_id"]: m for m in source["members"]}
    template = by_id["bar-2026-08-06-35"]
    groups = []

    def bar(day, minute, volume, label):
        """Add one unambiguous selected economic observation with exact string volume."""
        record = f"{label}-{day}-{minute}"
        start = datetime.fromisoformat(day + "T13:30:00+00:00") + timedelta(minutes=minute - 1)
        end = start + timedelta(minutes=1)
        metadata = {**template["envelope"]["metadata"], "provider_record_id": record,
                    "available_at": end.isoformat(), "interval_start": start.isoformat(),
                    "interval_end": end.isoformat(), "availability_evidence_ref": "synthetic://moments/" + record}
        envelope = {**template["envelope"], "event_id": "event-" + record, "raw_ref": "synthetic://moments/" + record,
                    "simulated_received_at": end.isoformat(), "metadata": metadata}
        body = {**template["raw_body"], "volume": volume}
        members.append(_member(record, "underlying_bar", "volume-bars", body, envelope))
        return record

    for index, day in enumerate(days):
        session_id = "session-" + day
        members.append(deepcopy(by_id[session_id]))
        cases = [(1, "0" if index < 10 else "2"), (5, "0"),
                 (6, "1" + "0" * 90 if index < 10 else "1" + "0" * 89 + "2"),
                 (7, "1000" if index < 10 else "1000." + "0" * 99 + "2"),
                 (8, "1.000" if index < 10 else "3.000")]
        if index < 19:
            cases.append((2, str(index)))
        if index < 3:
            cases.append((3, ("0", "1", "3")[index]))
        if index == 0:
            cases.append((4, "7"))
        groups.append(dict(session_record_id=session_id,
                           bar_record_ids=[bar(day, minute, value, "numeric") for minute, value in cases]))

    def partition(name, selected):
        """Declare complete selected inputs without an enclosing-payload self-reference."""
        body = {**original, "partition_id": name, "training_inputs": deepcopy(selected)}
        envelope = dict(event_id="event-" + name, raw_ref="synthetic://moments/" + name,
                        simulated_received_at="2026-09-03T00:00:00Z", stream_id="volume-partitions",
                        receive_sequence=None, supersedes_record_id=None, contract=None, metadata=None)
        members.append(_member(name, "volume_partition", "volume-partitions", body, envelope))

    partition("numeric", groups)
    for name, values in (
        ("product-bound", ("1" + "0" * 600,)),
        ("sum-bound", ("1" + "0" * 499, "0." + "0" * 499 + "1")),
        ("variance-bound", ("0." + "0" * 499 + "1", "0")),
    ):
        selected = deepcopy(groups)
        for index, value in enumerate(values):
            selected[index]["bar_record_ids"].append(bar(days[index], 9, value, name))
        partition(name, selected)
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    target.write_bytes(build_fixture())
    print(target)
