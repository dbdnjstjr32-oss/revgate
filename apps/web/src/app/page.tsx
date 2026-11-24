import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-20">
      <h1 className="text-3xl font-black tracking-tight text-indigo-700">RevGate</h1>
      <p className="mt-3 text-lg text-slate-700">
        카톡 캡처에 빨간 줄 긋는 도면 소통을 끝내고, 링크 하나로 3D 형상 변경점을 정밀 대조·합의·승인하세요.
      </p>
      <div className="mt-8 flex gap-3">
        <Link href="/login" className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white">
          발주처 로그인
        </Link>
        <Link href="/workspace" className="rounded-md border border-slate-300 bg-white px-4 py-2 font-medium text-slate-700">
          워크스페이스
        </Link>
      </div>
    </main>
  );
}
