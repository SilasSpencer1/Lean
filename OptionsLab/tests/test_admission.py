from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
from inspect import signature
import json
from pathlib import Path

import pytest

import options_lab.admission as api
from options_lab._input_parsing import _parse_contract_id
from options_lab.greek_inputs import normalize_greek_observation
from options_lab.greeks import FIXTURE_GREEK_METHOD, assess_greek_readiness
from options_lab.observations import normalize_observation_meta
from options_lab.quote_content import identify_quote_content
from options_lab.quote_inputs import normalize_quote_observation
from options_lab.underlying_inputs import normalize_underlying_quote


UTC = timezone.utc
FIXTURE_ID = "p08a-greek-ready-v1"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / f"{FIXTURE_ID}.json"
VERIFIED_AT = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)


def verify(payload: bytes | None = None, fixture_id: str = FIXTURE_ID) -> api.FixtureVerification:
    return api.verify_fixture_bundle(
        fixture_id,
        FIXTURE_PATH.read_bytes() if payload is None else payload,
        event_id="fixture-verification-1",
        raw_ref="test-fixture://p08a-greek-ready-v1",
        received_at=VERIFIED_AT,
    )


def admitted() -> api.VerifiedFixtureManifest:
    result = verify()
    assert result.rejection is None and result.value is not None
    return result.value


def payload_object() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_bytes())


def payload_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


def schema_failure(raw: object) -> tuple[str, str]:
    descriptor = api._CATALOG.descriptors[0]
    with pytest.raises(api._AdmissionFailure) as failure:
        api._build_manifest(
            descriptor, raw, FIXTURE_PATH.read_bytes(),
            descriptor.expected_payload_sha256, "event", "raw", VERIFIED_AT,
        )
    return failure.value.args


def raw_body_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_known_actual_bytes_admit_as_permanently_synthetic_evidence() -> None:
    payload = FIXTURE_PATH.read_bytes()
    result = verify(payload)

    assert type(result) is api.FixtureVerification
    assert result.rejection is None and type(result.value) is api.VerifiedFixtureManifest
    manifest = result.value
    assert manifest.payload_bytes == payload
    assert manifest.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert manifest.fixture_id == FIXTURE_ID
    assert manifest.schema_version == manifest.normalization_version == 1
    assert manifest.generator_id == "optionslab-test-fixture-builder"
    assert manifest.generator_version == "1"
    assert manifest.generator_source_ref == "OptionsLab/tests/build_fixture.py"
    assert manifest.received_at == VERIFIED_AT
    assert manifest.event_id == "fixture-verification-1"
    assert manifest.raw_ref == "test-fixture://p08a-greek-ready-v1"
    assert manifest.origin == "synthetic"
    assert manifest.fidelity_tier == 0
    assert manifest.permitted_use == "core_fixture"
    assert manifest.operational_allowed is False
    assert manifest.economic_allowed is False
    assert manifest.greek_method is FIXTURE_GREEK_METHOD
    assert manifest.coherence_protocol_ids == ()
    assert manifest.tick_definition_ids == ()


def test_members_are_byte_backed_immutable_and_decode_fresh_objects() -> None:
    manifest = admitted()

    assert tuple(member.kind for member in manifest.members) == (
        "option_quote", "underlying_quote", "greek_observation",
    )
    assert len({member.record_id for member in manifest.members}) == 3
    for member in manifest.members:
        first_body = member.decode_raw_body()
        second_body = member.decode_raw_body()
        first_envelope = member.decode_envelope()
        assert type(first_body) is dict and first_body == second_body
        assert first_body is not second_body
        assert hashlib.sha256(member.raw_body_bytes).hexdigest() == member.raw_hash
        first_body["caller_mutation"] = True
        first_envelope["caller_mutation"] = True
        assert "caller_mutation" not in member.decode_raw_body()
        assert "caller_mutation" not in member.decode_envelope()

    first_profiles = manifest.decode_modeled_source_profiles()
    second_profiles = manifest.decode_modeled_source_profiles()
    first_profiles[0]["source"] = "caller-change"
    assert first_profiles is not second_profiles
    assert second_profiles[0]["source"] != "caller-change"

    with pytest.raises(FrozenInstanceError):
        manifest.fixture_id = "changed"
    with pytest.raises(TypeError):
        api.VerifiedFixtureManifest()
    with pytest.raises(TypeError):
        api.VerifiedFixtureMember()


