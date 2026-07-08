import { useEffect, useRef, useState } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { Stack, router } from "expo-router";
import { StatusBar } from "expo-status-bar";
import * as Notifications from "expo-notifications";
import { getToken, getRole } from "@/lib/auth";
import { initNotifications, checkReviewUpdates, type ReviewChange } from "@/lib/notifications";
import { processQueue } from "@/lib/uploadQueue";
import { colors } from "@/lib/theme";

// App-wide watcher: whenever a driver is signed in, poll for review outcomes on ANY screen and
// surface each approve/reject as a local notification + in-app banner. Reacts to AUTH STATE
// (not just app launch, H5): the poll re-checks the session each tick, so it starts working
// immediately after a fresh login without needing a relaunch. Also kicks the upload queue once
// signed in, so interrupted uploads resume even if the driver never opens the Uploads screen.
function ReviewWatcher() {
  const [banner, setBanner] = useState<ReviewChange | null>(null);
  const inited = useRef(false);

  useEffect(() => {
    let alive = true;

    // Tapping a push notification (app closed/backgrounded) deep-links into history.
    const sub = Notifications.addNotificationResponseReceivedListener(() => {
      router.push("/history");
    });

    const poll = async () => {
      const token = await getToken();
      const role = await getRole();
      if (!token || role !== "driver") return; // not a signed-in driver yet; try again next tick
      if (!inited.current) {
        inited.current = true;
        initNotifications().catch(() => undefined);   // permission + push token
        processQueue().catch(() => undefined);        // resume any interrupted uploads
      }
      const changes = await checkReviewUpdates();
      if (alive && changes.length > 0) setBanner(changes[changes.length - 1]);
    };
    poll();
    const timer = setInterval(poll, 20000);

    return () => {
      alive = false;
      clearInterval(timer);
      sub.remove();
    };
  }, []);

  if (!banner) return null;
  const ok = banner.status === "approved";
  return (
    <Pressable
      style={[styles.banner, { borderColor: ok ? colors.ok : colors.danger }]}
      onPress={() => {
        setBanner(null);
        router.push("/history");
      }}
    >
      <Text style={[styles.bannerTitle, { color: ok ? colors.ok : colors.danger }]}>
        {ok ? "Inspection approved" : "Inspection rejected"}
      </Text>
      <Text style={styles.bannerBody}>{banner.plate}. Tap to view details.</Text>
    </Pressable>
  );
}

export default function RootLayout() {
  return (
    <>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          contentStyle: { backgroundColor: colors.bg },
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="login" options={{ title: "BluCheck" }} />
        <Stack.Screen name="register" options={{ title: "Register" }} />
        <Stack.Screen name="vehicles" options={{ title: "Home" }} />
        <Stack.Screen name="capture" options={{ title: "Record Inspection" }} />
        <Stack.Screen name="upload-status" options={{ title: "Uploads" }} />
        <Stack.Screen name="history" options={{ title: "My Inspections" }} />
      </Stack>
      <ReviewWatcher />
    </>
  );
}

const styles = StyleSheet.create({
  banner: {
    position: "absolute",
    top: 44,
    left: 12,
    right: 12,
    backgroundColor: colors.surfaceRaised,
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
  },
  bannerTitle: { fontWeight: "700", fontSize: 14 },
  bannerBody: { color: colors.text, marginTop: 4, fontSize: 13 },
});
