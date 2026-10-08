"use client";

/* One config section's edit state: load, local edits, save, and the
   "route not live yet" case (seeds shown read-only). */

import { useCallback, useEffect, useState } from "react";
import { useToast } from "../../../../../ui";
import { errorText, isMissingRoute } from "../_components/serviceApi";
import { loadSection, saveSection, type ConfigSection } from "../_components/configApi";

export interface SectionState<T> {
  rows: T[];
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  /** The server has no config route yet: rows are the seeds, read-only. */
  offline: boolean;
  error: string | null;
  setRow(index: number, patch: Partial<T>): void;
  addRow(row: T): void;
  removeRow(index: number): void;
  save(): Promise<void>;
  reset(): void;
}

export function useSection<T>(slug: string, section: ConfigSection, seeds: T[]): SectionState<T> {
  const toast = useToast();
  const [base, setBase] = useState<T[]>(seeds);
  const [rows, setRows] = useState<T[]>(seeds);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    let live = true;
    loadSection<T>(slug, section)
      .then(list => {
        if (!live) return;
        const next = list.length ? list : seeds;
        setBase(next); setRows(next); setOffline(false); setError(null);
      })
      .catch(err => {
        if (!live) return;
        if (isMissingRoute(err)) setOffline(true);
        else setError(errorText(err));
      })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // seeds are module constants per section; re-running on them would loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, section]);

  const setRow = useCallback((index: number, patch: Partial<T>) => {
    setRows(prev => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
    setDirty(true);
  }, []);
  const addRow = useCallback((row: T) => { setRows(prev => [...prev, row]); setDirty(true); }, []);
  const removeRow = useCallback((index: number) => {
    setRows(prev => prev.filter((_, i) => i !== index));
    setDirty(true);
  }, []);
  const reset = useCallback(() => { setRows(base); setDirty(false); }, [base]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const saved = await saveSection<T>(slug, section, rows);
      setBase(saved); setRows(saved); setDirty(false);
      toast.success("Saved.");
    } catch (err) {
      toast.error("Could not save", errorText(err));
    } finally {
      setSaving(false);
    }
  }, [slug, section, rows, toast]);

  return { rows, loading, saving, dirty, offline, error, setRow, addRow, removeRow, save, reset };
}