def test_actual_fixture_normalizes_through_current_owners_and_is_greek_ready() -> None:
    manifest = admitted()
    option_member, underlying_member, greek_member = manifest.members
    option_envelope = option_member.decode_envelope()
    underlying_envelope = underlying_member.decode_envelope()
    greek_envelope = greek_member.decode_envelope()

    option_contract = _parse_contract_id(option_envelope["contract"])
    option_meta = normalize_observation_meta(
        option_envelope["metadata"], event_id=option_envelope["event_id"],
        raw_ref=option_envelope["raw_ref"],
        received_at=datetime.fromisoformat(option_envelope["simulated_received_at"]),
    )
    underlying_meta = normalize_observation_meta(
        underlying_envelope["metadata"], event_id=underlying_envelope["event_id"],
        raw_ref=underlying_envelope["raw_ref"],
        received_at=datetime.fromisoformat(underlying_envelope["simulated_received_at"]),
    )
    assert option_meta.rejection is None and option_meta.value is not None
    assert underlying_meta.rejection is None and underlying_meta.value is not None
    option = normalize_quote_observation(
        option_member.decode_raw_body(), contract=option_contract,
        meta=option_meta.value, event_id=option_envelope["event_id"],
    )
    underlying = normalize_underlying_quote(
        underlying_member.decode_raw_body(), meta=underlying_meta.value,
        event_id=underlying_envelope["event_id"],
    )
    assert option.rejection is None and option.value is not None
    assert underlying.rejection is None and underlying.value is not None

    greek = normalize_greek_observation(
        greek_member.decode_raw_body(), contract=_parse_contract_id(greek_envelope["contract"]),
        event_id=greek_envelope["event_id"], raw_ref=greek_envelope["raw_ref"],
        received_at=datetime.fromisoformat(greek_envelope["simulated_received_at"]),
    )
    assert greek.rejection is None and greek.value is not None
    readiness = assess_greek_readiness(
        greek.value, option.value, underlying.value, method=manifest.greek_method,
        decision_at=datetime.fromisoformat(greek_envelope["simulated_received_at"]),
    )
    assert readiness.status == "READY"
    assert option_member.raw_hash != identify_quote_content(option.value).content_hash
    assert underlying_member.raw_hash != identify_quote_content(underlying.value).content_hash
    assert manifest.assembled_at != greek.value.as_of
    assert manifest.received_at != greek.value.received_at


def test_payload_root_rejects_every_tampering_domain_even_with_recomputed_member_hash() -> None:
    mutations = []
    for path, value in (
        (("fixture_id",), "caller-fixture"),
        (("generator_id",), "caller-generator"),
        (("origin",), "external"),
        (("permitted_use",), "operational"),
        (("operational_allowed",), True),
        (("members", 0, "envelope", "raw_ref"), "external://changed"),
    ):
        raw = payload_object()
        target = raw
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        mutations.append(payload_bytes(raw))

    raw = payload_object()
    raw["members"][0]["raw_body"]["ask"] = "5.20"
    raw["members"][0]["raw_hash"] = raw_body_hash(raw["members"][0]["raw_body"])
    mutations.append(payload_bytes(raw))

    for payload in mutations:
        rejection = verify(payload).rejection
        assert rejection is not None
        assert (rejection.field, rejection.code) == ("payload", "hash_mismatch")
        assert rejection.reasons == ("hash_mismatch",)


def test_unknown_catalog_id_and_whitespace_change_are_rejected_safely() -> None:
    unknown = verify(fixture_id="not-registered").rejection
    changed = verify(FIXTURE_PATH.read_bytes() + b" ").rejection

    assert unknown is not None and (unknown.field, unknown.code) == (
        "fixture_id", "unknown_fixture",
    )
    assert changed is not None and (changed.field, changed.code) == (
        "payload", "hash_mismatch",
    )
    assert FIXTURE_PATH.read_bytes() not in repr(changed).encode()


