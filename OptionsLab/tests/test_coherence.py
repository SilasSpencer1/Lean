from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
from inspect import signature
import json
from pathlib import Path

import pytest

import options_lab.admission as admission
import options_lab.coherence as api
from options_lab._input_parsing import _parse_contract_id
from options_lab.greek_inputs import normalize_greek_observation
from options_lab.observations import normalize_observation_meta
from options_lab.quote_content import identify_quote_content
from options_lab.quote_inputs import normalize_quote_observation
from options_lab.underlying_inputs import normalize_underlying_quote

UTC = timezone.utc
AT = datetime(2026, 9, 5, 14, 29, 58, tzinfo=UTC)
NOW = AT + timedelta(seconds=2)
FIXTURE_ID = "p08b-quote-coherence-v1"
FIXTURE = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
OLD_FIXTURE = FIXTURE.with_name("p08a-greek-ready-v1.json")


def raw_body(name="overlap"):
    return next(m["raw_body"] for m in json.loads(FIXTURE.read_bytes())["members"]
                if m["record_id"] == name)


def normalize(body):
    return api.normalize_quote_coherence(
        body, event_id="raw-coherence", raw_ref="synthetic://raw-coherence",
        received_at=NOW,
    )


@pytest.fixture(scope="module")
def admitted():
    result = admission.verify_fixture_bundle(
        FIXTURE_ID, FIXTURE.read_bytes(), event_id="verify-coherence",
        raw_ref="fixture://coherence", received_at=datetime.now(UTC),
    )
    assert result.rejection is None and result.value is not None
    manifest = result.value
    quotes = []
    for record_id in ("option-member-1", "underlying-member-1"):
        member = next(m for m in manifest.members if m.record_id == record_id)
        envelope = member.decode_envelope()
        meta = normalize_observation_meta(
            envelope["metadata"], event_id=envelope["event_id"], raw_ref=envelope["raw_ref"],
            received_at=datetime.fromisoformat(envelope["simulated_received_at"]),
        ).value
        assert meta is not None
        if member.kind == "option_quote":
            quote = normalize_quote_observation(
                member.decode_raw_body(), contract=_parse_contract_id(envelope["contract"]),
                meta=meta, event_id=envelope["event_id"],
            ).value
        else:
            quote = normalize_underlying_quote(
                member.decode_raw_body(), meta=meta, event_id=envelope["event_id"],
            ).value
        assert quote is not None
        quotes.append(quote)
    return manifest, *quotes


def evidence(admitted, name):
    member = next(m for m in admitted[0].members if m.record_id == name)
    envelope = member.decode_envelope()
    result = api.normalize_quote_coherence(
        member.decode_raw_body(), event_id=envelope["event_id"],
        raw_ref=envelope["raw_ref"],
        received_at=datetime.fromisoformat(envelope["simulated_received_at"]),
    )
    assert result.rejection is None and result.value is not None
    return result.value


def assess(admitted, name="joint", *, at=NOW, cap=timedelta(seconds=5)):
    manifest, option, underlying = admitted
    return api.assess_quote_coherence(
        evidence(admitted, name), option, underlying, manifest=manifest,
        decision_at=at, max_quote_age=cap,
    )


@pytest.mark.parametrize("name", ("joint", "overlap", "overlap_known_end"))
def test_registered_proofs_bind_actual_members_and_remain_synthetic(admitted, name):
    result = assess(admitted, name)
    assert result.coherent and result.reasons == ()
    assert result.coherent_at == result.evidence.coherent_at
    assert result.proof_basis == result.evidence.method
    assert tuple(m.record_id for m in result.matched_members) == (
        "option-member-1", "underlying-member-1", name,
    )
    assert result.matched_members[0].raw_hash != result.option_identity.content_hash
    assert result.supports_instant(result.coherent_at)
    assert result.manifest.origin == "synthetic" and result.manifest.fidelity_tier == 0
    assert not result.manifest.operational_allowed and not result.manifest.economic_allowed


