import { Platform } from "react-native";
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import * as FileSystem from "expo-file-system";
import Constants from "expo-constants";
import { savePushToken, listMyInspections } from "./api";

// Show notifications while the app is foregrounded too.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

const SEEN_FILE = (FileSystem.documentDirectory ?? "") + "review-status.json";

// True once a native FCM/push token is registered with the backend. When set, the backend
// delivers review notifications via push, so the local-poll fallback must NOT also raise a
// notification (that would double-notify the driver). The in-app banner still shows.
let fcmActive = false;

export type ReviewChange = { id: string; status: "approved" | "rejected"; plate: string };

// Request notification permission, set up the Android channel, and best-effort
// register an Expo push token. Push delivery requires an EAS projectId + FCM;
// when that is not configured we silently fall back to local polling.
export async function initNotifications(): Promise<void> {
  try {
    const existing = await Notifications.getPermissionsAsync();
    let granted = existing.granted;
    if (!granted && existing.canAskAgain) {
      const req = await Notifications.requestPermissionsAsync();
      granted = req.granted;
    }
    if (!granted) return;

    if (Platform.OS === "android") {
      await Notifications.setNotificationChannelAsync("reviews", {
        name: "Inspection reviews",
        importance: Notifications.AndroidImportance.HIGH,
        lightColor: "#4a9d8e",
      });
    }

    // Register a push token so the backend can reach the driver even when the app is
    // closed. Prefer the native FCM device token (works with google-services.json + the
    // backend FCM sender); fall back to an Expo push token if an EAS projectId is set.
    // If neither is available, delivery falls back to the in-app poll below.
    try {
      if (Device.isDevice) {
        let token: string | null = null;
        try {
          token = (await Notifications.getDevicePushTokenAsync()).data as string;
        } catch {
          const projectId =
            (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)?.eas?.projectId ??
            (Constants as { easConfig?: { projectId?: string } }).easConfig?.projectId;
          if (projectId) token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
        }
        if (token) {
          try {
            await savePushToken(token);
            fcmActive = true; // backend push is live; disable local-poll notifications
          } catch {
            // token save failed; keep the local-poll fallback active
          }
        }
      }
    } catch {
      // No FCM/projectId configured: rely on in-app polling below.
    }
  } catch {
    // Notification setup is best-effort; never block the app on it.
  }
}

async function readSeen(): Promise<Record<string, string>> {
  try {
    const t = await FileSystem.readAsStringAsync(SEEN_FILE);
    return JSON.parse(t) as Record<string, string>;
  } catch {
    return {};
  }
}

// Poll the driver's inspections, compare against the last-seen status snapshot,
// and raise a local notification for anything freshly approved/rejected. Returns
// the changes so the caller can also show an in-app banner. On the very first
// run it only records a baseline (no notifications) to avoid a burst on install.
export async function checkReviewUpdates(): Promise<ReviewChange[]> {
  const seen = await readSeen();
  const firstRun = Object.keys(seen).length === 0;

  let items;
  try {
    items = (await listMyInspections()).items;
  } catch {
    return [];
  }

  const next: Record<string, string> = {};
  const changes: ReviewChange[] = [];
  for (const it of items) {
    next[it.id] = it.status;
    const prev = seen[it.id];
    if (
      !firstRun &&
      prev &&
      prev !== it.status &&
      (it.status === "approved" || it.status === "rejected")
    ) {
      changes.push({ id: it.id, status: it.status, plate: it.vehicle_plate });
    }
  }

  await FileSystem.writeAsStringAsync(SEEN_FILE, JSON.stringify(next)).catch(() => undefined);

  // When FCM push is live, the backend already delivers the system notification. Skip the
  // local one to avoid double-notifying; the caller still gets `changes` for the in-app banner.
  if (fcmActive) return changes;

  for (const c of changes) {
    await Notifications.scheduleNotificationAsync({
      content: {
        title: c.status === "approved" ? "Inspection approved" : "Inspection rejected",
        body:
          c.status === "approved"
            ? `${c.plate} passed the cleanliness check.`
            : `${c.plate} was rejected. Open BluCheck for details.`,
        data: { inspectionId: c.id },
      },
      trigger: null,
    }).catch(() => undefined);
  }

  return changes;
}