def test_public_verifier_has_no_catalog_path_or_hash_override() -> None:
    assert tuple(signature(api.verify_fixture_bundle).parameters) == (
        "fixture_id", "payload", "event_id", "raw_ref", "received_at",
    )
    assert signature(api.verify_fixture_bundle).parameters["event_id"].kind.name == "KEYWORD_ONLY"
    assert not any(name in signature(api.verify_fixture_bundle).parameters for name in (
        "catalog", "catalog_path", "expected_hash", "expected_payload_sha256",
    ))


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        (b"\xff", "invalid_utf8"),
        (b'{"x":1,"x":2}', "duplicate_key"),
        (b'{"x":NaN}', "nonfinite_number"),
        (b'{"x":1e400}', "nonfinite_number"),
        (b'{', "invalid_json"),
    ),
)
def test_strict_json_boundary_rejects_malformed_input(payload: bytes, code: str) -> None:
    value, observed = api._decode_json(payload)
    assert value is None
    assert observed == code


def test_catalog_parser_rejects_bad_schema_and_duplicate_ids_without_import_crash() -> None:
    good = payload_object()
    descriptor = {
        "fixture_id": FIXTURE_ID,
        "generator_id": good["generator_id"],
        "generator_version": good["generator_version"],
        "payload_schema_version": 1,
        "normalization_version": 1,
        "expected_payload_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
    }
    duplicate = payload_bytes({"catalog_schema_version": 1, "fixtures": [descriptor, descriptor]})
    wrong_version = payload_bytes({"catalog_schema_version": 2, "fixtures": [descriptor]})

    assert api._parse_catalog(duplicate).error_code == "catalog_invalid"
    assert api._parse_catalog(wrong_version).error_code == "catalog_invalid"


@pytest.mark.parametrize(
    ("change", "expected"),
    (
        (lambda raw: raw.update(extra=True), ("$", "unknown_fields")),
        (lambda raw: raw.pop("assembled_at"), ("assembled_at", "missing")),
        (lambda raw: raw.update(schema_version=True), ("schema_version", "invalid_type")),
        (
            lambda raw: raw["modeled_source_profiles"][0].update(units=[]),
            ("modeled_source_profiles[0].units", "expected_exact_dict"),
        ),
        (
            lambda raw: raw["members"][0].update(raw_body=[]),
            ("members[0].raw_body", "expected_exact_dict"),
        ),
        (
            lambda raw: raw["members"][0]["envelope"].update(contract=[]),
            ("members[0].envelope.contract", "expected_exact_dict"),
        ),
        (
            lambda raw: raw["members"][0]["envelope"].update(metadata=[]),
            ("members[0].envelope.metadata", "expected_exact_dict"),
        ),
        (
            lambda raw: raw["members"][0]["envelope"].update(receive_sequence=True),
            ("members[0].envelope.receive_sequence", "invalid_type"),
        ),
        (
            lambda raw: raw["definitions"]["greek_method"].update(method_spec_hash="0" * 64),
            ("definitions.greek_method", "invalid_value"),
        ),
        (
            lambda raw: raw["definitions"].update(coherence_protocol_ids=["future"]),
            ("definitions.coherence_protocol_ids", "invalid_value"),
        ),
    ),
)
def test_closed_payload_framing_has_stable_safe_failures(change, expected) -> None:
    raw = payload_object()
    change(raw)
    assert schema_failure(raw) == expected


def test_duplicate_and_cross_reference_integrity_failures_are_distinct() -> None:
    raw = payload_object()
    raw["members"][1]["record_id"] = raw["members"][0]["record_id"]
    assert schema_failure(raw) == ("members[1].record_id", "duplicate_id")

    raw = payload_object()
    raw["members"][0]["profile_id"] = "unknown"
    assert schema_failure(raw) == ("members[0].profile_id", "unknown_profile")

    raw = payload_object()
    raw["members"][0]["profile_id"] = "greek-profile-1"
    assert schema_failure(raw) == ("members[0].profile_id", "kind_mismatch")

    raw = payload_object()
    raw["members"][0]["envelope"]["stream_id"] = "other-stream"
    assert schema_failure(raw) == ("members[0].envelope.stream_id", "stream_mismatch")

    raw = payload_object()
    raw["members"][0]["raw_hash"] = "0" * 64
    assert schema_failure(raw) == ("members[0].raw_hash", "hash_mismatch")


