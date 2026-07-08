import { useEffect, useState, useCallback, useRef } from "react";
import { View, Text, FlatList, Pressable, StyleSheet, ActivityIndicator, RefreshControl, Alert, Animated } from "react-native";
import { router } from "expo-router";
import {
  getQueue,
  processQueue,
  cancelItem,
  setProgressListener,
  type QueueItem,
  type UploadProgress,
} from "@/lib/uploadQueue";
import { getMyInspection, type InspectionDetail } from "@/lib/api";
import { colors, statusColor } from "@/lib/theme";
import { useOnline } from "@/lib/useNetwork";

function mb(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Post-upload analysis lifecycle shown to the driver. Server statuses map onto these steps.
const TERMINAL = ["approved", "rejected", "failed"];
const STEPS = ["Video uploaded", "Processing video", "Analyzing cleanliness"];
function stageIndex(status: string): number {
  if (status === "pending") return 2; // scored / under review
  if (status === "approved" || status === "rejected") return 3; // result reached
  return 1; // uploading / processing
}

function AnalysisTracker({ detail }: { detail: InspectionDetail }) {
  const failed = detail.status === "failed";
  const terminal = detail.status === "approved" || detail.status === "rejected";
  const idx = stageIndex(detail.status);

  // Pulse the active step so the driver sees the analysis is live.
  const pulse = useRef(new Animated.Value(0.4)).current;
  useEffect(() => {
    if (terminal || failed) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 700, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0.4, duration: 700, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [terminal, failed, pulse]);

  // Fade + rise the result card in when it arrives.
  const reveal = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (terminal) Animated.spring(reveal, { toValue: 1, useNativeDriver: true, friction: 7 }).start();
  }, [terminal, reveal]);

  return (
    <View style={styles.tracker}>
      <Text style={styles.trackerTitle}>{detail.vehicle_plate}</Text>
      {STEPS.map((label, i) => {
        const done = i < idx || terminal;
        const active = i === idx && !terminal && !failed;
        return (
          <View key={i} style={styles.step}>
            {active ? (
              <Animated.View style={[styles.pulseDot, { opacity: pulse, transform: [{ scale: pulse }] }]} />
            ) : (
              <Text style={[styles.dot, { color: done ? colors.ok : colors.textDim }]}>{done ? "●" : "○"}</Text>
            )}
            <Text style={[styles.stepLabel, { color: done || active ? colors.text : colors.textDim }]}>{label}</Text>
            {active ? <ActivityIndicator size="small" color={colors.accent} style={{ marginLeft: "auto" }} /> : null}
          </View>
        );
      })}
      {terminal ? (
        <Animated.View style={[styles.result, { borderColor: detail.status === "approved" ? colors.ok : colors.danger, opacity: reveal, transform: [{ translateY: reveal.interpolate({ inputRange: [0, 1], outputRange: [12, 0] }) }] }]}>
          <Text style={[styles.resultTitle, { color: detail.status === "approved" ? colors.ok : colors.danger }]}>
            {detail.status === "approved" ? "Passed — vehicle is clean" : "Needs re-cleaning"}
          </Text>
          {detail.status === "rejected" && detail.reject_reason ? (
            <Text style={styles.resultReason}>{detail.reject_reason}</Text>
          ) : null}
          {detail.status === "rejected" ? (
            <Pressable style={styles.resultBtn} onPress={() => router.push("/history")}>
              <Text style={styles.resultBtnText}>See what to clean →</Text>
            </Pressable>
          ) : null}
        </Animated.View>
      ) : failed ? (
        <Text style={styles.error}>Processing failed. Please re-record and upload again.</Text>
      ) : (
        <Text style={styles.trackerHint}>Analyzing live… this usually takes under a minute. You can leave — we'll notify you.</Text>
      )}
    </View>
  );
}

