"""Reverify one retained bundle through its original source and current runtime."""

from .admission import VerifiedFixtureManifest, VerifiedFixtureMember, verify_fixture_bundle
from .bundle_manifest_inputs import normalize_bundle_manifest
from .bundles import VerifiedBundle, verify_bundle
from .calibration import _normalization_shape
from .calibration_inputs import _matches_retained, _retained_fixture_shape
from .config import ExecutionPolicy, StrategyConfig
from .context_recheck import _config
from .feature_vector import EXACT_VWAP_SPEC, CLOSE_VOLUME_PROXY_SPEC
from .runtime import measure_runtime
from .volume_normalization import FeatureNormalization


def _fresh_bundle(bundle: VerifiedBundle) -> tuple[VerifiedBundle | None, str | None, object | None]:
    """Return freshly proven A0/A2 evidence, a bounded reason and reached rejection.

    :param bundle: Retained actual A2 bundle and original input owners.
    :returns: Fresh bundle or failure reason and reached lower rejection.
    :raises TypeError: If a top-level trusted owner has the wrong exact type.
    """
    if type(bundle) is not VerifiedBundle:
        raise TypeError("bundle requires exact VerifiedBundle")
    try:
        fixture, member, upstream, normalization, spec = (bundle.original_fixture, bundle.member,
            bundle.supplied_upstream_fixtures, bundle.supplied_normalization, bundle.spec)
        if (type(fixture) is not VerifiedFixtureManifest
                or type(member) is not VerifiedFixtureMember or type(member.record_id) is not str
                or type(upstream) is not tuple
                or len(upstream) > 64
                or any(type(root) is not VerifiedFixtureManifest for root in upstream)
                or type(bundle.config) is not StrategyConfig
                or type(bundle.config.execution) is not ExecutionPolicy
                or not (spec is None or spec is EXACT_VWAP_SPEC or spec is CLOSE_VOLUME_PROXY_SPEC)
                or (normalization is not None and type(normalization) is not FeatureNormalization)):
            return None, "retained_content_mismatch", None
        if (not _retained_fixture_shape(fixture)
                or any(not _retained_fixture_shape(root) for root in upstream)
                or (normalization is not None and not _normalization_shape(normalization))):
            return None, "retained_content_mismatch", None
        config = _config(bundle.config)
        if config is None:
            return None, "retained_content_mismatch", None
    except AttributeError:
        return None, "retained_content_mismatch", None
    root = verify_fixture_bundle(fixture.fixture_id, fixture.payload_bytes,
        event_id=fixture.event_id, raw_ref=fixture.raw_ref, received_at=fixture.received_at)
    if root.value is None:
        return None, "fresh_bundle_rejected", root.rejection
    fresh_member = next((item for item in root.value.members if item.record_id == member.record_id), None)
    if fresh_member is None or fresh_member.kind != "model_bundle":
        return None, "fresh_bundle_rejected", None
    body = fresh_member.decode_raw_body()
    if type(body) is not dict or type(body.get("model_utf8")) is not str:
        return None, "fresh_bundle_rejected", None
    receipt = dict(event_id=root.value.event_id, raw_ref=root.value.raw_ref, received_at=root.value.received_at)
    inspected = normalize_bundle_manifest(body.get("manifest"), **receipt)
    runtime = measure_runtime()
    if inspected.value is None or runtime.value is None:
        return None, "fresh_bundle_rejected", inspected.rejection if inspected.value is None else runtime.rejection
    try:
        model_bytes = body["model_utf8"].encode("utf-8")
    except UnicodeEncodeError:
        return None, "fresh_bundle_rejected", None
    checked = verify_bundle(inspected.value, model_bytes, fixture=fixture, spec=spec,
        normalization=normalization, config=config, runtime=runtime.value, upstream_fixtures=upstream)
    if checked.value is None:
        return None, "fresh_bundle_rejected", checked.rejection
    if not _matches_retained(bundle, checked.value, set()):
        return checked.value, "retained_content_mismatch", None
    return checked.value, None, None
