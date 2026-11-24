import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "RevGate — 3D 리비전 변경점 리뷰 게이트",
  description: "카톡 캡처 대신 링크 하나로 3D 형상 변경점을 대조·합의·승인합니다.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
