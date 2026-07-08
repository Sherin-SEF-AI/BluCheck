"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { ScoreBar, SourceBadge, ReasonChips } from "@/components/AgentBits";
import { Icon } from "@/components/Icon";
import {
  getToken, getAgentActivity, setMode, runPending,
  type AgentActivity, type ModelVersion, getModelVersion,
} from "@/lib/api";
import { fmtIST } from "@/lib/time";
import { usePolling } from "@/lib/usePolling";

type Mode = "auto" | "assist" | "shadow" | "disabled";

const MODES: { key: Mode; name: string; line: string }[] = [
  { key: "auto", name: "Full Auto", line: "Approves and rejects on its own; escalates only uncertain cases and notifies drivers." },
  { key: "assist", name: "Semi Auto", line: "Scores and recommends a decision; you confirm each one with a click." },
  { key: "shadow", name: "Manual", line: "You review everything. The agent scores silently in the background so you can compare." },
  { key: "disabled", name: "Off", line: "Agent is off. No scoring, no decisions." },
];

export default function AgentHome() {
  const [data, setData] = useState<AgentActivity | null>(null);
  const [mv, setMv] = useState<ModelVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined" && !getToken()) window.location.href = "/login/";
  }, []);

  const load = useCallback(async () => {
    try {
      const [a, v] = await Promise.all([getAgentActivity(), getModelVersion()]);
      setData(a);
      setMv(v);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load agent");
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 5000);

  async function changeMode(m: Mode) {
    if (m === "auto" && !window.confirm("Switch to FULL AUTO?\n\nThe agent will approve and reject inspections without a human, and will immediately work through the current backlog. Uncertain cases are still sent to a human.")) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      await setMode(m);
      if (m === "auto") setNote("Full Auto on. Agent is working the backlog now.");
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to change mode"); }
    finally { setBusy(false); }
  }

  async function runBacklog() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const r = await runPending();
      setNote(`Agent processed backlog: ${r.approved} approved, ${r.rejected} rejected, ${r.escalated} sent to a human.`);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }


  const s = data?.summary;
  const mode = (mv?.mode ?? "shadow") as Mode;
  const active = MODES.find((m) => m.key === mode);
  const scoredCap = s
    ? `${s.scored_total ?? 0} scored today${s.avg_latency_ms != null ? ` · ${Math.round(s.avg_latency_ms / 100) / 10}s avg decision` : ""}`
    : "";

  return (
    <>
      <Nav />
      <div className="container">
        <div className="home-head">
          <div>
            <h1>Agent</h1>
            {scoredCap ? <div className="who-cap">{scoredCap}</div> : null}
          </div>
          <span className={`status-dot ${s?.online ? "on" : ""}`}>
            <span className="d" /> {s?.online ? "online" : "idle"}
          </span>
        </div>

        {err ? <div className="error" style={{ marginBottom: 12 }}>{err}</div> : null}
        {note ? <div className="banner-row agent" style={{ color: "var(--accent)" }}>{note}</div> : null}

        {/* Mode: single segmented control */}
        <div className="seg" role="tablist" aria-label="Autonomy mode">
          {MODES.map((m) => (
            <button
              key={m.key}
              className={`${mode === m.key ? "on" : ""} ${mode === m.key && m.key === "disabled" ? "danger-on" : ""}`}
              disabled={busy}
              aria-selected={mode === m.key}
              onClick={() => changeMode(m.key)}
            >
              {m.name}
            </button>
          ))}
        </div>
        {active ? <div className="mode-line">{active.line}</div> : null}

        {/* Live numbers */}
        <div className="metrics">
          <div className="metric ok"><div className="n">{s?.auto_approved ?? "—"}</div><div className="l">Approved</div></div>
          <div className="metric bad"><div className="n">{s?.auto_rejected ?? "—"}</div><div className="l">Rejected</div></div>
          <div className="metric warn"><div className="n">{s?.escalated ?? "—"}</div><div className="l">Escalated</div></div>
          <div className="metric"><div className="n">{s?.awaiting_human ?? "—"}</div><div className="l">Awaiting you</div></div>
        </div>

        {/* Activity feed */}
        <div className="label-row">
          <span className="t">Recent decisions</span>
          <a href="/audit/">View all</a>
        </div>
        <div className="card">
          {!data ? (
            <div className="dim">Loading…</div>
          ) : data.items.length === 0 ? (
            <div className="dim" style={{ padding: 8 }}>
              No inspections scored yet. Once drivers submit inspections, the agent&apos;s decisions appear here.
            </div>
          ) : (
            <div className="feed">
              {data.items.map((it) => (
                <div key={it.inspection_id} className="feed-row" onClick={() => { window.location.href = `/inspection/?id=${it.inspection_id}`; }}>
                  <div>
                    <div className="plate">{it.vehicle_plate}</div>
                    <div className="who">{it.driver_name}</div>
                  </div>
                  <div className="feed-mid">
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                      <span className={`badge ${it.status}`}>{it.status}</span>
                      <SourceBadge source={it.decision_source} />
                      <span className="dim mono" style={{ fontSize: 11 }}>{fmtIST(it.reviewed_at ?? it.created_at)}</span>
                    </div>
                    {it.reasons.length > 0 ? <ReasonChips reasons={it.reasons} /> : <span className="dim" style={{ fontSize: 12 }}>clean — no issues detected</span>}
                  </div>
                  <div className="feed-right">
                    <ScoreBar score={it.overall_score} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <details className="adv">
          <summary><Icon name="bolt" size={13} className="chev" /> Manual controls</summary>
          <div className="adv-body">
            <button className="ghost" disabled={busy} onClick={runBacklog} title="Have the agent decide all scored, still-pending inspections now">
              <Icon name="bolt" size={14} /> Run backlog now
            </button>
          </div>
          <div className="review-hint" style={{ marginTop: 12 }}>
            Thresholds, scoring math, calibration and accuracy live on <a href="/model/">Performance</a> — the single place to tune the agent.
          </div>
        </details>
      </div>
    </>
  );
}
