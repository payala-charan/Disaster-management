import { useState } from "react";

/** Runs a mutation, tracks which one is busy, and keeps the last error message (e.g. a 409 explanation). */
export function useMutation() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      after?.();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, run, clearError: () => setError(null) };
}
