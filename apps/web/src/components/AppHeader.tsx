"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api";
import type { Me } from "@/lib/useMe";

export function AppHeader({ me, children }: { me: Me | null; children?: React.ReactNode }) {
  const router = useRouter();
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1600px] items-center gap-3 px-4 py-2.5">
        <Link href="/workspace" className="text-base font-black tracking-tight text-indigo-700">
          RevGate<span className="ml-1 text-xs font-medium text-slate-500">레브게이트</span>
        </Link>
        <div className="flex-1 text-sm text-slate-600">{children}</div>
        {me && (
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <span data-testid="me-email">{me.user.email}</span>
            <span className="rounded bg-slate-100 px-1.5 py-0.5">{me.workspace.security_mode}</span>
            <button
              type="button"
              className="text-slate-500 underline"
              onClick={async () => {
                await api("/v1/auth/logout", { json: {} });
                router.replace("/login");
              }}
            >
              로그아웃
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