export default function UploadStatus() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [busy, setBusy] = useState(false);
  // Live progress keyed by inspectionId-kind.
  const [progress, setProgress] = useState<Record<string, UploadProgress>>({});
  // Server-side analysis state keyed by inspectionId, once its uploads are all done.
  const [analysis, setAnalysis] = useState<Record<string, InspectionDetail>>({});
  const analysisRef = useRef<Record<string, InspectionDetail>>({});
  const startedRef = useRef(false);

  const refresh = useCallback(async () => {
    setItems(await getQueue());
  }, []);

  useEffect(() => {
    setProgressListener((p) => {
      setProgress((prev) => ({ ...prev, [`${p.inspectionId}-${p.kind}`]: p }));
    });
    refresh();
    const t = setInterval(refresh, 1500);
    // Kick the queue once on mount so uploads run even if navigation raced.
    if (!startedRef.current) {
      startedRef.current = true;
      processQueue().catch(() => undefined).finally(refresh);
    }
    return () => {
      setProgressListener(null);
      clearInterval(t);
    };
  }, [refresh]);

  // Once both captures of an inspection have finished uploading, follow its analysis on the
  // server (processing -> scored -> approved/rejected) and show it live. Stops at a result.
  useEffect(() => {
    const byId: Record<string, QueueItem[]> = {};
    for (const it of items) (byId[it.inspectionId] ||= []).push(it);
    const readyIds = Object.keys(byId).filter(
      (id) => byId[id].length > 0 && byId[id].every((i) => i.status === "completed")
    );
    if (readyIds.length === 0) return;

    let cancelled = false;
    const poll = async () => {
      for (const id of readyIds) {
        const cur = analysisRef.current[id];
        if (cur && TERMINAL.includes(cur.status)) continue; // done; stop polling this one
        try {
          const d = await getMyInspection(id);
          if (cancelled) return;
          analysisRef.current = { ...analysisRef.current, [id]: d };
          setAnalysis(analysisRef.current);
        } catch {
          /* transient; keep polling */
        }
      }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [items]);

  async function retryAll() {
    setBusy(true);
    try {
      await processQueue();
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  function pct(item: QueueItem): number {
    const live = progress[`${item.inspectionId}-${item.kind}`];
    if (live) return Math.round(live.fraction * 100);
    if (item.status === "completed") return 100;
    const done = Object.keys(item.completedParts).length;
    return Math.round((done / item.totalParts) * 100);
  }

  const anyPending = items.some((i) => i.status !== "completed");
  const online = useOnline();

  return (
    <View style={styles.container}>
      {!online && anyPending ? (
        <View style={styles.offline}>
          <Text style={styles.offlineText}>Offline. Uploads will resume automatically when you are back online.</Text>
        </View>
      ) : null}
      <FlatList
        data={items}
        keyExtractor={(i) => `${i.inspectionId}-${i.kind}`}
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => { refresh(); processQueue().catch(() => undefined); }} tintColor={colors.accent} />}
        ListHeaderComponent={
          Object.values(analysis).length ? (
            <View style={styles.analysisSection}>
              <Text style={styles.sectionTitle}>ANALYSIS</Text>
              {Object.values(analysis).map((d) => (
                <AnalysisTracker key={d.id} detail={d} />
              ))}
            </View>
          ) : null
        }
        ListEmptyComponent={<Text style={styles.dim}>No pending uploads. Every clip is safely delivered.</Text>}
        renderItem={({ item }) => {
          const p = pct(item);
          return (
            <View style={styles.row}>
              <View style={styles.rowTop}>
                <Text style={styles.kind}>{item.kind.toUpperCase()}</Text>
                <Text style={[styles.status, { color: statusColor[item.status] ?? colors.textDim }]}>
                  {item.status}
                </Text>
              </View>
              <View style={styles.barTrack}>
                <View style={[styles.barFill, { width: `${p}%`, backgroundColor: item.status === "error" ? colors.danger : colors.accent }]} />
              </View>
              <Text style={styles.meta}>
                {p}% · {mb(item.fileSize)} · {Object.keys(item.completedParts).length}/{item.totalParts} parts
              </Text>
              {item.lastError ? <Text style={styles.error}>{item.lastError}</Text> : null}
              {item.status !== "completed" ? (
                <Pressable
                  onPress={() => {
                    Alert.alert("Cancel upload?", `Discard this ${item.kind} clip and stop uploading it?`, [
                      { text: "Keep", style: "cancel" },
                      { text: "Cancel upload", style: "destructive", onPress: async () => { await cancelItem(item.inspectionId, item.kind); await refresh(); } },
                    ]);
                  }}
                  hitSlop={8}
                >
                  <Text style={styles.cancel}>Cancel upload</Text>
                </Pressable>
              ) : null}
            </View>
          );
        }}
      />

      {anyPending ? (
        <Pressable style={styles.button} onPress={retryAll} disabled={busy}>
          <Text style={styles.buttonText}>{busy ? "Uploading..." : "Retry pending uploads"}</Text>
        </Pressable>
      ) : null}
      <Pressable style={styles.secondary} onPress={() => router.replace("/vehicles")}>
        <Text style={styles.secondaryText}>{anyPending ? "Continue in background" : "Done, back to vehicles"}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: 16 },
  row: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 6, padding: 14, marginBottom: 10 },
  rowTop: { flexDirection: "row", justifyContent: "space-between" },
  kind: { color: colors.text, fontFamily: colors.mono, letterSpacing: 1 },
  status: { fontFamily: colors.mono },
  barTrack: { height: 6, backgroundColor: colors.surfaceRaised, borderRadius: 3, marginTop: 10, overflow: "hidden" },
  barFill: { height: 6, borderRadius: 3 },
  meta: { color: colors.textDim, fontFamily: colors.mono, marginTop: 6, fontSize: 12 },
  error: { color: colors.danger, marginTop: 6, fontSize: 12 },
  cancel: { color: colors.danger, marginTop: 8, fontSize: 13, fontWeight: "600" },
  dim: { color: colors.textDim, textAlign: "center", marginTop: 40 },
  button: { backgroundColor: colors.accent, borderRadius: 6, padding: 16, alignItems: "center" },
  buttonText: { color: "#ffffff", fontWeight: "700" },
  secondary: { padding: 14, alignItems: "center" },
  secondaryText: { color: colors.accent },
  offline: { backgroundColor: "#2a1b1b", borderColor: colors.danger, borderWidth: 1, borderRadius: 6, padding: 10, marginBottom: 10 },
  offlineText: { color: colors.text, fontSize: 12 },
  analysisSection: { marginBottom: 6 },
  sectionTitle: { color: colors.accent, fontFamily: colors.mono, letterSpacing: 1, fontSize: 12, marginBottom: 8 },
  tracker: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 14, marginBottom: 12 },
  trackerTitle: { color: colors.text, fontFamily: colors.mono, letterSpacing: 1, fontSize: 15, marginBottom: 10 },
  step: { flexDirection: "row", alignItems: "center", marginBottom: 8, minHeight: 22 },
  stepIcon: { width: 20, marginRight: 8 },
  pulseDot: { width: 12, height: 12, borderRadius: 6, marginLeft: 4, marginRight: 12, backgroundColor: colors.accent },
  dot: { width: 20, marginRight: 8, textAlign: "center", fontSize: 12 },
  resultBtn: { marginTop: 10, alignSelf: "flex-start" },
  resultBtnText: { color: colors.accent, fontWeight: "600" },
  stepLabel: { fontSize: 14 },
  result: { borderWidth: 1, borderRadius: 6, padding: 12, marginTop: 6 },
  resultTitle: { fontWeight: "700", fontSize: 15 },
  resultReason: { color: colors.textDim, marginTop: 4, fontSize: 13 },
  trackerHint: { color: colors.textDim, fontSize: 12, marginTop: 6, lineHeight: 17 },
});