def test_profile_canonicalization_resource_failure_is_owned(monkeypatch) -> None:
    original = api._canonical_bytes

    def fail_profiles(value: object) -> bytes:
        if type(value) is list:
            raise MemoryError
        return original(value)

    monkeypatch.setattr(api, "_canonical_bytes", fail_profiles)
    assert schema_failure(payload_object()) == (
        "modeled_source_profiles", "resource_limit",
    )


def test_trusted_arguments_require_exact_types_and_aware_receipt() -> None:
    payload_rejection = verify(bytearray(FIXTURE_PATH.read_bytes())).rejection
    assert payload_rejection is not None
    assert (payload_rejection.field, payload_rejection.code) == (
        "payload", "invalid_type",
    )
    with pytest.raises(TypeError):
        api.verify_fixture_bundle(
            FIXTURE_ID, FIXTURE_PATH.read_bytes(), event_id=1,
            raw_ref="raw", received_at=VERIFIED_AT,
        )
    with pytest.raises(ValueError):
        api.verify_fixture_bundle(
            FIXTURE_ID, FIXTURE_PATH.read_bytes(), event_id="event",
            raw_ref="raw", received_at=datetime(2026, 9, 6),
        )


class StringSubclass(str):
    pass


class BytesSubclass(bytes):
    pass


class Hostile:
    def __repr__(self) -> str:
        raise AssertionError("hostile repr must not be evaluated")

    def __str__(self) -> str:
        raise AssertionError("hostile string conversion must not be evaluated")


@pytest.mark.parametrize(
    ("fixture_id", "code"),
    (
        (None, "invalid_type"), (True, "invalid_type"),
        (StringSubclass(FIXTURE_ID), "invalid_type"),
        (Hostile(), "invalid_type"), ("", "invalid_value"),
    ),
)
def test_external_fixture_id_failures_are_owned(fixture_id: object, code: str) -> None:
    result = api.verify_fixture_bundle(
        fixture_id, FIXTURE_PATH.read_bytes(), event_id="event", raw_ref="raw",
        received_at=VERIFIED_AT,
    )
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == ("fixture_id", code)


@pytest.mark.parametrize(
    "payload", [None, True, bytearray(), BytesSubclass(b"fixture"), Hostile()]
)
def test_external_payload_requires_exact_bytes(payload: object) -> None:
    result = api.verify_fixture_bundle(
        FIXTURE_ID, payload, event_id="event", raw_ref="raw", received_at=VERIFIED_AT,
    )
    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (
        "payload", "invalid_type",
    )


def test_registered_payload_missing_fixture_id_returns_safe_rejection(monkeypatch) -> None:
    raw = payload_object()
    del raw["fixture_id"]
    payload = payload_bytes(raw)
    descriptor = api._FixtureDescriptor(
        FIXTURE_ID, raw["generator_id"], raw["generator_version"], 1, 1,
        hashlib.sha256(payload).hexdigest(),
    )
    monkeypatch.setattr(
        api, "_CATALOG", api._CatalogState((descriptor,), "0" * 64, None)
    )

    result = api.verify_fixture_bundle(
        FIXTURE_ID, payload, event_id="event", raw_ref="raw",
        received_at=VERIFIED_AT,
    )

    assert result.value is None and result.rejection is not None
    assert (result.rejection.field, result.rejection.code) == (
        "fixture_id", "missing",
    )


def test_trusted_envelope_is_validated_before_untrusted_bundle_inputs() -> None:
    with pytest.raises(TypeError):
        api.verify_fixture_bundle(
            None, None, event_id=object(), raw_ref="raw", received_at=VERIFIED_AT,
        )


def test_result_constructor_enforces_exact_xor_and_rejection_is_immutable() -> None:
    manifest = admitted()
    rejection = verify(fixture_id="unknown").rejection
    assert rejection is not None

    with pytest.raises(ValueError):
        api.FixtureVerification()
    with pytest.raises(ValueError):
        api.FixtureVerification(manifest, rejection)
    with pytest.raises(FrozenInstanceError):
        rejection.code = "changed"
    with pytest.raises(ValueError):
        api.FixtureInputRejection(
            "event", VERIFIED_AT, "raw", "arbitrary", "hash_mismatch"
        )
    with pytest.raises(ValueError):
        api.FixtureInputRejection(
            "event", VERIFIED_AT, "raw", "payload", "unknown_profile"
        )
