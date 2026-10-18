"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: FileDrop + useUpload.
   useUpload wraps uploadFile() (ticketed direct upload with progress) so a
   module only says which hotel and what purpose the file is for; the purpose
   drives retention and routing on the server.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from "react";
import { LuUpload } from "react-icons/lu";
import { ApiError, uploadFile } from "../lib/api";
import type { FileRef } from "../lib/types";
import { cx } from "./core";

/** Mirrors the server's FILE_MAX_BYTES default so users learn early, not after a 20 MB upload. */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export function FileDrop({
  onFiles, accept, multiple = false, disabled = false, title = "Drop a file here or tap to choose",
  hint, maxBytes = MAX_UPLOAD_BYTES, onReject, children,
}: {
  onFiles: (files: File[]) => void;
  /** Same syntax as <input accept>, e.g. ".csv,.xlsx,application/pdf". */
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  title?: ReactNode;
  hint?: ReactNode;
  maxBytes?: number;
  onReject?: (reason: string, file: File) => void;
  children?: ReactNode;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const take = (list: FileList | null) => {
    if (!list || disabled) return;
    const files = Array.from(list).slice(0, multiple ? undefined : 1);
    const ok: File[] = [];
    for (const f of files) {
      if (f.size > maxBytes) onReject?.(`${f.name} is larger than ${Math.round(maxBytes / 1024 / 1024)} MB.`, f);
      else ok.push(f);
    }
    if (ok.length) onFiles(ok);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    take(e.dataTransfer.files);
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      inputRef.current?.click();
    }
  };

  return (
    <div
      className="cx-drop"
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled || undefined}
      data-over={over || undefined}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={onKey}
      onDragOver={e => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <LuUpload aria-hidden="true" size={22} color="var(--clefs-gold-500)" />
      <div className="cx-drop__title">{title}</div>
      {hint && <div className="cx-drop__hint">{hint}</div>}
      {children}
      <input
        ref={inputRef}
        type="file"
        hidden
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={e => { take(e.target.files); e.target.value = ""; }}
      />
    </div>
  );
}

export interface UploadState {
  upload(file: File): Promise<FileRef | null>;
  progress: number;
  uploading: boolean;
  error: ApiError | null;
  result: FileRef | null;
  reset(): void;
}

export function useUpload(hotelSlug: string, purpose: string): UploadState {
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<FileRef | null>(null);

  const upload = useCallback(async (file: File) => {
    setUploading(true);
    setError(null);
    setResult(null);
    setProgress(0);
    try {
      const ref = await uploadFile(hotelSlug, file, purpose, setProgress);
      setResult(ref);
      return ref;
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, String((err as Error)?.message || err)));
      return null;
    } finally {
      setUploading(false);
    }
  }, [hotelSlug, purpose]);

  const reset = useCallback(() => {
    setProgress(0);
    setUploading(false);
    setError(null);
    setResult(null);
  }, []);

  return { upload, progress, uploading, error, result, reset };
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className={cx("cx-progress", className)} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="cx-progress__bar" style={{ width: `${pct}%` }} />
    </div>
  );
}
