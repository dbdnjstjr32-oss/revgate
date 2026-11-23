"""Redis Pub/Sub channels shared with the worker (workers/cad/src/tasks.py) + SSE framing."""

from __future__ import annotations

import json
from typing import Any

TERMINAL_EVENTS = {"ready", "needs_align", "failed"}
STATUS_EVENT = {"READY": "ready", "NEEDS_ALIGN": "needs_align", "FAILED": "failed"}


def diff_channel(diff_id) -> str:
    return f"rg:events:diff:{diff_id}"


def rev_channel(rev_id) -> str:
    return f"rg:events:rev:{rev_id}"


def sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"
