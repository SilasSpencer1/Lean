"""Build small registered raw-volume compound cases using actual upstream calendars."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json

from build_fixture import _member, _payload_bytes


FIXTURE_ID = "p10b-volume-omissions-v1"


def build_fixture() -> bytes:
    """Generate explicit one-day selections with truthful current assembly provenance.

    :returns: Complete new bytes; no earlier source payload is rewritten.
    """
    source = json.loads((Path(__file__).parent / "fixtures/p10a-volume-training-v1.json").read_bytes())
    by_id = {m["record_id"]: m for m in source["members"]}
    payload = {**source, "fixture_id": FIXTURE_ID, "generator_version": "3",
               "assembled_at": datetime.now(timezone.utc).isoformat(),
               "generator_source_ref": "OptionsLab/tests/build_volume_omissions_fixture.py",
               "members": [], "modeled_source_profiles": [p for p in source["modeled_source_profiles"] if p["profile_id"] in ("volume-calendar", "volume-bars", "volume-partitions")]}
    members = payload["members"]
    session = deepcopy(by_id["session-2026-08-06"])
    members.append(session)
    template = by_id["bar-2026-08-06-35"]
    partition_template = by_id["training"]

    def bar(name, body=None, metadata=None):
        """Retain intentional raw failure plus independently changed non-volume facts."""
        env = deepcopy(template["envelope"])
        env.update(event_id="event-" + name, raw_ref="synthetic://omissions/" + name)
        env["metadata"].update(provider_record_id=name, **(metadata or {}))
        raw = {**template["raw_body"], "volume": True, **(body or {})}
        if name == "missing-volume":
            del raw["volume"]
        members.append(_member(name, "underlying_bar", "volume-bars", raw, env))
        return name

    def partition(name, bars, *, calendar="session-2026-08-06", heldout=False):
        """Bind explicit actual members without an enclosing-root self-reference."""
        raw = {**partition_template["raw_body"], "partition_id": name,
               "training_sessions": ["2026-08-05" if heldout else "2026-08-06"],
               "training_inputs": [dict(session_record_id=calendar, bar_record_ids=bars)]}
        env = {**partition_template["envelope"], "event_id": "event-" + name,
               "raw_ref": "synthetic://omissions/" + name}
        members.append(_member(name, "volume_partition", "volume-partitions", raw, env))

    cases = [
        ("boolean", {}, {}), ("negative", {"volume": "-1"}, {}),
        ("nonfinite", {"volume": "NaN"}, {}), ("missing-volume", {}, {}),
        ("hidden-revision", {"revision_id": True}, {}),
        ("hidden-definition", {"volume_definition_id": True}, {}),
        ("hidden-vwap", {"vwap_numerator": "NaN"}, {}),
        ("huge-close", {"close_price": "1" + "0" * 1001}, {}),
        ("unknown-definition", {"volume_definition_id": None}, {}),
        ("wrong-definition", {"volume_definition_id": "other"}, {}),
        ("self-revision", {"supersedes_revision_id": template["raw_body"]["revision_id"]}, {}),
        ("adjusted", {"price_basis": "split_adjusted"}, {}),
        ("future", {}, {"available_at": "2026-09-04T00:00:00Z"}),
        ("wrong-source", {}, {"source": "other"}),
        ("fill-forward", {}, {"is_fill_forward": True}),
        ("quality", {}, {"quality_flags": ["suspect"]}),
        ("duration", {}, {"interval_start": "2026-08-06T14:03:00Z"}),
        ("alignment", {}, {"interval_start": "2026-08-06T14:04:01Z", "interval_end": "2026-08-06T14:05:01Z"}),
        ("outside", {}, {"interval_start": "2026-08-06T13:29:00Z", "interval_end": "2026-08-06T13:30:00Z"}),
        ("wrong-day", {}, {"interval_start": "2026-08-07T14:04:00Z", "interval_end": "2026-08-07T14:05:00Z", "available_at": "2026-08-07T14:05:00Z"}),
    ]
    for name, body, metadata in cases:
        partition("case-" + name, [bar(name, body, metadata)])
    original = next(m for m in members if m["record_id"] == "boolean")
    duplicate = deepcopy(original)
    duplicate.update(record_id="boolean-copy")
    duplicate["envelope"].update(event_id="event-boolean-copy", raw_ref="synthetic://omissions/copy", receive_sequence=999)
    members.append(duplicate)
    partition("duplicate", ["boolean", "boolean-copy"])
    partition("heldout", ["boolean"], heldout=True)
    good = bar("typed-good", {"volume": "10"})
    partition("minute-conflict", ["boolean", good])
    conflicting = bar("identity-other-minute", metadata={"interval_start": "2026-08-06T14:03:00Z", "interval_end": "2026-08-06T14:04:00Z"})
    members[-1]["envelope"]["metadata"]["provider_record_id"] = "boolean"
    partition("source-conflict", ["boolean", conflicting])
    future_session = deepcopy(session)
    future_session["record_id"] = "future-session"
    future_session["raw_body"]["available_at"] = "2026-09-04T00:00:00Z"
    members.append(_member("future-session", "exchange_session", "volume-calendar", future_session["raw_body"], future_session["envelope"]))
    partition("future-calendar", ["boolean"], calendar="future-session")
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    target.write_bytes(build_fixture())
    print(target)
