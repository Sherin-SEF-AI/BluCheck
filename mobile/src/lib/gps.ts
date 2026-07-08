import * as Location from "expo-location";
import type { Gps } from "./api";

export class LocationDeniedError extends Error {
  constructor() {
    super("Location permission is required to record an inspection.");
  }
}

// Capture a high-accuracy fix. Throws if permission is denied so the capture flow
// can refuse to proceed with a clear message.
export async function captureGps(): Promise<Gps> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  if (status !== "granted") {
    throw new LocationDeniedError();
  }
  const pos = await Location.getCurrentPositionAsync({
    accuracy: Location.Accuracy.Highest,
  });
  return {
    lat: pos.coords.latitude,
    lon: pos.coords.longitude,
    accuracy_m: pos.coords.accuracy ?? null,
  };
}
