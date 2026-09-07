"""Build downstream frozen artifacts only after actual source fixtures are finalized."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json

from options_lab.admission import verify_fixture_bundle
from options_lab.volume import fit_volume_baseline
from options_lab.volume_inputs import normalize_volume_partition, _declared_inputs
from build_fixture import _member, _payload_bytes


FIXTURE_ID = "p10b-volume-normalization-v1"
GENERATOR = "optionslab-volume-normalization-fixture-builder"
CUTOFF = datetime(2026, 9, 3, tzinfo=timezone.utc)
AVAILABLE = "2026-09-03T01:00:00Z"


def build_fixture() -> bytes:
    """Compute frozen claims from finalized actual catalog-backed upstream bytes.

    :returns: Independent B payload with truthful assembly time and no own-root claim.
    :raises AssertionError: If an actual upstream admission or intended fit fails.
    """
    folder = Path(__file__).parent / "fixtures"
    assembled = datetime.now(timezone.utc)
    source = json.loads((folder / "p10a-volume-training-v1.json").read_bytes())
    profile = dict(profile_id="volume-normalization", kind="feature_normalization", source=GENERATOR,
                   stream_id="volume-normalization", feed_class=None, fidelity=None,
                   availability_basis="measured", units={"volume": "shares"}, record_identity_rule="new_event_id_per_update")
    payload = {**source, "fixture_id": FIXTURE_ID, "generator_id": GENERATOR, "generator_version": "1",
               "assembled_at": assembled.isoformat(), "generator_source_ref": "OptionsLab/tests/build_volume_normalization_fixture.py",
               "modeled_source_profiles": [profile, {**profile, "profile_id": "wrong-source", "source": "other"}], "members": []}

    def raw_fit(fixture_id, partition_id):
        """Fit actual registered A using the same materializer, binder and moment owner."""
        manifest = verify_fixture_bundle(fixture_id, (folder / (fixture_id + ".json")).read_bytes(),
                                         event_id="normalization-build", received_at=assembled,
                                         raw_ref="synthetic://normalization-build/" + fixture_id).value
        assert manifest is not None
        member = next(m for m in manifest.members if m.record_id == partition_id)
        partition = normalize_volume_partition(member.decode_raw_body(), manifest=manifest, record_id=partition_id).value
        assert partition is not None
        groups, _ = _declared_inputs(partition, manifest, CUTOFF)
        fit = fit_volume_baseline(groups, CUTOFF, partition=partition, manifest=manifest)
        assert fit.baseline is not None, fit.reasons
        return dict(schema_version=1, training_fixture_id=fixture_id, training_payload_sha256=manifest.payload_sha256,
                    partition_record_id=partition_id, cutoff=CUTOFF.isoformat(), baseline_snapshot=fit.baseline.snapshot,
                    baseline_content_hash=fit.baseline.content_hash, available_at=AVAILABLE, availability_basis="measured")

    def add(name, raw, *, profile_id="volume-normalization", supersedes=None):
        """Retain one artifact and modeled receipt, separate from truthful assembly."""
        env = dict(event_id="event-" + name, raw_ref="synthetic://normalization/" + name,
                   simulated_received_at=AVAILABLE, stream_id="volume-normalization", receive_sequence=None,
                   supersedes_record_id=supersedes, contract=None, metadata=None)
        payload["members"].append(_member(name, "feature_normalization", profile_id, raw, env))

    good = raw_fit("p10a-volume-training-v1", "training")
    for name, fixture, partition in (
        ("good", "p10a-volume-training-v1", "training"),
        ("raw-omission", "p10a-volume-training-v1", "raw-volume"),
        ("empty", "p10a-volume-training-v1", "empty-bars"),
        ("all-raw-omitted", "p10b-volume-omissions-v1", "case-boolean"),
        ("numeric", "p10a-volume-moments-v1", "numeric"),
    ):
        add(name, raw_fit(fixture, partition))
    for name, changes in (
        ("unknown-availability", {"available_at": None}),
        ("future-availability", {"available_at": "2026-09-05T00:00:00Z"}),
        ("precutoff", {"available_at": "2026-09-02T23:59:59Z"}),
        ("at-cutoff", {"available_at": CUTOFF.isoformat()}),
        ("at-decision", {"available_at": "2026-09-04T14:05:00Z"}),
        ("malformed-time", {"available_at": "tomorrow"}),
        ("assumed-basis", {"availability_basis": "assumed"}),
        ("invalid-basis", {"availability_basis": "modeled"}),
        ("wrong-root", {"training_payload_sha256": "0" * 64}),
        ("wrong-fixture", {"training_fixture_id": "other"}),
        ("wrong-hash", {"baseline_content_hash": "0" * 64}),
        ("wrong-cutoff", {"cutoff": "2026-09-03T00:00:01Z"}),
        ("unknown-partition", {"partition_record_id": "absent"}),
        ("wrong-kind", {"partition_record_id": "bar-2026-08-06-35"}),
        ("failed-binding", {"partition_record_id": "heldout"}),
    ):
        add(name, {**deepcopy(good), **changes})
    raw = deepcopy(good)
    del raw["available_at"]
    add("missing-availability", raw)
    add("profile-mismatch", deepcopy(good), profile_id="wrong-source")
    add("superseded", deepcopy(good), supersedes="good")
    for field, value in (("mean", "999"), ("population_stddev", "99"), ("sample_count", 21),
                         ("contributing_sessions", ["2026-08-01"]), ("reasons", ["population_stddev_zero"])):
        raw = deepcopy(good)
        raw["baseline_snapshot"]["buckets"][-1][field] = value
        add("tamper-" + field, raw)
    for field in ("normalization_id", "numeric_id", "training_input_hash"):
        raw = deepcopy(good)
        raw["baseline_snapshot"][field] = "0" * 64
        add("tamper-" + field, raw)
    raw = raw_fit("p10a-volume-moments-v1", "numeric")
    raw["partition_record_id"] = "product-bound"
    add("arithmetic-failure", raw)
    return _payload_bytes(payload)


if __name__ == "__main__":
    target = Path(__file__).parent / "fixtures" / (FIXTURE_ID + ".json")
    target.write_bytes(build_fixture())
    print(target)
