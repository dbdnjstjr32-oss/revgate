"""ARQ task bodies (PRODUCT_SPEC §3.4, IMPLEMENTATION_BLUEPRINT §3 state machines).

Every CAD computation still goes through runner.run_job -> one isolated subprocess per job; the
ARQ process itself never imports OCP. Flow per job:

    S3 raw STEP -> tmpfs scratch -> run_job(subprocess, 60 s) -> Brotli upload (Content-Encoding: br)
    -> Postgres state / diff_items / error_code -> Redis Pub/Sub event -> (ZERO_RETENTION) q-purge

Queues: q-ingest (ingest_revision), q-diff-batch + q-diff-interactive (compute_diff),
        q-purge (purge_revision, cron housekeeping).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError

from .ids import uuid7
from .runner import run_job

log = logging.getLogger("revgate.worker")

Q_INGEST = "q-ingest"
Q_DIFF_BATCH = "q-diff-batch"
Q_DIFF_INTERACTIVE = "q-diff-interactive"
Q_PURGE = "q-purge"
SYSTEM_ACTOR = "system:cad-worker"


def rev_channel(rev_id: str | UUID) -> str:
    return f"rg:events:rev:{rev_id}"


def diff_channel(diff_id: str | UUID) -> str:
    return f"rg:events:diff:{diff_id}"


def _sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def publish(ctx: dict, channel: str, event: str, data: dict[str, Any]) -> None:
    await ctx["redis"].publish(channel, json.dumps({"event": event, "data": data}, default=str))


async def audit(conn, workspace_id, entity_type: str, entity_id, action: str, payload: dict[str, Any], actor: str = SYSTEM_ACTOR) -> None:
    await conn.execute(
        "INSERT INTO audit_events (workspace_id, entity_type, entity_id, actor_contact, action, payload_sha256)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        workspace_id, entity_type, entity_id, actor, action, _sha(payload),
    )


def _job_dir(ctx: dict, kind: str, ident: str) -> Path:
    d = Path(ctx["scratch"]) / f"{kind}-{ident}-{uuid.uuid4().hex[:12]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _download(ctx: dict, key: str, dest: Path) -> None:
    s = ctx["storage"]
    try:
        await asyncio.to_thread(s.download, s.s.bucket_raw, key, dest)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise FileNotFoundError(key) from exc
        raise


# ============================================================================================
# q-ingest
# ============================================================================================
async def ingest_revision(ctx: dict, revision_id: str) -> dict[str, Any]:
    rid = UUID(revision_id)
    db = ctx["db"]
    row = await db.fetchrow(
        "SELECT r.id, r.part_id, r.status, r.raw_r2_key, r.file_sha256, p.workspace_id"
        " FROM revisions r JOIN parts p ON p.id = r.part_id WHERE r.id = $1", rid)
    if row is None:
        return {"status": "MISSING"}
    if row["status"] not in ("QUEUED", "PARSING"):
        return {"status": "SKIPPED", "reason": f"revision is {row['status']}"}
    await db.execute("UPDATE revisions SET status = 'PARSING' WHERE id = $1", rid)
    await publish(ctx, rev_channel(rid), "progress", {"revision_id": rid, "status": "PARSING"})

    job_dir = _job_dir(ctx, "ingest", revision_id)
    try:
        if not row["raw_r2_key"]:
            return await _fail_revision(ctx, row, "RAW_FILE_NOT_FOUND")
        raw = job_dir / "raw.step"
        try:
            await _download(ctx, row["raw_r2_key"], raw)
        except FileNotFoundError:
            return await _fail_revision(ctx, row, "RAW_FILE_NOT_FOUND")
        out = job_dir / "out"
        res = await asyncio.to_thread(run_job, {
            "job_type": "ingest", "job_id": f"ingest-{revision_id}", "revision_id": revision_id,
            "step_path": str(raw), "out_dir": str(out), "expected_sha256": row["file_sha256"],
        }, scratch_root=job_dir / "jobs")
        if not res.ok:
            return await _fail_revision(ctx, row, res.error_code or "WORKER_INTERNAL_ERROR", res.message)

        prefix = f"ws/{row['workspace_id']}/parts/{row['part_id']}/revisions/{rid}/"
        s = ctx["storage"]
        uploads = {}
        for name in ("lod0.glb", "lod1.glb", "analytical_meta.json"):
            uploads[name] = await asyncio.to_thread(s.put_file_brotli, s.s.bucket_viewer, prefix + name, out / name)
        async with db.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE revisions SET status = 'READY', viewer_prefix = $2, error_code = NULL WHERE id = $1", rid, prefix)
            await audit(conn, row["workspace_id"], "revision", rid, "REVISION_READY",
                        {"viewer_prefix": prefix, "summary": res.result.get("summary"), "warnings": res.result.get("warnings")})
        await publish(ctx, rev_channel(rid), "ready", {"revision_id": rid, "status": "READY", "viewer_prefix": prefix,
                                                       "warnings": res.result.get("warnings", [])})
        await maybe_purge(ctx, rid)
        return {"status": "READY", "uploads": uploads, "timings_ms": res.result.get("timings_ms")}
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


async def _fail_revision(ctx: dict, row, code: str, message: str | None = None) -> dict[str, Any]:
    db = ctx["db"]
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE revisions SET status = 'FAILED', error_code = $2 WHERE id = $1", row["id"], code)
        await audit(conn, row["workspace_id"], "revision", row["id"], "REVISION_FAILED", {"error_code": code, "message": message})
    await publish(ctx, rev_channel(row["id"]), "failed", {"revision_id": row["id"], "status": "FAILED", "error_code": code})
    log.warning("revision %s failed: %s %s", row["id"], code, message or "")
    return {"status": "FAILED", "error_code": code}


# ============================================================================================
# q-diff-batch / q-diff-interactive
# ============================================================================================
async def compute_diff(ctx: dict, diff_run_id: str) -> dict[str, Any]:
    did = UUID(diff_run_id)
    db = ctx["db"]
    run = await db.fetchrow(
        "SELECT d.*, p.workspace_id FROM diff_runs d JOIN parts p ON p.id = d.part_id WHERE d.id = $1", did)
    if run is None:
        return {"status": "MISSING"}
    if run["status"] not in ("QUEUED", "COMPUTING"):
        return {"status": "SKIPPED", "reason": f"diff run is {run['status']}"}
    await db.execute("UPDATE diff_runs SET status = 'COMPUTING', updated_at = now() WHERE id = $1", did)
    await publish(ctx, diff_channel(did), "progress", {"diff_run_id": did, "status": "COMPUTING", "stage": "DOWNLOADING"})

    revs = {r["id"]: r for r in await db.fetch(
        "SELECT id, raw_r2_key, file_sha256 FROM revisions WHERE id = ANY($1::uuid[])", [run["base_rev_id"], run["target_rev_id"]])}
    job_dir = _job_dir(ctx, "diff", diff_run_id)
    try:
        paths = {}
        for side, rev_id in (("base", run["base_rev_id"]), ("target", run["target_rev_id"])):
            rev = revs.get(rev_id)
            if rev is None or not rev["raw_r2_key"]:
                return await _fail_diff(ctx, run, "RAW_FILE_NOT_FOUND", f"{side} raw STEP is not available (purged?)")
            dest = job_dir / f"{side}.step"
            try:
                await _download(ctx, rev["raw_r2_key"], dest)
            except FileNotFoundError:
                return await _fail_diff(ctx, run, "RAW_FILE_NOT_FOUND", f"{side} raw STEP object missing")
            paths[side] = dest

        manual = run["align_method"] == "MANUAL_321"
        alignment = {"mode": "MANUAL_321", "transform_4x4": list(run["transform_4x4"])} if manual else {"mode": "AUTO"}
        await publish(ctx, diff_channel(did), "progress", {"diff_run_id": did, "status": "COMPUTING", "stage": "COMPUTING",
                                                           "align_mode": alignment["mode"]})
        out = job_dir / "out"
        res = await asyncio.to_thread(run_job, {
            "job_type": "diff", "job_id": f"diff-{diff_run_id}", "diff_run_id": diff_run_id,
            "base": {"revision_id": str(run["base_rev_id"]), "step_path": str(paths["base"]),
                     "expected_sha256": revs[run["base_rev_id"]]["file_sha256"]},
            "target": {"revision_id": str(run["target_rev_id"]), "step_path": str(paths["target"]),
                       "expected_sha256": revs[run["target_rev_id"]]["file_sha256"]},
            "out_dir": str(out), "alignment": alignment,
        }, scratch_root=job_dir / "jobs")
        if not res.ok:
            return await _fail_diff(ctx, run, res.error_code or "WORKER_INTERNAL_ERROR", res.message)

        await publish(ctx, diff_channel(did), "progress", {"diff_run_id": did, "status": "COMPUTING", "stage": "UPLOADING"})
        manifest = json.loads((out / "diff_manifest.json").read_text("utf-8"))
        prefix = f"ws/{run['workspace_id']}/parts/{run['part_id']}/diffs/{did}/{uuid7().hex}/"
        s = ctx["storage"]
        manifest_key = prefix + "diff_manifest.json"
        await asyncio.to_thread(s.put_file_brotli, s.s.bucket_viewer, manifest_key, out / "diff_manifest.json")
        for art in manifest["artifacts"].values():
            if art:
                await asyncio.to_thread(s.put_file_brotli, s.s.bucket_viewer, prefix + art, out / art)

        status = res.result["status"]  # READY | NEEDS_ALIGN
        al = manifest["alignment"]
        async with db.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE diff_runs SET status = $2, align_method = $3, transform_4x4 = $4, align_confidence = $5,"
                " manifest_r2_key = $6, error_code = NULL, updated_at = now() WHERE id = $1",
                did, status, al["method"], al["transform_4x4"], al["confidence"], manifest_key)
            n_items = await _replace_items(conn, did, manifest if status == "READY" else None)
            await audit(conn, run["workspace_id"], "diff_run", did, f"DIFF_{status}", {
                "manifest_key": manifest_key, "alignment": al, "summary": manifest["summary"], "items": n_items})
        event = "ready" if status == "READY" else "needs_align"
        await publish(ctx, diff_channel(did), event, {
            "diff_run_id": did, "status": status, "alignment": al, "summary": manifest["summary"], "item_count": n_items,
            "warnings": res.result.get("warnings", [])})
        for rev_id in (run["base_rev_id"], run["target_rev_id"]):
            await maybe_purge(ctx, rev_id)
        return {"status": status, "items": n_items, "timings_ms": res.result.get("timings_ms")}
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


async def _replace_items(conn, did: UUID, manifest: dict | None) -> int:
    """Policy: diff_items exist only for READY runs; check state survives a recompute by change_key."""
    previous = {r["change_key"]: r for r in await conn.fetch(
        "SELECT change_key, checked_at, checked_by_contact FROM diff_items WHERE diff_run_id = $1", did)}
    await conn.execute("DELETE FROM diff_items WHERE diff_run_id = $1", did)
    if manifest is None:
        return 0
    rows = []
    for ch in manifest["changes"]:
        prev = previous.get(ch["change_id"])
        rows.append((
            uuid7(), did, ch["change_id"], ch["category"], ch["severity"], ch["title"], ch["fact"],
            (ch.get("engineering_hint") or {}).get("text"), ch["anchor_xyz"],
            prev["checked_at"] if prev else None, prev["checked_by_contact"] if prev else None,
        ))
    if rows:
        await conn.executemany(
            "INSERT INTO diff_items (id, diff_run_id, change_key, category, severity, title, fact_json, hint_text,"
            " anchor_xyz, checked_at, checked_by_contact) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", rows)
    return len(rows)


async def _fail_diff(ctx: dict, run, code: str, message: str | None = None) -> dict[str, Any]:
    db = ctx["db"]
    async with db.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE diff_runs SET status = 'FAILED', error_code = $2, updated_at = now() WHERE id = $1", run["id"], code)
        await _replace_items(conn, run["id"], None)
        await audit(conn, run["workspace_id"], "diff_run", run["id"], "DIFF_FAILED", {"error_code": code, "message": message})
    await publish(ctx, diff_channel(run["id"]), "failed", {"diff_run_id": run["id"], "status": "FAILED", "error_code": code,
                                                           "message": message})
    log.warning("diff run %s failed: %s %s", run["id"], code, message or "")
    for rev_id in (run["base_rev_id"], run["target_rev_id"]):
        await maybe_purge(ctx, rev_id)
    return {"status": "FAILED", "error_code": code}


# ============================================================================================
# q-purge (Mode B zero-retention + Mode A TTL)
# ============================================================================================
# NEEDS_ALIGN still needs the raw files for the 3-2-1 recompute (q-diff-interactive), so it counts as
# pending; housekeeping enforces a 24 h hard cap for zero-retention workspaces.
PENDING_DIFFS_SQL = (
    "SELECT count(*) FROM diff_runs WHERE (base_rev_id = $1 OR target_rev_id = $1)"
    " AND status IN ('QUEUED', 'COMPUTING', 'NEEDS_ALIGN')"
)
ZERO_RETENTION_HARD_CAP_HOURS = 24


async def maybe_purge(ctx: dict, rev_id: UUID) -> bool:
    """ZERO_RETENTION: drop the raw STEP as soon as nothing queued still needs it."""
    row = await ctx["db"].fetchrow(
        "SELECT r.status, r.raw_r2_key, w.security_mode FROM revisions r JOIN parts p ON p.id = r.part_id"
        " JOIN workspaces w ON w.id = p.workspace_id WHERE r.id = $1", rev_id)
    if row is None or row["security_mode"] != "ZERO_RETENTION" or not row["raw_r2_key"]:
        return False
    if row["status"] not in ("READY", "FAILED"):
        return False
    if await ctx["db"].fetchval(PENDING_DIFFS_SQL, rev_id):
        return False
    # unique job id: ARQ drops jobs whose id it already knows (e.g. a purge after a re-drop)
    job = await ctx["redis"].enqueue_job("purge_revision", str(rev_id), "ZERO_RETENTION", _queue_name=Q_PURGE,
                                         _job_id=f"purge:{rev_id}:{uuid.uuid4().hex}")
    if job is None:
        log.error("q-purge refused the purge job of revision %s", rev_id)
    return job is not None


async def purge_revision(ctx: dict, revision_id: str, reason: str = "ZERO_RETENTION") -> dict[str, Any]:
    rid = UUID(revision_id)
    db = ctx["db"]
    s = ctx["storage"]
    async with db.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT r.id, r.raw_r2_key, r.file_sha256, r.status, p.workspace_id FROM revisions r"
            " JOIN parts p ON p.id = r.part_id WHERE r.id = $1 FOR UPDATE OF r", rid)
        if row is None or not row["raw_r2_key"]:
            return {"status": "NOOP"}
        if reason == "ZERO_RETENTION" and await conn.fetchval(PENDING_DIFFS_SQL, rid):
            return {"status": "DEFERRED"}
        key = row["raw_r2_key"]
        await asyncio.to_thread(s.delete, s.s.bucket_raw, key)
        await conn.execute("UPDATE revisions SET raw_r2_key = NULL, raw_purged_at = now() WHERE id = $1", rid)
        await audit(conn, row["workspace_id"], "revision", rid, "RAW_PURGED",
                    {"reason": reason, "raw_r2_key": key, "file_sha256": row["file_sha256"]}, actor="system:q-purge")
    await publish(ctx, rev_channel(rid), "purged", {"revision_id": rid, "reason": reason})
    log.info("purged raw STEP of revision %s (%s)", rid, reason)
    return {"status": "PURGED", "key": key}


async def housekeeping(ctx: dict) -> dict[str, Any]:
    """Cron: Mode A TTL purge + lazy link expiry."""
    db = ctx["db"]
    expired_raw = await db.fetch(
        "SELECT r.id FROM revisions r JOIN parts p ON p.id = r.part_id JOIN workspaces w ON w.id = p.workspace_id"
        " WHERE w.security_mode = 'TTL_VAULT' AND r.raw_r2_key IS NOT NULL"
        " AND r.created_at < now() - make_interval(days => w.retention_days)")
    for r in expired_raw:
        await ctx["redis"].enqueue_job("purge_revision", str(r["id"]), "TTL_EXPIRED", _queue_name=Q_PURGE)
    stale_zero = await db.fetch(
        "SELECT r.id FROM revisions r JOIN parts p ON p.id = r.part_id JOIN workspaces w ON w.id = p.workspace_id"
        " WHERE w.security_mode = 'ZERO_RETENTION' AND r.raw_r2_key IS NOT NULL"
        f" AND r.created_at < now() - interval '{ZERO_RETENTION_HARD_CAP_HOURS} hours'")
    for r in stale_zero:
        await ctx["redis"].enqueue_job("purge_revision", str(r["id"]), "ZERO_RETENTION_HARD_CAP", _queue_name=Q_PURGE)
    links = await db.execute("UPDATE review_links SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND expires_at < now()")
    return {"ttl_purges": len(expired_raw), "zero_retention_cap_purges": len(stale_zero), "links": links,
            "at": datetime.now(timezone.utc).isoformat()}
