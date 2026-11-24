"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { api, errorText } from "@/lib/api";

function Verify() {
  const params = useSearchParams();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // magic links are single-use (StrictMode double effects)
    started.current = true;
    const token = params.get("token");
    const next = params.get("next") ?? "/workspace";
    if (!token) {
      setError("토큰이 없습니다");
      return;
    }
    api("/v1/auth/verify", { json: { token } })
      .then(() => router.replace(next.startsWith("/") ? next : "/workspace"))
      .catch((e) => setError(errorText(e)));
  }, [params, router]);

  return error ? (
    <div data-testid="verify-error" className="text-sm text-red-700">
      {error} — <Link href="/login" className="underline">다시 로그인</Link>
    </div>
  ) : (
    <p data-testid="verify-pending" className="text-sm text-slate-600">로그인 확인 중…</p>
  );
}

export default function VerifyPage() {
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Suspense>
        <Verify />
      </Suspense>
    </main>
  );
}
