"""UUIDv7 primary keys (blueprint §3) — RFC 9562 layout, monotonic order, time-floor queries."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.ids import uuid7, uuid7_floor, uuid7_time


def test_uuid7_layout_and_monotonic_order():
    ids = [uuid7() for _ in range(5000)]
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in ids)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)  # strictly increasing within the process
    assert [u.bytes for u in ids] == sorted(u.bytes for u in ids)  # Postgres compares uuid bytewise


def test_uuid7_floor_selects_by_creation_time():
    now = datetime.now(timezone.utc)
    u = uuid7()
    assert abs((uuid7_time(u) - now).total_seconds()) < 1.0
    assert uuid7_floor(now - timedelta(seconds=1)) <= u
    assert uuid7_floor(now + timedelta(seconds=1)) > u
