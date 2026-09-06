"""Build finite current-market traces; each adverse scenario has its own stream."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes

FIXTURE_ID = "p08d-current-market-v1"
AT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=timezone.utc)


def build_fixture() -> bytes:
    """
    Assemble actual five-kind evidence with isolated explicit revision traces.

    :returns: Readable bytes with the actual assembly instant and source locator.
    """
    folder = Path(__file__).parent / "fixtures"
    payload = json.loads((folder / "p08a-greek-ready-v1.json").read_bytes())
    proof = json.loads((folder / "p08b-quote-coherence-v1.json").read_bytes())
    ticks = json.loads((folder / "p08c-tick-evidence-v1.json").read_bytes())
    templates = {m["kind"]: m for m in payload["members"]}
    templates["quote_coherence"] = next(m for m in proof["members"] if m["record_id"] == "joint")
    templates["tick_rule"] = next(m for m in ticks["members"] if m["record_id"] == "tick-good")
    profiles = {p["kind"]: p for bundle in (payload, proof, ticks) for p in bundle["modeled_source_profiles"]}
    payload["members"] = []
    payload["modeled_source_profiles"] = []

    def add(scenario, label, kind, *, second=0, sequence=1, parent=None, changes=None, meta=None, envelope=None):
        """Append one owned member to its explicit scenario/kind stream."""
        stream = f"{scenario}-{kind}"
        record_id = f"{scenario}-{label}"
        if not any(p["profile_id"] == stream for p in payload["modeled_source_profiles"]):
            profile = deepcopy(profiles[kind])
            profile.update(profile_id=stream, stream_id=stream)
            payload["modeled_source_profiles"].append(profile)
        template = deepcopy(templates[kind])
        body, env = template["raw_body"], template["envelope"]
        env.update(stream_id=stream, event_id=f"event-{record_id}", raw_ref=f"synthetic://context/{record_id}",
                   receive_sequence=sequence, supersedes_record_id=f"{scenario}-{parent}" if parent else None)
        facts = env["metadata"] if env["metadata"] is not None else body
        facts.update(available_at=(AT + timedelta(seconds=second)).isoformat())
        facts["evidence_id" if kind == "quote_coherence" else "provider_record_id"] = f"provider-{record_id}"
        if kind == "tick_rule":
            body.update(price_from="0", price_until=None, effective_from=AT.isoformat(),
                        effective_until=(AT + timedelta(hours=6)).isoformat())
        if changes:
            body.update(changes)
        if meta:
            env["metadata"].update(meta)
        if envelope:
            env.update(envelope)
        member = _member(record_id, kind, stream, body, env)
        payload["members"].append(member)
        return member

    # Preserve original quote contents/metadata so the actual Greek/proof hashes bind.
    for kind in templates:
        row = add("market", kind, kind, second=1)
        if kind in ("option_quote", "underlying_quote"):
            row["envelope"]["metadata"] = deepcopy(templates[kind]["envelope"]["metadata"])
        row["raw_hash"] = _member("unused", kind, row["profile_id"], row["raw_body"], row["envelope"])["raw_hash"]

    for scenario, change, metadata, env in (
        ("crossed", {"bid": "6"}, {}, {}), ("malformed", {"ask": True}, {}, {}),
        ("unknown", {}, {"available_at": None}, {}),
        ("both", {}, {"source": None}, {"contract": {}}),
        ("source", {}, {"source": "wrong"}, {}),
        ("profile", {}, {"feed_class": "delayed"}, {}),
        ("event", {}, {"event_at": (AT + timedelta(seconds=5)).isoformat()}, {}),
        ("future", {"ask": True}, {}, {}),
        ("tie", {}, {}, {"receive_sequence": 1}),
        ("missing-sequence", {}, {}, {"receive_sequence": None}),
        ("linear", {}, {}, {}), ("orphan", {}, {}, {"supersedes_record_id": "absent"}),
        ("cycle", {}, {}, {}), ("fork", {}, {}, {}),
        ("conflict", {"ask": "5.20"}, {"provider_record_id": "shared"}, {}),
        ("duplicate", {}, {"provider_record_id": "shared"}, {}),
        ("unlinked", {}, {}, {"supersedes_record_id": None}),
    ):
        first = add(scenario, "A", "option_quote")
        if scenario in ("conflict", "duplicate"):
            first["envelope"]["metadata"]["provider_record_id"] = "shared"
            env["supersedes_record_id"] = None
        if scenario == "cycle":
            first["envelope"]["supersedes_record_id"] = f"{scenario}-B"
        add(scenario, "B", "option_quote", second=3 if scenario == "future" else 0,
            sequence=2, parent="A", changes=change, meta=metadata, envelope=env)
        if scenario == "fork":
            add(scenario, "C", "option_quote", sequence=3, parent="A")
        if scenario == "duplicate":
            add(scenario, "C", "option_quote", sequence=3, meta={"provider_record_id": "shared"})

    # R7's six examples, including malformed and equal-time edge variants.
    for scenario in ("bands", "schedule", "bad-band", "raw-band", "overlap", "tick-future",
                     "tick-fork", "tick-linear", "tick-tie", "tick-null", "scheduled-revision"):
        add(scenario, "A", "tick_rule", changes={"price_until": "3"})
        if scenario in ("bands", "overlap", "bad-band", "raw-band"):
            add(scenario, "B", "tick_rule", second=1, sequence=2,
                changes={"price_from": "4" if "band" in scenario and scenario != "bands" else "3", "increment": "0.05"})
        if scenario == "overlap":
            payload["members"][-2]["raw_body"]["price_until"] = "4"
        if scenario == "schedule":
            payload["members"][-1]["raw_body"]["effective_until"] = (AT + timedelta(hours=2)).isoformat()
            add(scenario, "B", "tick_rule", second=1, sequence=2,
                changes={"effective_from": (AT + timedelta(hours=2)).isoformat(), "increment": "0.05"})
        if scenario not in ("bands", "schedule", "overlap"):
            add(scenario, "A2", "tick_rule", second=3 if scenario == "tick-future" else 1,
                sequence=3, parent="A", changes={"price_until": "4", "increment": True if scenario == "raw-band" else "0"})
        if scenario == "scheduled-revision":
            payload["members"][-1]["raw_body"]["effective_from"] = (AT + timedelta(hours=2)).isoformat()
        if scenario in ("tick-fork", "tick-linear", "tick-tie", "tick-null"):
            add(scenario, "A3", "tick_rule", second=1,
                sequence=None if scenario == "tick-null" else 3 if scenario == "tick-tie" else 4,
                parent="A" if scenario == "tick-fork" else "A2")

    # Additional identity/causality traces share no scenario stream implicitly.
    for kind in ("option_quote", "underlying_quote", "greek_observation", "quote_coherence", "tick_rule"):
        scenario = "identity-" + kind
        first = add(scenario, "A", kind)
        identity_field = "evidence_id" if kind == "quote_coherence" else "provider_record_id"
        facts = first["envelope"]["metadata"] or first["raw_body"]
        changes = {"ask": "5.20"} if kind.endswith("quote") else {"delta": "0.55"} if kind == "greek_observation" else {"snapshot_id": "changed"} if kind == "quote_coherence" else {"increment": "0.05"}
        if kind.endswith("quote"):
            add(scenario, "B", kind, sequence=2, changes=changes, meta={identity_field: facts[identity_field]})
        else:
            add(scenario, "B", kind, sequence=2, changes={**changes, identity_field: facts[identity_field]})
    first = add("future-identity", "A", "option_quote")
    add("future-identity", "00-future", "option_quote", second=3, sequence=2,
        changes={"ask": "5.20"}, meta={"provider_record_id": first["envelope"]["metadata"]["provider_record_id"]})
    add("future-meta", "A", "option_quote")
    add("future-meta", "B", "option_quote", second=3, sequence=2, parent="A", meta={"source": None})
    add("giant", "A", "option_quote", changes={"bid_size": 10 ** 1001})
    add("unknown-key", "A", "option_quote")
    add("unknown-key", "B", "option_quote", sequence=2, changes={"ask": True}, meta={"source": None})
    add("cross-stream", "A", "option_quote")
    add("other-stream", "B", "option_quote", second=1, envelope={"supersedes_record_id": "cross-stream-A"})
    add("future-link", "A", "option_quote")
    add("future-link", "F", "option_quote", second=3, sequence=2,
        envelope={"supersedes_record_id": "malformed-A"})
    add("unknown-contract", "A", "option_quote", envelope={"contract": {}})
    original = add("alias-conflict", "A", "option_quote")
    changed = deepcopy(original["envelope"]["contract"])
    changed["right"] = "put"
    add("alias-conflict", "B", "option_quote", sequence=2,
        meta={"provider_record_id": original["envelope"]["metadata"]["provider_record_id"]}, envelope={"contract": changed})
    add("alias-conflict", "C", "option_quote", sequence=3,
        meta={"provider_record_id": original["envelope"]["metadata"]["provider_record_id"]})
    for scenario, kind, changes in (
        ("bad-method", "greek_observation", {"method_id": "wrong"}),
        ("bad-protocol", "quote_coherence", {"protocol_id": "wrong"}),
        ("bad-definition", "tick_rule", {"rule_id": "wrong"}),
        ("wrong-asof", "greek_observation", {"as_of": (AT + timedelta(seconds=1)).isoformat()}),
    ):
        add(scenario, "A", kind)
        add(scenario, "B", kind, second=1, sequence=2, parent="A", changes=changes)
    for row in payload["members"]:
        row["raw_hash"] = _member(row["record_id"], row["kind"], row["profile_id"], row["raw_body"], row["envelope"])["raw_hash"]
    payload.update(fixture_id=FIXTURE_ID, generator_id="optionslab-context-fixture-builder", generator_version="1",
                   assembled_at=datetime.now(timezone.utc).isoformat(), generator_source_ref="OptionsLab/tests/build_context_fixture.py")
    payload["definitions"].update(coherence_protocol_ids=proof["definitions"]["coherence_protocol_ids"],
                                  tick_definition_ids=ticks["definitions"]["tick_definition_ids"])
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
    target.write_bytes(build_fixture())
    print(target)
