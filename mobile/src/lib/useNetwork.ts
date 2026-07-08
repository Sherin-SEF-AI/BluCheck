import { useEffect, useState } from "react";
import * as Network from "expo-network";

// Lightweight connectivity hook. Polls network state; returns true when online.
export function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const state = await Network.getNetworkStateAsync();
        if (active) setOnline(Boolean(state.isConnected && state.isInternetReachable !== false));
      } catch {
        /* assume online on error */
      }
    };
    check();
    const t = setInterval(check, 4000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);
  return online;
}
