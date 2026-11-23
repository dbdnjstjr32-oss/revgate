#!/bin/sh
# RevGate MinIO bootstrap (blueprint §1.2 / product spec §4 "3단 버킷 Blast Radius 격리").
# Idempotent: safe to re-run on every `docker compose up`.
set -eu

: "${MINIO_ENDPOINT:=http://minio:9000}"
: "${WEB_ORIGIN:=http://localhost:3000}"
: "${RAW_BUCKET:=revgate-raw-cad}"
: "${VIEWER_BUCKET:=revgate-viewer-derived}"
: "${AUDIT_BUCKET:=revgate-audit-proofs}"
ALIAS=local

log() { echo "[minio-init] $*"; }

log "waiting for ${MINIO_ENDPOINT} ..."
i=0
until mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    log "ERROR: MinIO not reachable after 60s"
    exit 1
  fi
  sleep 1
done
log "connected"

# bucket-1: raw STEP vault (private only, never CDN-linked; per-workspace TTL is enforced by q-purge,
#           a 90-day lifecycle rule is the bucket-level safety net = max Mode A retention).
mc mb --ignore-existing "$ALIAS/$RAW_BUCKET"
# bucket-2: viewer-derived (lod0/lod1 GLB, analytical_meta.json, delta GLBs) via 15-min signed URLs.
mc mb --ignore-existing "$ALIAS/$VIEWER_BUCKET"
# bucket-3: approval proof PDFs, WORM. Object Lock can only be enabled at creation time.
if mc ls "$ALIAS/$AUDIT_BUCKET" >/dev/null 2>&1; then
  log "bucket $AUDIT_BUCKET already exists"
else
  mc mb --with-lock "$ALIAS/$AUDIT_BUCKET"
fi

for b in "$RAW_BUCKET" "$VIEWER_BUCKET" "$AUDIT_BUCKET"; do
  # No anonymous access anywhere: browsers only get presigned URLs.
  mc anonymous set none "$ALIAS/$b" >/dev/null
done

# (the mc image ships no grep/awk -> plain shell pattern matching)
ILM_RULES="$(mc ilm rule ls --json "$ALIAS/$RAW_BUCKET" 2>/dev/null || true)"
case "$ILM_RULES" in
  *'"Days":90'* | *'"days":90'*) log "lifecycle rule (90d expiry) already present on $RAW_BUCKET" ;;
  *) mc ilm rule add --expire-days 90 "$ALIAS/$RAW_BUCKET" \
       || log "WARN: lifecycle rule not supported by this server build (skipped)" ;;
esac

# CORS for the Next.js dev origin: presigned PUT (upload) + GET/HEAD (GLB streaming).
CORS_FILE=/tmp/revgate-cors.xml
cat > "$CORS_FILE" <<EOF
<CORSConfiguration>
  <CORSRule>
    <AllowedOrigin>${WEB_ORIGIN}</AllowedOrigin>
    <AllowedMethod>GET</AllowedMethod>
    <AllowedMethod>HEAD</AllowedMethod>
    <AllowedMethod>PUT</AllowedMethod>
    <AllowedHeader>*</AllowedHeader>
    <ExposeHeader>ETag</ExposeHeader>
    <ExposeHeader>Content-Length</ExposeHeader>
    <ExposeHeader>Content-Encoding</ExposeHeader>
    <MaxAgeSeconds>3600</MaxAgeSeconds>
  </CORSRule>
</CORSConfiguration>
EOF

for b in "$RAW_BUCKET" "$VIEWER_BUCKET" "$AUDIT_BUCKET"; do
  if mc cors set "$ALIAS/$b" "$CORS_FILE" >/dev/null 2>&1; then
    log "bucket CORS set: $b -> ${WEB_ORIGIN}"
  else
    log "WARN: bucket-level CORS unsupported for $b; relying on server-wide MINIO_API_CORS_ALLOW_ORIGIN"
  fi
done

# Keep the server-wide allow-list in sync as well (persisted in MinIO config).
mc admin config set "$ALIAS" api cors_allow_origin="${WEB_ORIGIN}" >/dev/null 2>&1 \
  || log "WARN: could not persist api cors_allow_origin (env var still applies)"

log "buckets:"
mc ls "$ALIAS"
log "done"
