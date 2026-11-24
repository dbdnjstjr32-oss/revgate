# RevGate (레브게이트) — 프로그램 소개 및 사용 가이드

> **한 줄 요약:** 카카오톡 캡처에 빨간 줄 그어가며 수정 도면을 설명하던 제조·기구 외주 발주 소통을 끝내고, **웹 링크 하나로 3D 형상 변경점(Diff)을 정밀 대조·측정·승인하는 하드웨어용 GitHub PR & 도면 접수 게이트**입니다.

---

## 1. 이게 무슨 프로그램인가요? (기획 배경)

로봇·드론·하드웨어 스타트업의 기구설계자나 중소 제조사가 CNC·3D프린팅 외주를 맡길 때 현장에서는 이런 일이 반복됩니다.

1. 이메일이나 카카오톡으로 `하우징_최종_진짜최종_v4.step` 같은 3D CAD 파일이 오갑니다.
2. *"여기 구멍 지름 0.5mm 키웠어요"*, *"옆면 단차 2mm 낮췄어요"*를 캡처 사진에 빨간 펜으로 그려서 보냅니다.
3. 가공업체 사장님 PC에는 발주처와 같은 비싼 CAD 프로그램이 없거나, 현장 태블릿에서는 파일을 못 열어 예전 도면으로 잘못 깎는 **오가공·납기 지연·수백만 원대 소재 폐기 사고**가 발생합니다.

**RevGate**는 소프트웨어 개발자들이 코드 변경점을 **GitHub PR(Pull Request)**로 비교·리뷰·승인하듯, **3D CAD 도면(`.step`)의 리비전(v1 vs v2) 차이를 서버에서 자동 분석해 웹 링크 하나로 3D 화면에 띄워주고, 외주처가 확인 후 '가공 착수 승인' 도장까지 찍게 해주는 B2B 웹서비스**입니다.

---

## 2. 누구를 위한 기능들이 들어있나요?

### ① 발주처(기구설계자)용 기능
- **드래그&드롭 자동 Diff:** `rev_A.step`, `rev_B.step` 두 파일을 올리면 서버가 자동으로 형상을 비교해 변경점 체크리스트를 생성합니다.
  - 홀(구멍) 지름/위치 변경, 포켓 깊이·단차 이동, 얇아진 벽 두께(박육) 경고, 부피 차이 등을 자동 감지합니다.
- **팩트(Fact)와 참고 추정(Hint)의 분리:** *"홀 지름이 Ø3.30 → Ø4.20mm로 변경됨"* 같은 **100% 기하학적 사실**과 *"ISO M4 → M5 탭 기초홀 규격과 일치"* 같은 **참고 추정**을 화면에 엄격히 분리해 오가공 리스크를 차단합니다.
- **좌표계 정합 & 3-2-1 수동 정합:** 설계자가 원점을 옮기거나 부품을 회전시켜 내보냈더라도 자동 정합(RANSAC)으로 맞춰 비교하며, 대칭 부품 등 자동 정합 신뢰도가 낮을 때는 브라우저에서 **3-2-1 데이텀 클릭 정합**을 지원합니다.
- **보안 정책 선택 (`TTL_VAULT` vs `ZERO_RETENTION`):**
  - `TTL_VAULT`: 원본 STEP을 비공개 버킷에 기간 한정 보관해 언제든 `v1↔v3` 등 조합 비교 가능.
  - `ZERO_RETENTION`: 파싱 직후 원본 STEP을 서버에서 즉시 파기(`RAW_PURGED`), 추후 재비교가 필요하면 로컬 파일을 다시 올려 **SHA-256 해시 검증 후 재연산(Hash-Verified Re-drop)**.

### ② 외주 가공업체(사장님·현장 담당자)용 기능
- **무설치·비로그인 6자리 OTP 열람 (`/r/[token]`):** 회원가입이나 CAD 프로그램 설치 없이 공유 링크를 누르고 6자리 OTP(10초 인증)만 입력하면 PC·태블릿 브라우저에서 바로 3D 뷰어가 열립니다.
- **0.001mm 정밀 측정 (클라이언트 측 해석적 계산):** 일반 웹 3D 뷰어는 메쉬(삼각형) 오차 때문에 치수가 부정확하지만, RevGate는 서버가 추출한 각 면의 수학적 정의(`analytical_meta.json`)를 활용해 구멍과 구멍 사이 중심거리, 면과 면 사이 거리, 홀-외벽 최소 두께를 **0.001mm 단위**로 정확히 계산합니다.
- **3D 핀 코멘트 & 가공 착수 승인 PDF:** 변경된 면을 하나씩 체크하고 3D 모델 표면에 직접 핀을 꽂아 질문할 수 있습니다. 모든 확인이 끝나고 **"가공 착수 승인"** 버튼을 누르면 양측의 분쟁을 막아주는 **승인 증빙 PDF 리포트**가 자동 생성됩니다.
- **화면 유출 방지 동적 워터마크:** 열람자 정보·시각이 3D 뷰어 전체에 은은하게 깔리며, 개발자 도구로 워터마크를 지우거나 숨기려 하면 화면이 즉시 블러 처리됩니다.

