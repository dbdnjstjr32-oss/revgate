"""S3 / R2 / MinIO access for the queue runtime (boto3, path-style, SigV4).

Viewer artefacts (GLB / JSON) are stored Brotli-compressed with `Content-Encoding: br`, so
browsers decode them transparently when they stream the 15-minute signed URLs (blueprint §2.1).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import boto3
import brotli
from botocore.config import Config
from botocore.exceptions import ClientError

CONTENT_TYPES = {".glb": "model/gltf-binary", ".json": "application/json", ".pdf": "application/pdf"}


@dataclass(frozen=True)
class S3Settings:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    region: str
    bucket_raw: str
    bucket_viewer: str
    bucket_audit: str
    brotli_quality: int

    @classmethod
    def from_env(cls) -> "S3Settings":
        return cls(
            endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://minio:9000"),
            access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "revgate"),
            secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "revgate-dev-secret"),
            region=os.environ.get("S3_REGION", "us-east-1"),
            bucket_raw=os.environ.get("S3_BUCKET_RAW", "revgate-raw-cad"),
            bucket_viewer=os.environ.get("S3_BUCKET_VIEWER", "revgate-viewer-derived"),
            bucket_audit=os.environ.get("S3_BUCKET_AUDIT", "revgate-audit-proofs"),
            brotli_quality=int(os.environ.get("BROTLI_QUALITY", "9")),
        )


def make_client(s: S3Settings):
    return boto3.client(
        "s3",
        endpoint_url=s.endpoint_url,
        aws_access_key_id=s.access_key_id,
        aws_secret_access_key=s.secret_access_key,
        region_name=s.region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class Storage:
    def __init__(self, settings: S3Settings) -> None:
        self.s = settings
        self.client = make_client(settings)

    def download(self, bucket: str, key: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, key, str(dest))
        return dest.stat().st_size

    def put_brotli(self, bucket: str, key: str, data: bytes, content_type: str) -> dict:
        body = brotli.compress(data, quality=self.s.brotli_quality)
        self.client.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType=content_type, ContentEncoding="br",
            Metadata={"uncompressed-size": str(len(data)), "sha256": hashlib.sha256(data).hexdigest()},
        )
        return {"key": key, "bytes": len(data), "stored_bytes": len(body)}

    def put_file_brotli(self, bucket: str, key: str, path: Path) -> dict:
        return self.put_brotli(bucket, key, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

    def delete(self, bucket: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket, Key=key)

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
