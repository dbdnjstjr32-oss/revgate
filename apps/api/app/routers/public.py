"""[4. 공유 링크 & 외부 6자리 OTP] + [6. 가공 착수 승인] — no account, link-scoped sessions.

* OTP: 6 digits, HMAC-SHA256(secret, otp_session_id:code) at rest, 5 min TTL, at most 3 requests per
  link per 10 minutes (counted via UUIDv7 time order of otp_sessions.id), locked after 5 failures.
* rg_ext_sid: HttpOnly cookie, 24 h, valid for exactly one review_link_id.
* approve: row lock -> APPROVED (terminal) + immutable approval snapshot (Phase 4 §2.1: review_links.
  approval_snapshot_json + canonical approval_snapshot_sha256) -> PDF rendered from that snapshot only
  (WeasyPrint) into the WORM audit bucket. Missing material / tolerance must be acknowledged first (§3.2).
* Phase 4 §2.2: a superseded approval stays APPROVED but reports effective_status SUPERSEDED and no longer
  hands out the original STEP files.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from revgate_contracts import ApprovalSnapshot

from .. import audit, mailer
from .. import serializers as S
from .. import spec as SP
from ..config import Settings
from ..context import SpecContext, spec_context
from ..deps import ExternalSession, external_session, get_db, get_redis, get_settings_dep, get_storage, utcnow
from ..errors import ApiError, not_found
from ..ids import uuid7, uuid7_floor
from ..models import DiffItem, DiffRun, OtpSession, Part, PinComment, ReviewLink, Revision
from ..pdf import render_approval_pdf
from ..schemas import ApproveIn, OtpVerifyIn
from ..security import EXTERNAL_COOKIE, ip_hash, mask_contact, new_otp_code, new_token, otp_hash, otp_matches, set_cookie, sha256_hex
from ..storage import Storage
from ..supersede import latest_revision_label
from .diffs import _item_order

router = APIRouter(prefix="/v1/public/links", tags=["public-links"])
KST = ZoneInfo("Asia/Seoul")


async def resolve_link(db: AsyncSession, token: str, *, for_update: bool = False) -> tuple[ReviewLink, DiffRun, Part]:
    stmt = select(ReviewLink).where(ReviewLink.token_hash == sha256_hex(token))
    if for_update:
        stmt = stmt.with_for_update()
    link = (await db.execute(stmt)).scalar_one_or_none()
    if link is None:
        raise not_found("link")
    if link.status == "ACTIVE" and link.expires_at <= utcnow():
        link.status = "EXPIRED"  # lazy terminal transition
        await db.commit()
    run = await db.get(DiffRun, link.diff_run_id)
    return link, run, await db.get(Part, run.part_id)


def _usable(link: ReviewLink) -> None:
    if link.status not in ("ACTIVE", "APPROVED"):
        raise ApiError(410, f"LINK_{link.status}", f"review link is {link.status}")


async def _session_for(request: Request, db: AsyncSession, link: ReviewLink) -> ExternalSession:
    ext = await external_session(request, db)
    if ext is None:
        raise ApiError(401, "OTP_REQUIRED", "verify the 6-digit code first")
    if ext.link.id != link.id:
        raise ApiError(403, "LINK_SCOPE", "session belongs to another review link")
    return ext


@router.get("/{token}/meta")
async def link_meta(token: str, db: AsyncSession = Depends(get_db)):
    """Unauthenticated (OG card): no geometry, no full contact."""
    link, run, p = await resolve_link(db, token)
    base, target = await db.get(Revision, run.base_rev_id), await db.get(Revision, run.target_rev_id)
    count = await db.scalar(select(func.count()).select_from(DiffItem).where(DiffItem.diff_run_id == run.id))
    return {"part_number": p.part_number, "rev_pair": {"base": base.rev_label, "target": target.rev_label},
            "change_count": int(count or 0), "masked_contact": mask_contact(link.recipient_contact), "status": link.status,
            "effective_status": "SUPERSEDED" if link.superseded_at is not None else link.status,
            "superseded_reason": link.superseded_reason}


@router.post("/{token}/otp/request")
async def otp_request(token: str, request: Request, db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis),
                      settings: Settings = Depends(get_settings_dep)):
    link, _run, p = await resolve_link(db, token)
    _usable(link)
    window_start = utcnow() - timedelta(seconds=settings.otp_rate_window_sec)
    recent = await db.scalar(select(func.count()).select_from(OtpSession).where(
        OtpSession.review_link_id == link.id, OtpSession.id >= uuid7_floor(window_start)))
    if recent >= settings.otp_rate_limit:
        raise ApiError(429, "OTP_RATE_LIMITED", f"max {settings.otp_rate_limit} codes per {settings.otp_rate_window_sec // 60} minutes",
                       headers={"Retry-After": str(settings.otp_rate_window_sec)})
    code = new_otp_code()
    sid = uuid7()
    db.add(OtpSession(id=sid, review_link_id=link.id, contact=link.recipient_contact, otp_hash=otp_hash(settings, str(sid), code),
                      otp_expires_at=utcnow() + timedelta(seconds=settings.otp_ttl_sec)))
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id,
                 actor=link.recipient_contact, action="OTP_REQUESTED", request=request)
    await db.commit()
    await mailer.send(redis, settings, to=link.recipient_contact, kind="OTP", subject="RevGate 인증번호",
                      body=f"[RevGate] 인증번호 {code} ({settings.otp_ttl_sec // 60}분 유효)", data={"code": code, "link_id": str(link.id)})
    return {"ttl_sec": settings.otp_ttl_sec, "masked_contact": mask_contact(link.recipient_contact)}


@router.post("/{token}/otp/verify")
async def otp_verify(token: str, body: OtpVerifyIn, request: Request, response: Response, db: AsyncSession = Depends(get_db),
                     settings: Settings = Depends(get_settings_dep)):
    link, _run, p = await resolve_link(db, token)
    _usable(link)
    otp = (await db.execute(select(OtpSession).where(OtpSession.review_link_id == link.id, OtpSession.verified_at.is_(None))
                            .order_by(OtpSession.id.desc()).limit(1).with_for_update())).scalar_one_or_none()
    if otp is None:
        raise ApiError(400, "OTP_NOT_REQUESTED", "request a code first")
    if otp.attempts >= settings.otp_max_attempts:
        raise ApiError(423, "OTP_LOCKED", "too many wrong codes; request a new code")
    if otp.otp_expires_at <= utcnow():
        raise ApiError(410, "OTP_EXPIRED", "code expired; request a new code")
    if not otp_matches(settings, str(otp.id), body.code, otp.otp_hash):
        otp.attempts += 1
        audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id,
                     actor=link.recipient_contact, action="OTP_FAILED", payload={"attempts": otp.attempts}, request=request)
        await db.commit()
        if otp.attempts >= settings.otp_max_attempts:
            raise ApiError(423, "OTP_LOCKED", "too many wrong codes; request a new code")
        raise ApiError(401, "OTP_INVALID", "wrong code", remaining_attempts=settings.otp_max_attempts - otp.attempts)
    session = new_token()
    now = utcnow()
    otp.verified_at = now
    otp.session_token_hash = sha256_hex(session)
    otp.session_expires_at = now + timedelta(seconds=settings.ext_session_ttl_sec)
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id,
                 actor=link.recipient_contact, action="OTP_VERIFIED", request=request)
    await db.commit()
    set_cookie(response, EXTERNAL_COOKIE, session, settings.ext_session_ttl_sec, settings)
    return {"verified": True, "session_expires_at": S.iso(otp.session_expires_at)}


def watermark(link: ReviewLink, run: DiffRun) -> str:
    return f"{link.recipient_name} · {mask_contact(link.recipient_contact)} · {utcnow().astimezone(KST):%Y-%m-%d %H:%M} · {str(run.id)[:8]}"


@router.get("/{token}/bundle")
async def bundle(token: str, request: Request, db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis),
                 storage: Storage = Depends(get_storage), settings: Settings = Depends(get_settings_dep)):
    link, run, p = await resolve_link(db, token)
    _usable(link)
    await _session_for(request, db, link)
    if run.status != "READY":
        raise ApiError(409, "DIFF_NOT_READY", f"diff run is {run.status}")
    items = sorted((await db.execute(select(DiffItem).where(DiffItem.diff_run_id == run.id))).scalars().all(), key=_item_order)
    pins = (await db.execute(select(PinComment).where(PinComment.diff_run_id == run.id).order_by(PinComment.created_at))).scalars().all()
    base, target = await db.get(Revision, run.base_rev_id), await db.get(Revision, run.target_rev_id)
    urls = await S.diff_urls(storage, redis, run, base, target)
    # original STEP only after approval, by explicit grant, and never once the approval is superseded (§2.2)
    if link.allow_raw_download and link.status == "APPROVED" and link.superseded_at is None:
        urls["raw"] = {side: storage.presign_get(storage.raw, rev.raw_r2_key, filename=f"{p.part_number}_{rev.rev_label}.step")
                       for side, rev in (("base", base), ("target", target)) if rev.raw_r2_key}
    ctx = await spec_context(db, p, base, target, items)
    latest = await latest_revision_label(db, p.id)
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id,
                 actor=link.recipient_contact, action="LINK_VIEWED", request=request)
    await db.commit()
    return {"link": S.link(link, latest), "part": S.part(p),
            "revisions": {"base": S.revision(base, p), "target": S.revision(target, p)},
            "diff_run": S.diff_run(run), "items": [S.item(i) for i in items], "pins": [S.pin(x) for x in pins],
            "signed_urls": urls, "watermark_text": watermark(link, run),
            # Phase 4
            "attachments": ctx.attachments_json(storage), "spec_diffs": ctx.spec_diffs, "spec_warnings": ctx.spec_warnings}


def _snapshot_revision(rev: Revision, card: dict, attachments: list) -> dict:
    return {"id": str(rev.id), "rev_label": rev.rev_label, "step_sha256": rev.file_sha256, "spec_version": rev.spec_version,
            "spec_card": card,
            "attachments": [{"id": str(a.id), "kind": a.kind, "filename": a.filename, "sha256": a.file_sha256} for a in attachments]}


def _dfm_ack(body: ApproveIn, ctx: SpecContext) -> dict:
    process = ctx.target_card["process"]
    allowed = SP.dfm_check_ids(process)
    if body.dfm_ack is None:
        return {"process": process, "checked_ids": []}
    unknown = sorted(set(body.dfm_ack.checked_ids) - set(allowed))
    if body.dfm_ack.process != process or unknown:
        raise ApiError(422, "DFM_ACK_INVALID", f"DFM checklist must use the target process {process}",
                       process=process, allowed=list(allowed), unknown=unknown)
    checked = set(body.dfm_ack.checked_ids)
    return {"process": process, "checked_ids": [c for c in allowed if c in checked]}  # canonical order


@router.post("/{token}/approve")
async def approve(token: str, request: Request, body: ApproveIn | None = None, db: AsyncSession = Depends(get_db),
                  redis: Redis = Depends(get_redis), storage: Storage = Depends(get_storage),
                  settings: Settings = Depends(get_settings_dep)):
    body = body or ApproveIn()
    link, run, p = await resolve_link(db, token, for_update=True)
    if link.status == "APPROVED":
        raise ApiError(409, "ALREADY_APPROVED", "this link was already approved", approved_at=S.iso(link.approved_at))
    _usable(link)
    await _session_for(request, db, link)
    if run.status != "READY":
        raise ApiError(409, "DIFF_NOT_READY", f"diff run is {run.status}")

    items = sorted((await db.execute(select(DiffItem).where(DiffItem.diff_run_id == run.id))).scalars().all(), key=_item_order)
    pins = (await db.execute(select(PinComment).where(PinComment.diff_run_id == run.id).order_by(PinComment.created_at))).scalars().all()
    base, target = await db.get(Revision, run.base_rev_id), await db.get(Revision, run.target_rev_id)
    ctx = await spec_context(db, p, base, target, items)
    acked = set(body.acked_warnings)
    missing_ack = [code for code in ctx.ack_required if code not in acked]
    if missing_ack:  # §3.2 explicit acknowledgement of missing material / tolerance
        raise ApiError(422, "SPEC_ACK_REQUIRED", "acknowledge the missing material / tolerance warnings first", required=missing_ack)
    dfm = _dfm_ack(body, ctx)

    approved_at = utcnow()
    manifest_sha = sha256_hex(await storage.get_decoded(storage.viewer, run.manifest_r2_key)) if run.manifest_r2_key else None
    snapshot = {
        "snapshot_version": "1.0",
        "approved_at": approved_at.isoformat(),
        "approver": {"name": link.recipient_name, "contact": mask_contact(link.recipient_contact),
                     "ip_hash": ip_hash(settings, audit.client_ip(request))},
        "part": {"id": str(p.id), "part_number": p.part_number, "name": p.name},
        "base_rev": _snapshot_revision(base, ctx.base_card, ctx.base_attachments),
        "target_rev": _snapshot_revision(target, ctx.target_card, ctx.target_attachments),
        "diff_run": {"id": str(run.id), "align_method": run.align_method, "transform_4x4": [float(x) for x in run.transform_4x4],
                     "manifest_sha256": manifest_sha},
        "spec_diffs": ctx.spec_diffs,
        "geometry_changes": [{"change_key": i.change_key, "category": i.category, "severity": i.severity, "title": i.title,
                              "fact": i.fact_json, "hint_text": i.hint_text, "checked": i.checked_at is not None,
                              "checked_by": i.checked_by_contact} for i in items],
        "dfm_ack": dfm,
        "missing_spec_warnings_acked": sorted(acked & {w["code"] for w in ctx.spec_warnings}),
        "unresolved_pin_count": sum(1 for x in pins if x.resolved_at is None),
    }
    ApprovalSnapshot.model_validate(snapshot)  # SSOT contract check before the record becomes immutable
    snapshot_sha = SP.snapshot_sha256(snapshot)

    pdf = await asyncio.to_thread(render_approval_pdf, snapshot, snapshot_sha)
    key = f"ws/{p.workspace_id}/links/{link.id}/approval-{approved_at:%Y%m%dT%H%M%SZ}.pdf"
    await storage.put_locked(storage.audit, key, pdf, "application/pdf",
                             retain_until=(approved_at + timedelta(days=settings.audit_retention_days)).astimezone(timezone.utc))
    link.status, link.approved_at, link.approval_pdf_r2_key = "APPROVED", approved_at, key
    link.approval_snapshot_json, link.approval_snapshot_sha256, link.dfm_ack_json = snapshot, snapshot_sha, dfm
    pdf_sha = sha256_hex(pdf)
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id,
                 actor=link.recipient_contact, action="APPROVED", payload_sha256=snapshot_sha, request=request)
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id,
                 actor=link.recipient_contact, action="APPROVAL_PDF_SEALED",
                 payload={"pdf_key": key, "pdf_sha256": pdf_sha, "snapshot_sha256": snapshot_sha}, request=request)
    await db.commit()
    return {"approved_at": approved_at.isoformat(), "pdf_signed_url": storage.presign_get(storage.audit, key, filename="approval.pdf"),
            "pdf_sha256": pdf_sha, "snapshot_sha256": snapshot_sha, "approval_snapshot": snapshot}
