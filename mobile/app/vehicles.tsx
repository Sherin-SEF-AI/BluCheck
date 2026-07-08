import { useEffect, useState } from "react";
import { View, Text, FlatList, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { router } from "expo-router";
import { listVehicles, getMyRewards, type Vehicle, ApiError } from "@/lib/api";
import { clearSession, getName } from "@/lib/auth";
import { pendingCount } from "@/lib/uploadQueue";
import { colors } from "@/lib/theme";

export default function Home() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [name, setName] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [points, setPoints] = useState<number | null>(null);
  const [tier, setTier] = useState<string | null>(null);

  useEffect(() => {
    getName().then((n) => setName(n ?? "")).catch(() => undefined);
    const check = () => pendingCount().then(setPending).catch(() => undefined);
    check();
    const t = setInterval(check, 2000);
    getMyRewards().then((r) => { setPoints(r.points); setTier(r.tier); }).catch(() => undefined);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        setVehicles(await listVehicles());
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          await clearSession();
          router.replace("/login");
          return;
        }
        setError(e instanceof Error ? e.message : "Failed to load your vehicle");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function logout() {
    await clearSession();
    router.replace("/login");
  }

  function startInspection(v: Vehicle) {
    router.push({ pathname: "/capture", params: { vehicleId: v.id, plate: v.registration_plate } });
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  const single = vehicles.length === 1 ? vehicles[0] : null;

  return (
    <View style={styles.container}>
      {name ? <Text style={styles.greeting}>Hello, {name}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      {single ? (
        <View style={styles.card}>
          <Text style={styles.cardLabel}>YOUR CAR</Text>
          <Text style={styles.plateBig}>{single.registration_plate}</Text>
          {single.model ? <Text style={styles.model}>{single.model}</Text> : null}
          <Pressable style={styles.button} onPress={() => startInspection(single)}>
            <Text style={styles.buttonText}>Start inspection</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={vehicles}
          keyExtractor={(v) => v.id}
          ListEmptyComponent={<Text style={styles.dim}>No vehicle assigned to your account.</Text>}
          renderItem={({ item }) => (
            <Pressable style={styles.row} onPress={() => startInspection(item)}>
              <Text style={styles.plate}>{item.registration_plate}</Text>
              <Text style={styles.model}>{item.model ?? "Vehicle"}</Text>
            </Pressable>
          )}
        />
      )}

      {pending > 0 ? (
        <Pressable style={styles.pendingBanner} onPress={() => router.push("/upload-status")}>
          <View style={styles.pendingDot} />
          <Text style={styles.pendingText}>
            {pending} upload{pending > 1 ? "s" : ""} in progress. Tap to view.
          </Text>
        </Pressable>
      ) : null}

      <Pressable style={styles.rewardsBtn} onPress={() => router.push("/rewards")}>
        <View>
          <Text style={styles.rewardsLabel}>MY REWARDS</Text>
          <Text style={styles.rewardsTier}>{tier ?? "Keep inspecting to earn"}</Text>
        </View>
        <Text style={styles.rewardsPoints}>{points !== null ? `${points} pts` : "→"}</Text>
      </Pressable>

      <Pressable style={styles.secondary} onPress={() => router.push("/history")}>
        <Text style={styles.secondaryText}>View my inspections</Text>
      </Pressable>
      <Pressable style={styles.secondary} onPress={logout}>
        <Text style={styles.logoutText}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  greeting: { color: colors.text, fontSize: 20, fontWeight: "700", marginBottom: 16, marginTop: 4 },
  card: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 20, marginBottom: 12 },
  cardLabel: { color: colors.textDim, fontFamily: colors.mono, fontSize: 12, letterSpacing: 1 },
  plateBig: { color: colors.text, fontSize: 30, fontFamily: colors.mono, letterSpacing: 2, marginTop: 6 },
  row: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 6, padding: 16, marginBottom: 10 },
  plate: { color: colors.text, fontSize: 18, fontFamily: colors.mono, letterSpacing: 1 },
  model: { color: colors.textDim, marginTop: 4 },
  dim: { color: colors.textDim, textAlign: "center", marginTop: 40 },
  error: { color: colors.danger, marginBottom: 8 },
  button: { backgroundColor: colors.accent, borderRadius: 6, padding: 16, alignItems: "center", marginTop: 18 },
  buttonText: { color: "#ffffff", fontWeight: "700", fontSize: 16 },
  secondary: { padding: 14, alignItems: "center" },
  secondaryText: { color: colors.accent },
  logoutText: { color: colors.textDim },
  pendingBanner: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surfaceRaised, borderColor: colors.warn, borderWidth: 1, borderRadius: 6, padding: 12, marginTop: 8 },
  pendingDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.warn },
  pendingText: { color: colors.text, fontSize: 13 },
  rewardsBtn: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.surface, borderColor: colors.accent, borderWidth: 1, borderRadius: 8, padding: 16, marginTop: 12 },
  rewardsLabel: { color: colors.textDim, fontFamily: colors.mono, fontSize: 11, letterSpacing: 1 },
  rewardsTier: { color: colors.text, fontSize: 15, fontWeight: "700", marginTop: 3 },
  rewardsPoints: { color: colors.accent, fontSize: 18, fontWeight: "800", fontFamily: colors.mono },
});