### ③ 가공업체 전용 무료 도면 접수함 (`/shop/[slug]`)
- 가공업체 한 곳은 수많은 발주처와 거래하는 허브입니다. 가공업체가 전용 접수 링크(예: `/shop/oo-precision`)를 만들어 거래처에 공유하면, 발주처가 로그인 없이 v1/v2 도면을 접수할 수 있고 가공업체 워크스페이스에 자동으로 Diff 결과가 도착합니다.

---

## 3. 시스템 구조 (기술 구성 한눈에 보기)

1. **`apps/web` (프론트엔드 — Next.js 15 + React Three Fiber / Three.js)**
   - `/login`, `/workspace`, `/parts/[partId]/diffs/[diffId]`, `/r/[token]`, `/shop/[slug]` 화면 제공
   - `128×128 DataTexture` + `MeshStandardMaterial.onBeforeCompile` 기반 **1-Draw-Call** 면 하이라이팅 및 고스트 모드
   - `_FACE_ID` 버텍스 속성을 GLB에 직접 내장해 클릭 즉시 O(1) 면 식별 및 카메라 450ms 부드러운 줌인
2. **`apps/api` (백엔드 — FastAPI + PostgreSQL 16 + Redis 7 + WeasyPrint)**
   - Alembic으로 관리되는 11개 테이블 스키마 (`workspaces`, `users`, `shop_inboxes`, `parts`, `revisions`, `diff_runs`, `diff_items`, `review_links`, `otp_sessions`, `pin_comments`, `audit_events`)
   - 29개 REST/SSE 엔드포인트, `HttpOnly` 세션 쿠키(`rg_sid`, `rg_ext_sid`), 15분 만료 Signed URL 스트리밍, 불변 감사 로그, 승인 PDF 생성
3. **`workers/cad` (CAD 기하 연산 워커 — Python 3.11 + `cadquery-ocp` OpenCASCADE)**
   - C++ OCCT 크래시/메모리 폭주로부터 서버를 보호하기 위해 **1-Job-1-Subprocess** 격리 실행 및 4개 ARQ 큐(`q-ingest`, `q-diff-batch`, `q-diff-interactive`, `q-purge`) 운영
   - Brotli 압축 1-Primitive GLB 생성, `cKDTree` 점군 편차 히트맵 폴백
4. **`packages/contracts` (단일 진실 공급원 SSOT)**
   - JSON Schema로부터 TypeScript 타입과 Python Pydantic v2 모델을 자동 생성해 프론트엔드·API·CAD 워커 간 데이터 계약 일치 보장

---

## 4. 현재 구현 및 검증 완료 상태

로컬 풀스택 환경에서 **모의(Mock) 객체 없이 실제 Docker 컨테이너(PostgreSQL 16, Redis 7, MinIO, FastAPI, ARQ `cad-worker`)와 실제 Chromium 브라우저**로 전 계층 검증을 완료했습니다.

- **CAD 워커 엔진 (`workers/cad`):** 55개 테스트 통과 (5종 Golden STEP 파라메트릭 도면 비교, 3-2-1 재정합, `SIGSEGV`/`SIGKILL`/OOM 격리 처리, Open Shell 폴백 등)
- **백엔드 API E2E (`apps/api`):** 22개 통합 테스트 통과 (로그인, Presign 업로드, SSE Diff 완료, 6자리 OTP·레이트 리밋, 핀·승인 PDF, Zero-Retention 파기 및 SHA-256 재드롭, Shop Inbox)
- **프론트엔드 (`apps/web`):** `tsc --noEmit`, `eslint (0 warnings)`, Vitest 단위 테스트 20개, `next build` 통과 + Playwright 풀스택 브라우저 E2E 6개 시나리오 전체 통과

> **로컬 개발 환경 참고:** 현재 외부 이메일/SMS 발송 프로바이더를 연결하기 전 단계이므로, 매직링크와 6자리 OTP는 Redis(`rg:dev:outbox`)를 통해 화면의 개발용 안내 배너에서 바로 확인하도록 구성되어 있으며, 오브젝트 스토리지는 Cloudflare R2 호환 로컬 MinIO를 사용합니다.

---

## 5. 내 PC에서 직접 켜서 체험하는 방법

현재 프로젝트 경로는 `C:\Users\dbdnj\.gemini\antigravity\scratch\revgate` 입니다.

1. **백엔드 스택 확인 (Docker Desktop 실행 상태에서):**
   ```powershell
   cd C:\Users\dbdnj\.gemini\antigravity\scratch\revgate
   docker compose -f infra/docker-compose.yml --profile app up -d
   ```
   - API 확인: `http://localhost:8000/health` (문서: `http://localhost:8000/docs`)
2. **웹 프론트엔드 실행:**
   ```powershell
   corepack pnpm --filter @revgate/web dev
   ```
   - 브라우저에서 **`http://localhost:3000/login`** 접속
   - 아무 이메일이나 입력 후 **로그인 링크 받기**를 누르면 화면 아래에 **`DEV · rg:dev:outbox`** 버튼이 나타나 바로 로그인됩니다.
3. **테스트용 3D 도면 파일 위치:**
   - `C:\Users\dbdnj\.gemini\antigravity\scratch\revgate\workers\cad\tests\golden\` 폴더 안의 `01_hole_diam` ~ `05_symmetric_needs_align` 폴더에 있는 `rev_A.step`, `rev_B.step` 두 파일을 워크스페이스에 드래그&드롭하면 바로 3D Diff를 체험하실 수 있습니다!
