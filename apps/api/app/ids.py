"""UUIDv7 (RFC 9562, method 1: 12-bit counter in rand_a) — app-generated primary keys (blueprint §3).

Dependency-free; kept in sync with workers/cad/src/ids.py.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from datetime import datetime

_lock = threading.Lock()
_last_ms = 0
_seq = 0


def uuid7() -> uuid.UUID:
    """Time-ordered UUIDv7, strictly monotonic within this process."""
    global _last_ms, _seq
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms = ms
            _seq = secrets.randbits(10)  # random start, leaves headroom for the counter
        else:
            _seq += 1
            if _seq > 0xFFF:  # counter overflow: borrow the next millisecond
                _last_ms += 1
                _seq = 0
        value = (_last_ms << 80) | (0x7 << 76) | (_seq << 64) | (0b10 << 62) | secrets.randbits(62)
    return uuid.UUID(int=value)


def uuid7_floor(ts: datetime) -> uuid.UUID:
    """Smallest UUIDv7 for the given instant — `id >= uuid7_floor(t)` selects rows created at/after t."""
    ms = int(ts.timestamp() * 1000)
    return uuid.UUID(int=(ms << 80) | (0x7 << 76) | (0b10 << 62))


def uuid7_time(u: uuid.UUID) -> datetime:
    from datetime import timezone

    return datetime.fromtimestamp((u.int >> 80) / 1000.0, tz=timezone.utc)
