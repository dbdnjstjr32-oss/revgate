"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button, Card, ErrorText, Field, inputCls } from "@/components/ui";
import { api, devOutbox, errorText } from "@/lib/api";

function LoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const next = params.get("next") ?? "/workspace";
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [devToken, setDevToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setDevToken(null);
    try {
      await api("/v1/auth/magic-link", { json: { email } });
      setSent(true);
      // local-development hint: the API mirrors outgoing mail into rg:dev:outbox
      for (let i = 0; i < 12; i++) {
        const [msg] = await devOutbox({ to: email.trim().toLowerCase(), kind: "MAGIC_LINK", limit: "1" });
        if (msg?.data?.token) {
          setDevToken(msg.data.token);
          break;
        }
        await new Promise((r) => setTimeout(r, 400));
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="발주처 로그인 (Magic Link)" testId="login-card">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <Field label="업무용 이메일">
          <input data-testid="login-email" type="email" required className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="designer@company.com" />
        </Field>
        <Button data-testid="login-submit" type="submit" className="w-full" disabled={busy || !email.includes("@")}>
          로그인 링크 받기
        </Button>
      </form>
      {sent && <p data-testid="login-sent" className="mt-3 text-sm text-emerald-700">메일함에서 로그인 링크를 여세요 (15분 유효).</p>}
      {devToken && (
        <div data-testid="dev-magic-link" className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          <p className="font-semibold">DEV · rg:dev:outbox</p>
          <p>로컬 개발 환경이라 메일 대신 outbox에서 링크를 찾았습니다.</p>
          <Button
            data-testid="dev-magic-open"
            variant="secondary"
            className="mt-1"
            onClick={() => router.push(`/auth/verify?token=${encodeURIComponent(devToken)}&next=${encodeURIComponent(next)}`)}
          >
            로그인 링크 열기
          </Button>
        </div>
      )}
      <ErrorText error={error} />
    </Card>
  );
}

export default function LoginPage() {
  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <h1 className="mb-6 text-2xl font-black tracking-tight text-indigo-700">RevGate</h1>
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
