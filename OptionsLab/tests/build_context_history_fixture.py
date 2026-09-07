"""Build admitted minute history with independent concrete adverse streams."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from build_fixture import _member, _metadata, _payload_bytes

FIXTURE_ID = "p08d-history-context-v1"


def build_fixture() -> bytes:
    """Assemble actual owner-shaped session and bar records.

    :returns: Readable payload with the actual assembly timestamp.
    """
    original = json.loads((Path(__file__).parent / "fixtures/p08d-reference-context-v1.json").read_bytes())
    payload = {**original, "members": [], "modeled_source_profiles": []}
    for member in original["members"]:
        if member["record_id"].startswith("joined-"):
            payload["members"].append(deepcopy(member))
            payload["modeled_source_profiles"].extend(deepcopy(p) for p in original["modeled_source_profiles"] if p["profile_id"] == member["profile_id"])
    session = deepcopy(next(m for m in original["members"] if m["record_id"] == "null-end-S"))
    session.update(record_id="history-S", profile_id="history-session")
    session["envelope"].update(stream_id="history-session", event_id="history-session", raw_ref="synthetic://history/session")
    payload["members"].append(session)
    payload["modeled_source_profiles"].append(dict(profile_id="history-session", kind="exchange_session", source="fixture-calendar", stream_id="history-session", feed_class=None, fidelity="genuine", availability_basis="measured", units={}, record_identity_rule="new_provider_record_id_per_update"))

    def add(scenario, label, *, minute=0, available="14:01:00", seq=1, parent=None, changes=None, meta_changes=None, env_changes=None):
        """Append one actual arrival with independently editable raw evidence."""
        stream, record = scenario + "-bars", scenario + "-" + label
        if not any(p["profile_id"] == stream for p in payload["modeled_source_profiles"]):
            payload["modeled_source_profiles"].append(dict(profile_id=stream, kind="underlying_bar", source="fixture-bars", stream_id=stream, feed_class="realtime", fidelity="genuine", availability_basis="measured", units=dict(price="USD_per_share", volume="shares", vwap_numerator="USD", vwap_denominator="shares"), record_identity_rule="provider_record_id_and_revision_id"))
        at = "2026-09-08T" + available + "Z"
        meta = _metadata("fixture-bars", scenario + "-minute-" + str(minute))
        meta.update(kind="interval", event_at=None, available_at=at, interval_start=f"2026-09-08T14:{minute:02d}:00Z", interval_end=f"2026-09-08T14:{minute+1:02d}:00Z")
        if meta_changes:
            meta.update(meta_changes)
        body = dict(symbol="SPY", close_price="650", volume="10", vwap_numerator="6500", vwap_denominator="10", price_basis="raw", volume_definition_id="fixture-volume", vwap_definition_id="fixture-vwap", revision_id="r2" if parent else "r1", supersedes_revision_id="r1" if parent else None)
        if changes:
            body.update(changes)
        env = dict(event_id="event-" + record, raw_ref="synthetic://history/" + record, simulated_received_at=at, stream_id=stream, receive_sequence=seq, supersedes_record_id=scenario + "-" + parent if parent else None, contract=None, metadata=meta)
        if env_changes:
            env.update(env_changes)
        member = _member(record, "underlying_bar", stream, body, env)
        payload["members"].append(member)
        return member

    add("history", "B0")
    add("history", "B0r2", available="14:02:00", seq=2, parent="B0", changes={"close_price": "651"})
    add("history", "B1", minute=1, available="14:03:00", seq=3)
    for name, value in (("missing", None), ("zero", "0"), ("raw", True), ("unknown", "651")):
        add(name, "B0")
        add(name, "B0r2", available="14:02:00", seq=2, parent="B0", changes={"close_price": value}, meta_changes={"available_at": None} if name == "unknown" else None)
    for name, changes in (
        ("partial", dict(volume=None, volume_definition_id=None, vwap_numerator=None, vwap_denominator=None, vwap_definition_id=None)),
        ("zero-vwap", dict(volume="0", vwap_numerator="0", vwap_denominator="0")),
        ("definitions", dict(volume="20", volume_definition_id="other-volume", vwap_definition_id="other-vwap")),
        ("huge", dict(close_price="1" + "0" * 1000)),
    ):
        add(name, "B0", changes=changes)
    for name, seq in (("ordered", 5), ("tie", 4), ("unknown-seq", None), ("backward", 3)):
        add(name, "B0", available="14:03:00", seq=4)
        add(name, "B1", minute=1, available="14:03:00", seq=seq)
    for name in ("duplicate", "conflict"):
        a = add(name, "B0")
        add(name, "B0r2", available="14:02:00", seq=2, parent="B0", changes={"close_price": "651"})
        b = add(name, "redelivery", seq=3, env_changes={"simulated_received_at": "2026-09-08T14:03:00Z"})
        b["envelope"]["metadata"] = deepcopy(a["envelope"]["metadata"])
        if name == "conflict":
            b["raw_body"]["close_price"] = "652"
    add("future", "B0")
    add("future", "B1", minute=1, available="14:02:00.000001", seq=2)
    add("gap", "B0")
    add("gap", "B2", minute=2, available="14:03:00", seq=2)
    add("gap", "B1", minute=1, available="14:04:00", seq=3)
    for name, changes, meta_changes, env_changes in (
        ("body-lineage", {"supersedes_revision_id": "wrong"}, None, None),
        ("shifted", None, {"interval_end": "2026-09-08T14:02:00Z"}, None),
        ("missing-link", None, None, {"supersedes_record_id": None}),
        ("profile", None, {"source": "wrong-source"}, None),
    ):
        add(name, "B0")
        add(name, "B0r2", available="14:02:00", seq=2, parent="B0", changes=changes, meta_changes=meta_changes, env_changes=env_changes)
    add("cross", "B0")
    add("cross-other", "B1", minute=1, available="14:03:00", seq=3)
    a = add("ambiguous", "B0")
    b = add("ambiguous-other", "B0")
    b["envelope"].update({k: deepcopy(v) for k, v in a["envelope"].items() if k != "stream_id"})
    a = add("same-time-duplicate", "B0", available="14:03:00", seq=4)
    add("same-time-duplicate", "B0r2", available="14:03:00", seq=5, parent="B0", changes={"close_price": "651"})
    b = add("same-time-duplicate", "A-redelivery", available="14:03:00", seq=10)
    b["envelope"]["metadata"] = deepcopy(a["envelope"]["metadata"])
    add("same-time-correction-copy", "B0", available="14:03:00", seq=4)
    original = add("same-time-correction-copy", "B0r2", available="14:03:00", seq=5, parent="B0", changes={"close_price": "651"})
    copy = add("same-time-correction-copy", "A-copy", available="14:03:00", seq=3, parent="B0")
    copy["raw_body"] = deepcopy(original["raw_body"])
    copy["envelope"]["metadata"] = deepcopy(original["envelope"]["metadata"])
    for name in ("chain", "future-correction"):
        add(name, "B0")
        add(name, "B0r2", available="14:02:00" if name == "chain" else "14:03:00.000001", seq=2, parent="B0", changes={"close_price": "651" if name == "chain" else True})
    add("chain", "B0r3", available="14:03:00", seq=3, parent="B0r2", changes={"close_price": "652", "revision_id": "r3", "supersedes_revision_id": "r2"})
    add("huge-sequence", "B0", seq=10**1000)
    add("wrong-meta", "B0", meta_changes={"kind": "quote", "event_at": "2026-09-08T14:00:00Z"})
    add("wrong-feed", "B0", meta_changes={"feed_class": "delayed"})
    add("wrong-fidelity", "B0", meta_changes={"fidelity": "synthetic"})
    add("wrong-basis", "B0", meta_changes={"availability_basis": "assumed"})
    for member in payload["members"]:
        member["raw_hash"] = _member(member["record_id"], member["kind"], member["profile_id"], member["raw_body"], member["envelope"])["raw_hash"]
    payload.update(fixture_id=FIXTURE_ID, generator_id="optionslab-context-history-fixture-builder", generator_version="1", assembled_at=datetime.now(timezone.utc).isoformat(), generator_source_ref="OptionsLab/tests/build_context_history_fixture.py")
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
    target.write_bytes(build_fixture())
    print(target)
