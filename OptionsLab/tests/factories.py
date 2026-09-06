"""Reusable synthetic evidence factories for OptionsLab tests."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from options_lab.bars import UnderlyingBar
from options_lab.observations import ObservationMeta
from options_lab.sessions import ExchangeSession


UTC = timezone.utc
OPEN = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
CLOSE = datetime(2026, 9, 4, 20, 0, tzinfo=UTC)


def session(**changes: object) -> ExchangeSession:
    """Return synthetic calendar evidence without claiming source admission."""
    values = {
        "calendar": "XNYS",
        "session_date": date(2026, 9, 4),
        "kind": "regular",
        "opens_at": OPEN,
        "closes_at": CLOSE,
        "source": "synthetic-calendar",
        "provider_record_id": "XNYS-2026-09-04",
        "source_version": "synthetic-calendar-v1",
        "available_at": OPEN,
        "availability_basis": "measured",
        "received_at": OPEN,
        "raw_ref": "synthetic://calendar/2026-09-04",
        "fidelity": "synthetic",
    }
    values.update(changes)
    return ExchangeSession(**values)


def bar(minute: int = 0, **changes: object) -> UnderlyingBar:
    """Return one synthetic completed bar with explicit causal ordering."""
    start = OPEN + timedelta(minutes=minute)
    available = start + timedelta(minutes=1, seconds=1)
    revision = changes.pop("revision_id", "r1")
    provider_record_id = changes.pop("provider_record_id", f"bar-{minute}")
    meta_changes = changes.pop("meta_changes", {})
    meta_values = {
        "source": "synthetic-bars",
        "provider_record_id": provider_record_id,
        "raw_ref": f"synthetic://bars/{provider_record_id}/{revision}",
        "feed_class": "delayed",
        "fidelity": "synthetic",
        "kind": "interval",
        "event_at": None,
        "available_at": available,
        "received_at": available + timedelta(seconds=1),
        "availability_basis": "assumed",
        "availability_evidence_ref": "synthetic-bar-clock-v1",
        "interval_start": start,
        "interval_end": start + timedelta(minutes=1),
        "is_fill_forward": False,
        "quality_flags": (),
    }
    meta_values.update(meta_changes)
    values = {
        "symbol": "SPY",
        "meta": ObservationMeta(**meta_values),
        "close_price": Decimal(f"{10000 + minute}e-2"),
        "volume": Decimal("10"),
        "vwap_numerator": Decimal("1000"),
        "vwap_denominator": Decimal("10"),
        "price_basis": "raw",
        "volume_definition_id": "synthetic-volume-v1",
        "vwap_definition_id": "synthetic-vwap-v1",
        "revision_id": revision,
        "supersedes_revision_id": None,
        "receive_sequence": minute + 1,
    }
    values.update(changes)
    return UnderlyingBar(**values)
