"""Local-development helper (NOT registered when REVGATE_ENV=prod or DEV_OUTBOX=false).

GET /v1/dev/outbox — read the `rg:dev:outbox` messages (magic links, OTP codes, inbox notifications)
so the web app can offer one-click "dev" login / OTP fill while no mail/SMS provider is wired.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db, get_redis
from ..errors import not_found
from ..mailer import OUTBOX_KEY
from ..models import ReviewLink
from ..security import sha256_hex

router = APIRouter(prefix="/v1/dev", tags=["dev"])


@router.get("/outbox")
async def outbox(to: str | None = Query(default=None), kind: str | None = Query(default=None),
                 link_token: str | None = Query(default=None), limit: int = Query(default=5, ge=1, le=50),
                 redis: Redis = Depends(get_redis), db: AsyncSession = Depends(get_db)):
    if link_token:  # the public reviewer page only knows its link token, not the full contact
        link = (await db.execute(select(ReviewLink).where(ReviewLink.token_hash == sha256_hex(link_token)))).scalar_one_or_none()
        if link is None:
            raise not_found("link")
        to = link.recipient_contact
    out = []
    for raw in await redis.lrange(OUTBOX_KEY, 0, 499):
        msg = json.loads(raw)
        if (to is None or msg["to"] == to) and (kind is None or msg["kind"] == kind):
            out.append(msg)
            if len(out) >= limit:
                break
    return {"messages": out}
