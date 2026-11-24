/** Browser-side upload: SHA-256 (WebCrypto) -> presigned PUT straight to R2/MinIO -> complete. */

import type { AttachmentItem } from "@revgate/contracts";

import { api } from "./api";

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function isStepFile(name: string): boolean {
  return /\.(step|stp)$/i.test(name);
}

/** PUT with progress (XHR); Content-Length is set by the browser and must match the signed value. */
export function putFile(url: string, file: Blob, onProgress?: (fraction: number) => void,
  contentType = "application/octet-stream"): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", contentType); // must equal the signed Content-Type
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("upload failed (network)"));
    xhr.send(file);
  });
}

export interface PresignResponse {
  revision_id: string;
  put_url: string;
  r2_key: string;
  redrop: boolean;
}

export type UploadPhase = "hashing" | "uploading" | "verifying" | "done";

export interface StagedUpload {
  revisionId: string;
  redrop: boolean;
}

/** SHA-256 -> presign -> PUT. The revision stays PENDING_UPLOAD (or purged, for a re-drop) until completed. */
export async function stageRevision(
  partId: string,
  file: File,
  revLabel: string,
  onPhase?: (phase: UploadPhase, progress?: number) => void,
): Promise<StagedUpload> {
  if (!isStepFile(file.name)) throw new Error("STEP(.step/.stp) 파일만 업로드할 수 있습니다");
  if (file.size > MAX_UPLOAD_BYTES) throw new Error("최대 50MB까지 업로드할 수 있습니다");
  onPhase?.("hashing");
  const sha = await sha256Hex(file);
  const pre = await api<PresignResponse>(`/v1/parts/${partId}/revisions/presign`, {
    json: { rev_label: revLabel, filename: file.name, file_size_bytes: file.size, file_sha256: sha },
  });
  onPhase?.("uploading", 0);
  await putFile(pre.put_url, file, (p) => onPhase?.("uploading", p));
  return { revisionId: pre.revision_id, redrop: pre.redrop };
}

/** The API re-hashes the stored object, then queues q-ingest (or restores a purged raw STEP). */
export function completeRevision(revisionId: string): Promise<{ status: string; redropped?: boolean }> {
  return api<{ status: string; redropped?: boolean }>(`/v1/revisions/${revisionId}/complete`, { json: {} });
}

export async function uploadRevision(
  partId: string,
  file: File,
  revLabel: string,
  onPhase?: (phase: UploadPhase, progress?: number) => void,
): Promise<{ revisionId: string; redrop: boolean; status: string }> {
  const staged = await stageRevision(partId, file, revLabel, onPhase);
  onPhase?.("verifying");
  const done = await completeRevision(staged.revisionId);
  onPhase?.("done");
  return { revisionId: staged.revisionId, redrop: staged.redrop, status: done.status };
}


// ---- Phase 4: 2D drawings (PDF / DXF) attached to a revision ------------------------------------
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
export type DrawingKind = "2D_PDF" | "2D_DXF";

export function drawingKindFor(name: string): DrawingKind | null {
  if (/\.pdf$/i.test(name)) return "2D_PDF";
  if (/\.dxf$/i.test(name)) return "2D_DXF";
  return null;
}

export const DRAWING_CONTENT_TYPE: Record<DrawingKind, string> = { "2D_PDF": "application/pdf", "2D_DXF": "application/dxf" };

/** SHA-256 -> presign -> PUT (signed Content-Type) -> complete (the API re-hashes + sniffs the bytes). */
export async function uploadDrawing(revisionId: string, file: File): Promise<{ attachment: AttachmentItem; superseded_links: number }> {
  const kind = drawingKindFor(file.name);
  if (!kind) throw new Error("2D 도면은 PDF(.pdf) 또는 DXF(.dxf)만 첨부할 수 있습니다");
  if (file.size > MAX_ATTACHMENT_BYTES) throw new Error("2D 도면은 최대 25MB까지 첨부할 수 있습니다");
  const sha = await sha256Hex(file);
  const contentType = DRAWING_CONTENT_TYPE[kind];
  const pre = await api<{ attachment_id: string; put_url: string }>(`/v1/revisions/${revisionId}/attachments/presign`, {
    json: { kind, filename: file.name, content_type: contentType, file_size_bytes: file.size, file_sha256: sha },
  });
  await putFile(pre.put_url, file, undefined, contentType);
  return api(`/v1/revisions/${revisionId}/attachments/${pre.attachment_id}/complete`, { json: {} });
}

export function deleteDrawing(revisionId: string, attachmentId: string): Promise<{ attachment: AttachmentItem; superseded_links: number }> {
  return api(`/v1/revisions/${revisionId}/attachments/${attachmentId}`, { method: "DELETE" });
}
