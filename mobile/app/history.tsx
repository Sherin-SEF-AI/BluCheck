import { useCallback, useEffect, useState } from "react";
import { View, Text, FlatList, StyleSheet, ActivityIndicator, Pressable, RefreshControl, Alert, Image, ScrollView } from "react-native";
import { router } from "expo-router";
import { listMyInspections, getMyInspection, appealInspection, type InspectionSummary, type ZoneIssueLabel } from "@/lib/api";
import { colors, statusColor } from "@/lib/theme";

type RejectInfo = { reason: string | null; vehicleId: string; plate: string; labels: ZoneIssueLabel[]; photos: string[] };

function pretty(key: string): string {
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export default function History() {
  const [items, setItems] = useState<InspectionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  // Reject reason + vehicle for rejected inspections, fetched from detail.
  const [rejects, setRejects] = useState<Record<string, RejectInfo>>({});
  const [appealing, setAppealing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listMyInspections();
      setItems(res.items);
      const rejected = res.items.filter((i) => i.status === "rejected");
      const info: Record<string, RejectInfo> = {};
      await Promise.all(
        rejected.map(async (i) => {
          try {
            const d = await getMyInspection(i.id);
            const photos = (d.captures ?? []).flatMap((c) => c.frames.filter((f) => f.selected).map((f) => f.thumb_url)).slice(0, 6);
            info[i.id] = { reason: d.reject_reason, vehicleId: d.vehicle_id, plate: d.vehicle_plate, labels: d.reject_labels ?? [], photos };
          } catch { /* ignore */ }
        })
      );
      setRejects(info);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function onAppeal(id: string) {
    Alert.alert("Disagree with this result?", "A person will re-review your inspection. Only do this if you believe the automated rejection was wrong.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Send for review", onPress: async () => {
          setAppealing(id);
          try { await appealInspection(id); Alert.alert("Sent", "A reviewer will take another look. You'll be notified of the outcome."); await load(); }
          catch (e) { Alert.alert("Could not submit", e instanceof Error ? e.message : "Try again"); }
          finally { setAppealing(null); }
        },
      },
    ]);
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={items}
        keyExtractor={(i) => i.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.accent} />}
        ListEmptyComponent={<Text style={styles.dim}>No inspections yet.</Text>}
        renderItem={({ item }) => {
          const rej = rejects[item.id];
          return (
            <View style={styles.row}>
              <View style={styles.rowTop}>
                <Text style={styles.plate}>{item.vehicle_plate}</Text>
                <Text style={[styles.status, { color: statusColor[item.status] ?? colors.textDim }]}>
                  {item.status}
                </Text>
              </View>
              <Text style={styles.ts}>
                {item.captured_at_utc
                  ? new Date(item.captured_at_utc).toLocaleString("sv-SE", { timeZone: "Asia/Kolkata" }) + " IST"
                  : "-"}
              </Text>
              {item.status === "rejected" ? (
                <>
                  <Text style={styles.reject}>Rejected — re-clean checklist:</Text>
                  {rej && rej.labels.length > 0 ? (
                    <View style={styles.checklist}>
                      {rej.labels.map((l, idx) => (
                        <Text key={idx} style={styles.checkItem}>☐ {pretty(l.zone_key)}: {pretty(l.issue_key)}</Text>
                      ))}
                    </View>
                  ) : rej?.reason ? (
                    <Text style={styles.reject}>{rej.reason}</Text>
                  ) : null}
                  {rej && rej.photos.length > 0 ? (
                    <>
                      <Text style={styles.photoLabel}>Inspection photos — clean the areas above:</Text>
                      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.photoRow}>
                        {rej.photos.map((url, idx) => (
                          <Image key={idx} source={{ uri: url }} style={styles.photo} />
                        ))}
                      </ScrollView>
                    </>
                  ) : null}
                  {rej ? (
                    <Pressable
                      style={styles.reinspect}
                      onPress={() =>
                        router.push({
                          pathname: "/capture",
                          params: { vehicleId: rej.vehicleId, plate: rej.plate, reinspectionOf: item.id },
                        })
                      }
                    >
                      <Text style={styles.reinspectText}>Re-clean &amp; re-inspect</Text>
                    </Pressable>
                  ) : null}
                  <Pressable style={styles.appeal} disabled={appealing === item.id} onPress={() => onAppeal(item.id)}>
                    <Text style={styles.appealText}>{appealing === item.id ? "Sending…" : "I disagree — ask a person to review"}</Text>
                  </Pressable>
                </>
              ) : null}
            </View>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" },
  row: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 6, padding: 14, marginBottom: 10 },
  rowTop: { flexDirection: "row", justifyContent: "space-between" },
  plate: { color: colors.text, fontFamily: colors.mono, letterSpacing: 1, fontSize: 16 },
  status: { fontFamily: colors.mono },
  ts: { color: colors.textDim, fontFamily: colors.mono, marginTop: 6, fontSize: 12 },
  dim: { color: colors.textDim, textAlign: "center", marginTop: 40 },
  reject: { color: colors.danger, marginTop: 8, fontSize: 13 },
  checklist: { marginTop: 8, gap: 4 },
  checkItem: { color: colors.text, fontSize: 13, fontFamily: colors.mono },
  photoLabel: { color: colors.textDim, fontSize: 12, marginTop: 10 },
  photoRow: { marginTop: 6 },
  photo: { width: 96, height: 64, borderRadius: 6, marginRight: 6, backgroundColor: colors.surfaceRaised },
  reinspect: { marginTop: 10, backgroundColor: colors.accent, borderRadius: 6, padding: 12, alignItems: "center" },
  reinspectText: { color: "#ffffff", fontWeight: "700" },
  appeal: { marginTop: 8, borderColor: colors.border, borderWidth: 1, borderRadius: 6, padding: 10, alignItems: "center" },
  appealText: { color: colors.textDim, fontSize: 13 },
});
