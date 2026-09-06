"""Build independent synthetic coherence assessment scenarios, not an ordered trace."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from build_fixture import _member, _payload_bytes
from options_lab._input_parsing import _parse_contract_id
from options_lab.observations import normalize_observation_meta
from options_lab.quote_content import identify_quote_content
from options_lab.quote_inputs import normalize_quote_observation
from options_lab.underlying_inputs import normalize_underlying_quote

FIXTURE_ID = "p08b-quote-coherence-v1"
SOURCE_REF = "OptionsLab/tests/build_coherence_fixture.py"
AT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=timezone.utc)


def _quote_hash(member: dict[str, object]) -> str:
    """Derive a semantic quote hash through the actual retained raw owners."""
    envelope = member["envelope"]
    meta = normalize_observation_meta(
        envelope["metadata"], event_id=envelope["event_id"],
        raw_ref=envelope["raw_ref"],
        received_at=datetime.fromisoformat(envelope["simulated_received_at"]),
    ).value
    if meta is None:
        raise RuntimeError("fixture quote metadata is invalid")
    if member["kind"] == "option_quote":
        quote = normalize_quote_observation(
            member["raw_body"], contract=_parse_contract_id(envelope["contract"]),
            meta=meta, event_id=envelope["event_id"],
        ).value
    else:
        quote = normalize_underlying_quote(
            member["raw_body"], meta=meta, event_id=envelope["event_id"],
        ).value
    if quote is None:
        raise RuntimeError("fixture quote body is invalid")
    identity = identify_quote_content(quote)
    if identity.content_hash is None:
        raise RuntimeError("fixture quote identity is unsupported")
    return identity.content_hash


def build_fixture() -> bytes:
    """
    Build actual registered good and adverse proof members with current owners.

    :returns: Readable complete payload bytes with the actual assembly timestamp.
    :raises RuntimeError: If a reused quote cannot normalize or identify exactly.
    """
    old = Path(__file__).parent / "fixtures" / "p08a-greek-ready-v1.json"
    payload = json.loads(old.read_bytes())
    option, underlying = payload["members"][:2]
    option_hash, underlying_hash = _quote_hash(option), _quote_hash(underlying)
    joint = {
        "evidence_id": "joint", "source": "synthetic-coherence",
        "protocol_id": "fixture-joint-book-snapshot-v1",
        "contract": deepcopy(option["envelope"]["contract"]),
        "option_quote_hash": option_hash, "underlying_quote_hash": underlying_hash,
        "method": "joint_snapshot", "coherent_at": AT.isoformat(),
        "available_at": (AT + timedelta(seconds=1)).isoformat(),
        "snapshot_id": "joint-book-1", "sides": [],
    }
    overlap = deepcopy(joint)
    overlap.update(
        method="side_validity_overlap", snapshot_id=None,
        protocol_id="fixture-side-validity-overlap-v1",
        coherent_at=(AT + timedelta(seconds=1)).isoformat(),
        available_at=(AT + timedelta(seconds=2)).isoformat(),
        sides=[{
            "role": role, "quote_content_hash": quote_hash,
            "valid_from": AT.isoformat(), "invalidated_at": None,
            "observed_through": (AT + timedelta(seconds=1)).isoformat(),
            "evidence_record_id": "complete-stream-record-1",
        } for role, quote_hash in (
            ("option_bid", option_hash), ("option_ask", option_hash),
            ("underlying_bid", underlying_hash), ("underlying_ask", underlying_hash),
        )],
    )
    cases = {"joint": joint, "overlap": overlap}

    def variant(name: str, base: dict[str, object], **changes: object) -> dict[str, object]:
        """Retain a separate named assessment scenario without repairing facts."""
        body = deepcopy(base)
        body.update(changes)
        cases[name] = body
        return body

    def iso(seconds: float) -> str:
        """Produce exact microsecond-scale simulated fixture instants."""
        return (AT + timedelta(seconds=seconds)).isoformat()

    variant("joint_missing_snapshot", joint, snapshot_id=None)
    variant("joint_with_sides", joint, sides=deepcopy(overlap["sides"]))
    variant("joint_wrong_protocol", joint, protocol_id=overlap["protocol_id"])
    variant("joint_unknown_protocol", joint, protocol_id="unknown-fixture-protocol")
    variant("joint_missing_point", joint, coherent_at=None)
    variant("joint_missing_available", joint, available_at=None)
    variant("joint_future_point", joint, coherent_at=iso(3), available_at=iso(3))
    variant("joint_point_after_available", joint, available_at=iso(-1))
    variant("joint_source_after_point", joint, coherent_at=iso(-1))
    variant("joint_profile_mismatch", joint, source="different-raw-source")
    variant("joint_hash_mismatch", joint, option_quote_hash="0" * 64)
    variant("joint_future_available", joint, available_at=iso(3))
    variant("overlap_snapshot_present", overlap, snapshot_id="incompatible-snapshot")
    variant("overlap_missing_role", overlap, sides=deepcopy(overlap["sides"][:3]))
    variant("overlap_duplicate_role", overlap)["sides"][1]["role"] = "option_bid"
    variant("overlap_wrong_hash", overlap)["sides"][0]["quote_content_hash"] = "0" * 64
    variant("overlap_missing_start", overlap)["sides"][0]["valid_from"] = None
    variant("overlap_missing_watermark", overlap)["sides"][0]["observed_through"] = None
    variant("overlap_early_watermark", overlap)["sides"][0]["observed_through"] = iso(0)
    variant("overlap_invalidated_at_point", overlap)["sides"][0]["invalidated_at"] = iso(1)
    variant("overlap_reversed_end", overlap)["sides"][0]["invalidated_at"] = iso(0)
    variant("overlap_reversed_watermark", overlap)["sides"][0]["valid_from"] = iso(2)
    variant("overlap_future_watermark", overlap)["sides"][0]["observed_through"] = iso(3)
    variant("overlap_future_invalidation", overlap)["sides"][0]["invalidated_at"] = iso(3)
    variant("overlap_source_after_start", overlap)["sides"][0]["valid_from"] = iso(-1)
    disjoint = variant("overlap_disjoint", overlap)
    disjoint["sides"][0].update(valid_from=iso(1.5), observed_through=iso(2))
    endpoint = variant("overlap_known_end", overlap, coherent_at=iso(0.5))
    for side in endpoint["sides"]:
        side.update(observed_through=iso(2), invalidated_at=iso(1))

    variant("joint_ambiguous", joint)
    malformed = deepcopy(option)
    malformed["record_id"] = "malformed-option"
    malformed["envelope"]["contract"]["strike"] = "not-a-decimal"
    malformed["envelope"]["metadata"]["provider_record_id"] = "malformed-option-source"
    payload["members"].insert(0, malformed)
    crossed_body, crossed_envelope = deepcopy(option["raw_body"]), deepcopy(option["envelope"])
    crossed_body["bid"] = "5.20"
    crossed_envelope["raw_ref"] = "synthetic://option/crossed"
    crossed_envelope["event_id"] = "crossed-option-event"
    crossed_envelope["metadata"]["provider_record_id"] = "crossed-option-source"
    crossed = _member("crossed-option", "option_quote", option["profile_id"],
                      crossed_body, crossed_envelope)
    payload["members"].append(crossed)
    variant("joint_crossed_option", joint, option_quote_hash=_quote_hash(crossed))

    payload["modeled_source_profiles"].append({
        "profile_id": "coherence-scenarios", "kind": "quote_coherence",
        "source": "synthetic-coherence", "stream_id": "isolated-proof-scenarios",
        "feed_class": None, "fidelity": None, "availability_basis": "measured",
        "units": {}, "record_identity_rule": "new_evidence_id_per_update",
    })
    for index, (name, body) in enumerate(cases.items(), 1):
        body["evidence_id"] = name
        payload["members"].append(_member(
            name, "quote_coherence", "coherence-scenarios", body, {
                "event_id": f"coherence-{name}", "raw_ref": f"synthetic://coherence/{name}",
                "simulated_received_at": iso(4), "stream_id": "isolated-proof-scenarios",
                "receive_sequence": index, "supersedes_record_id": None,
                "contract": None, "metadata": None,
            },
        ))
    ambiguous = deepcopy(next(m for m in payload["members"] if m["record_id"] == "joint_ambiguous"))
    ambiguous["record_id"] = "joint-ambiguous-second-occurrence"
    payload["members"].append(ambiguous)
    malformed_proof = deepcopy(joint)
    del malformed_proof["source"]
    payload["members"].insert(0, _member(
        "malformed-coherence", "quote_coherence", "coherence-scenarios", malformed_proof,
        {"event_id": "malformed-proof", "raw_ref": "synthetic://malformed-proof",
         "simulated_received_at": iso(4), "stream_id": "isolated-proof-scenarios",
         "receive_sequence": 0, "supersedes_record_id": None,
         "contract": None, "metadata": None},
    ))
    payload.update(
        fixture_id=FIXTURE_ID, generator_id="optionslab-coherence-fixture-builder",
        generator_version="1", generator_source_ref=SOURCE_REF,
        assembled_at=datetime.now(timezone.utc).isoformat(),
    )
    payload["definitions"]["coherence_protocol_ids"] = [
        "fixture-joint-book-snapshot-v1", "fixture-side-validity-overlap-v1",
    ]
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
    target.write_bytes(build_fixture())
    print(target)
