"""가공 착수 승인 증빙 PDF (WeasyPrint) — Phase 4 §2.1.

The PDF is rendered from the stored approval snapshot (review_links.approval_snapshot_json) and its SHA-256
ONLY: nothing is re-queried or recomputed, so the record always shows exactly what was approved. The only
other inputs are static labels (process / DFM / warning names) and the legal notice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, select_autoescape

_ENV = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
KST = ZoneInfo("Asia/Seoul")

LEGAL_NOTICE = ("본 문서의 3D 치수는 STEP 공칭 형상 기준이며, 실제 가공 공차·나사·표면처리는 첨부 2D 도면 및 "
                "제조 사양이 우선합니다.")
PROCESS_LABEL = {
    "CNC_MILLING": "CNC 밀링", "CNC_TURNING": "CNC 선반", "PRINT_FDM": "3D 프린팅 (FDM)",
    "PRINT_SLA_SLS_MJF": "3D 프린팅 (SLA/SLS/MJF)", "SHEET_METAL": "판금", "OTHER": "기타/미지정",
}
TOLERANCE_LABEL = {"ISO_2768_fH": "ISO 2768-fH", "ISO_2768_mK": "ISO 2768-mK", "ISO_2768_cL": "ISO 2768-cL",
                   "CUSTOM": "사용자 정의", "UNSPECIFIED": "미지정"}
INSERT_LABEL = {"TAPPED_DIRECT": "직접 탭", "HELICOIL": "헬리코일", "HEAT_SET_BRASS": "열압입 황동 인서트", "PEM": "PEM 인서트"}
INSPECTION_LABEL = {"NONE": "검사 없음", "DIMENSIONAL_SHEET": "치수 성적서", "CMM_REPORT": "CMM 리포트"}
DFM_LABEL = {
    "corner_r": "내측 코너 R (공구 반경)", "thin_wall": "박육부 변형·채터링", "precision_fit": "정밀 끼워맞춤 공차",
    "coating_shift": "코팅·아노다이징 두께 치수 변화", "datum_setup": "데이텀·치구 세팅",
    "orientation": "적층 방향", "overhang": "오버행·서포트", "heat_insert": "열압입 인서트 홀", "shrinkage": "수축·휨",
}
DFM_ORDER = {"CNC": ["corner_r", "thin_wall", "precision_fit", "coating_shift", "datum_setup"],
             "PRINT": ["orientation", "overhang", "heat_insert", "shrinkage"]}
WARNING_LABEL = {"MISSING_MATERIAL": "재질 미지정", "MISSING_TOLERANCE": "공차 미지정 (2D 도면 없음)",
                 "UNDECLARED_THREAD": "나사 사양 미기재", "MISSING_BUILD_ORIENTATION": "적층 방향 미지정"}

TEMPLATE = _ENV.from_string("""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>RevGate 가공 착수 승인 증빙 {{ s.part.part_number }}</title>
<meta name="author" content="RevGate">
<meta name="description" content="approval snapshot sha256 {{ sha }}">
<style>
  @page { size: A4; margin: 15mm 13mm 20mm;
          @bottom-left { content: "Snapshot SHA-256 {{ sha[:16] }}…"; font-size: 7pt; color: #666; }
          @bottom-right { content: "RevGate · " counter(page) " / " counter(pages); font-size: 7.5pt; color: #666; } }
  body { font-family: "NanumGothic", "DejaVu Sans", sans-serif; font-size: 9pt; color: #111; }
  .seal { border: 1.6pt solid #0b3d91; padding: 3mm 4mm; margin-bottom: 4mm; }
  .seal .kicker { font-size: 8pt; letter-spacing: 1.2pt; color: #0b3d91; font-weight: bold; }
  h1 { font-size: 15pt; margin: 1mm 0 1mm; }
  h2 { font-size: 11pt; margin: 5mm 0 1.5mm; border-bottom: 0.6pt solid #999; padding-bottom: 0.8mm; }
  table { width: 100%; border-collapse: collapse; margin: 1.5mm 0 3mm; }
  th, td { border: 0.4pt solid #bbb; padding: 1.3mm 1.8mm; text-align: left; vertical-align: top; }
  th { background: #f1f3f5; }
  .kv th { width: 30%; }
  .sev-CRITICAL { color: #b00020; font-weight: bold; }
  .sev-MAJOR { color: #a15c00; font-weight: bold; }
  .hash { font-family: "DejaVu Sans Mono", monospace; font-size: 7pt; word-break: break-all; }
  .stamp { border: 1.2pt solid #0a7; color: #0a7; display: inline-block; padding: 1.5mm 3mm; font-weight: bold; }
  .muted { color: #666; }
  .notice { margin-top: 6mm; border-top: 0.8pt solid #333; padding-top: 2mm; font-size: 8.5pt; }
</style></head>
<body>
  <div class="seal">
    <div class="kicker">PRODUCTION APPROVAL RECORD</div>
    <h1>가공 착수 승인 증빙 — {{ s.part.part_number }}</h1>
    <div>승인 시각 {{ kst }} (KST) · {{ utc }} (UTC)</div>
    <div>Snapshot SHA-256 <span class="hash">{{ sha }}</span></div>
    <div style="margin-top:2mm"><span class="stamp">APPROVED — {{ s.approver.name }} ({{ s.approver.contact }})</span></div>
  </div>

  <table class="kv">
    <tr><th>부품 번호 / 이름</th><td>{{ s.part.part_number }} / {{ s.part.name }}</td></tr>
    <tr><th>리비전 (Base → Target)</th><td>{{ s.base_rev.rev_label }} → {{ s.target_rev.rev_label }}</td></tr>
    <tr><th>Diff run · 정합</th><td>{{ s.diff_run.id }} · {{ s.diff_run.align_method }}</td></tr>
    <tr><th>승인자 IP 해시</th><td class="hash">{{ s.approver.ip_hash or "-" }}</td></tr>
  </table>

  <h2>파일 무결성 지문</h2>
  <table>
    <tr><th>구분</th><th>파일</th><th>SHA-256</th></tr>
    {% for side, rev in (("Base", s.base_rev), ("Target", s.target_rev)) %}
    <tr><td>{{ side }} STEP</td><td>Rev {{ rev.rev_label }} · spec_version {{ rev.spec_version }}</td><td class="hash">{{ rev.step_sha256 }}</td></tr>
    {% for a in rev.attachments %}
    <tr><td>{{ side }} {{ a.kind }}</td><td>{{ a.filename }}</td><td class="hash">{{ a.sha256 }}</td></tr>
    {% endfor %}
    {% endfor %}
    <tr><td>Diff manifest</td><td>diff_manifest.json</td><td class="hash">{{ s.diff_run.manifest_sha256 or "-" }}</td></tr>
  </table>

  <h2>제조 사양 요약 (Target Rev {{ t.rev_label }}, spec_version {{ t.spec_version }})</h2>
  <table class="kv">
    <tr><th>공정</th><td>{{ process_label.get(c.process, c.process) }}</td></tr>
    <tr><th>재질</th><td>{{ c.material.grade or "미지정" }}{% if c.material.standard %} ({{ c.material.standard }}){% endif %}</td></tr>
    <tr><th>열처리</th><td>{% if c.heat_treatment.required %}필요{% if c.heat_treatment.spec %} · {{ c.heat_treatment.spec }}{% endif %}{% else %}없음{% endif %}</td></tr>
    <tr><th>표면처리</th><td>{{ c.surface_finish.type or "미지정" }}{% if c.surface_finish.anodizing_thickness_um %} · {{ c.surface_finish.anodizing_thickness_um }} µm{% endif %}{% if c.surface_finish.color %} · {{ c.surface_finish.color }}{% endif %}{% if c.surface_finish.get("ra_um") is not none %} · Ra {{ c.surface_finish.ra_um }} µm{% endif %}{% if c.surface_finish.masking_note %} · 마스킹: {{ c.surface_finish.masking_note }}{% endif %}</td></tr>
    <tr><th>일반 공차</th><td>{{ tolerance_label[c.general_tolerance.standard] }}{% if c.general_tolerance.custom_note %} · {{ c.general_tolerance.custom_note }}{% endif %}</td></tr>
    {% if c.additive_spec %}<tr><th>적층 사양</th><td>방향 {{ c.additive_spec.build_orientation or "미지정" }}{% if c.additive_spec.layer_height_mm %} · 층 {{ c.additive_spec.layer_height_mm }} mm{% endif %}{% if c.additive_spec.get("infill_pct") is not none %} · 충전 {{ c.additive_spec.infill_pct }}%{% endif %}{% if c.additive_spec.get("wall_loops") is not none %} · 벽 {{ c.additive_spec.wall_loops }}{% endif %}{% if c.additive_spec.support_note %} · {{ c.additive_spec.support_note }}{% endif %}</td></tr>{% endif %}
    <tr><th>주문</th><td>수량 {{ c.order.quantity if c.order.get("quantity") is not none else "-" }} · 납기 {{ c.order.due_date or "-" }} · {{ inspection_label[c.order.inspection_level] }}</td></tr>
  </table>
  <table>
    <tr><th>중요 공차</th><th>위치</th><th>사양</th><th>데이텀</th></tr>
    {% for ct in c.critical_tolerances %}<tr><td>{{ ct.id }}</td><td>{{ ct.location }}</td><td>{{ ct.spec }}</td><td>{{ ct.datum_ref or "-" }}</td></tr>
    {% else %}<tr><td colspan="4" class="muted">지정 없음</td></tr>{% endfor %}
  </table>
  <table>
    <tr><th>나사·인서트</th><th>수량</th><th>깊이</th><th>방식</th></tr>
    {% for th in c.threads_and_inserts %}<tr><td>{{ th.spec }}{% if th.note %} ({{ th.note }}){% endif %}</td><td>{{ th.count }}</td><td>{{ th.depth_mm ~ " mm" if th.depth_mm else "-" }}</td><td>{{ insert_label[th.insert_type] }}</td></tr>
    {% else %}<tr><td colspan="4" class="muted">지정 없음</td></tr>{% endfor %}
  </table>

  <h2>Base ↔ Target 제조 사양 변경 ({{ s.spec_diffs|length }}건)</h2>
  <table>
    <tr><th>등급</th><th>항목</th><th>Base</th><th>Target</th></tr>
    {% for d in s.spec_diffs %}<tr><td class="sev-{{ d.severity }}">{{ d.severity }}</td><td>{{ d.label }}</td><td>{{ d.before or "미지정" }}</td><td>{{ d.after or "미지정" }}</td></tr>
    {% else %}<tr><td colspan="4" class="muted">사양 변경 없음</td></tr>{% endfor %}
  </table>

  <h2>3D 형상 변경 체크리스트 ({{ s.geometry_changes|length }}건)</h2>
  <table>
    <tr><th>#</th><th>등급</th><th>항목</th><th>확정 팩트</th><th>확인</th></tr>
    {% for g in s.geometry_changes %}
    <tr><td>{{ loop.index }}</td><td class="sev-{{ g.severity }}">{{ g.severity }}</td>
        <td>[{{ g.category }}] {{ g.title }}{% if g.hint_text %}<br><small>(참고 추정) {{ g.hint_text }}</small>{% endif %}</td>
        <td>{% for k, v in g.fact.items() %}{{ k }}={{ v }}{% if not loop.last %}, {% endif %}{% endfor %}</td>
        <td>{% if g.checked %}✔ {{ g.checked_by }}{% else %}—{% endif %}</td></tr>
    {% else %}<tr><td colspan="5" class="muted">3D 형상 동일 (형상 변경 없음)</td></tr>{% endfor %}
  </table>

  <h2>DFM · 중요부위 확인 ({{ process_label.get(s.dfm_ack.process, s.dfm_ack.process) }})</h2>
  <table>
    <tr><th>확인 항목</th><th>상태</th></tr>
    {% for cid in dfm_ids %}<tr><td>{{ dfm_label.get(cid, cid) }} <span class="muted">({{ cid }})</span></td><td>{{ "✔ 확인" if cid in s.dfm_ack.checked_ids else "—" }}</td></tr>
    {% else %}<tr><td colspan="2" class="muted">해당 공정의 체크리스트 없음</td></tr>{% endfor %}
  </table>
  <table class="kv">
    <tr><th>누락 사양 인지 확인</th><td>{% for w in s.missing_spec_warnings_acked %}{{ warning_label.get(w, w) }} ({{ w }}){% if not loop.last %}, {% endif %}{% else %}해당 없음{% endfor %}</td></tr>
    <tr><th>미해결 3D 핀</th><td>{{ s.unresolved_pin_count }}건</td></tr>
  </table>

  <div class="notice"><b>법적 고지</b> — {{ legal }}</div>
  <p class="muted">Snapshot SHA-256: <span class="hash">{{ sha }}</span> (sha256 of json.dumps(snapshot, sort_keys=True, separators=(",", ":")))</p>
</body></html>""")


def _dfm_ids(snapshot: dict[str, Any]) -> list[str]:
    process = snapshot["dfm_ack"]["process"]
    if process.startswith("CNC_"):
        return DFM_ORDER["CNC"]
    if process.startswith("PRINT_"):
        return DFM_ORDER["PRINT"]
    return list(snapshot["dfm_ack"]["checked_ids"])


def render_approval_html(snapshot: dict[str, Any], snapshot_sha256: str) -> str:
    approved = datetime.fromisoformat(snapshot["approved_at"])
    if approved.tzinfo is None:
        approved = approved.replace(tzinfo=timezone.utc)
    return TEMPLATE.render(
        s=snapshot, sha=snapshot_sha256, t=snapshot["target_rev"], c=snapshot["target_rev"]["spec_card"],
        kst=approved.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S"), utc=approved.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        legal=LEGAL_NOTICE, dfm_ids=_dfm_ids(snapshot), process_label=PROCESS_LABEL, tolerance_label=TOLERANCE_LABEL,
        insert_label=INSERT_LABEL, inspection_label=INSPECTION_LABEL, dfm_label=DFM_LABEL, warning_label=WARNING_LABEL,
    )


def render_approval_pdf(snapshot: dict[str, Any], snapshot_sha256: str) -> bytes:
    from weasyprint import HTML  # heavy import: only when an approval happens

    return HTML(string=render_approval_html(snapshot, snapshot_sha256)).write_pdf()