ADVERSE = (
    ("joint_missing_snapshot", "joint_snapshot_id_missing"),
    ("joint_with_sides", "joint_sides_present"),
    ("joint_wrong_protocol", "protocol_method_mismatch"),
    ("joint_unknown_protocol", "protocol_not_registered"),
    ("joint_missing_point", "coherent_at_missing"),
    ("joint_missing_available", "available_at_missing"),
    ("joint_future_point", "coherent_at_after_decision"),
    ("joint_point_after_available", "coherent_at_after_available"),
    ("joint_source_after_point", "option_event_after_coherent_at"),
    ("joint_profile_mismatch", "evidence_profile_mismatch"),
    ("joint_hash_mismatch", "option_hash_mismatch"),
    ("joint_future_available", "available_after_decision"),
    ("overlap_snapshot_present", "overlap_snapshot_id_present"),
    ("overlap_missing_role", "side_roles_invalid"),
    ("overlap_duplicate_role", "side_roles_invalid"),
    ("overlap_wrong_hash", "option_bid_hash_mismatch"),
    ("overlap_missing_start", "option_bid_valid_from_missing"),
    ("overlap_missing_watermark", "option_bid_watermark_missing"),
    ("overlap_early_watermark", "option_bid_instant_not_supported"),
    ("overlap_invalidated_at_point", "option_bid_instant_not_supported"),
    ("overlap_reversed_end", "option_bid_interval_order_invalid"),
    ("overlap_reversed_watermark", "option_bid_watermark_before_valid_from"),
    ("overlap_future_watermark", "option_bid_watermark_after_available"),
    ("overlap_future_invalidation", "option_bid_invalidation_after_available"),
    ("overlap_source_after_start", "option_bid_source_after_valid_from"),
    ("overlap_disjoint", "option_bid_instant_not_supported"),
)


@pytest.mark.parametrize("name,reason", ADVERSE)
def test_registered_adverse_proofs_fail_semantics_after_successful_membership(admitted, name, reason):
    result = assess(admitted, name)
    assert len(result.matched_members) == 3
    assert not any("member_" in r for r in result.reasons)
    assert reason in result.reasons
    assert not result.coherent and result.coherent_at is None and result.proof_basis is None
    assert not result.supports_instant(AT)
    assert result.evidence == evidence(admitted, name)


def test_point_and_interval_endpoints_do_not_invent_continuous_validity(admitted):
    joint = assess(admitted)
    overlap = assess(admitted, "overlap")
    bounded = assess(admitted, "overlap_known_end")
    assert joint.supports_instant(AT)
    assert not joint.supports_instant(AT + timedelta(microseconds=1))
    assert not joint.supports_instant(AT - timedelta(microseconds=1))
    assert overlap.supports_instant(AT) and overlap.supports_instant(AT + timedelta(seconds=1))
    assert not overlap.supports_instant(AT + timedelta(seconds=1, microseconds=1))
    assert not overlap.supports_instant(AT - timedelta(microseconds=1))
    assert bounded.supports_instant(AT + timedelta(seconds=1) - timedelta(microseconds=1))
    assert not bounded.supports_instant(AT + timedelta(seconds=1))
    assert not bounded.supports_instant(NOW + timedelta(microseconds=1))
    # One actual complete-stream record can substantiate four distinct roles.
    assert len({s.evidence_record_id for s in overlap.evidence.sides}) == 1
    member = next(m for m in admitted[0].members if m.record_id == "greek-member-1")
    env = member.decode_envelope()
    greek = normalize_greek_observation(
        member.decode_raw_body(), contract=_parse_contract_id(env["contract"]),
        event_id=env["event_id"], raw_ref=env["raw_ref"],
        received_at=datetime.fromisoformat(env["simulated_received_at"]),
    ).value
    assert greek is not None
    assert joint.supports_instant(greek.as_of) and overlap.supports_instant(greek.as_of)


def test_exact_age_caps_availability_and_missing_evidence_preserve_owner_failures(admitted):
    assert assess(admitted, at=AT + timedelta(seconds=5)).coherent
    stale = assess(admitted, at=AT + timedelta(seconds=5, microseconds=1))
    assert "coherence_too_old" in stale.reasons
    assert any(r.startswith("option_") for r in stale.reasons)
    assert assess(admitted, cap=timedelta(seconds=2)).coherent
    assert "coherence_too_old" in assess(admitted, cap=timedelta(seconds=1)).reasons
    missing = api.assess_quote_coherence(
        None, admitted[1], admitted[2], manifest=admitted[0],
        decision_at=AT + timedelta(seconds=6), max_quote_age=timedelta(seconds=5),
    )
    assert "coherence_evidence_missing" in missing.reasons
    assert any(r.startswith("option_") for r in missing.reasons)
    assert not missing.supports_instant(AT)


