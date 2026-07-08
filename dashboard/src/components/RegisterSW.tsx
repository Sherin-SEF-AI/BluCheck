"use client";
import { useEffect } from "react";

// Registers the service worker so the dashboard is installable ("Add to Home Screen")
// and works as an app shell. No-op on browsers without service-worker support.
export default function RegisterSW() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);
  return null;
}
