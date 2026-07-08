import { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ActivityIndicator, ScrollView, RefreshControl } from "react-native";
import { getMyRewards, getMyCoaching, type Rewards, type Coaching } from "@/lib/api";
import { colors } from "@/lib/theme";

const TIER_COLOR: Record<string, string> = {
  Bronze: "#b08d57",
  Silver: "#c0c6cc",
  Gold: "#e0b93b",
  Platinum: "#7fd6c8",
};

export default function RewardsScreen() {
  const [data, setData] = useState<Rewards | null>(null);
  const [coaching, setCoaching] = useState<Coaching | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getMyRewards());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load rewards");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
    getMyCoaching().then(setCoaching).catch(() => undefined);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return <View style={styles.center}><ActivityIndicator color={colors.accent} /></View>;
  }
  if (error || !data) {
    return <View style={styles.center}><Text style={styles.error}>{error ?? "No rewards yet"}</Text></View>;
  }

  const tierColor = TIER_COLOR[data.tier] ?? colors.accent;
  // Progress toward the next tier.
  const currentTierMin = [...data.tiers].reverse().find((t) => data.points >= t.min_points)?.min_points ?? 0;
  const span = data.next_tier_at !== null ? data.next_tier_at - currentTierMin : 1;
  const progress = data.next_tier_at !== null ? Math.max(0, Math.min(1, (data.points - currentTierMin) / (span || 1))) : 1;

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ padding: 16, gap: 14 }}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.accent} />}
    >
      {/* Points + tier hero */}
      <View style={[styles.hero, { borderColor: tierColor }]}>
        <Text style={styles.points}>{data.points}</Text>
        <Text style={styles.pointsLabel}>REWARD POINTS</Text>
        <View style={[styles.tierPill, { backgroundColor: tierColor }]}>
          <Text style={styles.tierText}>{data.tier}</Text>
        </View>
        {data.next_tier_at !== null ? (
          <>
            <View style={styles.progressTrack}>
              <View style={[styles.progressFill, { width: `${progress * 100}%`, backgroundColor: tierColor }]} />
            </View>
            <Text style={styles.progressText}>{data.next_tier_at - data.points} points to next tier</Text>
          </>
        ) : (
          <Text style={styles.progressText}>Top tier reached</Text>
        )}
      </View>

      {/* AI coach */}
      {coaching ? (
        <View style={styles.coachCard}>
          <Text style={styles.coachLabel}>✨ YOUR COACH</Text>
          <Text style={styles.coachHead}>{coaching.headline}</Text>
          <Text style={styles.coachTip}>{coaching.tip}</Text>
        </View>
      ) : null}

      {/* Stat grid */}
      <View style={styles.grid}>
        <Stat label="Day streak" value={`${data.streak_days}🔥`} />
        <Stat label="Approved" value={String(data.approved_count)} />
        <Stat label="First-pass" value={String(data.first_pass_count)} />
        <Stat label="This month" value={`+${data.this_month_points}`} />
      </View>

      {/* How to earn */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>HOW YOU EARN</Text>
        <Text style={styles.earnRow}>+{data.per_approved}  Every approved inspection</Text>
        <Text style={styles.earnRow}>+{data.per_first_pass}  Passing on the first try (no re-clean)</Text>
        <Text style={styles.earnRow}>+{data.per_streak_day}  Each day of your inspection streak</Text>
      </View>

      {/* Recent activity */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>RECENT</Text>
        {data.recent.length === 0 ? (
          <Text style={styles.dim}>No inspections yet. Complete one to start earning.</Text>
        ) : (
          data.recent.map((e, i) => (
            <View key={i} style={styles.recentRow}>
              <Text style={styles.recentDate}>{e.date}</Text>
              <Text style={styles.recentLabel}>{e.label}</Text>
              <Text style={[styles.recentPts, { color: e.points > 0 ? colors.ok : colors.textDim }]}>
                {e.points > 0 ? `+${e.points}` : "—"}
              </Text>
            </View>
          ))
        )}
      </View>

      {/* Tiers */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>TIERS</Text>
        {data.tiers.map((t) => (
          <View key={t.name} style={styles.tierRow}>
            <View style={[styles.tierDot, { backgroundColor: TIER_COLOR[t.name] ?? colors.accent }]} />
            <Text style={[styles.tierName, data.tier === t.name && { color: colors.text, fontWeight: "700" }]}>{t.name}</Text>
            <Text style={styles.tierMin}>{t.min_points}+ pts</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center", padding: 24 },
  error: { color: colors.danger, textAlign: "center" },
  hero: { backgroundColor: colors.surface, borderWidth: 1, borderRadius: 12, padding: 22, alignItems: "center", gap: 8 },
  points: { color: colors.text, fontSize: 52, fontWeight: "800", fontFamily: colors.mono },
  pointsLabel: { color: colors.textDim, fontFamily: colors.mono, fontSize: 12, letterSpacing: 2 },
  tierPill: { paddingHorizontal: 14, paddingVertical: 4, borderRadius: 20, marginTop: 2 },
  tierText: { color: "#0a0a0a", fontWeight: "800", letterSpacing: 1, fontSize: 13 },
  progressTrack: { width: "100%", height: 8, backgroundColor: colors.surfaceRaised, borderRadius: 4, overflow: "hidden", marginTop: 8 },
  progressFill: { height: "100%" },
  progressText: { color: colors.textDim, fontSize: 12 },
  coachCard: { backgroundColor: colors.surface, borderColor: colors.accent, borderWidth: 1, borderRadius: 10, padding: 16, gap: 6 },
  coachLabel: { color: colors.accent, fontFamily: colors.mono, fontSize: 11, letterSpacing: 1 },
  coachHead: { color: colors.text, fontSize: 16, fontWeight: "700" },
  coachTip: { color: colors.textDim, fontSize: 14, lineHeight: 20 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  stat: { flexGrow: 1, flexBasis: "45%", backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 14, alignItems: "center", gap: 4 },
  statValue: { color: colors.text, fontSize: 22, fontWeight: "700", fontFamily: colors.mono },
  statLabel: { color: colors.textDim, fontSize: 12 },
  card: { backgroundColor: colors.surface, borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 16, gap: 8 },
  cardTitle: { color: colors.textDim, fontFamily: colors.mono, fontSize: 12, letterSpacing: 1, marginBottom: 2 },
  earnRow: { color: colors.text, fontSize: 14 },
  recentRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 4 },
  recentDate: { color: colors.textDim, fontFamily: colors.mono, fontSize: 12, width: 52 },
  recentLabel: { color: colors.text, fontSize: 13, flex: 1 },
  recentPts: { fontFamily: colors.mono, fontSize: 14, fontWeight: "700" },
  dim: { color: colors.textDim },
  tierRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 4 },
  tierDot: { width: 12, height: 12, borderRadius: 6 },
  tierName: { color: colors.textDim, flex: 1, fontSize: 14 },
  tierMin: { color: colors.textDim, fontFamily: colors.mono, fontSize: 12 },
});
