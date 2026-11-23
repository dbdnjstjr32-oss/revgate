"""Outgoing magic links / OTP codes / inbox notifications.

No provider is wired yet: messages are logged, and with DEV_OUTBOX=true (default outside prod) also
pushed to the Redis list `rg:dev:outbox` — the local-development / test hook used by the e2e suite.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from .config import Settings

log = logging.getLogger("revgate.mailer")
OUTBOX_KEY = "rg:dev:outbox"


async def send(redis: Redis, settings: Settings, *, to: str, kind: str, subject: str, body: str,
               data: dict[str, Any] | None = None) -> None:
    msg = {"to": to, "kind": kind, "subject": subject, "body": body, "data": data or {},
           "at": datetime.now(timezone.utc).isoformat()}
    log.info("outgoing %s to %s: %s", kind, to, subject)
    if settings.dev_outbox and settings.env != "prod":
        await redis.lpush(OUTBOX_KEY, json.dumps(msg, ensure_ascii=False))
        await redis.ltrim(OUTBOX_KEY, 0, 499)
