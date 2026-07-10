"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import {
  getMetrics, getTrends, getOverdue, getCadence, setCadence, runOverdue,
  getCosts, getDigest, generateDigest,
  type Metrics, type Trends, type OverdueList, type CostEstimate, type Digest,
} from "@/lib/api";

function fmtOverdue(h: number | null, never: boolean): string {
  if (never) return "never inspected";
  if (h === null) return "";
  if (h < 24) return `${Math.round(h)}h overdue`;
  return `${Math.round(h / 24)}d overdue`;
}

export default function MetricsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [trends, setTrends] = useState<Trends | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [overdue, setOverdue] = useState<OverdueList | null>(null);
  const [cadence, setCad] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [costs, setCosts] = useState<CostEstimate | null>(null);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [digestBusy, setDigestBusy] = useState(false);

  const loadOverdue = useCallback(async () => {
    try {
      const [o, c] = await Promise.all([getOverdue(), getCadence()]);
      setOverdue(o); setCad(c.cadence_hours);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [m, t] = await Promise.all([getMetrics(), getTrends()]);
        setMetrics(m);
        setTrends(t);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load metrics");
      }
    })();
    loadOverdue();
    getCosts().then(setCosts).catch(() => undefined);
    getDigest().then(setDigest).catch(() => undefined);
  }, [loadOverdue]);

  async function makeDigest() {
    setDigestBusy(true);
    try { setDigest(await generateDigest()); } catch { /* ignore */ } finally { setDigestBusy(false); }
  }

  async function saveCadence() {
    if (typeof cadence !== "number" || cadence < 1) return;
    setBusy(true); setNote(null);
    try { await setCadence(cadence); await loadOverdue(); setNote("Cadence saved."); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }
  async function sendReminders() {
    setBusy(true); setNote(null);
    try { const r = await runOverdue(); setNote(`Reminders sent: ${r.reminded} (of ${r.overdue} overdue, ${r.escalated} critical).`); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Metrics</h2>
        {error ? <div className="error">{error}</div> : null}
        {note ? <div className="banner-row agent" style={{ color: "var(--accent)" }}>{note}</div> : null}

        <div className="section-title" style={{ marginTop: 0 }}>
          OVERDUE VEHICLES {overdue ? `· ${overdue.count} past the ${overdue.cadence_hours}h cadence` : ""}
        </div>
        <div className="card">
          <div className="filters" style={{ marginTop: 0, alignItems: "center" }}>
            <span className="review-hint" style={{ marginTop: 0 }}>Inspect at least every</span>
            <input type="number" min={1} value={cadence} onChange={(e) => setCad(e.target.value === "" ? "" : Number(e.target.value))} style={{ width: 90 }} />
            <span className="review-hint" style={{ marginTop: 0 }}>hours</span>
            <button className="ghost" disabled={busy} onClick={saveCadence}>Save cadence</button>
            <button className="ghost" disabled={busy} onClick={sendReminders} title="Also runs automatically once a day">Send reminders now</button>
          </div>
          {overdue && overdue.items.length > 0 ? (
            <table style={{ marginTop: 12 }}>
              <thead><tr><th>Vehicle</th><th>Driver</th><th>Last passed</th><th>Status</th></tr></thead>
              <tbody>
                {overdue.items.map((o) => (
                  <tr key={o.driver_id}>
                    <td className="mono">{o.plate}</td>
                    <td>{o.name}</td>
                    <td className="mono dim">{o.last_approved_at ? new Date(o.last_approved_at).toLocaleString("sv-SE", { timeZone: "Asia/Kolkata" }) : "—"}</td>
                    <td><span className={`badge ${o.severity === "critical" ? "rejected" : "pending"}`}>{fmtOverdue(o.hours_overdue, o.never)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="dim" style={{ marginTop: 10 }}>All vehicles are within the inspection cadence. 🎉</div>}
        </div>

        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 320px" }}>
            <div className="section-title">WEEKLY DIGEST</div>
            <div className="card">
              <div className="filters" style={{ marginTop: 0, marginBottom: digest?.text ? 10 : 0 }}>
                <button className="ghost" disabled={digestBusy} onClick={makeDigest}>{digestBusy ? "Generating…" : "Generate digest"}</button>
                {digest?.generated_at ? <span className="review-hint" style={{ marginTop: 0 }}>Updated {new Date(digest.generated_at).toLocaleDateString("sv-SE")}{digest.stale ? " · stale" : ""}</span> : null}
              </div>
              {digest?.text ? (
                <div style={{ whiteSpace: "pre-wrap", fontSize: 13.5, lineHeight: 1.55, color: "var(--text)" }}>{digest.text.replace(/\*\*/g, "").replace(/^#+\s*/gm, "")}</div>
              ) : <div className="dim" style={{ fontSize: 13 }}>No digest yet. It is written automatically each Monday, or generate one now.</div>}
            </div>
          </div>

          <div style={{ flex: "1 1 300px" }}>
            <div className="section-title">ESTIMATED COST · {costs?.period ?? "this month"}</div>
            <div className="card">
              {costs ? (
                <>
                  <div style={{ fontSize: 30, fontWeight: 800, fontFamily: "var(--mono)", color: "var(--text)" }}>${costs.total_est_usd.toLocaleString()}<span className="dim" style={{ fontSize: 14, fontWeight: 400 }}> /mo est.</span></div>
                  <table style={{ marginTop: 10 }}><tbody>
                    <tr><td className="dim">Inference ({costs.images_sent} images, {costs.inference_calls} calls)</td><td className="mono" style={{ textAlign: "right" }}>${costs.inference_usd}</td></tr>
                    <tr><td className="dim">Storage (~{costs.storage_gb} GB)</td><td className="mono" style={{ textAlign: "right" }}>${costs.storage_usd}</td></tr>
                    <tr><td className="dim">AWS baseline</td><td className="mono" style={{ textAlign: "right" }}>${costs.aws_baseline_usd}</td></tr>
                  </tbody></table>
                  <div className="review-hint" style={{ marginTop: 8 }}>{costs.assumptions.join(" · ")}</div>
                </>
              ) : <div className="dim">Loading…</div>}
            </div>
          </div>
        </div>

        {!metrics ? (
          <div className="dim">Loading...</div>
        ) : (
          <>
            <div className="section-title">STATUS COUNTS</div>
            <div className="stat-row">
              {Object.entries(metrics.counts_by_status).map(([status, count]) => (
                <div className="stat" key={status}>
                  <div className="value">{count}</div>
                  <div className="label">{status}</div>
                </div>
              ))}
              {Object.keys(metrics.counts_by_status).length === 0 ? <div className="dim">No inspections yet.</div> : null}
            </div>

            <div className="section-title">AVERAGE TIME TO REVIEW</div>
            <div className="stat">
              <div className="value">
                {metrics.average_review_seconds != null
                  ? `${(metrics.average_review_seconds / 3600).toFixed(1)}h`
                  : "-"}
              </div>
              <div className="label">capture to decision</div>
            </div>

            {trends && trends.reviews_by_day.length > 0 ? (
              <>
                <div className="section-title">REVIEWS BY DAY</div>
                <div className="card">
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 8, height: 120 }}>
                    {trends.reviews_by_day.map((d) => {
                      const max = Math.max(...trends.reviews_by_day.map((x) => x.approved + x.rejected), 1);
                      const total = d.approved + d.rejected;
                      return (
                        <div key={d.day} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }} title={`${d.day}: ${d.approved} approved, ${d.rejected} rejected`}>
                          <div style={{ width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", height: 90 }}>
                            <div style={{ height: `${(d.rejected / max) * 90}px`, background: "var(--danger)" }} />
                            <div style={{ height: `${(d.approved / max) * 90}px`, background: "var(--ok)" }} />
                          </div>
                          <div className="mono" style={{ fontSize: 9, color: "var(--text-dim)" }}>{d.day.slice(5)}</div>
                          <div className="mono" style={{ fontSize: 10 }}>{total}</div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="mono dim" style={{ fontSize: 11, marginTop: 8 }}>green = approved · red = rejected</div>
                </div>

                <div className="section-title">PER DRIVER</div>
                <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                  <table>
                    <thead><tr><th>Driver</th><th>Total</th><th>Approved</th><th>Rejected</th><th>Approval rate</th></tr></thead>
                    <tbody>
                      {trends.per_driver.map((p) => (
                        <tr key={p.driver}>
                          <td>{p.driver}</td>
                          <td className="mono">{p.total}</td>
                          <td className="mono">{p.approved}</td>
                          <td className="mono">{p.rejected}</td>
                          <td className="mono">{p.approval_rate != null ? `${Math.round(p.approval_rate * 100)}%` : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

            <div className="section-title">REJECTS BY VEHICLE (repeat offenders)</div>
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <table>
                <thead>
                  <tr><th>Plate</th><th>Rejects</th></tr>
                </thead>
                <tbody>
                  {metrics.rejects_by_vehicle.map((r) => (
                    <tr key={r.vehicle_plate}>
                      <td className="mono">{r.vehicle_plate}</td>
                      <td className="mono">{r.rejects}</td>
                    </tr>
                  ))}
                  {metrics.rejects_by_vehicle.length === 0 ? (
                    <tr><td colSpan={2} className="dim" style={{ textAlign: "center", padding: 24 }}>No rejects recorded.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </>
  );
}
