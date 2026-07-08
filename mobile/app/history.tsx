import { useCallback, useEffect, useState } from "react";
import { View, Text, FlatList, StyleSheet, ActivityIndicator, Pressable, RefreshControl, Alert, Image, ScrollView } from "react-native";
import { router } from "expo-router";
import { listMyInspections, getMyInspection, appealInspection, type InspectionSummary, type ZoneIssueLabel, type FlaggedFrame } from "@/lib/api";
import { colors, statusColor } from "@/lib/theme";

type RejectInfo = { reason: string | null; vehicleId: string; plate: string; labels: ZoneIssueLabel[]; flagged: FlaggedFrame[] };

function pretty(key: string): string {
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// Which capture group a flagged zone belongs to. A re-clean only re-films the flagged groups.
const INTERIOR_ZONES = new Set(["seats", "floor_mats", "dashboard_console"]);
function recleanTargets(labels: ZoneIssueLabel[]): { groups: string; zones: string } {
  const groups = new Set<string>();
  const zoneKeys = new Set<string>();
  for (const l of labels) {
    zoneKeys.add(l.zone_key);
    groups.add(INTERIOR_ZONES.has(l.zone_key) ? "interior" : "exterior");
  }
  // No labels -> fall back to a full re-clean so nothing is skipped.
  const g = groups.size ? Array.from(groups) : ["exterior", "interior"];
  return { groups: g.join(","), zones: Array.from(zoneKeys).map(pretty).join(", ") };
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
            info[i.id] = { reason: d.reject_reason, vehicleId: d.vehicle_id, plate: d.vehicle_plate, labels: d.reject_labels ?? [], flagged: d.flagged_frames ?? [] };
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
    Alert.alert("Disagree with this result?", "Your inspection will be re-reviewed right away. Only do this if you believe the rejection was wrong.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Request re-review", onPress: async () => {
          setAppealing(id);
          try {
            const res = await appealInspection(id);
            if (res.status === "approved") {
              Alert.alert("Appeal accepted", "Re-reviewed and accepted — your inspection now passes.");
            } else if (res.status === "rejected") {
              Alert.alert("Rejection stands", res.reason || "Re-reviewed, but the rejection was upheld.");
            } else {
              Alert.alert("Sent for review", "A reviewer will take another look. You'll be notified of the outcome.");
            }
            await load();
          }
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
                  {rej && rej.flagged.length > 0 ? (
                    <>
                      <Text style={styles.photoLabel}>Exactly what to clean:</Text>
                      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.photoRow}>
                        {rej.flagged.map((f, idx) => (
                          <View key={idx} style={styles.flaggedCard}>
                            <Image source={{ uri: f.thumb_url }} style={styles.photo} />
                            <Text style={styles.flaggedZone} numberOfLines={1}>{f.zone_label}</Text>
                            <Text style={styles.flaggedIssue} numberOfLines={1}>
                              {f.severity ? `${pretty(f.severity)} ` : ""}{f.issue_key ? pretty(f.issue_key) : "issue"}
                            </Text>
                          </View>
                        ))}
                      </ScrollView>
                    </>
                  ) : null}
                  {rej ? (
                    <Pressable
                      style={styles.reinspect}
                      onPress={() => {
                        const t = recleanTargets(rej.labels);
                        router.push({
                          pathname: "/capture",
                          params: { vehicleId: rej.vehicleId, plate: rej.plate, reinspectionOf: item.id, groups: t.groups, zones: t.zones },
                        });
                      }}
                    >
                      <Text style={styles.reinspectText}>
                        Re-clean {recleanTargets(rej.labels).groups.split(",").length < 2 ? `(${recleanTargets(rej.labels).groups} only)` : "& re-inspect"}
                      </Text>
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
  photo: { width: 110, height: 74, borderRadius: 6, backgroundColor: colors.surfaceRaised },
  flaggedCard: { marginRight: 8, width: 110 },
  flaggedZone: { color: colors.text, fontSize: 12, fontWeight: "700", marginTop: 4 },
  flaggedIssue: { color: colors.danger, fontSize: 11, marginTop: 1 },
  reinspect: { marginTop: 10, backgroundColor: colors.accent, borderRadius: 6, padding: 12, alignItems: "center" },
  reinspectText: { color: "#ffffff", fontWeight: "700" },
  appeal: { marginTop: 8, borderColor: colors.border, borderWidth: 1, borderRadius: 6, padding: 10, alignItems: "center" },
  appealText: { color: colors.textDim, fontSize: 13 },
});
