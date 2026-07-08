"use client";
import type { ZoneIssueLabel } from "@/lib/api";
import { Icon } from "@/components/Icon";

function scoreColor(s: number): string {
  if (s >= 85) return "var(--ok)";
  if (s <= 40) return "var(--danger)";
  return "var(--warn)";
}

export function ScoreBar({ score }: { score: number | null | undefined }) {
  if (score == null) return <span className="dim mono" style={{ fontSize: 12 }}>—</span>;
  const s = Math.max(0, Math.min(100, score));
  return (
    <span className="scorebar" title={`Cleanliness score ${score}/100`}>
      <span className="track">
        <span className="fill" style={{ width: `${s}%`, background: scoreColor(s) }} />
      </span>
      <span className="num">{Math.round(score)}</span>
    </span>
  );
}

export function SourceBadge({ source }: { source: "agent" | "human" | null | undefined }) {
  if (source === "agent") return <span className="src agent" title="Decided autonomously by the cleanliness agent"><Icon name="chip" size={13} /> Agent</span>;
  if (source === "human") return <span className="src human" title="Decided by a human reviewer"><Icon name="human" size={13} /> Human</span>;
  return <span className="src human" style={{ opacity: 0.6 }}>· pending</span>;
}

export function ReasonChips({ reasons }: { reasons: ZoneIssueLabel[] }) {
  if (!reasons || reasons.length === 0) return null;
  return (
    <span className="chips">
      {reasons.map((r, i) => (
        <span key={i} className="chip">{r.zone_key.replace(/_/g, " ")}: {r.issue_key}</span>
      ))}
    </span>
  );
}
