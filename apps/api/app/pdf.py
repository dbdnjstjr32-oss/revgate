"""가공 착수 승인 증빙 PDF (WeasyPrint) — snapshot of the approved diff run (blueprint §4 [6])."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, select_autoescape

_ENV = Environment(autoescape=select_autoescape(default=True, default_for_string=True))

TEMPLATE = _ENV.from_string("""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>RevGate 가공 착수 승인 증빙 {{ s.part.part_number }}</title>
<meta name="author" content="RevGate">
<meta name="description" content="snapshot sha256 {{ s.snapshot_sha256 }}">
<style>
  @page { size: A4; margin: 16mm 14mm 18mm;
          @bottom-right { content: "RevGate · " counter(page) " / " counter(pages); font-size: 8pt; color: #666; } }
  body { font-family: "NanumGothic", "DejaVu Sans", sans-serif; font-size: 9.5pt; color: #111; }
  h1 { font-size: 16pt; margin: 0 0 2mm; }
  .sub { color: #555; margin-bottom: 6mm; }
  table { width: 100%; border-collapse: collapse; margin: 3mm 0 6mm; }
  th, td { border: 0.4pt solid #bbb; padding: 1.6mm 2mm; text-align: left; vertical-align: top; }
  th { background: #f1f3f5; }
  .kv th { width: 34%; }
  .sev-CRITICAL { color: #b00020; font-weight: bold; }
  .sev-MAJOR { color: #a15c00; font-weight: bold; }
  .hash { font-family: "DejaVu Sans Mono", monospace; font-size: 7.5pt; word-break: break-all; }
  .stamp { border: 1.2pt solid #0a7; color: #0a7; display: inline-block; padding: 2mm 4mm; font-weight: bold; }
</style></head>
<body>
  <h1>가공 착수 승인 증빙</h1>
  <div class="sub">RevGate Approval Proof · {{ s.approved_at_kst }} (KST) · link {{ s.link.id }}</div>
  <div class="stamp">APPROVED — {{ s.link.recipient_name }} ({{ s.link.masked_contact }})</div>
  <table class="kv">
    <tr><th>부품 번호 / 이름</th><td>{{ s.part.part_number }} / {{ s.part.name }}</td></tr>
    <tr><th>리비전 (Base → Target)</th><td>{{ s.base.rev_label }} → {{ s.target.rev_label }}</td></tr>
    <tr><th>Base SHA-256</th><td class="hash">{{ s.base.file_sha256 }}</td></tr>
    <tr><th>Target SHA-256</th><td class="hash">{{ s.target.file_sha256 }}</td></tr>
    <tr><th>Diff run</th><td>{{ s.diff_run.id }}</td></tr>
    <tr><th>정합</th><td>{{ s.diff_run.align_method }} (confidence {{ s.confidence }})</td></tr>
    <tr><th>체적 변화</th><td>{{ s.summary.volume_delta_mm3 }} mm³ · Boolean {{ s.summary.boolean_status }}</td></tr>
    <tr><th>승인 시각 (UTC)</th><td>{{ s.approved_at }}</td></tr>
  </table>

  <h2>변경점 체크리스트 ({{ s["items"]|length }}건)</h2>
  <table>
    <tr><th>#</th><th>등급</th><th>항목</th><th>확정 팩트</th><th>확인</th></tr>
    {% for it in s["items"] %}
    <tr><td>{{ loop.index }}</td><td class="sev-{{ it.severity }}">{{ it.severity }}</td>
        <td>[{{ it.category }}] {{ it.title }}{% if it.hint_text %}<br><small>(참고 추정) {{ it.hint_text }}</small>{% endif %}</td>
        <td>{% for k, v in it.fact.items() %}{{ k }}={{ v }}{% if not loop.last %}, {% endif %}{% endfor %}</td>
        <td>{% if it.checked %}✔ {{ it.checked_by_contact }}{% else %}—{% endif %}</td></tr>
    {% endfor %}
  </table>

  <h2>3D 핀 코멘트 ({{ s.pins|length }}건)</h2>
  <table>
    <tr><th>작성자</th><th>면 / 측</th><th>내용</th><th>상태</th></tr>
    {% for p in s.pins %}
    <tr><td>{{ p.author_label }} ({{ p.author_type }})</td><td>face {{ p.face_id }} / {{ p.rev_side }}</td>
        <td>{{ p.body }}</td><td>{{ "해결" if p.resolved else "미해결" }}</td></tr>
    {% else %}
    <tr><td colspan="4">없음</td></tr>
    {% endfor %}
  </table>

  <p>Snapshot SHA-256: <span class="hash">{{ s.snapshot_sha256 }}</span></p>
</body></html>""")


def render_approval_pdf(snapshot: dict[str, Any]) -> bytes:
    from weasyprint import HTML  # heavy import: only when an approval happens

    html = TEMPLATE.render(s=snapshot)
    return HTML(string=html).write_pdf()
