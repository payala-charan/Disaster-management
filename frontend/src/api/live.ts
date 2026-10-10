/**
 * Live updates over the backend WebSocket (/ws).
 * - Any situation_changed / run_finished / action_updated message triggers a refresh of every page
 *   that uses useApi (through `liveBus`).
 * - alert messages become toast notifications.
 * Reconnects automatically with backoff (1 s, 2 s, ... max 15 s).
 */
import { useEffect, useRef, useState } from "react";
import { USE_MOCKS, WS_URL } from "./client";
import type { Alert } from "../types";

export const liveBus = new EventTarget();
export const requestRefresh = () => liveBus.dispatchEvent(new Event("refresh"));

export type Toast = { id: string; alert: Alert };
type Status = "connecting" | "live" | "offline" | "mock";

export function useLiveUpdates() {
  const [status, setStatus] = useState<Status>(USE_MOCKS ? "mock" : "connecting");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const retry = useRef(0);

  useEffect(() => {
    if (USE_MOCKS) return;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let debounce: ReturnType<typeof setTimeout> | undefined;

    const refreshSoon = () => {  // coalesce bursts (a run emits several messages)
      clearTimeout(debounce);
      debounce = setTimeout(requestRefresh, 150);
    };

    const connect = () => {
      setStatus("connecting");
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        retry.current = 0;
        setStatus("live");
        refreshSoon(); // catch up on anything missed while disconnected
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as { type: string; data: unknown };
          if (msg.type === "alert") {
            const alert = msg.data as Alert;
            setToasts((t) => [...t.slice(-4), { id: `${alert.id}-${Date.now()}`, alert }]);
          }
          refreshSoon();
        } catch {
          /* ignore malformed messages */
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setStatus("offline");
        const delay = Math.min(15_000, 1000 * 2 ** retry.current++);
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      clearTimeout(debounce);
      ws?.close();
    };
  }, []);

  const dismiss = (id: string) => setToasts((t) => t.filter((x) => x.id !== id));
  return { status, toasts, dismiss };
}
