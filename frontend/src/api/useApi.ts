import { useEffect, useRef, useState } from "react";
import { liveBus } from "./live";

/**
 * Loads data on mount; call reload() to fetch again after a change.
 * Also re-fetches automatically when the backend pushes a change over the WebSocket (live updates).
 * Existing data stays on screen while a reload is in flight, so pages do not flicker.
 */
export function useApi<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const hasData = useRef(false);

  useEffect(() => {
    const onRefresh = () => setTick((t) => t + 1);
    liveBus.addEventListener("refresh", onRefresh);
    return () => liveBus.removeEventListener("refresh", onRefresh);
  }, []);

  useEffect(() => {
    let alive = true;
    loader()
      .then((d) => {
        if (!alive) return;
        hasData.current = true;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        // A failed background refresh keeps the last good data on screen; only a first load shows the error.
        if (alive && !hasData.current) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  return {
    data,
    error,
    loading: data === null && error === null,
    reload: () => setTick((t) => t + 1),
  };
}
