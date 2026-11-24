"use client";

/** GitHub-PR style change checklist: [확정] facts are always shown apart from the (참고 추정) hint. */

import type { Change } from "@revgate/contracts";

import { SEVERITY_STYLE, factValue } from "@/lib/format";
import type { DiffItem } from "@/lib/types";

export function ChangeList({ items, changes, selectedKey, onSelect, onToggle, canCheck }: {
  items: DiffItem[];
  changes: Change[];
  selectedKey: string | null;
  onSelect: (changeKey: string) => void;
  onToggle: (item: DiffItem, checked: boolean) => void;
  canCheck: boolean;
}) {
  const byKey = new Map(changes.map((c) => [c.change_id, c]));
  const done = items.filter((i) => i.checked).length;
  return (
    <div data-testid="change-list" className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-between text-xs text-slate-600">
        <span className="font-semibold text-slate-800">변경점 {items.length}건</span>
        <span data-testid="change-progress">확인 {done}/{items.length}</span>
      </div>
      {items.length === 0 && <p className="text-sm text-slate-500">기하학적 변경점이 없습니다.</p>}
      <ul className="space-y-2 overflow-y-auto">
        {items.map((it) => {
          const ch = byKey.get(it.change_key);
          const hint = ch?.engineering_hint;
          return (
            <li
              key={it.id}
              data-testid="change-item"
              data-change-key={it.change_key}
              data-category={it.category}
              data-checked={it.checked ? "true" : "false"}
              className={`rounded-lg border p-2.5 ${selectedKey === it.change_key ? "border-amber-400 bg-amber-50" : "border-slate-200 bg-white"}`}
            >
              <div className="flex items-start gap-2">
                <input
                  data-testid="change-check"
                  type="checkbox"
                  className="mt-0.5 h-6 w-6 shrink-0 accent-indigo-600 p-2"
                  checked={it.checked}
                  disabled={!canCheck}
                  onChange={(e) => onToggle(it, e.target.checked)}
                  aria-label="변경점 확인"
                />
                <button type="button" className="min-h-[44px] flex-1 text-left" onClick={() => onSelect(it.change_key)}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${SEVERITY_STYLE[it.severity]}`}>{it.severity}</span>
                    <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] font-bold text-white">확정</span>
                    <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{it.category}</span>
                  </div>
                  <p data-testid="change-title" className="mt-1 text-sm font-semibold text-slate-900">{it.title}</p>
                </button>
              </div>
              <dl data-testid="change-fact" className="mt-2 grid grid-cols-2 gap-x-2 gap-y-0.5 rounded-md bg-slate-50 p-2 text-[11px]">
                {Object.entries(it.fact).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-slate-500">{k}</dt>
                    <dd className="font-mono text-slate-800">{factValue(k, v)}</dd>
                  </div>
                ))}
              </dl>
              {it.hint_text && (
                <p data-testid="change-hint" className="mt-2 rounded-md border border-dashed border-sky-300 bg-sky-50 px-2 py-1 text-[11px] text-sky-900">
                  (참고 추정{hint ? ` · ${hint.confidence}` : ""}) {it.hint_text.replace(/^참고 추정:\s*/, "")}
                </p>
              )}
              {it.checked && <p className="mt-1 text-[10px] text-emerald-700">✔ {it.checked_by_contact}</p>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
