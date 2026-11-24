"""Request bodies (blueprint §4)."""

from __future__ import annotations

import math
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from .security import EMAIL_RE, SHA256_RE


class MagicLinkIn(BaseModel):
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("invalid email")
        return v


class VerifyIn(BaseModel):
    token: str = Field(min_length=16, max_length=200)


class PartIn(BaseModel):
    part_number: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    default_material: str | None = Field(default=None, max_length=120)
    default_finish: str | None = Field(default=None, max_length=120)


class FileMeta(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    file_size_bytes: int = Field(gt=0)
    file_sha256: str
    rev_label: str | None = Field(default=None, max_length=40)

    @field_validator("file_sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        v = v.lower()
        if not SHA256_RE.match(v):
            raise ValueError("file_sha256 must be 64 hex characters")
        return v

    @field_validator("filename")
    @classmethod
    def _ext(cls, v: str) -> str:
        if not v.lower().endswith((".step", ".stp")):
            raise ValueError("only .step / .stp files are accepted")
        return v


class PresignIn(FileMeta):
    rev_label: str = Field(min_length=1, max_length=40)


class CompleteIn(BaseModel):
    spec_card_json: dict[str, Any] | None = None


class DiffIn(BaseModel):
    base_rev_id: UUID
    target_rev_id: UUID


class RealignIn(BaseModel):
    transform_4x4: list[float] = Field(min_length=16, max_length=16)

    @field_validator("transform_4x4")
    @classmethod
    def _finite(cls, v: list[float]) -> list[float]:
        if not all(math.isfinite(x) for x in v):
            raise ValueError("transform_4x4 must be finite numbers")
        return v


class LinkIn(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_contact: str = Field(min_length=3, max_length=320)
    expires_in_days: int = Field(default=7, ge=1, le=90)
    allow_raw_download: bool = False


class OtpVerifyIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class CheckIn(BaseModel):
    checked: bool


class PinIn(BaseModel):
    diff_item_id: UUID | None = None
    face_id: int = Field(ge=0)
    rev_side: Literal["BASE", "TARGET"]
    pin_xyz: list[float] = Field(min_length=3, max_length=3)
    body: str = Field(min_length=1, max_length=2000)


class ResolveIn(BaseModel):
    resolved: bool


class InboxIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
    shop_name: str = Field(min_length=1, max_length=120)
    owner_contact: str = Field(min_length=3, max_length=320)


class SubmissionPresignIn(BaseModel):
    sender_name: str = Field(min_length=1, max_length=120)
    sender_contact: str = Field(min_length=3, max_length=320)
    part_number: str = Field(min_length=1, max_length=120)
    files: list[FileMeta] = Field(min_length=2, max_length=2, description="[v1_meta, v2_meta]")


class SubmissionCompleteIn(BaseModel):
    submission_id: UUID
    spec_card_json: dict[str, Any] | None = None


class WorkspaceSettingsIn(BaseModel):
    security_mode: Literal["TTL_VAULT", "ZERO_RETENTION"] | None = None
    # product spec §4: Mode A keeps raw STEP for 30~90 days
    retention_days: int | None = Field(default=None, ge=30, le=90)

    @model_validator(mode="after")
    def _not_empty(self) -> "WorkspaceSettingsIn":
        if self.security_mode is None and self.retention_days is None:
            raise ValueError("provide security_mode and/or retention_days")
        return self

# ---- Phase 4 (docs/PHASE4_MANUFACTURING_SPEC.md) --------------------------------------------------
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024


class AttachmentPresignIn(BaseModel):
    kind: Literal["2D_PDF", "2D_DXF", "SPEC_SHEET"]
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127)
    file_size_bytes: int = Field(gt=0)
    file_sha256: str

    @field_validator("file_sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        v = v.lower()
        if not SHA256_RE.match(v):
            raise ValueError("file_sha256 must be 64 hex characters")
        return v

    @field_validator("filename")
    @classmethod
    def _basename(cls, v: str) -> str:
        v = v.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not v or v in (".", ".."):
            raise ValueError("invalid filename")
        return v


class DfmAckIn(BaseModel):
    process: Literal["CNC_MILLING", "CNC_TURNING", "PRINT_FDM", "PRINT_SLA_SLS_MJF", "SHEET_METAL", "OTHER"]
    checked_ids: list[str] = Field(default_factory=list, max_length=20)


class ApproveIn(BaseModel):
    """Optional body of POST /v1/public/links/{token}/approve (Phase 1~3 clients send none)."""
    dfm_ack: DfmAckIn | None = None
    acked_warnings: list[str] = Field(default_factory=list, max_length=20)
