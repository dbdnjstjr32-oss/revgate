"""Object storage (Cloudflare R2 in prod, MinIO locally) — 3-bucket blast-radius split (product spec §4).

* presigned PUT 10 min (Content-Length + Content-Type are signed -> the declared size is enforced)
* presigned GET 15 min
* server-side calls use the internal endpoint; URLs handed to clients are signed for the public one
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import datetime
from typing import Any

import boto3
import brotli
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings

STEP_CONTENT_TYPE = "application/octet-stream"


def _client(settings: Settings, endpoint: str):
    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key, region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      request_checksum_calculation="when_required", response_checksum_validation="when_required"),
    )


def _missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.internal = _client(settings, settings.s3_endpoint_url)
        self.public = _client(settings, settings.s3_public_endpoint_url)
        self.raw = settings.s3_bucket_raw
        self.viewer = settings.s3_bucket_viewer
        self.audit = settings.s3_bucket_audit

    # ---- URLs (pure signing, no network) -----------------------------------------------------
    def presign_put(self, bucket: str, key: str, size: int, content_type: str = STEP_CONTENT_TYPE) -> str:
        return self.public.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": key, "ContentLength": size, "ContentType": content_type},
            ExpiresIn=self.settings.put_url_ttl_sec)

    def presign_get(self, bucket: str, key: str, filename: str | None = None) -> str:
        params: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.public.generate_presigned_url("get_object", Params=params, ExpiresIn=self.settings.get_url_ttl_sec)

    # ---- server-side I/O ------------------------------------------------------------------------
    async def head(self, bucket: str, key: str) -> dict[str, Any] | None:
        def _head():
            try:
                return self.internal.head_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                if _missing(exc):
                    return None
                raise
        return await asyncio.to_thread(_head)

    async def sha256(self, bucket: str, key: str) -> tuple[str, int]:
        def _hash():
            body = self.internal.get_object(Bucket=bucket, Key=key)["Body"]
            h, n = hashlib.sha256(), 0
            for chunk in iter(lambda: body.read(1 << 20), b""):
                h.update(chunk)
                n += len(chunk)
            return h.hexdigest(), n
        return await asyncio.to_thread(_hash)

    async def delete(self, bucket: str, key: str) -> None:
        await asyncio.to_thread(self.internal.delete_object, Bucket=bucket, Key=key)

    async def get_decoded(self, bucket: str, key: str) -> bytes:
        def _get():
            obj = self.internal.get_object(Bucket=bucket, Key=key)
            data = obj["Body"].read()
            return brotli.decompress(data) if obj.get("ContentEncoding") == "br" else data
        return await asyncio.to_thread(_get)

    async def put_locked(self, bucket: str, key: str, data: bytes, content_type: str, retain_until: datetime) -> None:
        """WORM upload (S3 Object Lock retention) for approval proofs."""
        md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
        await asyncio.to_thread(
            self.internal.put_object, Bucket=bucket, Key=key, Body=data, ContentType=content_type, ContentMD5=md5,
            ObjectLockMode=self.settings.audit_lock_mode, ObjectLockRetainUntilDate=retain_until,
            Metadata={"sha256": hashlib.sha256(data).hexdigest()})

    async def ping(self) -> bool:
        await asyncio.to_thread(self.internal.head_bucket, Bucket=self.viewer)
        return True


def raw_key(workspace_id, part_id, revision_id) -> str:
    return f"ws/{workspace_id}/parts/{part_id}/revisions/{revision_id}/raw.step"
