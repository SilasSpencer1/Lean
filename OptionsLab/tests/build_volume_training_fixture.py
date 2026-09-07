"""Build explicit synthetic prior-session volume training and adverse selections."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from build_fixture import _member, _metadata, _payload_bytes


FIXTURE_ID = "p10a-volume-training-v1"
GENERATOR = "optionslab-volume-training-fixture-builder"
DAYS = (
    "2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
    "2026-08-13", "2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19",
    "2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26",
    "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02",
)


def build_fixture() -> bytes:
    """Assemble declared calendar facts without inferring weekdays or holidays.

    :returns: Actual fixture bytes with truthful current generator assembly time.
    """
    prior = json.loads((Path(__file__).parent / "fixtures/p08a-greek-ready-v1.json").read_bytes())
    payload = {**prior, "members": [], "modeled_source_profiles": []}
    members = payload["members"]
    for kind, source, stream, units in (
        ("exchange_session", "fixture-calendar", "volume-calendar", {}),
        ("underlying_bar", "fixture-volume-bars", "volume-bars", dict(price="USD_per_share", volume="shares", vwap_numerator="USD", vwap_denominator="shares")),
        ("underlying_bar", "other-volume-bars", "other-volume-bars", dict(price="USD_per_share", volume="shares", vwap_numerator="USD", vwap_denominator="shares")),
        ("volume_partition", GENERATOR, "volume-partitions", {}),
    ):
        payload["modeled_source_profiles"].append(dict(
            profile_id=stream, kind=kind, source=source, stream_id=stream,
            feed_class="realtime" if kind == "underlying_bar" else None,
            fidelity=None if kind == "volume_partition" else "genuine",
            availability_basis="measured", units=units,
            record_identity_rule="provider_record_id_and_revision_id" if kind == "underlying_bar" else "new_event_id_per_update" if kind == "volume_partition" else "new_provider_record_id_per_update",
        ))

    def add(record, kind, body, *, stream, at, metadata=None):
        """Append an actual six-field member and eight-field receipt envelope."""
        env = dict(event_id="event-" + record, raw_ref="synthetic://volume/" + record,
                   simulated_received_at=at, stream_id=stream, receive_sequence=None,
                   supersedes_record_id=None, contract=None, metadata=metadata)
        member = _member(record, kind, stream, body, env)
        members.append(member)
        return member

    groups = []
    for index, day in enumerate(DAYS):
        session_id = "session-" + day
        add(session_id, "exchange_session", dict(
            calendar="XNYS", session_date=day, kind="regular",
            opens_at=day + "T13:30:00Z", closes_at=day + "T20:00:00Z",
            source="fixture-calendar", provider_record_id=session_id, source_version="1",
            available_at=day + "T12:00:00Z", availability_basis="measured", fidelity="genuine",
        ), stream="volume-calendar", at=day + "T12:00:00Z")
        ids = []
        for minute, volume in ((35, "900" if index < 10 else "1100"), (34, str(index // 5)), (33, None if index == 0 else "10")):
            record = f"bar-{day}-{minute}"
            meta = _metadata("fixture-volume-bars", record)
            meta.update(kind="interval", event_at=None, available_at=f"{day}T14:{minute-30:02d}:00Z",
                        interval_start=f"{day}T14:{minute-31:02d}:00Z", interval_end=f"{day}T14:{minute-30:02d}:00Z")
            body = dict(symbol="SPY", close_price=None, volume=volume, vwap_numerator=None,
                        vwap_denominator=None, price_basis="raw", volume_definition_id=None if minute == 33 and index == 1 else "synthetic-volume-v1",
                        vwap_definition_id=None, revision_id="r1", supersedes_revision_id=None)
            add(record, "underlying_bar", body, stream="volume-bars", at=meta["available_at"], metadata=meta)
            ids.append(record)
        groups.append(dict(session_record_id=session_id, bar_record_ids=ids))

    def partition(name, selected, **changes):
        """Add a complete concrete selection without embedding its payload root."""
        body = dict(schema_version=1, partition_id=name, training_sessions=list(DAYS),
                    validation_sessions=[], test_sessions=[], volume_definition_id="synthetic-volume-v1",
                    training_inputs=deepcopy(selected))
        body.update(changes)
        return add(name, "volume_partition", body, stream="volume-partitions", at="2026-09-03T00:00:00Z")

    partition("training", groups)
    partition("heldout", groups, training_sessions=list(DAYS[:-1]), validation_sessions=[DAYS[-1]])
    partition("nonmember", groups, training_sessions=list(DAYS[1:]))
    partition("empty-bars", [dict(session_record_id=groups[0]["session_record_id"], bar_record_ids=[])])
    partition("all-omitted", [dict(session_record_id=g["session_record_id"], bar_record_ids=[g["bar_record_ids"][2]]) for g in groups[:2]])
    first = next(m for m in members if m["record_id"] == groups[0]["bar_record_ids"][0])
    for name, body_changes, metadata_changes in (
        ("wrong-definition", {"volume_definition_id": "other"}, {}),
        ("raw-volume", {"volume": True}, {}),
        ("wrong-source", {}, {"source": "wrong"}),
        ("future-bar", {}, {"available_at": "2026-09-03T00:00:00.000001Z"}),
        ("fill-forward", {}, {"is_fill_forward": True}),
        ("quality", {}, {"quality_flags": ["bad"]}),
        ("adjusted", {"price_basis": "split_adjusted"}, {}),
        ("self-revision", {"supersedes_revision_id": "r1"}, {}),
        ("conflicting-revision", {"revision_id": "r2", "volume": "3000"}, {}),
        ("other-stream", {}, {"source": "other-volume-bars"}),
        ("source-identity", {}, {"interval_start": "2026-08-06T14:05:00Z", "interval_end": "2026-08-06T14:06:00Z", "available_at": "2026-08-06T14:06:00Z"}),
        ("unsupported-amount", {"volume": "1" + "0" * 1000}, {}),
    ):
        body, meta = deepcopy(first["raw_body"]), deepcopy(first["envelope"]["metadata"])
        body.update(body_changes)
        meta.update(metadata_changes)
        add(name + "-bar", "underlying_bar", body, stream="other-volume-bars" if name == "other-stream" else "volume-bars", at=meta["available_at"], metadata=meta)
        selected = deepcopy(groups)
        if name in ("conflicting-revision", "source-identity"):
            selected[0]["bar_record_ids"].append(name + "-bar")
        else:
            selected[0]["bar_record_ids"][0] = name + "-bar"
        partition(name, selected)
    duplicate = deepcopy(first)
    duplicate["record_id"] = "duplicate-bar"
    duplicate["envelope"].update(event_id="duplicate-event", raw_ref="synthetic://duplicate", receive_sequence=2, simulated_received_at="2026-08-06T14:06:00Z")
    members.append(duplicate)
    selected = deepcopy(groups)
    selected[0]["bar_record_ids"].append("duplicate-bar")
    partition("duplicate", selected)
    source_session = next(m for m in members if m["record_id"] == groups[0]["session_record_id"])
    for name, changes in (
        ("future-calendar", {"available_at": "2026-09-03T00:00:00.000001Z"}),
        ("missing-calendar", {"available_at": None}),
        ("early-close", {"kind": "early_close"}),
        ("wrong-calendar-profile", {"source": "other-calendar"}),
    ):
        body = {**source_session["raw_body"], **changes}
        add(name + "-session", "exchange_session", body, stream="volume-calendar", at="2026-08-06T12:00:00Z")
        selected = deepcopy(groups)
        selected[0]["session_record_id"] = name + "-session"
        partition(name, selected)
    partition("unknown-session", [dict(session_record_id="absent", bar_record_ids=[])])
    partition("wrong-member-kind", [dict(session_record_id="training", bar_record_ids=[])])
    partition("unknown-bar", [dict(session_record_id=groups[0]["session_record_id"], bar_record_ids=["absent"])])
    partition("wrong-bar-kind", [dict(session_record_id=groups[0]["session_record_id"], bar_record_ids=["training"])])
    alternate = {**source_session["raw_body"], "source_version": "2"}
    add("alternate-calendar", "exchange_session", alternate, stream="volume-calendar", at="2026-08-06T12:00:00Z")
    partition("calendar-conflict", groups + [dict(session_record_id="alternate-calendar", bar_record_ids=groups[0]["bar_record_ids"])])
    repeated = deepcopy(next(m["raw_body"] for m in members if m["record_id"] == groups[1]["session_record_id"]))
    repeated["provider_record_id"] = source_session["raw_body"]["provider_record_id"]
    add("repeated-calendar-identity", "exchange_session", repeated, stream="volume-calendar", at="2026-08-07T12:00:00Z")
    selected = deepcopy(groups)
    selected[1]["session_record_id"] = "repeated-calendar-identity"
    partition("calendar-source-identity", selected)
    dst_groups = []
    for day, hour in (("2026-02-27", 14), ("2026-03-09", 13)):
        session_id, record = "session-" + day, "bar-" + day
        body = {**source_session["raw_body"], "session_date": day, "provider_record_id": session_id,
                "opens_at": f"{day}T{hour}:30:00Z", "closes_at": f"{day}T{hour+7}:00:00Z", "available_at": day + "T12:00:00Z"}
        add(session_id, "exchange_session", body, stream="volume-calendar", at=day + "T12:00:00Z")
        meta = {**first["envelope"]["metadata"], "provider_record_id": record,
                "interval_start": f"{day}T{hour}:30:00Z", "interval_end": f"{day}T{hour}:31:00Z", "available_at": f"{day}T{hour}:31:00Z"}
        add(record, "underlying_bar", first["raw_body"], stream="volume-bars", at=meta["available_at"], metadata=meta)
        dst_groups.append(dict(session_record_id=session_id, bar_record_ids=[record]))
    partition("dst", dst_groups, training_sessions=["2026-02-27", "2026-03-09"])
    payload.update(fixture_id=FIXTURE_ID, generator_id=GENERATOR, generator_version="1",
                   assembled_at=datetime.now(timezone.utc).isoformat(), generator_source_ref="OptionsLab/tests/build_volume_training_fixture.py")
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    target.write_bytes(build_fixture())
    print(target)