@pytest.mark.parametrize("change", (
    {"raw_ref": "caller://different"}, {"source": "caller-source"},
    {"evidence_id": "caller-id"}, {"snapshot_id": "caller-snapshot"},
    {"available_at": NOW}, {"coherent_at": NOW},
))
def test_changed_evidence_cannot_borrow_member_hashes_or_identity(admitted, change):
    result = api.assess_quote_coherence(
        replace(evidence(admitted, "joint"), **change), admitted[1], admitted[2],
        manifest=admitted[0], decision_at=NOW, max_quote_age=timedelta(seconds=5),
    )
    assert "evidence_member_missing" in result.reasons
    assert not result.coherent


@pytest.mark.parametrize("field,value", (
    ("raw_ref", "different://locator"), ("received_at", NOW + timedelta(seconds=1)),
    ("provider_record_id", "new-same-price-update"), ("source", "different-source"),
))
def test_actual_quote_envelope_and_update_identity_are_bound(admitted, field, value):
    changed = replace(admitted[1], meta=replace(admitted[1].meta, **{field: value}))
    result = api.assess_quote_coherence(
        evidence(admitted, "joint"), changed, admitted[2], manifest=admitted[0],
        decision_at=NOW, max_quote_age=timedelta(seconds=5),
    )
    assert "option_member_missing" in result.reasons
    assert not result.coherent
    if field in ("raw_ref", "received_at"):
        assert identify_quote_content(changed).content_hash == identify_quote_content(admitted[1]).content_hash


def test_other_root_cannot_supply_evidence_and_original_fixture_is_unchanged(admitted):
    payload = OLD_FIXTURE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == "6a40fe628066a6f73c6c0b6599ef9ddaf94c213fdf0e1adf589e5333ac5f7c04"
    old = admission.verify_fixture_bundle(
        "p08a-greek-ready-v1", payload, event_id="old", raw_ref="old://fixture",
        received_at=datetime.now(UTC),
    ).value
    assert old is not None and old.coherence_protocol_ids == ()
    result = api.assess_quote_coherence(
        evidence(admitted, "joint"), admitted[1], admitted[2], manifest=old,
        decision_at=NOW, max_quote_age=timedelta(seconds=5),
    )
    assert "evidence_member_missing" in result.reasons and "protocol_not_registered" in result.reasons


def schema_paths():
    body = raw_body()
    return ([(key,) for key in body] + [("contract", key) for key in body["contract"]]
            + [("sides", i, key) for i, side in enumerate(body["sides"]) for key in side])


def target_at(body, path):
    target = body
    for part in path[:-1]:
        target = target[part]
    return target


@pytest.mark.parametrize("path", schema_paths())
def test_every_required_schema_key_reaches_a_closed_missing_rejection(path):
    body = raw_body()
    del target_at(body, path)[path[-1]]
    result = normalize(body)
    assert result.value is None and result.rejection is not None
    assert result.rejection.code == "missing"
    assert result.rejection.stage == "quote_coherence_normalization"
    assert result.rejection.reasons == ("missing",)


def test_schema_type_mutations_are_total_and_never_echo_raw_input():
    for path in schema_paths():
        for value in (None, False, 3, "", "untrusted-secret", [], {}):
            body = raw_body()
            target_at(body, path)[path[-1]] = deepcopy(value)
            result = normalize(body)
            assert (result.value is None) != (result.rejection is None)
            if result.rejection is not None:
                assert "untrusted-secret" not in repr(result.rejection)
    for raw in (None, False, [], "untrusted-secret", {1: "secret"}):
        assert normalize(raw).rejection is not None


def test_shape_precedence_and_adverse_values_survive_normalization():
    body = raw_body()
    del body["source"]
    body["sides"][0]["unknown"] = True
    assert (normalize(body).rejection.field, normalize(body).rejection.code) == ("source", "missing")
    body = raw_body()
    body["contract"]["strike"] = "NaN"
    body["option_quote_hash"] = False
    assert normalize(body).rejection.field == "contract.strike"
    for name, _ in ADVERSE:
        assert normalize(raw_body(name)).value is not None


