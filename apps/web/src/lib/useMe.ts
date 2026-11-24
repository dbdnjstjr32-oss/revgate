"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "./api";
import type { User, Workspace } from "./types";

export interface Me {
  user: User;
  workspace: Workspace;
}

/** Current internal session (rg_sid); redirects to /login on 401. */
export function useMe() {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);

  const reload = useCallback(async () => {
    try {
      setMe(await api<Me>("/v1/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [router, pathname]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { me, setMe, reload };
}
