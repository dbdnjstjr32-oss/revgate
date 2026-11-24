"use client";

/** diff_manifest.json + both analytical_meta.json (signed, Brotli-encoded objects decoded by the browser).
 *  Re-fetched only when the signed-URL bundle object itself changes (i.e. after a deliberate reload). */

import type { AnalyticalMeta, DiffManifest } from "@revgate/contracts";
import { useEffect, useState } from "react";

import { fetchSignedJson } from "./api";
import type { SignedUrls } from "./types";

export interface ViewerData {
  manifest: DiffManifest | null;
  baseMeta: AnalyticalMeta | null;
  targetMeta: AnalyticalMeta | null;
  loading: boolean;
  error: string | null;
}

const EMPTY: ViewerData = { manifest: null, baseMeta: null, targetMeta: null, loading: false, error: null };

export function useViewerData(urls: SignedUrls | null): ViewerData {
  const [data, setData] = useState<ViewerData>(EMPTY);

  useEffect(() => {
    if (!urls) return;
    let cancelled = false;
    setData((d) => ({ ...d, loading: true, error: null }));
    Promise.all([
      urls.manifest ? fetchSignedJson<DiffManifest>(urls.manifest) : Promise.resolve(null),
      urls.base?.meta ? fetchSignedJson<AnalyticalMeta>(urls.base.meta) : Promise.resolve(null),
      urls.target?.meta ? fetchSignedJson<AnalyticalMeta>(urls.target.meta) : Promise.resolve(null),
    ])
      .then(([manifest, baseMeta, targetMeta]) => {
        if (!cancelled) setData({ manifest, baseMeta, targetMeta, loading: false, error: null });
      })
      .catch((e: unknown) => {
        if (!cancelled) setData((d) => ({ ...d, loading: false, error: e instanceof Error ? e.message : String(e) }));
      });
    return () => {
      cancelled = true;
    };
  }, [urls]);

  return data;
}