def test_hostile_values_are_rejected_without_coercion():
    class Hostile(str):
        def __str__(self):
            raise AssertionError("coerced")
        def __repr__(self):
            raise AssertionError("repr")
        def __eq__(self, other):
            raise AssertionError("compared")
        __hash__ = str.__hash__
    body = raw_body()
    body[Hostile("hostile-key")] = 1
    assert normalize(body).rejection.code == "unknown_fields"
    body = raw_body()
    body["source"] = Hostile("source")
    assert normalize(body).rejection.code == "invalid_type"


def test_exact_constructors_results_and_public_signatures(admitted):
    value = evidence(admitted, "overlap")
    with pytest.raises(FrozenInstanceError):
        value.source = "changed"
    with pytest.raises(TypeError):
        replace(value, sides=list(value.sides))
    with pytest.raises(ValueError):
        replace(value.sides[0], role="unknown")
    with pytest.raises(ValueError):
        replace(value, option_quote_hash="A" * 64)
    with pytest.raises(ValueError):
        api.CoherenceValidation()
    with pytest.raises(ValueError):
        api.CoherenceValidation(value=value, rejection=normalize(None).rejection)
    with pytest.raises(ValueError):
        api.CoherenceInputRejection("e", NOW, "raw", "secret", "missing")
    with pytest.raises(TypeError):
        api.CoherenceAssessment(value, admitted[1], admitted[2], admitted[0], NOW,
                                timedelta(seconds=5), reasons=())
    for cap in (timedelta(0), timedelta(seconds=5, microseconds=1)):
        with pytest.raises(ValueError):
            assess(admitted, cap=cap)
    with pytest.raises(TypeError):
        assess(admitted, cap=5)
    with pytest.raises(ValueError):
        assess(admitted).supports_instant(datetime(2026, 9, 5))
    with pytest.raises(TypeError):
        assess(admitted).supports_instant(None)
    assert tuple(signature(api.normalize_quote_coherence).parameters) == (
        "raw", "event_id", "raw_ref", "received_at",
    )
    assert tuple(signature(api.assess_quote_coherence).parameters) == (
        "evidence", "option_quote", "underlying_quote", "manifest", "decision_at", "max_quote_age",
    )


def test_hostile_decimal_context_and_extreme_quotes_produce_bounded_evidence(admitted):
    with localcontext() as context:
        context.prec, context.Emin, context.Emax = 1, -1, 1
        for signal in context.traps:
            context.traps[signal] = True
        context.clear_flags()
        assert assess(admitted).coherent and assess(admitted, "overlap").coherent
        extreme = replace(admitted[1], ask=Decimal("1e1001"))
        result = api.assess_quote_coherence(
            evidence(admitted, "joint"), extreme, admitted[2], manifest=admitted[0],
            decision_at=NOW, max_quote_age=timedelta(seconds=5),
        )
        assert not result.coherent and result.option_identity.content_hash is None
        assert any("unsupported" in reason for reason in result.reasons)
        assert not any(context.flags.values())


def test_registered_ambiguous_evidence_never_selects_by_encounter_order(admitted):
    result = assess(admitted, "joint_ambiguous")
    assert "evidence_member_ambiguous" in result.reasons
    assert not result.coherent


def test_registered_crossed_option_fails_current_owner_despite_valid_proof_binding(admitted):
    member = next(m for m in admitted[0].members if m.record_id == "crossed-option")
    env = member.decode_envelope()
    meta = normalize_observation_meta(
        env["metadata"], event_id=env["event_id"], raw_ref=env["raw_ref"],
        received_at=datetime.fromisoformat(env["simulated_received_at"]),
    ).value
    quote = normalize_quote_observation(
        member.decode_raw_body(), contract=_parse_contract_id(env["contract"]),
        meta=meta, event_id=env["event_id"],
    ).value
    result = api.assess_quote_coherence(
        evidence(admitted, "joint_crossed_option"), quote, admitted[2], manifest=admitted[0],
        decision_at=NOW, max_quote_age=timedelta(seconds=5),
    )
    assert len(result.matched_members) == 3
    assert result.reasons == ("option_quote_crossed",)
    assert not result.coherent
