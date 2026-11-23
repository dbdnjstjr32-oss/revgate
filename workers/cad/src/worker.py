"""ARQ worker entrypoint — four dedicated queues in one lightweight process (PRODUCT_SPEC §3.4).

    python -m src.worker

  q-ingest            ingest_revision     parse + tessellate + analytical sidecar (tmpfs)
  q-diff-batch        compute_diff        auto-align + 3-stage diff (15 s Boolean budget)
  q-diff-interactive  compute_diff        3-2-1 manual realign — own worker slot, never queued behind batch
  q-purge             purge_revision      zero-retention / TTL raw-STEP destruction (+ cron housekeeping)

Each queue gets its own arq.Worker (own concurrency), sharing one asyncpg pool and one S3 client.
The heavy lifting happens in isolated subprocesses (runner.run_job), so this process stays small and
survives OCCT crashes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

import asyncpg
from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import Worker, func

from . import tasks
from .storage import S3Settings, Storage

log = logging.getLogger("revgate.worker")


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def build_shared() -> dict:
    dsn = os.environ.get("REVGATE_DB_DSN", "postgresql://revgate_app:revgate_app_dev@postgres:5432/revgate")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "6")), init=_init_conn)
    scratch = Path(os.environ.get("REVGATE_SCRATCH_DIR", "/tmp/revgate_scratch")) / "queue"
    scratch.mkdir(parents=True, exist_ok=True)
    return {"db": pool, "storage": Storage(S3Settings.from_env()), "scratch": str(scratch)}


def build_workers(shared: dict, redis_settings: RedisSettings) -> list[Worker]:
    async def on_startup(ctx: dict) -> None:
        ctx.update(shared)

    job_timeout = int(os.environ.get("ARQ_JOB_TIMEOUT_SEC", "180"))
    common = dict(redis_settings=redis_settings, on_startup=on_startup, handle_signals=False, job_timeout=job_timeout,
                  keep_result=3600, max_tries=1, poll_delay=0.2)
    return [
        Worker(functions=[func(tasks.ingest_revision, name="ingest_revision")], queue_name=tasks.Q_INGEST,
               max_jobs=int(os.environ.get("INGEST_CONCURRENCY", "1")), **common),
        Worker(functions=[func(tasks.compute_diff, name="compute_diff")], queue_name=tasks.Q_DIFF_BATCH,
               max_jobs=int(os.environ.get("DIFF_BATCH_CONCURRENCY", "1")), **common),
        Worker(functions=[func(tasks.compute_diff, name="compute_diff")], queue_name=tasks.Q_DIFF_INTERACTIVE,
               max_jobs=int(os.environ.get("DIFF_INTERACTIVE_CONCURRENCY", "1")), **common),
        Worker(functions=[func(tasks.purge_revision, name="purge_revision")], queue_name=tasks.Q_PURGE, max_jobs=4,
               cron_jobs=[cron(tasks.housekeeping, minute=set(range(0, 60, 10)), run_at_startup=False)], **common),
    ]


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    shared = await build_shared()
    workers = build_workers(shared, redis_settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    runs = [asyncio.create_task(w.async_run(), name=f"arq:{w.queue_name}") for w in workers]
    log.info("RevGate CAD worker up: queues %s", ", ".join(w.queue_name for w in workers))
    done, _ = await asyncio.wait([*runs, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task in runs and task.exception():
            log.error("worker %s crashed: %r", task.get_name(), task.exception())
    for w in workers:
        await w.close()
    for r in runs:
        r.cancel()
    await shared["db"].close()


if __name__ == "__main__":
    asyncio.run(main())
