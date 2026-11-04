import { useEffect, useState } from "react";
import { fetchIncidentSnapshot } from "./incidents.js";

export function useIncidents(reload) {
  const [state, setState] = useState({
    items: [],
    meta: null,
    status: "loading",
    error: "",
    lastSuccess: null,
  });
  useEffect(() => {
    let disposed = false,
      controller;
    async function refresh() {
      if (disposed || document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      const request = controller;
      try {
        const result = await fetchIncidentSnapshot({ signal: request.signal });
        if (!disposed && !request.signal.aborted)
          setState({
            ...result,
            status: "ready",
            error: "",
            lastSuccess: new Date(),
          });
      } catch (error) {
        if (!disposed && !request.signal.aborted)
          setState((previous) => ({
            ...previous,
            status: "error",
            error: error.message,
          }));
      }
    }
    refresh();
    const timer = window.setInterval(refresh, 45000);
    const visible = () => {
      if (document.visibilityState !== "hidden") refresh();
    };
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      disposed = true;
      controller?.abort();
      clearInterval(timer);
      window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [reload]);
  return state;
}
