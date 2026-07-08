import { useEffect, useRef } from "react";

// Poll `fn` every `ms`, but only while the tab is visible. Hidden tabs (backgrounded, another
// tab focused) stop polling entirely and run one refresh on becoming visible again. Saves the
// API from pointless background load. `fn` is kept in a ref so callers can pass an inline
// closure without resetting the interval every render.
export function usePolling(fn: () => void, ms: number, enabled = true): void {
  const saved = useRef(fn);
  useEffect(() => { saved.current = fn; });

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer == null) timer = setInterval(() => saved.current(), ms);
    };
    const stop = () => {
      if (timer != null) { clearInterval(timer); timer = null; }
    };
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        saved.current(); // catch up immediately on return
        start();
      }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [ms, enabled]);
}
