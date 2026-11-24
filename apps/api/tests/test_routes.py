"""Route contract: the API exposes exactly the endpoints listed in IMPLEMENTATION_BLUEPRINT §4 (+ /health).

§4 is titled "26개" but enumerates 29 method+path pairs; all 29 are implemented.
"""

from __future__ import annotations

BLUEPRINT_S4 = {
    # [1] internal auth
    ("POST", "/v1/auth/magic-link"), ("POST", "/v1/auth/verify"), ("GET", "/v1/auth/me"), ("POST", "/v1/auth/logout"),
    # [2] parts & presigned upload
    ("POST", "/v1/parts"), ("GET", "/v1/parts"), ("POST", "/v1/parts/{part_id}/revisions/presign"),
    ("POST", "/v1/revisions/{rev_id}/complete"), ("GET", "/v1/revisions/{rev_id}"),
    # [3] diff / SSE / realign
    ("POST", "/v1/parts/{part_id}/diffs"), ("GET", "/v1/diffs/{diff_id}"), ("GET", "/v1/diffs/{diff_id}/events"),
    ("POST", "/v1/diffs/{diff_id}/realign"),
    # [4] share links & OTP
    ("POST", "/v1/diffs/{diff_id}/links"), ("DELETE", "/v1/links/{link_id}"), ("GET", "/v1/public/links/{token}/meta"),
    ("POST", "/v1/public/links/{token}/otp/request"), ("POST", "/v1/public/links/{token}/otp/verify"),
    ("GET", "/v1/public/links/{token}/bundle"),
    # [5] checks & pins
    ("PATCH", "/v1/review/diff-items/{item_id}/check"), ("GET", "/v1/review/diffs/{diff_id}/pins"),
    ("POST", "/v1/review/diffs/{diff_id}/pins"), ("PATCH", "/v1/review/pins/{pin_id}/resolve"),
    # [6] approval & proof PDF
    ("POST", "/v1/public/links/{token}/approve"), ("GET", "/v1/links/{link_id}/approval-pdf"),
    # [7] shop inbox
    ("POST", "/v1/shop-inboxes"), ("GET", "/v1/public/shop/{slug}"), ("POST", "/v1/public/shop/{slug}/submissions/presign"),
    ("POST", "/v1/public/shop/{slug}/submissions/complete"),
}


# Phase 3 additions (web app needs): OWNER settings, read models, dev-only outbox helper.
PHASE3 = {
    ("PATCH", "/v1/workspace/settings"), ("GET", "/v1/parts/{part_id}"), ("GET", "/v1/diffs/{diff_id}/links"),
}
DEV_ONLY = {("GET", "/v1/dev/outbox")}  # not registered when REVGATE_ENV=prod


def test_openapi_matches_blueprint_section_4(http):
    spec = http.get("/openapi.json").json()
    exposed = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    assert len(BLUEPRINT_S4) == 29
    assert exposed == BLUEPRINT_S4 | PHASE3 | DEV_ONLY | {("GET", "/health")}
