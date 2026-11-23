"""ARQ producer side — job names / queue names are the contract with workers/cad/src/worker.py."""

from __future__ import annotations

import uuid

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

Q_INGEST = "q-ingest"
Q_DIFF_BATCH = "q-diff-batch"
Q_DIFF_INTERACTIVE = "q-diff-interactive"
Q_PURGE = "q-purge"


async def make_pool(redis_url: str) -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(redis_url))


async def _enqueue(arq: ArqRedis, function: str, *args, queue: str, kind: str) -> str:
    # ARQ silently drops a job whose id is already known (queued, running or result kept), so every
    # enqueue gets a fully random suffix — a recompute of the same diff run is a new job.
    job_id = f"{kind}:{args[0]}:{uuid.uuid4().hex}"
    job = await arq.enqueue_job(function, *args, _queue_name=queue, _job_id=job_id)
    if job is None:
        raise RuntimeError(f"ARQ refused job {job_id}")
    return job_id


async def enqueue_ingest(arq: ArqRedis, revision_id) -> str:
    return await _enqueue(arq, "ingest_revision", str(revision_id), queue=Q_INGEST, kind="ingest")


async def enqueue_diff(arq: ArqRedis, diff_run_id, *, interactive: bool = False) -> str:
    return await _enqueue(arq, "compute_diff", str(diff_run_id), queue=Q_DIFF_INTERACTIVE if interactive else Q_DIFF_BATCH,
                          kind="diff")


async def enqueue_purge(arq: ArqRedis, revision_id, reason: str) -> str:
    return await _enqueue(arq, "purge_revision", str(revision_id), reason, queue=Q_PURGE, kind="purge")
