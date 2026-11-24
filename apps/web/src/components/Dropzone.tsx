"use client";

import { useRef, useState } from "react";

export function Dropzone({ onFiles, label, hint, testId, disabled = false, multiple = false }: {
  onFiles: (files: File[]) => void;
  label: string;
  hint?: string;
  testId: string;
  disabled?: boolean;
  multiple?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      data-testid={testId}
      role="button"
      tabIndex={0}
      aria-disabled={disabled}
      onClick={() => !disabled && input.current?.click()}
      onKeyDown={(e) => e.key === "Enter" && !disabled && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled && e.dataTransfer.files.length) onFiles(Array.from(e.dataTransfer.files));
      }}
      className={`cursor-pointer rounded-lg border-2 border-dashed px-4 py-5 text-center text-sm transition ${
        disabled ? "cursor-not-allowed border-slate-200 text-slate-400" : over ? "border-indigo-500 bg-indigo-50 text-indigo-700" : "border-slate-300 text-slate-600 hover:border-indigo-400"
      }`}
    >
      <p className="font-medium">{label}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      <input
        ref={input}
        data-testid={`${testId}-input`}
        type="file"
        accept=".step,.stp"
        multiple={multiple}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}
