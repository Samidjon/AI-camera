"use client";

import { useEffect } from "react";

/** Registers the service worker that makes the dashboard installable and open offline. */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (
      process.env.NODE_ENV !== "production" ||
      !("serviceWorker" in navigator)
    )
      return;
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .catch(() => undefined);
  }, []);
  return null;
}
