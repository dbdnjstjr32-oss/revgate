/** API read models (apps/api/app/serializers.py). Geometry contracts come from @revgate/contracts. */

export type RevisionStatus = "PENDING_UPLOAD" | "QUEUED" | "PARSING" | "READY" | "FAILED";
export type DiffStatus = "QUEUED" | "COMPUTING" | "READY" | "NEEDS_ALIGN" | "FAILED";
export type LinkStatus = "ACTIVE" | "APPROVED" | "REVOKED" | "EXPIRED";
export type SecurityMode = "TTL_VAULT" | "ZERO_RETENTION";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "OWNER" | "ENGINEER" | "VIEWER";
  workspace_id: string;
}

export interface Workspace {
  id: string;
  name: string;
  plan: string;
  security_mode: SecurityMode;
  retention_days: number;
}

export interface Part {
  id: string;
  workspace_id: string;
  shop_inbox_id: string | null;
  part_number: string;
  name: string;
  default_material: string | null;
  default_finish: string | null;
  created_at: string;
}

export interface Revision {
  id: string;
  part_id: string;
  rev_label: string;
  file_sha256: string;
  file_size_bytes: number;
  status: RevisionStatus;
  error_code: string | null;
  raw_available: boolean;
  raw_purged_at: string | null;
  viewer_prefix: string | null;
  spec_card_json: Record<string, unknown>;
  created_at: string;
}

export interface DiffRun {
  id: string;
  part_id: string;
  base_rev_id: string;
  target_rev_id: string;
  align_method: "IDENTITY" | "AUTO_BREP_RANSAC" | "MANUAL_321";
  transform_4x4: number[];
  align_confidence: number | null;
  status: DiffStatus;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface DiffItem {
  id: string;
  diff_run_id: string;
  change_key: string;
  category: string;
  severity: "CRITICAL" | "MAJOR" | "INFO";
  title: string;
  fact: Record<string, number | string>;
  hint_text: string | null;
  anchor_xyz: number[];
  checked: boolean;
  checked_at: string | null;
  checked_by_contact: string | null;
}

export interface ReviewLink {
  id: string;
  diff_run_id: string;
  recipient_name: string;
  masked_contact: string;
  allow_raw_download: boolean;
  status: LinkStatus;
  expires_at: string;
  approved_at: string | null;
  created_at: string;
}

export interface Pin {
  id: string;
  diff_run_id: string;
  diff_item_id: string | null;
  review_link_id: string | null;
  author_type: "INTERNAL" | "EXTERNAL";
  author_label: string;
  face_id: number;
  rev_side: "BASE" | "TARGET";
  pin_xyz: number[];
  body: string;
  resolved: boolean;
  created_at: string;
}

export interface ViewerUrls {
  lod0: string;
  lod1: string;
  meta: string;
}

export interface SignedUrls {
  base: ViewerUrls | null;
  target: ViewerUrls | null;
  manifest?: string;
  delta_added_glb?: string | null;
  delta_removed_glb?: string | null;
  raw?: { base?: string; target?: string };
}

export interface DiffResponse {
  diff_run: DiffRun;
  items: DiffItem[];
  signed_urls: SignedUrls;
}

export interface BundleResponse {
  link: ReviewLink;
  part: Part;
  revisions: { base: Revision; target: Revision };
  diff_run: DiffRun;
  items: DiffItem[];
  pins: Pin[];
  signed_urls: SignedUrls;
  watermark_text: string;
}

export interface PartDetail {
  part: Part;
  revisions: Revision[];
  diff_runs: DiffRun[];
}
