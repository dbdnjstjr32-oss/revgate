"""E2E fixtures — everything is real: the running `api` service, the ARQ `cad-worker` (OCP subprocesses),
Postgres 16, Redis 7 and MinIO. Nothing is mocked; the only "test hook" is the dev outbox
(Redis list rg:dev:outbox) used to read magic links / OTP codes the way an inbox would.

Run:  docker compose -f infra/docker-compose.yml --profile test run --rm api-tests
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import boto3
import httpx
import psycopg
import pytest
import redis as redis_lib
from botocore.config import Config

API = os.environ.get("E2E_API_URL", "http://api:8000")
DB_DSN = os.environ.get("E2E_DB_DSN", "postgresql://revgate:revgate@postgres:5432/revgate")
APP_DB_DSN = os.environ.get("E2E_APP_DB_DSN", "postgresql://revgate_app:revgate_app_dev@postgres:5432/revgate")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
GOLDEN = Path(os.environ.get("E2E_GOLDEN_DIR", "/golden"))
TERMINAL = {"ready", "needs_align", "failed"}
OUTBOX = "rg:dev:outbox"


# ---------------------------------------------------------------------------------------------
# infrastructure handles
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def redis():
    r = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    return r


@pytest.fixture(scope="session")
def db():
    conn = psycopg.connect(DB_DSN, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def s3():
    return boto3.client(
        "s3", endpoint_url="http://localhost:9000", aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "revgate"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "revgate-dev-secret"), region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


@pytest.fixture(scope="session")
def http():
    with httpx.Client(base_url=API, timeout=60) as c:
        for _ in range(60):
            try:
                if c.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        yield c


def q(db, sql: str, *args) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def cookie_from(resp: httpx.Response, name: str) -> tuple[str, dict[str, Any]]:
    """Parse Set-Cookie (Secure cookies are not replayed by httpx over http, so tests pass them explicitly)."""
    for raw in resp.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(raw)
        if name in jar:
            m = jar[name]
            attrs = {k: m[k] for k in ("max-age", "path", "samesite") if m[k]}
            attrs["httponly"] = "httponly" in raw.lower()
            attrs["secure"] = "secure" in raw.lower()
            return m.value, attrs
    raise AssertionError(f"no Set-Cookie for {name}: {resp.headers.get_list('set-cookie')}")


def outbox(redis, to: str, kind: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for raw in redis.lrange(OUTBOX, 0, 200):
            msg = json.loads(raw)
            if msg["to"] == to and msg["kind"] == kind:
                return msg
        time.sleep(0.2)
    raise AssertionError(f"no {kind} message for {to} in the dev outbox")


def login(http: httpx.Client, redis, email: str | None = None) -> dict[str, Any]:
    email = email or f"{unique('eng')}@e2e.revgate.test"
    r = http.post("/v1/auth/magic-link", json={"email": email})
    assert r.status_code == 200 and r.json() == {"sent": True}
    token = outbox(redis, email, "MAGIC_LINK")["data"]["token"]
    r = http.post("/v1/auth/verify", json={"token": token})
    assert r.status_code == 200, r.text
    sid, attrs = cookie_from(r, "rg_sid")
    body = r.json()
    return {"email": email, "cookie": f"rg_sid={sid}", "sid": sid, "cookie_attrs": attrs, "token": token,
            "user": body["user"], "workspace": body["workspace"]}


def H(session: dict[str, Any] | str) -> dict[str, str]:
    return {"Cookie": session if isinstance(session, str) else session["cookie"]}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def presign_and_put(http: httpx.Client, s: dict, part_id: str, data: bytes, rev_label: str, filename: str = "part.step",
                    sha: str | None = None) -> dict[str, Any]:
    r = http.post(f"/v1/parts/{part_id}/revisions/presign", headers=H(s), json={
        "rev_label": rev_label, "filename": filename, "file_size_bytes": len(data),
        "file_sha256": sha or hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    body = r.json()
    put = httpx.put(body["put_url"], content=data, headers={"Content-Type": "application/octet-stream"}, timeout=60)
    assert put.status_code == 200, put.text
    return body


def upload_revision(http: httpx.Client, s: dict, part_id: str, path: Path, rev_label: str) -> str:
    body = presign_and_put(http, s, part_id, path.read_bytes(), rev_label, filename=path.name)
    r = http.post(f"/v1/revisions/{body['revision_id']}/complete", headers=H(s), json={"spec_card_json": {"material": "AL6061"}})
    assert r.status_code == 200 and r.json()["status"] == "QUEUED", r.text
    return body["revision_id"]


def wait_revision(http: httpx.Client, s: dict, rev_id: str, timeout: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = http.get(f"/v1/revisions/{rev_id}", headers=H(s))
        assert r.status_code == 200, r.text
        if r.json()["revision"]["status"] in ("READY", "FAILED"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"revision {rev_id} did not finish in {timeout}s")


def wait_diff(http: httpx.Client, s: dict, diff_id: str, statuses=("READY", "NEEDS_ALIGN", "FAILED"), timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = http.get(f"/v1/diffs/{diff_id}", headers=H(s))
        assert r.status_code == 200, r.text
        if r.json()["diff_run"]["status"] in statuses:
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"diff {diff_id} did not reach {statuses} in {timeout}s")


def sse_events(s: dict, diff_id: str, timeout: float = 120) -> list[tuple[str, dict]]:
    """Consume /v1/diffs/{id}/events until a terminal event (the server closes the stream)."""
    events: list[tuple[str, dict]] = []
    deadline = time.monotonic() + timeout  # keep-alive comments every 15 s would defeat a read timeout
    with httpx.Client(timeout=httpx.Timeout(30.0)) as c, c.stream(
            "GET", f"{API}/v1/diffs/{diff_id}/events", headers={**H(s), "Accept": "text/event-stream"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        event, data = None, []
        for line in r.iter_lines():
            if time.monotonic() > deadline:
                raise AssertionError(f"no terminal SSE event for {diff_id} within {timeout}s: {events}")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and event:
                events.append((event, json.loads("\n".join(data))))
                if event in TERMINAL:
                    break
                event, data = None, []
    return events


def make_part(http: httpx.Client, s: dict, part_number: str | None = None) -> dict[str, Any]:
    r = http.post("/v1/parts", headers=H(s), json={"part_number": part_number or unique("PN"), "name": "E2E bracket",
                                                   "default_material": "AL6061-T6", "default_finish": "Anodize"})
    assert r.status_code == 200, r.text
    return r.json()["part"]


def golden(case: str, side: str) -> Path:
    return GOLDEN / case / f"rev_{side}.step"


@pytest.fixture(scope="session")
def hole_diff(http, redis) -> dict[str, Any]:
    """01_hole_diam taken all the way to a READY diff (shared by the diff and review suites)."""
    s = login(http, redis)
    part = make_part(http, s)
    t0 = time.monotonic()
    a = upload_revision(http, s, part["id"], golden("01_hole_diam", "A"), "A")
    b = upload_revision(http, s, part["id"], golden("01_hole_diam", "B"), "B")
    r = http.post(f"/v1/parts/{part['id']}/diffs", headers=H(s), json={"base_rev_id": a, "target_rev_id": b})
    assert r.status_code == 200 and r.json()["status"] == "QUEUED", r.text
    diff_id = r.json()["diff_run_id"]
    events = sse_events(s, diff_id)
    return {"session": s, "part": part, "base": a, "target": b, "diff_id": diff_id, "events": events,
            "elapsed": time.monotonic() - t0}
